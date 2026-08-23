"""Audit-log correlation — putting a name on "who did this".

The gateway tells us *that* a role was deleted; only the audit log tells us
*who* deleted it and why. Two problems make a naive
``guild.audit_logs(limit=1)`` call unreliable:

* the audit entry and the gateway event race — either can arrive first;
* polling the audit log on every event burns REST calls during a raid.

So the cog feeds every ``on_audit_log_entry_create`` into this cache
(free, it comes over the gateway), and a listener that needs an executor
looks the cache up first, then waits a short moment for a matching entry to
arrive, and only then gives up. No REST call is ever made.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from typing import Callable, Deque, Dict, Optional

import discord

logger = logging.getLogger('moddy.serverlogs.audit')

#: Entries kept per guild.
CACHE_SIZE = 60
#: How long a cached entry may still be matched to a gateway event.
CACHE_TTL = 20.0
#: How long a listener waits for an entry that has not arrived yet.
DEFAULT_WAIT = 2.0


class AuditCache:
    """Short-lived, per-guild cache of incoming audit-log entries."""

    def __init__(self):
        self._entries: Dict[int, Deque] = defaultdict(lambda: deque(maxlen=CACHE_SIZE))
        self._waiters: Dict[int, list] = defaultdict(list)

    def add(self, entry: discord.AuditLogEntry) -> None:
        guild = getattr(entry, "guild", None)
        if guild is None:
            return
        self._entries[guild.id].append((time.monotonic(), entry))

        # Wake anything waiting for this exact entry.
        for future, check in list(self._waiters[guild.id]):
            if future.done():
                self._waiters[guild.id].remove((future, check))
                continue
            try:
                matched = check(entry)
            except Exception:
                matched = False
            if matched:
                future.set_result(entry)
                self._waiters[guild.id].remove((future, check))

    def find(self, guild_id: int, check: Callable[[discord.AuditLogEntry], bool]
             ) -> Optional[discord.AuditLogEntry]:
        """Most recent cached entry matching ``check``, if still fresh."""
        now = time.monotonic()
        for stamp, entry in reversed(self._entries.get(guild_id, ())):
            if now - stamp > CACHE_TTL:
                break
            try:
                if check(entry):
                    return entry
            except Exception:
                continue
        return None

    async def wait_for(self, guild_id: int,
                       check: Callable[[discord.AuditLogEntry], bool],
                       timeout: float = DEFAULT_WAIT) -> Optional[discord.AuditLogEntry]:
        """Cached match, or the first one to arrive within ``timeout``."""
        cached = self.find(guild_id, check)
        if cached is not None:
            return cached

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiters[guild_id].append((future, check))
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            if not future.done():
                future.cancel()
            self._waiters[guild_id] = [
                (f, c) for f, c in self._waiters[guild_id] if f is not future
            ]


def target_matcher(action: discord.AuditLogAction, target_id: Optional[int] = None,
                   extra: Optional[Callable[[discord.AuditLogEntry], bool]] = None
                   ) -> Callable[[discord.AuditLogEntry], bool]:
    """Build a ``check`` for :meth:`AuditCache.wait_for`."""

    def _check(entry: discord.AuditLogEntry) -> bool:
        if entry.action != action:
            return False
        if target_id is not None:
            entry_target_id = getattr(entry.target, "id", None) or entry.target_id
            if entry_target_id != target_id:
                return False
        return extra(entry) if extra else True

    return _check
