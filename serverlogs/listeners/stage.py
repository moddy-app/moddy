"""Stage instances: a stage channel going live, changing, and closing.

A "stage instance" is the live session itself, not the channel: it appears
when someone starts a stage and disappears when the last speaker ends it, so
``stage_start``/``stage_end`` are the honest names for the two lifecycle
events. The channel is passed to :meth:`~serverlogs.service.LogService.open`
so a stage happening in an ignored channel is never logged.
"""

from __future__ import annotations

from typing import Any

import discord

from serverlogs.listeners._diff import DiffRule, default_render, emit_diffs
from serverlogs.renderer import escape, fmt_channel


def _render_enum(value: Any, locale: str) -> str:
    if value is None:
        return default_render(value, locale)
    return f"`{value.name}`"


#: Tracked stage-instance settings — one line here is one loggable event.
STAGE_RULES = (
    DiffRule("stage_topic_update", "topic", as_block=True),
    DiffRule("stage_privacy_update", "privacy_level", render=_render_enum),
)


async def on_stage_create(service, stage: discord.StageInstance) -> None:
    guild = stage.guild
    who, reason = await service.executor(
        guild, discord.AuditLogAction.stage_instance_create, stage.id)

    entry = await service.open(guild, "stage_start", channel=stage.channel,
                               actor=who)
    if entry is None:
        return
    _header(entry, stage)
    entry.actor(who, reason)
    await service.submit(guild, entry)


async def on_stage_delete(service, stage: discord.StageInstance) -> None:
    guild = stage.guild
    who, reason = await service.executor(
        guild, discord.AuditLogAction.stage_instance_delete, stage.id)

    entry = await service.open(guild, "stage_end", channel=stage.channel,
                               actor=who)
    if entry is None:
        return
    _header(entry, stage)
    entry.actor(who, reason)
    await service.submit(guild, entry)


async def on_stage_update(service, before: discord.StageInstance,
                          after: discord.StageInstance) -> None:
    guild = after.guild
    await emit_diffs(
        service, guild, STAGE_RULES, before, after,
        header=lambda entry: _header(entry, after),
        audit_action=discord.AuditLogAction.stage_instance_update,
        audit_target_id=after.id,
    )


def _header(entry, stage: discord.StageInstance) -> None:
    """Identity lines shared by every stage log."""
    entry.line("channel", fmt_channel(stage.channel))
    entry.line("id", f"`{stage.id}`")
    entry.line("topic", escape(stage.topic))
    entry.line("privacy_level", _render_enum(stage.privacy_level, entry.locale))
