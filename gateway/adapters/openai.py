from __future__ import annotations
import base64
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
        if spec.operation == "vision":
            return await self._vision(spec)
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

    async def _vision(self, spec: CallSpec) -> AdapterResult:
        """Chat completion over ONE image (OCR, image understanding).

        The image travels on ``CallSpec.binary`` and is turned into a data URL
        only here, at send time: ``CallSpec.payload`` is what the staff webhook
        logger renders, and megabytes of base64 have no business being there.
        """
        if not spec.binary:
            raise ConfigurationError("vision requires image bytes on CallSpec.binary")
        model = spec.model or "gpt-4.1-nano"
        payload = dict(spec.payload or {})
        mime = payload.pop("mime", None) or "image/png"
        detail = payload.pop("detail", None) or "high"
        system = payload.pop("system", "")
        prompt = payload.pop("prompt", "")
        json_mode = payload.pop("json_mode", False)
        payload.pop("size_bytes", None)

        data_url = f"data:{mime};base64," + base64.b64encode(spec.binary).decode("ascii")
        user_parts: list = []
        if prompt:
            user_parts.append({"type": "text", "text": prompt})
        user_parts.append({"type": "image_url", "image_url": {"url": data_url, "detail": detail}})
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user_parts})

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
            try:
                body = await resp.text()
            except Exception:
                body = ""
            logger.warning("OpenAI 429 on %s: %s", resp.url, body[:500])
            raise RateLimitError("openai", retry_after)
        if resp.status >= 400:
            try:
                body = await resp.text()
            except Exception:
                body = ""
            raise ProviderError("openai", resp.status, body[:500])
