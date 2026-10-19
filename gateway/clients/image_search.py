"""
Reverse image search clients: ``gw.saucenao`` (art / anime / manga sources) and
``gw.tracemoe`` (anime frame -> title, episode, moment).

Each takes the image either as ``image_url`` (the provider fetches it) or as
``image`` bytes (upload fallback). The URL rides in ``metadata`` so it never
reaches the staff webhook log (see the adapters).
"""

from __future__ import annotations

import uuid
from typing import Optional

from ..spec import CallSpec, QuotaPlan
from ..executor import GatewayExecutor
from ..ratelimit import UNIT_REQUESTS

MODEL_SEARCH = "search"


async def _run(executor: GatewayExecutor, provider: str, *, image_url: Optional[str],
               image: Optional[bytes], mime: Optional[str], filename: Optional[str],
               quota: Optional[QuotaPlan], call_type: str, correlation_id: Optional[str],
               metadata: Optional[dict], extra_payload: Optional[dict] = None) -> dict:
    if not image_url and not image:
        raise ValueError("image_url or image bytes required")
    meta = dict(metadata or {})
    if image_url and not image:
        meta["image_url"] = image_url
    payload = {"source": "upload" if image else "url", "mime": mime, "filename": filename}
    if image:
        payload["size_bytes"] = len(image)
    if extra_payload:
        payload.update(extra_payload)
    spec = CallSpec(
        provider=provider,
        operation="search",
        model=MODEL_SEARCH,
        payload=payload,
        quota=quota or [],
        call_type=call_type,
        correlation_id=correlation_id or str(uuid.uuid4()),
        metadata=meta,
        rate_cost={UNIT_REQUESTS: 1},
        binary=image or None,
    )
    return await executor.execute(spec) or {}


class SauceNAOClient:
    """``await gw.saucenao.search(image_url=..., hide=3, quota=[...])`` -> trimmed JSON."""

    def __init__(self, executor: GatewayExecutor):
        self._executor = executor

    async def search(self, *, image_url: Optional[str] = None, image: Optional[bytes] = None,
                     mime: Optional[str] = None, filename: Optional[str] = None,
                     hide: int = 3, numres: int = 8, quota: Optional[QuotaPlan] = None,
                     call_type: str = "sauce", correlation_id: Optional[str] = None,
                     metadata: Optional[dict] = None) -> dict:
        return await _run(self._executor, "saucenao", image_url=image_url, image=image,
                          mime=mime, filename=filename, quota=quota, call_type=call_type,
                          correlation_id=correlation_id, metadata=metadata,
                          extra_payload={"hide": hide, "numres": numres})


class TraceMoeClient:
    """``await gw.tracemoe.search(image_url=...)`` -> ``{"result": [...]}``."""

    def __init__(self, executor: GatewayExecutor):
        self._executor = executor

    async def search(self, *, image_url: Optional[str] = None, image: Optional[bytes] = None,
                     mime: Optional[str] = None, filename: Optional[str] = None,
                     quota: Optional[QuotaPlan] = None, call_type: str = "sauce",
                     correlation_id: Optional[str] = None,
                     metadata: Optional[dict] = None) -> dict:
        return await _run(self._executor, "tracemoe", image_url=image_url, image=image,
                          mime=mime, filename=filename, quota=quota, call_type=call_type,
                          correlation_id=correlation_id, metadata=metadata)
