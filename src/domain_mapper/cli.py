"""CLI interface for the Enterprise Domain Mapper."""

import csv
import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.tree import Tree

from domain_mapper.mapper import DomainMapper
from domain_mapper.models import CompanyResult
from domain_mapper.output import write_clay_csv, write_detailed_csv

console = Console()

# Column name detection for CSV inputs
COMPANY_COLUMNS = ["company_name", "company", "name", "account", "account_name", "organization"]
DOMAIN_COLUMNS = ["domain", "website", "url", "parent_domain", "company_domain"]


def _detect_columns(headers: list[str]) -> tuple[str | None, str | None]:
    """Auto-detect company name and domain columns from CSV headers."""
    headers_lower = [h.lower().strip() for h in headers]

    company_col = None
    for candidate in COMPANY_COLUMNS:
        if candidate in headers_lower:
            company_col = headers[headers_lower.index(candidate)]
            break

    domain_col = None
    for candidate in DOMAIN_COLUMNS:
        if candidate in headers_lower:
            domain_col = headers[headers_lower.index(candidate)]
            break

    return company_col, domain_col


def _print_tree(result: CompanyResult) -> None:
    """Print a rich tree representation of the mapping results."""
    tree = Tree(f"[bold]{result.company_name}[/bold]")

    for domain in result.domains:
        icon = "[green]✓[/green]" if domain.dns_verified else "[dim]·[/dim]"
        source_tag = f"[dim][{domain.domain_source}][/dim]"

        if domain.dns_verified:
            source_tag += " [green]✓ DNS verified[/green]"

        tree.add(
            f"{icon} [cyan]{domain.subsidiary_name:<30}[/cyan] "
            f"{domain.domain:<25} {source_tag}"
        )

    console.print(tree)

    # Summary line
    total = len(result.domains)
    confirmed = len(result.confirmed_domains)
    guessed = len(result.guessed_domains)
    verified = len(result.verified_domains)
    console.print(
        f"\n[dim]Found {len(result.subsidiaries)} subsidiaries, "
        f"{total} domains ({confirmed} confirmed, {guessed} guessed, "
        f"{verified} DNS verified)[/dim]"
    )


@click.command()
@click.argument("input", type=str)
@click.option("--output", "-o", type=click.Path(), help="Output CSV file path")
@click.option(
    "--format",
    "-f",
    "output_format",
    type=click.Choice(["detailed", "clay"]),
    default="detailed",
    help="Output format: detailed (one row per domain) or clay (one row per company)",
)
@click.option("--verify-dns", is_flag=True, help="Verify guessed domains via DNS lookups")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def main(input: str, output: str | None, output_format: str, verify_dns: bool, verbose: bool):
    """Map enterprise companies to their subsidiary domains.

    INPUT can be a company name (e.g. "Boeing") or a CSV file path.
    """
    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)

    mapper = DomainMapper(verify_dns=verify_dns)
    results: list[CompanyResult] = []

    input_path = Path(input)
    if input_path.exists() and input_path.suffix.lower() == ".csv":
        # Batch mode: CSV input
        results = _process_csv(mapper, input_path, verify_dns)
    else:
        # Single company mode
        console.print(f"\n[bold]Mapping domains for:[/bold] {input}\n")
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Searching SEC EDGAR, Wikipedia, generating TLDs...", total=None)
            result = mapper.map_company(input)
            progress.update(task, completed=True)

        results = [result]
        _print_tree(result)

    # Write output file
    if output:
        output_path = Path(output)
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            if output_format == "clay":
                write_clay_csv(results, f)
            else:
                write_detailed_csv(results, f)
        console.print(f"\n[green]✓[/green] Results written to {output_path}")
        total_domains = sum(len(r.domains) for r in results)
        console.print(f"[dim]  {len(results)} companies, {total_domains} domains[/dim]")


def _process_csv(mapper: DomainMapper, csv_path: Path, verify_dns: bool) -> list[CompanyResult]:
    """Process a CSV file of companies."""
    results = []

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            console.print("[red]Error: CSV file has no headers[/red]")
            sys.exit(1)

        company_col, domain_col = _detect_columns(list(reader.fieldnames))
        if not company_col:
            console.print(
                f"[red]Error: Could not detect company name column. "
                f"Expected one of: {', '.join(COMPANY_COLUMNS)}[/red]"
            )
            sys.exit(1)

        rows = list(reader)

    console.print(f"\n[bold]Processing {len(rows)} companies from {csv_path.name}[/bold]")
    if domain_col:
        console.print(f"[dim]Using '{company_col}' for names, '{domain_col}' for parent domains[/dim]")
    else:
        console.print(f"[dim]Using '{company_col}' for names (no domain column detected)[/dim]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        for i, row in enumerate(rows):
            company_name = row.get(company_col, "").strip()
            parent_domain = row.get(domain_col, "").strip() if domain_col else ""

            if not company_name:
                continue

            task = progress.add_task(
                f"[{i + 1}/{len(rows)}] {company_name}...", total=None
            )
            result = mapper.map_company(company_name, parent_domain)
            results.append(result)
            progress.update(task, completed=True, description=f"[{i + 1}/{len(rows)}] {company_name}: {len(result.domains)} domains")

    # Print summary
    total_subs = sum(len(r.subsidiaries) for r in results)
    total_domains = sum(len(r.domains) for r in results)
    total_verified = sum(len(r.verified_domains) for r in results)

    console.print(f"\n[bold]Summary[/bold]")
    console.print(f"  Companies processed: {len(results)}")
    console.print(f"  Total subsidiaries found: {total_subs}")
    console.print(f"  Total domains mapped: {total_domains}")
    if verify_dns:
        console.print(f"  DNS verified: {total_verified}")

    return results


if __name__ == "__main__":
    main()
