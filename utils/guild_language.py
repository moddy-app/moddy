"""
Server language — the single source of truth for "which language does Moddy
speak in this server?".

Every module used to ship its own language dropdown (AltGuard's panel
language, Automod AI's ``langue_serveur``, the logs' ``locale``, a per-ticket
category language...), and everything else silently read
``guild.preferred_locale``. The same server could therefore be greeted in
English, warned in French and logged in German.

There is now **one** setting, stored outside of any module in
``guilds.data.settings.language``, edited from ``/config`` → *Server settings*:

* ``"auto"`` (default) — follow the server's own language
  (``guild.preferred_locale``), but only when **Community** is enabled: that
  is the only case where Discord lets a server actually pick a language.
  Anything else falls back to English.
* an explicit locale (``fr``, ``en-US``, ``es-ES``, ``pt-BR``, ``de``) — the
  languages Moddy is fully translated into.

Usage::

    from utils.guild_language import guild_locale

    locale = await guild_locale(bot, guild)          # async, authoritative
    locale = guild_locale_cached(bot, guild)         # sync hot paths

The stored setting is cached in-process (one row read per guild, then never
again) and invalidated on save and on a dashboard push — see
``invalidate_guild_language``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional, Union

import discord

logger = logging.getLogger('moddy.guild_language')

#: Sentinel meaning "derive the language from the server itself".
AUTO = "auto"

#: Languages Moddy is fully translated into (one ``locales/<code>.json`` each).
#: Order matters: it is the order of the dropdown in ``/config``.
SUPPORTED_LOCALES = ("en-US", "fr", "es-ES", "pt-BR", "de")

#: Used whenever nothing reliable can be derived.
DEFAULT_LOCALE = "en-US"

#: Where the setting lives in ``guilds.data`` (dotted path for update_guild_data).
SETTINGS_PATH = "settings.language"

# guild_id -> stored setting ("auto" or a supported locale).
_cache: Dict[int, str] = {}
# Guilds whose refresh task is already in flight (sync path only).
_refreshing: set = set()


# --------------------------------------------------------------------------- #
# Pure helpers (no bot, no DB — used by the tests and by the config UI)
# --------------------------------------------------------------------------- #

def match_supported_locale(raw: Optional[str]) -> Optional[str]:
    """Map any Discord locale onto a language Moddy actually speaks.

    ``en-GB`` → ``en-US``, ``es-419`` → ``es-ES``, ``pt-PT`` → ``pt-BR``.
    Returns ``None`` when the language is not translated at all (``ja``...),
    so the caller can fall back rather than serve a half-translated locale.
    """
    if not raw:
        return None
    value = str(raw).strip()
    if not value:
        return None
    for locale in SUPPORTED_LOCALES:
        if locale.lower() == value.lower():
            return locale
    base = value.split('-')[0].lower()
    for locale in SUPPORTED_LOCALES:
        if locale.split('-')[0].lower() == base:
            return locale
    return None


def normalize_language_setting(raw: Optional[str]) -> str:
    """Coerce a stored/incoming value into ``"auto"`` or a supported locale."""
    if not raw or str(raw).strip().lower() == AUTO:
        return AUTO
    return match_supported_locale(raw) or AUTO


def auto_locale(guild: Optional[discord.Guild]) -> str:
    """The language a server implicitly speaks.

    Discord only lets a server pick a ``preferred_locale`` that means anything
    once **Community** is enabled; outside of that it stays on the account
    default and would have us speak a language nobody on the server chose. So
    the signal is trusted only for community servers, and English otherwise.
    """
    if guild is None:
        return DEFAULT_LOCALE
    try:
        features = set(getattr(guild, "features", None) or ())
        if "COMMUNITY" not in features:
            return DEFAULT_LOCALE
        return match_supported_locale(
            str(getattr(guild, "preferred_locale", "") or "")) or DEFAULT_LOCALE
    except Exception:
        return DEFAULT_LOCALE


def resolve_locale(guild: Optional[discord.Guild], setting: Optional[str]) -> str:
    """Effective locale for ``guild`` given its stored ``setting``."""
    normalized = normalize_language_setting(setting)
    if normalized != AUTO:
        return normalized
    return auto_locale(guild)


# --------------------------------------------------------------------------- #
# Stored setting
# --------------------------------------------------------------------------- #

def _guild_id_of(guild: Union[discord.Guild, int, None]) -> Optional[int]:
    if guild is None:
        return None
    if isinstance(guild, int):
        return guild
    guild_id = getattr(guild, "id", None)
    return int(guild_id) if guild_id else None


async def get_language_setting(bot, guild: Union[discord.Guild, int, None]) -> str:
    """Stored setting for this guild (``"auto"`` or a locale), cached.

    Never raises: a database hiccup degrades to ``"auto"``, which is what an
    unconfigured server gets anyway.
    """
    guild_id = _guild_id_of(guild)
    if guild_id is None:
        return AUTO

    cached = _cache.get(guild_id)
    if cached is not None:
        return cached

    setting = AUTO
    db = getattr(bot, "db", None)
    if db is not None:
        try:
            guild_row = await db.get_guild(guild_id)
            data = (guild_row or {}).get('data') or {}
            settings = data.get('settings') or {}
            setting = normalize_language_setting(settings.get('language'))
        except Exception as e:
            logger.debug(f"Could not read the language setting of guild {guild_id}: {e}")
            return AUTO

    _cache[guild_id] = setting
    return setting


async def set_language_setting(bot, guild: Union[discord.Guild, int], value: str) -> str:
    """Persist the setting and refresh the cache. Returns the stored value."""
    guild_id = _guild_id_of(guild)
    if guild_id is None:
        raise ValueError("set_language_setting needs a guild")

    normalized = normalize_language_setting(value)
    db = getattr(bot, "db", None)
    if db is None:
        raise RuntimeError("Database unavailable")

    await db.update_guild_data(guild_id, SETTINGS_PATH, normalized)
    _cache[guild_id] = normalized
    return normalized


def invalidate_guild_language(guild: Union[discord.Guild, int, None] = None) -> None:
    """Drop the cached setting (a dashboard push wrote straight to the DB)."""
    if guild is None:
        _cache.clear()
        return
    guild_id = _guild_id_of(guild)
    if guild_id is not None:
        _cache.pop(guild_id, None)


# --------------------------------------------------------------------------- #
# Public resolution API
# --------------------------------------------------------------------------- #

async def guild_locale(bot, guild: Union[discord.Guild, int, None]) -> str:
    """The locale Moddy speaks in this guild. Use this everywhere."""
    guild_id = _guild_id_of(guild)
    setting = await get_language_setting(bot, guild_id)

    # Anything that is not an id is taken as the guild object itself (a real
    # discord.Guild, or a stand-in in the tests) — only an id needs a lookup.
    resolved = None if guild is None or isinstance(guild, int) else guild
    if resolved is None and guild_id is not None and bot is not None:
        try:
            resolved = bot.get_guild(guild_id)
        except Exception:
            resolved = None
    return resolve_locale(resolved, setting)


def guild_locale_cached(bot, guild: Optional[discord.Guild]) -> str:
    """Sync variant for hot paths that cannot await (automod, log rendering).

    Uses the cached setting when there is one. Otherwise it answers with the
    automatic language *now* and warms the cache in the background, so at
    worst the very first message of a freshly-booted guild is rendered with
    the automatic language instead of an explicit override.
    """
    guild_id = _guild_id_of(guild)
    if guild_id is None:
        return DEFAULT_LOCALE

    setting = _cache.get(guild_id)
    if setting is not None:
        return resolve_locale(guild, setting)

    _warm_cache(bot, guild_id)
    return auto_locale(guild)


def _warm_cache(bot, guild_id: int) -> None:
    """Fire-and-forget cache fill, at most one task in flight per guild."""
    if guild_id in _refreshing or getattr(bot, "db", None) is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    _refreshing.add(guild_id)

    async def _fill():
        try:
            await get_language_setting(bot, guild_id)
        finally:
            _refreshing.discard(guild_id)

    loop.create_task(_fill(), name=f"moddy-guild-language-{guild_id}")


__all__ = [
    "AUTO", "SUPPORTED_LOCALES", "DEFAULT_LOCALE", "SETTINGS_PATH",
    "match_supported_locale", "normalize_language_setting", "auto_locale",
    "resolve_locale", "get_language_setting", "set_language_setting",
    "invalidate_guild_language", "guild_locale", "guild_locale_cached",
]
