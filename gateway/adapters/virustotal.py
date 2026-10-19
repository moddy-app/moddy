"""
VirusTotal adapter — URL reputation for ``/link-check``.

Three operations on the v3 API, each one HTTP request (so the gateway's
provider-account rate limit of 4 requests/minute is accounted exactly):

* ``url_report``  → ``GET /urls/{id}``: the existing report, if VirusTotal has
  already analysed this URL (``{"found": False}`` on 404);
* ``url_submit``  → ``POST /urls``: ask for a fresh analysis, returns its id;
* ``analysis``    → ``GET /analyses/{id}``: status + engine statistics.

The URL being checked travels on ``CallSpec.binary`` (UTF-8 bytes) rather than
in ``payload``: the payload is copied verbatim to the staff webhook and the
``api_calls`` table, and a link may carry a token (reset links, magic links).
The log only gets the host.

Authentication is the ``x-apikey`` header; the key is never echoed in errors.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from typing import Optional

import aiohttp

from .base import BaseAdapter, AdapterResult
from ..spec import CallSpec
from ..errors import ConfigurationError, ProviderError, RateLimitError
from ..ratelimit import UNIT_REQUESTS

logger = logging.getLogger("moddy.gateway.virustotal")

_BASE = "https://www.virustotal.com/api/v3"
GUI_URL = "https://www.virustotal.com/gui/url/{id}"

OPERATIONS = ("url_report", "url_submit", "analysis")
#: One budget for the three operations: VirusTotal limits the API key, not the endpoint.
MODEL = "public_api"


def url_id(url: str) -> str:
    """VirusTotal's URL identifier: unpadded URL-safe base64 of the URL."""
    return base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")


def gui_url(url: str) -> str:
    """Link to the human report of ``url`` (the GUI keys URLs by SHA-256)."""
    return GUI_URL.format(id=hashlib.sha256(url.encode("utf-8")).hexdigest())


def parse_stats(stats: Optional[dict]) -> dict:
    stats = stats or {}
    return {key: int(stats.get(key, 0) or 0)
            for key in ("malicious", "suspicious", "harmless", "undetected", "timeout")}


def parse_report(payload: dict) -> dict:
    """``{found, id, stats, analysis_date}`` from ``GET /urls/{id}``."""
    data = (payload or {}).get("data") or {}
    attrs = data.get("attributes") or {}
    return {
        "found": True,
        "id": data.get("id"),
        "stats": parse_stats(attrs.get("last_analysis_stats")),
        "analysis_date": attrs.get("last_analysis_date"),
    }


def parse_analysis(payload: dict) -> dict:
    """``{status, stats}`` from ``GET /analyses/{id}`` (status: queued | in-progress | completed)."""
    attrs = ((payload or {}).get("data") or {}).get("attributes") or {}
    return {"status": attrs.get("status") or "queued", "stats": parse_stats(attrs.get("stats"))}


class VirusTotalAdapter(BaseAdapter):
    provider = "virustotal"

    def __init__(self, api_key: Optional[str]):
        if not api_key:
            raise ConfigurationError("VIRUSTOTAL_API_KEY is required for VirusTotalAdapter")
        self._api_key = api_key
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(headers={
            "x-apikey": self._api_key, "accept": "application/json"})
        logger.info("VirusTotal adapter ready")

    async def stop(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def execute(self, spec: CallSpec) -> AdapterResult:
        if spec.operation not in OPERATIONS:
            raise ConfigurationError(f"Unsupported VirusTotal operation: {spec.operation!r}")

        if spec.operation == "analysis":
            analysis_id = str((spec.payload or {}).get("analysis_id") or "")
            if not analysis_id:
                raise ConfigurationError("analysis requires payload.analysis_id")
            data = await self._request("GET", f"{_BASE}/analyses/{analysis_id}")
            return AdapterResult(data=parse_analysis(data), rate_cost={UNIT_REQUESTS: 1})

        if not spec.binary:
            raise ConfigurationError(f"{spec.operation} requires the URL on CallSpec.binary")
        url = spec.binary.decode("utf-8")

        if spec.operation == "url_report":
            data = await self._request("GET", f"{_BASE}/urls/{url_id(url)}", allow_404=True)
            result = {"found": False} if data is None else parse_report(data)
            return AdapterResult(data=result, rate_cost={UNIT_REQUESTS: 1})

        data = await self._request("POST", f"{_BASE}/urls", form={"url": url})
        analysis_id = ((data or {}).get("data") or {}).get("id")
        if not analysis_id:
            raise ProviderError("virustotal", 502, "submission returned no analysis id")
        return AdapterResult(data={"analysis_id": analysis_id}, rate_cost={UNIT_REQUESTS: 1})

    async def _request(self, method: str, url: str, *, form: Optional[dict] = None,
                       allow_404: bool = False) -> Optional[dict]:
        async with self._session.request(method, url, data=form) as resp:
            if resp.status == 404 and allow_404:
                return None
            await self._raise_for_status(resp)
            return await resp.json(content_type=None)

    @staticmethod
    async def _raise_for_status(resp: aiohttp.ClientResponse) -> None:
        if resp.status < 400:
            return
        body = ""
        try:
            body = (await resp.text())[:500]
        except Exception:  # noqa: BLE001
            pass
        if resp.status == 429:
            # The daily/monthly allowance is not worth retrying; the per-minute one is.
            if "QuotaExceededError" in body:
                raise ProviderError("virustotal", 403, "quota exceeded")
            retry_after: Optional[float] = None
            try:
                retry_after = min(float(resp.headers.get("Retry-After", 0)), 10.0) or None
            except (ValueError, TypeError):
                pass
            raise RateLimitError("virustotal", retry_after)
        raise ProviderError("virustotal", resp.status, body)
