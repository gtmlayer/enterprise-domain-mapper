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


# --- Null MX and guessed-domain confidence regressions --------------------
#
# Two defects, both measured against the live public repo on 18 Sep 2026 while
# mapping JPMorgan Chase:
#   1. A null MX counted as verified mail.
#   2. A guessed domain that happened to resolve was promoted to High.


class TestNullMx:
    """A published MX record is not proof that a domain accepts mail."""

    def test_rfc7505_null_mx_is_not_mail(self):
        from domain_mapper.dns_verifier import _is_null_mx

        assert _is_null_mx(".") is True

    def test_localhost_mx_is_not_mail(self):
        """luxembourg.com publishes `0 localhost.` and used to score as verified."""
        from domain_mapper.dns_verifier import _is_null_mx

        assert _is_null_mx("localhost.") is True
        assert _is_null_mx("LOCALHOST") is True

    def test_zero_address_mx_is_not_mail(self):
        """unitedkingdom.com publishes `1000 0.0.0.0.` and used to score as verified."""
        from domain_mapper.dns_verifier import _is_null_mx

        assert _is_null_mx("0.0.0.0.") is True

    def test_real_mx_target_is_mail(self):
        from domain_mapper.dns_verifier import _is_null_mx

        for exchange in (
            "cluster14.us.messagelabs.com.",
            "mx1-us1.ppe-hosted.com.",
            "aspmx.l.google.com.",
        ):
            assert _is_null_mx(exchange) is False


class TestGuessedDomainsCannotReachHigh:
    """A guessed domain resolving proves somebody owns it, not that the target does."""

    def test_name_guess_caps_at_medium(self):
        """chaseuk.com is a GoDaddy parked domain and used to score High."""
        from domain_mapper.dns_verifier import _bump_confidence
        from domain_mapper.models import Confidence, DomainSource

        got = _bump_confidence(Confidence.MEDIUM.value, DomainSource.NAME_GUESS.value)
        assert got == Confidence.MEDIUM.value

    def test_tld_guess_caps_at_medium(self):
        from domain_mapper.dns_verifier import _bump_confidence
        from domain_mapper.models import Confidence, DomainSource

        got = _bump_confidence(Confidence.LOW.value, DomainSource.TLD_GUESS.value)
        assert got == Confidence.MEDIUM.value

        got = _bump_confidence(got, DomainSource.TLD_GUESS.value)
        assert got == Confidence.MEDIUM.value

    def test_sourced_domain_may_reach_high(self):
        """A domain that came from a filing or an article is still allowed to be High."""
        from domain_mapper.dns_verifier import _bump_confidence
        from domain_mapper.models import Confidence, DomainSource

        got = _bump_confidence(Confidence.MEDIUM.value, DomainSource.SEC_EDGAR.value)
        assert got == Confidence.HIGH.value

    def test_verify_domains_never_marks_a_guess_high(self, monkeypatch):
        """End to end: an MX hit on a guessed domain stops at Medium."""
        import domain_mapper.dns_verifier as dv
        from domain_mapper.models import Confidence, DomainResult, DomainSource

        monkeypatch.setattr(dv, "_verify_single", lambda d: (d, "mx"))

        results = [
            DomainResult(
                parent_company="JPMorgan Chase",
                parent_domain="jpmorganchase.com",
                subsidiary_name="Chase UK",
                subsidiary_type="Subsidiary",
                jurisdiction="uk",
                domain=domain,
                domain_source=source,
                confidence=Confidence.MEDIUM.value,
            )
            for domain, source in (
                ("chaseuk.com", DomainSource.NAME_GUESS.value),
                ("luxembourg.com", DomainSource.NAME_GUESS.value),
                ("unitedkingdom.com", DomainSource.TLD_GUESS.value),
            )
        ]

        for result in dv.DnsVerifier().verify_domains(results):
            assert result.confidence != Confidence.HIGH.value, result.domain
