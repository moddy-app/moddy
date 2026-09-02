"""
AltGuard cog — the bot's half of the AltGuard contract.

Three responsibilities:

1. **Verdicts.** ``bot._listen_pubsub`` hands every ``altguard:verdict``
   message to :meth:`AltGuard.handle_verdict`, which validates it, drops
   duplicates (idempotency on ``verification_id``) and lets the guild's module
   apply it.
2. **Membership events.** Joins, departures, kicks and bans are published on
   ``altguard:membership`` so the service's scoring does not keep counting
   people who are long gone. A periodic full resync repairs whatever a gateway
   disconnect lost.
3. **Server-side commands.** ``/altguard verify`` and ``/altguard unverify``
   let a guild's moderators override the gate for one member.

See docs/ALTGUARD.md.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from services.altguard_client import (
    MEMBERSHIP_ACTIVE, MEMBERSHIP_BANNED, MEMBERSHIP_KICKED, MEMBERSHIP_LEFT,
    parse_verdict,
)
from utils.altguard_views import format_member_name
from utils.components_v2 import create_error_message, create_success_message
from utils.i18n import i18n, t
from utils.members import fetch_all_members

logger = logging.getLogger('moddy.cogs.altguard')

# How far back to look in the audit log to tell a departure from a kick.
AUDIT_LOOKBACK = timedelta(seconds=10)

# Full membership reconciliation cadence. The quota is 6 calls / 10 min /
# guild, so hourly is comfortably inside it while still bounding how long a
# missed Pub/Sub event can stay wrong.
RESYNC_INTERVAL_HOURS = 1

# Delay before the startup resync, so the member cache has time to fill. A
# resync sent with a half-filled cache would mark the missing half as `left`.
STARTUP_RESYNC_DELAY = 120  # seconds


class AltGuard(commands.Cog):
    """Verdict handling, membership reporting and server-side overrides."""

    altguard_group = app_commands.Group(
        name="altguard",
        description="Manage AltGuard verifications on this server",
        guild_only=True,
        default_permissions=discord.Permissions(manage_roles=True),
    )

    def __init__(self, bot):
        self.bot = bot
        self.resync_membership.start()

    async def cog_unload(self):
        self.resync_membership.cancel()

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    async def _module(self, guild_id: int):
        """The guild's AltGuard module instance when it is configured."""
        if not self.bot.module_manager:
            return None
        try:
            module = await self.bot.module_manager.get_module_instance(guild_id, 'altguard')
        except Exception as e:
            logger.error(f"[AltGuard] Module lookup failed for guild {guild_id}: {e}")
            return None
        return module if module and module.enabled else None

    @property
    def client(self):
        return getattr(self.bot, "altguard", None)

    # ------------------------------------------------------------------ #
    # Verdicts (called from bot._listen_pubsub)
    # ------------------------------------------------------------------ #

    async def handle_verdict(self, data: Dict[str, Any]) -> None:
        """Apply one ``altguard:verdict`` message."""
        verdict = parse_verdict(data)
        if verdict is None:
            logger.warning(f"[AltGuard] Ignoring malformed verdict payload: {data}")
            return

        module = await self._module(verdict['guild_id'])
        if module is None:
            logger.debug(
                f"[AltGuard] Verdict for guild {verdict['guild_id']} but the module "
                f"is not configured there — ignored"
            )
            return

        if self.bot.db:
            # A replayed message (Redis reconnect, service retry) inserts
            # nothing and stops here: no second role change, no second log.
            fresh = await self.bot.db.record_altguard_verification(
                verification_id=verdict['verification_id'],
                guild_id=verdict['guild_id'],
                user_id=verdict['user_id'],
                verdict=verdict['verdict'],
                score=verdict['score'],
                reasons=verdict['reasons'],
                enforced=verdict['enforced'],
            )
            if not fresh:
                logger.debug(
                    f"[AltGuard] Duplicate verdict {verdict['verification_id']} — ignored"
                )
                return

        await module.apply_verdict(verdict)
        logger.info(
            f"[AltGuard] Verdict {verdict['verdict']} applied for user "
            f"{verdict['user_id']} in guild {verdict['guild_id']} "
            f"(enforced={verdict['enforced']})"
        )

    # ------------------------------------------------------------------ #
    # Membership events
    # ------------------------------------------------------------------ #

    async def _publish(self, guild_id: int, user_id: int, state: str,
                       occurred_at: Optional[datetime] = None) -> None:
        client = self.client
        if client is None:
            return
        if await self._module(guild_id) is None:
            return  # guild does not use AltGuard: nothing to report
        await client.publish_membership(
            guild_id=guild_id, discord_user_id=user_id,
            state=state, occurred_at=occurred_at,
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        await self._publish(member.guild.id, member.id, MEMBERSHIP_ACTIVE,
                            member.joined_at or datetime.now(timezone.utc))

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Report a departure, distinguishing a kick from a voluntary leave.

        Discord does not say which it was, so the audit log is consulted. A
        ban arrives separately through ``on_member_ban`` and is skipped here to
        avoid reporting the same exit twice.
        """
        if member.bot:
            return

        state, occurred_at = await self._classify_removal(member)
        if state is None:
            return
        await self._publish(member.guild.id, member.id, state, occurred_at)

    async def _classify_removal(self, member: discord.Member):
        """(state, occurred_at) for a member_remove — None to skip the event."""
        now = datetime.now(timezone.utc)
        guild = member.guild

        if not guild.me.guild_permissions.view_audit_log:
            return MEMBERSHIP_LEFT, now

        cutoff = now - AUDIT_LOOKBACK
        try:
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.ban):
                if entry.target and entry.target.id == member.id and entry.created_at >= cutoff:
                    # on_member_ban reports this one; not twice.
                    return None, None
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.kick):
                if entry.target and entry.target.id == member.id and entry.created_at >= cutoff:
                    return MEMBERSHIP_KICKED, entry.created_at
        except discord.Forbidden:
            pass
        except Exception as e:
            logger.debug(f"[AltGuard] Audit log lookup failed in guild {guild.id}: {e}")

        return MEMBERSHIP_LEFT, now

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        """Hide a brand-new channel from unverified members.

        The gate is only as tight as its newest channel: without this, every
        channel created after the setup would be visible to everyone waiting
        behind it.
        """
        module = await self._module(channel.guild.id)
        if module is None:
            return
        await module.sync_channel(channel)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        if user.bot:
            return
        await self._publish(guild.id, user.id, MEMBERSHIP_BANNED)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        """A lifted ban is the only thing that can bring an account back.

        The service never un-bans on its own, and a resync deliberately leaves
        banned accounts alone, so this explicit event is what restores them —
        but only once they actually rejoin, which produces ``active``. Nothing
        is published here beyond a debug trace.
        """
        logger.debug(f"[AltGuard] Ban lifted for {user.id} in guild {guild.id}")

    # ------------------------------------------------------------------ #
    # Periodic reconciliation
    # ------------------------------------------------------------------ #

    @tasks.loop(hours=RESYNC_INTERVAL_HOURS)
    async def resync_membership(self):
        """Send the full member list of every AltGuard guild to the service."""
        client = self.client
        if client is None or not client.configured:
            return

        for guild in list(self.bot.guilds):
            await self.resync_guild(guild)

    async def resync_guild(self, guild: discord.Guild) -> bool:
        """Send one guild's full member list to the service.

        Also called right after a configuration is pushed from the dashboard, so
        a gate that just went live does not wait up to an hour for the service to
        learn who is already in the guild.

        Returns True when the service acknowledged the reconciliation.
        """
        client = self.client
        if client is None or not client.configured:
            return False

        module = await self._module(guild.id)
        if module is None:
            return False

        # A partially-filled cache would mark real members as `left`, so the list
        # is pulled in full over the gateway rather than reconciled against a
        # lie. Guilds are not chunked at startup any more (see
        # config.CHUNK_GUILDS_AT_STARTUP), and only guilds that actually run the
        # gate reach this point -- so this is the one place that pays for a full
        # member list, once an hour, without keeping it resident.
        members = await fetch_all_members(guild, cache=False)
        if members is None:
            logger.debug(
                f"[AltGuard] Skipping resync for guild {guild.id}: "
                f"member list unavailable"
            )
            return False

        member_ids = [m.id for m in members if not m.bot]
        try:
            reconciled = await client.resync_membership(guild.id, member_ids)
            logger.info(
                f"[AltGuard] Membership resynced for guild {guild.id} "
                f"({len(member_ids)} members)"
            )
            return reconciled
        except Exception as e:
            logger.error(f"[AltGuard] Resync failed for guild {guild.id}: {e}")
            return False

    @resync_membership.before_loop
    async def before_resync(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(STARTUP_RESYNC_DELAY)

    # ------------------------------------------------------------------ #
    # Server-side commands
    # ------------------------------------------------------------------ #

    @altguard_group.command(name="verify", description="Manually mark a member as verified")
    @app_commands.describe(member="The member to let through the verification gate")
    async def verify(self, interaction: discord.Interaction, member: discord.Member):
        await self._manual_decision(interaction, member, verify=True)

    @altguard_group.command(name="unverify", description="Send a member back behind the verification gate")
    @app_commands.describe(member="The member to un-verify")
    async def unverify(self, interaction: discord.Interaction, member: discord.Member):
        await self._manual_decision(interaction, member, verify=False)

    async def _manual_decision(self, interaction: discord.Interaction,
                               member: discord.Member, *, verify: bool):
        locale = i18n.get_user_locale(interaction)

        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message(
                view=create_error_message(
                    t('modules.altguard.errors.no_perms.title', locale=locale),
                    t('modules.altguard.errors.no_perms.description', locale=locale),
                ),
                ephemeral=True,
            )
            return

        module = await self._module(interaction.guild_id)
        if module is None:
            await interaction.response.send_message(
                view=create_error_message(
                    t('modules.altguard.errors.not_configured.title', locale=locale),
                    t('modules.altguard.errors.not_configured.description', locale=locale),
                ),
                ephemeral=True,
            )
            return

        if member.bot:
            await interaction.response.send_message(
                view=create_error_message(
                    t('modules.altguard.errors.bot_target.title', locale=locale),
                    t('modules.altguard.errors.bot_target.description', locale=locale),
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        if verify:
            await module.verify_member(member, actor_id=interaction.user.id)
        else:
            await module.unverify_member(member, actor_id=interaction.user.id)

        name = await format_member_name(self.bot, member)
        key = 'verified' if verify else 'unverified'
        await interaction.followup.send(
            view=create_success_message(
                t(f'modules.altguard.manual.{key}.title', locale=locale),
                t(f'modules.altguard.manual.{key}.description', locale=locale,
                  name=name, id=f"`{member.id}`"),
            ),
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(AltGuard(bot))
