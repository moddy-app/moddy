"""
Public API client — keyless, free web APIs used by the utility tools.

Wiktionary, Have I Been Pwned (range API), RDAP, DNS-over-HTTPS, crt.sh,
Statuspage pages, recherche-entreprises (data.gouv), Deezer, iTunes Search,
public Spotify pages and the WhatsMyName site checks all go through here.

Why not the gateway (docs/infra/API_GATEWAY.md)? The gateway is for paid or
quota-bound providers: it logs every call to PostgreSQL and the staff webhook.
These endpoints are free and some tools fan out widely (/username probes ~700
sites per search), which would flood that log. Anything with an API key or a
cost belongs in the gateway instead.

What this client guarantees:

* one shared ``aiohttp`` session, opened lazily, closed with the bot;
* a descriptive User-Agent with a contact URL — Wikimedia throttles anonymous
  clients without one to 10 requests/min;
* the public-only resolver of ``utils/safe_http.py``: even a host taken from
  third-party data can never reach a private / internal address;
* a per-host concurrency cap and a per-request timeout;
* an optional Redis JSON cache (``cache_ttl``), fail-open;
* one exception type, :class:`PublicAPIError`, with a stable ``code``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

import aiohttp

from config import DOCS_URL
from utils.safe_http import PublicOnlyResolver

logger = logging.getLogger("moddy.public_api")

USER_AGENT = f"ModdyBot/1.0 (+{DOCS_URL}; Discord bot utility tools)"
DEFAULT_TIMEOUT = 15.0
DEFAULT_HOST_CONCURRENCY = 4
MAX_BODY = 8 * 1024 * 1024
_CACHE_PREFIX = "moddy:tools:cache"

# Stable codes for PublicAPIError.code — callers switch on these.
TIMEOUT = "timeout"
NETWORK = "network"
RATE_LIMITED = "rate_limited"
NOT_FOUND = "not_found"
HTTP_ERROR = "http_error"
BAD_PAYLOAD = "bad_payload"
TOO_LARGE = "too_large"


class PublicAPIError(Exception):
    def __init__(self, code: str, *, status: Optional[int] = None, url: str = ""):
        super().__init__(f"{code} ({status}) {url}")
        self.code = code
        self.status = status
        self.url = url


class PublicAPI:
    """``bot.public_api`` — see the module docstring."""

    def __init__(self, bot=None, *, host_concurrency: Optional[Dict[str, int]] = None):
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()
        self._host_limits: Dict[str, int] = dict(host_concurrency or {})
        self._semaphores: Dict[str, asyncio.Semaphore] = {}

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def session(self) -> aiohttp.ClientSession:
        if self._session is not None and not self._session.closed:
            return self._session
        async with self._session_lock:
            if self._session is None or self._session.closed:
                connector = aiohttp.TCPConnector(resolver=PublicOnlyResolver(), limit=100,
                                                 ttl_dns_cache=300)
                self._session = aiohttp.ClientSession(
                    connector=connector, headers={"User-Agent": USER_AGENT})
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    def set_host_limit(self, host: str, limit: int) -> None:
        """Cap concurrent requests to ``host`` (call before the first request)."""
        self._host_limits[host] = max(1, int(limit))
        self._semaphores.pop(host, None)

    def _semaphore(self, host: str) -> asyncio.Semaphore:
        sem = self._semaphores.get(host)
        if sem is None:
            sem = asyncio.Semaphore(self._host_limits.get(host, DEFAULT_HOST_CONCURRENCY))
            self._semaphores[host] = sem
        return sem

    # ------------------------------------------------------------------ #
    # Cache
    # ------------------------------------------------------------------ #
    @staticmethod
    def cache_key(namespace: str, *parts: Any) -> str:
        digest = hashlib.sha1(json.dumps(parts, default=str, sort_keys=True).encode()).hexdigest()[:20]
        return f"{_CACHE_PREFIX}:{namespace}:{digest}"

    async def cache_get(self, key: str) -> Optional[Any]:
        redis = getattr(self.bot, "redis", None)
        if redis is None:
            return None
        try:
            raw = await redis.get(key)
            return None if raw is None else json.loads(raw)
        except Exception as exc:  # noqa: BLE001 — fail open
            logger.debug("public_api cache get failed: %s", exc)
            return None

    async def cache_set(self, key: str, value: Any, ttl: int) -> None:
        redis = getattr(self.bot, "redis", None)
        if redis is None or ttl <= 0:
            return
        try:
            await redis.set(key, json.dumps(value, separators=(",", ":"), default=str), ex=int(ttl))
        except Exception as exc:  # noqa: BLE001
            logger.debug("public_api cache set failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Requests
    # ------------------------------------------------------------------ #
    async def request(self, method: str, url: str, *, params: Optional[dict] = None,
                      headers: Optional[dict] = None, data: Any = None, json_body: Any = None,
                      timeout: float = DEFAULT_TIMEOUT, max_bytes: int = MAX_BODY,
                      allow_redirects: bool = True, accept_statuses: tuple = ()) -> "PublicResponse":
        """Raw request. Raises :class:`PublicAPIError` on network / HTTP failure.

        Statuses listed in ``accept_statuses`` (e.g. ``(404,)`` for a site check)
        are returned instead of raising.
        """
        host = (urlsplit(url).hostname or "").lower()
        session = await self.session()
        try:
            async with self._semaphore(host):
                async with session.request(
                    method, url, params=params, headers=headers, data=data, json=json_body,
                    timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=allow_redirects,
                ) as resp:
                    body = await _read_capped(resp, max_bytes)
                    result = PublicResponse(status=resp.status, body=body, url=str(resp.url),
                                            headers=dict(resp.headers))
        except asyncio.TimeoutError as exc:
            raise PublicAPIError(TIMEOUT, url=url) from exc
        except PublicAPIError:
            raise
        except (aiohttp.ClientError, OSError) as exc:
            raise PublicAPIError(NETWORK, url=url) from exc

        if result.status in accept_statuses or 200 <= result.status < 300:
            return result
        if result.status == 429:
            raise PublicAPIError(RATE_LIMITED, status=429, url=url)
        if result.status == 404:
            raise PublicAPIError(NOT_FOUND, status=404, url=url)
        raise PublicAPIError(HTTP_ERROR, status=result.status, url=url)

    async def get_json(self, url: str, *, params: Optional[dict] = None,
                       headers: Optional[dict] = None, timeout: float = DEFAULT_TIMEOUT,
                       cache_ttl: int = 0, cache_namespace: str = "json") -> Any:
        """GET + JSON decode, optionally cached in Redis for ``cache_ttl`` seconds."""
        key = None
        if cache_ttl:
            key = self.cache_key(cache_namespace, url, params)
            cached = await self.cache_get(key)
            if cached is not None:
                return cached
        resp = await self.request("GET", url, params=params, headers=headers, timeout=timeout)
        try:
            value = json.loads(resp.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise PublicAPIError(BAD_PAYLOAD, status=resp.status, url=url) from exc
        if key:
            await self.cache_set(key, value, cache_ttl)
        return value

    async def get_text(self, url: str, *, params: Optional[dict] = None,
                       headers: Optional[dict] = None, timeout: float = DEFAULT_TIMEOUT,
                       max_bytes: int = MAX_BODY, cache_ttl: int = 0,
                       cache_namespace: str = "text") -> str:
        key = None
        if cache_ttl:
            key = self.cache_key(cache_namespace, url, params)
            cached = await self.cache_get(key)
            if cached is not None:
                return cached
        resp = await self.request("GET", url, params=params, headers=headers,
                                  timeout=timeout, max_bytes=max_bytes)
        text = resp.text()
        if key:
            await self.cache_set(key, text, cache_ttl)
        return text


class PublicResponse:
    __slots__ = ("status", "body", "url", "headers")

    def __init__(self, *, status: int, body: bytes, url: str, headers: Dict[str, str]):
        self.status = status
        self.body = body
        self.url = url
        self.headers = headers

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise PublicAPIError(BAD_PAYLOAD, status=self.status, url=self.url) from exc


async def _read_capped(resp: aiohttp.ClientResponse, max_bytes: int) -> bytes:
    length = resp.headers.get("Content-Length")
    if length and length.isdigit() and int(length) > max_bytes:
        raise PublicAPIError(TOO_LARGE, status=resp.status, url=str(resp.url))
    chunks = []
    total = 0
    async for chunk in resp.content.iter_chunked(65536):
        total += len(chunk)
        if total > max_bytes:
            raise PublicAPIError(TOO_LARGE, status=resp.status, url=str(resp.url))
        chunks.append(chunk)
    return b"".join(chunks)
