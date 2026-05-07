"""Core orchestrator that combines all data sources and produces domain maps."""

import logging

from domain_mapper.dns_verifier import DnsVerifier
from domain_mapper.models import Confidence, CompanyResult, DomainResult, DomainSource
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

        # 2. Create confirmed domain results from SEC/Wikipedia subsidiaries
        for sub in result.subsidiaries:
            # If the subsidiary name looks like it could be a domain, add it directly
            domain = self._guess_direct_domain(sub.name, parent_domain)
            if domain:
                result.domains.append(
                    DomainResult(
                        parent_company=company_name,
                        parent_domain=parent_domain,
                        subsidiary_name=sub.name,
                        subsidiary_type=sub.subsidiary_type.value if hasattr(sub.subsidiary_type, 'value') else str(sub.subsidiary_type),
                        jurisdiction=sub.jurisdiction,
                        domain=domain,
                        domain_source=sub.source.value if hasattr(sub.source, 'value') else str(sub.source),
                        confidence=Confidence.HIGH.value,
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

        # 4. DNS verification (optional)
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
