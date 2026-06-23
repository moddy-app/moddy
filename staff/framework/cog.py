"""
StaffCommandsRouter — the single dispatcher cog for the staff command system.

Responsibilities:
- Discover and build all staff commands (message index + slash groups).
- Publish the slash groups on ``bot.staff_slash_groups`` so the per-guild sync
  (see ``bot.py``) can register them on OFFICIAL guilds only.
- Route message commands (``@Moddy d.jsk …``) to the right command.
- Provide the slash runner (``/dev jsk …``) with the shared ``incognito`` flag.
- Centralize permission checks, audit logging and error handling so individual
  commands stay focused on their behaviour.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from staff.base import StaffCommandsCog
from staff.framework import design, registry
from staff.framework.context import StaffContext
from utils.staff_permissions import staff_permissions, CommandType
from utils.staff_logger import staff_logger
from utils.i18n import t

logger = logging.getLogger("moddy.staff.router")


class StaffCommandsRouter(StaffCommandsCog):
    """Dispatches every migrated staff command across message + slash."""

    def __init__(self, bot):
        super().__init__(bot)
        self.message_index = {}
        self.subgroup_index = {}
        self.groups = []
        # Types fully owned by the new framework. Message commands of these
        # types are handled here; legacy cogs keep handling the rest.
        self.owned_types = set()

    async def setup(self):
        """Discover commands and register the slash groups on the bot."""
        registry.discover_commands()
        self.message_index, self.subgroup_index, self.groups = registry.build(self.bot, self._run_slash)
        self.owned_types = {ct for (ct, _) in self.message_index} | {ct for (ct, _) in self.subgroup_index}
        self.bot.staff_slash_groups = self.groups
        all_cmds = set(map(id, self.message_index.values()))
        for bucket in self.subgroup_index.values():
            all_cmds |= set(map(id, bucket.values()))
        logger.info("Staff router ready: %d command(s), %d slash group(s)", len(all_cmds), len(self.groups))

    def is_migrated(self, type_value: str, command_name: str) -> bool:
        """True if a (type, name) command has been migrated to the framework.

        Legacy cogs call this to defer migrated commands and avoid double
        dispatch. ``command_name`` may be a flat command or a sub-group name.
        """
        return ((type_value, command_name) in self.message_index
                or (type_value, command_name) in self.subgroup_index)

    async def cog_unload(self):
        # Drop our slash groups so a reload doesn't leave stale references.
        if getattr(self.bot, "staff_slash_groups", None) is self.groups:
            self.bot.staff_slash_groups = []

    # --- permission helpers ------------------------------------------------

    async def _has_permission(self, command, user_id: int) -> tuple[bool, str]:
        allowed, reason = await staff_permissions.check_command_permission(
            user_id, command.command_type, command.name
        )
        if not allowed:
            return False, reason
        # Optional fine-grained permission node.
        node = getattr(command, "permission", None)
        if node and not await self._has_node(user_id, node):
            return False, t("staff.common.permission_denied.description", locale="en-US")
        return True, ""

    async def _has_node(self, user_id: int, node: str) -> bool:
        # Super-admin, devs and Managers are not gated by granular nodes.
        if user_id == staff_permissions.SUPER_ADMIN_ID or self.bot.is_developer(user_id):
            return True
        from utils.staff_permissions import StaffRole
        roles = await staff_permissions.get_user_roles(user_id)
        if StaffRole.MANAGER in roles:
            return True
        if not self.bot.db:
            return False
        perms = await self.bot.db.get_staff_permissions(user_id)
        role_perms = perms.get("role_permissions", {}) or {}
        for granted in role_perms.values():
            if node in granted:
                return True
        return False

    # --- message transport -------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not staff_permissions or not self.bot.db:
            return
        parsed = staff_permissions.parse_staff_command(message.content)
        if not parsed:
            return
        command_type, command_name, args = parsed

        # Only handle types owned by the new framework; legacy cogs handle others.
        if command_type not in self.owned_types:
            return

        # Flat command, or a sub-group command (`mod.case create ...`).
        command = self.message_index.get((command_type.value, command_name))
        raw_args = args
        if command is None:
            bucket = self.subgroup_index.get((command_type.value, command_name))
            if not bucket:
                return  # not migrated — let the legacy cog handle it
            parts = (args or "").split(maxsplit=1)
            sub = parts[0].lower() if parts else ""
            raw_args = parts[1] if len(parts) > 1 else ""
            command = bucket.get(sub)
            if command is None:
                return

        allowed, reason = await self._has_permission(command, message.author.id)
        if not allowed:
            await self.reply_with_tracking(message, design.permission_denied("en-US", reason))
            return

        try:
            options = command.parse_message(raw_args)
        except Exception:
            options = {}
        ctx = StaffContext.from_message(self.bot, command, message, options, raw_args, cog=self)
        await self._invoke(command, ctx)

    # --- slash transport ---------------------------------------------------

    async def _run_slash(self, command, interaction: discord.Interaction, options: dict, incognito: bool):
        allowed, reason = await self._has_permission(command, interaction.user.id)
        ctx = StaffContext.from_interaction(self.bot, command, interaction, options, incognito, cog=self)
        if not allowed:
            await interaction.response.send_message(
                view=design.permission_denied(ctx.locale, reason), ephemeral=True
            )
            return
        await self._invoke(command, ctx)

    # --- shared invocation -------------------------------------------------

    async def _invoke(self, command, ctx: StaffContext):
        if staff_logger:
            try:
                await staff_logger.log_command(
                    command.command_type.value, command.name, ctx.author, args=command.log_args(ctx)
                )
            except Exception as exc:  # pragma: no cover - logging must never break commands
                logger.debug("staff log failed for %s.%s: %s", command.command_type.value, command.name, exc)

        try:
            await command.execute(ctx)
        except Exception as exc:
            logger.error("Error in staff command %s.%s: %s",
                         command.command_type.value, command.name, exc, exc_info=True)
            # For a not-yet-answered slash, let the global handler produce the
            # standard error view (and capture to Sentry).
            if ctx.is_slash and not ctx.interaction.response.is_done():
                raise
            try:
                await ctx.send(view=design.error(
                    t("staff.common.error.title", locale=ctx.locale),
                    t("staff.common.error.description", locale=ctx.locale),
                ))
            except Exception:
                pass


async def setup(bot):
    cog = StaffCommandsRouter(bot)
    await cog.setup()
    await bot.add_cog(cog)
