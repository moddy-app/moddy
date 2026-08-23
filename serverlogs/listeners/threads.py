"""Threads: creation, deletion, settings, archiving and locking.

Discord sends a single ``thread_update`` for everything a thread carries, so
the renamed/slowmode/auto-archive settings go through a diff table (see
:mod:`serverlogs.listeners._diff`) exactly like channels do.

``archived`` and ``locked`` are two independent booleans on the same object —
closing a thread flips the first, a moderator locking it flips the second —
so they are handled outside the table, as four distinct events, which lets a
server log lock/unlock (a moderation signal) without logging the archive
churn every busy forum produces.
"""

from __future__ import annotations

from typing import Any

import discord

from serverlogs.listeners._diff import DiffRule, emit_diffs
from serverlogs.renderer import fmt_channel, fmt_duration, fmt_time, fmt_user


# --------------------------------------------------------------------------- #
# Value renderers
# --------------------------------------------------------------------------- #

def _render_duration(value: Any, locale: str) -> str:
    return fmt_duration(int(value) if value is not None else None, locale)


def _render_minutes(value: Any, locale: str) -> str:
    """``auto_archive_duration`` is expressed in minutes, not seconds."""
    return fmt_duration(int(value) * 60 if value is not None else None, locale)


#: One line per tracked setting — this table *is* the thread log catalogue.
THREAD_RULES = (
    DiffRule("thread_name_update", "name"),
    DiffRule("thread_slowmode_update", "slowmode_delay", render=_render_duration),
    DiffRule("thread_archive_duration_update", "auto_archive_duration",
             render=_render_minutes),
)


# --------------------------------------------------------------------------- #
# Listeners
# --------------------------------------------------------------------------- #

async def on_thread_create(service, thread: discord.Thread) -> None:
    guild = thread.guild
    who, reason = await service.executor(
        guild, discord.AuditLogAction.thread_create, thread.id)

    entry = await service.open(guild, "thread_create", channel=thread, actor=who)
    if entry is None:
        return
    _header(entry, thread)
    # The owner is only in cache when the creator is a known member.
    owner = thread.owner
    if owner is not None:
        entry.line("owner", fmt_user(owner))
    entry.actor(who, reason)
    await service.submit(guild, entry)


async def on_thread_delete(service, thread: discord.Thread) -> None:
    guild = thread.guild
    who, reason = await service.executor(
        guild, discord.AuditLogAction.thread_delete, thread.id)

    entry = await service.open(guild, "thread_delete", channel=thread, actor=who)
    if entry is None:
        return
    _header(entry, thread)
    entry.line("thread_created", fmt_time(thread.created_at))
    entry.actor(who, reason)
    await service.submit(guild, entry)


async def on_thread_update(service, before: discord.Thread,
                           after: discord.Thread) -> None:
    guild = after.guild

    await emit_diffs(
        service, guild, THREAD_RULES, before, after,
        header=lambda entry: _header(entry, after),
        audit_action=discord.AuditLogAction.thread_update,
        audit_target_id=after.id,
    )

    if before.archived != after.archived:
        await _log_state(service, guild, after,
                         "thread_archive" if after.archived else "thread_unarchive")
    if before.locked != after.locked:
        await _log_state(service, guild, after,
                         "thread_lock" if after.locked else "thread_unlock")


# --------------------------------------------------------------------------- #
# Special cases
# --------------------------------------------------------------------------- #

async def _log_state(service, guild, thread: discord.Thread, event: str) -> None:
    """Archive/unarchive and lock/unlock share the same one-fact shape."""
    who, reason = await service.executor(
        guild, discord.AuditLogAction.thread_update, thread.id, wait=1.5)

    entry = await service.open(guild, event, channel=thread, actor=who)
    if entry is None:
        return
    _header(entry, thread)
    entry.actor(who, reason)
    await service.submit(guild, entry)


def _header(entry, thread: discord.Thread) -> None:
    """Identity lines shared by every thread event."""
    entry.line("thread", fmt_channel(thread))
    entry.line("id", f"`{thread.id}`")
    entry.line("parent_channel", fmt_channel(thread.parent))
