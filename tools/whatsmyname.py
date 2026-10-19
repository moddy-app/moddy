"""
``/username`` core — look a username up on ~650 sites with the community
WhatsMyName dataset (``wmn-data.json``, CC BY-SA 4.0, https://whatsmyname.app).

Each site entry says how to ask (``uri_check`` with ``{account}``, optional
``post_body`` / ``headers``) and how to read the answer:

* **exists**  — HTTP status ``e_code`` and the text ``e_string`` in the body;
* **missing** — HTTP status ``m_code`` and/or the text ``m_string``.

A profile only counts as FOUND when the status equals ``e_code``, ``e_string``
is in the body *and* ``m_string`` is not (a "not found" page that returns 200
would otherwise match). Only the link to a found profile ever leaves this
module: never the page content.

Everything is pure except :func:`load_dataset` and :func:`run_search`, which get
the ``bot.public_api`` client as an argument (tests pass a fake).
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote, urlsplit

DATASET_URL = "https://raw.githubusercontent.com/WebBreacher/WhatsMyName/main/wmn-data.json"
DATASET_TTL = 24 * 3600
LICENSE_NAME = "CC BY-SA 4.0"
PROJECT_URL = "https://whatsmyname.app"

NSFW_CATEGORY = "xx NSFW xx"
MAX_USERNAME = 40
USERNAME_RE = re.compile(r"^[A-Za-z0-9._\-]{1,%d}$" % MAX_USERNAME)

CONCURRENCY = 40          # requests in flight for one search
PER_HOST_LIMIT = 2        # simultaneous requests to one site
REQUEST_TIMEOUT = 6.0
GLOBAL_DEADLINE = 45.0
MAX_BODY = 1024 * 1024    # a page above this cannot be read: counted as "not checked"
MAX_PARALLEL_SEARCHES = 2
USER_COOLDOWN = 120.0
RESULT_TTL = 3600

#: Display order of the categories of the dataset (unknown ones go last).
CATEGORY_ORDER = ("social", "gaming", "coding", "tech", "video", "music", "images", "art",
                  "blog", "hobby", "business", "finance", "shopping", "dating", "news",
                  "political", "health", "archived", "misc")

ALL_STATUSES: Tuple[int, ...] = tuple(range(100, 600))
_REDIRECTS = frozenset({301, 302, 303, 307, 308})

# --------------------------------------------------------------------------- #
# Username validation
# --------------------------------------------------------------------------- #


class UsernameError(ValueError):
    """``code`` is one of ``empty`` / ``too_long`` / ``invalid``."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def validate_username(raw: str) -> str:
    """Normalise (``@`` dropped) and validate; raises :class:`UsernameError`."""
    name = (raw or "").strip().lstrip("@").strip()
    if not name:
        raise UsernameError("empty")
    if len(name) > MAX_USERNAME:
        raise UsernameError("too_long")
    if not USERNAME_RE.match(name):
        raise UsernameError("invalid")
    return name


# --------------------------------------------------------------------------- #
# Dataset
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Site:
    name: str
    uri_check: str
    uri_pretty: str
    cat: str
    e_code: int
    e_string: str
    m_code: Optional[int]
    m_string: str
    post_body: str
    headers: Tuple[Tuple[str, str], ...]
    strip_bad_char: str

    @property
    def host(self) -> str:
        return (urlsplit(self.uri_check).hostname or "").lower()


@dataclass
class Dataset:
    sites: List[Site]
    loaded_at: float = field(default_factory=time.time)


