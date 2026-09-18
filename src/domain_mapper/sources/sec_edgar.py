"""SEC EDGAR Exhibit 21 scraper for US-listed company subsidiaries."""

import logging
import re
import time

import requests
from bs4 import BeautifulSoup

from domain_mapper.models import DomainSource, Subsidiary, SubsidiaryType

logger = logging.getLogger(__name__)

EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index?q={query}&dateRange=custom&startdt=2020-01-01&forms=10-K"
EDGAR_COMPANY_URL = "https://www.sec.gov/cgi-bin/browse-edgar?company={query}&CIK=&type=10-K&dateb=&owner=include&count=10&search_text=&action=getcompany"
EDGAR_FULL_TEXT_URL = "https://efts.sec.gov/LATEST/search-index?q=%22{query}%22&forms=10-K&dateRange=custom&startdt=2020-01-01"
EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
EDGAR_FILING_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/"

HEADERS = {
    "User-Agent": "Enterprise Domain Mapper hello@gtmlayer.com",
    "Accept": "application/json",
}

# Rate limit: SEC EDGAR asks for max 10 requests/second
_last_request_time = 0.0

# Corporate suffixes stripped before matching a company name to an EDGAR title.
_CORP_SUFFIXES = {
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "co",
    "company",
    "ltd",
    "limited",
    "plc",
    "llc",
    "lp",
    "llp",
    "holdings",
    "holding",
    "group",
    "the",
    "sa",
    "ag",
    "nv",
    "se",
}


