"""
What every mini-bot of the process shares — paid for once, not once per bot.

One asyncpg pool (small: mini-bots do little DB work), one Redis connection,
the translations loaded once, one technical logger. Each Discord client only
adds its own gateway connection and guild cache on top.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("moddy.minibots")

#: Pool size of the mini-bots process (all bots together).
DB_POOL_MAX = int(os.environ.get("MINIBOTS_DB_POOL_MAX", "3"))
APPLICATION_NAME = "Moddy Mini-bots"


class _LoggerOwner:
    """The tiny ``bot`` the technical logger needs (user avatar + db flag)."""

    def __init__(self, db):
        self.db = db
        self.user = None
        self.guilds = ()


def install_process_patches(db) -> None:
    """The discord.py patches Moddy installs, once for the whole process:
    light-theme icons (needs the preferences cache), the interaction guard,
    then the mini-bot emoji swap (outermost, see minibots/emojis.py)."""
    try:
        from utils import icon_theme
        icon_theme.install()
    except Exception as exc:  # noqa: BLE001
        logger.error("Mini-bots: icon theme not installed: %s", exc)
    try:
        from utils import interaction_guard
        interaction_guard.install()
    except Exception as exc:  # noqa: BLE001
        logger.error("Mini-bots: interaction guard not installed: %s", exc)
    try:
        from minibots import emojis
        emojis.install()
    except Exception as exc:  # noqa: BLE001
        logger.error("Mini-bots: emoji swap not installed: %s", exc)
    try:
        from minibots import branding
        branding.install()
    except Exception as exc:  # noqa: BLE001
        logger.error("Mini-bots: branding not installed: %s", exc)


async def load_preferences(db) -> None:
    if db is None:
        return
    try:
        from utils import user_preferences
        await user_preferences.load_all(_LoggerOwner(db))
    except Exception as exc:  # noqa: BLE001
        logger.error("Mini-bots: user preferences not loaded: %s", exc)


class SharedResources:
    def __init__(self, *, db=None, redis=None, tech_logger=None,
                 version: Optional[str] = None):
        self.db = db
        self.redis = redis
        self.tech_logger = tech_logger
        self.version = version

    @classmethod
    async def create(cls) -> "SharedResources":
        from config import DATABASE_URL, REDIS_URL, REDIS_PASSWORD
        from utils.i18n import i18n

        i18n.load_translations()

        db = None
        if DATABASE_URL:
            from db.base import ModdyDatabase
            db = ModdyDatabase(DATABASE_URL, application_name=APPLICATION_NAME,
                               min_size=1, max_size=DB_POOL_MAX)
            try:
                await db.connect()
            except Exception as exc:  # noqa: BLE001 — the process can idle without it
                logger.error("Mini-bots: database unavailable: %s", exc)
                db = None
        else:
            logger.warning("Mini-bots: DATABASE_URL not set")

        redis = None
        if REDIS_URL:
            import redis.asyncio as aioredis
            try:
                redis = aioredis.from_url(REDIS_URL, password=REDIS_PASSWORD,
                                          decode_responses=True)
                await redis.ping()
            except Exception as exc:  # noqa: BLE001
                logger.error("Mini-bots: Redis unavailable: %s", exc)
                redis = None

        from utils.tech_logger import TechLogger
        tech_logger = TechLogger(_LoggerOwner(db))

        install_process_patches(db)

        await load_preferences(db)

        version = (os.environ.get("RAILWAY_GIT_COMMIT_SHA") or "")[:7] or None
        return cls(db=db, redis=redis, tech_logger=tech_logger, version=version)

    async def close(self) -> None:
        if self.tech_logger is not None:
            try:
                await self.tech_logger.close()
            except Exception:  # noqa: BLE001
                pass
        if self.redis is not None:
            try:
                await self.redis.aclose()
            except Exception:  # noqa: BLE001
                pass
        if self.db is not None:
            try:
                await self.db.close()
            except Exception:  # noqa: BLE001
                pass
