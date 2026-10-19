"""
``/sauce`` orchestration — find the source of an image.

Two providers answer in parallel through the gateway: SauceNAO (art, manga,
anime pictures; needs ``SAUCENAO_API_KEY``) and trace.moe (anime screenshot ->
title, episode, moment; the key is optional). Each one is optional: with
neither configured the command answers "unavailable"; with one, the other is
simply not asked. A provider that fails never hides the other's answer.

The image is handed to the providers **by URL** (they fetch it themselves,
nothing is downloaded here). If a provider cannot fetch the URL and the image is
a Discord attachment, it is downloaded once and uploaded instead.

One search counts once against the ``sauce`` quota (user / server / global),
whatever number of providers it queries.

See docs/features/TOOLS_INSPECT.md.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

import discord

from tools import sauce as sc
from utils.tool_card import ToolError

logger = logging.getLogger("moddy.sauce")

CALL_TYPE = "sauce"
TOOL = "sauce"
MAX_IMAGE_BYTES = 20 * 1024 * 1024
#: Wall-clock budget for the whole search (each provider has its own timeout).
SEARCH_TIMEOUT = 50

IMAGE_TYPES = ("image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".jfif", ".webp", ".gif")


@dataclass
class ImageSource:
    """An image to search: its URL, and the attachment when there is one."""

    url: str
    filename: str = "image"
    mime: Optional[str] = None
    size: Optional[int] = None
    attachment: Optional[discord.Attachment] = None


def is_supported_image(filename: Optional[str], content_type: Optional[str]) -> bool:
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype:
        return ctype in IMAGE_TYPES
    return (filename or "").lower().endswith(IMAGE_EXTENSIONS)


def from_attachment(attachment: discord.Attachment) -> ImageSource:
    """Validate an attachment option. Raises :class:`ToolError`."""
    if not is_supported_image(attachment.filename, attachment.content_type):
        raise ToolError("not_image", tool=TOOL)
    if attachment.size and attachment.size > MAX_IMAGE_BYTES:
        raise ToolError("too_large", tool=TOOL, max=f"{MAX_IMAGE_BYTES // (1024 * 1024)} MB")
    return ImageSource(url=attachment.url, filename=attachment.filename,
                       mime=(attachment.content_type or "").split(";")[0] or None,
                       size=attachment.size, attachment=attachment)


def from_message(message: discord.Message) -> Optional[ImageSource]:
    """First image of a message: attachment, else an embed image / thumbnail."""
    for attachment in getattr(message, "attachments", None) or []:
        if is_supported_image(attachment.filename, attachment.content_type):
            if attachment.size and attachment.size > MAX_IMAGE_BYTES:
                raise ToolError("too_large", tool=TOOL,
                                max=f"{MAX_IMAGE_BYTES // (1024 * 1024)} MB")
            return from_attachment(attachment)
    for embed in getattr(message, "embeds", None) or []:
        for media in (getattr(embed, "image", None), getattr(embed, "thumbnail", None)):
            url = sc.safe_url(getattr(media, "url", None))
            if url:
                return ImageSource(url=url, filename="image")
    return None


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def map_error(exc: BaseException) -> ToolError:
    """Gateway failure -> the ``ToolError`` the user reads."""
    from gateway.errors import (
        APIUnavailableError, ConfigurationError, GatewayTimeoutError, ModelRateLimitError,
        ProviderError, QuotaExceededError, RateLimitError,
    )
    if isinstance(exc, ToolError):
        return exc
    if isinstance(exc, QuotaExceededError):
        return ToolError("quota", tool=TOOL)
    if isinstance(exc, (ModelRateLimitError, RateLimitError)):
        return ToolError("busy", tool=TOOL)
    if isinstance(exc, ProviderError):
        if exc.status == 402:
            return ToolError("busy", tool=TOOL)
        if exc.status == 400:
            return ToolError("unreadable", tool=TOOL)
    if isinstance(exc, (GatewayTimeoutError, asyncio.TimeoutError)):
        return ToolError("timeout", tool=TOOL)
    if isinstance(exc, (APIUnavailableError, ConfigurationError)):
        return ToolError("unavailable", tool=TOOL)
    logger.warning("sauce provider failed: %s: %s", type(exc).__name__, exc)
    return ToolError("failed", tool=TOOL)


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #


def available(bot) -> Tuple[bool, bool]:
    """``(saucenao, tracemoe)`` availability."""
    gateway = getattr(bot, "gateway", None)
    if gateway is None or not getattr(gateway, "available", False):
        return False, False
    return gateway.saucenao_available(), gateway.tracemoe_available()


async def search(bot, source: ImageSource, *, user_id: int, guild_id: Optional[int],
                 allow_explicit: bool, origin: str = "slash") -> sc.SauceResult:
    """Run both providers. Raises :class:`ToolError` when nothing could be asked."""
    from gateway.errors import ProviderError
    from gateway.spec import QuotaTarget

    use_sauce, use_trace = available(bot)
    if not (use_sauce or use_trace):
        raise ToolError("unavailable", tool=TOOL)
    gateway = bot.gateway

    quota: List[QuotaTarget] = [QuotaTarget.user(user_id, CALL_TYPE)]
    if guild_id:
        quota.append(QuotaTarget.guild(guild_id, CALL_TYPE))
    quota.append(QuotaTarget.global_(CALL_TYPE))
    for target in quota:
        if not await gateway.quota_available(target):
            raise ToolError("quota", tool=TOOL)

    metadata = {"user_id": user_id, "guild_id": guild_id}
    result = sc.SauceResult(image_url=source.url)
    # The quota is charged once, by the first provider that is asked.
    charged_to = "saucenao" if use_sauce else "tracemoe"
    uploaded: dict = {}

    async def upload_bytes() -> Optional[bytes]:
        if source.attachment is None:
            return None
        if "bytes" not in uploaded:
            try:
                uploaded["bytes"] = await source.attachment.read()
            except (discord.HTTPException, discord.NotFound):
                uploaded["bytes"] = None
        return uploaded["bytes"]

    async def call(name: str):
        client = gateway.saucenao if name == "saucenao" else gateway.tracemoe
        kwargs = dict(quota=quota if name == charged_to else None, call_type=CALL_TYPE,
                      metadata=metadata)
        if name == "saucenao":
            kwargs["hide"] = sc.HIDE_NONE if allow_explicit else sc.HIDE_SAFE
        try:
            return await client.search(image_url=source.url, **kwargs)
        except ProviderError as exc:
            # "Could not fetch the image": upload it ourselves when we can.
            if exc.status != 400:
                raise
            data = await upload_bytes()
            if not data:
                raise
            return await client.search(image=data, mime=source.mime,
                                       filename=source.filename, **kwargs)

    names = [n for n, on in (("saucenao", use_sauce), ("tracemoe", use_trace)) if on]
    try:
        outcomes = await asyncio.wait_for(
            asyncio.gather(*(call(n) for n in names), return_exceptions=True), SEARCH_TIMEOUT)
    except asyncio.TimeoutError as exc:
        raise ToolError("timeout", tool=TOOL) from exc

    errors: List[ToolError] = []
    for name, outcome in zip(names, outcomes):
        if isinstance(outcome, BaseException):
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
            errors.append(map_error(outcome))
            result.failed.append(name)
            continue
        if name == "saucenao":
            result.used_saucenao = True
            result.matches = sc.parse_saucenao(outcome, allow_explicit=allow_explicit)
        else:
            result.used_tracemoe = True
            result.anime = sc.parse_tracemoe(outcome, allow_adult=allow_explicit)

    if not (result.used_saucenao or result.used_tracemoe):
        # Everything failed: tell the user the most useful reason.
        for code in ("quota", "busy", "timeout", "unreadable", "unavailable"):
            for error in errors:
                if error.code == code:
                    raise error
        raise errors[0] if errors else ToolError("failed", tool=TOOL)

    stats = getattr(bot, "stats", None)
    if stats is not None:
        try:
            stats.incr("sauce.used", dims={"source": origin, "found": "yes" if result.found else "no"})
        except Exception:  # noqa: BLE001 — statistics never break a command
            pass
    return result
