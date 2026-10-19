"""RDAP (the JSON successor of WHOIS): bootstrap lookup and record parsing.

The IANA bootstrap file maps each TLD to the RDAP server of its registry;
``bootstrap_map`` flattens it to ``{tld: base_url}`` (compact enough to cache in
Redis for a day). A record is parsed defensively — registries differ a lot in
what they publish — into a small :class:`DomainRecord`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"
FALLBACK_URL = "https://rdap.org/domain/{name}"

#: A domain younger than this many days is worth a warning (phishing is mostly
#: served from fresh registrations).
RECENT_DAYS = 30
VERY_RECENT_DAYS = 7


@dataclass
class DomainRecord:
    name: str = ""
    registrar: Optional[str] = None
    registrar_id: Optional[str] = None
    created: Optional[datetime] = None
    expires: Optional[datetime] = None
    updated: Optional[datetime] = None
    statuses: List[str] = field(default_factory=list)
    nameservers: List[str] = field(default_factory=list)
    dnssec: Optional[bool] = None
    related_url: Optional[str] = None   # the registrar's own RDAP record, when linked
    server: Optional[str] = None        # host of the RDAP server that answered

    def to_json(self) -> Dict[str, Any]:
        out = {k: getattr(self, k) for k in (
            "name", "registrar", "registrar_id", "statuses", "nameservers", "dnssec",
            "related_url", "server")}
        for key in ("created", "expires", "updated"):
            value = getattr(self, key)
            out[key] = value.timestamp() if value else None
        return out

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "DomainRecord":
        rec = cls()
        for key in ("name", "registrar", "registrar_id", "statuses", "nameservers", "dnssec",
                    "related_url", "server"):
            if key in data:
                setattr(rec, key, data[key])
        for key in ("created", "expires", "updated"):
            if data.get(key) is not None:
                setattr(rec, key, datetime.fromtimestamp(data[key], tz=timezone.utc))
        return rec


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #


def bootstrap_map(data: Dict[str, Any]) -> Dict[str, str]:
    """``{tld: rdap base url}`` from the IANA ``dns.json`` (https preferred)."""
    mapping: Dict[str, str] = {}
    for entry in (data or {}).get("services") or []:
        try:
            tlds, urls = entry[0], entry[1]
        except (IndexError, TypeError):
            continue
        https = [u for u in urls if isinstance(u, str) and u.startswith("https://")]
        chosen = (https or [u for u in urls if isinstance(u, str)] or [None])[0]
        if not chosen:
            continue
        for tld in tlds:
            mapping[str(tld).lower()] = chosen
    return mapping


def endpoint(base_url: str, name: str) -> str:
    """The domain-lookup URL of a registry RDAP server."""
    return f"{base_url.rstrip('/')}/domain/{name}"


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def parse_date(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        try:
            parsed = datetime.strptime(value.strip()[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _vcard_value(entity: Dict[str, Any], *fields: str) -> Optional[str]:
    card = entity.get("vcardArray")
    if not (isinstance(card, list) and len(card) > 1 and isinstance(card[1], list)):
        return None
    for wanted in fields:
        for item in card[1]:
            if isinstance(item, list) and len(item) >= 4 and item[0] == wanted:
                value = item[3]
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


def _registrar(entities: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    for entity in entities or []:
        if "registrar" in [str(r).lower() for r in entity.get("roles") or []]:
            name = _vcard_value(entity, "fn", "org")
            reg_id = None
            for public_id in entity.get("publicIds") or []:
                if isinstance(public_id, dict) and public_id.get("identifier"):
                    reg_id = str(public_id["identifier"])
                    break
            return name or entity.get("handle"), reg_id
    return None, None


def parse_domain(data: Dict[str, Any], *, server: Optional[str] = None) -> DomainRecord:
    """Normalise an RDAP domain object. Never raises on missing fields."""
    rec = DomainRecord(name=str(data.get("ldhName") or "").lower(), server=server)
    rec.registrar, rec.registrar_id = _registrar(data.get("entities") or [])

    for event in data.get("events") or []:
        if not isinstance(event, dict):
            continue
        action = str(event.get("eventAction") or "").lower()
        when = parse_date(event.get("eventDate"))
        if when is None:
            continue
        if action in ("registration", "reregistration") and rec.created is None:
            rec.created = when
        elif action == "expiration":
            rec.expires = when
        elif action in ("last changed", "last update"):
            rec.updated = when

    seen = set()
    for status in data.get("status") or []:
        text = str(status).strip()
        if text and text.lower() not in seen:
            seen.add(text.lower())
            rec.statuses.append(text)

    names = []
    for ns in data.get("nameservers") or []:
        if isinstance(ns, dict) and ns.get("ldhName"):
            names.append(str(ns["ldhName"]).lower().rstrip("."))
    rec.nameservers = sorted(set(names))

    secure = data.get("secureDNS")
    if isinstance(secure, dict):
        if "delegationSigned" in secure:
            rec.dnssec = bool(secure["delegationSigned"])
        elif "dsData" in secure or "keyData" in secure:
            rec.dnssec = bool(secure.get("dsData") or secure.get("keyData"))

    for link in data.get("links") or []:
        if (isinstance(link, dict) and str(link.get("rel")).lower() == "related"
                and "rdap" in str(link.get("type") or "") and link.get("href")):
            href = str(link["href"])
            if href.startswith("https://") and urlsplit(href).hostname:
                rec.related_url = href
                break
    return rec


def merge(base: DomainRecord, extra: DomainRecord) -> DomainRecord:
    """Fill the gaps of a registry record with the registrar's own record."""
    merged = replace(base)
    for key in ("registrar", "registrar_id", "created", "expires", "updated", "dnssec"):
        if getattr(merged, key) in (None, "") and getattr(extra, key) not in (None, ""):
            setattr(merged, key, getattr(extra, key))
    if not merged.nameservers:
        merged.nameservers = list(extra.nameservers)
    if extra.statuses:
        merged.statuses = sorted(set(merged.statuses) | set(extra.statuses),
                                 key=lambda s: (s.lower(), s))
    return merged


