"""DNS verification for guessed domains."""

import collections
import logging
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

from domain_mapper.models import Confidence, DomainResult, DomainSource
from domain_mapper.sources.cert_transparency import registrable_domain

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

# Sources a plain MX check can never carry to High, for two different reasons.
#
# A guessed domain that resolves proves somebody owns it, not that the company we asked
# about does: `chaseuk.com` has live mail because a stranger parked it in 2016, not
# because Chase runs it.
#
# A certificate-log domain is an observation rather than a guess, but a certificate
# still only proves someone operated the name. It reaches High by a stronger test
# further down: its mail running on the parent's own tenant.
_GUESS_SOURCES = {
    DomainSource.TLD_GUESS.value,
    DomainSource.NAME_GUESS.value,
    DomainSource.CERT_TRANSPARENCY.value,
}


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


def _mx_hosts(domain: str) -> list[str]:
    """Every non-null MX target for a domain, lowercased and without the trailing dot."""
    if not HAS_DNSPYTHON:
        return []
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=DNS_TIMEOUT)
    except dns.exception.DNSException:
        return []
    hosts = [str(rdata.exchange).strip().lower().rstrip(".") for rdata in answers]
    return [h for h in hosts if h and not _is_null_mx(h)]


def _mail_tenant(host: str) -> str:
    """The shared part of a mail provider's hostname.

    "cluster14a.us.messagelabs.com" and "cluster14.us.messagelabs.com" both reduce to
    "us.messagelabs.com", which is what makes them recognisable as one tenant.
    """
    parts = (host or "").split(".")
    return ".".join(parts[-3:]) if len(parts) >= 3 else host


def shares_mail_with_parent(
    domain_hosts: list[str], parent_hosts: list[str], parent_domain: str
) -> bool:
    """True when a domain's mail is demonstrably the parent's mail.

    Two ways that shows up, and both were observed on real filings:

      - the same tenant: jpmorgan.co.uk and jpmorgan.com both sit on us.messagelabs.com,
        and five HSBC regional domains share one Proofpoint tenant with hsbc.com
      - the MX pointing straight home: jpmorganchase.de resolves to threshold2.jpmorgan.com

    This is the test that separates a real regional domain from a parked lookalike.
    chaseuk.com has live mail on GoDaddy and chaseuk.co.uk on IONOS; neither matches.
    """
    if not domain_hosts:
        return False

    parent_tenants = {_mail_tenant(h) for h in parent_hosts if h}
    if parent_tenants & {_mail_tenant(h) for h in domain_hosts}:
        return True

    parent_registrable = registrable_domain(parent_domain)
    if parent_registrable:
        return any(registrable_domain(h) == parent_registrable for h in domain_hosts)
    return False


def _check_mx(domain: str) -> bool:
    """Check if a domain has usable MX records. Requires dnspython.

    The presence of an MX record is not proof of mail. `luxembourg.com` publishes
    `0 localhost.` and `unitedkingdom.com` publishes `1000 0.0.0.0.`; both used to score
    as verified. A domain whose every MX target is a null stub has no mail at all.

    Thin wrapper over `_mx_hosts` so there is one implementation rather than two.
    """
    return bool(_mx_hosts(domain))


def _check_a(domain: str) -> bool:
    """Check if a domain has an A record (resolves at all)."""
    try:
        socket.setdefaulttimeout(DNS_TIMEOUT)
        socket.getaddrinfo(domain, None)
        return True
    except (socket.gaierror, socket.timeout, OSError):
        return False


def _verify_single(domain: str) -> tuple[str, str, list[str]]:
    """Verify a single domain.

    Returns (domain, status, mx_hosts) where status is one of:
      "mx"         -> has usable MX records (treated as verified)
      "a-only"     -> resolves an A record but no MX (weaker signal)
      "unresolved" -> does not resolve

    The hosts travel with the status so the ownership test can compare mail operators
    without a second round of lookups.
    """
    hosts = _mx_hosts(domain)
    if hosts:
        return (domain, "mx", hosts)
    if _check_a(domain):
        return (domain, "a-only", [])
    return (domain, "unresolved", [])


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
        hosts_by_domain: dict[str, list[str]] = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(_verify_single, d): d for d in domains}
            for future in as_completed(futures):
                domain, status, hosts = future.result()
                status_by_domain[domain] = status
                hosts_by_domain[domain] = hosts

        # Establish what this group's mail actually looks like.
        #
        # The parent domain a caller supplies is often web-only. jpmorganchase.com has no
        # MX at all, while JPMorgan's mail runs on jpmorgan.com and chase.com, so testing
        # only against the parent promoted nothing for JPMorgan even though four observed
        # domains sat on the company's own messagelabs tenant.
        #
        # The reference set is therefore the parent's own tenants plus any tenant that at
        # least two certificate-observed domains agree on. Agreement is the evidence: one
        # squatter cannot define a group's mail. Where nothing agrees the set stays empty
        # and nothing is promoted, which is the safe direction.
        parent_hosts = {
            parent: _mx_hosts(parent)
            for parent in {r.parent_domain for r in to_verify if r.parent_domain}
        }

        observed_tenants = collections.Counter()
        for result in results:
            if (
                result.domain_source == DomainSource.CERT_TRANSPARENCY.value
                and status_by_domain.get(result.domain) == "mx"
            ):
                observed_tenants.update(
                    {_mail_tenant(h) for h in hosts_by_domain.get(result.domain, [])}
                )
        group_tenants = {t for t, n in observed_tenants.items() if n >= 2}
        if group_tenants:
            logger.info(f"Group mail tenants corroborated by the logs: {sorted(group_tenants)}")

        # Update results. Only an MX hit counts as "verified"; an A-only hit is
        # recorded as a weaker, separate status and never bumps confidence.
        for result in results:
            if result.dns_verified is not None:
                continue
            status = status_by_domain.get(result.domain, "unresolved")
            result.dns_status = status
            if status != "mx":
                result.dns_verified = False
                continue

            result.dns_verified = True
            result.confidence = _bump_confidence(result.confidence, result.domain_source)

            # The one case where DNS justifies High: a domain observed in the
            # certificate logs whose mail is demonstrably the parent's, either on the
            # same tenant or pointing straight at the parent domain. A guessed domain
            # is deliberately not promoted this way; that stays a separate decision.
            if result.domain_source == DomainSource.CERT_TRANSPARENCY.value:
                hosts = hosts_by_domain.get(result.domain, [])
                on_group_tenant = bool(group_tenants & {_mail_tenant(h) for h in hosts})
                if on_group_tenant or shares_mail_with_parent(
                    hosts, parent_hosts.get(result.parent_domain, []), result.parent_domain
                ):
                    result.confidence = Confidence.HIGH.value

        mx_count = sum(1 for s in status_by_domain.values() if s == "mx")
        a_only_count = sum(1 for s in status_by_domain.values() if s == "a-only")
        logger.info(
            f"DNS verification: {mx_count}/{len(domains)} MX-verified, "
            f"{a_only_count} resolve A-only (unverified)"
        )
        return results
