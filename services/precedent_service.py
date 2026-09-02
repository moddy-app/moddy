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
from collections import OrderedDict
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
        # Expired guilds are swept out on every load (see _evict_expired) — this
        # holds up to PRECEDENT_MAX_PER_GUILD embedding vectors per guild, so
        # letting stale entries linger is the single largest memory leak-shaped
        # cost in the bot even though nothing here actually leaks.
        #
        # An OrderedDict in insertion order, capped at PRECEDENT_CACHE_MAX_GUILDS:
        # the TTL bounds the cache in *time* but not in *size*, so a busy window
        # across many guilds could otherwise pin hundreds of megabytes of vectors
        # at once. Past the cap the least-recently-loaded guild is dropped.
        self._cache: "OrderedDict[int, tuple[float, List[ap.Precedent]]]" = OrderedDict()

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

    def _evict_expired(self, now: float) -> None:
        """Drop every guild whose cached precedents have passed their TTL.

        The TTL alone was only enforced on read, and only for the guild being
        read — so a guild that used the automod once kept its slice of vectors
        resident for the whole process lifetime. Sweeping on every load bounds
        the cache to the guilds actually active within the TTL window, which is
        what makes it a cache rather than an ever-growing table. The sweep is
        O(cached guilds), i.e. tens of entries, on a path that is already about
        to hit the database.
        """
        stale = [gid for gid, (expiry, _) in self._cache.items() if expiry <= now]
        for gid in stale:
            del self._cache[gid]

    def _evict_overflow(self) -> None:
        """Drop least-recently-loaded guilds past PRECEDENT_CACHE_MAX_GUILDS.

        Complements the TTL sweep: that one bounds how *long* a guild stays
        cached, this one bounds how *many* do at once. Evicting costs the guild a
        single extra query the next time it is seen.
        """
        while len(self._cache) > ac.PRECEDENT_CACHE_MAX_GUILDS:
            self._cache.popitem(last=False)

    async def _guild_precedents(self, guild_id: int) -> List[ap.Precedent]:
        now = time.time()
        entry = self._cache.get(guild_id)
        if entry is not None and entry[0] > now:
            # Refresh recency so a guild that keeps being read is not the one
            # evicted when the cache overflows.
            self._cache.move_to_end(guild_id)
            return entry[1]
        self._evict_expired(now)
        pres: List[ap.Precedent] = []
        try:
            rows = await self.bot.db.list_precedents(
                guild_id, limit=ac.PRECEDENT_MAX_PER_GUILD)
        except Exception as e:  # noqa: BLE001
            logger.error("precedent load failed (guild %s): %s", guild_id, e)
            rows = []
        for r in rows:
            # float32 array.array straight from the repository — never copied
            # into a list, which would undo the memory saving row by row.
            vector = r.get("vector")
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
        self._cache.move_to_end(guild_id)
        self._evict_overflow()
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
