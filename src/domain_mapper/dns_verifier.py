"""DNS verification for guessed domains."""

import logging
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

from domain_mapper.models import Confidence, DomainResult

logger = logging.getLogger(__name__)

# Feature-detect dnspython once, at import time, rather than swallowing the
# ImportError on every lookup. If it is missing we degrade to A-record checks
# only, but we say so loudly instead of silently no-opping MX verification.
try:
    import dns.exception
    import dns.resolver

    HAS_DNSPYTHON = True
except ImportError:  # pragma: no cover - exercised via the HAS_DNSPYTHON flag
    HAS_DNSPYTHON = False

# Timeout for DNS lookups in seconds
DNS_TIMEOUT = 5

# Confidence ladder, weakest to strongest. An MX-verified domain is promoted
# one rung (a brand-name guess that has live mail is a genuinely stronger signal).
_CONFIDENCE_LADDER = [Confidence.LOW.value, Confidence.MEDIUM.value, Confidence.HIGH.value]


def _bump_confidence(current: str) -> str:
    """Promote a confidence value by one rung, capped at HIGH."""
    try:
        idx = _CONFIDENCE_LADDER.index(current)
    except ValueError:
        return current
    return _CONFIDENCE_LADDER[min(idx + 1, len(_CONFIDENCE_LADDER) - 1)]


def _check_mx(domain: str) -> bool:
    """Check if a domain has MX records. Requires dnspython."""
    if not HAS_DNSPYTHON:
        return False
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=DNS_TIMEOUT)
        return len(answers) > 0
    except dns.exception.DNSException:
        # NXDOMAIN, NoAnswer, Timeout, NoNameservers etc. all subclass this.
        return False


def _check_a(domain: str) -> bool:
    """Check if a domain has an A record (resolves at all)."""
    try:
        socket.setdefaulttimeout(DNS_TIMEOUT)
        socket.getaddrinfo(domain, None)
        return True
    except (socket.gaierror, socket.timeout, OSError):
        return False


def _verify_single(domain: str) -> tuple[str, str]:
    """Verify a single domain.

    Returns (domain, status) where status is one of:
      "mx"         -> has MX records (treated as verified)
      "a-only"     -> resolves an A record but no MX (weaker signal)
      "unresolved" -> does not resolve
    """
    if _check_mx(domain):
        return (domain, "mx")
    if _check_a(domain):
        return (domain, "a-only")
    return (domain, "unresolved")


class DnsVerifier:
    """Verify guessed domains via DNS lookups."""

    def __init__(self, max_workers: int = 10):
        self.max_workers = max_workers

    def verify_domains(self, results: list[DomainResult]) -> list[DomainResult]:
        """Run DNS verification on all domain results. Updates results in place."""
        # Only check domains not already resolved (dns_verified still None)
        to_verify = [r for r in results if r.dns_verified is None]
        if not to_verify:
            return results

        if not HAS_DNSPYTHON:
            logger.warning(
                "dnspython is not installed, so MX (mail) verification is unavailable. "
                "Falling back to A-record resolution only, which cannot tell a real mail "
                "domain from a parking page. Install with: pip install dnspython"
            )

        domains = list({r.domain for r in to_verify})
        logger.info(f"DNS verification: checking {len(domains)} domains")

        status_by_domain: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(_verify_single, d): d for d in domains}
            for future in as_completed(futures):
                domain, status = future.result()
                status_by_domain[domain] = status

        # Update results. Only an MX hit counts as "verified"; an A-only hit is
        # recorded as a weaker, separate status and never bumps confidence.
        for result in results:
            if result.dns_verified is not None:
                continue
            status = status_by_domain.get(result.domain, "unresolved")
            result.dns_status = status
            if status == "mx":
                result.dns_verified = True
                result.confidence = _bump_confidence(result.confidence)
            else:
                result.dns_verified = False

        mx_count = sum(1 for s in status_by_domain.values() if s == "mx")
        a_only_count = sum(1 for s in status_by_domain.values() if s == "a-only")
        logger.info(
            f"DNS verification: {mx_count}/{len(domains)} MX-verified, "
            f"{a_only_count} resolve A-only (unverified)"
        )
        return results
