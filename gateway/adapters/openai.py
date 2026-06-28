from __future__ import annotations
import json
import logging
from typing import Optional

import aiohttp

from .base import BaseAdapter, AdapterResult
from ..spec import CallSpec
from ..errors import ConfigurationError, ProviderError, RateLimitError

logger = logging.getLogger("moddy.gateway.openai")

_BASE_URL = "https://api.openai.com/v1"


class OpenAIAdapter(BaseAdapter):
    provider = "openai"

    def __init__(self, api_key: Optional[str]):
        if not api_key:
            raise ConfigurationError("OPENAI_API_KEY is required for OpenAIAdapter")
        self._api_key = api_key
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }
        )
        logger.info("OpenAI adapter ready")

    async def stop(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def execute(self, spec: CallSpec) -> AdapterResult:
        if spec.operation == "embed":
            return await self._embed(spec)
        if spec.operation == "chat":
            return await self._chat(spec)
        raise ConfigurationError(f"Unsupported OpenAI operation: {spec.operation!r}")

    async def _embed(self, spec: CallSpec) -> AdapterResult:
        model = spec.model or "text-embedding-3-small"
        texts = spec.payload.get("texts", [])
        body = {"model": model, "input": texts}

        async with self._session.post(f"{_BASE_URL}/embeddings", json=body) as resp:
            await self._raise_for_status(resp)
            data = await resp.json()

        # Preserve order
        embeddings = [
            item["embedding"]
            for item in sorted(data["data"], key=lambda x: x["index"])
        ]
        usage = data.get("usage", {})
        return AdapterResult(
            data=embeddings,
            tokens_prompt=usage.get("prompt_tokens", 0),
            tokens_total=usage.get("total_tokens", 0),
        )

    async def _chat(self, spec: CallSpec) -> AdapterResult:
        model = spec.model or "gpt-4.1-nano"
        payload = dict(spec.payload)
        json_mode = payload.pop("json_mode", False)
        messages = payload.pop("messages", [])

        body: dict = {"model": model, "messages": messages, **payload}
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        async with self._session.post(f"{_BASE_URL}/chat/completions", json=body) as resp:
            await self._raise_for_status(resp)
            data = await resp.json()

        content: str = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage", {})

        parsed: str | dict = content
        if json_mode:
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                pass

        return AdapterResult(
            data=parsed,
            tokens_prompt=usage.get("prompt_tokens", 0),
            tokens_completion=usage.get("completion_tokens", 0),
            tokens_total=usage.get("total_tokens", 0),
        )

    async def _raise_for_status(self, resp: aiohttp.ClientResponse) -> None:
        if resp.status == 429:
            retry_after: Optional[float] = None
            try:
                retry_after = float(resp.headers.get("Retry-After", 0)) or None
            except (ValueError, TypeError):
                pass
            raise RateLimitError("openai", retry_after)
        if resp.status >= 400:
            try:
                body = await resp.text()
            except Exception:
                body = ""
            raise ProviderError("openai", resp.status, body[:500])
