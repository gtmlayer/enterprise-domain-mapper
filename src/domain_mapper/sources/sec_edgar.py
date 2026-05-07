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


def _rate_limit():
    """Enforce SEC EDGAR rate limits."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < 0.15:
        time.sleep(0.15 - elapsed)
    _last_request_time = time.time()


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
        """Find the CIK number for a company."""
        _rate_limit()
        url = "https://efts.sec.gov/LATEST/search-index"
        params = {"q": f'"{company_name}"', "forms": "10-K", "dateRange": "custom", "startdt": "2020-01-01"}
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])
            if hits:
                cik = hits[0].get("_source", {}).get("entity_id", "")
                if cik:
                    return str(cik).zfill(10)
        except Exception:
            pass

        # Fallback: try the company tickers JSON
        _rate_limit()
        try:
            resp = requests.get(
                "https://www.sec.gov/files/company_tickers.json",
                headers=HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            tickers = resp.json()
            name_lower = company_name.lower()
            for entry in tickers.values():
                if name_lower in entry.get("title", "").lower():
                    return str(entry["cik_str"]).zfill(10)
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
        """Parse an Exhibit 21 document to extract subsidiaries."""
        _rate_limit()
        subsidiaries = []
        try:
            resp = requests.get(exhibit_url, headers=HEADERS, timeout=15)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")
            text = soup.get_text("\n", strip=True)

            # Exhibit 21 typically lists subsidiaries as:
            # "Subsidiary Name    State/Country of Incorporation"
            # or in table rows
            lines = text.split("\n")

            for line in lines:
                line = line.strip()
                if not line or len(line) < 5:
                    continue

                # Skip headers and boilerplate
                if any(skip in line.lower() for skip in [
                    "exhibit", "subsidiaries", "registrant",
                    "name of subsidiary", "jurisdiction", "state or",
                    "incorporated", "page", "form 10-k",
                ]):
                    continue

                # Try to split into name and jurisdiction
                # Common patterns: tabs, multiple spaces, or specific delimiters
                parts = re.split(r"\t+|\s{3,}", line, maxsplit=1)
                if len(parts) >= 2:
                    name = parts[0].strip().strip("*").strip()
                    jurisdiction = parts[1].strip().strip("*").strip()
                    if len(name) > 2 and len(jurisdiction) > 1:
                        subsidiaries.append(
                            Subsidiary(
                                name=name,
                                jurisdiction=jurisdiction,
                                subsidiary_type=SubsidiaryType.SUBSIDIARY,
                                source=DomainSource.SEC_EDGAR,
                            )
                        )
                elif len(line) > 5 and not line.startswith("("):
                    # Single column - just the name
                    subsidiaries.append(
                        Subsidiary(
                            name=line.strip("*").strip(),
                            jurisdiction="",
                            subsidiary_type=SubsidiaryType.SUBSIDIARY,
                            source=DomainSource.SEC_EDGAR,
                        )
                    )

            # Also try parsing HTML tables
            if not subsidiaries:
                for table in soup.find_all("table"):
                    for row in table.find_all("tr"):
                        cells = row.find_all(["td", "th"])
                        if len(cells) >= 2:
                            name = cells[0].get_text(strip=True).strip("*").strip()
                            jurisdiction = cells[1].get_text(strip=True).strip("*").strip()
                            if (
                                len(name) > 2
                                and len(jurisdiction) > 1
                                and "name" not in name.lower()
                                and "subsidiary" not in name.lower()
                            ):
                                subsidiaries.append(
                                    Subsidiary(
                                        name=name,
                                        jurisdiction=jurisdiction,
                                        subsidiary_type=SubsidiaryType.SUBSIDIARY,
                                        source=DomainSource.SEC_EDGAR,
                                    )
                                )

        except Exception as e:
            logger.warning(f"Failed to parse Exhibit 21 at {exhibit_url}: {e}")

        logger.info(f"SEC EDGAR: found {len(subsidiaries)} subsidiaries")
        return subsidiaries
