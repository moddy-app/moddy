"""Advanced server logs — Discord wiring.

This cog is deliberately dumb: it owns the ``@commands.Cog.listener()``
methods and forwards each one to a builder in :mod:`serverlogs.listeners`.
Nothing here decides what a log says, where it goes or what it looks like —
that lives in ``serverlogs/`` (see ``docs/LOGS.md``).

Keeping the wiring separate is what makes the system editable: adding a
field to a log means touching one small builder, and adding an event means
touching the registry plus one builder — never this file's plumbing.
"""

from __future__ import annotations

import logging
import re
from typing import List

import discord
from discord.ext import commands

from serverlogs.listeners import (
    automod as automod_listener,
    assets as assets_listener,
    channels as channels_listener,
    guild as guild_listener,
    integrations as integrations_listener,
    invites as invites_listener,
    members as members_listener,
    messages as messages_listener,
    polls as polls_listener,
    roles as roles_listener,
    scheduled_events as events_listener,
    stage as stage_listener,
    threads as threads_listener,
    voice as voice_listener,
)
from serverlogs.service import LogService

logger = logging.getLogger('moddy.cogs.logs')

#: Discord invite links posted in a message (invites.invite_post).
_INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord(?:app)?\.com/invite|discord\.gg|discord\.me)/"
    r"([a-zA-Z0-9\-_]{2,32})",
    re.IGNORECASE,
)

#: Audit-log action id for a voice channel status change. Discord ships no
#: gateway event for it and discord.py has no enum member, so it is matched
#: on the raw value — harmless if Discord ever removes it.
_VOICE_STATUS_ACTION = 192


