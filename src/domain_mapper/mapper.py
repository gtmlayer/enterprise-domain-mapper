"""Core orchestrator that combines all data sources and produces domain maps."""

import logging

from domain_mapper.dns_verifier import DnsVerifier
from domain_mapper.models import CompanyResult, Confidence, DomainResult, DomainSource
from domain_mapper.sources.cert_transparency import CertTransparencySource
from domain_mapper.sources.sec_edgar import SecEdgarSource
from domain_mapper.sources.tld_generator import TldGenerator
from domain_mapper.sources.wikipedia import WikipediaSource

logger = logging.getLogger(__name__)


class DomainMapper:
    """Orchestrates subsidiary discovery and domain mapping."""

    def __init__(self, verify_dns: bool = False):
        self.sec_edgar = SecEdgarSource()
        self.wikipedia = WikipediaSource()
        self.tld_generator = TldGenerator()
        self.cert_transparency = CertTransparencySource()
        self.dns_verifier = DnsVerifier() if verify_dns else None
        self.verify_dns = verify_dns

    def map_company(self, company_name: str, parent_domain: str = "") -> CompanyResult:
        """Map a single company to its subsidiaries and domains."""
        result = CompanyResult(
            company_name=company_name,
            parent_domain=parent_domain,
        )

        # 1. Gather subsidiaries from all sources
        try:
            sec_subs = self.sec_edgar.get_subsidiaries(company_name)
            result.subsidiaries.extend(sec_subs)
        except Exception as e:
            result.errors.append(f"SEC EDGAR error: {e}")
            logger.warning(f"SEC EDGAR failed for '{company_name}': {e}")

        try:
            wiki_subs = self.wikipedia.get_subsidiaries(company_name)
            result.subsidiaries.extend(wiki_subs)
        except Exception as e:
            result.errors.append(f"Wikipedia error: {e}")
            logger.warning(f"Wikipedia failed for '{company_name}': {e}")

        # Deduplicate subsidiaries by name (case-insensitive)
        seen = set()
        unique_subs = []
        for sub in result.subsidiaries:
            key = sub.name.lower().strip()
            if key not in seen:
                seen.add(key)
                unique_subs.append(sub)
        result.subsidiaries = unique_subs

        # 2. Guess a .com directly from each SEC/Wikipedia subsidiary name
        for sub in result.subsidiaries:
            # If the subsidiary name looks like it could be a domain, add it directly
            domain = self._guess_direct_domain(sub.name, parent_domain)
            if domain:
                result.domains.append(
                    DomainResult(
                        parent_company=company_name,
                        parent_domain=parent_domain,
                        subsidiary_name=sub.name,
                        subsidiary_type=(
                            sub.subsidiary_type.value
                            if hasattr(sub.subsidiary_type, "value")
                            else str(sub.subsidiary_type)
                        ),
                        jurisdiction=sub.jurisdiction,
                        domain=domain,
                        # The entity came from SEC/Wikipedia, but this .com is a guess
                        # from its name, so the source is NAME_GUESS, not the filing.
                        domain_source=DomainSource.NAME_GUESS.value,
                        # A .com guessed from the entity name is unverified, so it
                        # must NOT be HIGH. It starts MEDIUM (a brand-name guess is
                        # a reasonable signal) and is only promoted to HIGH if DNS
                        # MX verification confirms live mail.
                        confidence=Confidence.MEDIUM.value,
                    )
                )

        # 3. Generate TLD-based domain guesses
        tld_domains = self.tld_generator.generate_domains(
            result.subsidiaries, company_name, parent_domain
        )
        # Avoid duplicating already-confirmed domains
        existing = {d.domain.lower() for d in result.domains}
        for td in tld_domains:
            if td.domain.lower() not in existing:
                result.domains.append(td)
                existing.add(td.domain.lower())

        # 4. Domains observed in certificate transparency logs.
        #
        # Unlike everything above, these are not derived from a subsidiary list, so they
        # reach brands that are not legal entities and never appear in a filing. They run
        # last so that anything already found by name or TLD keeps its own provenance.
        guessed_sources = {DomainSource.TLD_GUESS.value, DomainSource.NAME_GUESS.value}
        try:
            by_domain = {d.domain.lower(): d for d in result.domains}
            for cert_domain in self.cert_transparency.get_domains(company_name, parent_domain):
                key = cert_domain.domain.lower()
                already = by_domain.get(key)

                if already is None:
                    result.domains.append(cert_domain)
                    existing.add(key)
                    by_domain[key] = cert_domain
                    continue

                # The same domain reached by both routes. A certificate is direct
                # evidence about the domain; a guess is not, so the stronger provenance
                # wins even though the guess got here first. Without this, jpmorgan.co.uk
                # keeps "TLD guess" and is capped at Medium, despite sitting in the logs
                # and running on JPMorgan's own mail tenant.
                #
                # The guess's entity and jurisdiction are kept: the log knows a domain
                # exists, but not which subsidiary or country it belongs to.
                if already.domain_source in guessed_sources:
                    already.domain_source = DomainSource.CERT_TRANSPARENCY.value
                    if already.confidence == Confidence.LOW.value:
                        already.confidence = Confidence.MEDIUM.value
        except Exception as e:
            result.errors.append(f"Certificate log error: {e}")
            logger.warning(f"Certificate logs failed for '{company_name}': {e}")

        # 5. DNS verification (optional)
        if self.dns_verifier and result.domains:
            result.domains = self.dns_verifier.verify_domains(result.domains)

        logger.info(
            f"Mapped '{company_name}': {len(result.subsidiaries)} subsidiaries, "
            f"{len(result.domains)} domains"
        )
        return result

    def _guess_direct_domain(self, subsidiary_name: str, parent_domain: str) -> str | None:
        """Try to guess a .com domain directly from the subsidiary name."""
        import re

        # Clean the name
        clean = re.sub(r"[^a-zA-Z0-9\s]", "", subsidiary_name)
        words = clean.lower().split()

        if not words:
            return None

        # Try joining all words as a .com
        candidate = "".join(words) + ".com"

        # Don't return if it's the same as the parent domain
        if parent_domain and candidate.lower() == parent_domain.lower():
            return None

        # Only return if the name is reasonably short (likely a real brand)
        if len("".join(words)) <= 25:
            return candidate

        return None
