"""Tests for data models."""

from domain_mapper.models import (
    CompanyResult,
    Confidence,
    DomainResult,
    DomainSource,
    Subsidiary,
    SubsidiaryType,
)


def test_subsidiary_defaults():
    sub = Subsidiary(name="Test Corp")
    assert sub.name == "Test Corp"
    assert sub.jurisdiction == ""
    assert sub.subsidiary_type == SubsidiaryType.UNKNOWN


def test_domain_result_defaults():
    result = DomainResult(
        parent_company="Parent",
        parent_domain="parent.com",
        subsidiary_name="Sub",
        subsidiary_type="Subsidiary",
        jurisdiction="UK",
        domain="sub.co.uk",
        domain_source="TLD guess",
    )
    assert result.dns_verified is None
    assert result.confidence == "Low"


def test_company_result_properties():
    result = CompanyResult(company_name="Test")
    result.domains = [
        DomainResult(
            parent_company="Test",
            parent_domain="test.com",
            subsidiary_name="Sub1",
            subsidiary_type="Subsidiary",
            jurisdiction="UK",
            domain="test.co.uk",
            domain_source=DomainSource.TLD_GUESS.value,
            confidence=Confidence.HIGH.value,
        ),
        DomainResult(
            parent_company="Test",
            parent_domain="test.com",
            subsidiary_name="Sub2",
            subsidiary_type="Subsidiary",
            jurisdiction="Germany",
            domain="test.de",
            domain_source=DomainSource.TLD_GUESS.value,
            dns_verified=True,
            confidence=Confidence.MEDIUM.value,
        ),
        DomainResult(
            parent_company="Test",
            parent_domain="test.com",
            subsidiary_name="Sub3",
            subsidiary_type="Subsidiary",
            jurisdiction="France",
            domain="test.fr",
            domain_source=DomainSource.TLD_GUESS.value,
            dns_verified=False,
            confidence=Confidence.LOW.value,
        ),
    ]

    assert len(result.confirmed_domains) == 1
    assert len(result.verified_domains) == 1
    assert len(result.guessed_domains) == 3
