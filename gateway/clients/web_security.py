"""
Link reputation clients: ``gw.virustotal`` and ``gw.urlscan`` (``/link-check``).

The checked URL travels on ``CallSpec.binary`` and never in ``payload`` or
``metadata``: both are copied to the staff webhook and the ``api_calls`` table,
and a link can carry a secret (password-reset or magic-link tokens). Only the
host is logged, for debugging.
"""

from __future__ import annotations

import uuid
from typing import Optional
from urllib.parse import urlsplit

from ..adapters import urlscan as urlscan_adapter
from ..adapters import virustotal as vt_adapter
from ..executor import GatewayExecutor
from ..ratelimit import UNIT_REQUESTS
from ..spec import CallSpec, QuotaPlan


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "")[:100]


class VirusTotalClient:
    """``await gw.virustotal.url_report(url)`` -> ``{"found": bool, "stats": {...}}``."""

    def __init__(self, executor: GatewayExecutor):
        self._executor = executor

    async def _call(self, operation: str, *, url: Optional[str], payload: dict,
                    quota: Optional[QuotaPlan], call_type: str,
                    correlation_id: Optional[str], metadata: Optional[dict]) -> dict:
        meta = dict(metadata or {})
        if url:
            meta["host"] = _host(url)
        spec = CallSpec(
            provider="virustotal", operation=operation, model=vt_adapter.MODEL,
            payload=payload, quota=quota or [], call_type=call_type,
            correlation_id=correlation_id or str(uuid.uuid4()), metadata=meta,
            rate_cost={UNIT_REQUESTS: 1}, binary=url.encode("utf-8") if url else None)
        return await self._executor.execute(spec) or {}

    async def url_report(self, url: str, *, quota: Optional[QuotaPlan] = None,
                         call_type: str = "link_check", correlation_id: Optional[str] = None,
                         metadata: Optional[dict] = None) -> dict:
        return await self._call("url_report", url=url, payload={}, quota=quota,
                                call_type=call_type, correlation_id=correlation_id,
                                metadata=metadata)

    async def url_submit(self, url: str, *, quota: Optional[QuotaPlan] = None,
                         call_type: str = "link_check", correlation_id: Optional[str] = None,
                         metadata: Optional[dict] = None) -> dict:
        return await self._call("url_submit", url=url, payload={}, quota=quota,
                                call_type=call_type, correlation_id=correlation_id,
                                metadata=metadata)

    async def analysis(self, analysis_id: str, *, call_type: str = "link_check",
                       correlation_id: Optional[str] = None,
                       metadata: Optional[dict] = None) -> dict:
        return await self._call("analysis", url=None, payload={"analysis_id": analysis_id},
                                quota=None, call_type=call_type,
                                correlation_id=correlation_id, metadata=metadata)


class UrlscanClient:
    """``await gw.urlscan.scan(url)`` then ``await gw.urlscan.result(uuid)``."""

    def __init__(self, executor: GatewayExecutor):
        self._executor = executor

    async def scan(self, url: str, *, quota: Optional[QuotaPlan] = None,
                   call_type: str = "link_check", correlation_id: Optional[str] = None,
                   metadata: Optional[dict] = None) -> dict:
        meta = dict(metadata or {})
        meta["host"] = _host(url)
        spec = CallSpec(
            provider="urlscan", operation="scan_submit", model=urlscan_adapter.MODEL_SCAN,
            payload={}, quota=quota or [], call_type=call_type,
            correlation_id=correlation_id or str(uuid.uuid4()), metadata=meta,
            rate_cost={UNIT_REQUESTS: 1}, binary=url.encode("utf-8"))
        return await self._executor.execute(spec) or {}

    async def result(self, scan_uuid: str, *, max_wait: float = urlscan_adapter.DEFAULT_MAX_WAIT,
                     call_type: str = "link_check", correlation_id: Optional[str] = None,
                     metadata: Optional[dict] = None) -> dict:
        spec = CallSpec(
            provider="urlscan", operation="scan_result", model=urlscan_adapter.MODEL_RESULT,
            payload={"uuid": scan_uuid, "max_wait": max_wait}, quota=[], call_type=call_type,
            correlation_id=correlation_id or str(uuid.uuid4()), metadata=dict(metadata or {}),
            rate_cost={UNIT_REQUESTS: 1})
        return await self._executor.execute(spec) or {}
