"""
Views of the inspect utility tool (``/sauce``) — see docs/features/TOOLS_INSPECT.md.

Cards are built with ``utils/tool_card.py``. Every button is a link (the matches
and the reverse-search engines), so nothing here needs persistence.
"""

from __future__ import annotations

import re
from typing import List, Optional

from discord import ui

from cogs.error_handler import BaseView
from tools import sauce as sc
from utils import emojis as em
from utils.i18n import t
from utils.tool_card import (
    ACCENT_INFO, ACCENT_NEUTRAL, ToolCard, ToolError, escape_markdown, inline, link_button,
    link_row, requested_by, source_line, tool_error_card, truncate,
)


def _icon(name: str) -> str:
    """Icon constant of ``utils.emojis`` (empty until the artwork is uploaded)."""
    return getattr(em, name, "") or ""


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
        # Without SauceNAO only anime screenshots can be recognised: say so, instead
        # of letting an artwork or a photo look like "no match anywhere".
        anime_only = not result.used_saucenao and "saucenao" not in result.failed
        card.section(t("tools.sauce.nothing_title", locale=locale),
                     t("tools.sauce.nothing_body_anime_only" if anime_only
                       else "tools.sauce.nothing_body", locale=locale))

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


__all__ = ["build_sauce_card", "render_loading", "render_error", "ToolError"]
