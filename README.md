# Enterprise Domain Mapper

[![CI](https://github.com/gtmlayer/enterprise-domain-mapper/actions/workflows/ci.yml/badge.svg)](https://github.com/gtmlayer/enterprise-domain-mapper/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**Map enterprise corporate structures to enrichable domains.** Feed it company names, get back subsidiaries, acquisitions, and regional domains that your enrichment tools are missing.

Every sales team doing enterprise ABM hits the same wall: large companies have dozens of subsidiaries, acquisitions, and regional entities, each with their own email domain. Without a complete domain map, tools like Clay and Apollo only find contacts at the parent domain. Entire business units get missed.

This tool fixes that.

```
$ domain-mapper "NTT Data"

NTT Data
├── NTT DATA Services        nttdataservices.com  [SEC EDGAR]
├── NTT DATA Business Solutions  nttdata-solutions.com  [Wikipedia]
├── Dimension Data            dimensiondata.com    [Wikipedia]
├── NTT DATA Italia           nttdata.it           [TLD guess ✓ DNS verified]
├── NTT DATA UK               nttdata.co.uk        [TLD guess ✓ DNS verified]
└── NTT DATA Japan            nttdata.co.jp        [TLD guess ✓ DNS verified]

Found 12 subsidiaries, 18 domains (6 confirmed, 12 guessed, 9 DNS verified)
```

## The problem

Enterprise accounts don't operate under a single domain. A company like NTT Data has subsidiaries in 50+ countries, each with localised domains. Deloitte has member firms. Boeing has defence subsidiaries that use completely different brands.

If you're running enrichment against just `nttdata.com`, you're finding maybe 30% of the contacts you could be reaching. The rest are hiding behind `dimensiondata.com`, `nttdata.co.uk`, `nttdata.it`, and domains you didn't know existed.

Building these domain maps manually takes hours per account. We built this tool because we got tired of doing it by hand.

## Quick start

### Installation

```bash
pip install enterprise-domain-mapper
```

Or clone and install locally:

```bash
git clone https://github.com/gtmlayer/enterprise-domain-mapper.git
cd enterprise-domain-mapper
pip install -e .
```

### Single company lookup

```bash
domain-mapper "Boeing"
```

### Batch mode (CSV input)

```bash
domain-mapper accounts.csv --output results.csv
```

Your input CSV just needs a column with company names. The tool auto-detects columns named `company_name`, `company`, `name`, or `account`. If you have a domain column (`domain`, `website`, `url`), it'll use that as the parent domain for TLD guessing.

### With DNS verification

```bash
domain-mapper accounts.csv --output results.csv --verify-dns
```

This checks whether guessed domains actually have mail infrastructure (MX records) or at minimum resolve (A records). Adds a few seconds per company but filters out the noise.

## What it does

The tool combines three data sources and a verification layer to build comprehensive domain maps:

### 1. SEC EDGAR Exhibit 21 scraper

For US-listed companies, SEC filings include Exhibit 21: a legally required list of all subsidiaries. The tool looks up the company's CIK, finds the latest 10-K filing, and parses the subsidiary list with jurisdictions.

This is the highest-quality source - it's legally mandated disclosure, so it's comprehensive and current.

### 2. Wikipedia corporate structure parser

For non-US companies (or supplementary data), the tool searches Wikipedia for the company page and extracts subsidiary and acquisition data from infoboxes and structured sections.

Covers companies globally, though data depth varies by how well-maintained the Wikipedia page is.

### 3. TLD pattern generator

Once subsidiaries are identified with their jurisdictions, the tool generates likely domain patterns. A subsidiary in Italy with parent domain `nttdata.com` produces guesses like `nttdata.it`. Covers 70+ countries with their standard corporate TLD patterns (e.g. UK produces `co.uk` and `.uk`, Japan produces `co.jp` and `.jp`).

### 4. DNS verification (optional)

MX record lookup with A record fallback to confirm guessed domains actually resolve. MX records are the strongest signal - if a domain has mail infrastructure, it's real. A records confirm the domain exists even without mail setup.

## Output format

### Detailed output (default)

Nine columns, one row per subsidiary-domain pair:

| Column | Description |
|--------|-------------|
| `parent_company` | The company you looked up |
| `parent_domain` | Known parent domain |
| `subsidiary_name` | Name of the subsidiary or entity |
| `subsidiary_type` | Subsidiary, acquisition, division, etc. |
| `jurisdiction` | Country or region |
| `domain` | Confirmed or guessed domain |
| `domain_source` | Where it came from (SEC EDGAR, Wikipedia, TLD guess) |
| `dns_verified` | Whether DNS verification passed |
| `confidence` | High (confirmed), Medium (guessed + verified), Low (guessed only) |

### Clay import format

One row per company with domains consolidated into a single field, ready for direct import into Clay as a data source.

```bash
domain-mapper accounts.csv --output results.csv --format clay
```

## Importing into Clay

1. Run the tool with `--format clay` to get the Clay-optimised output
2. In Clay, create a new table or add to an existing one
3. Import the CSV - the columns map directly to Clay's expected format
4. Use the domain list column with Clay's enrichment tools to find contacts across all mapped domains

This is the workflow that sparked the whole tool. We were manually building domain maps for a client's enterprise accounts and realised the process was repeatable enough to automate.

## Example

The `examples/` directory contains `input_sample.csv` with five test companies to get you started:

```bash
domain-mapper examples/input_sample.csv --output examples/results.csv --verify-dns
```

## Contributing

Want to add a new data source? The architecture makes it straightforward:

1. Create a new module in `src/domain_mapper/sources/`
2. Implement a class with a `get_subsidiaries(company_name)` method that returns a list of subsidiary objects
3. Add it to the orchestrator in `mapper.py`
4. Write tests in `tests/`

Some data sources we'd love to see contributed:

- Companies House (UK company registry)
- OpenCorporates API
- Crunchbase (acquisitions data)
- D&B corporate hierarchies

Pull requests welcome. Run `ruff check` and `black` before submitting, and make sure `pytest` passes.

## Tech stack

- Python 3.10+
- `requests` and `beautifulsoup4` for web scraping
- `click` for the CLI
- `rich` for terminal output
- No paid APIs, no API keys required

## Built by GTM Layer

[GTM Layer](https://gtmlayer.com) builds revenue systems for B2B sales teams. We work with companies on CRM architecture, enrichment pipelines, signal-driven outbound, and everything in between.

This tool came out of real client work - we built it to solve a problem we kept hitting on enterprise ABM engagements. If you're running into similar challenges, [get in touch](https://gtmlayer.com).

## Licence

MIT - use it however you want.
