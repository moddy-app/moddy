"""
Speech-to-text service — the single entry point for transcribing a Discord
message's audio.

Both consumers (the ``Transcribe`` context menu and the button the Voice
Transcription module posts under voice messages) go through
``TranscriptionService.transcribe_message``: attachment discovery, guard rails,
the gateway call and error typing all live here, so the two UIs stay thin and
behave identically.

Every provider call goes through ``bot.gateway.transcription`` — see
docs/API_GATEWAY.md. Nothing in this file talks to Groq directly.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import discord

from gateway import (
    APIUnavailableError,
    GatewayError,
    GatewayTimeoutError,
    ModelRateLimitError,
    QuotaExceededError,
    QuotaTarget,
    RateLimitError,
)

logger = logging.getLogger("moddy.services.transcription")

# --------------------------------------------------------------------------- #
# Limits & constants
# --------------------------------------------------------------------------- #

CALL_TYPE = "voice_transcription"

# Groq caps uploads at 25 MB on the free tier. Refuse earlier, with a clear
# message, rather than burning a request on a guaranteed 413.
MAX_FILE_BYTES = 25 * 1024 * 1024
# Longest audio we accept. Keeps one message from eating a large share of the
# hourly audio-seconds budget (see gateway/config.py).
MAX_DURATION_SECONDS = 30 * 60

# Per-user throttle. Deliberately DISABLED (0) for now — per-user and per-guild
# caps are meant to be introduced later through `quota_overrides`
# (docs/API_GATEWAY.md), which needs no code change. The machinery is kept here
# so a hard, instant cap can be switched on without redesigning the flow.
MAX_USES_PER_MINUTE_PER_USER = 0

# Fallback bitrate used to estimate a duration when Discord does not ship one
# (a plain audio attachment rather than a voice message). ~128 kbps; only ever
# used for rate-limit accounting, and reconciled with the real duration once the
# provider answers.
_ESTIMATED_BYTES_PER_SECOND = 16_000

AUDIO_EXTENSIONS = (
    ".ogg", ".oga", ".opus", ".mp3", ".m4a", ".mp4a", ".wav",
    ".webm", ".flac", ".aac", ".mpga", ".mpeg",
)


class ErrorCode:
    """Failure reasons, mapped 1:1 to i18n keys by the UI layer."""

    NO_AUDIO = "no_audio"
    TOO_LARGE = "too_large"
    TOO_LONG = "too_long"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"
    QUOTA = "quota"
    IN_PROGRESS = "in_progress"
    DOWNLOAD_FAILED = "download_failed"
    EMPTY = "empty"
    FAILED = "failed"


class TranscriptionError(Exception):
    """Typed failure — ``code`` is an :class:`ErrorCode` value.

    ``params`` carries whatever the message needs to be helpful (the size cap
    that was exceeded, how many seconds until a limit resets, …) and is passed
    straight to the i18n formatter.
    """

    def __init__(self, code: str, **params):
        self.code = code
        self.params: Dict[str, object] = params
        super().__init__(code)


@dataclass
class TranscriptionResult:
    #: What the card shows inline — cut short when the audio was long.
    text: str
    #: The complete transcription, always. Shipped as a .txt when `truncated`.
    full_text: str = ""
    language: Optional[str] = None
    duration: float = 0.0
    truncated: bool = False
    attachment_name: str = ""


# --------------------------------------------------------------------------- #
# Attachment helpers (pure — no bot state, trivially testable)
# --------------------------------------------------------------------------- #

def is_voice_message(message: discord.Message) -> bool:
    """True for Discord's native voice messages (the waveform kind)."""
    if getattr(message.flags, "voice", False):
        return True
    # Fallback for gateway payloads that predate the flag: a voice message is a
    # single attachment carrying a waveform.
    return (
        len(message.attachments) == 1
        and getattr(message.attachments[0], "waveform", None) is not None
    )


def find_audio_attachment(message: discord.Message) -> Optional[discord.Attachment]:
    """First attachment that looks like audio, voice message or not."""
    for attachment in message.attachments:
        content_type = (attachment.content_type or "").lower()
        if content_type.startswith("audio/") or content_type.startswith("video/ogg"):
            return attachment
        if attachment.filename.lower().endswith(AUDIO_EXTENSIONS):
            return attachment
    return None


def attachment_duration(attachment: discord.Attachment) -> float:
    """Audio length in seconds — exact for voice messages, estimated otherwise."""
    duration = getattr(attachment, "duration", None)
    if duration:
        return float(duration)
    return round(attachment.size / _ESTIMATED_BYTES_PER_SECOND, 1)


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #

