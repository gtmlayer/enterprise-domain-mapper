"""Data models for domain mapping results."""

from dataclasses import dataclass, field
from enum import Enum


class DomainSource(str, Enum):
    SEC_EDGAR = "SEC EDGAR"
    WIKIPEDIA = "Wikipedia"
    TLD_GUESS = "TLD guess"
    # A .com guessed from the entity's name. The entity came from SEC/Wikipedia,
    # but the domain itself is a guess, so it is tagged here rather than claiming
    # the filing/article as the domain's source.
    NAME_GUESS = "Name guess"
    # Observed in a public certificate transparency log. Not a guess: the company
    # demonstrably operated this name at some point. Still not proof of ownership,
    # since anyone can obtain a certificate for a name they control.
    CERT_TRANSPARENCY = "Certificate log"


class Confidence(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class SubsidiaryType(str, Enum):
    SUBSIDIARY = "Subsidiary"
    ACQUISITION = "Acquisition"
    DIVISION = "Division"
    MEMBER_FIRM = "Member firm"
    REGIONAL_ENTITY = "Regional entity"
    UNKNOWN = "Unknown"


@dataclass
class Subsidiary:
    """A subsidiary or related entity of a parent company."""

    name: str
    jurisdiction: str = ""
    subsidiary_type: SubsidiaryType = SubsidiaryType.UNKNOWN
    source: DomainSource = DomainSource.TLD_GUESS


@dataclass
class DomainResult:
    """A single domain mapping result."""

    parent_company: str
    parent_domain: str
    subsidiary_name: str
    subsidiary_type: str
    jurisdiction: str
    domain: str
    domain_source: str
    dns_verified: bool | None = None
    # How the domain resolved during DNS verification:
    #   "mx"         -> has MX records (mail infrastructure) = treated as verified
    #   "a-only"     -> resolves an A record but no MX (e.g. parking page) = weaker signal
    #   "unresolved" -> did not resolve at all
    #   None         -> not checked (verification skipped, or a confirmed-source domain)
    dns_status: str | None = None
    confidence: str = "Low"


@dataclass
class CompanyResult:
    """All mapping results for a single company."""

    company_name: str
    parent_domain: str = ""
    subsidiaries: list[Subsidiary] = field(default_factory=list)
    domains: list[DomainResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def confirmed_domains(self) -> list[DomainResult]:
        return [d for d in self.domains if d.confidence == Confidence.HIGH.value]

    @property
    def verified_domains(self) -> list[DomainResult]:
        return [d for d in self.domains if d.dns_verified is True]

    @property
    def guessed_domains(self) -> list[DomainResult]:
        guess_sources = {DomainSource.TLD_GUESS.value, DomainSource.NAME_GUESS.value}
        return [d for d in self.domains if d.domain_source in guess_sources]
