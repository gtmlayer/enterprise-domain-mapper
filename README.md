# Enterprise Domain Mapper

[![CI](https://github.com/gtmlayer/enterprise-domain-mapper/actions/workflows/ci.yml/badge.svg)](https://github.com/gtmlayer/enterprise-domain-mapper/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**Map enterprise corporate structures to enrichable domains.** Feed it company names, get back subsidiaries, acquisitions, and regional domains that your enrichment tools are missing.

Every sales team doing enterprise ABM hits the same wall: large companies have dozens of subsidiaries, acquisitions, and regional entities, each with their own email domain. Without a complete domain map, tools like Clay and Apollo only find contacts at the parent domain. Entire business units get missed.

This tool fixes that.

```
$ domain-mapper "HSBC" --domain hsbc.com --verify-dns

HSBC
├── · Hang Seng Bank                 hangsengbank.com         [Name guess] resolves (no MX)
├── ✓ HSBC Bank Middle East          hsbcbankmiddleeast.com   [Name guess] ✓ MX verified
├── ✓ HSBC Bank Australia            hsbc.com.au              [Certificate log] ✓ MX verified
├── ✓ HSBC Bank Hong Kong            hsbc.com.hk              [Certificate log] ✓ MX verified
├── ✓ HSBC UK                        hsbc.co.uk               [Certificate log] ✓ MX verified
├── ✓ (certificate log, 327 certs)   hsbcnet.com              [Certificate log] ✓ MX verified
├── ✓ (certificate log, 208 certs)   hsbc.com.mx              [Certificate log] ✓ MX verified
└── ✓ (certificate log, 122 certs)   hsbc.fr                  [Certificate log] ✓ MX verified

Found 14 subsidiaries, 107 domains (47 guessed, 33 MX verified)
```

The tree above is abridged: the run generates 107 candidate domains, 33 of them have
live mail, and 23 are rated `High` because their mail runs on HSBC's own tenant. Pass
`--output results.csv` to get all of them with their sources and confidence.

Rows labelled `(certificate log, N certs)` are domains found in the logs that no
subsidiary list mentions, so there is no entity name to attach. The certificate count is
the ranking signal: a domain on 327 certificates is core infrastructure.

## The problem

Enterprise accounts don't operate under a single domain. A company like HSBC runs distinctly branded subsidiaries such as First Direct alongside regional banks in dozens of countries, each with its own domain. Deloitte has member firms. Nestlé sits over hundreds of consumer brands that use completely different domains.

If you're running enrichment against just `hsbc.com`, you're finding maybe 30% of the contacts you could be reaching. The rest are hiding behind `firstdirect.com`, `hsbc.co.uk`, `hsbc.com.hk`, and domains you didn't know existed.

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

Pass the parent domain when you know it. Regional TLD guessing builds from that stem,
so without it you get subsidiary names and `.com` guesses but no `hsbc.co.uk`:

```bash
domain-mapper "HSBC" --domain hsbc.com --verify-dns
```

In batch mode the domain column supplies this per row, so `--domain` is not needed.

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

The tool combines four data sources and a verification layer to build comprehensive domain maps:

### 1. SEC EDGAR Exhibit 21 scraper

For US-listed companies, SEC filings include Exhibit 21: a legally required list of all subsidiaries. The tool looks up the company's CIK, finds the latest 10-K filing, and parses the subsidiary list with jurisdictions.

This is the highest-quality source - it's legally mandated disclosure, so it's comprehensive and current.

### 2. Wikipedia corporate structure parser

For non-US companies (or supplementary data), the tool searches Wikipedia for the company page and extracts subsidiary and acquisition data from infoboxes and structured sections.

Covers companies globally, though data depth varies by how well-maintained the Wikipedia page is.

### 3. TLD pattern generator

Once subsidiaries are identified with their jurisdictions, the tool generates likely domain patterns. A subsidiary in Italy with parent domain `hsbc.com` produces guesses like `hsbc.it`. Covers 70+ countries with their standard corporate TLD patterns (e.g. UK produces `co.uk` and `.uk`, Japan produces `co.jp` and `.jp`).

### 4. Certificate transparency logs

Every publicly trusted certificate is published to a public log. Searching those logs
surfaces domains a company has demonstrably operated, rather than domains guessed from
a name, and it reaches brands that are not legal entities and so appear in no filing.

This matters because the first two sources structurally cannot answer "what are all
their domains". Exhibit 21 lists only *significant subsidiaries*: JPMorgan Chase files
18, for a bank with hundreds of entities. Wikipedia lists whatever an editor typed into
an infobox. Neither is a brand register. The logs are the closest free thing to one.

For HSBC this is the difference between 6 confirmed mail domains and 33.

A certificate proves somebody operated a name, not that this company owns it, so these
arrive as candidates rather than facts. See the confidence rules below.

**Limitation worth knowing:** the search keys on the company's own name stem, so brands
whose domains do not carry the parent's name are still missed. HSBC's First Direct and
M&S Bank do not appear, because `firstdirect.com` contains no "hsbc".

### 5. DNS verification (optional)

MX record lookup with A record fallback to confirm guessed domains actually resolve. A records confirm the domain exists even without mail setup.

Two limits worth knowing, because both used to produce false confidence:

- **An MX record is not always mail.** Registrars and parking pages publish a null MX (RFC 7505 `.`, or `localhost`, or `0.0.0.0`). `luxembourg.com` and `unitedkingdom.com` both do. Those are treated as no mail at all.
- **Mail is not ownership.** A guessed domain with live mail proves somebody owns it, not that the company you asked about does. So a guess is capped at `Medium` however well it resolves.

## Output format

### Detailed output (default)

Ten columns, one row per subsidiary-domain pair:

| Column | Description |
|--------|-------------|
| `parent_company` | The company you looked up |
| `parent_domain` | Known parent domain |
| `subsidiary_name` | Name of the subsidiary or entity |
| `subsidiary_type` | Subsidiary, acquisition, division, etc. |
| `jurisdiction` | Country or region |
| `domain` | Known or guessed domain |
| `domain_source` | Where it came from (SEC EDGAR, Wikipedia, TLD guess, Name guess) |
| `dns_verified` | `True` only when the domain has MX (mail) records |
| `dns_status` | DNS result: `mx`, `a-only` (resolves but no mail), `unresolved`, or blank if not checked |
| `confidence` | `High` = observed in the certificate logs **and** its mail runs on the group's own tenant; `Medium` = observed in the logs, or a guess with live mail; `Low` = an unverified guess. A guessed domain never reaches `High` on DNS alone, because mail on `chaseuk.com` proves a stranger parked it in 2016, not that Chase owns it |

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
