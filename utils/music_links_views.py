"""
Views of the music links module and ``/music link`` — see docs/features/MUSIC_LINKS.md.

Two cards, one look:

* the **automatic reply** under a Spotify link: one dim line (``♪ Title —
  Artist``) and the link buttons, nothing else — it is read by a whole channel,
  so it stays discreet;
* the **command card** (``/music link``): the cover, title, artist, album, year,
  duration, the link buttons and the "found by" footer, in the standard tool
  card anatomy (utils/tool_card.py).

Both carry only *link* buttons: nothing here needs persistence. The one
persistent view of the feature is the ``/config`` screen, listed in
``PERSISTENT_VIEWS`` below so it is registered through
utils/tool_views_registry.py (like every tool view).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import discord
from discord import ui

from config import COLORS
from tools import music as mu
from utils.emojis import APPLE_MUSIC, DEEZER, MUSIC_NOTE, SPOTIFY, YOUTUBE_MUSIC
from utils.i18n import t
from utils.tool_card import (
    ACCENT_INFO, ToolCard, escape_markdown, link_button, link_row, requested_by,
    source_line, truncate, with_icon,
)

#: Brand logos (empty until the artwork is uploaded: buttons then show text only).
PLATFORM_ICONS: Dict[str, str] = {
    mu.PLATFORM_SPOTIFY: SPOTIFY,
    mu.PLATFORM_DEEZER: DEEZER,
    mu.PLATFORM_APPLE: APPLE_MUSIC,
    mu.PLATFORM_YOUTUBE: YOUTUBE_MUSIC,
}

_NAME_LIMIT = 100


def platform_label(platform: str, locale: str) -> str:
    """Brand name of a platform (``modules.music_links.platforms.*``)."""
    return t(f"modules.music_links.platforms.{platform}", locale=locale)


def _link_buttons(info: mu.MusicInfo, platforms: Sequence[str], locale: str,
                  *, spotify: bool = False) -> List[ui.Button]:
    buttons: List[ui.Button] = []
    order = ([mu.PLATFORM_SPOTIFY] if spotify else []) + list(mu.PLATFORMS)
    for platform in order:
        url = info.spotify_url if platform == mu.PLATFORM_SPOTIFY else info.links.get(platform)
        if not url or (platform != mu.PLATFORM_SPOTIFY and platform not in platforms):
            continue
        buttons.append(link_button(platform_label(platform, locale), url,
                                   icon=PLATFORM_ICONS.get(platform)))
    return buttons


def _clip(text: str) -> str:
    return escape_markdown(truncate(text, _NAME_LIMIT)[0])


# --------------------------------------------------------------------------- #
# Automatic reply (module)
# --------------------------------------------------------------------------- #


def reply_line(info: mu.MusicInfo, locale: str) -> str:
    """``-# ♪ **Title** — Artist`` (album / artist get a small tag)."""
    title, artist = _clip(info.title), _clip(info.artist)
    if info.kind == mu.KIND_ARTIST:
        text = f"**{title}** · {t('modules.music_links.reply.artist_tag', locale=locale)}"
    elif info.kind == mu.KIND_ALBUM:
        text = (f"**{title}** — {artist} · "
                f"{t('modules.music_links.reply.album_tag', locale=locale)}")
    else:
        text = f"**{title}** — {artist}"
    return "-# " + with_icon(MUSIC_NOTE, text)


def build_reply_view(infos: Sequence[mu.MusicInfo], platforms: Sequence[str],
                     locale: str) -> Optional[ui.LayoutView]:
    """The compact reply for up to three Spotify items, or ``None`` when no item
    is worth a reply with these platforms (see :func:`tools.music.worth_posting`)."""
    view = ui.LayoutView(timeout=None)
    shown = 0
    for info in infos:
        if not mu.worth_posting(info, platforms):
            continue
        row = link_row(*_link_buttons(info, platforms, locale))
        if row is None:
            continue
        container = ui.Container(accent_colour=discord.Colour(COLORS["neutral"]))
        container.add_item(ui.TextDisplay(reply_line(info, locale)))
        container.add_item(row)
        view.add_item(container)
        shown += 1
    return view if shown else None


# --------------------------------------------------------------------------- #
# Command card (/music link)
# --------------------------------------------------------------------------- #


def build_link_card(info: mu.MusicInfo, locale: str, *,
                    requested_by_id: Optional[int] = None) -> ui.LayoutView:
    """The richer card of ``/music link``: cover, details, every link."""
    card = ToolCard(title=t(f"tools.music.title.{info.kind}", locale=locale),
                    icon=MUSIC_NOTE, accent=ACCENT_INFO)

    head = f"**{_clip(info.title)}**"
    if info.kind != mu.KIND_ARTIST:
        head += f"\n{_clip(info.artist)}"
    lines: List[str] = []
    if info.album:
        lines.append(f"**{t('tools.music.fields.album', locale=locale)}** · `{info.album.replace('`', 'ˋ')}`")
    if info.year:
        lines.append(f"**{t('tools.music.fields.year', locale=locale)}** · `{info.year}`")
    duration = mu.format_duration(info.duration)
    if duration:
        lines.append(f"**{t('tools.music.fields.duration', locale=locale)}** · `{duration}`")

    texts = [ui.TextDisplay(head)]
    if lines:
        texts.append(ui.TextDisplay("\n".join(lines)))
    if info.cover:
        card.add(ui.Section(*texts, accessory=ui.Thumbnail(info.cover)))
    else:
        for text in texts:
            card.add(text)

    if not info.direct_links:
        card.hint(t("tools.music.no_match", locale=locale))

    used = [(SPOTIFY, platform_label(mu.PLATFORM_SPOTIFY, locale))]
    if mu.PLATFORM_DEEZER in info.links:
        used.append((DEEZER, platform_label(mu.PLATFORM_DEEZER, locale)))
    if mu.PLATFORM_APPLE in info.links:
        used.append((APPLE_MUSIC, platform_label(mu.PLATFORM_APPLE, locale)))
    card.footer(source_line(locale, *used, verb="found_by"),
                requested_by(locale, requested_by_id))

    row = link_row(*_link_buttons(info, mu.PLATFORMS, locale, spotify=True))
    return card.build(rows=[row] if row else [])


# --------------------------------------------------------------------------- #
# Persistence registry (utils/tool_views_registry.py)
# --------------------------------------------------------------------------- #


def __getattr__(name: str):
    """Lazy ``PERSISTENT_VIEWS``: the config screen imports the module, which
    imports this file — resolving the list on demand keeps that cycle out."""
    if name == "PERSISTENT_VIEWS":
        from modules.configs.music_links_config import MusicLinksConfigView
        return [MusicLinksConfigView]
    raise AttributeError(name)


DYNAMIC_ITEM_CASES: list = []
