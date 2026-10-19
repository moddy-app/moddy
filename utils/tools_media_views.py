"""
Views of the media utility tools (``/image`` and ``/convert``) — see
docs/features/TOOLS_MEDIA.md.

Everything interactive is persistent (docs/guides/PERSISTENT_VIEWS.md) and
**stateless** wherever possible:

* the four ``/image`` buttons have fixed custom ids; the image they work on is
  re-derived from the attachments of ``interaction.message`` (the card always
  carries the image it is about), so nothing is stored anywhere;
* the format picker of ``/convert`` (and of the ``/image`` Convert button) is a
  ``DynamicItem`` select whose custom id holds a short state token
  (``utils.tool_card.save_state``: the CDN URL of the file, 24 h in Redis). An
  expired token answers "run the command again".

The orchestration (download -> work -> card) lives here too, because the
persistent callbacks need it; the cog only declares the slash commands.
"""

from __future__ import annotations

import io
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import discord
from discord import ui

from cogs.error_handler import BaseModal, BaseView
from services.media_tools_service import (
    ConvertResult, MediaToolsService, get_service, is_image_attachment, upload_limit_of,
)
from tools import image_ops
from tools.convert import formats
from tools.media_errors import MediaError
from utils import tool_card as tc
from utils.emojis import (
    ASPECT_RATIO, AUTO_FIX_HIGH, CLOUDCONVERT, COMPRESS, DOWNLOAD, IMAGE, TRANSFORM,
)
from utils.i18n import i18n, t

logger = logging.getLogger("moddy.tools_media")

NO_MENTIONS = tc.NO_MENTIONS
PREVIEW_NAME = "moddy-preview.png"
STATE_KIND = "convert"
ORIGIN_IMAGE, ORIGIN_CONVERT = "image", "convert"

_PREVIEW_MAX_BYTES = 8 * 1024 * 1024


def _view() -> BaseView:
    return BaseView()  # timeout=None; filled by ToolCard.build(view=...)


def safe_filename(name: str) -> str:
    """A file name safe for ``attachment://`` references (no spaces, no oddities)."""
    cleaned = re.sub(r"[^\w.\-]+", "_", name, flags=re.UNICODE).strip("._")
    return cleaned[:90] or "file"


# --------------------------------------------------------------------------- #
# Reading the card back (stateless buttons)
# --------------------------------------------------------------------------- #


def find_source_attachment(message: Optional[discord.Message]) -> Optional[discord.Attachment]:
    """The image a card is about: its first image attachment that is not the preview."""
    for attachment in getattr(message, "attachments", None) or []:
        if attachment.filename == PREVIEW_NAME:
            continue
        if is_image_attachment(attachment):
            return attachment
    return None


def owns_card(interaction: discord.Interaction) -> bool:
    """True when the clicker may edit the card in place (it is theirs, or private).

    An ephemeral message is only visible to its owner. A public one belongs to
    whoever ran the command (``interaction_metadata``); anybody else gets their
    own private answer instead of rewriting someone else's message.
    """
    message = interaction.message
    if message is None:
        return False
    if message.flags.ephemeral:
        return True
    meta = getattr(message, "interaction_metadata", None)
    return bool(meta and getattr(meta.user, "id", None) == interaction.user.id)


# --------------------------------------------------------------------------- #
# Small renderers
# --------------------------------------------------------------------------- #


def _fmt_size(size: int, locale: str) -> str:
    return tc.format_bytes(size, locale)


def _dims(info: image_ops.ImageInfo) -> str:
    return f"{info.width} × {info.height} px"


def _arrow(before: str, after: str, changed: bool = True) -> str:
    return f"{before} → {after}" if changed else after


def _percent_text(before: int, after: int) -> str:
    saved = image_ops.percent_saved(before, after)
    if saved == 0:
        return ""
    return f" ({'−' if saved > 0 else '+'}{abs(saved)} %)"


def _notes_text(keys: Sequence[str], root: str, locale: str) -> List[str]:
    out = []
    for key in dict.fromkeys(keys):
        text = i18n.get(f"{root}.notes.{key}", locale=locale)
        if not text.startswith("["):
            out.append(text)
    return out


