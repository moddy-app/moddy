"""
AI detector UI — the verdict card, the details panel and the persistent
"More information" button posted under it.

Verdict card
------------
Exactly the shape the product asked for: one container, a ``TextDisplay``
with the title and the sentence, a visible ``Separator`` where the templates
have their ``---`` line, then a ``TextDisplay`` with the ``-#`` line. The
"More information" button sits *outside* the container, in its own row.

Every value comes from the privacy whitelist of ``aidetector/manifest.py``
(issuer organisation, tool, camera model, dates, retouch names): no GPS, no
author, no identity assertion ever reaches a card.

Persistence
-----------
The button is a :class:`discord.ui.DynamicItem` whose ``custom_id`` carries
the channel, the image message and the attachment id, so it keeps working
after a restart with no stored state (registered by
:class:`AiDetectorPersistence`). The details are re-derived on click — from the
service's short cache, or by analysing the original attachment again.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import List, Optional, Tuple

import discord
from discord import ui

from aidetector import analysis as an
from aidetector import manifest as mf
from cogs.error_handler import BaseView
from utils.emojis import AI_SHIELD, INFO, ROBOT_WORKING
from utils.i18n import i18n, t

logger = logging.getLogger("moddy.ai_detector_views")

#: Accent of every detector card (the blue of the product mock-up).
ACCENT = 0x0069FE
LEARN_MORE_URL = "https://moddy.app/ai-detector"
NO_MENTIONS = discord.AllowedMentions.none()

_CID_PREFIX = "moddy:aidet:info"
#: History lines shown in the details panel (the rest is summarised).
MAX_HISTORY_LINES = 10

_VOWELS = "aeiouyhàâäéèêëîïôöùûüAEIOUYHÀÂÄÉÈÊËÎÏÔÖÙÛÜ"

#: Reasons rendered from ``ai_detector.errors`` (the rest: ``ai_detector.reasons``).
_ERROR_REASONS = frozenset({
    "failed", "unavailable", "gone", "no_image", "unsupported", "too_large",
    "download_failed", "bad_link", "other_server",
})


def _guarded(callback):
    """Route a dynamic-item callback error to the central handler (no live view)."""
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as e:  # noqa: BLE001
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, e, self.__class__.__name__)
    return wrapper


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #

def discord_timestamp(when: Optional[datetime], style: str = "f") -> Optional[str]:
    """``<t:unix:f>`` — Discord renders it in each reader's own language and
    time zone, which a server-wide card cannot do by itself."""
    if when is None:
        return None
    return f"<t:{int(when.timestamp())}:{style}>"


def of_issuer(issuer: str, locale: str) -> str:
    """"de X" with the French elision (``d'OpenAI``, ``de Google``).

    Only French templates use ``{of_issuer}``; the others put their own
    preposition in front of ``{issuer}``.
    """
    if locale.startswith("fr"):
        return f"d'{issuer}" if issuer[:1] in _VOWELS else f"de {issuer}"
    return issuer


def action_label(action: str, locale: str) -> str:
    """``c2pa.color_adjustments`` -> "couleurs" (raw name when unknown)."""
    key = action.removeprefix("c2pa.").replace(".", "_")
    text = i18n.get(f"ai_detector.actions.{key}", locale=locale)
    return f"`{action}`" if text.startswith("[") else text


def source_type_label(source: Optional[str], locale: str) -> Optional[str]:
    if not source:
        return None
    text = i18n.get(f"ai_detector.source_types.{source}", locale=locale)
    return f"`{source}`" if text.startswith("[") else text


def _tool_part(tool: Optional[str], issuer: Optional[str], locale: str) -> str:
    """``**ChatGPT** (OpenAI)``, or just one bold name when they coincide."""
    tool = tool or issuer or t("ai_detector.unknown_tool", locale=locale)
    if issuer and issuer.lower() != tool.lower():
        return t("ai_detector.tool_with_issuer", locale=locale, tool=tool, issuer=issuer)
    return t("ai_detector.tool_only", locale=locale, tool=tool)


def verdict_texts(result: an.Analysis, locale: str) -> Optional[Tuple[str, str, str]]:
    """``(title, sentence, footer)`` of the verdict card, or None."""
    kind = result.kind
    if kind is None:
        return None
    base = f"ai_detector.verdicts.{kind}"

    if kind == mf.KIND_UNSIGNED:
        generation = result.generation
        tool = generation.tool if generation else t("ai_detector.unknown_tool", locale=locale)
        return (
            t(f"{base}.title", locale=locale),
            t(f"{base}.body", locale=locale, tool=tool),
            t(f"{base}.footer", locale=locale, url=LEARN_MORE_URL),
        )

    verdict = result.verdict
    issuer = verdict.issuer or t("ai_detector.unknown_issuer", locale=locale)
    params = {
        "tool_part": _tool_part(verdict.tool, verdict.issuer, locale),
        "tool": verdict.tool or "",
        "issuer": issuer,
        "of_issuer": of_issuer(issuer, locale),
        "device": verdict.device or t("ai_detector.unknown_device", locale=locale),
        "date": discord_timestamp(verdict.when) or "",
        "actions": ", ".join(action_label(a, locale) for a in verdict.edits),
        "url": LEARN_MORE_URL,
    }
    body_key = "body"
    if kind in (mf.KIND_AI_FULL, mf.KIND_PHOTO) and verdict.when is None:
        body_key = "body_no_date"
    elif kind == mf.KIND_PHOTO_EDITED and not verdict.tool:
        body_key = "body_no_tool"
    return (
        t(f"{base}.title", locale=locale),
        t(f"{base}.{body_key}", locale=locale, **params),
        t(f"{base}.footer", locale=locale, **params),
    )


def _verdict_container(result: an.Analysis, locale: str) -> Optional[ui.Container]:
    texts = verdict_texts(result, locale)
    if texts is None:
        return None
    title, body, footer = texts
    container = ui.Container(accent_colour=discord.Colour(ACCENT))
    container.add_item(ui.TextDisplay(f"### {AI_SHIELD} **{title}**\n{body}"))
    container.add_item(ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
    container.add_item(ui.TextDisplay(f"-# {footer}"))
    return container


# --------------------------------------------------------------------------- #
# Cards
# --------------------------------------------------------------------------- #

def build_verdict_view(result: an.Analysis, locale: str, *,
                       button: Optional[Tuple[int, int, int]] = None) -> Optional[ui.LayoutView]:
    """The public card. ``button`` = ``(channel_id, message_id, attachment_id)``
    adds the persistent "More information" button below the container."""
    container = _verdict_container(result, locale)
    if container is None:
        return None
    view = BaseView()  # timeout=None
    view.add_item(container)
    if button is not None:
        view.add_item(ui.ActionRow(AiDetectorDetailsButton(*button, locale=locale)))
    return view


def render_loading_card(locale: str) -> ui.LayoutView:
    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=discord.Colour(ACCENT))
    container.add_item(ui.TextDisplay(f"{ROBOT_WORKING} **{t('ai_detector.loading', locale=locale)}**"))
    view.add_item(container)
    return view


def render_error_card(reason: str, locale: str) -> ui.LayoutView:
    from utils.components_v2 import create_error_message
    # A failure (``errors``) or an analysis outcome without verdict (``reasons``).
    section = "errors" if reason in _ERROR_REASONS else "reasons"
    description = i18n.get(f"ai_detector.{section}.{reason}", locale=locale)
    if description.startswith("["):
        description = t("ai_detector.errors.failed", locale=locale)
    return create_error_message(t("ai_detector.errors.title", locale=locale), description)


def _signature_lines(summary: mf.ManifestSummary, result: an.Analysis, locale: str) -> List[str]:
    td = lambda key, **kw: t(f"ai_detector.details.{key}", locale=locale, **kw)  # noqa: E731
    active = summary.active
    lines = [f"**{td('signature_title')}**"]
    if active and active.issuer:
        lines.append(f"- {td('issuer')} `{active.issuer}`")
    lines.append(f"- {td('validation')} `{result.validation_state}` · {td('validation_trusted')}")
    if active and active.signature_alg:
        lines.append(f"- {td('algorithm')} `{active.signature_alg}`")
    if active and active.signed_at:
        lines.append(f"- {td('signed_at')} {discord_timestamp(active.signed_at)}")
    if active and active.generator:
        version = f" `{active.generator_version}`" if active.generator_version else ""
        lines.append(f"- {td('generator')} `{active.generator}`{version}")
    device = next((m.device for m in summary.manifests if m.device), None)
    if device:
        lines.append(f"- {td('device')} `{device}`")
    return lines


def _history_lines(summary: mf.ManifestSummary, locale: str) -> List[str]:
    td = lambda key, **kw: t(f"ai_detector.details.{key}", locale=locale, **kw)  # noqa: E731
    # Oldest first: ingredients (deeper) before the active manifest.
    records = sorted(summary.actions, key=lambda r: -r.depth)
    lines = [f"**{td('history_title')}**"]
    for record in records[:MAX_HISTORY_LINES]:
        parts = [action_label(record.action, locale)]
        source = source_type_label(record.source_type, locale)
        if source:
            parts.append(source)
        if record.software:
            parts.append(f"`{record.software}`")
        stamp = discord_timestamp(record.when, "d")
        if stamp:
            parts.append(stamp)
        lines.append("- " + " · ".join(parts))
    if len(records) > MAX_HISTORY_LINES:
        lines.append(f"-# {td('history_more', count=len(records) - MAX_HISTORY_LINES)}")
    if not records:
        lines.append(f"-# {td('history_empty')}")
    lines.append(f"-# {td('chain', manifests=len(summary.manifests), ingredients=summary.ingredient_count)}")
    return lines


def _generation_lines(result: an.Analysis, locale: str) -> List[str]:
    td = lambda key, **kw: t(f"ai_detector.details.{key}", locale=locale, **kw)  # noqa: E731
    generation = result.generation
    lines = [f"**{td('metadata_title')}**",
             f"- {td('generator')} `{generation.tool}`",
             f"- {td('fields')} " + ", ".join(f"`{k}`" for k in generation.keywords)]
    for name, value in generation.settings.items():
        lines.append(f"- {td('setting', name=name, value=value[:100])}")
    lines.append(f"-# {td('prompt_hidden')}")
    return lines


def details_containers(result: an.Analysis, locale: str) -> List[ui.Container]:
    """The facts behind a verdict, then how the check works and how far to trust it."""
    td = lambda key, **kw: t(f"ai_detector.details.{key}", locale=locale, **kw)  # noqa: E731
    facts = ui.Container(accent_colour=discord.Colour(ACCENT))
    facts.add_item(ui.TextDisplay(f"### {INFO} {td('title')}"))

    kind = result.kind
    from_c2pa = kind is not None and kind != mf.KIND_UNSIGNED and result.summary is not None
    if kind is not None:
        title = t(f"ai_detector.verdicts.{kind}.title", locale=locale)
        source = td("source_c2pa" if from_c2pa else "source_unsigned")
        facts.add_item(ui.TextDisplay(
            f"**{td('verdict')}** {title}\n**{td('source')}** {source}"))
    facts.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

    if from_c2pa:
        facts.add_item(ui.TextDisplay("\n".join(_signature_lines(result.summary, result, locale))))
        facts.add_item(ui.TextDisplay("\n".join(_history_lines(result.summary, locale))))
    elif result.generation is not None:
        facts.add_item(ui.TextDisplay("\n".join(_generation_lines(result, locale))))
    facts.add_item(ui.TextDisplay(f"-# {td('privacy')}"))

    about = ui.Container(accent_colour=discord.Colour(ACCENT))
    about.add_item(ui.TextDisplay(f"**{td('how_title')}**\n{td('how_body')}"))
    about.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    reliability = td("reliability_c2pa" if from_c2pa or kind is None else "reliability_unsigned")
    about.add_item(ui.TextDisplay(f"**{td('reliability_title')}**\n{reliability}"))
    if kind in (mf.KIND_PHOTO, mf.KIND_PHOTO_EDITED):
        about.add_item(ui.TextDisplay(f"-# {td('reliability_photo')}"))
    about.add_item(ui.TextDisplay(f"-# {td('absence')}"))
    return [facts, about]


def _learn_more_row(locale: str) -> ui.ActionRow:
    return ui.ActionRow(ui.Button(
        label=t("ai_detector.learn_more", locale=locale),
        style=discord.ButtonStyle.link, url=LEARN_MORE_URL,
    ))


def build_details_view(result: an.Analysis, locale: str) -> ui.LayoutView:
    """Ephemeral answer to the "More information" button."""
    view = ui.LayoutView(timeout=None)
    for container in details_containers(result, locale):
        view.add_item(container)
    view.add_item(_learn_more_row(locale))
    return view


def build_check_view(result: an.Analysis, locale: str) -> ui.LayoutView:
    """``/ai-check``: the verdict card (or why there is none) + the details."""
    view = ui.LayoutView(timeout=None)
    container = _verdict_container(result, locale)
    if container is not None:
        view.add_item(container)
        for extra in details_containers(result, locale):
            view.add_item(extra)
    else:
        none = ui.Container(accent_colour=discord.Colour(ACCENT))
        reason = result.reason
        text = i18n.get(f"ai_detector.reasons.{reason}", locale=locale)
        if text.startswith("["):
            text = t("ai_detector.reasons.no_data", locale=locale)
        none.add_item(ui.TextDisplay(
            f"### {AI_SHIELD} **{t('ai_detector.no_verdict_title', locale=locale)}**\n{text}"))
        view.add_item(none)
        view.add_item(details_containers(result, locale)[1])
    view.add_item(_learn_more_row(locale))
    return view


# --------------------------------------------------------------------------- #
# Persistent "More information" button
# --------------------------------------------------------------------------- #

class AiDetectorDetailsButton(
    ui.DynamicItem[ui.Button],
    template=rf"{_CID_PREFIX}:(?P<channel_id>\d+):(?P<message_id>\d+):(?P<attachment_id>\d+)",
):
    """Shows the details of the verdict to whoever clicks, privately.

    Public by design: anyone who can read the card may want to know where its
    claim comes from. Nothing it shows goes beyond the card's own sources, and
    the click must come from the channel the image was posted in.
    """

    def __init__(self, channel_id: int, message_id: int, attachment_id: int,
                 locale: str = "en-US"):
        super().__init__(ui.Button(
            label=t("ai_detector.details_button", locale=locale)[:80],
            style=discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(INFO),
            custom_id=f"{_CID_PREFIX}:{channel_id}:{message_id}:{attachment_id}",
        ))
        self.channel_id = channel_id
        self.message_id = message_id
        self.attachment_id = attachment_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button,
                             match: re.Match):
        return cls(int(match["channel_id"]), int(match["message_id"]),
                   int(match["attachment_id"]))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)

        service = getattr(interaction.client, "ai_detector", None)
        if service is None or interaction.channel_id != self.channel_id:
            await interaction.followup.send(view=render_error_card("unavailable", locale),
                                            ephemeral=True)
            return

        result = service.cached(self.attachment_id)
        if result is None:
            attachment = await self._fetch_attachment(interaction)
            if attachment is None:
                await interaction.followup.send(view=render_error_card("gone", locale),
                                                ephemeral=True)
                return
            result = await service.analyze_attachment(attachment)

        if result.kind is None:
            await interaction.followup.send(view=render_error_card(result.reason, locale),
                                            ephemeral=True)
            return
        await interaction.followup.send(view=build_details_view(result, locale),
                                        ephemeral=True, allowed_mentions=NO_MENTIONS)

    async def _fetch_attachment(self, interaction: discord.Interaction) -> Optional[discord.Attachment]:
        channel = interaction.channel
        if channel is None or not hasattr(channel, "fetch_message"):
            return None
        try:
            message = await channel.fetch_message(self.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
        return next((a for a in message.attachments if a.id == self.attachment_id), None)


class AiDetectorPersistence(BaseView):
    """Marker view: registers the details button's dynamic item at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(AiDetectorDetailsButton)
