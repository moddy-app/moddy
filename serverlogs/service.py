"""The API every log listener uses.

A listener never touches the configuration, the webhooks or the queues. It
asks the service to *open* an entry for an event — which returns ``None``
immediately when the server does not log that event, when the channel is
muted or when the actor is on the ignore list — fills the entry in, and
submits it:

.. code-block:: python

    entry = await service.open(member.guild, "user_join", subject=member)
    if entry is None:
        return
    entry.line("account_created", fmt_time(member.created_at))
    entry.line("member_count", fmt_number(member.guild.member_count))
    await service.submit(member.guild, entry)

That early ``None`` is what keeps the module cheap: on a server that logs
nothing, every listener costs one dict lookup.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional, Tuple

import discord

from serverlogs import registry
from serverlogs.audit import AuditCache, target_matcher
from serverlogs.dispatcher import LogDispatcher
from serverlogs.renderer import LogEntry

logger = logging.getLogger('moddy.serverlogs.service')

MODULE_ID = "logs"

#: How long a mergeable event waits for its siblings before being delivered.
#: The events of one act come from different sources (a Moddy case, a gateway
#: event, an audit entry) and arrive within milliseconds of each other, but in
#: no guaranteed order — so the first one to arrive waits for the others.
MERGE_WINDOW = 3.0


class LogService:
    """Configuration lookup + rendering context + delivery, in one object."""

    def __init__(self, bot):
        self.bot = bot
        self.dispatcher = LogDispatcher(bot)
        self.audit = AuditCache()
        # (guild_id, family, subject_id) -> entries waiting to be merged.
        self._merging: Dict[Tuple[int, str, int], List[LogEntry]] = {}
        self._merge_tasks: Dict[Tuple[int, str, int], asyncio.Task] = {}

    # ------------------------------------------------------------------ #
    # Configuration
    # ------------------------------------------------------------------ #

    async def module(self, guild: Optional[discord.Guild]):
        """The guild's :class:`~modules.logs.LogsModule`, or ``None``."""
        if guild is None or not getattr(self.bot, "module_manager", None):
            return None
        try:
            module = await self.bot.module_manager.get_module_instance(guild.id, MODULE_ID)
        except Exception as e:
            logger.debug(f"[Logs] Could not load the logs module for {guild.id}: {e}")
            return None
        if module is None or not module.enabled:
            return None
        return module

    async def locale_for(self, guild: discord.Guild) -> str:
        """Language of the log messages: the server language.

        The logs used to carry their own ``locale`` setting; there is now one
        per server, set in ``/config`` -> Server settings. See
        ``utils/guild_language.py``.
        """
        from utils.guild_language import guild_locale
        return await guild_locale(self.bot, guild)

    # ------------------------------------------------------------------ #
    # Entry lifecycle
    # ------------------------------------------------------------------ #

    async def open(self, guild: Optional[discord.Guild], event: str, *,
                   channel: Optional[discord.abc.GuildChannel] = None,
                   subject: Optional[discord.abc.User] = None,
                   actor: Optional[discord.abc.User] = None) -> Optional[LogEntry]:
        """Start a log entry, or ``None`` when this event must not be logged.

        ``channel`` and ``subject``/``actor`` are checked against the
        server-wide ignore lists here, so no listener has to remember to.
        """
        module = await self.module(guild)
        if module is None or not module.is_event_logged(event):
            return None
        if channel is not None and module.is_ignored_channel(channel):
            return None
        for user in (subject, actor):
            if user is not None and module.is_ignored_actor(user):
                return None

        entry = LogEntry(event, await self.locale_for(guild))
        if subject is not None:
            entry.subject(subject)
        return entry

    async def submit(self, guild: discord.Guild, entry: Optional[LogEntry]) -> None:
        """Render and queue the entry for every channel bound to its event.

        An event that belongs to a *merge family* (a mute is a case, a
        timeout and a mirrored timeout — see ``registry.merge_family``) is
        held for :data:`MERGE_WINDOW` instead, so the act is delivered as one
        log rather than three. Everything else goes out immediately.
        """
        if entry is None or entry.is_empty:
            return
        module = await self.module(guild)
        if module is None:
            return

        family = registry.merge_family(entry.event)
        if (getattr(module, "merge_duplicates", False) and family is not None
                and entry.subject_id is not None):
            self._hold(guild, entry, family[0])
            return

        self._dispatch(module, entry, module.channels_for(entry.event))

    def _dispatch(self, module, entry: LogEntry, channel_ids) -> None:
        """Render once and queue the result for each destination channel."""
        if not channel_ids:
            return
        if not module.attach_transcripts:
            entry.files.clear()

        embed = entry.to_embed()
        for index, channel_id in enumerate(channel_ids):
            # discord.File objects are single-use streams; re-read them for
            # every destination so a fan-out doesn't send empty attachments.
            files = entry.files if index == 0 else _clone_files(entry.files)
            self.dispatcher.enqueue(channel_id, embed, files)

    # ------------------------------------------------------------------ #
    # Merging one act told several times
    # ------------------------------------------------------------------ #

    def _hold(self, guild: discord.Guild, entry: LogEntry, family: str) -> None:
        """Park an entry until its siblings have had their chance to arrive."""
        key = (guild.id, family, entry.subject_id)
        self._merging.setdefault(key, []).append(entry)
        if key not in self._merge_tasks:
            self._merge_tasks[key] = asyncio.create_task(
                self._flush_later(guild, key), name=f"moddy-logs-merge-{guild.id}")

    async def _flush_later(self, guild: discord.Guild, key) -> None:
        try:
            await asyncio.sleep(MERGE_WINDOW)
            await self.flush(guild, key)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[Logs] Merge flush failed for {key}: {e}", exc_info=True)
            self._merging.pop(key, None)
        finally:
            self._merge_tasks.pop(key, None)

    async def flush(self, guild: discord.Guild, key) -> None:
        """Deliver a merged group: the richest entry, enriched by the others.

        Merging happens **per channel**. The winner absorbs a sibling only
        where both would have landed in the same channel; a sibling routed
        somewhere the winner does not go is still delivered there, on its
        own. A server that already splits its categories across channels
        therefore loses nothing by turning merging on.
        """
        entries = self._merging.pop(key, [])
        if not entries:
            return
        module = await self.module(guild)
        if module is None:
            return

        # Highest priority first; ties keep their arrival order, so the entry
        # that opened the group wins.
        entries.sort(key=lambda item: -_priority(item.event))
        winner, siblings = entries[0], entries[1:]
        winner_channels = module.channels_for(winner.event)

        for sibling in siblings:
            channels = module.channels_for(sibling.event)
            elsewhere = [cid for cid in channels if cid not in winner_channels]
            if elsewhere:
                self._dispatch(module, sibling, elsewhere)
            if len(elsewhere) != len(channels):
                # They share at least one channel: that is the duplicate the
                # reader complained about.
                winner.absorb(sibling)

        self._dispatch(module, winner, winner_channels)

    # ------------------------------------------------------------------ #
    # Audit log
    # ------------------------------------------------------------------ #

    async def executor(self, guild: discord.Guild, action: discord.AuditLogAction,
                       target_id: Optional[int] = None, *, wait: float = 2.0,
                       extra=None) -> Tuple[Optional[discord.abc.User], Optional[str]]:
        """``(who, reason)`` for an action, from the audit-log cache."""
        if guild is None:
            return None, None
        entry = await self.audit.wait_for(
            guild.id, target_matcher(action, target_id, extra), timeout=wait)
        if entry is None:
            return None, None
        return entry.user, entry.reason

    async def close(self) -> None:
        for task in self._merge_tasks.values():
            task.cancel()
        self._merge_tasks.clear()
        self._merging.clear()
        await self.dispatcher.close()


def _priority(event: str) -> int:
    """How much context an event carries — the merge winner is the richest."""
    family = registry.merge_family(event)
    return family[1] if family else 0


def _clone_files(files):
    """Duplicate in-memory files so each destination gets its own stream."""
    import io

    clones = []
    for file in files:
        try:
            file.fp.seek(0)
            data = file.fp.read()
            file.fp.seek(0)
            clones.append(discord.File(io.BytesIO(data), filename=file.filename))
        except Exception:
            continue
    return clones
