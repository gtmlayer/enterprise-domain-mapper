"""TLD pattern generator for guessing subsidiary domains by jurisdiction."""

import logging
import re

from domain_mapper.models import Confidence, DomainResult, DomainSource, Subsidiary

logger = logging.getLogger(__name__)

# Map jurisdictions to likely TLD patterns
# Each jurisdiction maps to a list of TLD suffixes to try
JURISDICTION_TLD_MAP = {
    # Europe
    "united kingdom": [".co.uk", ".uk"],
    "uk": [".co.uk", ".uk"],
    "england": [".co.uk", ".uk"],
    "scotland": [".co.uk", ".uk"],
    "wales": [".co.uk", ".uk"],
    "germany": [".de"],
    "deutschland": [".de"],
    "france": [".fr"],
    "italy": [".it"],
    "italia": [".it"],
    "spain": [".es"],
    "netherlands": [".nl"],
    "belgium": [".be"],
    "austria": [".at"],
    "switzerland": [".ch"],
    "sweden": [".se"],
    "norway": [".no"],
    "denmark": [".dk"],
    "finland": [".fi"],
    "ireland": [".ie"],
    "portugal": [".pt"],
    "poland": [".pl"],
    "czech republic": [".cz"],
    "czechia": [".cz"],
    "hungary": [".hu"],
    "romania": [".ro"],
    "greece": [".gr"],
    "croatia": [".hr"],
    "slovakia": [".sk"],
    "slovenia": [".si"],
    "bulgaria": [".bg"],
    "estonia": [".ee"],
    "latvia": [".lv"],
    "lithuania": [".lt"],
    "luxembourg": [".lu"],
    "malta": [".com.mt"],
    "cyprus": [".com.cy"],
    "iceland": [".is"],
    # Asia Pacific
    "japan": [".co.jp", ".jp"],
    "china": [".cn", ".com.cn"],
    "south korea": [".co.kr", ".kr"],
    "korea": [".co.kr", ".kr"],
    "india": [".in", ".co.in"],
    "australia": [".com.au", ".au"],
    "new zealand": [".co.nz", ".nz"],
    "singapore": [".sg", ".com.sg"],
    "hong kong": [".hk", ".com.hk"],
    "taiwan": [".tw", ".com.tw"],
    "malaysia": [".my", ".com.my"],
    "thailand": [".th", ".co.th"],
    "indonesia": [".id", ".co.id"],
    "philippines": [".ph", ".com.ph"],
    "vietnam": [".vn", ".com.vn"],
    "pakistan": [".pk", ".com.pk"],
    "bangladesh": [".bd", ".com.bd"],
    # Americas
    "canada": [".ca"],
    "mexico": [".mx", ".com.mx"],
    "brazil": [".com.br", ".br"],
    "argentina": [".com.ar", ".ar"],
    "chile": [".cl"],
    "colombia": [".co", ".com.co"],
    "peru": [".pe", ".com.pe"],
    # Middle East and Africa
    "united arab emirates": [".ae"],
    "uae": [".ae"],
    "saudi arabia": [".sa", ".com.sa"],
    "israel": [".il", ".co.il"],
    "turkey": [".com.tr", ".tr"],
    "south africa": [".co.za", ".za"],
    "nigeria": [".ng", ".com.ng"],
    "kenya": [".co.ke", ".ke"],
    "egypt": [".eg", ".com.eg"],
    "morocco": [".ma"],
    # Other
    "russia": [".ru"],
    "ukraine": [".ua", ".com.ua"],
}


def _normalise_jurisdiction(jurisdiction: str) -> str:
    """Normalise a jurisdiction string for lookup."""
    j = jurisdiction.lower().strip()
    # Remove common prefixes/suffixes
    for remove in ["state of ", "republic of ", "kingdom of ", "commonwealth of "]:
        j = j.removeprefix(remove)
    return j.strip()


def _extract_domain_base(name: str, parent_domain: str) -> list[str]:
    """Generate possible domain base strings from a subsidiary name and parent domain."""
    bases = set()

    # Use the parent domain's base (e.g. "nttdata" from "nttdata.com")
    if parent_domain:
        parent_base = parent_domain.split(".")[0]
        bases.add(parent_base)

    # Try to create a domain-friendly version of the subsidiary name
    clean = re.sub(r"[^a-zA-Z0-9\s]", "", name)
    words = clean.lower().split()
    if words:
        # Join all words
        bases.add("".join(words))
        # First two words
        if len(words) >= 2:
            bases.add("".join(words[:2]))
        # Hyphenated
        if len(words) >= 2:
            bases.add("-".join(words[:3]))

    return [b for b in bases if len(b) > 2]


class TldGenerator:
    """Generate likely domain patterns from subsidiary jurisdiction data."""

    def generate_domains(
        self,
        subsidiaries: list[Subsidiary],
        parent_company: str,
        parent_domain: str = "",
    ) -> list[DomainResult]:
        """Generate guessed domains for each subsidiary based on jurisdiction."""
        results = []
        seen_domains = set()

        for sub in subsidiaries:
            jurisdiction = _normalise_jurisdiction(sub.jurisdiction)
            tlds = JURISDICTION_TLD_MAP.get(jurisdiction, [])

            if not tlds and len(jurisdiction) > 2:
                # Try partial matching
                for key, value in JURISDICTION_TLD_MAP.items():
                    if key in jurisdiction or jurisdiction in key:
                        tlds = value
                        break

            if not tlds:
                continue

            domain_bases = _extract_domain_base(sub.name, parent_domain)

            for base in domain_bases:
                for tld in tlds:
                    domain = f"{base}{tld}"
                    if domain not in seen_domains:
                        seen_domains.add(domain)
                        results.append(
                            DomainResult(
                                parent_company=parent_company,
                                parent_domain=parent_domain,
                                subsidiary_name=sub.name,
                                subsidiary_type=sub.subsidiary_type.value if hasattr(sub.subsidiary_type, 'value') else str(sub.subsidiary_type),
                                jurisdiction=sub.jurisdiction,
                                domain=domain,
                                domain_source=DomainSource.TLD_GUESS.value,
                                confidence=Confidence.LOW.value,
                            )
                        )

        logger.info(f"TLD generator: produced {len(results)} domain guesses")
        return results
