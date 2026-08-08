"""
Groq adapter — speech-to-text (Whisper) through Groq's OpenAI-compatible API.

Only the ``transcribe`` operation is implemented today; the endpoint layout
matches OpenAI's, so adding chat/embeddings later is a matter of adding the
matching ``_chat`` / ``_embed`` branch.

The audio bytes travel on ``CallSpec.binary`` rather than in ``CallSpec.payload``:
the payload is forwarded verbatim to the staff webhook logger, which renders it
as JSON, and a few megabytes of Opus have no business being there.
"""

from __future__ import annotations

import logging
from typing import Optional

import aiohttp

from .base import BaseAdapter, AdapterResult
from ..spec import CallSpec
from ..errors import ConfigurationError, ProviderError, RateLimitError
from ..ratelimit import UNIT_AUDIO_SECONDS, UNIT_REQUESTS

logger = logging.getLogger("moddy.gateway.groq")

_BASE_URL = "https://api.groq.com/openai/v1"

# Whisper accepts a short "prompt" biasing the vocabulary, and a temperature.
# 0 keeps the output as literal as possible — a transcription is not creative
# writing.
_TEMPERATURE = 0.0


class GroqAdapter(BaseAdapter):
    provider = "groq"

    def __init__(self, api_key: Optional[str]):
        if not api_key:
            raise ConfigurationError("GROQ_API_KEY is required for GroqAdapter")
        self._api_key = api_key
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self) -> None:
        # No global Content-Type here: the transcription endpoint is multipart
        # and aiohttp must be free to set its own boundary.
        self._session = aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {self._api_key}"}
        )
        logger.info("Groq adapter ready")

    async def stop(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def execute(self, spec: CallSpec) -> AdapterResult:
        if spec.operation == "transcribe":
            return await self._transcribe(spec)
        raise ConfigurationError(f"Unsupported Groq operation: {spec.operation!r}")

    async def _transcribe(self, spec: CallSpec) -> AdapterResult:
        if not spec.binary:
            raise ConfigurationError("transcribe requires audio bytes on CallSpec.binary")

        model = spec.model or "whisper-large-v3-turbo"
        payload = spec.payload or {}

        form = aiohttp.FormData()
        form.add_field(
            "file",
            spec.binary,
            filename=payload.get("filename") or "audio.ogg",
            content_type=payload.get("content_type") or "application/octet-stream",
        )
        form.add_field("model", model)
        # verbose_json is what makes the exact duration and the detected
        # language available — both are shown on the card and the duration is
        # what the audio-seconds rate limit is reconciled against.
        form.add_field("response_format", "verbose_json")
        form.add_field("temperature", str(_TEMPERATURE))
        if payload.get("language"):
            form.add_field("language", payload["language"])
        if payload.get("prompt"):
            form.add_field("prompt", payload["prompt"])

        async with self._session.post(
            f"{_BASE_URL}/audio/transcriptions", data=form
        ) as resp:
            await self._raise_for_status(resp)
            data = await resp.json()

        duration = float(data.get("duration") or 0.0)
        result = {
            "text": (data.get("text") or "").strip(),
            "language": data.get("language") or None,
            "duration": duration,
        }
        return AdapterResult(
            data=result,
            rate_cost={UNIT_REQUESTS: 1, UNIT_AUDIO_SECONDS: duration},
        )

    async def _raise_for_status(self, resp: aiohttp.ClientResponse) -> None:
        if resp.status == 429:
            retry_after: Optional[float] = None
            try:
                retry_after = float(resp.headers.get("Retry-After", 0)) or None
            except (ValueError, TypeError):
                pass
            raise RateLimitError("groq", retry_after)
        if resp.status >= 400:
            try:
                body = await resp.text()
            except Exception:
                body = ""
            raise ProviderError("groq", resp.status, body[:500])