class ServerLogs(commands.Cog):
    """Forwards Discord events to the server-logs builders."""

    def __init__(self, bot):
        self.bot = bot
        self.service = LogService(bot)

    async def cog_unload(self) -> None:
        await self.service.close()

    # ------------------------------------------------------------------ #
    # Audit log — the source of "who did it" for every other listener
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        # Cache first: another listener may already be waiting on this entry.
        self.service.audit.add(entry)

        guild = entry.guild
        if guild is None:
            return

        action = entry.action
        try:
            if action is discord.AuditLogAction.member_prune:
                await members_listener.on_member_prune(self.service, guild, entry)
            elif action is discord.AuditLogAction.member_move:
                await voice_listener.on_voice_move(self.service, guild, entry)
            elif action is discord.AuditLogAction.member_disconnect:
                await voice_listener.on_voice_kick(self.service, guild, entry)
            elif action in (discord.AuditLogAction.webhook_create,
                            discord.AuditLogAction.webhook_update,
                            discord.AuditLogAction.webhook_delete):
                await integrations_listener.on_webhook_audit(self.service, guild, entry)
            elif action in (discord.AuditLogAction.bot_add,
                            discord.AuditLogAction.integration_create):
                await integrations_listener.on_app_audit(self.service, guild, entry, "app_add")
            elif action is discord.AuditLogAction.integration_delete:
                await integrations_listener.on_app_audit(self.service, guild, entry, "app_remove")
            elif action is discord.AuditLogAction.automod_rule_update:
                await automod_listener.on_automod_rule_audit(self.service, guild, entry)
            elif action is discord.AuditLogAction.onboarding_update:
                await guild_listener.on_onboarding_update(self.service, guild, entry)
            elif action is discord.AuditLogAction.onboarding_prompt_create:
                await guild_listener.on_onboarding_prompt(
                    self.service, guild, entry, "onboarding_question_add")
            elif action is discord.AuditLogAction.onboarding_prompt_delete:
                await guild_listener.on_onboarding_prompt(
                    self.service, guild, entry, "onboarding_question_remove")
            elif action is discord.AuditLogAction.onboarding_prompt_update:
                await guild_listener.on_onboarding_prompt(
                    self.service, guild, entry, "onboarding_question_update")
            elif getattr(action, "value", None) == _VOICE_STATUS_ACTION:
                await channels_listener.on_voice_status_update(self.service, guild, entry)
        except Exception as e:
            _report(f"audit entry {action}", e)

    # ------------------------------------------------------------------ #
    # Members
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await _safe(members_listener.on_member_join(self.service, member), "member_join")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await _safe(members_listener.on_member_remove(self.service, member), "member_remove")

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.abc.User):
        await _safe(members_listener.on_member_ban(self.service, guild, user), "member_ban")

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.abc.User):
        await _safe(members_listener.on_member_unban(self.service, guild, user), "member_unban")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        await _safe(members_listener.on_member_update(self.service, before, after),
                    "member_update")

    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User):
        shared = [g for g in self.bot.guilds if g.get_member(after.id) is not None]
        if not shared:
            return
        await _safe(members_listener.on_user_update(self.service, shared, before, after),
                    "user_update")

    # ------------------------------------------------------------------ #
    # Messages
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.id == self.bot.user.id:
            return

        codes = _INVITE_RE.findall(message.content or "")
        if codes:
            await _safe(invites_listener.on_invite_post(self.service, message, codes),
                        "invite_post")

        if message.poll is not None:
            await _safe(polls_listener.on_poll_create(self.service, message), "poll_create")

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.guild is None:
            return
        await _safe(messages_listener.on_message_delete(self.service, message),
                    "message_delete")
        if message.poll is not None:
            await _safe(polls_listener.on_poll_delete(self.service, message), "poll_delete")

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        # The cached path above already logged it, with its content.
        if payload.cached_message is not None or payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        await _safe(messages_listener.on_raw_message_delete(self.service, guild, payload),
                    "raw_message_delete")

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: List[discord.Message]):
        await _safe(messages_listener.on_bulk_message_delete(self.service, messages),
                    "bulk_message_delete")

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent):
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        await _safe(
            messages_listener.on_raw_bulk_message_delete(self.service, guild, payload),
            "raw_bulk_message_delete")

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if after.guild is None:
            return

        if before.content != after.content:
            await _safe(messages_listener.on_message_edit(self.service, before, after),
                        "message_edit")

        # A crosspost is delivered as an edit setting the CROSSPOSTED flag.
        if not before.flags.crossposted and after.flags.crossposted:
            await _safe(messages_listener.on_message_publish(self.service, after),
                        "message_publish")

        if _poll_just_finalised(before, after):
            await _safe(polls_listener.on_poll_finalize(self.service, after),
                        "poll_finalize")

    @commands.Cog.listener()
    async def on_app_command_completion(self, interaction: discord.Interaction, command):
        await _safe(messages_listener.on_command_used(self.service, interaction),
                    "command_used")

    # ------------------------------------------------------------------ #
    # Channels and threads
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        await _safe(channels_listener.on_channel_create(self.service, channel),
                    "channel_create")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        await _safe(channels_listener.on_channel_delete(self.service, channel),
                    "channel_delete")

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel,
                                      after: discord.abc.GuildChannel):
        await _safe(channels_listener.on_channel_update(self.service, before, after),
                    "channel_update")

    @commands.Cog.listener()
    async def on_guild_channel_pins_update(self, channel, last_pin):
        await _safe(channels_listener.on_channel_pins_update(self.service, channel, last_pin),
                    "channel_pins_update")

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        await _safe(threads_listener.on_thread_create(self.service, thread), "thread_create")

    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread):
        await _safe(threads_listener.on_thread_delete(self.service, thread), "thread_delete")

    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread):
        await _safe(threads_listener.on_thread_update(self.service, before, after),
                    "thread_update")

    # ------------------------------------------------------------------ #
    # Roles, server, voice
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        await _safe(roles_listener.on_role_create(self.service, role), "role_create")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        await _safe(roles_listener.on_role_delete(self.service, role), "role_delete")

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        await _safe(roles_listener.on_role_update(self.service, before, after), "role_update")

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        await _safe(guild_listener.on_guild_update(self.service, before, after),
                    "guild_update")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member,
                                    before: discord.VoiceState,
                                    after: discord.VoiceState):
        await _safe(voice_listener.on_voice_state_update(self.service, member, before, after),
                    "voice_state_update")

    # ------------------------------------------------------------------ #
    # Invites, emojis, stickers, sounds
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        await _safe(invites_listener.on_invite_create(self.service, invite), "invite_create")

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        await _safe(invites_listener.on_invite_delete(self.service, invite), "invite_delete")

    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild: discord.Guild, before, after):
        await _safe(assets_listener.on_emojis_update(self.service, guild, before, after),
                    "emojis_update")

    @commands.Cog.listener()
    async def on_guild_stickers_update(self, guild: discord.Guild, before, after):
        await _safe(assets_listener.on_stickers_update(self.service, guild, before, after),
                    "stickers_update")

    @commands.Cog.listener()
    async def on_soundboard_sound_create(self, sound):
        await _safe(assets_listener.on_soundboard_sound_create(self.service, sound),
                    "sound_create")

    @commands.Cog.listener()
    async def on_soundboard_sound_delete(self, sound):
        await _safe(assets_listener.on_soundboard_sound_delete(self.service, sound),
                    "sound_delete")

    @commands.Cog.listener()
    async def on_soundboard_sound_update(self, before, after):
        await _safe(assets_listener.on_soundboard_sound_update(self.service, before, after),
                    "sound_update")

    # ------------------------------------------------------------------ #
    # Scheduled events, stages, polls
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_scheduled_event_create(self, event):
        await _safe(events_listener.on_event_create(self.service, event), "event_create")

    @commands.Cog.listener()
    async def on_scheduled_event_delete(self, event):
        await _safe(events_listener.on_event_delete(self.service, event), "event_delete")

    @commands.Cog.listener()
    async def on_scheduled_event_update(self, before, after):
        await _safe(events_listener.on_event_update(self.service, before, after),
                    "event_update")

    @commands.Cog.listener()
    async def on_scheduled_event_user_add(self, event, user):
        await _safe(events_listener.on_event_user_add(self.service, event, user),
                    "event_user_add")

    @commands.Cog.listener()
    async def on_scheduled_event_user_remove(self, event, user):
        await _safe(events_listener.on_event_user_remove(self.service, event, user),
                    "event_user_remove")

    @commands.Cog.listener()
    async def on_stage_instance_create(self, stage):
        await _safe(stage_listener.on_stage_create(self.service, stage), "stage_create")

    @commands.Cog.listener()
    async def on_stage_instance_delete(self, stage):
        await _safe(stage_listener.on_stage_delete(self.service, stage), "stage_delete")

    @commands.Cog.listener()
    async def on_stage_instance_update(self, before, after):
        await _safe(stage_listener.on_stage_update(self.service, before, after),
                    "stage_update")

    @commands.Cog.listener()
    async def on_raw_poll_vote_add(self, payload):
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        if guild is None:
            return
        await _safe(polls_listener.on_poll_vote(self.service, guild, payload, True),
                    "poll_vote_add")

    @commands.Cog.listener()
    async def on_raw_poll_vote_remove(self, payload):
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        if guild is None:
            return
        await _safe(polls_listener.on_poll_vote(self.service, guild, payload, False),
                    "poll_vote_remove")

    # ------------------------------------------------------------------ #
    # Discord AutoMod and applications
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_automod_rule_create(self, rule):
        await _safe(automod_listener.on_automod_rule_create(self.service, rule),
                    "automod_rule_create")

    @commands.Cog.listener()
    async def on_automod_rule_delete(self, rule):
        await _safe(automod_listener.on_automod_rule_delete(self.service, rule),
                    "automod_rule_delete")

    @commands.Cog.listener()
    async def on_automod_action(self, execution):
        await _safe(automod_listener.on_automod_action(self.service, execution),
                    "automod_action")

    @commands.Cog.listener()
    async def on_raw_app_command_permissions_update(self, payload):
        guild = getattr(payload, "guild", None)
        if guild is None:
            return
        await _safe(
            integrations_listener.on_app_command_permissions_update(
                self.service, guild, payload),
            "app_command_permissions_update")


def _poll_just_finalised(before: discord.Message, after: discord.Message) -> bool:
    if after.poll is None:
        return False
    try:
        if not after.poll.is_finalised():
            return False
        return before.poll is None or not before.poll.is_finalised()
    except AttributeError:
        return False


async def _safe(coro, label: str) -> None:
    """Run a builder; a broken log must never take a gateway handler down."""
    try:
        await coro
    except Exception as e:
        _report(label, e)


def _report(label: str, error: Exception) -> None:
    logger.error(f"[Logs] Listener {label} failed: {error}", exc_info=True)


async def setup(bot):
    await bot.add_cog(ServerLogs(bot))
