"""Tests for output formatting."""

import csv
import io

from domain_mapper.models import CompanyResult, Confidence, DomainResult, DomainSource, Subsidiary
from domain_mapper.output import write_clay_csv, write_detailed_csv


def _make_test_result() -> CompanyResult:
    result = CompanyResult(company_name="TestCo", parent_domain="testco.com")
    result.subsidiaries = [Subsidiary(name="TestCo UK")]
    result.domains = [
        DomainResult(
            parent_company="TestCo",
            parent_domain="testco.com",
            subsidiary_name="TestCo UK",
            subsidiary_type="Subsidiary",
            jurisdiction="UK",
            domain="testco.co.uk",
            domain_source=DomainSource.TLD_GUESS.value,
            dns_verified=True,
            confidence=Confidence.MEDIUM.value,
        ),
        DomainResult(
            parent_company="TestCo",
            parent_domain="testco.com",
            subsidiary_name="TestCo Germany",
            subsidiary_type="Subsidiary",
            jurisdiction="Germany",
            domain="testco.de",
            domain_source=DomainSource.TLD_GUESS.value,
            dns_verified=False,
            confidence=Confidence.LOW.value,
        ),
    ]
    return result


def test_detailed_csv():
    result = _make_test_result()
    output = io.StringIO()
    write_detailed_csv([result], output)

    output.seek(0)
    reader = csv.DictReader(output)
    rows = list(reader)

    assert len(rows) == 2
    assert rows[0]["parent_company"] == "TestCo"
    assert rows[0]["domain"] == "testco.co.uk"
    assert rows[0]["dns_verified"] == "True"
    assert rows[1]["domain"] == "testco.de"


def test_clay_csv():
    result = _make_test_result()
    output = io.StringIO()
    write_clay_csv([result], output)

    output.seek(0)
    reader = csv.DictReader(output)
    rows = list(reader)

    assert len(rows) == 1
    assert rows[0]["company_name"] == "TestCo"
    assert "testco.co.uk" in rows[0]["all_domains"]
    assert "testco.de" in rows[0]["all_domains"]
    assert rows[0]["subsidiary_count"] == "1"
