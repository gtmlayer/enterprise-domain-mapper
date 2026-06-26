"""Tests for the orchestrator (regression coverage for GTM-677 + dedup)."""

from domain_mapper.mapper import DomainMapper
from domain_mapper.models import Confidence, DomainSource, Subsidiary, SubsidiaryType


def test_guess_direct_domain():
    m = DomainMapper()
    assert m._guess_direct_domain("Acme Corp", "parent.com") == "acmecorp.com"
    assert m._guess_direct_domain("", "p.com") is None
    # Same as the parent domain -> skip
    assert m._guess_direct_domain("Parent", "parent.com") is None
    # Implausibly long -> skip
    assert m._guess_direct_domain("A" * 30, "p.com") is None


def test_direct_guess_is_not_high_confidence(monkeypatch):
    # GTM-677: an unverified name-based .com guess must not be HIGH confidence.
    m = DomainMapper()
    monkeypatch.setattr(
        m.sec_edgar,
        "get_subsidiaries",
        lambda name: [
            Subsidiary(
                name="Acme Brand",
                subsidiary_type=SubsidiaryType.SUBSIDIARY,
                source=DomainSource.SEC_EDGAR,
            )
        ],
    )
    monkeypatch.setattr(m.wikipedia, "get_subsidiaries", lambda name: [])

    result = m.map_company("Acme", "acme.com")
    direct = [d for d in result.domains if d.domain == "acmebrand.com"]

    assert direct, "expected a direct .com guess"
    assert direct[0].confidence == Confidence.MEDIUM.value
    assert direct[0].confidence != Confidence.HIGH.value
    # GTM-677 provenance: a name-guessed .com must not claim the SEC/Wikipedia source.
    assert direct[0].domain_source == DomainSource.NAME_GUESS.value


def test_subsidiaries_are_deduplicated_case_insensitively(monkeypatch):
    m = DomainMapper()
    monkeypatch.setattr(
        m.sec_edgar,
        "get_subsidiaries",
        lambda name: [Subsidiary(name="Dup Co", source=DomainSource.SEC_EDGAR)],
    )
    monkeypatch.setattr(
        m.wikipedia,
        "get_subsidiaries",
        lambda name: [Subsidiary(name="dup co", source=DomainSource.WIKIPEDIA)],
    )

    result = m.map_company("X", "x.com")
    assert len(result.subsidiaries) == 1
