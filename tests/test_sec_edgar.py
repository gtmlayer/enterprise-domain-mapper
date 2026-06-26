"""Tests for the SEC EDGAR source (regression coverage for GTM-666)."""

import responses

from domain_mapper.sources.sec_edgar import (
    SecEdgarSource,
    _match_cik_from_tickers,
    _normalise_company,
)

# Shape mirrors the real company_tickers.json payload.
TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
    "2": {"cik_str": 111111, "ticker": "APLE", "title": "Apple Hospitality REIT, Inc."},
}


def test_normalise_company_strips_suffixes():
    assert _normalise_company("Apple Inc.") == "apple"
    assert _normalise_company("MICROSOFT CORP") == "microsoft"
    assert _normalise_company("The Boeing Company") == "boeing"


def test_exact_match_beats_substring():
    # "Apple" must resolve to Apple Inc. (exact), not Apple Hospitality REIT.
    assert _match_cik_from_tickers(TICKERS, "Apple") == "0000320193"


def test_match_ignoring_corporate_suffix():
    assert _match_cik_from_tickers(TICKERS, "Microsoft Corporation") == "0000789019"


def test_no_match_returns_none():
    assert _match_cik_from_tickers(TICKERS, "Nonexistent Zzz Co") is None


def test_returns_zero_padded_cik():
    cik = _match_cik_from_tickers(TICKERS, "Microsoft")
    assert cik is not None and len(cik) == 10 and cik.isdigit()


@responses.activate
def test_parse_exhibit_21_extracts_name_and_jurisdiction():
    html = (
        "<html><body><pre>"
        "Exhibit 21.1\n"
        "Subsidiaries of the Registrant\n"
        "Name of Subsidiary                     Jurisdiction\n"
        "Acme Europe Ltd                        United Kingdom\n"
        "Acme Hellas SA                         Greece\n"
        "</pre></body></html>"
    )
    url = "https://www.sec.gov/Archives/edgar/data/1/x/ex21.htm"
    responses.add(responses.GET, url, body=html, status=200)

    subs = SecEdgarSource()._parse_exhibit_21(url)
    by_name = {s.name: s.jurisdiction for s in subs}

    assert "Acme Europe Ltd" in by_name
    assert by_name["Acme Europe Ltd"] == "United Kingdom"
    assert by_name["Acme Hellas SA"] == "Greece"
