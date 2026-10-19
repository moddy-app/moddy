"""DNS-over-HTTPS (JSON flavour) answers: parsing and email-authentication hints.

Cloudflare and Google return nearly the same JSON; the differences handled here
are how TXT strings are quoted and that Cloudflare may hand CAA records back in
the RFC 3597 ``\\# <len> <hex>`` form.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

PRIMARY_URL = "https://cloudflare-dns.com/dns-query"
FALLBACK_URL = "https://dns.google/resolve"
ACCEPT = "application/dns-json"

#: Types offered by /dns, in display order.
RECORD_TYPES: Tuple[str, ...] = ("A", "AAAA", "CNAME", "MX", "NS", "TXT", "CAA", "SOA")

TYPE_CODES: Dict[int, str] = {1: "A", 2: "NS", 5: "CNAME", 6: "SOA", 15: "MX",
                              16: "TXT", 28: "AAAA", 257: "CAA"}

# DNS response codes
RCODE_OK = 0
RCODE_SERVFAIL = 2
RCODE_NXDOMAIN = 3

#: DKIM selectors worth probing: the defaults of the big senders.
DKIM_SELECTORS: Tuple[str, ...] = ("default", "google", "selector1", "selector2", "k1")


@dataclass
class DnsRecord:
    type: str
    name: str
    ttl: int
    value: str                      # display text, already normalised
    priority: Optional[int] = None  # MX only

    @property
    def sort_key(self):
        return (self.priority if self.priority is not None else 0, self.value)


@dataclass
class DnsAnswer:
    rcode: int = RCODE_OK
    records: List[DnsRecord] = field(default_factory=list)
    authenticated: bool = False  # DNSSEC "AD" bit

    @property
    def nxdomain(self) -> bool:
        return self.rcode == RCODE_NXDOMAIN


# --------------------------------------------------------------------------- #
# Value normalisation
# --------------------------------------------------------------------------- #

_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')


def parse_txt(data: str) -> str:
    """``"v=spf1 " "include:x"`` -> ``v=spf1 include:x`` (strings are concatenated)."""
    parts = _QUOTED.findall(data or "")
    if not parts:
        return (data or "").strip()
    return "".join(p.replace('\\"', '"').replace("\\\\", "\\") for p in parts)


def parse_caa(data: str) -> str:
    """``0 issue "letsencrypt.org"`` or the ``\\# 22 00 05 ...`` wire form -> ``0 issue letsencrypt.org``."""
    text = (data or "").strip()
    if text.startswith("\\#"):
        try:
            _, length, *hex_parts = text.split()
            raw = bytes.fromhex("".join(hex_parts))[: int(length)]
            flags, tag_len = raw[0], raw[1]
            tag = raw[2:2 + tag_len].decode("ascii", "replace")
            value = raw[2 + tag_len:].decode("utf-8", "replace")
            return f"{flags} {tag} {value}"
        except (ValueError, IndexError):
            return text
    flags, _, rest = text.partition(" ")
    tag, _, value = rest.partition(" ")
    return f"{flags} {tag} {value.strip().strip(chr(34))}".strip()


def parse_mx(data: str) -> Tuple[Optional[int], str]:
    prio, _, host = (data or "").strip().partition(" ")
    if not prio.isdigit():
        return None, (data or "").strip().rstrip(".")
    return int(prio), (host.strip().rstrip(".").lower() or ".")


def soa_fields(data: str) -> Dict[str, str]:
    """``{primary, admin, serial, refresh, retry, expire, minimum}`` (empty when malformed)."""
    parts = (data or "").split()
    if len(parts) < 7:
        return {}
    keys = ("primary", "admin", "serial", "refresh", "retry", "expire", "minimum")
    out = dict(zip(keys, parts[:7]))
    out["primary"] = out["primary"].rstrip(".")
    out["admin"] = out["admin"].rstrip(".")
    return out


def _normalise(rtype: str, data: str) -> Tuple[str, Optional[int]]:
    if rtype == "TXT":
        return parse_txt(data), None
    if rtype == "CAA":
        return parse_caa(data), None
    if rtype == "MX":
        prio, host = parse_mx(data)
        return host, prio
    if rtype in ("NS", "CNAME"):
        return (data or "").strip().rstrip(".").lower(), None
    if rtype == "SOA":
        fields = soa_fields(data)
        if fields:
            return f"{fields['primary']} {fields['admin']} {fields['serial']}", None
        return (data or "").strip(), None
    return (data or "").strip(), None