class TranscriptionService:
    """Lives on ``bot.transcription``. Stateless apart from anti-duplicate guards."""

    def __init__(self, bot):
        self.bot = bot
        # message_id currently being transcribed — stops two people clicking the
        # same button from paying for the same audio twice.
        self._in_flight: set = set()
        # user_id -> recent call timestamps (only used when the throttle is on)
        self._recent_uses: Dict[int, List[float]] = {}

    # ----------------------------------------------------------------- #
    # Availability
    # ----------------------------------------------------------------- #

    def available(self) -> bool:
        gateway = getattr(self.bot, "gateway", None)
        return bool(gateway and gateway.available and gateway.groq_available())

    # ----------------------------------------------------------------- #
    # Main entry point
    # ----------------------------------------------------------------- #

    async def transcribe_message(
        self,
        message: discord.Message,
        *,
        requester_id: int,
        language: Optional[str] = None,
        max_characters: int = 3500,
    ) -> TranscriptionResult:
        """Transcribe a message's audio attachment.

        Raises:
            TranscriptionError: for every expected failure (no audio, file too
                large, provider unavailable, rate limited, …). Unexpected
                exceptions are converted to ``ErrorCode.FAILED`` rather than
                leaking provider internals to the UI.
        """
        attachment = self.preflight(message, requester_id=requester_id)
        duration_hint = attachment_duration(attachment)

        if message.id in self._in_flight:
            raise TranscriptionError(ErrorCode.IN_PROGRESS)
        self._in_flight.add(message.id)
        try:
            audio = await self._download(attachment)
            transcription = await self._call_gateway(
                audio,
                attachment=attachment,
                message=message,
                requester_id=requester_id,
                duration_hint=duration_hint,
                language=language,
            )
        finally:
            self._in_flight.discard(message.id)

        full_text = (transcription.text or "").strip()
        if not full_text:
            raise TranscriptionError(ErrorCode.EMPTY)

        # A long recording does not lose a word: the card shows what fits and
        # the complete text rides along as a .txt attachment (see
        # utils/transcription_views.py::build_transcription_message).
        truncated = len(full_text) > max_characters
        text = full_text[:max_characters].rstrip() + "…" if truncated else full_text

        self._record_use(requester_id)
        return TranscriptionResult(
            text=text,
            full_text=full_text,
            language=transcription.language,
            duration=transcription.duration or duration_hint,
            truncated=truncated,
            attachment_name=attachment.filename,
        )

    # ----------------------------------------------------------------- #
    # Steps
    # ----------------------------------------------------------------- #

    def preflight(self, message: discord.Message, *, requester_id: int) -> discord.Attachment:
        """Validate everything that can be checked without any network call."""
        if not self.available():
            raise TranscriptionError(ErrorCode.UNAVAILABLE)

        attachment = find_audio_attachment(message)
        if attachment is None:
            raise TranscriptionError(ErrorCode.NO_AUDIO)

        if attachment.size > MAX_FILE_BYTES:
            raise TranscriptionError(
                ErrorCode.TOO_LARGE, size=round(MAX_FILE_BYTES / (1024 * 1024)),
            )

        duration = attachment_duration(attachment)
        if duration > MAX_DURATION_SECONDS:
            raise TranscriptionError(
                ErrorCode.TOO_LONG, minutes=MAX_DURATION_SECONDS // 60,
            )

        wait = self._throttle_wait(requester_id)
        if wait:
            raise TranscriptionError(ErrorCode.RATE_LIMITED, seconds=wait)

        return attachment

    async def _download(self, attachment: discord.Attachment) -> bytes:
        try:
            return await attachment.read()
        except (discord.HTTPException, discord.NotFound) as exc:
            logger.warning("Voice transcription: attachment download failed: %s", exc)
            raise TranscriptionError(ErrorCode.DOWNLOAD_FAILED) from exc

    async def _call_gateway(
        self,
        audio: bytes,
        *,
        attachment: discord.Attachment,
        message: discord.Message,
        requester_id: int,
        duration_hint: float,
        language: Optional[str],
    ):
        guild_id = message.guild.id if message.guild else None
        quota = [QuotaTarget.user(requester_id, CALL_TYPE)]
        if guild_id:
            quota.append(QuotaTarget.guild(guild_id, CALL_TYPE))

        try:
            return await self.bot.gateway.transcription.transcribe(
                audio,
                filename=attachment.filename or "voice-message.ogg",
                content_type=attachment.content_type,
                language=language,
                duration_hint=duration_hint,
                quota=quota,
                call_type=CALL_TYPE,
                metadata={
                    "guild_id": guild_id,
                    "user_id": requester_id,
                    "message_id": message.id,
                },
            )
        except ModelRateLimitError as exc:
            raise TranscriptionError(
                ErrorCode.RATE_LIMITED,
                seconds=int(exc.retry_after or 60),
            ) from exc
        except RateLimitError as exc:
            raise TranscriptionError(
                ErrorCode.RATE_LIMITED,
                seconds=int(exc.retry_after or 30),
            ) from exc
        except QuotaExceededError as exc:
            raise TranscriptionError(ErrorCode.QUOTA) from exc
        except (APIUnavailableError, GatewayTimeoutError, asyncio.TimeoutError) as exc:
            raise TranscriptionError(ErrorCode.UNAVAILABLE) from exc
        except GatewayError as exc:
            logger.warning("Voice transcription failed: %s", exc)
            raise TranscriptionError(ErrorCode.FAILED) from exc
        except Exception as exc:  # noqa: BLE001 — never leak provider internals
            logger.error("Voice transcription crashed: %s", exc, exc_info=True)
            raise TranscriptionError(ErrorCode.FAILED) from exc

    # ----------------------------------------------------------------- #
    # Per-user throttle (off by default, see MAX_USES_PER_MINUTE_PER_USER)
    # ----------------------------------------------------------------- #

    def _throttle_wait(self, user_id: int) -> int:
        """Seconds the user must wait, or 0 when they may proceed."""
        if MAX_USES_PER_MINUTE_PER_USER <= 0:
            return 0

        now = time.monotonic()
        cutoff = now - 60
        stamps = [ts for ts in self._recent_uses.get(user_id, []) if ts > cutoff]
        self._recent_uses[user_id] = stamps

        # Keep the dict from growing: drop users idle for over two minutes.
        for uid in [u for u, s in self._recent_uses.items()
                    if u != user_id and (not s or max(s) < now - 120)]:
            del self._recent_uses[uid]

        if len(stamps) >= MAX_USES_PER_MINUTE_PER_USER:
            return max(int(60 - (now - min(stamps))), 1)
        return 0

    def _record_use(self, user_id: int) -> None:
        if MAX_USES_PER_MINUTE_PER_USER <= 0:
            return
        self._recent_uses.setdefault(user_id, []).append(time.monotonic())
