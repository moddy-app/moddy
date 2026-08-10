"""
Global sanctions — Moddy-team sanctions, resolved from the cases system.

A **global sanction** is issued by the Moddy team against a *user* or a
*server* that breaks Moddy's own rules. It is stored like every other sanction:
a case of type ``global`` (scope ``platform``) carrying one or more sanctions.
There is **no attribute** involved any more — ``BLACKLISTED`` is legacy and is
migrated into a global ``ban`` case at startup
(``ModerationRepository.migrate_legacy_blacklist_attributes``).

Three levels exist, mapped onto the existing ``sanction_action`` enum so no
schema change is needed:

===============  =====================  ==========================================
Level            ``sanction_action``    Effect
===============  =====================  ==========================================
``warn``         ``warn``               Informational only — nothing is blocked.
``limited``      ``restrict``           Reduced service: no premium, no **new**
                                        module can be configured, and the
                                        automod AI ignores the server entirely.
``suspended``    ``ban``                No access to any Moddy service at all
                                        (replaces the old blacklist).
===============  =====================  ==========================================

Levels are ordered: a subject with several active global sanctions is at the
**highest** level it holds.

Global sanctions may be **temporary** — ``restrict`` and ``ban`` accept an
``expires_at`` (``TEMPORARY_ACTIONS``), swept by ``bot.case_expiry`` like any
other sanction. An expired sanction stops counting immediately: the resolver
filters on the expiry in SQL and never caches a level past it.

Reads are cached in memory on the bot (short TTL) because they sit on hot paths
(every interaction, every message the automod would look at). The cache is
invalidated explicitly whenever the bot or the backend mutates a global case
(:func:`invalidate`, ``moddy:blacklist:updates``).

This module is deliberately dependency-light (no discord import) so it can be
used from the bot, the modules, the internal API and any worker.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Iterable, Optional, Tuple

logger = logging.getLogger('moddy.global_sanctions')

#: How long a resolved level stays cached, in seconds. Kept short so a
#: sanction lifted straight in DB (dashboard) self-heals even if the
#: invalidation event is missed.
CACHE_TTL = 120

SUBJECT_USER = "discord_user"
SUBJECT_GUILD = "discord_guild"

#: Case type holding global (Moddy-team) sanctions.
CASE_TYPE = "global"


class GlobalLevel(str, Enum):
    """Severity of the global sanction a subject currently holds."""
    NONE = "none"
    WARN = "warn"
    LIMITED = "limited"
    SUSPENDED = "suspended"


#: Severity ordering — the resolved level is the max of the active sanctions.
_ORDER: Dict[GlobalLevel, int] = {
    GlobalLevel.NONE: 0,
    GlobalLevel.WARN: 1,
    GlobalLevel.LIMITED: 2,
    GlobalLevel.SUSPENDED: 3,
}

#: ``sanction_action`` value -> level. Actions absent here are not global
#: sanction levels and are ignored when resolving.
ACTION_TO_LEVEL: Dict[str, GlobalLevel] = {
    "warn": GlobalLevel.WARN,
    "restrict": GlobalLevel.LIMITED,
    "ban": GlobalLevel.SUSPENDED,
}

#: level -> ``sanction_action`` (for UIs that speak levels).
LEVEL_TO_ACTION: Dict[GlobalLevel, str] = {
    GlobalLevel.WARN: "warn",
    GlobalLevel.LIMITED: "restrict",
    GlobalLevel.SUSPENDED: "ban",
}


# --------------------------------------------------------------------------- #
# Presentation — the accent bar and icon of each level
# --------------------------------------------------------------------------- #

#: Accent colour of a level, from mildest to heaviest. Panels about a global
#: sanction take the accent of the level they are about, so the severity reads
#: at a glance before a single word is parsed.
LEVEL_ACCENTS: Dict[GlobalLevel, int] = {
    GlobalLevel.NONE: 0x99AAB5,       # neutral grey
    GlobalLevel.WARN: 0xFEE75C,       # yellow
    GlobalLevel.LIMITED: 0xF28500,    # orange
    GlobalLevel.SUSPENDED: 0xDA3E27,  # red
}


def level_accent(level: GlobalLevel) -> int:
    """Accent colour (hex int) for a global sanction level."""
    return LEVEL_ACCENTS.get(level, LEVEL_ACCENTS[GlobalLevel.NONE])


def level_emoji(level: GlobalLevel) -> str:
    """Custom emoji announcing a global sanction level."""
    from utils.emojis import WARNING, UNDONE, LEGAL, INFO
    return {
        GlobalLevel.WARN: WARNING,
        GlobalLevel.LIMITED: UNDONE,
        GlobalLevel.SUSPENDED: LEGAL,
    }.get(level, INFO)


# --------------------------------------------------------------------------- #
# What a suspended subject may still do
# --------------------------------------------------------------------------- #

#: Slash commands a suspended user keeps access to. A suspension cuts off the
#: product, not the person's ability to understand and contest it: they must
#: still be able to read their own cases, reach support and appeal.
SUSPENDED_ALLOWED_COMMANDS = frozenset({
    "mycases",   # read their own moderation cases (and their references)
    "moddy",     # about panel: support, terms, legal links
    "ping",      # is the bot even up
})

#: Component ``custom_id`` prefixes a suspended user may still click. Same
#: reasoning: appeal buttons, their own cases browser, and the /moddy panel.
SUSPENDED_ALLOWED_CUSTOM_IDS = (
    "moddy:apl:",                 # automod sanction appeals (DM buttons)
    "moddy:cases:browser:",       # /mycases (user mode) — see is_command_allowed
    "moddy:moddy:",               # /moddy informational panel
)


# --------------------------------------------------------------------------- #
# Joining a server
# --------------------------------------------------------------------------- #

def decide_join_refusal(
    *,
    guild_level: "GlobalLevel",
    owner_level: "GlobalLevel",
    inviter_level: "GlobalLevel" = None,
) -> Optional[str]:
    """Whether Moddy may stay in a server it was just added to.

    Three ways a join is refused — returns which one applies, or ``None``:

    - ``"guild"``   — the **server itself** is suspended; it lost every service.
    - ``"owner"``   — its **owner** is globally sanctioned.
    - ``"inviter"`` — the **person who added the bot** is globally sanctioned.

    A *limited* person is refused just like a suspended one: a limitation
    freezes growth (no premium, no new modules), and bringing Moddy into
    another server is exactly that kind of growth. A *limited server* is **not**
    refused — its existing setup keeps working, so evicting the bot would break
    what the limitation is meant to preserve.

    Kept pure so the policy lives in one readable place, independent of how the
    levels were resolved.
    """
    if guild_level is GlobalLevel.SUSPENDED:
        return "guild"
    if at_least(owner_level, GlobalLevel.LIMITED):
        return "owner"
    if inviter_level is not None and at_least(inviter_level, GlobalLevel.LIMITED):
        return "inviter"
    return None


def is_command_allowed_when_suspended(command_name: Optional[str]) -> bool:
    """Whether a suspended user may still run this slash command."""
    if not command_name:
        return False
    # Sub-commands arrive as "group sub" — the root name is what we gate on.
    return command_name.split(" ")[0] in SUSPENDED_ALLOWED_COMMANDS


def is_component_allowed_when_suspended(custom_id: Optional[str]) -> bool:
    """Whether a suspended user may still click this component."""
    if not custom_id:
        return False
    if custom_id.startswith("moddy:cases:browser:"):
        # Only the personal browser (`/mycases`), never the guild one.
        return custom_id.endswith(":user")
    return custom_id.startswith(SUSPENDED_ALLOWED_CUSTOM_IDS)


def level_from_actions(actions: Iterable[str]) -> GlobalLevel:
    """Resolve the highest level out of a list of active sanction actions."""
    level = GlobalLevel.NONE
    for action in actions:
        candidate = ACTION_TO_LEVEL.get(action)
        if candidate and _ORDER[candidate] > _ORDER[level]:
            level = candidate
    return level


def at_least(level: GlobalLevel, minimum: GlobalLevel) -> bool:
    """Whether ``level`` is at least as severe as ``minimum``."""
    return _ORDER[level] >= _ORDER[minimum]


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #

def _cache(bot) -> Dict[Tuple[str, str], Tuple[GlobalLevel, float]]:
    store = getattr(bot, "_global_sanction_cache", None)
    if store is None:
        store = {}
        bot._global_sanction_cache = store
    return store


def invalidate(bot, subject_type: Optional[str] = None,
               subject_id: Optional[int] = None) -> None:
    """Drop cached levels.

    Called with no argument it clears everything; with a subject it only drops
    that entry. Used by the staff commands, the case service and the backend
    Pub/Sub handler.
    """
    store = _cache(bot)
    if subject_type is None or subject_id is None:
        store.clear()
        return
    store.pop((subject_type, str(subject_id)), None)


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #

def _ttl_for(sanctions) -> float:
    """Cache TTL bounded by the soonest sanction expiry.

    Global sanctions may be temporary, so a cached level must never outlive
    the sanction that produced it: a 2-hour restriction has to stop applying
    the second it lapses, not up to ``CACHE_TTL`` later.
    """
    ttl = float(CACHE_TTL)
    now = datetime.now(timezone.utc)
    for sanction in sanctions:
        expires_at = sanction.get("expires_at")
        if expires_at is None:
            continue
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        remaining = (expires_at - now).total_seconds()
        if 0 < remaining < ttl:
            ttl = remaining
    return max(ttl, 1.0)


async def get_level(bot, subject_type: str, subject_id: Optional[int]) -> GlobalLevel:
    """Return the global sanction level of a subject (cached)."""
    if subject_id is None or not getattr(bot, "db", None):
        return GlobalLevel.NONE

    key = (subject_type, str(subject_id))
    store = _cache(bot)
    cached = store.get(key)
    now = time.monotonic()
    if cached and cached[1] > now:
        return cached[0]

    try:
        sanctions = await bot.db.list_active_global_actions(subject_type, subject_id)
    except Exception as exc:
        logger.error("Global sanction lookup failed for %s %s: %s",
                     subject_type, subject_id, exc)
        # Fail open: a DB hiccup must not lock everyone out of the bot.
        return cached[0] if cached else GlobalLevel.NONE

    level = level_from_actions(s["action"] for s in sanctions)
    store[key] = (level, now + _ttl_for(sanctions))
    return level


async def get_user_level(bot, user_id: Optional[int]) -> GlobalLevel:
    """Global sanction level of a Discord user."""
    return await get_level(bot, SUBJECT_USER, user_id)


async def get_guild_level(bot, guild_id: Optional[int]) -> GlobalLevel:
    """Global sanction level of a Discord server."""
    return await get_level(bot, SUBJECT_GUILD, guild_id)


async def get_context_level(bot, *, user_id: Optional[int] = None,
                            guild_id: Optional[int] = None) -> GlobalLevel:
    """Highest level between the acting user and the server they act in.

    A limited server limits everyone acting in it; a limited user stays limited
    wherever they go. Anything Moddy does happens in one of those two contexts,
    so this is the check almost every caller wants.
    """
    level = GlobalLevel.NONE
    if user_id is not None:
        level = await get_user_level(bot, user_id)
    if guild_id is not None and not at_least(level, GlobalLevel.SUSPENDED):
        guild_level = await get_guild_level(bot, guild_id)
        if _ORDER[guild_level] > _ORDER[level]:
            level = guild_level
    return level


# --------------------------------------------------------------------------- #
# Shorthands
# --------------------------------------------------------------------------- #

async def is_suspended(bot, *, user_id: Optional[int] = None,
                       guild_id: Optional[int] = None) -> bool:
    """Whether the user or the server has no access to Moddy at all."""
    level = await get_context_level(bot, user_id=user_id, guild_id=guild_id)
    return level == GlobalLevel.SUSPENDED


async def is_limited(bot, *, user_id: Optional[int] = None,
                     guild_id: Optional[int] = None) -> bool:
    """Whether the user or the server runs with reduced service (or worse)."""
    level = await get_context_level(bot, user_id=user_id, guild_id=guild_id)
    return at_least(level, GlobalLevel.LIMITED)
