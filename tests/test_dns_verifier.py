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
    monkeypatch.setattr(dv, "_mx_hosts", lambda d: ["mx.example.com"])
    monkeypatch.setattr(dv, "_check_a", lambda d: True)
    out = DnsVerifier().verify_domains([_result("mail.example.com")])[0]
    assert out.dns_verified is True
    assert out.dns_status == "mx"
    assert out.confidence == Confidence.MEDIUM.value  # promoted from LOW


def test_a_only_is_not_verified_and_does_not_bump(monkeypatch):
    # GTM-665: a parking page that resolves an A record but has no MX must NOT
    # be treated as verified, and must not gain confidence.
    monkeypatch.setattr(dv, "_mx_hosts", lambda d: [])
    monkeypatch.setattr(dv, "_check_a", lambda d: True)
    out = DnsVerifier().verify_domains([_result("parked.example.com")])[0]
    assert out.dns_verified is False
    assert out.dns_status == "a-only"
    assert out.confidence == Confidence.LOW.value


def test_unresolved(monkeypatch):
    monkeypatch.setattr(dv, "_mx_hosts", lambda d: [])
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
    monkeypatch.setattr(dv, "_mx_hosts", lambda d: ["mx.example.com"])
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

        monkeypatch.setattr(
            dv, "_verify_single", lambda d: (d, "mx", ["mx.godaddy-parked.example"])
        )

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


# --- Shared-mail ownership test -------------------------------------------
#
# The discriminator that separates a real regional domain from a parked lookalike.
# jpmorgan.co.uk and jpmorgan.com both sit on us.messagelabs.com; chaseuk.com has live
# mail on GoDaddy and chaseuk.co.uk on IONOS, and neither belongs to Chase.


class TestTenantIdentifiers:
    """A mail vendor is not a tenant. Only a customer id the provider assigned counts."""

    def test_extracts_a_real_customer_id(self):
        """HSBC's Proofpoint hostnames carry 00299f02, which is HSBC and nobody else."""
        got = dv.tenant_identifiers(["mxa-00299f02.gslb.pphosted.com"], "hsbc.co.uk")
        assert got == {"00299f02"}

    def test_microsoft_365_yields_nothing(self):
        """The regression. M365 names every tenant after its own domain, so two
        unrelated companies look identical once the provider's domain is stripped."""
        roper = dv.tenant_identifiers(
            ["ropertech-com.mail.protection.outlook.com"], "ropertech.com"
        )
        funeral_home = dv.tenant_identifiers(
            ["roperandsons-com.mail.protection.outlook.com"], "roperandsons.com"
        )
        assert roper == set()
        assert funeral_home == set()
        assert not (roper & funeral_home)

    def test_a_shared_cluster_number_yields_nothing(self):
        """Symantec clusters are shared by many unrelated customers, and the suffix
        varies: cluster14 and cluster14a are the same shared infrastructure."""
        for host in ("cluster14.us.messagelabs.com", "cluster14a.us.messagelabs.com"):
            assert dv.tenant_identifiers([host], "jpmorgan.com") == set(), host

    def test_a_numbered_gateway_yields_nothing(self):
        assert dv.tenant_identifiers(["gw3197.fortimail.com"], "roperind.net") == set()

    def test_a_customer_id_survives_the_number_stripping(self):
        """00299f02 must not be mistaken for a numbered generic host."""
        assert dv.tenant_identifiers(["mxb-00299f02.gslb.pphosted.com"], "hsbc.fr") == {"00299f02"}

    def test_generic_labels_are_ignored(self):
        assert dv.tenant_identifiers(["mail.protection.outlook.com"], "example.com") == set()
        assert dv.tenant_identifiers(["smtp.secureserver.net"], "chaseuk.com") == set()


class TestMailPointsAt:
    def test_mx_inside_the_target_domain(self):
        """Nobody points their MX inside somebody else's domain."""
        assert dv.mail_points_at(["threshold2.jpmorgan.com"], "jpmorgan.com")

    def test_a_different_domain_does_not_count(self):
        assert not dv.mail_points_at(["threshold2.jpmorgan.com"], "jpmorganchase.com")

    def test_a_vendor_host_does_not_count(self):
        assert not dv.mail_points_at(["mxa-00299f02.gslb.pphosted.com"], "hsbc.com")


