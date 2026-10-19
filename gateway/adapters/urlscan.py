"""
urlscan.io adapter — sandboxed page scan + screenshot for ``/link-check``.

* ``scan_submit`` → ``POST /api/v1/scan/`` with ``visibility: "unlisted"`` (not
  public in search; visible to security vendors only). Returns the scan uuid.
* ``scan_result`` → waits for the scan to finish, then returns its verdict and
  the page facts. The wait (``GET /api/v1/result/<uuid>/`` answers 404 until the
  scan is done) is a polling loop *inside this one call*, so the gateway log and
  rate limiter see one call, not ten; the loop ends at ``max_wait`` with
  ``{"ready": False}`` so the caller can still link to the pending report.

As for VirusTotal, the scanned URL rides on ``CallSpec.binary`` so tokens inside
a link never reach the staff webhook or the ``api_calls`` table.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import aiohttp

from .base import BaseAdapter, AdapterResult
from ..spec import CallSpec
from ..errors import ConfigurationError, ProviderError, RateLimitError
from ..ratelimit import UNIT_REQUESTS

logger = logging.getLogger("moddy.gateway.urlscan")

_BASE = "https://urlscan.io/api/v1"
SCREENSHOT_URL = "https://urlscan.io/screenshots/{uuid}.png"
REPORT_URL = "https://urlscan.io/result/{uuid}/"

OPERATIONS = ("scan_submit", "scan_result")
MODEL_SCAN = "scan"
MODEL_RESULT = "result"

DEFAULT_MAX_WAIT = 40.0
FIRST_DELAY = 8.0     # urlscan advises waiting ~10 s before the first poll
POLL_INTERVAL = 3.0


def parse_result(data: dict, uuid: str) -> dict:
    """Normalised verdict + page facts of a finished scan."""
    data = data or {}
    verdicts = data.get("verdicts") or {}
    overall = verdicts.get("overall") or {}
    page = data.get("page") or {}
    task = data.get("task") or {}

    brands = []
    for source in (overall.get("brands"), (verdicts.get("urlscan") or {}).get("brands")):
        for brand in source or []:
            name = brand.get("name") if isinstance(brand, dict) else brand
            if name and str(name) not in brands:
                brands.append(str(name))
    return {
        "ready": True,
        "uuid": uuid,
        "malicious": bool(overall.get("malicious")),
        "score": int(overall.get("score") or 0),
        "categories": [str(c) for c in (overall.get("categories") or [])][:5],
        "brands": brands[:5],
        "page_url": page.get("url"),
        "page_ip": page.get("ip"),
        "country": page.get("country"),
        "title": page.get("title"),
        "report_url": task.get("reportURL") or REPORT_URL.format(uuid=uuid),
        "screenshot_url": task.get("screenshotURL") or SCREENSHOT_URL.format(uuid=uuid),
    }


class UrlscanAdapter(BaseAdapter):
    provider = "urlscan"

    def __init__(self, api_key: Optional[str]):
        if not api_key:
            raise ConfigurationError("URLSCAN_API_KEY is required for UrlscanAdapter")
        self._api_key = api_key
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(headers={
            "API-Key": self._api_key, "accept": "application/json"})
        logger.info("urlscan adapter ready")

    async def stop(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def execute(self, spec: CallSpec) -> AdapterResult:
        if spec.operation == "scan_submit":
            return await self._submit(spec)
        if spec.operation == "scan_result":
            return await self._wait_result(spec)
        raise ConfigurationError(f"Unsupported urlscan operation: {spec.operation!r}")

    async def _submit(self, spec: CallSpec) -> AdapterResult:
        if not spec.binary:
            raise ConfigurationError("scan_submit requires the URL on CallSpec.binary")
        body = {"url": spec.binary.decode("utf-8"), "visibility": "unlisted"}
        async with self._session.post(f"{_BASE}/scan/", json=body) as resp:
            await self._raise_for_status(resp)
            data = await resp.json(content_type=None)
        uuid = (data or {}).get("uuid")
        if not uuid:
            raise ProviderError("urlscan", 502, "submission returned no uuid")
        return AdapterResult(
            data={"uuid": uuid, "report_url": REPORT_URL.format(uuid=uuid)},
            rate_cost={UNIT_REQUESTS: 1})

    async def _wait_result(self, spec: CallSpec) -> AdapterResult:
        payload = spec.payload or {}
        uuid = str(payload.get("uuid") or "")
        if not uuid:
            raise ConfigurationError("scan_result requires payload.uuid")
        max_wait = float(payload.get("max_wait", DEFAULT_MAX_WAIT))
        deadline = time.monotonic() + max_wait
        await asyncio.sleep(min(float(payload.get("first_delay", FIRST_DELAY)), max_wait))
        while True:
            async with self._session.get(f"{_BASE}/result/{uuid}/") as resp:
                if resp.status == 200:
                    return AdapterResult(
                        data=parse_result(await resp.json(content_type=None), uuid),
                        rate_cost={UNIT_REQUESTS: 1})
                if resp.status == 410:
                    raise ProviderError("urlscan", 410, "scan deleted")
                if resp.status not in (404, 202):
                    await self._raise_for_status(resp)
            if time.monotonic() + POLL_INTERVAL >= deadline:
                return AdapterResult(
                    data={"ready": False, "uuid": uuid, "report_url": REPORT_URL.format(uuid=uuid)},
                    rate_cost={UNIT_REQUESTS: 1})
            await asyncio.sleep(POLL_INTERVAL)

    @staticmethod
    async def _raise_for_status(resp: aiohttp.ClientResponse) -> None:
        if resp.status < 400:
            return
        if resp.status == 429:
            retry_after: Optional[float] = None
            try:
                retry_after = min(float(resp.headers.get("X-Rate-Limit-Reset-After", 0)), 10.0) or None
            except (ValueError, TypeError):
                pass
            raise RateLimitError("urlscan", retry_after)
        body = ""
        try:
            body = (await resp.text())[:500]
        except Exception:  # noqa: BLE001
            pass
        raise ProviderError("urlscan", resp.status, body)