def parse_response(payload: dict, qtype: str) -> DnsAnswer:
    """Records of type ``qtype`` from a DoH JSON answer (CNAME hops are dropped)."""
    answer = DnsAnswer(
        rcode=int(payload.get("Status", RCODE_OK) or 0),
        authenticated=bool(payload.get("AD")),
    )
    for item in payload.get("Answer") or []:
        if not isinstance(item, dict):
            continue
        rtype = TYPE_CODES.get(item.get("type"))
        if rtype != qtype:
            continue
        value, prio = _normalise(rtype, str(item.get("data", "")))
        if not value:
            continue
        answer.records.append(DnsRecord(
            type=rtype, name=str(item.get("name", "")).rstrip(".").lower(),
            ttl=int(item.get("TTL", 0) or 0), value=value, priority=prio))
    if qtype == "MX":
        answer.records.sort(key=lambda r: r.sort_key)
    elif qtype in ("NS", "A", "AAAA", "CAA"):
        answer.records.sort(key=lambda r: r.value)
    return answer


def has_answer(payload: dict) -> bool:
    """True if the DoH answer carries any record at all (used for DKIM probing)."""
    return bool(payload.get("Answer")) and int(payload.get("Status", 0) or 0) == RCODE_OK


def ttl_parts(ttl: int) -> Tuple[int, str]:
    """``300`` -> ``(5, "min")``: the unit key is ``s`` / ``min`` / ``h`` / ``d``."""
    ttl = max(0, int(ttl))
    if ttl < 120:
        return ttl, "s"
    if ttl < 7200:
        return round(ttl / 60), "min"
    if ttl < 172800:
        return round(ttl / 3600), "h"
    return round(ttl / 86400), "d"


def ttl_summary(records: Iterable[DnsRecord]) -> Optional[Tuple[int, int]]:
    """``(min_ttl, max_ttl)`` of a record set, or ``None`` when empty."""
    values = [r.ttl for r in records]
    return (min(values), max(values)) if values else None


# --------------------------------------------------------------------------- #
# Email authentication hints
# --------------------------------------------------------------------------- #


@dataclass
class EmailHints:
    spf: Optional[str] = None            # the SPF record, if any
    spf_all: Optional[str] = None        # "-all" | "~all" | "?all" | "+all" | None
    dmarc: Optional[str] = None          # the DMARC record, if any
    dmarc_policy: Optional[str] = None   # none | quarantine | reject
    dkim: List[str] = field(default_factory=list)  # selectors found

    @property
    def any(self) -> bool:
        return bool(self.spf or self.dmarc or self.dkim)


def spf_record(txt_values: Iterable[str]) -> Optional[str]:
    for value in txt_values:
        if value.lower().lstrip().startswith("v=spf1"):
            return value.strip()
    return None


def spf_all(record: Optional[str]) -> Optional[str]:
    if not record:
        return None
    match = re.search(r"(?:^|\s)([+\-~?]?)all(?:\s|$)", record.lower())
    if not match:
        return None
    return (match.group(1) or "+") + "all"


def dmarc_record(txt_values: Iterable[str]) -> Optional[str]:
    for value in txt_values:
        if value.lower().lstrip().startswith("v=dmarc1"):
            return value.strip()
    return None


def dmarc_policy(record: Optional[str]) -> Optional[str]:
    if not record:
        return None
    match = re.search(r"(?:^|;)\s*p\s*=\s*(none|quarantine|reject)\b", record.lower())
    return match.group(1) if match else None


def build_hints(domain_txt: Iterable[str], dmarc_txt: Iterable[str],
                dkim_found: Iterable[str]) -> EmailHints:
    spf = spf_record(domain_txt)
    dmarc = dmarc_record(dmarc_txt)
    return EmailHints(spf=spf, spf_all=spf_all(spf), dmarc=dmarc,
                      dmarc_policy=dmarc_policy(dmarc), dkim=list(dkim_found))
