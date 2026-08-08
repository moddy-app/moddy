from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from ..spec import CallSpec, QuotaPlan
from ..executor import GatewayExecutor
from ..ratelimit import UNIT_AUDIO_SECONDS, UNIT_REQUESTS

# Groq's Whisper turbo model: the fastest of the family, and the one whose
# limits are configured in GatewayConfig.model_rate_limits.
DEFAULT_MODEL = "whisper-large-v3-turbo"


@dataclass
class Transcription:
    """Result of one speech-to-text call."""

    text: str
    language: Optional[str] = None   # as reported by the model ("french", "en", …)
    duration: float = 0.0            # seconds of audio, as measured by the model


class TranscriptionClient:
    """High-level speech-to-text client.

    Usage:
        result = await gw.transcription.transcribe(
            audio_bytes,
            filename="voice-message.ogg",
            content_type="audio/ogg",
            duration_hint=12.4,
            quota=[QuotaTarget.user(user_id, "voice_transcription")],
            call_type="voice_transcription",
            metadata={"guild_id": guild_id, "user_id": user_id},
        )
        result.text
    """

    def __init__(self, executor: GatewayExecutor):
        self._executor = executor

    async def transcribe(
        self,
        audio: bytes,
        *,
        filename: str,
        content_type: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        language: Optional[str] = None,
        prompt: Optional[str] = None,
        duration_hint: float = 0.0,
        quota: Optional[QuotaPlan] = None,
        call_type: str = "voice_transcription",
        correlation_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> Transcription:
        """Transcribe an audio file.

        ``duration_hint`` is the audio length in seconds as known *before* the
        call (Discord ships it with voice messages). It is what the audio-seconds
        rate limit reserves; the exact duration returned by the provider then
        reconciles the reservation. Pass 0 when unknown — only the request-count
        limits apply in that case.
        """
        spec = CallSpec(
            provider="groq",
            operation="transcribe",
            model=model,
            payload={
                "filename": filename,
                "content_type": content_type,
                "language": language,
                "prompt": prompt,
                # Metadata only — the bytes themselves ride on `binary`.
                "size_bytes": len(audio),
                "duration_hint": round(duration_hint, 2),
            },
            quota=quota or [],
            call_type=call_type,
            correlation_id=correlation_id or str(uuid.uuid4()),
            metadata=metadata or {},
            rate_cost={
                UNIT_REQUESTS: 1,
                UNIT_AUDIO_SECONDS: max(duration_hint, 0.0),
            },
            binary=audio,
        )
        data = await self._executor.execute(spec)
        return Transcription(
            text=(data or {}).get("text", ""),
            language=(data or {}).get("language"),
            duration=float((data or {}).get("duration") or duration_hint or 0.0),
        )
