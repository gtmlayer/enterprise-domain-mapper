"""Tests for the Wikipedia parser (regression coverage for GTM-667/668)."""

from domain_mapper.models import SubsidiaryType
from domain_mapper.sources.wikipedia import WikipediaSource

# A subsidiaries section (table with a country column) plus an acquisitions list.
HTML = """
<div>
  <h2>Subsidiaries</h2>
  <table>
    <tr><th>Name</th><th>Country</th></tr>
    <tr><td>Acme Europe</td><td>Italy</td></tr>
    <tr><td>Acme Holdings</td><td>United Kingdom</td></tr>
  </table>
  <h2>Acquisitions</h2>
  <ul>
    <li>Bought Co (2020)</li>
  </ul>
</div>
"""


def _parse():
    return {s.name: s for s in WikipediaSource()._parse_corporate_structure(HTML)}


def test_subsidiary_table_rows_are_not_labelled_acquisition():
    # GTM-667: rows under a "Subsidiaries" heading must be SUBSIDIARY, not ACQUISITION.
    subs = _parse()
    assert subs["Acme Europe"].subsidiary_type == SubsidiaryType.SUBSIDIARY
    assert subs["Acme Holdings"].subsidiary_type == SubsidiaryType.SUBSIDIARY


def test_acquisition_section_is_labelled_acquisition():
    subs = _parse()
    assert subs["Bought Co"].subsidiary_type == SubsidiaryType.ACQUISITION


def test_jurisdiction_inferred_from_table(monkeypatch):
    # GTM-668: jurisdiction is inferred so TLD guessing can fire for Wikipedia subs.
    subs = _parse()
    assert subs["Acme Europe"].jurisdiction == "italy"
    assert subs["Acme Holdings"].jurisdiction == "united kingdom"
