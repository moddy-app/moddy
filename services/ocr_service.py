"""
OCR — extract the text of an image for a person (``/ocr`` and the Transcribe menu).

Distinct from the automod's image OCR on purpose: that one runs silently on
suspicious images and only needs the scam vocabulary to survive (gpt-4.1-nano
vision, a fraction of a cent). This one answers a human who wants the text,
layout included, so it uses the dedicated document OCR — Google Cloud Vision
``DOCUMENT_TEXT_DETECTION`` — through ``bot.gateway.vision``.

Google's free tier is 1000 images a month for all of Moddy; the gateway
enforces it with a calendar-month, fail-closed rule, and each person/server
gets a small daily bucket (``ocr_command`` quota, seeded in ``db/base.py``).
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from typing import Dict, Optional

import discord

logger = logging.getLogger("moddy.services.ocr")

#: Discord lets people post large images; Vision's JSON body caps near 10 MB
#: once base64-encoded, so anything above this is re-encoded first.
MAX_INLINE_BYTES = 7 * 1024 * 1024
MAX_FILE_BYTES = 25 * 1024 * 1024
#: Longest side kept when an image has to be re-encoded (dense text survives).
REENCODE_MAX_SIDE = 3000
#: Characters shown inline on the card; the rest travels as a .txt.
CARD_TEXT_LIMIT = 3500

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".ico")
CALL_TYPE = "ocr_command"


class ErrorCode:
    """Failure reasons, mapped 1:1 to ``ocr.errors.<code>`` i18n keys."""

    NO_IMAGE = "no_image"
    TOO_LARGE = "too_large"
    UNAVAILABLE = "unavailable"
    QUOTA = "quota"
    MONTHLY_CAP = "monthly_cap"
    DOWNLOAD_FAILED = "download_failed"
    UNREADABLE = "unreadable"
    EMPTY = "empty"
    FAILED = "failed"


class OcrError(Exception):
    def __init__(self, code: str, **params):
        self.code = code
        self.params: Dict[str, object] = params
        super().__init__(code)


@dataclass
class OcrResult:
    text: str
    full_text: str = ""
    language: Optional[str] = None
    truncated: bool = False


def is_image(attachment: discord.Attachment) -> bool:
    ctype = (attachment.content_type or "").lower()
    return ctype.startswith("image/") or (attachment.filename or "").lower().endswith(IMAGE_EXTENSIONS)


def find_image_attachment(message: discord.Message) -> Optional[discord.Attachment]:
    for attachment in getattr(message, "attachments", None) or []:
        if is_image(attachment):
            return attachment
    return None


def shrink_for_vision(raw: bytes) -> bytes:
    """Re-encode an oversized image as JPEG (≤ REENCODE_MAX_SIDE). Sync — run
    it in a thread. Raises :class:`OcrError` (``unreadable``) on garbage."""
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(raw))
        if img.size[0] * img.size[1] > 80_000_000:
            raise OcrError(ErrorCode.UNREADABLE)
        img.seek(0)
        img = img.convert("RGB")
        img.thumbnail((REENCODE_MAX_SIDE, REENCODE_MAX_SIDE), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return buf.getvalue()
    except OcrError:
        raise
    except Exception as exc:  # noqa: BLE001 — Pillow raises many types
        raise OcrError(ErrorCode.UNREADABLE) from exc


def split_for_card(text: str, limit: int = CARD_TEXT_LIMIT) -> OcrResult:
    text = (text or "").strip()
    if len(text) <= limit:
        return OcrResult(text=text, full_text=text)
    cut = text.rfind("\n", 0, limit)
    cut = cut if cut > limit // 2 else limit
    return OcrResult(text=text[:cut].rstrip() + "\n…", full_text=text, truncated=True)


class OcrService:
    def __init__(self, bot):
        self.bot = bot

    def _gateway(self):
        return getattr(self.bot, "gateway", None)

    def available(self) -> bool:
        gateway = self._gateway()
        return bool(gateway and gateway.vision is not None and gateway.google_vision_available())

    def preflight(self, attachment: Optional[discord.Attachment]) -> discord.Attachment:
        """Every check that needs no network call."""
        if not self.available():
            raise OcrError(ErrorCode.UNAVAILABLE)
        if attachment is None or not is_image(attachment):
            raise OcrError(ErrorCode.NO_IMAGE)
        if attachment.size and attachment.size > MAX_FILE_BYTES:
            raise OcrError(ErrorCode.TOO_LARGE, size=MAX_FILE_BYTES // (1024 * 1024))
        return attachment

    async def extract(self, attachment: discord.Attachment, *, user_id: int,
                      guild_id: Optional[int], source: str) -> OcrResult:
        from gateway.errors import (
            APIUnavailableError, ConfigurationError, GatewayError, ModelRateLimitError,
            QuotaExceededError,
        )
        from gateway.spec import QuotaTarget
        self.preflight(attachment)
        try:
            raw = await attachment.read()
        except (discord.HTTPException, discord.NotFound) as exc:
            raise OcrError(ErrorCode.DOWNLOAD_FAILED) from exc
        mime = attachment.content_type or "image/png"
        if len(raw) > MAX_INLINE_BYTES:
            raw = await asyncio.to_thread(shrink_for_vision, raw)
            mime = "image/jpeg"

        quota = [QuotaTarget.user(user_id, CALL_TYPE), QuotaTarget.global_(CALL_TYPE)]
        if guild_id:
            quota.insert(1, QuotaTarget.guild(guild_id, CALL_TYPE))
        try:
            result = await self._gateway().vision.document_text(
                raw, mime=mime, quota=quota, call_type=CALL_TYPE,
                metadata={"user_id": user_id, "guild_id": guild_id, "source": source},
            )
        except QuotaExceededError as exc:
            raise OcrError(ErrorCode.QUOTA) from exc
        except ModelRateLimitError as exc:
            raise OcrError(ErrorCode.MONTHLY_CAP) from exc
        except (APIUnavailableError, ConfigurationError) as exc:
            raise OcrError(ErrorCode.UNAVAILABLE) from exc
        except GatewayError as exc:
            logger.warning("OCR failed: %s", exc)
            raise OcrError(ErrorCode.FAILED) from exc

        if not (result.text or "").strip():
            raise OcrError(ErrorCode.EMPTY)
        stats = getattr(self.bot, "stats", None)
        if stats is not None:
            stats.incr("ocr.used", dims={"source": source})
        out = split_for_card(result.text)
        out.language = result.language
        return out
