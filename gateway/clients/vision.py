from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional

from ..spec import CallSpec, QuotaPlan
from ..executor import GatewayExecutor
from ..ratelimit import UNIT_REQUESTS

# Google Vision has no "model": the rate-limit rules (monthly free tier, one per
# billed feature) are keyed on the operation name instead.
MODEL_SAFE_SEARCH = "safe_search"
MODEL_DOCUMENT_TEXT = "document_text"


@dataclass
class SafeSearchResult:
    """Google likelihood names per SafeSearch axis (``VERY_UNLIKELY`` … ``VERY_LIKELY``)."""

    adult: str = "UNKNOWN"
    racy: str = "UNKNOWN"
    violence: str = "UNKNOWN"
    medical: str = "UNKNOWN"
    spoof: str = "UNKNOWN"

    def as_dict(self) -> dict:
        return {
            "adult": self.adult, "racy": self.racy, "violence": self.violence,
            "medical": self.medical, "spoof": self.spoof,
        }


@dataclass
class OcrResult:
    """Text extracted from one image."""

    text: str = ""
    language: Optional[str] = None
    extra: dict = field(default_factory=dict)


class VisionClient:
    """High-level image-analysis client (Google Cloud Vision).

    Usage:
        verdict = await gw.vision.safe_search(
            image_bytes, mime="image/png",
            quota=[QuotaTarget.guild(guild_id, "automod_safesearch")],
            call_type="automod_safesearch",
            metadata={"guild_id": guild_id},
        )
        verdict.adult  # "VERY_LIKELY"

        ocr = await gw.vision.document_text(image_bytes, mime="image/png", ...)
        ocr.text
    """

    def __init__(self, executor: GatewayExecutor):
        self._executor = executor

    async def _run(self, operation: str, image: bytes, *, mime: Optional[str],
                   quota: Optional[QuotaPlan], call_type: str,
                   correlation_id: Optional[str], metadata: Optional[dict],
                   extra_payload: Optional[dict] = None):
        payload = {
            # Metadata only — the bytes themselves ride on `binary`.
            "mime": mime,
            "size_bytes": len(image),
        }
        if extra_payload:
            payload.update(extra_payload)
        spec = CallSpec(
            provider="google_vision",
            operation=operation,
            model=operation,
            payload=payload,
            quota=quota or [],
            call_type=call_type,
            correlation_id=correlation_id or str(uuid.uuid4()),
            metadata=metadata or {},
            rate_cost={UNIT_REQUESTS: 1},
            binary=image,
        )
        return await self._executor.execute(spec)

    async def safe_search(
        self,
        image: bytes,
        *,
        mime: Optional[str] = None,
        quota: Optional[QuotaPlan] = None,
        call_type: str = "automod_safesearch",
        correlation_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> SafeSearchResult:
        data = await self._run(
            MODEL_SAFE_SEARCH, image, mime=mime, quota=quota, call_type=call_type,
            correlation_id=correlation_id, metadata=metadata,
        ) or {}
        return SafeSearchResult(**{k: data.get(k, "UNKNOWN") for k in
                                   ("adult", "racy", "violence", "medical", "spoof")})

    async def document_text(
        self,
        image: bytes,
        *,
        mime: Optional[str] = None,
        language_hints: Optional[list] = None,
        quota: Optional[QuotaPlan] = None,
        call_type: str = "ocr_command",
        correlation_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> OcrResult:
        data = await self._run(
            MODEL_DOCUMENT_TEXT, image, mime=mime, quota=quota, call_type=call_type,
            correlation_id=correlation_id, metadata=metadata,
            extra_payload={"language_hints": language_hints} if language_hints else None,
        ) or {}
        return OcrResult(text=data.get("text", "") or "", language=data.get("language"))
