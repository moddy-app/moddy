"""
Views of the inspect utility tools (``/exif``, ``/sauce``) — see
docs/features/TOOLS_INSPECT.md.

Cards are built with ``utils/tool_card.py``. The only interactive component is
the ``All tags`` button of ``/exif``: a :class:`discord.ui.DynamicItem` whose
``custom_id`` carries a short state token (the raw tag dump does not fit in a
``custom_id`` and cannot be re-derived from the ephemeral card), registered at
startup by :class:`InspectPersistence`. Everything else is a link button.
"""

from __future__ import annotations

import io
import re
from typing import List, Optional, Tuple

import discord
from discord import ui

from cogs.error_handler import BaseView
from tools import metadata as md
from tools import sauce as sc
from utils import emojis as em
from utils.i18n import i18n, t
from utils.tool_card import (
    ACCENT_INFO, ACCENT_NEUTRAL, ACCENT_WARNING, NO_MENTIONS, ToolCard, ToolError,
    button_emoji, detail, escape_markdown, expired_card, format_bytes, guarded, heading,
    inline, link_button, link_row, load_state, requested_by, save_state, source_line,
    tool_error_card, truncate, with_icon,
)

PERSISTENT_VIEWS: list = []
DYNAMIC_ITEM_CASES: list = []

#: State kind of the raw tag dump (``save_state``).
RAW_STATE_KIND = "exif_raw"
#: The stored dump is cut here (a few hundred KB of tags help nobody).
MAX_RAW_CHARS = 150_000
#: Budget of the field lines of one /exif card (the message cap is 4000).
EXIF_FIELDS_BUDGET = 2700
MAX_EXIF_FIELDS = 40


def _icon(name: str) -> str:
    """Icon constant of ``utils.emojis`` (empty until the artwork is uploaded)."""
    return getattr(em, name, "") or ""


# --------------------------------------------------------------------------- #
# /exif
# --------------------------------------------------------------------------- #


def _field_value(value: str, locale: str) -> str:
    """Booleans are stored as tokens by the reader; translate them here."""
    if value == md.YES:
        return t("tools.exif.yes", locale=locale)
    if value == md.NO:
        return t("tools.exif.no", locale=locale)
    return value


def _label(kind: str, key: str, locale: str) -> str:
    text = i18n.get(f"tools.exif.{kind}.{key}", locale=locale)
    return key.replace("_", " ").capitalize() if text.startswith("[") else text


def _kind_label(report: md.MetadataReport, locale: str) -> str:
    text = i18n.get(f"tools.exif.kinds.{report.kind}", locale=locale)
    name = report.kind if text.startswith("[") else text
    return f"{name} · {report.format}" if report.format else name


def exif_file_line(filename: str, report: md.MetadataReport, locale: str) -> str:
    safe, _ = truncate(md.clean(filename, 80) or "—", 80)
    return " · ".join((inline(safe), _kind_label(report, locale), format_bytes(report.size, locale)))


def build_exif_card(report: md.MetadataReport, filename: str, locale: str, *,
                    raw_token: Optional[str] = None) -> ui.LayoutView:
    """The /exif answer: what the file reveals, most sensitive first."""
    meaningful = report.meaningful
    accent = ACCENT_WARNING if report.has_location else ACCENT_INFO
    if not meaningful and not report.has_coordinates:
        accent = ACCENT_NEUTRAL

    card = ToolCard(icon=_icon("FRAME_INSPECT"), title=t("tools.exif.title", locale=locale),
                    accent=accent)
    card.text(exif_file_line(filename, report, locale))

    # Privacy verdict first: it is what people came to learn.
    hints: List[str] = []
    if report.has_location or report.has_coordinates:
        hints.append(t("tools.exif.hints.location", locale=locale))
    if report.has_person:
        hints.append(t("tools.exif.hints.person", locale=locale))
    if md.FLAG_AI in report.flags:
        hints.append(t("tools.exif.hints.ai", locale=locale))
    if md.FLAG_C2PA in report.flags:
        hints.append(t("tools.exif.hints.c2pa", locale=locale))
    if hints:
        card.text("\n".join(hints))

    if not meaningful:
        card.section(t("tools.exif.nothing_title", locale=locale),
                     t("tools.exif.nothing_body_image" if report.kind == md.KIND_IMAGE
                       else "tools.exif.nothing_body", locale=locale))
        # The bare image properties are still worth showing, compactly.
        props = [detail(_label("fields", f.key, locale), _field_value(f.value, locale))
                 for f in report.fields]
        if props:
            card.text("\n".join(props))
    else:
        budget = EXIF_FIELDS_BUDGET
        shown = 0
        hidden = 0
        for group, fields in report.groups():
            lines: List[str] = []
            for f in fields:
                line = detail(_label("fields", f.key, locale), _field_value(f.value, locale))
                if shown >= MAX_EXIF_FIELDS or budget - len(line) < 0:
                    hidden += 1
                    continue
                budget -= len(line) + 1
                shown += 1
                lines.append(line)
            if lines:
                icon = _icon("LOCATION_ON") if group == md.GROUP_LOCATION else None
                card.section(_label("groups", group, locale), "\n".join(lines), icon=icon)
        if hidden:
            card.hint(t("tools.exif.more_hidden", locale=locale, count=hidden))

    card.footer(source_line(locale, verb="computed_locally"))

    rows: List[ui.Item] = []
    buttons: List[ui.Button] = []
    if report.has_coordinates:
        buttons.append(link_button(t("tools.exif.open_map", locale=locale),
                                   md.osm_url(report.latitude, report.longitude),
                                   icon=_icon("OPENSTREETMAP")))
    row = link_row(*buttons)
    # The raw dump button lives in the same action row as the map link.
    if raw_token:
        if row is None:
            row = ui.ActionRow()
        row.add_item(ExifRawButton(raw_token, locale))
    if row is not None:
        rows.append(row)
    return card.build(view=BaseView(), rows=rows)