class TestCertPromotion:
    """A certificate-observed domain reaches High only on real ownership evidence."""

    @staticmethod
    def _cert(domain, parent="jpmorganchase.com"):
        from domain_mapper.models import DomainResult

        return DomainResult(
            parent_company="Test",
            parent_domain=parent,
            subsidiary_name="(certificate log)",
            subsidiary_type="Observed domain",
            jurisdiction="",
            domain=domain,
            domain_source=DomainSource.CERT_TRANSPARENCY.value,
            confidence=Confidence.MEDIUM.value,
        )

    @staticmethod
    def _wire(monkeypatch, hosts):
        monkeypatch.setattr(dv, "_mx_hosts", lambda d: hosts.get(d, []))
        monkeypatch.setattr(dv, "_verify_single", lambda d: (d, "mx", hosts.get(d, [])))

    def test_a_corroborated_customer_id_promotes(self, monkeypatch):
        hosts = {
            "hsbc.co.uk": ["mxb-00299f02.gslb.pphosted.com"],
            "hsbc.fr": ["mxa-00299f02.gslb.pphosted.com"],
        }
        self._wire(monkeypatch, hosts)
        out = dv.DnsVerifier().verify_domains([self._cert(d, "hsbc.com") for d in hosts])
        assert all(r.confidence == Confidence.HIGH.value for r in out)

    def test_unrelated_microsoft_365_domains_are_not_a_group(self, monkeypatch):
        """The Roper regression: a funeral home must not become a Roper domain."""
        hosts = {
            "roperwhitney.com": ["roperwhitney-com.mail.protection.outlook.com"],
            "roperandsons.com": ["roperandsons-com.mail.protection.outlook.com"],
            "roperandroper.com": ["roperandroper-com.mail.protection.outlook.com"],
        }
        self._wire(monkeypatch, hosts)
        out = dv.DnsVerifier().verify_domains([self._cert(d, "ropertech.com") for d in hosts])
        assert all(r.confidence == Confidence.MEDIUM.value for r in out), [
            (r.domain, r.confidence) for r in out
        ]

    def test_one_domain_cannot_corroborate_itself(self, monkeypatch):
        hosts = {"only-one.com": ["mxa-abcd1234.gslb.pphosted.com"]}
        self._wire(monkeypatch, hosts)
        out = dv.DnsVerifier().verify_domains([self._cert("only-one.com")])
        assert out[0].confidence == Confidence.MEDIUM.value

    def test_mx_inside_the_parent_domain_promotes_alone(self, monkeypatch):
        hosts = {"jpmorganchase.de": ["threshold2.jpmorgan.com"]}
        self._wire(monkeypatch, hosts)
        out = dv.DnsVerifier().verify_domains([self._cert("jpmorganchase.de", "jpmorgan.com")])
        assert out[0].confidence == Confidence.HIGH.value

    def test_a_guess_on_the_group_tenant_still_stays_medium(self, monkeypatch):
        """Deliberate boundary: guesses cap at Medium, which is a separate decision."""
        from domain_mapper.models import DomainResult

        hosts = {
            "a.com": ["mxa-00299f02.gslb.pphosted.com"],
            "b.com": ["mxb-00299f02.gslb.pphosted.com"],
            "guessed.com": ["mxa-00299f02.gslb.pphosted.com"],
        }
        self._wire(monkeypatch, hosts)
        guess = DomainResult(
            parent_company="Test",
            parent_domain="hsbc.com",
            subsidiary_name="Guessed",
            subsidiary_type="Subsidiary",
            jurisdiction="uk",
            domain="guessed.com",
            domain_source=DomainSource.TLD_GUESS.value,
            confidence=Confidence.MEDIUM.value,
        )
        out = dv.DnsVerifier().verify_domains(
            [self._cert("a.com", "hsbc.com"), self._cert("b.com", "hsbc.com"), guess]
        )
        by_domain = {r.domain: r.confidence for r in out}
        assert by_domain["a.com"] == Confidence.HIGH.value
        assert by_domain["guessed.com"] == Confidence.MEDIUM.value