def image_details(info: image_ops.ImageInfo, locale: str,
                  before: Optional[image_ops.ImageInfo] = None) -> List[str]:
    """Detail lines; with ``before``, changed fields read ``before → after``."""
    lines = []
    fmt_after = formats.label_of(info.format_key)
    fmt = _arrow(formats.label_of(before.format_key), fmt_after,
                 bool(before and before.format_key != info.format_key)) if before else fmt_after
    lines.append(tc.detail(t("tools.image.format", locale=locale), fmt))
    dims = _arrow(_dims(before), _dims(info), bool(before and (before.width, before.height) !=
                                                    (info.width, info.height))) if before else _dims(info)
    lines.append(tc.detail(t("tools.image.dimensions", locale=locale), dims))
    weight = _fmt_size(info.size_bytes, locale)
    if before:
        weight = f"{_fmt_size(before.size_bytes, locale)} → {weight}" + \
            _percent_text(before.size_bytes, info.size_bytes)
    lines.append(tc.detail(t("tools.image.weight", locale=locale), weight))
    lines.append(tc.detail(
        t("tools.image.transparency", locale=locale),
        t("tools.image.yes" if info.has_alpha else "tools.image.no", locale=locale)))
    if info.animated:
        lines.append(tc.detail(t("tools.image.animation", locale=locale),
                               t("tools.image.frames", locale=locale, count=info.frames)))
    return lines


# --------------------------------------------------------------------------- #
# Files of a card
# --------------------------------------------------------------------------- #


class CardFiles:
    """The attachments of an image card and how the container references them."""

    def __init__(self, files: List[discord.File], gallery: Optional[str], file_ref: Optional[str]):
        self.files = files
        self.gallery = gallery          # attachment name shown in the gallery
        self.file_ref = file_ref        # attachment shown as a file component (not previewable)


async def make_card_files(service: MediaToolsService, data: bytes, filename: str,
                          info: image_ops.ImageInfo, upload_limit: int,
                          *, attach_source: bool = True) -> CardFiles:
    """Attach the image (stateless buttons need it) plus a PNG preview when
    Discord cannot display its format; a source above the upload limit is only
    previewed, never attached."""
    name = safe_filename(filename)
    previewable = info.format_key in formats.PREVIEWABLE_IMAGE_KEYS
    if attach_source and len(data) <= upload_limit:
        if previewable:
            return CardFiles([discord.File(io.BytesIO(data), name)], name, None)
        preview = await service.preview_png(data)
        return CardFiles([discord.File(io.BytesIO(data), name),
                          discord.File(io.BytesIO(preview), PREVIEW_NAME)], PREVIEW_NAME, name)
    preview = await service.preview_png(data)
    return CardFiles([discord.File(io.BytesIO(preview), PREVIEW_NAME)], PREVIEW_NAME, None)


def card_files_from_message(message: discord.Message, source: discord.Attachment,
                            info: image_ops.ImageInfo) -> CardFiles:
    """References to the attachments a card already carries (nothing is re-uploaded)."""
    if info.format_key in formats.PREVIEWABLE_IMAGE_KEYS:
        return CardFiles([], source.filename, None)
    has_preview = any(a.filename == PREVIEW_NAME for a in message.attachments)
    return CardFiles([], PREVIEW_NAME if has_preview else None, source.filename)


# --------------------------------------------------------------------------- #
# Image card
# --------------------------------------------------------------------------- #


def build_image_card(info: image_ops.ImageInfo, locale: str, card_files: CardFiles, *,
                     before: Optional[image_ops.ImageInfo] = None, notes: Sequence[str] = (),
                     hints: Sequence[str] = (), suggestion: Optional[Tuple[str, int]] = None,
                     notice: Optional[str] = None, buttons: bool = True,
                     accent: int = tc.ACCENT_INFO,
                     requester_id: Optional[int] = None) -> ui.LayoutView:
    card = tc.ToolCard(title=t("tools.image.title", locale=locale), icon=IMAGE,
                       accent=tc.ACCENT_WARNING if notice else accent)
    if notice:
        card.text(notice)
    if card_files.gallery:
        card.media(f"attachment://{card_files.gallery}")
    if card_files.file_ref:
        card.file(card_files.file_ref)
    card.details(image_details(info, locale, before))
    for text in _notes_text(notes, "tools.image", locale):
        card.hint(text)
    if suggestion:
        card.hint(t("tools.image.suggestion", locale=locale,
                    format=formats.label_of(suggestion[0]),
                    size=tc.inline(_fmt_size(suggestion[1], locale))))
    for text in hints:
        card.hint(text)
    card.footer(tc.requested_by(locale, requester_id))
    rows = [image_buttons_row(locale)] if buttons else []
    return card.build(view=_view(), rows=rows)


