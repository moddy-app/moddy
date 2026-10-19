"""
``/status`` core — the services we follow and how an Atlassian Statuspage
``/api/v2/summary.json`` payload becomes a small, display-ready summary.

Everything here is pure except :func:`fetch_summary` / :func:`fetch_all`, which
take the ``bot.public_api`` client as an argument (so tests pass a fake).

Why Statuspage? It is the one status-page product shared by hundreds of
services, with a public, keyless, stable JSON endpoint. Services on other
products (Notion, Slack, Stripe...) are deliberately not listed: a service only
gets an entry once its endpoint has been checked.

Cache: one *compact* summary per service, 60 s, in process memory (the raw
payload of a large page such as Cloudflare is ~230 KB: it is parsed and
dropped, never stored).
"""

from __future__ import annotations

import asyncio
import html
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from utils import emojis as em

# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Service:
    key: str
    name: str
    base_url: str   # page URL, no trailing slash; the API lives under /api/v2/
    logo: str       # custom emoji token ("" until the artwork is uploaded)

    @property
    def summary_url(self) -> str:
        return f"{self.base_url}/api/v2/summary.json"


# Every base URL below answered with a Statuspage ``summary.json`` on 2026-10-19
# (checked from the build sandbox with curl). Zoom's historical host
# (status.zoom.us) 302-redirects to the one used here. The logo is read from
# utils/emojis.py at import time: an empty token simply drops the logo.
SERVICES: Tuple[Service, ...] = (
    Service("discord", "Discord", "https://www.discordstatus.com", em.DISCORD_LOGO),
    Service("github", "GitHub", "https://www.githubstatus.com", em.GITHUB),
    Service("cloudflare", "Cloudflare", "https://www.cloudflarestatus.com", em.CLOUDFLARE),
    Service("openai", "OpenAI", "https://status.openai.com", em.OPENAI),
    Service("claude", "Claude", "https://status.anthropic.com", em.ANTHROPIC),
    Service("reddit", "Reddit", "https://www.redditstatus.com", em.REDDIT),
    Service("twitch", "Twitch", "https://status.twitch.com", em.TWITCH),
    Service("dropbox", "Dropbox", "https://status.dropbox.com", em.DROPBOX),
    Service("atlassian", "Atlassian", "https://status.atlassian.com", em.ATLASSIAN),
    Service("vercel", "Vercel", "https://www.vercel-status.com", em.VERCEL),
    Service("netlify", "Netlify", "https://www.netlifystatus.com", em.NETLIFY),
    Service("zoom", "Zoom", "https://www.zoomstatus.com", em.ZOOM),
    Service("epicgames", "Epic Games", "https://status.epicgames.com", em.EPIC_GAMES),
    Service("npm", "npm", "https://status.npmjs.org", em.NPM),
    Service("digitalocean", "DigitalOcean", "https://status.digitalocean.com", em.DIGITALOCEAN),
    Service("figma", "Figma", "https://status.figma.com", em.FIGMA),
)

BY_KEY: Dict[str, Service] = {s.key: s for s in SERVICES}


def get_service(key: str) -> Optional[Service]:
    return BY_KEY.get((key or "").strip().lower())


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def find_services(query: str, limit: int = 25) -> List[Service]:
    """Services matching ``query`` (contains, accent/punctuation-insensitive).

    Prefix matches come first; an empty query lists everything in registry order.
    """
    needle = _norm(query)
    if not needle:
        return list(SERVICES)[:limit]
    starts: List[Service] = []
    contains: List[Service] = []
    for service in SERVICES:
        haystacks = (_norm(service.key), _norm(service.name))
        if any(h.startswith(needle) for h in haystacks):
            starts.append(service)
        elif any(needle in h for h in haystacks):
            contains.append(service)
    return (starts + contains)[:limit]


def resolve_service(value: str) -> Optional[Service]:
    """An autocomplete value (the key) or free text typed by the user."""
    service = get_service(value)
    if service is not None:
        return service
    matches = find_services(value, limit=2)
    exact = [s for s in matches if _norm(s.name) == _norm(value)]
    if exact:
        return exact[0]
    return matches[0] if matches and _norm(value) else None


