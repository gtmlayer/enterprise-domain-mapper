"""Output formatting for domain mapping results."""

import csv
import io
import logging
from typing import TextIO

from domain_mapper.models import CompanyResult, DomainResult

logger = logging.getLogger(__name__)

DETAILED_HEADERS = [
    "parent_company",
    "parent_domain",
    "subsidiary_name",
    "subsidiary_type",
    "jurisdiction",
    "domain",
    "domain_source",
    "dns_verified",
    "confidence",
]

CLAY_HEADERS = [
    "company_name",
    "parent_domain",
    "subsidiary_count",
    "all_domains",
    "confirmed_domains",
    "guessed_domains",
    "verified_domains",
]


def write_detailed_csv(results: list[CompanyResult], output: TextIO) -> None:
    """Write detailed CSV output with one row per subsidiary-domain pair."""
    writer = csv.DictWriter(output, fieldnames=DETAILED_HEADERS)
    writer.writeheader()

    for company in results:
        for domain in company.domains:
            writer.writerow({
                "parent_company": domain.parent_company,
                "parent_domain": domain.parent_domain,
                "subsidiary_name": domain.subsidiary_name,
                "subsidiary_type": domain.subsidiary_type,
                "jurisdiction": domain.jurisdiction,
                "domain": domain.domain,
                "domain_source": domain.domain_source,
                "dns_verified": domain.dns_verified if domain.dns_verified is not None else "",
                "confidence": domain.confidence,
            })


def write_clay_csv(results: list[CompanyResult], output: TextIO) -> None:
    """Write Clay-optimised CSV with one row per company, domains consolidated."""
    writer = csv.DictWriter(output, fieldnames=CLAY_HEADERS)
    writer.writeheader()

    for company in results:
        all_domains = [d.domain for d in company.domains]
        confirmed = [d.domain for d in company.confirmed_domains]
        guessed = [d.domain for d in company.guessed_domains]
        verified = [d.domain for d in company.verified_domains]

        writer.writerow({
            "company_name": company.company_name,
            "parent_domain": company.parent_domain,
            "subsidiary_count": len(company.subsidiaries),
            "all_domains": "; ".join(all_domains),
            "confirmed_domains": "; ".join(confirmed),
            "guessed_domains": "; ".join(guessed),
            "verified_domains": "; ".join(verified),
        })


def results_to_csv_string(results: list[CompanyResult], format: str = "detailed") -> str:
    """Convert results to a CSV string."""
    output = io.StringIO()
    if format == "clay":
        write_clay_csv(results, output)
    else:
        write_detailed_csv(results, output)
    return output.getvalue()
