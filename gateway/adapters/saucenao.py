"""
SauceNAO adapter — reverse image search over art / anime / manga indexes.

One operation, ``search`` (``https://saucenao.com/search.php``, ``output_type=2``,
all indexes). The image reaches SauceNAO either as a **URL** (SauceNAO fetches
it itself; the URL travels in ``CallSpec.metadata["image_url"]`` and never in
``payload``, because the payload is forwarded to the staff webhook and a Discord
CDN link is a signed, private URL) or as **bytes** (``CallSpec.binary``) when
SauceNAO cannot fetch the URL.

Authentication is the ``api_key`` query parameter (``SAUCENAO_API_KEY``); the
key never appears in an exception message or a log line. The account limits
(30-second burst + daily) are enforced by the gateway rate limiter before the
call (see ``gateway/config.py``); this adapter only translates what SauceNAO
answers into typed gateway errors:

* HTTP 429                       -> ``RateLimitError``
* "limit" in the header message  -> ``ProviderError(402, "limit")``  (not retried)
* other negative header status   -> ``ProviderError(400, message)``  (bad image / unfetchable URL)
* positive header status         -> ``ProviderError(503, ...)``      (SauceNAO side, retried)
"""

from __future__ import annotations

import logging
from typing import Optional

import aiohttp

from .base import BaseAdapter, AdapterResult
from ..spec import CallSpec
from ..errors import ConfigurationError, ProviderError, RateLimitError
from ..ratelimit import UNIT_REQUESTS

logger = logging.getLogger("moddy.gateway.saucenao")

_ENDPOINT = "https://saucenao.com/search.php"
_USER_AGENT = "Moddy/1.0 (+https://moddy.app)"
#: Fields of each result kept in the answer (the rest is bulk).
_HEADER_KEYS = ("similarity", "thumbnail", "index_id", "index_name", "hidden")


def trim_response(data: dict) -> dict:
    """The part of a SauceNAO response the bot uses."""
    header = data.get("header") or {}
    results = []
    for item in (data.get("results") or [])[:10]:
        if not isinstance(item, dict):
            continue
        h = item.get("header") or {}
        results.append({
            "header": {k: h.get(k) for k in _HEADER_KEYS if k in h},
            "data": item.get("data") or {},
        })
    return {
        "header": {k: header.get(k) for k in
                   ("status", "short_remaining", "long_remaining", "short_limit", "long_limit")
                   if k in header},
        "results": results,
    }


def check_header(data: dict) -> None:
    """Raise the typed error a non-zero SauceNAO ``header.status`` stands for."""
    header = data.get("header") or {}
    status = header.get("status", 0)
    if not isinstance(status, int) or status == 0:
        return
    message = str(header.get("message") or "")[:200]
    if "limit" in message.lower():
        raise ProviderError("saucenao", 402, "limit")
    if status < 0:
        raise ProviderError("saucenao", 400, message or "invalid request")
    raise ProviderError("saucenao", 503, message or "server error")


class SauceNAOAdapter(BaseAdapter):
    provider = "saucenao"

    def __init__(self, api_key: Optional[str]):
        if not api_key:
            raise ConfigurationError("SAUCENAO_API_KEY is required for SauceNAOAdapter")
        self._api_key = api_key
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(headers={"User-Agent": _USER_AGENT})
        logger.info("SauceNAO adapter ready")

    async def stop(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def execute(self, spec: CallSpec) -> AdapterResult:
        if spec.operation != "search":
            raise ConfigurationError(f"Unsupported SauceNAO operation: {spec.operation!r}")
        payload = spec.payload or {}
        params = {
            "output_type": 2,
            "api_key": self._api_key,
            "numres": int(payload.get("numres", 8)),
            "db": 999,
            "hide": int(payload.get("hide", 3)),
        }
        if spec.binary:
            form = aiohttp.FormData()
            form.add_field("file", spec.binary, filename=payload.get("filename") or "image",
                           content_type=payload.get("mime") or "application/octet-stream")
            request = self._session.post(_ENDPOINT, params=params, data=form)
        else:
            image_url = (spec.metadata or {}).get("image_url")
            if not image_url:
                raise ConfigurationError("search needs metadata['image_url'] or binary bytes")
            request = self._session.get(_ENDPOINT, params={**params, "url": image_url})

        async with request as resp:
            if resp.status == 429:
                raise RateLimitError("saucenao", None)
            if resp.status >= 400:
                # Never echo the URL: it carries the API key.
                raise ProviderError("saucenao", resp.status, (await resp.text())[:200])
            try:
                data = await resp.json(content_type=None)
            except (ValueError, aiohttp.ContentTypeError) as exc:
                # An HTML page (maintenance / Cloudflare) instead of JSON.
                raise ProviderError("saucenao", 502, "non-JSON answer") from exc
        if not isinstance(data, dict):
            raise ProviderError("saucenao", 502, "unexpected answer")
        check_header(data)
        return AdapterResult(data=trim_response(data), rate_cost={UNIT_REQUESTS: 1})
