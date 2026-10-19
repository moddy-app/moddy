"""crt.sh (Certificate Transparency logs) -> a clean list of subdomains.

crt.sh returns one row per certificate with the names it covers in
``name_value`` (newline separated). Many certificates share the same names, wildcards
(``*.example.com``) are not subdomains, and rows can carry e-mail addresses or
names of other domains: :func:`extract` turns that into a sorted, deduplicated
list of hostnames strictly below the queried domain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, List

SEARCH_URL = "https://crt.sh/"
#: Fallback when crt.sh is overloaded (it often is): same Certificate
#: Transparency data, keyless, but paginated and rate limited.
CERTSPOTTER_URL = "https://api.certspotter.com/v1/issuances"
CERTSPOTTER_MAX_PAGES = 3
PAGE_SIZE = 40
#: A domain with more names than this keeps only the first ones (sorted); the
#: full count is still reported.
MAX_NAMES = 5000

_HOST_RE = re.compile(r"^[a-z0-9_](?:[a-z0-9_.-]*[a-z0-9_])?$")


@dataclass
class SubdomainResult:
    domain: str
    names: List[str] = field(default_factory=list)  # sorted, no wildcards, apex excluded
    wildcards: int = 0                              # distinct wildcard names seen
    total: int = 0                                  # distinct names before the cap
    certificates: int = 0                           # rows crt.sh returned
    source: str = "crt.sh"                          # which provider answered

    @property
    def truncated(self) -> bool:
        return self.total > len(self.names)


def query_params(domain: str) -> dict:
    """``%.example.com`` is crt.sh's "any subdomain" pattern (URL-encoded by aiohttp)."""
    return {"q": f"%.{domain}", "output": "json"}


def certspotter_params(domain: str, after: Any = None) -> dict:
    params = {"domain": domain, "include_subdomains": "true", "expand": "dns_names"}
    if after is not None:
        params["after"] = str(after)
    return params


def certspotter_rows(entries: Any) -> List[dict]:
    """Cert Spotter issuances -> the row shape :func:`extract` reads."""
    rows = []
    for entry in entries if isinstance(entries, list) else []:
        names = entry.get("dns_names") if isinstance(entry, dict) else None
        if isinstance(names, list):
            rows.append({"name_value": "\n".join(str(n) for n in names)})
    return rows


def extract(entries: Iterable[Any], domain: str, *, cap: int = MAX_NAMES) -> SubdomainResult:
    domain = domain.lower().strip(".")
    suffix = "." + domain
    names, wildcards = set(), set()
    count = 0
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        count += 1
        raw = f"{entry.get('name_value', '')}\n{entry.get('common_name', '')}"
        for line in raw.split("\n"):
            name = line.strip().lower().rstrip(".")
            if not name or "@" in name or " " in name:
                continue
            if name.startswith("*."):
                base = name[2:]
                if base == domain or base.endswith(suffix):
                    wildcards.add(name)
                    if base != domain:
                        names.add(base)
                continue
            if name != domain and name.endswith(suffix) and _HOST_RE.match(name):
                names.add(name)
    ordered = sorted(names)
    return SubdomainResult(
        domain=domain, names=ordered[:cap], wildcards=len(wildcards),
        total=len(ordered), certificates=count)


def page_count(total: int, size: int = PAGE_SIZE) -> int:
    return max(1, -(-total // size))


def page(names: List[str], index: int, size: int = PAGE_SIZE) -> List[str]:
    index = max(0, min(index, page_count(len(names), size) - 1))
    return names[index * size:(index + 1) * size]