# --------------------------------------------------------------------------- #
# Indicators and component statuses
# --------------------------------------------------------------------------- #

IND_NONE = "none"
IND_MINOR = "minor"
IND_MAJOR = "major"
IND_CRITICAL = "critical"
IND_MAINTENANCE = "maintenance"
IND_UNKNOWN = "unknown"

#: Order of the overview: problems first, then maintenance, then fine, then
#: services we could not reach.
INDICATOR_ORDER = {IND_CRITICAL: 0, IND_MAJOR: 1, IND_MINOR: 2, IND_MAINTENANCE: 3,
                   IND_NONE: 4, IND_UNKNOWN: 5}
PROBLEM_INDICATORS = frozenset({IND_MINOR, IND_MAJOR, IND_CRITICAL})

COMP_OPERATIONAL = "operational"
COMP_DEGRADED = "degraded_performance"
COMP_PARTIAL = "partial_outage"
COMP_MAJOR = "major_outage"
COMP_MAINTENANCE = "under_maintenance"
#: Worst first.
COMPONENT_SEVERITY = {COMP_MAJOR: 4, COMP_PARTIAL: 3, COMP_DEGRADED: 2, COMP_MAINTENANCE: 1,
                      COMP_OPERATIONAL: 0}


def normalize_indicator(value: Optional[str]) -> str:
    value = (value or "").strip().lower()
    return value if value in INDICATOR_ORDER and value != IND_UNKNOWN else IND_UNKNOWN


def indicator_dot(indicator: str) -> str:
    """Status dot icon of a page indicator (may be "" until artwork exists)."""
    return {
        IND_NONE: em.STATUS_OK,
        IND_MINOR: em.STATUS_DEGRADED,
        IND_MAJOR: em.STATUS_DOWN,
        IND_CRITICAL: em.STATUS_DOWN,
        IND_MAINTENANCE: em.STATUS_MAINTENANCE,
    }.get(indicator, em.GREY_STATUS)


def component_dot(status: str) -> str:
    return {
        COMP_OPERATIONAL: em.STATUS_OK,
        COMP_DEGRADED: em.STATUS_DEGRADED,
        COMP_PARTIAL: em.STATUS_DEGRADED,
        COMP_MAJOR: em.STATUS_DOWN,
        COMP_MAINTENANCE: em.STATUS_MAINTENANCE,
    }.get(status, em.GREY_STATUS)


def impact_dot(impact: str) -> str:
    """Dot of an incident impact (none / minor / major / critical)."""
    return {
        "minor": em.STATUS_DEGRADED,
        "major": em.STATUS_DOWN,
        "critical": em.STATUS_DOWN,
        "maintenance": em.STATUS_MAINTENANCE,
    }.get(impact, em.STATUS_DEGRADED)


# --------------------------------------------------------------------------- #
# Parsed summary
# --------------------------------------------------------------------------- #


@dataclass
class ComponentIssue:
    name: str
    group: Optional[str]
    status: str


@dataclass
class IncidentInfo:
    name: str
    impact: str          # none / minor / major / critical
    status: str          # investigating / identified / monitoring ...
    started_at: Optional[float]
    update: str          # latest update body, plain text
    url: str


@dataclass
class MaintenanceInfo:
    name: str
    status: str          # scheduled / in_progress / verifying
    start: Optional[float]
    end: Optional[float]
    update: str
    url: str


@dataclass
class ServiceSummary:
    key: str
    indicator: str = IND_UNKNOWN
    description: str = ""
    page_url: str = ""
    page_updated_at: Optional[float] = None
    fetched_at: float = 0.0
    issues: List[ComponentIssue] = field(default_factory=list)
    incidents: List[IncidentInfo] = field(default_factory=list)
    maintenances: List[MaintenanceInfo] = field(default_factory=list)
    components_total: int = 0
    error: Optional[str] = None     # PublicAPIError code when unreachable

    @property
    def reachable(self) -> bool:
        return self.error is None

    @property
    def has_problem(self) -> bool:
        return self.indicator in PROBLEM_INDICATORS


