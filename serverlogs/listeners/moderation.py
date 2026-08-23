"""Moderation: Moddy's own cases and sanctions.

Unlike every other listener module, this one is not driven by a Discord
gateway event: it is called by :mod:`services.case_service` whenever a
sanction is recorded or lifted, so a server's mod-log shows Moddy's actions
(automod sanctions, staff sanctions, expirations) next to the native Discord
ones.

Discord-side moderation — bans, kicks and timeouts applied with Discord's
own tools — is logged by :mod:`serverlogs.listeners.members`, which mirrors
those into this category too.

Some events in the ``moderation`` category have no source in Moddy yet
(cases cannot be deleted, and there is no report system): ``case_delete``,
``mass_case_delete``, ``report_create``, ``reports_ignore`` and
``reports_accept`` are declared in the registry and served by
:func:`log_case_event`, so they start working the day those features land,
without touching the logs system.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

import discord

from serverlogs.renderer import escape, fmt_number, fmt_time, fmt_user, truncate

logger = logging.getLogger('moddy.serverlogs.moderation')

#: Sanction action (utils.moderation_cases.SanctionAction) -> (added, removed).
SANCTION_EVENTS: Mapping[str, tuple] = {
    "warn": ("warn_add", "warn_remove"),
    "mute": ("mute_add", "mute_remove"),
    "ban": ("ban_add", "ban_remove"),
    "restrict": ("case_update", "case_update"),
    "revoke_access": ("case_update", "case_update"),
}


def event_for(action: str, *, revoked: bool = False) -> Optional[str]:
    """Registry event name for a sanction action, or ``None`` if untracked."""
    pair = SANCTION_EVENTS.get(str(action))
    if pair is None:
        return None
    return pair[1] if revoked else pair[0]


async def log_case_event(service, guild: discord.Guild, event: str, *,
                         subject: Optional[discord.abc.User] = None,
                         subject_id: Optional[int] = None,
                         actor: Optional[discord.abc.User] = None,
                         reference: Optional[str] = None,
                         reason: Optional[str] = None,
                         expires_at=None,
                         note: Optional[str] = None,
                         count: Optional[int] = None) -> None:
    """Emit one moderation log. Safe to call from anywhere, never raises."""
    try:
        entry = await service.open(guild, event, subject=subject, actor=actor)
        if entry is None:
            return

        if subject is None and subject_id:
            entry.line("user", f"<@{subject_id}>")
            entry.line("id", f"`{subject_id}`")
            # Uncached member: the id is still what pairs this log with the
            # gateway's own version of the same act (see LogEntry.subject_id).
            try:
                entry.subject_id = int(subject_id)
            except (TypeError, ValueError):
                pass
        if reference:
            entry.line("case", f"`{escape(reference)}`")
        if count is not None:
            entry.line("count", fmt_number(count))
        if actor is not None:
            entry.line("moderator", fmt_user(actor))
        if expires_at is not None:
            entry.line("expires", fmt_time(expires_at, "F"))
        if note:
            entry.block("note", escape(truncate(note, 900)))
        entry.actor(actor, reason)
        await service.submit(guild, entry)
    except Exception as e:
        # A log must never break the moderation action that produced it.
        logger.debug(f"[Logs] Could not log moderation event {event}: {e}")


async def log_sanction(bot, *, guild_id: Any, action: str, subject_id: Any,
                       issuer_id: Optional[Any] = None,
                       reason: Optional[str] = None, expires_at=None,
                       reference: Optional[str] = None,
                       revoked: bool = False) -> None:
    """Entry point used by :class:`services.case_service.CaseService`.

    Takes ids rather than objects because the case service works with raw
    ids, and resolves them against the bot's cache here.
    """
    event = event_for(action, revoked=revoked)
    if event is None:
        return

    cog = bot.get_cog("ServerLogs") if bot else None
    if cog is None or not getattr(cog, "service", None):
        return

    try:
        guild = bot.get_guild(int(guild_id))
    except (TypeError, ValueError):
        guild = None
    if guild is None:
        return

    subject = None
    try:
        subject = guild.get_member(int(subject_id)) or bot.get_user(int(subject_id))
    except (TypeError, ValueError):
        pass

    actor = None
    if issuer_id:
        try:
            actor = guild.get_member(int(issuer_id)) or bot.get_user(int(issuer_id))
        except (TypeError, ValueError):
            actor = None

    await log_case_event(
        cog.service, guild, event,
        subject=subject, subject_id=subject_id, actor=actor,
        reference=reference, reason=reason, expires_at=expires_at,
    )
