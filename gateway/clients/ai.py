from __future__ import annotations
import uuid
from typing import Optional

from ..spec import CallSpec, QuotaPlan
from ..executor import GatewayExecutor


class AIClient:
    """High-level AI client.

    Usage:
        vectors = await gw.ai.embed(["hello", "world"])
        result  = await gw.ai.chat(
            system=..., user=..., model="gpt-4.1-nano",
            quota=[QuotaTarget.guild(guild_id, "ban_reason")],
            call_type="ban_reason",
            metadata={"guild_id": guild_id, "user_id": user_id},
        )
    """

    def __init__(self, executor: GatewayExecutor):
        self._executor = executor

    async def embed(
        self,
        texts: list[str],
        *,
        model: str = "text-embedding-3-small",
        call_type: str = "embed",
        correlation_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> list[list[float]]:
        """Embed a list of texts. Not quota-gated but fully resilient."""
        spec = CallSpec(
            provider="openai",
            operation="embed",
            model=model,
            payload={"texts": texts},
            quota=[],
            call_type=call_type,
            correlation_id=correlation_id or str(uuid.uuid4()),
            metadata=metadata or {},
        )
        return await self._executor.execute(spec)

    async def chat(
        self,
        *,
        system: str,
        user: str,
        model: str = "gpt-4.1-nano",
        json_mode: bool = False,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        quota: QuotaPlan,
        call_type: str,
        correlation_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> str | dict:
        """Chat completion with optional JSON mode. Always declaratively quota-gated."""
        payload: dict = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "json_mode": json_mode,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        spec = CallSpec(
            provider="openai",
            operation="chat",
            model=model,
            payload=payload,
            quota=quota,
            call_type=call_type,
            correlation_id=correlation_id or str(uuid.uuid4()),
            metadata=metadata or {},
        )
        return await self._executor.execute(spec)
