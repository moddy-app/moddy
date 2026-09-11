"""
Ticket handling statistics — the cards behind ``/ticket stats``.

Two shapes: the whole team, or one person. Both are read straight from
``ticket_ratings`` and ``ticket_transcripts`` with SQL aggregates rather than
from ``bot.stats``, which exists for bounded counters and deliberately refuses
to carry a user id as a dimension. Ratings are named data about named people;
they belong in their own table, queried on demand.

Volume is counted from transcripts, not from the ``tickets`` table: a closed
ticket whose channel was tidied away still counts towards the work its staff
did, and ``tickets`` rows do not survive that.

See docs/TICKETS.md.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import discord
from discord import ui

from utils.emojis import INFO, STAR, TICKET
from utils.i18n import t
from utils.ticket_rating_views import score_label

DEFAULT_STATS_DAYS = 30
MAX_STATS_DAYS = 365

# How many people the leaderboard names, and how many individual reviews one
# person's card shows. Both are capped by what fits in a Discord message.
LEADERBOARD_SIZE = 15
INDIVIDUAL_RATINGS_SHOWN = 10

# Below this many ratings an average says more about luck than about service,
# so it is shown with a caveat rather than ranked on.
MIN_RATINGS_FOR_AVERAGE = 3

_ACCENT = 0x5865F2


def _average(summary: Dict[str, Any]) -> Optional[float]:
    average = summary.get('average')
    return round(float(average), 2) if average is not None else None


def _breakdown(summary: Dict[str, Any], locale: str) -> str:
    """The 5→1 histogram, one line per score, blank scores included.

    Showing the empty rows is the point: "no one ever rated this 1" is
    information, and a histogram with holes in it is unreadable.
    """
    lines = []
    for score in (5, 4, 3, 2, 1):
        count = int(summary.get(f's{score}') or 0)
        lines.append(f"{STAR * score} `{score_label(score, locale)}` — `{count}`")
    return "\n".join(lines)


def build_staff_stats_card(staff: discord.abc.User, summary: Dict[str, Any],
                           recent: List[Dict[str, Any]], handled: int, *,
                           days: int, locale: str) -> ui.LayoutView:
    """One staffer: their volume, their average, and their latest reviews."""
    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=discord.Colour(_ACCENT))
    container.add_item(ui.TextDisplay(
        f"### {TICKET} {t('modules.tickets.stats.staff_title', locale=locale)}"))

    ratings = int(summary.get('ratings') or 0)
    average = _average(summary)
    lines = [
        f"**{t('modules.tickets.stats.member', locale=locale)}** "
        f"{staff.mention} (`{staff.id}`)",
        f"**{t('modules.tickets.stats.window', locale=locale)}** "
        f"`{days}` {t('modules.tickets.stats.days', locale=locale)}",
        f"**{t('modules.tickets.stats.handled', locale=locale)}** `{handled}`",
        f"**{t('modules.tickets.stats.ratings', locale=locale)}** `{ratings}`",
    ]
    if average is not None:
        line = f"**{t('modules.tickets.stats.average', locale=locale)}** `{average}/5`"
        if ratings < MIN_RATINGS_FOR_AVERAGE:
            line += f" -# {t('modules.tickets.stats.few_ratings', locale=locale)}"
        lines.append(line)
    container.add_item(ui.TextDisplay("\n".join(lines)))

    if ratings:
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(_breakdown(summary, locale)))

    if recent:
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(
            f"**{t('modules.tickets.stats.recent', locale=locale)}**"))
        for rating in recent:
            score = int(rating['score'])
            stamp = discord.utils.format_dt(rating['created_at'], 'R')
            entry = (f"{STAR * score} `#{rating['ticket_number']}` · "
                     f"`{score_label(score, locale)}` · {stamp}")
            if rating.get('comment'):
                entry += f"\n-# {rating['comment'][:300]}"
            container.add_item(ui.TextDisplay(entry))
    elif not ratings:
        container.add_item(ui.TextDisplay(
            f"{INFO} {t('modules.tickets.stats.no_ratings', locale=locale)}"))

    view.add_item(container)
    return view


def build_stats_leaderboard_card(guild: discord.Guild,
                                 leaderboard: List[Dict[str, Any]],
                                 handled: Dict[int, int], *,
                                 days: int, locale: str) -> ui.LayoutView:
    """The whole team, ordered by how many tickets they were rated on.

    By volume, not by average: a single five-star review must not outrank
    somebody who handled fifty tickets. The average is shown next to it, with a
    caveat under :data:`MIN_RATINGS_FOR_AVERAGE` reviews.
    """
    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=discord.Colour(_ACCENT))
    container.add_item(ui.TextDisplay(
        f"### {TICKET} {t('modules.tickets.stats.team_title', locale=locale)}"))
    container.add_item(ui.TextDisplay(
        f"**{t('modules.tickets.stats.window', locale=locale)}** "
        f"`{days}` {t('modules.tickets.stats.days', locale=locale)}"))

    if not leaderboard:
        container.add_item(ui.TextDisplay(
            f"{INFO} {t('modules.tickets.stats.no_ratings', locale=locale)}"))
        view.add_item(container)
        return view

    container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    lines = []
    for row in leaderboard[:LEADERBOARD_SIZE]:
        staff_id = row['rated_staff_id']
        ratings = int(row.get('ratings') or 0)
        average = _average(row)
        entry = (f"<@{staff_id}> — "
                 f"`{handled.get(staff_id, 0)}` "
                 f"{t('modules.tickets.stats.handled_short', locale=locale)} · "
                 f"`{ratings}` {t('modules.tickets.stats.ratings_short', locale=locale)}")
        if average is not None:
            entry += f" · {STAR} `{average}/5`"
            if ratings < MIN_RATINGS_FOR_AVERAGE:
                entry += " *"
        negative = int(row.get('negative') or 0)
        if negative:
            entry += (f" · `{negative}` "
                      f"{t('modules.tickets.stats.negative_short', locale=locale)}")
        lines.append(entry)
    container.add_item(ui.TextDisplay("\n".join(lines)))
    if any(int(r.get('ratings') or 0) < MIN_RATINGS_FOR_AVERAGE for r in leaderboard):
        container.add_item(ui.TextDisplay(
            f"-# * {t('modules.tickets.stats.few_ratings', locale=locale)}"))

    view.add_item(container)
    return view
