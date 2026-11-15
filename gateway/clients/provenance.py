"""
Content provenance client (``bot.gateway.provenance``) — OpenAI's
``POST /v1/content_provenance_checks``: is this image carrying OpenAI's C2PA
manifest or its SynthID watermark?

Usage:
    data = await gw.provenance.check(
        image_bytes, mime="image/png", filename="image.png",
        automatic=False, call_type="ai_detector_command",
        metadata={"guild_id": guild_id, "user_id": user_id},
    )
    # -> the raw API JSON ({"results": [...]}); interpret it with
    #    aidetector.provenance.parse_response.

``automatic`` only picks the rate-limit accounting key (the module's own
budget vs the on-demand one, see gateway/config.py); the adapter calls the
same endpoint either way. The bytes ride on ``CallSpec.binary`` so they never
reach the staff webhook log.
"""

from __future__ import annotations

import uuid
from typing import Optional

from ..executor import GatewayExecutor
from ..ratelimit import UNIT_REQUESTS
from ..spec import CallSpec, QuotaPlan

OPERATION = "content_provenance"
#: Rate-limit accounting keys (``CallSpec.model``).
MODEL_ON_DEMAND = "content_provenance"
MODEL_AUTOMATIC = "content_provenance_auto"


class ProvenanceClient:
    def __init__(self, executor: GatewayExecutor):
        self._executor = executor

    async def check(
        self,
        image: bytes,
        *,
        mime: Optional[str] = None,
        filename: Optional[str] = None,
        automatic: bool = False,
        quota: Optional[QuotaPlan] = None,
        call_type: str = "ai_detector",
        correlation_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        spec = CallSpec(
            provider="openai",
            operation=OPERATION,
            model=MODEL_AUTOMATIC if automatic else MODEL_ON_DEMAND,
            payload={"mime": mime, "filename": filename, "size_bytes": len(image)},
            quota=quota or [],
            call_type=call_type,
            correlation_id=correlation_id or str(uuid.uuid4()),
            metadata=metadata or {},
            rate_cost={UNIT_REQUESTS: 1},
            binary=image,
        )
        return await self._executor.execute(spec) or {}
