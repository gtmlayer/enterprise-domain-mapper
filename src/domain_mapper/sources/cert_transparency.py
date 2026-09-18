"""Certificate transparency source: domains a company has actually been issued certs for.

The other two sources cannot answer "what are all their brands and domains", and no
amount of fixing them will. SEC Exhibit 21 lists only *significant subsidiaries*, the
legal entities a registrant consolidates: JPMorgan Chase files 18, for a bank with
hundreds. Brands are not legal entities, so Chase, Nutmeg and InstaMed can never appear
there. Wikipedia gives whatever an editor typed into an infobox, which for JPMorgan is
four entries. Neither is a brand register.

Certificate transparency logs are. Every publicly trusted certificate is logged, so a
wildcard search over the logs surfaces domains the company has demonstrably operated,
rather than domains we guessed from a name. A single `%jpmorgan%` query returns about
90 registrable domains including jpmorganassetmanagement in seven countries, which no
other source here reaches.

What a cert does NOT prove is ownership: anyone can obtain a certificate for a name they
control, including a squatter. So these arrive as candidates, and only a domain whose
mail runs on the same tenant as the parent's is promoted further.
"""

import json
import logging
import os
import re
import tempfile
import time

import requests

from domain_mapper.models import Confidence, DomainResult, DomainSource
from domain_mapper.sources.tld_generator import JURISDICTION_TLD_MAP

logger = logging.getLogger(__name__)

CRT_SH_URL = "https://crt.sh/"
REQUEST_TIMEOUT = 45
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60

# Multi-part public suffixes, taken from the TLD generator's own country map so the two
# stay in step. Without these, "hsbc.co.uk" reduces to "co.uk" and every UK domain in
# the logs collapses into one meaningless row.
_MULTI_PART_SUFFIXES = frozenset(
    suffix.lstrip(".")
    for suffixes in JURISDICTION_TLD_MAP.values()
    for suffix in suffixes
    if suffix.lstrip(".").count(".") >= 1
)

# crt.sh returns a lot of noise for a short stem. A name must be at least this long
# before it is worth querying, or "chase" drags in every purchase-shaped domain.
_MIN_STEM = 4

# Cap the returned candidates. The ranking is by how many certificates a domain appears
# on, so the tail is single-cert one-offs rather than infrastructure.
_MAX_CANDIDATES = 60


def registrable_domain(hostname: str) -> str:
    """Reduce a hostname to its registrable domain, honouring multi-part suffixes.

    "www.hsbc.co.uk" -> "hsbc.co.uk", not "co.uk".
    """
    host = (hostname or "").strip().lower().strip(".")
    host = host.lstrip("*.")
    if not host or " " in host:
        return ""
    parts = host.split(".")
    if len(parts) < 2:
        return ""
    if len(parts) >= 3 and ".".join(parts[-2:]) in _MULTI_PART_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def derive_stem(company_name: str, parent_domain: str) -> str:
    """Work out what to search the logs for.

    Defaults to the parent domain's own label. Where a token of the company name is a
    proper prefix of that label, the shorter token is preferred, because it is what the
    group's other domains are actually built from: "jpmorganchase" would miss
    jpmorganam.fr and jpmorganassetmanagement.de, while "jpmorgan" catches both.
    """
    label = ""
    if parent_domain:
        registrable = registrable_domain(parent_domain)
        label = registrable.split(".")[0] if registrable else ""

    tokens = [t for t in re.sub(r"[^a-z0-9]+", " ", (company_name or "").lower()).split() if t]

    if label:
        for token in tokens:
            if len(token) >= _MIN_STEM and len(token) < len(label) and label.startswith(token):
                return token
        return label

    return max(tokens, key=len) if tokens else ""


def _cache_path(stem: str) -> str:
    safe = re.sub(r"[^a-z0-9_-]+", "_", stem.lower())
    return os.path.join(tempfile.gettempdir(), f"domain-mapper-crtsh-{safe}.json")


def _read_cache(stem: str):
    path = _cache_path(stem)
    try:
        if os.path.exists(path) and (time.time() - os.path.getmtime(path)) < CACHE_TTL_SECONDS:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
    except Exception:
        pass
    return None


def _write_cache(stem: str, payload) -> None:
    try:
        with open(_cache_path(stem), "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    except Exception:
        pass


class CertTransparencySource:
    """Find domains a company has been issued certificates for."""

    def get_domains(self, company_name: str, parent_domain: str = "") -> list[DomainResult]:
        """Return ranked candidate domains from the certificate logs.

        Returns an empty list on any failure rather than a partial one. crt.sh is free
        and frequently slow, and a half-answer that looks whole is worse than nothing:
        a caller cannot tell a company with few domains from a query that timed out.
        """
        stem = derive_stem(company_name, parent_domain)
        if len(stem) < _MIN_STEM:
            logger.info(f"Certificate logs: no usable search stem for '{company_name}'")
            return []

        rows = _read_cache(stem)
        if rows is None:
            try:
                resp = requests.get(
                    CRT_SH_URL,
                    params={"q": f"%{stem}%", "output": "json"},
                    timeout=REQUEST_TIMEOUT,
                    headers={"User-Agent": "enterprise-domain-mapper"},
                )
                resp.raise_for_status()
                rows = resp.json()
                _write_cache(stem, rows)
            except Exception as e:
                logger.warning(
                    f"Certificate logs unreachable for '{stem}' ({e}). "
                    "Returning no candidates rather than a partial list."
                )
                return []

        counts = self._count_by_domain(rows, stem)
        parent_registrable = registrable_domain(parent_domain)
        counts.pop(parent_registrable, None)

        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:_MAX_CANDIDATES]
        logger.info(
            f"Certificate logs: {len(counts)} distinct domains for '{stem}', "
            f"returning top {len(ranked)}"
        )

        return [
            DomainResult(
                parent_company=company_name,
                parent_domain=parent_domain,
                subsidiary_name=f"(certificate log, {count} cert{'s' if count != 1 else ''})",
                subsidiary_type="Observed domain",
                jurisdiction="",
                domain=domain,
                domain_source=DomainSource.CERT_TRANSPARENCY.value,
                # Observed, not guessed, so it starts above a name guess. It stays here
                # unless its mail runs on the same tenant as the parent's, because a
                # certificate proves someone operated the name, not that this company did.
                confidence=Confidence.MEDIUM.value,
            )
            for domain, count in ranked
        ]

    @staticmethod
    def _count_by_domain(rows, stem: str) -> dict[str, int]:
        """Count how many log entries each registrable domain appears on.

        The stem must appear in the registrable domain's own label, not merely somewhere
        in the hostname. Without that check a certificate for `jpmorgan.example-phish.com`
        would register `example-phish.com` as one of the company's domains.
        """
        counts: dict[str, int] = {}
        for row in rows or []:
            names = (row.get("name_value") or "").split("\n")
            names.append(row.get("common_name") or "")
            for name in {n.strip() for n in names if n and n.strip()}:
                domain = registrable_domain(name)
                if not domain:
                    continue
                if stem not in domain.split(".")[0]:
                    continue
                counts[domain] = counts.get(domain, 0) + 1
        return counts