def raw_state_payload(report: md.MetadataReport, filename: str) -> Optional[dict]:
    """What ``save_state`` keeps for the ``All tags`` button (``None``: nothing to show)."""
    if not report.raw:
        return None
    text = md.raw_dump(report, filename)
    text, _ = truncate(text, MAX_RAW_CHARS)
    return {"name": md.clean(filename, 60) or "file", "text": text}


class ExifRawButton(ui.DynamicItem[ui.Button],
                    template=r"moddy:tools:exif:raw:(?P<token>[A-Za-z0-9]{10})"):
    """Sends every tag found as a ``.txt`` file, privately."""

    def __init__(self, token: str, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=t("tools.exif.all_tags", locale=locale)[:80],
            style=discord.ButtonStyle.secondary,
            custom_id=f"moddy:tools:exif:raw:{token}",
        ))
        self.token = token

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button,
                             match: re.Match):
        return cls(match["token"])

    @guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        payload = await load_state(interaction.client, RAW_STATE_KIND, self.token)
        if not payload:
            await interaction.response.send_message(view=expired_card(locale), ephemeral=True)
            return
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(payload.get("name", "file")))[:50] or "file"
        data = str(payload.get("text", "")).encode("utf-8")
        await interaction.response.send_message(
            content=t("tools.exif.raw_sent", locale=locale),
            file=discord.File(io.BytesIO(data), filename=f"metadata-{name}.txt"),
            ephemeral=True, allowed_mentions=NO_MENTIONS)