def image_buttons_row(locale: str) -> ui.ActionRow:
    row = ui.ActionRow()
    for cls in (ImageResizeButton, ImageCompressButton, ImageConvertButton, ImageBackgroundButton):
        row.add_item(cls(locale))
    return row


# --------------------------------------------------------------------------- #
# Orchestration helpers
# --------------------------------------------------------------------------- #


def _tool_error(exc: MediaError, tool: str) -> tc.ToolError:
    return tc.ToolError(exc.code, tool=tool, **exc.params)


async def _begin(interaction: discord.Interaction, locale: str, text: str, *,
                 in_place: bool) -> None:
    """Acknowledge with a loading card: in the card itself, or in a private message."""
    loading = tc.loading_card(locale, text)
    if interaction.response.is_done():
        return
    if in_place:
        await interaction.response.edit_message(view=loading)
    else:
        await interaction.response.send_message(view=loading, ephemeral=True)


async def _finish(interaction: discord.Interaction, view: ui.LayoutView,
                  files: Optional[List[discord.File]] = None) -> None:
    kwargs: Dict[str, Any] = {"view": view, "allowed_mentions": NO_MENTIONS}
    if files is not None:
        kwargs["attachments"] = files
    await interaction.edit_original_response(**kwargs)


async def _show_error(interaction: discord.Interaction, exc: MediaError, tool: str, locale: str,
                      *, restore: Optional[ui.LayoutView] = None) -> None:
    """Expected failure. ``restore`` (a rebuilt card) keeps the user's image on screen."""
    card = tc.tool_error_card(_tool_error(exc, tool), locale)
    if restore is not None:
        await _finish(interaction, restore)
        await interaction.followup.send(view=card, ephemeral=True, allowed_mentions=NO_MENTIONS)
    elif interaction.response.is_done():
        await _finish(interaction, card, files=[])
    else:
        await tc.respond(interaction, view=card, ephemeral=True)


async def find_card_attachment(interaction: discord.Interaction) -> Optional[discord.Attachment]:
    """The image of the card an interaction comes from.

    ``interaction.message`` is the first place to look; when it carries no image
    (Discord does not always repeat the attachments of the message a Modal was
    opened from), the message is fetched back through the interaction token,
    which also works for ephemeral cards.
    """
    attachment = find_source_attachment(interaction.message)
    if attachment is not None or interaction.message is None:
        return attachment
    try:
        fetched = await interaction.original_response()
    except discord.HTTPException:
        fetched = None
    attachment = find_source_attachment(fetched)
    if attachment is None:
        logger.warning(
            "image card without a readable image: message=%s flags=%s attachments=%s fetched=%s",
            interaction.message.id, interaction.message.flags.value,
            [a.filename for a in interaction.message.attachments],
            [a.filename for a in getattr(fetched, "attachments", [])])
    return attachment


async def _read_source(service: MediaToolsService, interaction: discord.Interaction
                       ) -> Tuple[bytes, discord.Attachment]:
    attachment = await find_card_attachment(interaction)
    if attachment is None:
        raise MediaError("expired")
    return await service.read_attachment(attachment), attachment


