"""Tests for the certificate transparency source.

Exhibit 21 lists significant subsidiaries and Wikipedia lists what an editor typed.
Neither is a brand register, so neither can answer "what are all their domains". The
certificate logs can, because every publicly trusted certificate is published.
"""

import domain_mapper.sources.cert_transparency as ct
from domain_mapper.models import Confidence, DomainSource


class TestRegistrableDomain:
    def test_handles_multi_part_public_suffixes(self):
        """Naive last-two-labels turns every UK domain into "co.uk"."""
        assert ct.registrable_domain("www.hsbc.co.uk") == "hsbc.co.uk"
        assert ct.registrable_domain("a.b.chase.com.au") == "chase.com.au"
        assert ct.registrable_domain("mail.jpmorgan.com") == "jpmorgan.com"

    def test_strips_wildcards(self):
        assert ct.registrable_domain("*.jpmorganam.fr") == "jpmorganam.fr"

    def test_rejects_junk(self):
        for value in ("", "   ", "localhost", "not a host"):
            assert ct.registrable_domain(value) == ""


class TestDeriveStem:
    def test_prefers_the_shorter_brand_token(self):
        """ "jpmorganchase" misses jpmorganam.fr and jpmorganassetmanagement.de."""
        assert ct.derive_stem("JPMorgan Chase", "jpmorganchase.com") == "jpmorgan"

    def test_falls_back_to_the_parent_label(self):
        assert ct.derive_stem("HSBC", "hsbc.com") == "hsbc"
        assert ct.derive_stem("Boeing", "boeing.com") == "boeing"

    def test_uses_the_company_name_when_no_parent_domain(self):
        assert ct.derive_stem("Boeing", "") == "boeing"


class TestStemMustBeInTheRegistrableLabel:
    def test_rejects_a_lookalike_subdomain(self):
        """A cert for jpmorgan.example-phish.com must not register example-phish.com."""
        rows = [{"name_value": "jpmorgan.example-phish.com", "common_name": ""}]
        assert ct.CertTransparencySource._count_by_domain(rows, "jpmorgan") == {}

    def test_counts_a_genuine_match(self):
        rows = [
            {"name_value": "www.jpmorganam.fr\n*.jpmorganam.fr", "common_name": ""},
            {"name_value": "jpmorganam.fr", "common_name": ""},
        ]
        assert ct.CertTransparencySource._count_by_domain(rows, "jpmorgan") == {"jpmorganam.fr": 3}


class TestFailureIsTotalNotPartial:
    def test_returns_nothing_when_the_log_is_unreachable(self, monkeypatch, caplog):
        """A half-answer that looks whole is worse than none: a caller cannot tell a
        company with few domains from a query that timed out."""

        def boom(*args, **kwargs):
            raise TimeoutError("crt.sh timed out")

        monkeypatch.setattr(ct, "_read_cache", lambda stem: None)
        monkeypatch.setattr(ct.requests, "get", boom)

        got = ct.CertTransparencySource().get_domains("JPMorgan Chase", "jpmorganchase.com")

        assert got == []
        assert "Returning no candidates rather than a partial list" in caplog.text

    def test_returns_nothing_without_a_usable_stem(self, monkeypatch):
        monkeypatch.setattr(ct, "_read_cache", lambda stem: None)
        assert ct.CertTransparencySource().get_domains("", "") == []


class TestCandidatesStartAtMedium:
    def test_observed_domains_are_medium_not_high(self, monkeypatch):
        """A certificate proves someone operated the name, not that this company did."""
        rows = [{"name_value": "jpmorganfunds.com", "common_name": ""}] * 3
        monkeypatch.setattr(ct, "_read_cache", lambda stem: rows)

        got = ct.CertTransparencySource().get_domains("JPMorgan Chase", "jpmorganchase.com")

        assert [d.domain for d in got] == ["jpmorganfunds.com"]
        assert got[0].confidence == Confidence.MEDIUM.value
        assert got[0].domain_source == DomainSource.CERT_TRANSPARENCY.value

    def test_the_parent_domain_is_not_returned_as_its_own_subsidiary(self, monkeypatch):
        rows = [{"name_value": "jpmorganchase.com", "common_name": ""}]
        monkeypatch.setattr(ct, "_read_cache", lambda stem: rows)
        got = ct.CertTransparencySource().get_domains("JPMorgan Chase", "jpmorganchase.com")
        assert got == []
