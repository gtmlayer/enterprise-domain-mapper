"""Tests for the CLI (regression coverage for GTM-669)."""

from click.testing import CliRunner

from domain_mapper import cli as cli_mod
from domain_mapper.cli import _detect_columns, main
from domain_mapper.models import CompanyResult, DomainResult, DomainSource


def test_detect_columns():
    company, domain = _detect_columns(["Company", "Website"])
    assert company == "Company"
    assert domain == "Website"


def test_empty_input_errors():
    # GTM-669: whitespace-only input is rejected, not mapped as a company.
    result = CliRunner().invoke(main, ["   "])
    assert result.exit_code != 0
    assert "no input" in result.output.lower()


def test_missing_csv_errors():
    # GTM-669: a .csv path that does not exist errors clearly.
    result = CliRunner().invoke(main, ["definitely_missing_file.csv"])
    assert result.exit_code != 0
    assert "not found" in result.output.lower()


def test_single_company_happy_path(monkeypatch):
    def fake_map(self, name, parent_domain=""):
        cr = CompanyResult(company_name=name, parent_domain=parent_domain)
        cr.domains = [
            DomainResult(
                parent_company=name,
                parent_domain=parent_domain,
                subsidiary_name="Sub",
                subsidiary_type="Subsidiary",
                jurisdiction="",
                domain="sub.example.com",
                domain_source=DomainSource.TLD_GUESS.value,
            )
        ]
        return cr

    monkeypatch.setattr(cli_mod.DomainMapper, "map_company", fake_map)
    result = CliRunner().invoke(main, ["Acme"])
    assert result.exit_code == 0
    assert "Acme" in result.output
