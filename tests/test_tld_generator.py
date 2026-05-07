"""Tests for TLD pattern generation."""

from domain_mapper.models import DomainSource, Subsidiary, SubsidiaryType
from domain_mapper.sources.tld_generator import TldGenerator, _normalise_jurisdiction


def test_normalise_jurisdiction():
    assert _normalise_jurisdiction("United Kingdom") == "united kingdom"
    assert _normalise_jurisdiction("  Germany  ") == "germany"
    assert _normalise_jurisdiction("Republic of Ireland") == "ireland"
    assert _normalise_jurisdiction("State of Delaware") == "delaware"


def test_generate_domains_uk():
    generator = TldGenerator()
    subs = [
        Subsidiary(
            name="NTT DATA UK",
            jurisdiction="United Kingdom",
            subsidiary_type=SubsidiaryType.SUBSIDIARY,
            source=DomainSource.SEC_EDGAR,
        )
    ]

    results = generator.generate_domains(subs, "NTT Data", "nttdata.com")

    domains = [r.domain for r in results]
    assert any(".co.uk" in d for d in domains), f"Expected .co.uk domain in {domains}"
    assert all(r.domain_source == DomainSource.TLD_GUESS.value for r in results)
    assert all(r.confidence == "Low" for r in results)


def test_generate_domains_japan():
    generator = TldGenerator()
    subs = [
        Subsidiary(
            name="NTT DATA Japan",
            jurisdiction="Japan",
            subsidiary_type=SubsidiaryType.REGIONAL_ENTITY,
            source=DomainSource.WIKIPEDIA,
        )
    ]

    results = generator.generate_domains(subs, "NTT Data", "nttdata.com")

    domains = [r.domain for r in results]
    assert any(".co.jp" in d or ".jp" in d for d in domains), f"Expected .jp domain in {domains}"


def test_generate_domains_no_jurisdiction():
    generator = TldGenerator()
    subs = [
        Subsidiary(
            name="Mystery Sub",
            jurisdiction="",
            subsidiary_type=SubsidiaryType.SUBSIDIARY,
            source=DomainSource.SEC_EDGAR,
        )
    ]

    results = generator.generate_domains(subs, "Parent", "parent.com")
    # No jurisdiction means no TLD guesses
    assert len(results) == 0


def test_generate_domains_deduplication():
    generator = TldGenerator()
    subs = [
        Subsidiary(name="TestCo UK", jurisdiction="United Kingdom", source=DomainSource.SEC_EDGAR),
        Subsidiary(name="TestCo UK", jurisdiction="UK", source=DomainSource.WIKIPEDIA),
    ]

    results = generator.generate_domains(subs, "TestCo", "testco.com")
    domains = [r.domain for r in results]
    # Should not have duplicates
    assert len(domains) == len(set(domains))
