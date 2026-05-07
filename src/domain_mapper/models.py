"""Data models for domain mapping results."""

from dataclasses import dataclass, field
from enum import Enum


class DomainSource(str, Enum):
    SEC_EDGAR = "SEC EDGAR"
    WIKIPEDIA = "Wikipedia"
    TLD_GUESS = "TLD guess"


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
        return [d for d in self.domains if d.domain_source == DomainSource.TLD_GUESS.value]
