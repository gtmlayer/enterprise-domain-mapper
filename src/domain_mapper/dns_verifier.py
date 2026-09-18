"""DNS verification for guessed domains."""

import logging
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

from domain_mapper.models import Confidence, DomainResult, DomainSource

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

# Confidence ladder, weakest to strongest.
_CONFIDENCE_LADDER = [Confidence.LOW.value, Confidence.MEDIUM.value, Confidence.HIGH.value]

# Domain sources that are guesses rather than observations. A guessed domain that
# resolves proves somebody owns it, not that the company we asked about does, so
# DNS evidence alone can never carry one of these to High. `chaseuk.com` has live
# mail because a stranger parked it in 2016, not because Chase runs it.
_GUESS_SOURCES = {DomainSource.TLD_GUESS.value, DomainSource.NAME_GUESS.value}


def _bump_confidence(current: str, domain_source: str | None = None) -> str:
    """Promote a confidence value by one rung.

    Capped at HIGH overall, and capped at MEDIUM when the domain itself was
    guessed. High is reserved for domains that came from a source (a filing, an
    article), never for a guess that happened to resolve.
    """
    ceiling = len(_CONFIDENCE_LADDER) - 1
    if domain_source in _GUESS_SOURCES:
        ceiling = _CONFIDENCE_LADDER.index(Confidence.MEDIUM.value)
    try:
        idx = _CONFIDENCE_LADDER.index(current)
    except ValueError:
        return current
    return _CONFIDENCE_LADDER[min(idx + 1, ceiling)]


# MX targets that advertise mail infrastructure which cannot accept a message.
# RFC 7505 defines "." as an explicit null MX; `localhost` and `0.0.0.0` are the
# informal equivalents that registrars and parking pages publish by default.
_NULL_MX_TARGETS = frozenset({"", ".", "localhost", "localhost.", "0.0.0.0", "0.0.0.0."})


def _is_null_mx(exchange: str) -> bool:
    """True when an MX target cannot receive mail."""
    return exchange.strip().lower() in _NULL_MX_TARGETS


def _check_mx(domain: str) -> bool:
    """Check if a domain has usable MX records. Requires dnspython.

    The presence of an MX record is not proof of mail. `luxembourg.com` publishes
    `0 localhost.` and `unitedkingdom.com` publishes `1000 0.0.0.0.`; both used to
    score as verified. A domain whose every MX target is a null stub is treated as
    having no mail at all.
    """
    if not HAS_DNSPYTHON:
        return False
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=DNS_TIMEOUT)
    except dns.exception.DNSException:
        # NXDOMAIN, NoAnswer, Timeout, NoNameservers etc. all subclass this.
        return False
    return any(not _is_null_mx(str(rdata.exchange)) for rdata in answers)


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
                result.confidence = _bump_confidence(result.confidence, result.domain_source)
            else:
                result.dns_verified = False

        mx_count = sum(1 for s in status_by_domain.values() if s == "mx")
        a_only_count = sum(1 for s in status_by_domain.values() if s == "a-only")
        logger.info(
            f"DNS verification: {mx_count}/{len(domains)} MX-verified, "
            f"{a_only_count} resolve A-only (unverified)"
        )
        return results
