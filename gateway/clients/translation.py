from __future__ import annotations
import uuid
from typing import Optional

from ..spec import CallSpec, QuotaPlan
from ..executor import GatewayExecutor


class TranslationClient:
    """High-level translation client.

    Usage:
        result = await gw.translation.translate(
            text, target_lang="EN-US",
            quota=[QuotaTarget.user(user_id, "translation")],
            call_type="translation",
            metadata={"user_id": user_id},
        )
        # result = {"text": "...", "detected_source_language": "FR"}
    """

    def __init__(self, executor: GatewayExecutor):
        self._executor = executor

    async def translate(
        self,
        text: str,
        target_lang: str,
        *,
        source_lang: Optional[str] = None,
        quota: QuotaPlan,
        call_type: str = "translation",
        correlation_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        """Translate text. Returns ``{"text": ..., "detected_source_language": ...}``."""
        payload: dict = {"text": text, "target_lang": target_lang}
        if source_lang:
            payload["source_lang"] = source_lang

        spec = CallSpec(
            provider="deepl",
            operation="translate",
            model=None,
            payload=payload,
            quota=quota,
            call_type=call_type,
            correlation_id=correlation_id or str(uuid.uuid4()),
            metadata=metadata or {},
        )
        return await self._executor.execute(spec)
