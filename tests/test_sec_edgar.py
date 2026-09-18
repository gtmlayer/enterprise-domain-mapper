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


# --- EDGAR name-matching and Exhibit 21 parsing regressions ----------------
#
# The EDGAR source matched company names by bare substring containment, so a title
# that collapsed to one generic token under suffix stripping could swallow a longer
# query. Measured 18 Sep 2026: "JP Morgan" resolved to MORGAN GROUP HOLDING CO,
# because "MORGAN GROUP HOLDING CO" normalises to "morgan", which is contained in
# "jp morgan", and the shortest-title tie-break preferred it over the real filer.


def _tickers(*titles: str) -> dict:
    """Minimal company_tickers.json fixture: {index: {cik_str, ticker, title}}."""
    return {
        str(i): {"cik_str": 1000 + i, "ticker": f"T{i}", "title": title}
        for i, title in enumerate(titles)
    }


class TestCikMatchingIsTokenAnchored:
    def test_spaced_query_does_not_match_suffix_collapsed_title(self):
        """The measured failure: "JP Morgan" must not land on MORGAN GROUP HOLDING CO."""
        from domain_mapper.sources.sec_edgar import _match_cik_from_tickers

        tickers = _tickers("MORGAN GROUP HOLDING CO", "JPMORGAN CHASE & CO")
        cik = _match_cik_from_tickers(tickers, "JP Morgan")

        assert cik == "0000001001"  # JPMORGAN CHASE & CO, not the Morgan Group

    def test_despaced_query_matches_the_real_filer(self):
        from domain_mapper.sources.sec_edgar import _match_cik_from_tickers

        tickers = _tickers("JPMORGAN CHASE & CO")
        assert _match_cik_from_tickers(tickers, "JP Morgan") == "0000001000"
        assert _match_cik_from_tickers(tickers, "JPMorgan") == "0000001000"
        assert _match_cik_from_tickers(tickers, "JPMorgan Chase") == "0000001000"

    def test_suffix_collapsed_title_alone_yields_nothing(self):
        """With only the wrong company present, the answer is None, not that company."""
        from domain_mapper.sources.sec_edgar import _match_cik_from_tickers

        tickers = _tickers("MORGAN GROUP HOLDING CO")
        assert _match_cik_from_tickers(tickers, "JP Morgan") is None

    def test_non_us_company_resolves_no_cik(self):
        """EDGAR holds SEC registrants only, so a non-US name must return nothing.

        Each of these previously returned a real but unrelated US filer's
        subsidiary list, because a shared fragment was enough to match.
        """
        from domain_mapper.sources.sec_edgar import _match_cik_from_tickers

        tickers = _tickers(
            "ABERCROMBIE & FITCH CO",
            "TELLABS INC",
            "JPMORGAN CHASE & CO",
            "MORGAN GROUP HOLDING CO",
        )
        for name in ("Nestle SA", "Siemens AG", "Carrefour SA"):
            assert _match_cik_from_tickers(tickers, name) is None, name

    def test_exact_match_still_works(self):
        from domain_mapper.sources.sec_edgar import _match_cik_from_tickers

        tickers = _tickers("BOEING CO", "MICROSOFT CORP", "BERKSHIRE HATHAWAY INC")
        assert _match_cik_from_tickers(tickers, "Boeing") == "0000001000"
        assert _match_cik_from_tickers(tickers, "Microsoft") == "0000001001"
        assert _match_cik_from_tickers(tickers, "Berkshire Hathaway") == "0000001002"


class TestExhibit21EntityFilter:
    """Exhibit 21 is a rendered table; a naive text pass keeps its furniture."""

    def test_rejects_table_headers(self):
        from domain_mapper.sources.sec_edgar import _is_entity_name

        for header in (
            "Document",
            "Organized Under",
            "The Laws Of",
            "Percentage of",
            "Voting",
            "Jurisdiction",
            "Name of Subsidiary",
        ):
            assert _is_entity_name(header) is False, header

    def test_rejects_bare_jurisdictions(self):
        from domain_mapper.sources.sec_edgar import _is_entity_name

        for place in (
            "Delaware",
            "Illinois",
            "United States",
            "United Kingdom",
            "Germany",
            "Luxembourg",
        ):
            assert _is_entity_name(place) is False, place

    def test_rejects_filenames_and_exhibit_labels(self):
        from domain_mapper.sources.sec_edgar import _is_entity_name

        for junk in ("EX-21.1", "ex211-q425.htm", "brhc10050563_ex21-1.htm"):
            assert _is_entity_name(junk) is False, junk

    def test_rejects_dates_and_numbers(self):
        from domain_mapper.sources.sec_edgar import _is_entity_name

        for junk in ("December 31, 2025", "as of December 28, 2012", "12/31/2025", "100%"):
            assert _is_entity_name(junk) is False, junk

    def test_keeps_real_entities(self):
        from domain_mapper.sources.sec_edgar import _is_entity_name

        for name in (
            "Paymentech, LLC",
            "J.P. Morgan Securities plc",
            "J.P. Morgan SE",
            "One Equity Partners",
            "JPMorgan Chase Holdings LLC",
            "Chase Bank USA, National Association",
            "Banco J.P. Morgan S.A.",
        ):
            assert _is_entity_name(name) is True, name

    def test_keeps_an_entity_named_after_a_jurisdiction(self):
        """A bare "Delaware" is furniture; "Delaware Trust Company" is a company."""
        from domain_mapper.sources.sec_edgar import _is_entity_name

        assert _is_entity_name("Delaware Trust Company") is True


