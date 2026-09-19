"""DNS verification for guessed domains."""

import collections
import logging
import re
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


# Labels that appear in mail hostnames without identifying anybody: role prefixes,
# regions, and the provider's own structural words. A match on one of these is a match
# on the mail industry, not on a company.
_GENERIC_MAIL_LABELS = frozenset(
    """mx mx0 mx1 mx2 mx3 mxa mxb mxc mail mails email smtp imap pop in inbound out
    outbound relay alt aspmx gw gateway cluster filter spam mta mailhost mailstore
    secure protection gslb lb edge node host srv server com net org co inc ltd
    us eu uk usa eur emea apac asia na sa au ca jp cn de fr it es nl global intl
    prod prd corp""".split()
)

# A tenant token must be at least this long. Shorter strings are region codes and
# sequence numbers, which thousands of unrelated customers share.
_MIN_TENANT_TOKEN = 4


def _flatten(value: str) -> str:
    """Lowercase alphanumerics only, so "roperwhitney-com" meets "roperwhitney.com"."""
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _is_derived_from_domain(token: str, domain: str) -> bool:
    """True when a token is just the domain's own name in different punctuation.

    Microsoft 365 names every tenant after its own domain: roperwhitney.com resolves to
    roperwhitney-com.mail.protection.outlook.com. Two unrelated companies therefore
    produce hostnames that look alike in structure while sharing no customer at all, so
    a token like this says nothing about who else is on that mail system.
    """
    flat_token, flat_domain = _flatten(token), _flatten(domain)
    if not flat_token or not flat_domain:
        return False
    return flat_token in flat_domain or flat_domain in flat_token


def tenant_identifiers(mx_hosts: list[str], domain: str) -> set[str]:
    """Customer-specific tokens in a domain's MX hostnames.

    The customer-specific part is whatever sits in front of the provider's own
    registrable domain, minus generic mail labels and minus anything derived from the
    domain's own name. What survives is an identifier the provider assigned:

      mxa-00299f02.gslb.pphosted.com            -> {"00299f02"}  HSBC's Proofpoint id
      ropertech-com.mail.protection.outlook.com -> set()         the domain, restated
      cluster14.us.messagelabs.com              -> set()         a shared cluster

    An empty set means the mail says nothing about who else belongs to this group,
    which is the correct answer far more often than the previous check allowed.
    """
    found: set[str] = set()
    for host in mx_hosts:
        host = (host or "").lower().strip().rstrip(".")
        if not host:
            continue
        provider = registrable_domain(host)
        local = host[: -(len(provider) + 1)] if provider and host.endswith("." + provider) else host
        for token in re.split(r"[.\-_]+", local):
            # Providers number their shared infrastructure: cluster14, cluster14a, mx2,
            # gw3197. Strip a trailing sequence, digits plus any letter suffix, before
            # testing, so all of those reduce to the generic word they are built on. A
            # customer id such as 00299f02 survives, because what is left ("00299f") is
            # not a generic mail word.
            unnumbered = re.sub(r"\d+[a-z]*$", "", token)
            if (
                len(token) >= _MIN_TENANT_TOKEN
                and token not in _GENERIC_MAIL_LABELS
                and unnumbered not in _GENERIC_MAIL_LABELS
                and not _is_derived_from_domain(token, domain)
            ):
                found.add(token)
    return found


def mail_points_at(mx_hosts: list[str], domain: str) -> bool:
    """True when a domain's mail is hosted inside the domain it is tested against.

    jpmorganchase.de resolves to threshold2.jpmorgan.com. Nobody points their MX inside
    someone else's domain, so this is ownership evidence in a way that merely sharing a
    mail vendor is not.
    """
    target = registrable_domain(domain)
    return bool(target) and any(registrable_domain(h) == target for h in mx_hosts)


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
        # Two kinds of evidence count, and sharing a mail vendor is not one of them.
        # An earlier version compared the last three labels of each MX hostname, which
        # on any shared provider is the provider's own domain: ropertech.com and a
        # funeral home called Roper and Sons both reduced to protection.outlook.com, so
        # the funeral home was promoted as a Roper Technologies domain.
        #
        # What counts is a customer identifier the provider assigned, corroborated by at
        # least two observed domains. HSBC's Proofpoint id 00299f02 qualifies; a
        # Microsoft 365 hostname named after the domain itself does not, and neither
        # does a shared Symantec cluster number.
        tenants_by_domain: dict[str, set[str]] = {}
        observed_tenants = collections.Counter()
        for result in results:
            domain = result.domain
            if domain in tenants_by_domain:
                continue
            if (
                result.domain_source == DomainSource.CERT_TRANSPARENCY.value
                and status_by_domain.get(domain) == "mx"
            ):
                ids = tenant_identifiers(hosts_by_domain.get(domain, []), domain)
                tenants_by_domain[domain] = ids
                observed_tenants.update(ids)

        group_tenants = {t for t, n in observed_tenants.items() if n >= 2}
        if group_tenants:
            logger.info(f"Group mail tenant ids corroborated by the logs: {sorted(group_tenants)}")
        else:
            logger.info(
                "No corroborated mail tenant id: nothing will be promoted to High on "
                "mail evidence, only on an MX inside the parent's own domain."
            )

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
                ids = tenants_by_domain.get(result.domain) or tenant_identifiers(
                    hosts, result.domain
                )
                on_group_tenant = bool(group_tenants & ids)
                points_home = mail_points_at(hosts, result.parent_domain)
                if on_group_tenant or points_home:
                    result.confidence = Confidence.HIGH.value

        mx_count = sum(1 for s in status_by_domain.values() if s == "mx")
        a_only_count = sum(1 for s in status_by_domain.values() if s == "a-only")
        logger.info(
            f"DNS verification: {mx_count}/{len(domains)} MX-verified, "
            f"{a_only_count} resolve A-only (unverified)"
        )
        return results
