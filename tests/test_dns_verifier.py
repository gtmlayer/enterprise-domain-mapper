"""Tests for DNS verification (regression coverage for GTM-663/664/665)."""

import logging

import domain_mapper.dns_verifier as dv
from domain_mapper.dns_verifier import DnsVerifier, _bump_confidence
from domain_mapper.models import Confidence, DomainResult, DomainSource


def _result(domain: str, confidence: str = Confidence.LOW.value) -> DomainResult:
    return DomainResult(
        parent_company="P",
        parent_domain="p.com",
        subsidiary_name="S",
        subsidiary_type="Subsidiary",
        jurisdiction="",
        domain=domain,
        domain_source=DomainSource.TLD_GUESS.value,
        confidence=confidence,
    )


def test_bump_confidence_ladder():
    assert _bump_confidence(Confidence.LOW.value) == Confidence.MEDIUM.value
    assert _bump_confidence(Confidence.MEDIUM.value) == Confidence.HIGH.value
    assert _bump_confidence(Confidence.HIGH.value) == Confidence.HIGH.value


def test_mx_marks_verified_and_bumps(monkeypatch):
    monkeypatch.setattr(dv, "_check_mx", lambda d: True)
    monkeypatch.setattr(dv, "_check_a", lambda d: True)
    out = DnsVerifier().verify_domains([_result("mail.example.com")])[0]
    assert out.dns_verified is True
    assert out.dns_status == "mx"
    assert out.confidence == Confidence.MEDIUM.value  # promoted from LOW


def test_a_only_is_not_verified_and_does_not_bump(monkeypatch):
    # GTM-665: a parking page that resolves an A record but has no MX must NOT
    # be treated as verified, and must not gain confidence.
    monkeypatch.setattr(dv, "_check_mx", lambda d: False)
    monkeypatch.setattr(dv, "_check_a", lambda d: True)
    out = DnsVerifier().verify_domains([_result("parked.example.com")])[0]
    assert out.dns_verified is False
    assert out.dns_status == "a-only"
    assert out.confidence == Confidence.LOW.value


def test_unresolved(monkeypatch):
    monkeypatch.setattr(dv, "_check_mx", lambda d: False)
    monkeypatch.setattr(dv, "_check_a", lambda d: False)
    out = DnsVerifier().verify_domains([_result("nope.invalid")])[0]
    assert out.dns_verified is False
    assert out.dns_status == "unresolved"


def test_missing_dnspython_warns_instead_of_silent_noop(monkeypatch, caplog):
    # GTM-664: when dnspython is absent we must say so, not silently no-op MX.
    monkeypatch.setattr(dv, "HAS_DNSPYTHON", False)
    monkeypatch.setattr(dv, "_check_a", lambda d: True)
    with caplog.at_level(logging.WARNING):
        out = DnsVerifier().verify_domains([_result("x.example.com")])[0]
    assert any("dnspython" in m for m in caplog.messages)
    assert out.dns_status == "a-only"


def test_check_mx_returns_false_without_dnspython(monkeypatch):
    monkeypatch.setattr(dv, "HAS_DNSPYTHON", False)
    assert dv._check_mx("example.com") is False


def test_already_verified_results_are_left_untouched(monkeypatch):
    monkeypatch.setattr(dv, "_check_mx", lambda d: True)
    r = _result("confirmed.example.com")
    r.dns_verified = True  # pretend it came confirmed from a source
    out = DnsVerifier().verify_domains([r])[0]
    assert out.dns_status is None  # untouched
