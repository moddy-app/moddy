"""Per-user display preferences (icon theme + language override).

Storage: ``users.data.preferences`` JSONB::

    {"icon_theme": "dark" | "light", "language": "<locale>"}

An absent key means "default" (dark icons / follow the Discord client
language). The in-memory cache holds ONLY users with at least one non-default
value, so lookups on the hot path (every i18n resolution, every icon render)
are a single dict miss for almost everybody. The cache is filled once at
startup by :func:`load_all` and kept in sync by the setters.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

logger = logging.getLogger("moddy.user_preferences")

DEFAULT_ICON_THEME = "dark"
ICON_THEMES = ("dark", "light")
SUPPORTED_LANGUAGES = ("fr", "en-US", "es-ES", "pt-BR", "de")

# user_id -> {"icon_theme": ..., "language": ...} (non-default keys only)
_cache: Dict[int, Dict[str, str]] = {}


def _clean(raw) -> Dict[str, str]:
    """Keep only valid, non-default values from a stored preferences blob."""
    out: Dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    if raw.get("icon_theme") == "light":
        out["icon_theme"] = "light"
    if raw.get("language") in SUPPORTED_LANGUAGES:
        out["language"] = raw["language"]
    return out


def get_icon_theme(user_id: Optional[int]) -> str:
    """Return ``"dark"`` (default) or ``"light"``. Sync, cache only."""
    if user_id is None:
        return DEFAULT_ICON_THEME
    return _cache.get(int(user_id), {}).get("icon_theme", DEFAULT_ICON_THEME)


def get_language_override(user_id: Optional[int]) -> Optional[str]:
    """Return the forced locale, or None when the user follows Discord."""
    if user_id is None:
        return None
    return _cache.get(int(user_id), {}).get("language")


async def load_all(bot) -> int:
    """Load every user having stored preferences into the cache."""
    db = getattr(bot, "db", None)
    if db is None:
        return 0
    rows = await db.get_users_with_preferences()
    _cache.clear()
    for user_id, raw in rows:
        cleaned = _clean(raw)
        if cleaned:
            _cache[int(user_id)] = cleaned
    logger.info("Loaded user preferences for %d users", len(_cache))
    return len(_cache)


async def _persist(bot, user_id: int, prefs: Dict[str, str]) -> None:
    # The whole (small) object is written so removing a key really removes it.
    await bot.db.update_user_data(user_id, "preferences", prefs)
    if prefs:
        _cache[user_id] = prefs
    else:
        _cache.pop(user_id, None)


async def _stored(bot, user_id: int) -> Dict[str, str]:
    """Current preferences, read from the DB (source of truth for a write)."""
    user = await bot.db.get_user(user_id)
    return _clean((user.get("data") or {}).get("preferences"))


async def set_icon_theme(bot, user_id: int, theme: str) -> None:
    if theme not in ICON_THEMES:
        raise ValueError(f"Unsupported icon theme: {theme!r}")
    user_id = int(user_id)
    prefs = await _stored(bot, user_id)
    if theme == DEFAULT_ICON_THEME:
        prefs.pop("icon_theme", None)
    else:
        prefs["icon_theme"] = theme
    await _persist(bot, user_id, prefs)


async def set_language(bot, user_id: int, locale: Optional[str]) -> None:
    """Force a locale, or pass None / ``"auto"`` to follow Discord again."""
    if locale == "auto":
        locale = None
    if locale is not None and locale not in SUPPORTED_LANGUAGES:
        raise ValueError(f"Unsupported language: {locale!r}")
    user_id = int(user_id)
    prefs = await _stored(bot, user_id)
    if locale is None:
        prefs.pop("language", None)
    else:
        prefs["language"] = locale
    await _persist(bot, user_id, prefs)