_TAG_BREAK = re.compile(r"<\s*(?:br\s*/?|/p|/li|/div)\s*>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def clean_text(raw: Optional[str]) -> str:
    """Plain text of a Statuspage update body (HTML fragments, entities, spacing)."""
    text = _TAG_BREAK.sub("\n", raw or "")
    text = _TAG.sub("", text)
    text = html.unescape(text)
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{2,}", "\n", "\n".join(line for line in lines if line)).strip()


def parse_time(value) -> Optional[float]:
    """ISO-8601 (``Z`` or an offset) -> epoch seconds, ``None`` when unusable."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _latest_update(raw: dict) -> str:
    updates = [u for u in (raw.get("incident_updates") or []) if isinstance(u, dict)]
    if not updates:
        return ""
    newest = max(updates, key=lambda u: parse_time(u.get("display_at") or u.get("created_at")) or 0)
    return clean_text(newest.get("body"))


def _link(raw: dict, fallback: str) -> str:
    link = raw.get("shortlink")
    if isinstance(link, str) and link.startswith("http"):
        return link
    ident = raw.get("id")
    return f"{fallback}/incidents/{ident}" if ident else fallback


def parse_summary(key: str, payload: dict, *, now: Optional[float] = None) -> ServiceSummary:
    """Statuspage ``summary.json`` -> :class:`ServiceSummary` (never raises on shape)."""
    now = time.time() if now is None else now
    page = payload.get("page") if isinstance(payload.get("page"), dict) else {}
    status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
    page_url = str(page.get("url") or "").rstrip("/")

    components = [c for c in (payload.get("components") or []) if isinstance(c, dict)]
    groups = {c.get("id"): str(c.get("name") or "") for c in components if c.get("group")}
    issues: List[ComponentIssue] = []
    leaves = 0
    for comp in components:
        if comp.get("group"):
            continue   # a group header only mirrors the status of its children
        leaves += 1
        comp_status = str(comp.get("status") or COMP_OPERATIONAL)
        if comp_status == COMP_OPERATIONAL:
            continue
        issues.append(ComponentIssue(
            name=str(comp.get("name") or "?"),
            group=groups.get(comp.get("group_id")) or None,
            status=comp_status))

    incidents: List[IncidentInfo] = []
    for raw in payload.get("incidents") or []:
        if not isinstance(raw, dict) or raw.get("status") in ("resolved", "postmortem", "completed"):
            continue
        incidents.append(IncidentInfo(
            name=str(raw.get("name") or "?"),
            impact=str(raw.get("impact") or "minor"),
            status=str(raw.get("status") or "investigating"),
            started_at=parse_time(raw.get("started_at") or raw.get("created_at")),
            update=_latest_update(raw),
            url=_link(raw, page_url)))
    incidents.sort(key=lambda i: (-impact_rank(i.impact), -(i.started_at or 0)))

    maintenances: List[MaintenanceInfo] = []
    for raw in payload.get("scheduled_maintenances") or []:
        if not isinstance(raw, dict) or raw.get("status") in ("completed", "resolved"):
            continue
        maintenances.append(MaintenanceInfo(
            name=str(raw.get("name") or "?"),
            status=str(raw.get("status") or "scheduled"),
            start=parse_time(raw.get("scheduled_for")),
            end=parse_time(raw.get("scheduled_until")),
            update=_latest_update(raw),
            url=_link(raw, page_url)))
    # In progress first, then the soonest.
    maintenances.sort(key=lambda m: (0 if m.status == "in_progress" else 1, m.start or 0))

    return ServiceSummary(
        key=key,
        indicator=normalize_indicator(status.get("indicator")),
        description=str(status.get("description") or ""),
        page_url=page_url,
        page_updated_at=parse_time(page.get("updated_at")),
        fetched_at=now,
        issues=issues,
        incidents=incidents,
        maintenances=maintenances,
        components_total=leaves)


def impact_rank(impact: str) -> int:
    return {"critical": 3, "major": 2, "minor": 1}.get(impact, 0)


# --------------------------------------------------------------------------- #
# Display helpers (pure)
# --------------------------------------------------------------------------- #


@dataclass
class IssueGroup:
    name: Optional[str]            # group name; None for stand-alone components
    status: str                    # worst status inside
    items: List[ComponentIssue]


def group_issues(issues: Sequence[ComponentIssue]) -> List[IssueGroup]:
    """Non-operational components grouped under their Statuspage group.

    Stand-alone components stay alone. Worst groups first, then by name.
    """
    buckets: Dict[Tuple[bool, str], IssueGroup] = {}
    for issue in issues:
        ident = (issue.group is not None, issue.group if issue.group is not None else issue.name)
        bucket = buckets.get(ident)
        if bucket is None:
            bucket = buckets[ident] = IssueGroup(name=issue.group, status=issue.status, items=[])
        bucket.items.append(issue)
        if COMPONENT_SEVERITY.get(issue.status, 0) > COMPONENT_SEVERITY.get(bucket.status, 0):
            bucket.status = issue.status
    ordered = sorted(
        buckets.values(),
        key=lambda g: (-COMPONENT_SEVERITY.get(g.status, 0), (g.name or g.items[0].name).lower()))
    return ordered


def overview_order(summaries: Iterable[ServiceSummary]) -> List[ServiceSummary]:
    """Problems first, then alphabetical inside each severity."""
    summaries = list(summaries)
    names = {s.key: (BY_KEY[s.key].name.lower() if s.key in BY_KEY else s.key) for s in summaries}
    return sorted(summaries, key=lambda s: (INDICATOR_ORDER.get(s.indicator, 9), names[s.key]))


@dataclass
class OverviewStats:
    total: int
    problems: int
    maintenance: int
    unknown: int
    worst: str          # worst indicator among the reachable services


def overview_stats(summaries: Sequence[ServiceSummary]) -> OverviewStats:
    problems = sum(1 for s in summaries if s.indicator in PROBLEM_INDICATORS)
    maintenance = sum(1 for s in summaries if s.indicator == IND_MAINTENANCE)
    unknown = sum(1 for s in summaries if s.indicator == IND_UNKNOWN)
    reachable = [s.indicator for s in summaries if s.indicator != IND_UNKNOWN]
    worst = min(reachable, key=INDICATOR_ORDER.get, default=IND_UNKNOWN)
    return OverviewStats(total=len(summaries), problems=problems, maintenance=maintenance,
                         unknown=unknown, worst=worst)


# --------------------------------------------------------------------------- #
# Fetching (public_api is injected)
# --------------------------------------------------------------------------- #

CACHE_TTL = 60.0          # a good summary is reused for a minute
ERROR_TTL = 15.0          # an unreachable page is retried soon, not hammered
MIN_REFRESH = 10.0        # "Refresh" cannot bypass the cache more often than this
FETCH_TIMEOUT = 8.0

_cache: Dict[str, Tuple[float, ServiceSummary]] = {}


def clear_cache() -> None:
    _cache.clear()


async def fetch_summary(api, service: Service, *, force: bool = False,
                        now: Optional[float] = None) -> ServiceSummary:
    """Summary of one service; never raises (an unreachable page is ``unknown``)."""
    from services.public_api import PublicAPIError

    clock = time.monotonic()
    hit = _cache.get(service.key)
    if hit is not None:
        stored_at, summary = hit
        age = clock - stored_at
        ttl = CACHE_TTL if summary.reachable else ERROR_TTL
        if age < (MIN_REFRESH if force else ttl):
            return summary
    try:
        payload = await api.get_json(service.summary_url, timeout=FETCH_TIMEOUT)
        if not isinstance(payload, dict):
            raise PublicAPIError("bad_payload", url=service.summary_url)
        summary = parse_summary(service.key, payload, now=now)
    except PublicAPIError as exc:
        summary = ServiceSummary(key=service.key, error=exc.code,
                                 page_url=service.base_url, fetched_at=now or time.time())
    except Exception:  # noqa: BLE001 — a malformed page must not break the overview
        summary = ServiceSummary(key=service.key, error="bad_payload",
                                 page_url=service.base_url, fetched_at=now or time.time())
    _cache[service.key] = (clock, summary)
    return summary


async def fetch_all(api, services: Sequence[Service] = SERVICES, *,
                    force: bool = False) -> List[ServiceSummary]:
    """All summaries in parallel (each bounded by :data:`FETCH_TIMEOUT`)."""
    return list(await asyncio.gather(*(fetch_summary(api, s, force=force) for s in services)))