def _int_or_none(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_dataset(payload: dict) -> Dataset:
    """Usable sites of ``wmn-data.json``: valid, not NSFW, https/http, with ``{account}``."""
    sites: List[Site] = []
    for raw in payload.get("sites") or []:
        if not isinstance(raw, dict) or raw.get("valid") is False:
            continue
        cat = str(raw.get("cat") or "misc")
        uri = str(raw.get("uri_check") or "")
        body = str(raw.get("post_body") or "")
        e_code = _int_or_none(raw.get("e_code"))
        if cat == NSFW_CATEGORY or e_code is None or not uri.startswith(("http://", "https://")):
            continue
        if "{account}" not in uri and "{account}" not in body:
            continue
        headers = raw.get("headers") if isinstance(raw.get("headers"), dict) else {}
        sites.append(Site(
            name=str(raw.get("name") or urlsplit(uri).hostname or "?"),
            uri_check=uri,
            uri_pretty=str(raw.get("uri_pretty") or ""),
            cat=cat,
            e_code=e_code,
            e_string=str(raw.get("e_string") or ""),
            m_code=_int_or_none(raw.get("m_code")),
            m_string=str(raw.get("m_string") or ""),
            post_body=body,
            headers=tuple((str(k), str(v)) for k, v in headers.items()),
            strip_bad_char=str(raw.get("strip_bad_char") or "")))
    return Dataset(sites=sites)


_dataset: Optional[Dataset] = None


async def load_dataset(api, *, force: bool = False) -> Dataset:
    """The dataset, from memory (24 h), then Redis (24 h), then GitHub."""
    global _dataset
    if _dataset is not None and not force and time.time() - _dataset.loaded_at < DATASET_TTL:
        return _dataset
    text = await api.get_text(DATASET_URL, timeout=20, max_bytes=4 * 1024 * 1024,
                              cache_ttl=DATASET_TTL, cache_namespace="wmn")
    dataset = parse_dataset(json.loads(text))
    if not dataset.sites:
        raise ValueError("empty WhatsMyName dataset")
    for host in {s.host for s in dataset.sites if s.host}:
        api.set_host_limit(host, PER_HOST_LIMIT)
    _dataset = dataset
    return dataset


def reset_dataset() -> None:
    global _dataset
    _dataset = None


# --------------------------------------------------------------------------- #
# One check
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RequestSpec:
    method: str
    url: str
    headers: Dict[str, str]
    data: Optional[bytes]
    allow_redirects: bool
    profile_url: str       # the link shown to the user ("" when none is usable)


def _account_for(site: Site, username: str) -> str:
    account = username
    for char in site.strip_bad_char:
        account = account.replace(char, "")
    return account


def build_request(site: Site, username: str) -> RequestSpec:
    account = _account_for(site, username)
    encoded = quote(account, safe="")
    url = site.uri_check.replace("{account}", encoded)
    data = site.post_body.replace("{account}", account).encode("utf-8") if site.post_body else None
    pretty = site.uri_pretty or (site.uri_check if not site.post_body else "")
    profile = pretty.replace("{account}", encoded) if pretty else ""
    # A 3xx is a meaningful answer for some sites ("missing" redirects to the
    # home page): do not follow it when the dataset expects one.
    redirects = site.e_code not in _REDIRECTS and (site.m_code not in _REDIRECTS)
    return RequestSpec(
        method="POST" if data is not None else "GET",
        url=url,
        headers=dict(site.headers),
        data=data,
        allow_redirects=redirects,
        profile_url=profile)


def is_found(site: Site, status: int, body: str) -> bool:
    """The WhatsMyName detection rule (see the module docstring)."""
    if status != site.e_code:
        return False
    if site.e_string and site.e_string not in body:
        return False
    if site.m_string and site.m_string in body:
        return False
    return True


# --------------------------------------------------------------------------- #
# A whole search
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Profile:
    site: str
    url: str          # "" when the dataset gives no human-readable link
    cat: str


@dataclass
class Progress:
    done: int = 0
    total: int = 0


@dataclass
class SearchOutcome:
    username: str
    found: List[Profile]
    total: int          # sites that were to be checked
    checked: int        # sites that gave a readable answer
    timed_out: bool = False

    @property
    def unchecked(self) -> int:
        return max(0, self.total - self.checked)


async def run_search(api, dataset: Dataset, username: str, *, progress: Optional[Progress] = None,
                     concurrency: int = CONCURRENCY, deadline: float = GLOBAL_DEADLINE,
                     timeout: float = REQUEST_TIMEOUT, seed: Optional[int] = None) -> SearchOutcome:
    """Check every site; returns what was found when done or at the deadline."""
    from services.public_api import PublicAPIError

    sites = list(dataset.sites)
    random.Random(seed).shuffle(sites)       # interleave hosts: the per-host cap stalls less
    progress = progress if progress is not None else Progress()
    progress.done, progress.total = 0, len(sites)
    gate = asyncio.Semaphore(max(1, concurrency))
    found: List[Profile] = []
    checked = 0

    async def check(site: Site) -> None:
        nonlocal checked
        spec = build_request(site, username)
        try:
            async with gate:
                response = await api.request(
                    spec.method, spec.url, headers=spec.headers or None, data=spec.data,
                    timeout=timeout, max_bytes=MAX_BODY, allow_redirects=spec.allow_redirects,
                    accept_statuses=ALL_STATUSES)
            checked += 1
            if is_found(site, response.status, response.text()):
                found.append(Profile(site=site.name, url=spec.profile_url, cat=site.cat))
        except PublicAPIError:
            pass                  # blocked, down, too large: simply "not checked"
        except Exception:         # noqa: BLE001 — one odd site must never sink the search
            pass
        finally:
            progress.done += 1

    tasks = [asyncio.ensure_future(check(site)) for site in sites]
    timed_out = False
    try:
        _, pending = await asyncio.wait(tasks, timeout=deadline)
        if pending:
            timed_out = True
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        raise
    found.sort(key=lambda p: (category_rank(p.cat), p.site.lower()))
    return SearchOutcome(username=username, found=found, total=len(sites), checked=checked,
                         timed_out=timed_out)


# --------------------------------------------------------------------------- #
# Presentation helpers (pure)
# --------------------------------------------------------------------------- #


def category_rank(cat: str) -> int:
    try:
        return CATEGORY_ORDER.index(cat)
    except ValueError:
        return len(CATEGORY_ORDER)


def group_by_category(profiles: Iterable[Profile]) -> List[Tuple[str, List[Profile]]]:
    groups: Dict[str, List[Profile]] = {}
    for profile in profiles:
        groups.setdefault(profile.cat, []).append(profile)
    return sorted(groups.items(), key=lambda kv: (category_rank(kv[0]), kv[0]))


_MD_SPECIALS = re.compile(r"[\[\]()*_`~|<>\\]")


def safe_label(name: str) -> str:
    """A site name that cannot break a markdown link."""
    return _MD_SPECIALS.sub("", name).strip() or "?"


def safe_url(url: str) -> str:
    return url.replace(" ", "%20").replace(">", "%3E").replace("<", "%3C")


def profile_line(profile: Profile) -> str:
    """``- [Site](<url>)`` (no embed), or just the name when there is no link."""
    label = safe_label(profile.site)
    if not profile.url:
        return f"- {label}"
    return f"- [{label}](<{safe_url(profile.url)}>)"


def paginate(blocks: Sequence[Tuple[str, Sequence[str]]], *, budget: int = 2800,
             continued: Callable[[str], str] = lambda title: title) -> List[List[Tuple[str, List[str]]]]:
    """Pack ``(heading, lines)`` blocks into pages of at most ``budget`` characters.

    A block that does not fit is split across pages (its heading repeated through
    ``continued``). Every page holds at least one line, so progress is guaranteed.
    """
    pages: List[List[Tuple[str, List[str]]]] = []
    current: List[Tuple[str, List[str]]] = []
    used = 0
    for title, lines in blocks:
        remaining = list(lines)
        first = True
        while remaining:
            heading = title if first else continued(title)
            head_cost = len(heading) + 2
            if current and used + head_cost + len(remaining[0]) + 1 > budget:
                pages.append(current)
                current, used = [], 0
            chunk: List[str] = []
            cost = head_cost
            while remaining and (not chunk or used + cost + len(remaining[0]) + 1 <= budget):
                line = remaining.pop(0)
                chunk.append(line)
                cost += len(line) + 1
            current.append((heading, chunk))
            used += cost
            first = False
            if remaining:
                pages.append(current)
                current, used = [], 0
    if current:
        pages.append(current)
    return pages or [[]]


def export_text(outcome_name: str, profiles: Sequence[Profile], *, generated_at: str,
                category_label: Callable[[str], str] = lambda c: c) -> str:
    """The ``.txt`` export: plain, one profile per line, licence attribution at the end."""
    lines = [f"Username: {outcome_name}", f"Generated: {generated_at}",
             f"Profiles found: {len(profiles)}", ""]
    for cat, items in group_by_category(profiles):
        lines.append(f"[{category_label(cat)}]")
        for item in items:
            lines.append(f"{item.site}: {item.url}" if item.url else item.site)
        lines.append("")
    lines.append(f"Data: WhatsMyName ({LICENSE_NAME}) {PROJECT_URL}")
    return "\n".join(lines) + "\n"


def profiles_to_json(profiles: Sequence[Profile]) -> List[List[str]]:
    return [[p.site, p.url, p.cat] for p in profiles]


def profiles_from_json(items: Iterable[Sequence[str]]) -> List[Profile]:
    return [Profile(site=str(i[0]), url=str(i[1]), cat=str(i[2])) for i in items if len(i) >= 3]


# --------------------------------------------------------------------------- #
# Guard rails: per-user cooldown + bot-wide parallelism
# --------------------------------------------------------------------------- #


class Cooldowns:
    """In-memory per-user cooldown (the bot is a single process)."""

    def __init__(self, seconds: float = USER_COOLDOWN, *,
                 clock: Callable[[], float] = time.monotonic, max_entries: int = 4096):
        self.seconds = seconds
        self._clock = clock
        self._until: Dict[int, float] = {}
        self._max = max_entries

    def remaining(self, user_id: int) -> float:
        left = self._until.get(user_id, 0.0) - self._clock()
        return max(0.0, left)

    def hit(self, user_id: int) -> None:
        if len(self._until) >= self._max:
            now = self._clock()
            self._until = {u: t for u, t in self._until.items() if t > now}
        self._until[user_id] = self._clock() + self.seconds

    def clear(self, user_id: int) -> None:
        self._until.pop(user_id, None)


class SearchSlots:
    """At most ``size`` searches at once, bot-wide. Never queues: a full pool
    answers "busy" immediately instead of keeping people waiting on a spinner."""

    def __init__(self, size: int = MAX_PARALLEL_SEARCHES):
        self.size = size
        self.active = 0

    def try_enter(self) -> bool:
        if self.active >= self.size:
            return False
        self.active += 1
        return True

    def leave(self) -> None:
        self.active = max(0, self.active - 1)


def format_wait(seconds: float) -> str:
    """``1 min 12 s`` / ``45 s`` — language-neutral."""
    total = max(1, int(round(seconds)))
    minutes, secs = divmod(total, 60)
    return f"{minutes} min {secs} s" if minutes else f"{secs} s"
