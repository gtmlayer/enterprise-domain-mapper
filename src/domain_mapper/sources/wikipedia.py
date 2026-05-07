"""Wikipedia corporate structure parser for subsidiary discovery."""

import logging
import re

import requests
from bs4 import BeautifulSoup

from domain_mapper.models import DomainSource, Subsidiary, SubsidiaryType

logger = logging.getLogger(__name__)

WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "Enterprise Domain Mapper (hello@gtmlayer.com)"}


class WikipediaSource:
    """Extract subsidiary and acquisition data from Wikipedia."""

    def get_subsidiaries(self, company_name: str) -> list[Subsidiary]:
        """Search Wikipedia for a company and extract corporate structure data."""
        try:
            page_title = self._find_company_page(company_name)
            if not page_title:
                logger.info(f"No Wikipedia page found for '{company_name}'")
                return []

            html = self._get_page_html(page_title)
            if not html:
                return []

            subsidiaries = self._parse_corporate_structure(html)
            logger.info(f"Wikipedia: found {len(subsidiaries)} subsidiaries for '{company_name}'")
            return subsidiaries

        except Exception as e:
            logger.warning(f"Wikipedia lookup failed for '{company_name}': {e}")
            return []

    def _find_company_page(self, company_name: str) -> str | None:
        """Search Wikipedia for the company's page."""
        params = {
            "action": "query",
            "list": "search",
            "srsearch": f"{company_name} company",
            "srnamespace": "0",
            "srlimit": "5",
            "format": "json",
        }
        try:
            resp = requests.get(WIKIPEDIA_API_URL, params=params, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("query", {}).get("search", [])
            if results:
                return results[0]["title"]
        except Exception as e:
            logger.warning(f"Wikipedia search failed: {e}")

        return None

    def _get_page_html(self, title: str) -> str | None:
        """Fetch the parsed HTML of a Wikipedia page."""
        params = {
            "action": "parse",
            "page": title,
            "prop": "text",
            "format": "json",
        }
        try:
            resp = requests.get(WIKIPEDIA_API_URL, params=params, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            return data.get("parse", {}).get("text", {}).get("*", "")
        except Exception as e:
            logger.warning(f"Failed to fetch Wikipedia page '{title}': {e}")
            return None

    def _parse_corporate_structure(self, html: str) -> list[Subsidiary]:
        """Parse Wikipedia HTML to extract subsidiaries and acquisitions."""
        soup = BeautifulSoup(html, "html.parser")
        subsidiaries = []
        seen_names = set()

        # 1. Parse the infobox for subsidiaries/divisions
        infobox = soup.find("table", class_="infobox")
        if infobox:
            for row in infobox.find_all("tr"):
                header = row.find("th")
                data = row.find("td")
                if header and data:
                    header_text = header.get_text(strip=True).lower()
                    if any(k in header_text for k in ["subsidiar", "division", "brand"]):
                        for item in self._extract_list_items(data):
                            if item not in seen_names:
                                seen_names.add(item)
                                sub_type = SubsidiaryType.DIVISION if "division" in header_text else SubsidiaryType.SUBSIDIARY
                                subsidiaries.append(
                                    Subsidiary(
                                        name=item,
                                        subsidiary_type=sub_type,
                                        source=DomainSource.WIKIPEDIA,
                                    )
                                )

        # 2. Look for sections about subsidiaries/acquisitions
        for heading in soup.find_all(["h2", "h3", "h4"]):
            heading_text = heading.get_text(strip=True).lower()
            if any(k in heading_text for k in [
                "subsidiar", "acquisition", "merger", "division",
                "brand", "affiliate", "member firm",
            ]):
                # Collect content until the next heading
                content_elements = []
                sibling = heading.find_next_sibling()
                while sibling and sibling.name not in ["h2", "h3", "h4"]:
                    content_elements.append(sibling)
                    sibling = sibling.find_next_sibling()

                for element in content_elements:
                    # Extract from lists
                    if element.name in ["ul", "ol"]:
                        for li in element.find_all("li", recursive=False):
                            name = self._clean_entity_name(li)
                            if name and name not in seen_names and len(name) > 2:
                                seen_names.add(name)
                                sub_type = (
                                    SubsidiaryType.ACQUISITION
                                    if "acqui" in heading_text or "merger" in heading_text
                                    else SubsidiaryType.SUBSIDIARY
                                )
                                subsidiaries.append(
                                    Subsidiary(
                                        name=name,
                                        subsidiary_type=sub_type,
                                        source=DomainSource.WIKIPEDIA,
                                    )
                                )

                    # Extract from tables
                    if element.name == "table":
                        for row in element.find_all("tr")[1:]:  # Skip header
                            cells = row.find_all(["td", "th"])
                            if cells:
                                name = self._clean_entity_name(cells[0])
                                if name and name not in seen_names and len(name) > 2:
                                    seen_names.add(name)
                                    subsidiaries.append(
                                        Subsidiary(
                                            name=name,
                                            subsidiary_type=SubsidiaryType.ACQUISITION,
                                            source=DomainSource.WIKIPEDIA,
                                        )
                                    )

        return subsidiaries

    def _extract_list_items(self, element) -> list[str]:
        """Extract individual items from a td cell that may contain a list or plain text."""
        items = []

        # Check for an actual list
        ul = element.find("ul")
        if ul:
            for li in ul.find_all("li", recursive=False):
                name = self._clean_entity_name(li)
                if name and len(name) > 2:
                    items.append(name)
            return items

        # Check for <br>-separated items
        text = element.decode_contents()
        if "<br" in text:
            parts = re.split(r"<br\s*/?>", text)
            for part in parts:
                clean = BeautifulSoup(part, "html.parser").get_text(strip=True)
                clean = re.sub(r"\[.*?\]", "", clean).strip()
                if clean and len(clean) > 2:
                    items.append(clean)
            return items

        # Single item
        name = self._clean_entity_name(element)
        if name and len(name) > 2:
            items.append(name)

        return items

    def _clean_entity_name(self, element) -> str:
        """Extract a clean entity name from an HTML element."""
        # Prefer the first link text if available
        link = element.find("a")
        if link:
            name = link.get_text(strip=True)
        else:
            name = element.get_text(strip=True)

        # Remove citation markers and parenthetical dates
        name = re.sub(r"\[.*?\]", "", name)
        name = re.sub(r"\(.*?\)", "", name)
        name = name.strip().strip(",").strip()

        # Truncate at common separators that indicate descriptions
        for sep in [" - ", " -- ", ": ", " | "]:
            if sep in name:
                name = name.split(sep)[0].strip()

        return name
