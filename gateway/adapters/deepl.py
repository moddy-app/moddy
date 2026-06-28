from __future__ import annotations
import logging
from typing import Optional

import aiohttp

from .base import BaseAdapter, AdapterResult
from ..spec import CallSpec
from ..errors import ConfigurationError, ProviderError, RateLimitError

logger = logging.getLogger("moddy.gateway.deepl")


class DeepLAdapter(BaseAdapter):
    provider = "deepl"

    def __init__(self, api_key: Optional[str], free: bool = True):
        if not api_key:
            raise ConfigurationError("DEEPL_API_KEY is required for DeepLAdapter")
        self._api_key = api_key
        base = "https://api-free.deepl.com" if free else "https://api.deepl.com"
        self._base_url = f"{base}/v2"
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"}
        )
        tier = "free" if "api-free" in self._base_url else "pro"
        logger.info("DeepL adapter ready (tier=%s)", tier)

    async def stop(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def execute(self, spec: CallSpec) -> AdapterResult:
        if spec.operation == "translate":
            return await self._translate(spec)
        raise ConfigurationError(f"Unsupported DeepL operation: {spec.operation!r}")

    async def _translate(self, spec: CallSpec) -> AdapterResult:
        p = spec.payload
        form: dict = {
            "text": p["text"],
            "target_lang": p["target_lang"],
        }
        if p.get("source_lang"):
            form["source_lang"] = p["source_lang"]

        async with self._session.post(
            f"{self._base_url}/translate", data=form
        ) as resp:
            await self._raise_for_status(resp)
            data = await resp.json()

        translation = data["translations"][0]
        return AdapterResult(
            data={
                "text": translation["text"],
                "detected_source_language": translation.get("detected_source_language"),
            }
        )

    async def _raise_for_status(self, resp: aiohttp.ClientResponse) -> None:
        if resp.status == 429:
            retry_after: Optional[float] = None
            try:
                retry_after = float(resp.headers.get("Retry-After", 0)) or None
            except (ValueError, TypeError):
                pass
            raise RateLimitError("deepl", retry_after)
        if resp.status >= 400:
            try:
                body = await resp.text()
            except Exception:
                body = ""
            raise ProviderError("deepl", resp.status, body[:500])