class InspectPersistence(BaseView):
    """Marker view: registers the dynamic items of the inspect tools at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(ExifRawButton)


PERSISTENT_VIEWS.append(InspectPersistence)
DYNAMIC_ITEM_CASES.append((ExifRawButton, ("abcDEF1234",)))


# --------------------------------------------------------------------------- #
# /sauce
# --------------------------------------------------------------------------- #

_ENGINE_ICONS = {"google_lens": "GOOGLE_LENS", "yandex": "YANDEX", "bing": "BING",
                 "tineye": "TINEYE"}
_ENGINE_NAMES = {"google_lens": "Google Lens", "yandex": "Yandex", "bing": "Bing",
                 "tineye": "TinEye"}
_SITE_LABEL_LIMIT = 28


def _percent(value: float) -> str:
    return f"{value:.0f}%"


def _confidence_key(similarity: float) -> str:
    return "high" if similarity >= 90 else "likely" if similarity >= 75 else "possible"


def _match_block(match: sc.SourceMatch, locale: str) -> str:
    title = escape_markdown(match.title) if match.title else t("tools.sauce.untitled", locale=locale)
    lines = [f"**{title}**"]
    if match.author:
        lines.append(t("tools.sauce.by_author", locale=locale, author=escape_markdown(match.author)))
    lines.append(f"{sc.similarity_bar(match.similarity)} `{_percent(match.similarity)}` · "
                 f"{t('tools.sauce.confidence.' + _confidence_key(match.similarity), locale=locale)}"
                 f" · {escape_markdown(match.site)}")
    return "\n".join(lines)


def _anime_block(anime: sc.AnimeMatch, locale: str) -> str:
    lines = [f"**{escape_markdown(anime.title)}**"]
    if anime.english and anime.english != anime.title:
        lines.append(f"-# {escape_markdown(anime.english)}")
    where: List[str] = []
    if anime.episode:
        where.append(t("tools.sauce.episode", locale=locale, episode=inline(anime.episode)))
    if anime.timestamp:
        where.append(t("tools.sauce.at", locale=locale, time=inline(anime.timestamp)))
    if where:
        lines.append(" · ".join(where))
    lines.append(f"{sc.similarity_bar(anime.similarity)} `{_percent(anime.similarity)}` · "
                 f"{t('tools.sauce.confidence.' + _confidence_key(anime.similarity), locale=locale)}")
    return "\n".join(lines)


def _short(text: str, limit: int = _SITE_LABEL_LIMIT) -> str:
    return truncate(text, limit)[0]


def build_sauce_card(result: sc.SauceResult, locale: str, *,
                     requester_id: Optional[int] = None) -> ui.LayoutView:
    """The /sauce answer: best source first, other matches, then manual search links."""
    found = result.found
    card = ToolCard(icon=_icon("IMAGE_SEARCH"), title=t("tools.sauce.title", locale=locale),
                    accent=ACCENT_INFO if found else ACCENT_NEUTRAL)
    buttons: List[ui.Button] = []

    if found:
        matches = list(result.matches)
        if result.best_is_anime:
            card.section(t("tools.sauce.anime_title", locale=locale),
                         _anime_block(result.anime, locale), icon=_icon("MOVIE") or None)
            if result.anime.anilist_url:
                buttons.append(link_button("AniList", result.anime.anilist_url,
                                           icon=_icon("ANILIST")))
            if matches:
                card.section(t("tools.sauce.also_title", locale=locale),
                             _match_block(matches[0], locale))
                others = matches[1:1 + sc.MAX_OTHERS]
            else:
                others = []
            best = matches[0] if matches else None
        else:
            best = matches[0]
            card.section(t("tools.sauce.best_title", locale=locale), _match_block(best, locale))
            others = matches[1:1 + sc.MAX_OTHERS]
            if result.anime is not None:
                card.section(t("tools.sauce.anime_title", locale=locale),
                             _anime_block(result.anime, locale), icon=_icon("MOVIE") or None)
                if result.anime.anilist_url:
                    buttons.append(link_button("AniList", result.anime.anilist_url,
                                               icon=_icon("ANILIST")))
        if best is not None:
            for url in best.urls[:3]:
                host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
                buttons.insert(0, link_button(_short(host or best.site), url))
        if others:
            lines = []
            for m in others:
                label = escape_markdown(_short(m.title or m.site, 40))
                target = f"[{label}]({m.url})" if m.url else f"**{label}**"
                lines.append(f"-# {_percent(m.similarity)} · {target} · {escape_markdown(m.site)}")
            card.text("\n".join(lines))
    else:
        card.section(t("tools.sauce.nothing_title", locale=locale),
                     t("tools.sauce.nothing_body", locale=locale))

    if result.failed:
        names = ", ".join({"saucenao": "SauceNAO", "tracemoe": "trace.moe"}.get(n, n)
                          for n in result.failed)
        card.hint(t("tools.sauce.partial", locale=locale, providers=names))

    names = []
    if result.used_saucenao:
        names.append((_icon("SAUCENAO"), "SauceNAO"))
    if result.used_tracemoe:
        names.append((_icon("TRACEMOE"), "trace.moe"))
    card.footer(source_line(locale, *names, verb="found_by") if names else None,
                requested_by(locale, requester_id))

    rows: List[ui.Item] = []
    first = link_row(*buttons)
    if first is not None:
        rows.append(first)
    engines = [link_button(_ENGINE_NAMES[key], url, icon=_icon(_ENGINE_ICONS[key]))
               for key, url in sc.search_engine_urls(result.image_url)]
    second = link_row(*engines)
    if second is not None:
        rows.append(second)
    return card.build(view=BaseView(), rows=rows)


def render_loading(locale: str, tool: str) -> ui.LayoutView:
    from utils.tool_card import loading_card
    return loading_card(locale, t(f"tools.{tool}.loading", locale=locale))


def render_error(error, locale: str, tool: str, **params) -> ui.LayoutView:
    return tool_error_card(error, locale, tool=tool, **params)


__all__ = ["PERSISTENT_VIEWS", "DYNAMIC_ITEM_CASES", "build_exif_card", "build_sauce_card",
           "raw_state_payload", "ExifRawButton", "InspectPersistence", "render_loading",
           "render_error", "ToolError", "heading", "button_emoji", "save_state"]
