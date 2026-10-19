"""
trace.moe adapter — which anime, episode and moment a screenshot comes from.

One operation, ``search`` (``https://api.trace.moe/search?anilistInfo&cutBorders``).
The API works anonymously (100 searches a month, one request at a time per
IP); ``TRACEMOE_API_KEY`` is optional and sent as ``x-trace-key`` to raise the
quota and concurrency. As with SauceNAO the image reaches it either as a URL
(``CallSpec.metadata["image_url"]``, kept out of the webhook-logged payload) or
as bytes (``CallSpec.binary``).

Errors: 402 (quota used up) and 429 map to ``ProviderError(402)`` /
``RateLimitError``; 400 is an unreadable or unfetchable image
(``ProviderError(400)``, not retried); 5xx are retried by the gateway.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import aiohttp

from .base import BaseAdapter, AdapterResult
from ..spec import CallSpec
from ..errors import ConfigurationError, ProviderError, RateLimitError
from ..ratelimit import UNIT_REQUESTS

logger = logging.getLogger("moddy.gateway.tracemoe")

_ENDPOINT = "https://api.trace.moe/search"
_USER_AGENT = "Moddy/1.0 (+https://moddy.app)"
_KEEP = 3  # results kept in the answer


def trim_response(data: dict) -> dict:
    """The part of a trace.moe response the bot uses (top results, slim AniList info)."""
    results = []
    for item in (data.get("result") or [])[:_KEEP]:
        if not isinstance(item, dict):
            continue
        anilist = item.get("anilist")
        if isinstance(anilist, dict):
            anilist = {k: anilist.get(k) for k in ("id", "title", "isAdult")}
        results.append({
            "anilist": anilist,
            "filename": item.get("filename"),
            "episode": item.get("episode"),
            "from": item.get("from"),
            "to": item.get("to"),
            "similarity": item.get("similarity"),
            "image": item.get("image"),
        })
    return {"result": results}


class TraceMoeAdapter(BaseAdapter):
    provider = "tracemoe"

    def __init__(self, api_key: Optional[str] = None):
        # Optional key: anonymous use is supported (and is the default).
        self._api_key = api_key or None
        self._session: Optional[aiohttp.ClientSession] = None
        # Anonymous callers get one request at a time per IP.
        self._slots = asyncio.Semaphore(4 if self._api_key else 1)

    async def start(self) -> None:
        headers = {"User-Agent": _USER_AGENT}
        if self._api_key:
            headers["x-trace-key"] = self._api_key
        self._session = aiohttp.ClientSession(headers=headers)
        logger.info("trace.moe adapter ready (%s)", "keyed" if self._api_key else "anonymous")

    async def stop(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def execute(self, spec: CallSpec) -> AdapterResult:
        if spec.operation != "search":
            raise ConfigurationError(f"Unsupported trace.moe operation: {spec.operation!r}")
        payload = spec.payload or {}
        params = {"anilistInfo": "", "cutBorders": ""}
        async with self._slots:
            if spec.binary:
                form = aiohttp.FormData()
                form.add_field("image", spec.binary, filename=payload.get("filename") or "image",
                               content_type=payload.get("mime") or "application/octet-stream")
                request = self._session.post(_ENDPOINT, params=params, data=form)
            else:
                image_url = (spec.metadata or {}).get("image_url")
                if not image_url:
                    raise ConfigurationError("search needs metadata['image_url'] or binary bytes")
                request = self._session.get(_ENDPOINT, params={**params, "url": image_url})
            async with request as resp:
                if resp.status == 402:
                    raise ProviderError("tracemoe", 402, "quota")
                if resp.status == 429:
                    retry = None
                    try:
                        retry = float(resp.headers.get("Retry-After", 0)) or None
                    except (TypeError, ValueError):
                        pass
                    raise RateLimitError("tracemoe", retry)
                if resp.status >= 400:
                    raise ProviderError("tracemoe", resp.status, (await resp.text())[:200])
                try:
                    data = await resp.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError) as exc:
                    raise ProviderError("tracemoe", 502, "non-JSON answer") from exc
        if not isinstance(data, dict):
            raise ProviderError("tracemoe", 502, "unexpected answer")
        if data.get("error"):
            raise ProviderError("tracemoe", 400, str(data["error"])[:200])
        return AdapterResult(data=trim_response(data), rate_cost={UNIT_REQUESTS: 1})
