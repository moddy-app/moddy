"""Discord scheduled events: lifecycle, settings and subscriptions.

Discord collapses every property of a scheduled event into a single
``on_scheduled_event_update``, so the eight tracked settings are declared as a
:class:`~serverlogs.listeners._diff.DiffRule` table and
:func:`~serverlogs.listeners._diff.emit_diffs` turns each real change into its
own log — a server can follow start-time changes without also logging every
description tweak.

The two subscription events (a member pressing "Interested", or dropping it)
come from their own gateway events and are logged with the member as subject.
"""

from __future__ import annotations

from typing import Any

import discord

from serverlogs.listeners._diff import DiffRule, default_render, emit_diffs
from serverlogs.renderer import escape, fmt_channel, fmt_number, fmt_time


# --------------------------------------------------------------------------- #
# Value renderers
# --------------------------------------------------------------------------- #

def _render_enum(value: Any, locale: str) -> str:
    if value is None:
        return default_render(value, locale)
    return f"`{value.name}`"


def _render_datetime(value: Any, locale: str) -> str:
    if value is None:
        return default_render(value, locale)
    return fmt_time(value, "F")


def _render_image(value: Any, locale: str) -> str:
    from utils.i18n import t
    url = getattr(value, "url", None)
    if not url:
        return default_render(None, locale)
    return f"[{t('modules.logs.values.open_link', locale=locale)}]({url})"


def _render_location(value: Any, locale: str) -> str:
    """External events carry a free-text location; voice/stage ones a channel."""
    if value is None:
        return default_render(value, locale)
    if isinstance(value, str):
        return escape(value)
    return fmt_channel(value)


#: Tracked scheduled-event settings — one line here is one loggable event.
EVENT_RULES = (
    DiffRule("event_name_update", "name"),
    DiffRule("event_description_update", "description", as_block=True),
    DiffRule("event_location_update", "location", render=_render_location),
    DiffRule("event_privacy_level_update", "privacy_level", render=_render_enum),
    DiffRule("event_start_time_update", "start_time", render=_render_datetime),
    DiffRule("event_end_time_update", "end_time", render=_render_datetime),
    DiffRule("event_status_update", "status", render=_render_enum),
    DiffRule("event_image_update", "cover_image", render=_render_image),
)


# --------------------------------------------------------------------------- #
# Listeners
# --------------------------------------------------------------------------- #

async def on_event_create(service, event: discord.ScheduledEvent) -> None:
    guild = event.guild
    who, reason = await service.executor(
        guild, discord.AuditLogAction.scheduled_event_create, event.id)

    entry = await service.open(guild, "event_create", actor=who)
    if entry is None:
        return
    _header(entry, event)
    entry.line("location", _render_location(event.location or event.channel,
                                            entry.locale))
    entry.line("start_time", _render_datetime(event.start_time, entry.locale))
    entry.line("end_time", _render_datetime(event.end_time, entry.locale))
    entry.line("privacy_level", _render_enum(event.privacy_level, entry.locale))
    entry.block("description", escape(event.description))
    entry.actor(who, reason)
    await service.submit(guild, entry)


async def on_event_delete(service, event: discord.ScheduledEvent) -> None:
    guild = event.guild
    who, reason = await service.executor(
        guild, discord.AuditLogAction.scheduled_event_delete, event.id)

    entry = await service.open(guild, "event_delete", actor=who)
    if entry is None:
        return
    _header(entry, event)
    entry.line("start_time", _render_datetime(event.start_time, entry.locale))
    entry.line("status", _render_enum(event.status, entry.locale))
    entry.line("subscribers", fmt_number(event.user_count))
    entry.actor(who, reason)
    await service.submit(guild, entry)


async def on_event_update(service, before: discord.ScheduledEvent,
                          after: discord.ScheduledEvent) -> None:
    guild = after.guild
    await emit_diffs(
        service, guild, EVENT_RULES, before, after,
        header=lambda entry: _header(entry, after),
        audit_action=discord.AuditLogAction.scheduled_event_update,
        audit_target_id=after.id,
    )


async def on_event_user_add(service, event: discord.ScheduledEvent,
                            user: discord.User) -> None:
    await _log_subscription(service, event, user, "event_user_subscribe")


async def on_event_user_remove(service, event: discord.ScheduledEvent,
                               user: discord.User) -> None:
    await _log_subscription(service, event, user, "event_user_unsubscribe")


async def _log_subscription(service, event: discord.ScheduledEvent,
                            user: discord.User, log_event: str) -> None:
    """A member marking themselves interested in an event, or dropping it."""
    guild = event.guild
    entry = await service.open(guild, log_event, subject=user)
    if entry is None:
        return
    entry.line("event", escape(event.name))
    entry.line("subscribers", fmt_number(event.user_count))
    entry.url(getattr(event, "url", None))
    await service.submit(guild, entry)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _header(entry, event: discord.ScheduledEvent) -> None:
    """Identity lines shared by every scheduled-event log."""
    entry.line("event", escape(event.name))
    entry.line("id", f"`{event.id}`")
    entry.url(getattr(event, "url", None))
    if event.cover_image is not None:
        entry.image(event.cover_image.url)
