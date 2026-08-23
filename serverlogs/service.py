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

import logging
from typing import Optional, Tuple

import discord

from serverlogs.audit import AuditCache, target_matcher
from serverlogs.dispatcher import LogDispatcher
from serverlogs.renderer import LogEntry
from utils.i18n import i18n

logger = logging.getLogger('moddy.serverlogs.service')

MODULE_ID = "logs"


class LogService:
    """Configuration lookup + rendering context + delivery, in one object."""

    def __init__(self, bot):
        self.bot = bot
        self.dispatcher = LogDispatcher(bot)
        self.audit = AuditCache()

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

    def locale_for(self, guild: discord.Guild, module) -> str:
        """Language of the log messages: the configured one, else the server's."""
        configured = getattr(module, "locale", None)
        if configured and configured != "auto":
            return configured
        preferred = str(guild.preferred_locale) if guild.preferred_locale else "en-US"
        supported = i18n._supported_locales  # noqa: SLF001 — same package contract
        if preferred in supported:
            return preferred
        base = preferred.split("-")[0]
        for candidate in supported:
            if candidate == base or candidate.startswith(f"{base}-"):
                return candidate
        return "en-US"

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

        entry = LogEntry(event, self.locale_for(guild, module))
        if subject is not None:
            entry.subject(subject)
        return entry

    async def submit(self, guild: discord.Guild, entry: Optional[LogEntry]) -> None:
        """Render and queue the entry for every channel bound to its event."""
        if entry is None or entry.is_empty:
            return
        module = await self.module(guild)
        if module is None:
            return

        channel_ids = module.channels_for(entry.event)
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
        await self.dispatcher.close()


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
