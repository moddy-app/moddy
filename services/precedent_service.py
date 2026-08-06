"""
Server-precedents service (session 7) — the caller-side glue for the RAG.

The pure matching lives in ``automod/precedents.py`` (cosine + shortcut). This
service owns the *I/O* side that the pipeline must not touch:

* **recording** a precedent from a human ruling (accepted/refused appeal, shadow
  ``✅``/``❌`` click) — it embeds the message once (through ``bot.gateway`` via
  the shared engine) and stores it via the repository;
* **serving** a guild's precedents to the engine as a lazy provider, with a
  short in-process cache so matching stays in-process and DB load is bounded to
  one query per guild per window.

It never decides anything — the engine applies the shortcut / injection. See
docs/AUTOMOD_AI.md §2quinquies.
"""

from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable, List, Optional

from automod import constants as ac
from automod import precedents as ap
from automod import get_engine
from automod.normalize import collapse_repeats

logger = logging.getLogger("moddy.services.precedents")


class PrecedentService:
    def __init__(self, bot):
        self.bot = bot
        # guild_id -> (expiry_ts, list[Precedent]). Short TTL: a freshly recorded
        # precedent is visible within PRECEDENT_CACHE_TTL_SECONDS everywhere.
        self._cache: dict[int, tuple[float, List[ap.Precedent]]] = {}

    # -- Recording (from a human ruling) -----------------------------------

    async def record(
        self,
        guild_id: int,
        contenu: str,
        verdict_humain: str,
        *,
        source: str,
        categorie: str = "",
        gravite: str = "",
    ) -> Optional[int]:
        """Store a precedent from a human ruling. Embeds the message once.

        Best-effort: a missing DB / embedding just means no precedent is learned
        from this ruling (never raises to the caller). Returns the row id or None.
        """
        if not ac.PRECEDENTS_ENABLED or not getattr(self.bot, "db", None):
            return None
        content = (contenu or "").strip()
        if not content or verdict_humain not in (
                ac.PRECEDENT_NON_SANCTIONNABLE, ac.PRECEDENT_SANCTIONNABLE):
            return None
        try:
            engine = get_engine(self.bot)
            vector = await engine.embeddings.embed_query(content)
        except Exception as e:  # noqa: BLE001
            logger.debug("precedent embed failed (guild %s): %s", guild_id, e)
            vector = None
        if not vector:
            return None

        contenu_norm = collapse_repeats(content) or content
        try:
            pid = await self.bot.db.add_precedent(
                guild_id=guild_id,
                contenu_norm=contenu_norm,
                embedding=vector,
                verdict_humain=verdict_humain,
                source=source,
                categorie=categorie or None,
                gravite=gravite or None,
                max_per_guild=ac.PRECEDENT_MAX_PER_GUILD,
            )
        except Exception as e:  # noqa: BLE001
            logger.error("precedent store failed (guild %s): %s", guild_id, e)
            return None
        if pid is not None:
            self.invalidate(guild_id)
        return pid

    # -- Serving (to the engine) -------------------------------------------

    async def _guild_precedents(self, guild_id: int) -> List[ap.Precedent]:
        now = time.time()
        entry = self._cache.get(guild_id)
        if entry is not None and entry[0] > now:
            return entry[1]
        pres: List[ap.Precedent] = []
        try:
            rows = await self.bot.db.list_precedents(
                guild_id, limit=ac.PRECEDENT_MAX_PER_GUILD)
        except Exception as e:  # noqa: BLE001
            logger.error("precedent load failed (guild %s): %s", guild_id, e)
            rows = []
        for r in rows:
            vector = r.get("vector") or []
            if not vector:
                continue
            pres.append(ap.Precedent(
                id=str(r.get("id")),
                message=r.get("contenu_norm") or "",
                verdict_humain=r.get("verdict_humain") or "",
                vector=vector,
                categorie=r.get("categorie") or "",
                gravite=r.get("gravite") or "",
                source=r.get("source") or "",
            ))
        self._cache[guild_id] = (now + ac.PRECEDENT_CACHE_TTL_SECONDS, pres)
        return pres

    def make_provider(
        self, guild_id: int
    ) -> Optional[Callable[[Callable[[], Awaitable[Optional[List[float]]]]],
                           Awaitable[List[ap.PrecedentMatch]]]]:
        """Build the lazy ``precedents_fn`` for the pipeline, or None.

        None when precedents are disabled or there is no DB — the engine then
        judges with no precedent block. The provider embeds the message (via the
        supplied ``get_vector``) **only** when the guild actually has precedents,
        so a server that has taught the bot nothing pays no extra call.
        """
        if not ac.PRECEDENTS_ENABLED or not getattr(self.bot, "db", None):
            return None

        async def provider(get_vector) -> List[ap.PrecedentMatch]:
            pres = await self._guild_precedents(guild_id)
            if not pres:
                return []
            qvec = await get_vector()
            if not qvec:
                return []
            return ap.match(qvec, pres)

        return provider

    def invalidate(self, guild_id: int) -> None:
        """Drop a guild's cached precedents (after a record / delete)."""
        self._cache.pop(guild_id, None)
