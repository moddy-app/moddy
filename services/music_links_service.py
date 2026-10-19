"""
Music links service (``bot.music_links``) — a Spotify link in, the same song on
other platforms out. Shared by the ``music_links`` module and ``/music``.

The Odesli / song.link public API is dead (``401 PUBLIC_API_ACCESS_DEPRECATED``)
and the Spotify Web API is closed to new apps, so the resolution is home-made
and uses only free, keyless endpoints through ``bot.public_api``:

1. the **public Spotify page** — its Open Graph / ``music:*`` meta tags give the
   title, artist, album, year, duration and cover (a non-browser User-Agent is
   required: a browser one receives the empty single-page-app shell);
2. the **Deezer** public search API;
3. the **iTunes Search API** (Apple Music), about 20 requests a minute — a local
   budget keeps well under it and a skipped lookup simply leaves Apple Music out;
4. a **YouTube Music search link** (there is no free API).

Matching rules are in ``tools/music.py`` (strict: no link rather than a wrong
link). Results are cached per Spotify item for 30 days in Redis (failures one
hour, partial results ten minutes). Every external call is bounded; concurrency
is capped; the module never awaits a resolution inside ``on_message`` (see
:meth:`spawn`).

See docs/features/MUSIC_LINKS.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict, deque
from typing import Any, Awaitable, Deque, Dict, List, Optional, Set
from urllib.parse import urljoin, urlsplit

from services.public_api import (
    NETWORK, NOT_FOUND, TIMEOUT, PublicAPIError,
)
from tools import music as mu

logger = logging.getLogger("moddy.music_links")

SPOTIFY_ACCEPT_LANGUAGE = {"Accept-Language": "en"}
SPOTIFY_TIMEOUT = 10.0
SPOTIFY_MAX_BYTES = 2_000_000
SEARCH_TIMEOUT = 8.0
#: Deadline of one whole resolution (Spotify page + both searches).
RESOLVE_TIMEOUT = 25.0
#: Deadline of a background reply (short links + resolutions + the send).
BACKGROUND_TIMEOUT = 40.0

MAX_CONCURRENT = 4
MAX_PENDING_BACKGROUND = 40

TTL_RESOLVED = 30 * 24 * 3600
TTL_PARTIAL = 10 * 60
TTL_FAILED = 3600
TTL_TRANSIENT = 120

#: iTunes allows about 20 requests/minute per IP: stay below.
ITUNES_BUDGET = 16
ITUNES_WINDOW = 60.0

#: Failure codes (also the keys of ``tools.music.errors``).
NOT_FOUND_CODE = "not_found"
UNAVAILABLE = "unavailable"
UNSUPPORTED = "unsupported"

_CACHE_PREFIX = "moddy:music:v1"
_MAX_REDIRECTS = 3
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)
_LOCAL_CACHE_MAX = 512

DEEZER_API = "https://api.deezer.com"
ITUNES_API = "https://itunes.apple.com/search"


class MusicLinksError(Exception):
    """An expected failure: ``code`` maps to ``tools.music.errors.<code>``."""

    def __init__(self, code: str, *, ttl: int = 0):
        super().__init__(code)
        self.code = code
        self.ttl = ttl


class MusicLinksService:
    def __init__(self, bot):
        self.bot = bot
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        self._inflight: Dict[str, "asyncio.Task[mu.MusicInfo]"] = {}
        self._local: "OrderedDict[str, tuple]" = OrderedDict()
        self._itunes_calls: Deque[float] = deque()
        self._background: Set["asyncio.Task[Any]"] = set()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def spawn(self, coro: Awaitable[Any], *, timeout: float = BACKGROUND_TIMEOUT) -> bool:
        """Run ``coro`` in the background, bounded in time and in number.

        ``on_message`` must never wait for the network: the module hands its
        work over here and returns. Returns False (and drops the work) when too
        many replies are already pending.
        """
        if len(self._background) >= MAX_PENDING_BACKGROUND:
            close = getattr(coro, "close", None)
            if close:
                close()
            return False

        async def runner() -> None:
            try:
                await asyncio.wait_for(coro, timeout)
            except asyncio.TimeoutError:
                logger.debug("music links: background task timed out")
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a background reply must never crash the bot
                logger.exception("music links: background task failed")

        task = asyncio.get_running_loop().create_task(runner())
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return True

    async def stop(self) -> None:
        tasks = list(self._background) + list(self._inflight.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------------ #
    # Statistics
    # ------------------------------------------------------------------ #
    def record(self, metric: str, **dims: str) -> None:
        """``music_links.<metric>``; never raises."""
        stats = getattr(self.bot, "stats", None)
        if stats is None:
            return
        try:
            stats.incr(f"music_links.{metric}", dims=dims)
        except Exception:  # noqa: BLE001
            logger.debug("music links: stats failed", exc_info=True)

    # ------------------------------------------------------------------ #
    # Cache (Redis, in-process fallback)
    # ------------------------------------------------------------------ #
    async def _cache_get(self, key: str) -> Optional[Any]:
        redis = getattr(self.bot, "redis", None)
        if redis is not None:
            try:
                raw = await redis.get(key)
                if raw is not None:
                    return json.loads(raw)
            except Exception as exc:  # noqa: BLE001 — fail open
                logger.debug("music links: cache get failed: %s", exc)
        entry = self._local.get(key)
        if entry is None:
            return None
        expires, raw = entry
        if expires < time.monotonic():
            self._local.pop(key, None)
            return None
        return json.loads(raw)

    async def _cache_set(self, key: str, value: Any, ttl: int) -> None:
        raw = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        redis = getattr(self.bot, "redis", None)
        if redis is not None:
            try:
                await redis.set(key, raw, ex=int(ttl))
                return
            except Exception as exc:  # noqa: BLE001
                logger.debug("music links: cache set failed: %s", exc)
        self._local[key] = (time.monotonic() + ttl, raw)
        self._local.move_to_end(key)
        while len(self._local) > _LOCAL_CACHE_MAX:
            self._local.popitem(last=False)

    # ------------------------------------------------------------------ #
    # Short links
    # ------------------------------------------------------------------ #
    async def resolve_short(self, url: str) -> mu.SpotifyRef:
        """Follow a ``spotify.link`` / ``spoti.fi`` redirect (3 hops at most,
        only through Spotify hosts) to the ``open.spotify.com`` item it names.

        Raises :class:`MusicLinksError` (``unsupported`` for a playlist,
        ``not_found`` when it leads anywhere else, ``unavailable`` on network).
        """
        key = f"{_CACHE_PREFIX}:short:{hashlib.sha1(url.lower().encode()).hexdigest()[:20]}"
        cached = await self._cache_get(key)
        if isinstance(cached, dict) and cached.get("kind") in mu.KINDS:
            return mu.SpotifyRef(cached["kind"], str(cached["id"]))

        api = self.bot.public_api
        current = url
        for _ in range(_MAX_REDIRECTS + 1):
            host = (urlsplit(current).hostname or "").lower()
            if urlsplit(current).scheme != "https" or host not in mu.SHORT_LINK_HOSTS:
                raise MusicLinksError(NOT_FOUND_CODE)
            if host == "open.spotify.com":
                if mu.is_unsupported_url(current):
                    raise MusicLinksError(UNSUPPORTED)
                ref = mu.ref_from_url(current)
                if ref is None:
                    raise MusicLinksError(NOT_FOUND_CODE)
                await self._cache_set(key, {"kind": ref.kind, "id": ref.id}, TTL_RESOLVED)
                return ref
            try:
                response = await api.request(
                    "GET", current, allow_redirects=False, timeout=SPOTIFY_TIMEOUT,
                    max_bytes=64_000, accept_statuses=_REDIRECT_STATUSES,
                    headers=SPOTIFY_ACCEPT_LANGUAGE)
            except PublicAPIError as exc:
                raise MusicLinksError(NOT_FOUND_CODE if exc.code == NOT_FOUND else UNAVAILABLE) from exc
            location = response.headers.get("Location") or response.headers.get("location")
            if response.status not in _REDIRECT_STATUSES or not location:
                raise MusicLinksError(NOT_FOUND_CODE)
            current = urljoin(current, location)
        raise MusicLinksError(NOT_FOUND_CODE)

    async def refs_from_text(self, text: str, *,
                             limit: int = mu.MAX_LINKS_PER_MESSAGE) -> List[mu.SpotifyRef]:
        """Every resolvable Spotify item of ``text`` (short links followed),
        without duplicates. Links that cannot be followed are skipped."""
        refs: List[mu.SpotifyRef] = []
        for parsed in mu.find_links(text, limit=limit):
            ref = parsed.ref
            if parsed.type == mu.LINK_SHORT:
                try:
                    ref = await self.resolve_short(parsed.url)
                except MusicLinksError:
                    continue
            if ref is not None and ref not in refs:
                refs.append(ref)
        return refs[:limit]

    # ------------------------------------------------------------------ #
    # Resolution
    # ------------------------------------------------------------------ #
    async def resolve(self, ref: mu.SpotifyRef, *, source: str = "auto") -> mu.MusicInfo:
        """The item behind ``ref`` and where else it lives.

        Raises :class:`MusicLinksError` (``not_found`` / ``unavailable``) when
        Spotify's page cannot be read — a card needs at least a title.
        """
        try:
            info = await self._resolve_cached(ref)
        except MusicLinksError as exc:
            self.record("failed", reason=exc.code, source=source)
            raise
        self.record("resolved", kind=ref.kind, source=source)
        return info

    async def _resolve_cached(self, ref: mu.SpotifyRef) -> mu.MusicInfo:
        key = f"{_CACHE_PREFIX}:{ref.key}"
        failed_key = f"{_CACHE_PREFIX}:fail:{ref.key}"

        info = mu.MusicInfo.from_dict(await self._cache_get(key))
        if info is not None:
            return info
        failure = await self._cache_get(failed_key)
        if isinstance(failure, dict) and failure.get("code"):
            raise MusicLinksError(str(failure["code"]))

        # Concurrent requests for the same item share one resolution.
        task = self._inflight.get(ref.key)
        if task is None:
            task = asyncio.get_running_loop().create_task(
                self._resolve_and_store(ref, key, failed_key))
            self._inflight[ref.key] = task
            task.add_done_callback(lambda _t, k=ref.key: self._inflight.pop(k, None))
        return await asyncio.shield(task)

    async def resolve_quiet(self, ref: mu.SpotifyRef, *, source: str = "auto") -> Optional[mu.MusicInfo]:
        """:meth:`resolve`, or ``None`` on any failure (the module stays silent)."""
        try:
            return await self.resolve(ref, source=source)
        except MusicLinksError:
            return None
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("music links: unexpected failure resolving %s", ref.key)
            return None

    async def _resolve_and_store(self, ref: mu.SpotifyRef, key: str, failed_key: str) -> mu.MusicInfo:
        try:
            async with self._semaphore:
                info = await asyncio.wait_for(self._resolve_uncached(ref), RESOLVE_TIMEOUT)
        except MusicLinksError as exc:
            if exc.ttl:
                await self._cache_set(failed_key, {"code": exc.code}, exc.ttl)
            raise
        except asyncio.TimeoutError as exc:
            await self._cache_set(failed_key, {"code": UNAVAILABLE}, TTL_TRANSIENT)
            raise MusicLinksError(UNAVAILABLE) from exc
        await self._cache_set(key, info.to_dict(), TTL_PARTIAL if info.partial else TTL_RESOLVED)
        return info

    async def _resolve_uncached(self, ref: mu.SpotifyRef) -> mu.MusicInfo:
        meta = await self._fetch_meta(ref)
        info = mu.MusicInfo.from_meta(meta)
        info.links[mu.PLATFORM_YOUTUBE] = mu.youtube_music_search_url(meta)

        deezer, apple = await asyncio.gather(
            self._guarded_lookup(self._deezer(meta)),
            self._guarded_lookup(self._apple(meta)),
        )
        for platform, outcome in ((mu.PLATFORM_DEEZER, deezer), (mu.PLATFORM_APPLE, apple)):
            url, failed = outcome
            if url:
                info.links[platform] = url
            info.partial = info.partial or failed
        return info

    @staticmethod
    async def _guarded_lookup(lookup: Awaitable[Optional[str]]):
        """``(url_or_None, failed)``: a platform that errors is left out, and the
        result is cached only briefly so it is asked again soon."""
        try:
            return await lookup, False
        except (PublicAPIError, asyncio.TimeoutError, _BudgetExceeded) as exc:
            logger.debug("music links: lookup failed: %s", exc)
            return None, True
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("music links: lookup crashed")
            return None, True

    async def _fetch_meta(self, ref: mu.SpotifyRef) -> mu.SpotifyMeta:
        try:
            html = await self.bot.public_api.get_text(
                ref.url, headers=SPOTIFY_ACCEPT_LANGUAGE, timeout=SPOTIFY_TIMEOUT,
                max_bytes=SPOTIFY_MAX_BYTES)
        except PublicAPIError as exc:
            if exc.code == NOT_FOUND:
                raise MusicLinksError(NOT_FOUND_CODE, ttl=TTL_FAILED) from exc
            # Timeout / network / 403 / 429: Spotify may be blocking this IP.
            logger.info("music links: Spotify page unavailable (%s, status %s)", exc.code, exc.status)
            raise MusicLinksError(UNAVAILABLE,
                                  ttl=TTL_TRANSIENT if exc.code in (TIMEOUT, NETWORK) else TTL_FAILED) from exc
        meta = mu.parse_spotify_page(html, ref)
        if meta is None:
            logger.info("music links: no metadata on the Spotify page of %s", ref.key)
            raise MusicLinksError(UNAVAILABLE, ttl=TTL_FAILED)
        return meta

    # -- Deezer ---------------------------------------------------------- #
    async def _deezer(self, meta: mu.SpotifyMeta) -> Optional[str]:
        endpoint = {mu.KIND_TRACK: "search", mu.KIND_ALBUM: "search/album",
                    mu.KIND_ARTIST: "search/artist"}[meta.kind]
        queries = [mu.plain_query(meta)]
        fallback = mu.deezer_fallback_query(meta)
        if fallback:
            queries.append(fallback)
        for query in queries:
            payload = await asyncio.wait_for(
                self.bot.public_api.get_json(
                    f"{DEEZER_API}/{endpoint}", params={"q": query, "limit": 25},
                    timeout=SEARCH_TIMEOUT),
                SEARCH_TIMEOUT + 2)
            if isinstance(payload, dict) and payload.get("error"):
                raise PublicAPIError("rate_limited" if "quota" in str(payload["error"]).lower()
                                     else "bad_payload", url=endpoint)
            match = mu.best_match(meta, mu.deezer_candidates(payload, meta.kind))
            if match is not None:
                return match.url
        return None

    # -- Apple Music (iTunes Search API) --------------------------------- #
    def _itunes_allowed(self) -> bool:
        now = time.monotonic()
        while self._itunes_calls and now - self._itunes_calls[0] > ITUNES_WINDOW:
            self._itunes_calls.popleft()
        if len(self._itunes_calls) >= ITUNES_BUDGET:
            return False
        self._itunes_calls.append(now)
        return True

    async def _apple(self, meta: mu.SpotifyMeta) -> Optional[str]:
        entity = {mu.KIND_TRACK: "song", mu.KIND_ALBUM: "album",
                  mu.KIND_ARTIST: "musicArtist"}[meta.kind]
        if not self._itunes_allowed():
            raise _BudgetExceeded("itunes budget")
        payload = await asyncio.wait_for(
            self.bot.public_api.get_json(
                ITUNES_API, params={"term": mu.plain_query(meta), "entity": entity,
                                    "media": "music", "limit": 15},
                timeout=SEARCH_TIMEOUT),
            SEARCH_TIMEOUT + 2)
        match = mu.best_match(meta, mu.itunes_candidates(payload, meta.kind))
        return match.url if match is not None else None


class _BudgetExceeded(Exception):
    """The local iTunes request budget is spent: skip Apple Music this time."""