async def present_image(service: MediaToolsService, interaction: discord.Interaction, *,
                        data: bytes, filename: str, locale: str,
                        before: Optional[image_ops.ImageInfo] = None,
                        notes: Sequence[str] = (), suggestion: Optional[Tuple[str, int]] = None,
                        hints: Sequence[str] = (), requester_id: Optional[int] = None,
                        info: Optional[image_ops.ImageInfo] = None,
                        accent: int = tc.ACCENT_INFO, notice: Optional[str] = None) -> None:
    """Build the image card for ``data`` and publish it as the interaction's response."""
    limit = upload_limit_of(interaction)
    info = info or await service.inspect_image(data)
    files = await make_card_files(service, data, filename, info, limit)
    attached = len(data) <= limit
    hint_list = list(hints)
    if not attached:
        hint_list.append(t("tools.image.too_big_to_attach", locale=locale,
                           max=tc.inline(_fmt_size(limit, locale))))
    view = build_image_card(info, locale, files, before=before, notes=notes,
                            suggestion=suggestion, hints=hint_list, buttons=attached,
                            accent=accent, requester_id=requester_id, notice=notice)
    await _finish(interaction, view, files=files.files)


# --------------------------------------------------------------------------- #
# /image — first card
# --------------------------------------------------------------------------- #


async def start_image(interaction: discord.Interaction, attachment: discord.Attachment, *,
                      ephemeral: bool) -> None:
    """The ``/image`` command: preview + facts + the four action buttons."""
    locale = i18n.get_user_locale(interaction)
    service = get_service(interaction.client)
    await interaction.response.defer(ephemeral=ephemeral)
    try:
        if not is_image_attachment(attachment):
            raise MediaError("not_image")
        raw = await service.read_attachment(attachment)
        info = await service.inspect_image(raw)
        await present_image(service, interaction, data=raw, filename=attachment.filename,
                            locale=locale, info=info,
                            requester_id=None if ephemeral else interaction.user.id)
    except MediaError as exc:
        await interaction.edit_original_response(
            view=tc.tool_error_card(_tool_error(exc, "image"), locale), attachments=[])


# --------------------------------------------------------------------------- #
# /image — actions
# --------------------------------------------------------------------------- #


_LOADING_KEYS = {"resize": "loading_resize", "compress": "loading_compress",
                 "bg": "loading_bg", "convert": "loading_convert"}


async def apply_image_action(interaction: discord.Interaction, op: str, params: Dict[str, Any]
                             ) -> None:
    """Run ``op`` (resize / compress / bg) on the card's image and show the result."""
    locale = i18n.get_user_locale(interaction)
    service = get_service(interaction.client)
    in_place = owns_card(interaction)
    await _begin(interaction, locale, t(f"tools.image.{_LOADING_KEYS[op]}", locale=locale),
                 in_place=in_place)
    requester = None
    if in_place and not (interaction.message and interaction.message.flags.ephemeral):
        requester = interaction.user.id
    raw = None
    source = None
    try:
        raw, source = await _read_source(service, interaction)
        before = await service.inspect_image(raw)
        if op == "resize":
            done = await service.resize(raw, **params)
        elif op == "compress":
            done = await service.compress(raw, params["level"])
        else:
            done = await service.remove_background(raw)
        limit = upload_limit_of(interaction)
        if not done.unchanged and len(done.data) > limit:
            raise MediaError("result_too_large", max=tc.format_bytes(limit, locale))
        name = formats.output_name(source.filename, done.format_key)
        after = await service.inspect_image(done.data)
        hints = []
        if op == "bg":
            hints.append(t("tools.image.bg_note", locale=locale))
        if done.unchanged:
            hints.append(t("tools.image.unchanged", locale=locale))
        await present_image(
            service, interaction, data=done.data, filename=name, locale=locale, info=after,
            before=before, notes=done.notes, suggestion=done.suggestion, hints=hints,
            requester_id=requester,
            accent=tc.ACCENT_WARNING if done.unchanged else tc.ACCENT_SUCCESS)
    except MediaError as exc:
        restore = None
        if in_place and raw is not None:
            try:
                info = await service.inspect_image(raw)
                restore = build_image_card(info, locale, card_files_from_message(
                    interaction.message, source, info), buttons=True, requester_id=requester)
            except MediaError:
                restore = None
        await _show_error(interaction, exc, "image", locale, restore=restore if in_place else None)


# --------------------------------------------------------------------------- #
# Modals (V2) — Resize / Compress
# --------------------------------------------------------------------------- #


