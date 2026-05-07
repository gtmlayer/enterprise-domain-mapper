"""DNS verification for guessed domains."""

import logging
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

from domain_mapper.models import Confidence, DomainResult

logger = logging.getLogger(__name__)

# Timeout for DNS lookups in seconds
DNS_TIMEOUT = 5


def _check_mx(domain: str) -> bool:
    """Check if a domain has MX records."""
    import dns.resolver

    try:
        dns.resolver.resolve(domain, "MX", lifetime=DNS_TIMEOUT)
        return True
    except Exception:
        return False


def _check_a(domain: str) -> bool:
    """Check if a domain has an A record (fallback)."""
    try:
        socket.setdefaulttimeout(DNS_TIMEOUT)
        socket.getaddrinfo(domain, None)
        return True
    except (socket.gaierror, socket.timeout, OSError):
        return False


def _verify_single(domain: str) -> tuple[str, bool]:
    """Verify a single domain. Returns (domain, verified)."""
    try:
        # Try dnspython first for MX records
        if _check_mx(domain):
            return (domain, True)
    except ImportError:
        pass

    # Fall back to A record check
    if _check_a(domain):
        return (domain, True)

    return (domain, False)


class DnsVerifier:
    """Verify guessed domains via DNS lookups."""

    def __init__(self, max_workers: int = 10):
        self.max_workers = max_workers

    def verify_domains(self, results: list[DomainResult]) -> list[DomainResult]:
        """Run DNS verification on all domain results. Updates results in place."""
        # Only verify guessed domains (confirmed ones from SEC/Wikipedia are already good)
        to_verify = [r for r in results if r.dns_verified is None]
        if not to_verify:
            return results

        domains = list({r.domain for r in to_verify})
        logger.info(f"DNS verification: checking {len(domains)} domains")

        verified_domains = set()
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(_verify_single, d): d for d in domains}
            for future in as_completed(futures):
                domain, is_verified = future.result()
                if is_verified:
                    verified_domains.add(domain)

        # Update results
        for result in results:
            if result.domain in verified_domains:
                result.dns_verified = True
                if result.confidence == Confidence.LOW.value:
                    result.confidence = Confidence.MEDIUM.value
            elif result.dns_verified is None:
                result.dns_verified = False

        verified_count = len(verified_domains)
        logger.info(f"DNS verification: {verified_count}/{len(domains)} domains verified")
        return results