class TestFullTextFallbackIsGated:
    """The full-text fallback is how a non-US company reached a US filer's subsidiaries.

    EDGAR full-text search returns any filing that MENTIONS the phrase, so its top
    hit is routinely a different company. The hit's own entity name must clear the
    name match before its CIK is used.
    """

    def test_display_name_is_stripped_to_the_entity(self):
        from domain_mapper.sources.sec_edgar import _strip_display_name

        got = _strip_display_name("JPMORGAN CHASE & CO  (JPM)  (CIK 0000019617)")
        assert got == "JPMORGAN CHASE & CO"

    def test_unrelated_filer_does_not_match_the_query(self):
        from domain_mapper.sources.sec_edgar import _names_match

        assert _names_match("Nestle SA", "ABERCROMBIE & FITCH CO") is False
        assert _names_match("Siemens AG", "TELLABS INC") is False
        assert _names_match("Carrefour SA", "MORGAN GROUP HOLDING CO") is False

    def test_the_right_filer_does_match(self):
        from domain_mapper.sources.sec_edgar import _names_match

        assert _names_match("JP Morgan", "JPMORGAN CHASE & CO") is True
        assert _names_match("Boeing", "BOEING CO") is True

    def test_find_cik_returns_none_when_no_hit_name_matches(self, monkeypatch):
        """End to end on the fallback: hits exist, none match, so the answer is None."""
        import domain_mapper.sources.sec_edgar as se

        class _Resp:
            status_code = 200

            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        def fake_get(url, **kwargs):
            if "company_tickers" in url:
                # Registry finds nothing, as it should for a non-US company.
                return _Resp({})
            # Full-text search returns a real but unrelated US filer.
            return _Resp(
                {
                    "hits": {
                        "hits": [
                            {
                                "_source": {
                                    "ciks": ["0001018840"],
                                    "display_names": [
                                        "ABERCROMBIE & FITCH CO  (ANF)  (CIK 0001018840)"
                                    ],
                                }
                            }
                        ]
                    }
                }
            )

        monkeypatch.setattr(se.requests, "get", fake_get)
        assert se.SecEdgarSource()._find_cik("Nestle SA") is None


class TestExhibit21ProseAndHeadings:
    """Two leftovers found re-running JPMorgan Chase on 18 Sep 2026."""

    def test_rejects_exhibit_heading_with_non_breaking_space(self):
        """JPMorgan Chase writes its heading as "Exhibit\\xa021"."""
        from domain_mapper.sources.sec_edgar import _is_entity_name

        assert _is_entity_name("Exhibit\xa021") is False
        assert _is_entity_name("Exhibit 21") is False
        assert _is_entity_name("Exhibit 21.1") is False

    def test_rejects_explanatory_prose(self):
        from domain_mapper.sources.sec_edgar import _is_entity_name

        prose = (
            "Also included in the list are certain subsidiaries that have been "
            "designated as material legal entities for resolution planning purposes "
            "under the Dodd-Frank Act that did not meet the definition of a "
            "significant subsidiary under SEC rules."
        )
        assert _is_entity_name(prose) is False

    def test_keeps_long_but_plausible_entity_names(self):
        from domain_mapper.sources.sec_edgar import _is_entity_name

        for name in (
            "JPMorgan Asset Management Holdings (Luxembourg) S.à r.l.",
            "JPMorgan Chase Bank, National Association",
            "J.P. Morgan Services India Private Limited",
            "JPMorgan Securities Japan Co., Ltd.",
        ):
            assert _is_entity_name(name) is True, name


# --- Exhibit 21 jurisdiction pairing --------------------------------------
#
# The flat-text pass ran first and the table pass was gated behind
# "if not subsidiaries", so the table pass never ran. The text pass "succeeded"
# on every filing, just without jurisdictions, because a rendered table puts each
# cell on its own line and the splitter only pairs cells within one line.
# JPMorgan Chase filed 18 entities with jurisdictions and all 18 arrived empty.

from bs4 import BeautifulSoup  # noqa: E402