def _int_or_none(raw: Optional[str]) -> Optional[int]:
    raw = (raw or "").strip().replace(" ", "")
    if not raw:
        return None
    if not raw.isdigit():
        raise MediaError("invalid_number")
    return int(raw)


class ResizeModal(BaseModal):
    """Width / height / percent + how to fit. Opened by the Resize button."""

    def __init__(self, locale: str, message: Optional[discord.Message] = None):
        super().__init__(title=t("tools.image.resize_title", locale=locale)[:45])
        self.message = message
        self.add_item(ui.TextDisplay(t("tools.image.resize_help", locale=locale)))
        self.width = ui.Label(
            text=t("tools.image.width_label", locale=locale)[:45],
            component=ui.TextInput(style=discord.TextStyle.short, required=False, max_length=5,
                                   placeholder="1280"))
        self.height = ui.Label(
            text=t("tools.image.height_label", locale=locale)[:45],
            component=ui.TextInput(style=discord.TextStyle.short, required=False, max_length=5,
                                   placeholder="720"))
        self.percent = ui.Label(
            text=t("tools.image.percent_label", locale=locale)[:45],
            description=t("tools.image.percent_hint", locale=locale)[:100],
            component=ui.TextInput(style=discord.TextStyle.short, required=False, max_length=3,
                                   placeholder="50"))
        self.mode = ui.Label(
            text=t("tools.image.mode_label", locale=locale)[:45],
            component=ui.RadioGroup(required=True, options=[
                discord.RadioGroupOption(
                    label=t(f"tools.image.modes.{m}", locale=locale)[:100], value=m,
                    description=t(f"tools.image.modes.{m}_hint", locale=locale)[:100],
                    default=(m == image_ops.FIT))
                for m in image_ops.RESIZE_MODES]))
        for item in (self.width, self.height, self.percent, self.mode):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        interaction.message = self.message or interaction.message
        try:
            width = _int_or_none(self.width.component.value)
            height = _int_or_none(self.height.component.value)
            percent = _int_or_none(self.percent.component.value)
        except MediaError as exc:
            await tc.respond(interaction, view=tc.tool_error_card(_tool_error(exc, "image"), locale),
                             ephemeral=True)
            return
        if width is None and height is None and percent is None:
            await tc.respond(interaction, view=tc.tool_error_card(
                _tool_error(MediaError("nothing_to_do"), "image"), locale), ephemeral=True)
            return
        await apply_image_action(interaction, "resize", {
            "width": width, "height": height,
            "percent": float(percent) if percent is not None else None,
            "mode": self.mode.component.value or image_ops.FIT})


class CompressModal(BaseModal):
    """Compression strength. Opened by the Compress button."""

    def __init__(self, locale: str, message: Optional[discord.Message] = None):
        super().__init__(title=t("tools.image.compress_title", locale=locale)[:45])
        self.message = message
        self.add_item(ui.TextDisplay(t("tools.image.compress_help", locale=locale)))
        self.level = ui.Label(
            text=t("tools.image.level_label", locale=locale)[:45],
            component=ui.RadioGroup(required=True, options=[
                discord.RadioGroupOption(
                    label=t(f"tools.image.levels.{lv}", locale=locale)[:100], value=lv,
                    description=t(f"tools.image.levels.{lv}_hint", locale=locale)[:100],
                    default=(lv == image_ops.BALANCED))
                for lv in image_ops.COMPRESS_LEVELS]))
        self.add_item(self.level)

    async def on_submit(self, interaction: discord.Interaction):
        interaction.message = self.message or interaction.message
        await apply_image_action(interaction, "compress",
                                 {"level": self.level.component.value or image_ops.BALANCED})


# --------------------------------------------------------------------------- #
# /image — the four persistent buttons
# --------------------------------------------------------------------------- #


class ImageResizeButton(ui.DynamicItem[ui.Button], template=r"moddy:tools:image:resize"):
    def __init__(self, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=t("tools.image.btn_resize", locale=locale)[:80],
            style=discord.ButtonStyle.secondary, emoji=tc.button_emoji(ASPECT_RATIO),
            custom_id="moddy:tools:image:resize"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls()

    @tc.guarded
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            ResizeModal(i18n.get_user_locale(interaction), interaction.message))


