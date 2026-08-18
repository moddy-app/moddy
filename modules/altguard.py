"""
AltGuard module — anti multi-account verification gate.

A new member lands with a single role (``unverified_role_id``) that only opens
the verification channel. There they click one button, accept the consent
modal, receive a personal single-use link, and the external **AltGuard**
service decides. Its verdict arrives on the Redis channel ``altguard:verdict``
(consumed in ``bot._listen_pubsub`` and dispatched to
:meth:`AltGuardModule.apply_verdict`).

What this module owns:

- the guild configuration (channel, both roles, optional log channel, panel
  language) and the single verification panel message, re-posted on every save
  so a deleted panel repairs itself;
- the roles: gating on join, promoting on ``passed``, manual staff overrides;
- the local state (``altguard_members``) other features read — notably
  ``modules/auto_role.py``, which must not hand out its roles to a human who
  has not passed the gate.

What it deliberately does NOT own: the decision. Score and reasons are audit
data, never shown to the verified user (that would hand out a bypass oracle).
See docs/ALTGUARD.md.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import discord

from db.repositories.altguard import (
    SOURCE_MODDY_STAFF, SOURCE_SERVER_STAFF, SOURCE_SERVICE,
    STATUS_BLOCKED, STATUS_FLAGGED, STATUS_PENDING, STATUS_VERIFIED,
    VERDICT_PASSED, VERDICT_TO_STATUS,
)
from modules.module_manager import ModuleBase
from utils.emojis import SHIELD

logger = logging.getLogger('moddy.modules.altguard')

MODULE_ID = "altguard"

# Languages the verification panel can be rendered in. Guild admins choose the
# language and nothing else: the panel wording is fixed so every server states
# the same thing about the same data processing.
PANEL_LOCALES = ("fr", "en-US", "es-ES", "pt-BR", "de")
DEFAULT_PANEL_LOCALE = "en-US"

# Accent colour of the panel container (matches the spec: 5793266 / #5865F2).
PANEL_ACCENT = 0x5865F2


class AltGuardModule(ModuleBase):
    """Anti multi-account verification gate."""

    MODULE_ID = MODULE_ID
    MODULE_NAME = "AltGuard"
    MODULE_DESCRIPTION = "Anti multi-account verification before server access"
    # The /config picker uses the shield (it is a protection module); the
    # animated AltGuard face is reserved for the panel message itself.
    MODULE_EMOJI = SHIELD

    def __init__(self, bot, guild_id: int):
        super().__init__(bot, guild_id)

        self.channel_id: Optional[int] = None
        self.unverified_role_id: Optional[int] = None
        self.verified_role_id: Optional[int] = None
        self.log_channel_id: Optional[int] = None
        self.panel_locale: str = DEFAULT_PANEL_LOCALE
        self.message_id: Optional[int] = None

    # ------------------------------------------------------------------ #
    # ModuleBase contract
    # ------------------------------------------------------------------ #

    async def load_config(self, config_data: Dict[str, Any]) -> bool:
        try:
            self.config = config_data
            self.channel_id = config_data.get('channel_id')
            self.unverified_role_id = config_data.get('unverified_role_id')
            self.verified_role_id = config_data.get('verified_role_id')
            self.log_channel_id = config_data.get('log_channel_id')
            panel_locale = config_data.get('panel_locale') or DEFAULT_PANEL_LOCALE
            self.panel_locale = panel_locale if panel_locale in PANEL_LOCALES else DEFAULT_PANEL_LOCALE
            self.message_id = config_data.get('message_id')

            # The gate is meaningless without all three: somewhere to verify,
            # a role that holds people back, a role that lets them in.
            self.enabled = bool(
                self.channel_id and self.unverified_role_id and self.verified_role_id
            )
            return True
        except Exception as e:
            logger.error(f"Error loading altguard config: {e}")
            return False

    def get_default_config(self) -> Dict[str, Any]:
        return {
            'channel_id': None,
            'unverified_role_id': None,
            'verified_role_id': None,
            'log_channel_id': None,
            'panel_locale': DEFAULT_PANEL_LOCALE,
            'message_id': None,
        }

    def get_required_fields(self) -> List[str]:
        return ['channel_id', 'unverified_role_id', 'verified_role_id']

    async def validate_config(self, config_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        guild = self.bot.get_guild(self.guild_id) if self.bot else None
        if not guild:
            return False, "Server not found"

        if not guild.me.guild_permissions.manage_roles:
            return False, "I need the Manage Roles permission to run the verification gate"

        channel = guild.get_channel(config_data.get('channel_id')) if config_data.get('channel_id') else None
        if not channel:
            return False, "Verification channel not found"
        if not isinstance(channel, discord.TextChannel):
            return False, "The verification channel must be a text channel"

        perms = channel.permissions_for(guild.me)
        if not (perms.view_channel and perms.send_messages):
            return False, f"I cannot post in {channel.mention}"

        unverified_id = config_data.get('unverified_role_id')
        verified_id = config_data.get('verified_role_id')
        if unverified_id and verified_id and unverified_id == verified_id:
            return False, "The unverified and verified roles must be different"

        for key, label in (('unverified_role_id', "unverified"), ('verified_role_id', "verified")):
            role_id = config_data.get(key)
            if not role_id:
                continue
            role = guild.get_role(role_id)
            if not role:
                return False, f"The {label} role no longer exists"
            if role.id == guild.id:
                return False, "@everyone cannot be used as a verification role"
            if role.managed:
                return False, f"The {label} role is managed by an integration and cannot be assigned"
            if role >= guild.me.top_role:
                return False, f"I cannot assign {role.mention}: it is above my highest role"

        log_channel_id = config_data.get('log_channel_id')
        if log_channel_id:
            log_channel = guild.get_channel(log_channel_id)
            if not log_channel:
                return False, "Log channel not found"
            if not log_channel.permissions_for(guild.me).send_messages:
                return False, f"I cannot post in {log_channel.mention}"

        panel_locale = config_data.get('panel_locale')
        if panel_locale and panel_locale not in PANEL_LOCALES:
            return False, f"Unsupported panel language: {panel_locale}"

        return True, None

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @property
    def guild(self) -> Optional[discord.Guild]:
        return self.bot.get_guild(self.guild_id) if self.bot else None

    def _role(self, role_id: Optional[int]) -> Optional[discord.Role]:
        guild = self.guild
        if not guild or not role_id:
            return None
        return guild.get_role(role_id)

    async def get_status(self, user_id: int) -> str:
        """Gate state of a member in this guild (``pending`` when unknown)."""
        if not self.bot.db:
            return STATUS_PENDING
        row = await self.bot.db.get_altguard_member(self.guild_id, user_id)
        return row['status'] if row else STATUS_PENDING

    async def is_verified(self, user_id: int) -> bool:
        if not self.bot.db:
            return False
        return await self.bot.db.is_altguard_verified(self.guild_id, user_id)

    async def _persist(self, updates: Dict[str, Any]) -> None:
        """Write config keys the panel does not own (currently ``message_id``).

        Goes straight through ``update_guild_data`` rather than
        ``save_module_config``: this is bookkeeping, not a configuration
        change, and re-running validation here could refuse to store the id of
        a message that was just posted successfully.
        """
        if not self.bot.db:
            return
        config = dict(self.config or {})
        config.update(updates)
        self.config = config
        await self.bot.db.update_guild_data(self.guild_id, f"modules.{MODULE_ID}", config)
        await self.load_config(config)

    # ------------------------------------------------------------------ #
    # Verification panel
    # ------------------------------------------------------------------ #

    async def refresh_panel(self) -> Optional[discord.Message]:
        """Delete the previous panel and post a fresh one.

        Called on every save of the configuration. Re-posting unconditionally is
        the point: it repairs a panel someone deleted, and it is the only way a
        language change or a channel change reaches the message. A panel that
        can no longer be found is simply replaced.
        """
        from utils.altguard_views import build_verification_panel

        guild = self.guild
        if not guild or not self.channel_id:
            return None

        channel = guild.get_channel(self.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return None

        await self.delete_panel()

        try:
            message = await channel.send(view=build_verification_panel(self.panel_locale))
        except discord.Forbidden:
            logger.warning(f"[AltGuard] Cannot post the panel in guild {self.guild_id}")
            return None
        except Exception as e:
            logger.error(f"[AltGuard] Failed to post the panel in guild {self.guild_id}: {e}")
            return None

        await self._persist({'message_id': message.id})
        return message

    async def delete_panel(self) -> None:
        """Remove the currently stored panel message, if it still exists."""
        guild = self.guild
        if not guild or not self.message_id or not self.channel_id:
            return

        channel = guild.get_channel(self.channel_id)
        if isinstance(channel, discord.TextChannel):
            try:
                message = await channel.fetch_message(self.message_id)
                await message.delete()
            except (discord.NotFound, discord.Forbidden):
                pass
            except Exception as e:
                logger.debug(f"[AltGuard] Could not delete old panel in guild {self.guild_id}: {e}")

        await self._persist({'message_id': None})

    # ------------------------------------------------------------------ #
    # Role plumbing
    # ------------------------------------------------------------------ #

    async def _apply_gate_roles(self, member: discord.Member, *, verified: bool,
                                reason: str) -> None:
        """Put the member on the right side of the gate (idempotent)."""
        unverified = self._role(self.unverified_role_id)
        verified_role = self._role(self.verified_role_id)

        to_add = []
        to_remove = []
        if verified:
            if verified_role and verified_role not in member.roles:
                to_add.append(verified_role)
            if unverified and unverified in member.roles:
                to_remove.append(unverified)
        else:
            if unverified and unverified not in member.roles:
                to_add.append(unverified)
            if verified_role and verified_role in member.roles:
                to_remove.append(verified_role)

        try:
            if to_add:
                await member.add_roles(*to_add, reason=reason)
            if to_remove:
                await member.remove_roles(*to_remove, reason=reason)
        except discord.Forbidden:
            logger.warning(
                f"[AltGuard] Missing permissions to move {member.id} through the gate "
                f"in guild {self.guild_id}"
            )
        except Exception as e:
            logger.error(f"[AltGuard] Role update failed for {member.id} in guild {self.guild_id}: {e}")

    # ------------------------------------------------------------------ #
    # Events
    # ------------------------------------------------------------------ #

    async def on_member_join(self, member: discord.Member) -> None:
        """Gate a joining member (bots are never gated).

        A member who already passed the gate in this guild — someone who left
        and came back — goes straight to the verified role rather than being
        asked to verify again.
        """
        if not self.enabled or member.bot:
            return

        if await self.is_verified(member.id):
            await self._apply_gate_roles(member, verified=True, reason="AltGuard: already verified")
            return

        if self.bot.db:
            existing = await self.bot.db.get_altguard_member(self.guild_id, member.id)
            if not existing:
                await self.bot.db.set_altguard_member_status(
                    self.guild_id, member.id, STATUS_PENDING, source=SOURCE_SERVICE,
                )

        await self._apply_gate_roles(member, verified=False, reason="AltGuard: verification pending")

    # ------------------------------------------------------------------ #
    # Verdicts
    # ------------------------------------------------------------------ #

    async def apply_verdict(self, verdict: Dict[str, Any]) -> None:
        """Act on one ``altguard:verdict`` message (already parsed & validated).

        ``enforced=False`` is the service's shadow mode: the guild has not
        switched enforcement on, so the verdict is logged and recorded but
        **nothing** is applied — no role change, no message to the member.
        """
        from utils.altguard_views import build_log_card

        user_id = verdict['user_id']
        decision = verdict['verdict']
        enforced = verdict['enforced']

        guild = self.guild
        member = guild.get_member(user_id) if guild else None

        if enforced and self.bot.db:
            await self.bot.db.set_altguard_member_status(
                self.guild_id, user_id, VERDICT_TO_STATUS[decision],
                source=SOURCE_SERVICE, verification_id=verdict['verification_id'],
            )

        if enforced and member is not None:
            passed = decision == VERDICT_PASSED
            await self._apply_gate_roles(
                member, verified=passed, reason=f"AltGuard verdict: {decision}",
            )
            if passed:
                await self._run_auto_role(member)

        await self.log_event(build_log_card(
            locale=self.panel_locale,
            kind="verdict",
            user_id=user_id,
            verdict=decision,
            score=verdict['score'],
            reasons=verdict['reasons'],
            enforced=enforced,
            verification_id=verdict['verification_id'],
        ))

    async def _run_auto_role(self, member: discord.Member) -> None:
        """Hand the member over to Auto Role, which was held back until now.

        Auto Role skips humans that have not passed the gate (see
        ``modules/auto_role.py``), so its roles are applied here instead — the
        moment the gate opens.
        """
        try:
            auto_role = await self.bot.module_manager.get_module_instance(self.guild_id, 'auto_role')
            if auto_role and auto_role.enabled:
                await auto_role.apply_roles(member)
        except Exception as e:
            logger.error(f"[AltGuard] Auto Role hand-off failed for {member.id}: {e}")

    # ------------------------------------------------------------------ #
    # Manual staff decisions
    # ------------------------------------------------------------------ #

    async def verify_member(self, member: discord.Member, *, actor_id: int,
                            moddy_staff: bool = False) -> None:
        """Manually let a member through (server moderator or Moddy staff)."""
        source = SOURCE_MODDY_STAFF if moddy_staff else SOURCE_SERVER_STAFF
        if self.bot.db:
            await self.bot.db.set_altguard_member_status(
                self.guild_id, member.id, STATUS_VERIFIED,
                source=source, decided_by=actor_id,
            )
        await self._apply_gate_roles(
            member, verified=True, reason=f"AltGuard: manual verification by {actor_id}",
        )
        await self._run_auto_role(member)

        from utils.altguard_views import build_log_card
        await self.log_event(build_log_card(
            locale=self.panel_locale, kind="manual_verify",
            user_id=member.id, actor_id=actor_id, moddy_staff=moddy_staff,
        ))

    async def unverify_member(self, member: discord.Member, *, actor_id: int,
                              moddy_staff: bool = False) -> None:
        """Send a member back behind the gate."""
        source = SOURCE_MODDY_STAFF if moddy_staff else SOURCE_SERVER_STAFF
        if self.bot.db:
            await self.bot.db.set_altguard_member_status(
                self.guild_id, member.id, STATUS_PENDING,
                source=source, decided_by=actor_id,
            )
        await self._apply_gate_roles(
            member, verified=False, reason=f"AltGuard: manual unverification by {actor_id}",
        )

        from utils.altguard_views import build_log_card
        await self.log_event(build_log_card(
            locale=self.panel_locale, kind="manual_unverify",
            user_id=member.id, actor_id=actor_id, moddy_staff=moddy_staff,
        ))

    # ------------------------------------------------------------------ #
    # Logging
    # ------------------------------------------------------------------ #

    async def log_event(self, view) -> None:
        """Post a card in the optional log channel; never raises."""
        guild = self.guild
        if not guild or not self.log_channel_id or view is None:
            return
        channel = guild.get_channel(self.log_channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        try:
            await channel.send(view=view)
        except discord.Forbidden:
            logger.info(f"[AltGuard] Cannot post logs in guild {self.guild_id}")
        except Exception as e:
            logger.error(f"[AltGuard] Log post failed in guild {self.guild_id}: {e}")


__all__ = [
    "AltGuardModule", "MODULE_ID", "PANEL_LOCALES", "DEFAULT_PANEL_LOCALE",
    "PANEL_ACCENT", "STATUS_PENDING", "STATUS_VERIFIED", "STATUS_FLAGGED",
    "STATUS_BLOCKED",
]
