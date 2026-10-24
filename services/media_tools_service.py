"""
Media tools service — the Discord-free engine room of ``/image`` and ``/convert``.

* downloads (size-guarded) and CPU work in ``asyncio.to_thread`` behind a
  semaphore of 3 (Pillow, converters); ffmpeg is serialised on its own;
* image operations (``tools/image_ops``) and background removal
  (``tools/bg_removal``, u2netp through onnxruntime);
* conversions: local engines first (``tools/convert/local``, ``media``), the
  CloudConvert path (``bot.gateway.cloudconvert``) only for layout office
  documents and ebooks, with its own per-member daily quota;
* every expected failure becomes a :class:`tools.media_errors.MediaError` whose
  ``code`` is an i18n key under ``tools.image.errors`` / ``tools.convert.errors``.

See docs/features/TOOLS_MEDIA.md.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import discord

from tools import bg_removal, image_ops
from tools.convert import detect, formats, local, media
from tools.media_errors import MediaError

logger = logging.getLogger("moddy.services.media_tools")

CPU_SLOTS = 3
CLOUD_CALL_TYPE = "convert_cloud"
#: The only hosts a CloudConvert export link may point to.
CLOUD_HOST_SUFFIX = ".cloudconvert.com"
#: Discord's default upload limit when the interaction cannot tell.
DEFAULT_UPLOAD_LIMIT = 10 * 1024 * 1024


@dataclass
class ConvertResult:
    """A finished conversion: the file, or a 24-hour link when it is too big to upload."""

    output: Optional[local.ConvertOutput] = None
    link: Optional[str] = None
    link_name: str = ""
    link_size: int = 0
    cloud: bool = False


def is_image_attachment(attachment: discord.Attachment) -> bool:
    ctype = (attachment.content_type or "").lower()
    if ctype.startswith("image/"):
        return True
    key = formats.EXTENSION_TO_KEY.get((attachment.filename or "").lower().rsplit(".", 1)[-1])
    return bool(key and formats.FORMATS[key].group == formats.IMAGE)


def upload_limit_of(interaction: discord.Interaction) -> int:
    try:
        return int(interaction.filesize_limit or DEFAULT_UPLOAD_LIMIT)
    except (AttributeError, TypeError, ValueError):
        return DEFAULT_UPLOAD_LIMIT


def gateway_error(exc: Exception) -> MediaError:
    """Map a gateway failure of the CloudConvert path to a stable error code."""
    from gateway.errors import (
        APIUnavailableError, ConfigurationError, GatewayError, GatewayTimeoutError,
        ModelRateLimitError, ProviderError, QuotaExceededError, RateLimitError,
    )
    if isinstance(exc, QuotaExceededError):
        return MediaError("quota")
    if isinstance(exc, (ModelRateLimitError, RateLimitError)):
        return MediaError("cloud_busy")
    if isinstance(exc, GatewayTimeoutError):
        return MediaError("timeout")
    if isinstance(exc, ProviderError):
        if exc.status == 422:
            return MediaError("convert_failed")
        if exc.status == 400:
            return MediaError("convert_failed")
        return MediaError("cloud_unavailable")
    if isinstance(exc, (APIUnavailableError, ConfigurationError)):
        return MediaError("cloud_unavailable")
    if isinstance(exc, GatewayError):
        return MediaError("convert_failed")
    return MediaError("convert_failed")


class MediaToolsService:
    def __init__(self, bot):
        self.bot = bot
        self._cpu = asyncio.Semaphore(CPU_SLOTS)
        self.remover = bg_removal.BackgroundRemover()

    # -- capabilities ---------------------------------------------------- #
    def cloud_available(self) -> bool:
        gateway = getattr(self.bot, "gateway", None)
        try:
            return bool(gateway and gateway.cloudconvert is not None
                        and gateway.cloudconvert_available())
        except Exception:  # noqa: BLE001
            return False

    def capabilities(self) -> formats.Capabilities:
        return formats.capabilities(cloud=self.cloud_available())

    def targets(self, src: str) -> List[str]:
        """Targets this deployment can really produce for ``src`` (ffmpeg probed)."""
        caps = self.capabilities()
        keys = formats.targets(src, caps)
        if not media.available():
            keys = [k for k in keys if formats.engine_for(src, k) != formats.E_FFMPEG]
        return keys

    # -- downloads -------------------------------------------------------- #
    async def read_attachment(self, attachment: discord.Attachment,
                              limit: int = formats.MAX_INPUT_BYTES) -> bytes:
        if attachment.size and attachment.size > limit:
            raise MediaError("too_large", max=limit // (1024 * 1024))
        if getattr(attachment, "from_components", False):
            # A media item parsed from a V2 card carries no ConnectionState, so
            # ``read()`` raises; its CDN URL is downloaded directly instead.
            return await self.read_url(attachment.url, limit)
        try:
            data = await attachment.read()
        except (discord.HTTPException, discord.NotFound) as exc:
            raise MediaError("download_failed") from exc
        if len(data) > limit:
            raise MediaError("too_large", max=limit // (1024 * 1024))
        return data

    async def read_url(self, url: str, limit: int = formats.MAX_INPUT_BYTES) -> bytes:
        """Download a Discord CDN URL stored in a card's state (public resolver only)."""
        host = (urlsplit(url).hostname or "").lower()
        if not (host.endswith("discordapp.com") or host.endswith("discordapp.net")
                or host.endswith("discord.com")):
            raise MediaError("download_failed")
        api = getattr(self.bot, "public_api", None)
        if api is None:
            raise MediaError("download_failed")
        from services.public_api import PublicAPIError
        try:
            response = await api.request("GET", url, timeout=40.0, max_bytes=limit + 1)
        except PublicAPIError as exc:
            code = "too_large" if exc.code == "too_large" else "download_failed"
            raise MediaError(code, max=limit // (1024 * 1024)) from exc
        return response.body

    # -- CPU helper ------------------------------------------------------- #
    async def cpu(self, func: Callable, *args, **kwargs):
        async with self._cpu:
            return await asyncio.to_thread(func, *args, **kwargs)

    # -- /image ----------------------------------------------------------- #
    async def inspect_image(self, raw: bytes) -> image_ops.ImageInfo:
        return await self.cpu(image_ops.read_info, raw)

    async def preview_png(self, raw: bytes) -> bytes:
        return await self.cpu(image_ops.preview_png, raw)

    async def resize(self, raw: bytes, **params) -> image_ops.Processed:
        return await self.cpu(lambda: image_ops.resize(raw, **params))

    async def compress(self, raw: bytes, level: str) -> image_ops.Processed:
        return await self.cpu(lambda: image_ops.compress(raw, level=level))

    async def convert_image(self, raw: bytes, target: str) -> image_ops.Processed:
        return await self.cpu(image_ops.convert, raw, target)

    async def _fetch_model(self, url: str) -> bytes:
        api = getattr(self.bot, "public_api", None)
        if api is None:
            raise MediaError("model_unavailable")
        from services.public_api import PublicAPIError
        try:
            response = await api.request("GET", url, timeout=90.0,
                                         max_bytes=bg_removal.MODEL_MAX_BYTES)
        except PublicAPIError as exc:
            raise MediaError("model_unavailable") from exc
        return response.body

    async def remove_background(self, raw: bytes) -> image_ops.Processed:
        # The remover serialises inferences itself (one at a time, whole process).
        return await self.remover.remove(raw, self._fetch_model)

    async def close(self) -> None:
        await self.remover.close()

    # -- /convert --------------------------------------------------------- #
    def identify(self, filename: str, content_type: Optional[str], raw: bytes) -> str:
        """Format key of the file or :class:`MediaError` ``unsupported_type``."""
        key = detect.detect(filename, content_type, raw)
        if key is None or not self.targets(key):
            raise MediaError("unsupported_type", name=filename[:60])
        return key

    async def convert(self, src: str, dst: str, raw: bytes, filename: str, *,
                      user_id: int, guild_id: Optional[int], source_url: Optional[str],
                      upload_limit: int) -> ConvertResult:
        route = formats.route(src, dst)
        if route is None or dst not in self.targets(src):
            raise MediaError("unsupported")
        if route.cloud:
            return await self._convert_cloud(
                src, dst, raw, filename, user_id=user_id, guild_id=guild_id,
                source_url=source_url, upload_limit=upload_limit)
        if route.engine == formats.E_FFMPEG:
            output = await media.convert_media(src, dst, raw, filename)
        else:
            async with self._cpu:
                output = await asyncio.to_thread(local.convert_bytes, src, dst, raw, filename)
        return ConvertResult(output=output)

    async def _convert_cloud(self, src: str, dst: str, raw: bytes, filename: str, *,
                             user_id: int, guild_id: Optional[int], source_url: Optional[str],
                             upload_limit: int) -> ConvertResult:
        from gateway.errors import GatewayError
        from gateway.spec import QuotaTarget
        if not self.cloud_available() or not source_url:
            raise MediaError("cloud_unavailable")
        quota = [QuotaTarget.user(user_id, CLOUD_CALL_TYPE), QuotaTarget.global_(CLOUD_CALL_TYPE)]
        if guild_id:
            quota.insert(1, QuotaTarget.guild(guild_id, CLOUD_CALL_TYPE))
        out_name = formats.output_name(filename, dst)
        try:
            job = await self.bot.gateway.cloudconvert.convert(
                import_url=source_url, input_format=formats.FORMATS[src].ext,
                output_format=formats.FORMATS[dst].ext,
                filename=f"input.{formats.FORMATS[src].ext}", output_filename=out_name,
                size_bytes=len(raw), quota=quota,
                metadata={"user_id": user_id, "guild_id": guild_id, "from": src, "to": dst})
        except GatewayError as exc:
            logger.info("CloudConvert %s->%s failed: %s", src, dst, exc)
            raise gateway_error(exc) from exc

        url = str(job.get("url") or "")
        size = int(job.get("size") or 0)
        host = (urlsplit(url).hostname or "").lower()
        if not url.startswith("https://") or not host.endswith(CLOUD_HOST_SUFFIX):
            raise MediaError("convert_failed")
        if size and size > upload_limit:
            return ConvertResult(link=url, link_name=out_name, link_size=size, cloud=True)
        api = getattr(self.bot, "public_api", None)
        if api is None:
            return ConvertResult(link=url, link_name=out_name, link_size=size, cloud=True)
        from services.public_api import PublicAPIError
        try:
            response = await api.request("GET", url, timeout=60.0, max_bytes=upload_limit + 1)
        except PublicAPIError as exc:
            if exc.code == "too_large":
                return ConvertResult(link=url, link_name=out_name, link_size=size, cloud=True)
            raise MediaError("download_failed") from exc
        output = local.ConvertOutput(out_name, response.body, dst)
        return ConvertResult(output=output, cloud=True)


def get_service(bot) -> MediaToolsService:
    """The shared service (created lazily, stored on the bot)."""
    service = getattr(bot, "media_tools", None)
    if service is None:
        service = MediaToolsService(bot)
        bot.media_tools = service
    return service
