"""Which servers are *bump prospects* — pure decision code.

A bump prospect is a server that lists itself on at least one bump directory
(DISBOARD, DiscordL, French.gg… — :data:`bumpreminder.registry.BUMP_BOTS`) but
has nothing reminding it to bump: no third-party reminder bot, and not Moddy's
own Bump Reminder module. That server misses bump windows today, which is
exactly what Moddy's module fixes, so the team gets a card to reach out.

This module only answers the question from a set of member ids; the gateway
lookup and the module check live in ``services/bump_prospect_service.py``.

Adding a reminder bot is a one-entry change to :data:`REMINDER_BOTS`.

See docs/features/BUMP_PROSPECTS.md.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

from bumpreminder.registry import BUMP_BOTS, BumpBot

__all__ = ["REMINDER_BOTS", "WATCHED_IDS", "is_watched", "prospect_directories"]


#: Third-party bots that already remind a server to bump. A server holding any
#: of them is covered, so it is never a prospect. The most common ones; the
#: list is the team's, not an exhaustive census.
REMINDER_BOTS = frozenset({
    478321260481478677,
    1441619898756501574,
    735147814878969968,
})

#: Every member id the detection needs to look up in one server: the
#: directories and the reminder bots. One gateway query covers them all.
WATCHED_IDS: Tuple[int, ...] = tuple(
    [spec.app_id for spec in BUMP_BOTS] + sorted(REMINDER_BOTS))


def is_watched(user_id: int) -> bool:
    """Whether a member joining or leaving can change a server's verdict."""
    return user_id in _WATCHED_SET


_WATCHED_SET = frozenset(WATCHED_IDS)


def prospect_directories(member_ids: Iterable[int]) -> Optional[List[BumpBot]]:
    """The directories that make this server a prospect, or ``None``.

    ``member_ids`` are the watched ids actually present in the server. The
    answer is ``None`` when no directory is there, or when a reminder bot
    already covers the server; otherwise the directories, in registry order.
    Moddy's own module is the caller's half: it needs the database.
    """
    present = set(member_ids)
    if present & REMINDER_BOTS:
        return None
    directories = [spec for spec in BUMP_BOTS if spec.app_id in present]
    return directories or None