class ImageCompressButton(ui.DynamicItem[ui.Button], template=r"moddy:tools:image:compress"):
    def __init__(self, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=t("tools.image.btn_compress", locale=locale)[:80],
            style=discord.ButtonStyle.secondary, emoji=tc.button_emoji(COMPRESS),
            custom_id="moddy:tools:image:compress"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls()

    @tc.guarded
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            CompressModal(i18n.get_user_locale(interaction), interaction.message))


class ImageBackgroundButton(ui.DynamicItem[ui.Button], template=r"moddy:tools:image:bg"):
    def __init__(self, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=t("tools.image.btn_background", locale=locale)[:80],
            style=discord.ButtonStyle.secondary, emoji=tc.button_emoji(AUTO_FIX_HIGH),
            custom_id="moddy:tools:image:bg"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls()

    @tc.guarded
    async def callback(self, interaction: discord.Interaction):
        await apply_image_action(interaction, "bg", {})


class ImageConvertButton(ui.DynamicItem[ui.Button], template=r"moddy:tools:image:convert"):
    """Opens the same format picker as /convert, limited to image formats."""

    def __init__(self, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=t("tools.image.btn_convert", locale=locale)[:80],
            style=discord.ButtonStyle.secondary, emoji=tc.button_emoji(TRANSFORM),
            custom_id="moddy:tools:image:convert"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls()

    @tc.guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        service = get_service(interaction.client)
        attachment = await find_card_attachment(interaction)
        if attachment is None:
            raise tc.ToolError("expired", tool="image")
        try:
            raw = await service.read_attachment(attachment)
            info = await service.inspect_image(raw)
        except MediaError as exc:
            raise _tool_error(exc, "image") from None
        keys = [k for k in service.targets(info.format_key)
                if formats.FORMATS[k].group == formats.IMAGE]
        state = {"u": attachment.url, "n": attachment.filename, "k": info.format_key,
                 "z": len(raw), "o": ORIGIN_IMAGE, "uid": interaction.user.id}
        token = await tc.save_state(interaction.client, STATE_KIND, state)
        view = build_convert_picker(
            filename=attachment.filename, src=info.format_key, size=len(raw), token=token,
            targets=keys, locale=locale, service=service)
        await tc.respond(interaction, view=view, ephemeral=True)


# --------------------------------------------------------------------------- #
# /convert — picker + select
# --------------------------------------------------------------------------- #


def grouped_targets(keys: Sequence[str]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for group in formats.GROUPS:
        members = [k for k in keys if formats.FORMATS[k].group == group]
        if members:
            out[group] = members
    return out


def build_convert_picker(*, filename: str, src: str, size: int, token: str,
                         targets: Sequence[str], locale: str,
                         service: Optional[MediaToolsService] = None,
                         notice: Optional[str] = None) -> ui.LayoutView:
    """"Convert it to…": one select per kind of target (images, documents, audio…)."""
    card = tc.ToolCard(title=t("tools.convert.title", locale=locale), icon=TRANSFORM)
    if notice:
        card.text(notice)
    card.details([
        (t("tools.convert.file", locale=locale), filename[:80]),
        (t("tools.convert.detected", locale=locale),
         f"{formats.label_of(src)} · {_fmt_size(size, locale)}"),
    ])
    card.text(t("tools.convert.pick", locale=locale))
    has_cloud = any(formats.engine_for(src, k) == formats.E_CLOUD for k in targets)
    if has_cloud:
        card.hint(t("tools.convert.cloud_hint", locale=locale))
    rows = []
    for group, keys in grouped_targets(targets).items():
        row = ui.ActionRow()
        row.add_item(ConvertSelect(token, group, locale, src=src, keys=keys))
        rows.append(row)
    return card.build(view=_view(), rows=rows)


class ConvertSelect(
    ui.DynamicItem[ui.Select],
    template=r"moddy:tools:convert:to:(?P<token>[A-Za-z0-9]{10}):(?P<group>[a-z]+)",
):
    """Target format picker. The file comes from the stored state (token)."""

    def __init__(self, token: str, group: str, locale: str = "en-US", *, src: Optional[str] = None,
                 keys: Sequence[str] = ()):
        options = []
        for key in keys:
            cloud = src is not None and formats.engine_for(src, key) == formats.E_CLOUD
            options.append(discord.SelectOption(
                label=formats.label_of(key), value=key,
                description=t("tools.convert.option_cloud", locale=locale)[:100] if cloud else None))
        super().__init__(ui.Select(
            placeholder=t(f"tools.convert.groups.{group}", locale=locale)[:150],
            options=options or [discord.SelectOption(label="—", value="-")],
            custom_id=f"moddy:tools:convert:to:{token}:{group}"))
        self.token = token
        self.group = group

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["token"], match["group"])

    @tc.guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        state = await tc.load_state(interaction.client, STATE_KIND, self.token)
        if not state:
            await tc.respond(interaction, view=tc.expired_card(locale), ephemeral=True)
            return
        dst = self.item.values[0]
        if dst not in formats.FORMATS:
            return
        await run_conversion(interaction, state, dst)


async def run_conversion(interaction: discord.Interaction, state: Dict[str, Any], dst: str,
                         *, from_command: bool = False) -> None:
    """Convert the stored file to ``dst`` and publish the result.

    ``from_command``: the interaction is a slash command that already deferred
    (the answer is its original response); otherwise it is a select click.
    """
    locale = i18n.get_user_locale(interaction)
    service = get_service(interaction.client)
    src, name, origin = state["k"], state["n"], state.get("o", ORIGIN_CONVERT)
    tool = "image" if origin == ORIGIN_IMAGE else "convert"
    in_place = True
    if not from_command:
        in_place = owns_card(interaction) and (
            interaction.message.flags.ephemeral or state.get("uid") == interaction.user.id)
        await _begin(interaction, locale, t("tools.convert.loading", locale=locale),
                     in_place=in_place)
    requester = None
    if not from_command and in_place and not interaction.message.flags.ephemeral:
        requester = interaction.user.id
    try:
        raw = await service.read_url(state["u"])
        limit = upload_limit_of(interaction)
        result = await service.convert(
            src, dst, raw, name, user_id=interaction.user.id,
            guild_id=interaction.guild_id, source_url=state["u"], upload_limit=limit)
        await present_conversion(service, interaction, result, state, dst, raw_size=len(raw),
                                 locale=locale, limit=limit, requester=requester)
    except MediaError as exc:
        if interaction.response.is_done():
            await _show_error(interaction, exc, tool, locale)
        else:
            await tc.respond(interaction, view=tc.tool_error_card(_tool_error(exc, tool), locale),
                             ephemeral=True)


async def present_conversion(service: MediaToolsService, interaction: discord.Interaction,
                             result: ConvertResult, state: Dict[str, Any], dst: str, *,
                             raw_size: int, locale: str, limit: int,
                             requester: Optional[int]) -> None:
    out = result.output
    src = state["k"]
    if out is not None and len(out.data) > limit:
        raise MediaError("result_too_large", max=tc.format_bytes(limit, locale))

    # An image out of the /image flow goes back to the full image card (chainable).
    if out is not None and state.get("o") == ORIGIN_IMAGE and formats.group_of(dst) == formats.IMAGE:
        before = None
        try:
            src_raw = await service.read_url(state["u"])
            before = await service.inspect_image(src_raw)
        except MediaError:
            pass
        await present_image(service, interaction, data=out.data, filename=out.name, locale=locale,
                            before=before, notes=out.notes, requester_id=requester,
                            accent=tc.ACCENT_SUCCESS)
        return

    card = tc.ToolCard(title=t("tools.convert.title", locale=locale), icon=TRANSFORM,
                       accent=tc.ACCENT_SUCCESS)
    files: List[discord.File] = []
    rows: List[ui.Item] = []
    summary = f"{formats.label_of(src)} → {formats.label_of(dst)}"
    if out is not None:
        card.text(tc.with_icon(DOWNLOAD, f"**{out.name}**"))
        previewable = out.format_key in formats.PREVIEWABLE_IMAGE_KEYS
        att_name = safe_filename(out.name)
        files.append(discord.File(io.BytesIO(out.data), att_name))
        if previewable:
            card.media(f"attachment://{att_name}")
        else:
            card.file(att_name)
        size_text = _fmt_size(len(out.data), locale)
        extra = []
        if out.pages > 1:
            extra.append((t("tools.convert.pages", locale=locale), out.pages))
        card.details([(t("tools.convert.conversion", locale=locale), summary),
                      (t("tools.convert.weight", locale=locale),
                       f"{_fmt_size(raw_size, locale)} → {size_text}")] + extra)
    else:
        card.text(tc.with_icon(DOWNLOAD, f"**{result.link_name}**"))
        card.details([(t("tools.convert.conversion", locale=locale), summary),
                      (t("tools.convert.weight", locale=locale),
                       f"{_fmt_size(raw_size, locale)} → {_fmt_size(result.link_size, locale)}")])
        card.hint(t("tools.convert.link_note", locale=locale))
        row = tc.link_row(tc.link_button(t("tools.convert.download", locale=locale), result.link,
                                         icon=DOWNLOAD))
        if row:
            rows.append(row)
    for text in _notes_text(out.notes if out else [], "tools.convert", locale):
        card.hint(text)
    if result.cloud:
        card.footer(tc.source_line(locale, (CLOUDCONVERT, "CloudConvert"), verb="converted_by"),
                    tc.requested_by(locale, requester))
    else:
        card.footer(tc.requested_by(locale, requester))
    view = card.build(view=_view(), rows=rows)
    await _finish(interaction, view, files=files)


# --------------------------------------------------------------------------- #
# /convert — the command's entry point
# --------------------------------------------------------------------------- #


async def start_convert(interaction: discord.Interaction, attachment: discord.Attachment,
                        fmt: Optional[str], *, ephemeral: bool) -> None:
    locale = i18n.get_user_locale(interaction)
    service = get_service(interaction.client)
    await interaction.response.defer(ephemeral=ephemeral)
    try:
        raw = await service.read_attachment(attachment)
        src = service.identify(attachment.filename, attachment.content_type, raw)
        targets = service.targets(src)
        state = {"u": attachment.url, "n": attachment.filename, "k": src, "z": len(raw),
                 "o": ORIGIN_CONVERT, "uid": interaction.user.id}
        if fmt:
            dst = resolve_format(fmt)
            if dst is None or dst not in targets:
                raise MediaError("bad_target", format=fmt[:20], source=formats.label_of(src),
                                 available=", ".join(formats.label_of(k) for k in targets))
            await run_conversion(interaction, state, dst, from_command=True)
            return
        token = await tc.save_state(interaction.client, STATE_KIND, state)
        view = build_convert_picker(filename=attachment.filename, src=src, size=len(raw),
                                    token=token, targets=targets, locale=locale, service=service)
        await interaction.edit_original_response(view=view)
    except MediaError as exc:
        await interaction.edit_original_response(
            view=tc.tool_error_card(_tool_error(exc, "convert"), locale), attachments=[])


def resolve_format(text: str) -> Optional[str]:
    """``"PDF"``, ``".pdf"``, ``"jpeg"``, ``"Markdown"`` -> format key."""
    needle = text.strip().lower().lstrip(".")
    if needle in formats.FORMATS:
        return needle
    if needle in formats.EXTENSION_TO_KEY:
        return formats.EXTENSION_TO_KEY[needle]
    for key, fmt in formats.FORMATS.items():
        if fmt.label.lower() == needle:
            return key
    return None


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


class ToolsMediaPersistence(BaseView):
    """Marker view: registers the dynamic items of /image and /convert at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(ImageResizeButton, ImageCompressButton, ImageConvertButton,
                              ImageBackgroundButton, ConvertSelect)


PERSISTENT_VIEWS: list = [ToolsMediaPersistence]
DYNAMIC_ITEM_CASES: list = [
    (ImageResizeButton, ()),
    (ImageCompressButton, ()),
    (ImageConvertButton, ()),
    (ImageBackgroundButton, ()),
    (ConvertSelect, ("abcdefghij", "image")),
]