def _rate_limit():
    """Enforce SEC EDGAR rate limits."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < 0.15:
        time.sleep(0.15 - elapsed)
    _last_request_time = time.time()


def _normalise_company(name: str) -> str:
    """Lowercase, strip punctuation and common corporate suffixes for matching."""
    cleaned = re.sub(r"[^a-z0-9\s]", " ", name.lower())
    words = [w for w in cleaned.split() if w and w not in _CORP_SUFFIXES]
    return " ".join(words)


def _leading_runs(tokens: list[str]) -> set[str]:
    """Every despaced leading run of whole tokens: [a, b, c] -> {a, ab, abc}."""
    runs: set[str] = set()
    acc = ""
    for token in tokens:
        acc += token
        runs.add(acc)
    return runs


def _is_token_prefix(short: list[str], long: list[str]) -> bool:
    """True when `short` is a leading run of whole tokens in `long`."""
    return bool(short) and len(short) < len(long) and long[: len(short)] == short


def _match_score(query: str, title: str) -> int:
    """Score how well `title` matches `query`. 0 means no match.

    Three rungs, all anchored on whole tokens:

      3 - exact normalised match
      2 - one side, despaced, equals a leading run of whole tokens in the other
          ("JP Morgan" -> "jpmorgan" meets the "JPMORGAN" token of JPMORGAN CHASE)
      1 - one side is a leading run of whole tokens in the other

    Bare substring containment is deliberately absent. It is what mapped
    "JP Morgan" to MORGAN GROUP HOLDING CO: stripping corporate suffixes from that
    title leaves the single token "morgan", which is contained in "jp morgan", and
    the shortest-title tie-break then preferred it over the real filer. A title
    that collapses to one generic token must not be able to swallow a longer query.
    """
    norm_query = _normalise_company(query)
    norm_title = _normalise_company(title)
    if not norm_query or not norm_title:
        return 0

    if norm_query == norm_title:
        return 3

    query_tokens = norm_query.split()
    title_tokens = norm_title.split()

    query_despaced = norm_query.replace(" ", "")
    title_despaced = norm_title.replace(" ", "")
    if query_despaced in _leading_runs(title_tokens) or title_despaced in _leading_runs(
        query_tokens
    ):
        return 2

    if _is_token_prefix(query_tokens, title_tokens) or _is_token_prefix(title_tokens, query_tokens):
        return 1

    return 0


def _names_match(query: str, title: str) -> bool:
    """True when two company names match on any rung of `_match_score`."""
    return _match_score(query, title) > 0


def _strip_display_name(display_name: str) -> str:
    """Take the entity name out of an EDGAR display name.

    "JPMORGAN CHASE & CO  (JPM)  (CIK 0000019617)" -> "JPMORGAN CHASE & CO"
    """
    return display_name.split("(")[0].strip()


def _match_cik_from_tickers(tickers: dict, company_name: str) -> str | None:
    """Find the best-matching CIK for a company name in the company_tickers.json payload.

    Pure function (no network) so it can be unit-tested with a fixture. Ranks
    candidates by `_match_score`, breaking ties by the shortest title (closest
    match). Returns a zero-padded 10-digit CIK, or None where nothing matches.

    None is the right answer far more often than it looks. EDGAR only holds SEC
    registrants, so a non-US company has no CIK at all and any US filer returned
    for one is wrong by construction.
    """
    if not _normalise_company(company_name):
        return None

    best_cik: str | None = None
    best_score = 0
    best_len = 10**9

    for entry in tickers.values():
        title = entry.get("title", "")
        cik_str = entry.get("cik_str")
        if cik_str is None or not title:
            continue

        score = _match_score(company_name, title)
        if score == 0:
            continue

        norm_len = len(_normalise_company(title))
        if score > best_score or (score == best_score and norm_len < best_len):
            best_score = score
            best_len = norm_len
            best_cik = str(cik_str).zfill(10)

    return best_cik


# Exhibit 21 is a rendered HTML table, so a naive text pass picks up the table's own
# furniture alongside the subsidiaries: column headers, the filing's jurisdiction
# cells, the exhibit label and the HTML filename. Those then flow downstream as if
# they were companies, which is how `germany.com` and `thelawsof.com` ended up in a
# JPMorgan Chase domain map. Everything below is rejected by whole-string match, so a
# real entity such as "Delaware Trust Company" survives while a bare "Delaware" does not.

_US_STATES = (
    frozenset("""alabama alaska arizona arkansas california colorado connecticut delaware florida
    georgia hawaii idaho illinois indiana iowa kansas kentucky louisiana maine maryland
    massachusetts michigan minnesota mississippi missouri montana nebraska nevada ohio
    oklahoma oregon pennsylvania tennessee texas utah vermont virginia washington
    wisconsin wyoming""".split())
    | frozenset(
        {
            "new hampshire",
            "new jersey",
            "new mexico",
            "new york",
            "north carolina",
            "north dakota",
            "rhode island",
            "south carolina",
            "south dakota",
            "west virginia",
            "district of columbia",
            "puerto rico",
            "virgin islands",
            "guam",
            "american samoa",
            "northern mariana islands",
        }
    )
)

_COUNTRIES = frozenset(
    {
        "united states",
        "united states of america",
        "usa",
        "u s a",
        "us",
        "united kingdom",
        "uk",
        "england",
        "scotland",
        "wales",
        "northern ireland",
        "england and wales",
        "great britain",
        "ireland",
        "germany",
        "france",
        "luxembourg",
        "netherlands",
        "the netherlands",
        "switzerland",
        "spain",
        "italy",
        "belgium",
        "austria",
        "sweden",
        "norway",
        "denmark",
        "finland",
        "poland",
        "portugal",
        "greece",
        "iceland",
        "malta",
        "cyprus",
        "monaco",
        "liechtenstein",
        "czech republic",
        "hungary",
        "romania",
        "bulgaria",
        "croatia",
        "slovakia",
        "slovenia",
        "estonia",
        "latvia",
        "lithuania",
        "ukraine",
        "russia",
        "turkey",
        "israel",
        "canada",
        "mexico",
        "brazil",
        "argentina",
        "chile",
        "colombia",
        "peru",
        "uruguay",
        "venezuela",
        "ecuador",
        "costa rica",
        "panama",
        "dominican republic",
        "jamaica",
        "bahamas",
        "barbados",
        "bermuda",
        "cayman islands",
        "british virgin islands",
        "jersey",
        "guernsey",
        "isle of man",
        "gibraltar",
        "curacao",
        "mauritius",
        "trinidad and tobago",
        "china",
        "hong kong",
        "macau",
        "japan",
        "singapore",
        "india",
        "australia",
        "new zealand",
        "south korea",
        "korea",
        "taiwan",
        "malaysia",
        "indonesia",
        "thailand",
        "philippines",
        "vietnam",
        "south africa",
        "nigeria",
        "kenya",
        "egypt",
        "morocco",
        "uae",
        "united arab emirates",
        "saudi arabia",
        "qatar",
        "bahrain",
        "kuwait",
        "oman",
    }
)

_HEADER_PHRASES = frozenset(
    {
        "document",
        "documents",
        "name",
        "names",
        "entity",
        "entities",
        "legal name",
        "subsidiary",
        "subsidiaries",
        "subsidiaries of the registrant",
        "list of subsidiaries",
        "significant subsidiaries",
        "name of subsidiary",
        "name of entity",
        "jurisdiction",
        "jurisdiction of incorporation",
        "jurisdiction of organization",
        "jurisdiction of organisation",
        "state of incorporation",
        "state or country of incorporation",
        "state or other jurisdiction",
        "country of incorporation",
        "incorporation",
        "organized under",
        "organised under",
        "the laws of",
        "laws of",
        "organized under the laws of",
        "organised under the laws of",
        "percentage of",
        "percentage owned",
        "percent owned",
        "percentage of voting",
        "ownership",
        "owned",
        "voting",
        "voting securities",
        "voting power",
        "parent",
        "registrant",
        "exhibit",
        "page",
        "table of contents",
        "country",
        "state",
        "type",
        "business name",
        "dba",
        "as of",
    }
)

_MONTHS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)


def _is_jurisdiction(value: str) -> bool:
    """True when a cell is a recognised place of incorporation rather than an entity.

    Used to decide whether the line after an entity name is that entity's jurisdiction
    or the next entity. Strict on purpose: consuming the wrong line would silently
    delete a subsidiary, so an unrecognised place leaves the jurisdiction empty rather
    than eating the following row.
    """
    low = re.sub(r"\s+", " ", value or "").strip().lower().strip(" .,;:")
    return bool(low) and (low in _US_STATES or low in _COUNTRIES)


def _is_entity_name(name: str) -> bool:
    """True when a parsed Exhibit 21 cell looks like a company rather than table furniture."""
    cleaned = re.sub(r"\s+", " ", name or "").strip().strip("*").strip()
    if len(cleaned) < 3:
        return False

    low = cleaned.lower().strip(" .,;:")
    if not re.search(r"[a-z]", low):
        return False
    if low in _HEADER_PHRASES or low in _US_STATES or low in _COUNTRIES:
        return False

    # The exhibit's own heading. JPMorgan Chase writes it with a non-breaking space
    # ("Exhibit\xa021"), which survives a naive header comparison.
    if re.match(r"^exhibit\b", low):
        return False

    # Prose. Exhibit 21 often carries an explanatory sentence above the list, e.g.
    # JPMorgan Chase's Dodd-Frank resolution-planning note, which parses as one very
    # long "entity". No real company name runs past a dozen words.
    if len(cleaned.split()) > 12:
        return False

    # Filenames and exhibit labels: "ex211-q425.htm", "brhc10050563_ex21-1.htm", "EX-21.1"
    if re.search(r"\.(?:html?|pdf|txt|xml|xlsx?|jpe?g|png)$", low):
        return False
    if re.match(r"^ex[\s\-_.]?\d", low):
        return False
    if re.match(r"^[a-z]{0,6}\d{6,}", low):
        return False

    # Dates: "December 31, 2025", "as of December 28, 2012", "12/31/2025"
    if re.match(r"^as of\b", low):
        return False
    if any(month in low for month in _MONTHS) and re.search(r"\b(?:19|20)\d{2}\b", low):
        return False
    if re.match(r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$", low):
        return False

    # Pure numbers, percentages and punctuation
    if re.match(r"^[\d\s.,%()\-]+$", low):
        return False

    return True


class SecEdgarSource:
    """Fetch subsidiary data from SEC EDGAR Exhibit 21 filings."""

    def get_subsidiaries(self, company_name: str) -> list[Subsidiary]:
        """Look up a company on SEC EDGAR and extract Exhibit 21 subsidiaries."""
        try:
            cik = self._find_cik(company_name)
            if not cik:
                logger.info(f"No SEC EDGAR CIK found for '{company_name}'")
                return []

            filing_url = self._find_latest_10k(cik)
            if not filing_url:
                logger.info(f"No 10-K filing found for CIK {cik}")
                return []

            exhibit_url = self._find_exhibit_21(filing_url, cik)
            if not exhibit_url:
                logger.info(f"No Exhibit 21 found in 10-K for '{company_name}'")
                return []

            return self._parse_exhibit_21(exhibit_url)

        except Exception as e:
            logger.warning(f"SEC EDGAR lookup failed for '{company_name}': {e}")
            return []

    def _find_cik(self, company_name: str) -> str | None:
        """Find the CIK number for a company.

        Primary source is the official company_tickers.json registry (stable,
        documented), matched on whole tokens by `_match_score`.

        The EDGAR full-text search is a secondary fallback, and a dangerous one:
        it returns any filing that MENTIONS the phrase, so its top hit is often a
        different company that happens to name this one. Its hits are therefore
        gated on the same name match, and an unmatched hit yields None rather than
        a guess.
        """
        # Primary: company_tickers.json (the reliable registry)
        _rate_limit()
        try:
            resp = requests.get(
                "https://www.sec.gov/files/company_tickers.json",
                headers=HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            cik = _match_cik_from_tickers(resp.json(), company_name)
            if cik:
                return cik
        except Exception:
            pass

        # Fallback: EDGAR full-text search. The CIK lives in _source.ciks
        # (a list of zero-padded CIK strings), NOT in an "entity_id" field.
        _rate_limit()
        url = "https://efts.sec.gov/LATEST/search-index"
        params = {"q": f'"{company_name}"', "forms": "10-K"}
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            hits = resp.json().get("hits", {}).get("hits", [])
            for hit in hits:
                source = hit.get("_source", {})
                ciks = source.get("ciks") or []
                if not ciks:
                    continue
                names = source.get("display_names") or []
                if any(_names_match(company_name, _strip_display_name(n)) for n in names):
                    return str(ciks[0]).zfill(10)
            if hits:
                logger.info(
                    f"EDGAR full-text hits for '{company_name}' matched no entity name; "
                    "returning no CIK rather than the top hit"
                )
        except Exception:
            pass

        return None

    def _find_latest_10k(self, cik: str) -> str | None:
        """Find the most recent 10-K filing index URL."""
        _rate_limit()
        url = EDGAR_SUBMISSIONS_URL.format(cik=cik)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            recent = data.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            accessions = recent.get("accessionNumber", [])

            for i, form in enumerate(forms):
                if form == "10-K":
                    accession = accessions[i].replace("-", "")
                    return EDGAR_FILING_URL.format(cik=cik.lstrip("0"), accession=accession)

        except Exception as e:
            logger.warning(f"Failed to fetch submissions for CIK {cik}: {e}")

        return None

    def _find_exhibit_21(self, filing_index_url: str, cik: str) -> str | None:
        """Find the Exhibit 21 document URL within a 10-K filing."""
        _rate_limit()
        try:
            # Try the index page
            index_url = filing_index_url.rstrip("/") + "/index.json"
            resp = requests.get(index_url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            items = data.get("directory", {}).get("item", [])
            for item in items:
                name = item.get("name", "").lower()
                if "ex21" in name or "exhibit21" in name or "ex-21" in name:
                    return filing_index_url.rstrip("/") + "/" + item["name"]

            # Fallback: parse the HTML index
            _rate_limit()
            html_url = filing_index_url.rstrip("/")
            if not html_url.endswith(".htm"):
                # Try fetching the index page directly
                resp = requests.get(html_url, headers=HEADERS, timeout=15)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")
                for link in soup.find_all("a"):
                    href = link.get("href", "")
                    text = link.get_text("", strip=True).lower()
                    if "exhibit 21" in text or "ex21" in href.lower() or "ex-21" in href.lower():
                        if href.startswith("/"):
                            return f"https://www.sec.gov{href}"
                        return filing_index_url.rstrip("/") + "/" + href

        except Exception as e:
            logger.warning(f"Failed to find Exhibit 21: {e}")

        return None

    def _parse_exhibit_21(self, exhibit_url: str) -> list[Subsidiary]:
        """Parse an Exhibit 21 document to extract subsidiaries.

        The HTML table is tried first and the flat-text pass is the fallback. It used
        to be the other way round, gated behind `if not subsidiaries`, so the table
        branch effectively never ran: the text pass always "succeeded", just without
        jurisdictions. JPMorgan Chase's filing states United States, Japan, United
        Kingdom, Germany, India and Luxembourg, and every one of them was discarded,
        which left regional TLD guessing with no stem to build from.
        """
        _rate_limit()
        try:
            resp = requests.get(exhibit_url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            subsidiaries = _parse_exhibit_tables(soup)
            if not subsidiaries:
                subsidiaries = _parse_exhibit_lines(soup)

        except Exception as e:
            logger.warning(f"Failed to parse Exhibit 21 at {exhibit_url}: {e}")
            subsidiaries = []

        with_jurisdiction = sum(1 for s in subsidiaries if s.jurisdiction)
        logger.info(
            f"SEC EDGAR: found {len(subsidiaries)} subsidiaries "
            f"({with_jurisdiction} with a jurisdiction)"
        )
        return subsidiaries


def _parse_exhibit_tables(soup: BeautifulSoup) -> list[Subsidiary]:
    """Parse Exhibit 21 from its HTML table: column one the entity, column two the place.

    Filings pad their tables with empty spacer cells, so blanks are dropped before the
    columns are read. The header row is rejected by `_is_entity_name` on the name cell
    ("Name", "December 31, 2025Name"), with a light guard on the jurisdiction cell for
    the concatenated variants ("Organized UnderThe Laws Of").
    """
    subsidiaries: list[Subsidiary] = []
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
            cells = [c for c in cells if c]
            if len(cells) < 2:
                continue

            name = cells[0].strip("*").strip()
            jurisdiction = cells[1].strip("*").strip()
            if not _is_entity_name(name) or len(jurisdiction) <= 1:
                continue
            if re.search(r"laws of|incorporation|jurisdiction", jurisdiction, re.I):
                continue

            subsidiaries.append(
                Subsidiary(
                    name=name,
                    jurisdiction=jurisdiction,
                    subsidiary_type=SubsidiaryType.SUBSIDIARY,
                    source=DomainSource.SEC_EDGAR,
                )
            )
    return subsidiaries


def _parse_exhibit_lines(soup: BeautifulSoup) -> list[Subsidiary]:
    """Parse Exhibit 21 from flat text, for filings that are not tables.

    Two shapes are handled: name and place separated by tabs or runs of spaces on one
    line, and name on one line with its place on the next. The second only consumes the
    following line when that line is a recognised jurisdiction, so an unrecognised place
    costs an empty jurisdiction rather than swallowing the next subsidiary.
    """
    text = soup.get_text("\n", strip=True)
    lines = [line.strip() for line in text.split("\n")]
    lines = [line for line in lines if line]

    subsidiaries: list[Subsidiary] = []
    index = 0
    while index < len(lines):
        line = lines[index]

        parts = re.split(r"\t+|\s{3,}", line, maxsplit=1)
        if len(parts) >= 2:
            name = parts[0].strip().strip("*").strip()
            jurisdiction = parts[1].strip().strip("*").strip()
            if _is_entity_name(name) and len(jurisdiction) > 1:
                subsidiaries.append(
                    Subsidiary(
                        name=name,
                        jurisdiction=jurisdiction,
                        subsidiary_type=SubsidiaryType.SUBSIDIARY,
                        source=DomainSource.SEC_EDGAR,
                    )
                )
                index += 1
                continue

        if _is_entity_name(line) and not line.startswith("("):
            jurisdiction = ""
            if index + 1 < len(lines) and _is_jurisdiction(lines[index + 1]):
                jurisdiction = lines[index + 1].strip("*").strip()
                index += 1
            subsidiaries.append(
                Subsidiary(
                    name=line.strip("*").strip(),
                    jurisdiction=jurisdiction,
                    subsidiary_type=SubsidiaryType.SUBSIDIARY,
                    source=DomainSource.SEC_EDGAR,
                )
            )

        index += 1

    return subsidiaries