JPM_TABLE = """
<html><body><table>
  <tr><td></td><td></td><td></td><td></td><td></td><td></td></tr>
  <tr><td>December&#160;31, 2025Name</td><td>Organized UnderThe Laws Of</td></tr>
  <tr><td>JPMorgan Chase Bank, National Association</td><td>United States</td></tr>
  <tr><td></td><td></td></tr>
  <tr><td>JPMorgan Securities Japan Co., Ltd.</td><td>Japan</td></tr>
  <tr><td>J.P. Morgan Securities plc</td><td>United Kingdom</td></tr>
  <tr><td>J.P. Morgan SE</td><td>Germany</td></tr>
</table></body></html>
"""


class TestExhibit21TableParse:
    def test_table_parse_keeps_jurisdictions(self):
        from domain_mapper.sources.sec_edgar import _parse_exhibit_tables

        subs = _parse_exhibit_tables(BeautifulSoup(JPM_TABLE, "html.parser"))
        got = {s.name: s.jurisdiction for s in subs}

        assert got["JPMorgan Securities Japan Co., Ltd."] == "Japan"
        assert got["J.P. Morgan Securities plc"] == "United Kingdom"
        assert got["J.P. Morgan SE"] == "Germany"
        assert all(s.jurisdiction for s in subs), "every row must carry its jurisdiction"

    def test_table_parse_drops_header_and_spacer_rows(self):
        from domain_mapper.sources.sec_edgar import _parse_exhibit_tables

        subs = _parse_exhibit_tables(BeautifulSoup(JPM_TABLE, "html.parser"))
        names = [s.name for s in subs]

        assert len(subs) == 4
        assert not any("Organized Under" in n for n in names)
        assert not any("2025" in n for n in names)

    def test_boeing_style_header_is_dropped(self):
        """Boeing's header is a bare "Name" / "Place of Incorporation" pair."""
        from domain_mapper.sources.sec_edgar import _parse_exhibit_tables

        html = """<table>
          <tr><td>Name</td><td>Place of Incorporation</td></tr>
          <tr><td>Astro Limited</td><td>Bermuda</td></tr>
          <tr><td>Aviall, Inc.</td><td>Delaware</td></tr>
        </table>"""
        subs = _parse_exhibit_tables(BeautifulSoup(html, "html.parser"))

        assert [(s.name, s.jurisdiction) for s in subs] == [
            ("Astro Limited", "Bermuda"),
            ("Aviall, Inc.", "Delaware"),
        ]


class TestExhibit21LineParse:
    """The fallback, for filings that are not tables."""

    def test_pairs_a_name_with_the_jurisdiction_on_the_next_line(self):
        from domain_mapper.sources.sec_edgar import _parse_exhibit_lines

        html = "<p>J.P. Morgan SE<br/>Germany<br/>Paymentech, LLC<br/>United States</p>"
        subs = _parse_exhibit_lines(BeautifulSoup(html, "html.parser"))

        assert [(s.name, s.jurisdiction) for s in subs] == [
            ("J.P. Morgan SE", "Germany"),
            ("Paymentech, LLC", "United States"),
        ]

    def test_does_not_swallow_the_next_entity_as_a_jurisdiction(self):
        """The dangerous case: consuming a following line that is another company."""
        from domain_mapper.sources.sec_edgar import _parse_exhibit_lines

        html = "<p>Paymentech, LLC<br/>J.P. Morgan Securities LLC<br/>Astro Limited</p>"
        subs = _parse_exhibit_lines(BeautifulSoup(html, "html.parser"))

        assert len(subs) == 3, "no entity may be eaten as another's jurisdiction"
        assert all(s.jurisdiction == "" for s in subs)

    def test_same_line_pairing_still_works(self):
        from domain_mapper.sources.sec_edgar import _parse_exhibit_lines

        html = "<pre>Astro Limited     Bermuda\nAviall, Inc.      Delaware</pre>"
        subs = _parse_exhibit_lines(BeautifulSoup(html, "html.parser"))

        assert [(s.name, s.jurisdiction) for s in subs] == [
            ("Astro Limited", "Bermuda"),
            ("Aviall, Inc.", "Delaware"),
        ]


class TestJurisdictionRecogniser:
    def test_recognises_places(self):
        from domain_mapper.sources.sec_edgar import _is_jurisdiction

        for place in ("Germany", "United Kingdom", "Japan", "Luxembourg", "Delaware", "Bermuda"):
            assert _is_jurisdiction(place) is True, place

    def test_rejects_companies_and_blanks(self):
        from domain_mapper.sources.sec_edgar import _is_jurisdiction

        for value in ("J.P. Morgan SE", "Paymentech, LLC", "", "   ", "Organized Under"):
            assert _is_jurisdiction(value) is False, value