# --------------------------------------------------------------------------- #
# Derived values
# --------------------------------------------------------------------------- #


def days_between(start: datetime, end: datetime) -> int:
    """Whole days from ``start`` to ``end`` (negative when ``end`` is earlier)."""
    return int((end - start).total_seconds() // 86400)


def age_days(created: Optional[datetime], now: Optional[datetime] = None) -> Optional[int]:
    if created is None:
        return None
    return max(0, days_between(created, now or datetime.now(timezone.utc)))


def days_left(expires: Optional[datetime], now: Optional[datetime] = None) -> Optional[int]:
    if expires is None:
        return None
    return days_between(now or datetime.now(timezone.utc), expires)


def age_level(days: Optional[int]) -> Optional[str]:
    """``"very_recent"`` (<= 7 d), ``"recent"`` (<= 30 d) or ``None``."""
    if days is None:
        return None
    if days <= VERY_RECENT_DAYS:
        return "very_recent"
    if days <= RECENT_DAYS:
        return "recent"
    return None


_STATUS_CODES = {
    "clienttransferprohibited": "transfer_locked",
    "servertransferprohibited": "transfer_locked",
    "clientdeleteprohibited": "delete_locked",
    "serverdeleteprohibited": "delete_locked",
    "clientupdateprohibited": "update_locked",
    "serverupdateprohibited": "update_locked",
    "clientrenewprohibited": "renew_locked",
    "serverrenewprohibited": "renew_locked",
    "ok": "active",
    "active": "active",
    "clienthold": "on_hold",
    "serverhold": "on_hold",
    "pendingdelete": "pending_delete",
    "redemptionperiod": "redemption",
    "pendingtransfer": "pending_transfer",
    "pendingcreate": "pending",
    "pendingrenew": "pending",
    "pendingupdate": "pending",
    "pendingrestore": "pending",
    "inactive": "inactive",
    "addperiod": "grace_period",
    "autorenewperiod": "grace_period",
    "renewperiod": "grace_period",
    "transferperiod": "grace_period",
}

#: Status codes that mean "something is wrong with this domain".
WARNING_STATUSES = frozenset({"on_hold", "pending_delete", "redemption", "inactive"})


def humanize_status(raw: str) -> Optional[str]:
    """``"client transfer prohibited"`` / ``clientTransferProhibited`` -> ``"transfer_locked"``."""
    key = re.sub(r"[^a-z]", "", str(raw).lower())
    return _STATUS_CODES.get(key)


def summarize_statuses(raw_statuses: List[str]) -> Tuple[List[str], List[str]]:
    """``(known codes in a stable order, unknown raw values)`` — deduplicated."""
    order = ["active", "transfer_locked", "delete_locked", "update_locked", "renew_locked",
             "grace_period", "pending", "pending_transfer", "inactive", "on_hold",
             "redemption", "pending_delete"]
    codes, unknown = set(), []
    for raw in raw_statuses:
        code = humanize_status(raw)
        if code:
            codes.add(code)
        elif raw not in unknown:
            unknown.append(raw)
    # "active" is noise next to real flags — "ok" only means "no other status".
    if len(codes) > 1:
        codes.discard("active")
    return [c for c in order if c in codes], unknown
