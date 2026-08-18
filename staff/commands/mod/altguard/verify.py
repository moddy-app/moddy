"""`/mod altguard verify` — let a member through the AltGuard gate manually."""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from staff.commands.mod.altguard._shared import (
    GROUP, GROUP_DESCRIPTION, PERMISSION, resolve_module, resolve_target,
)
from utils.altguard_views import format_member_name
from utils.i18n import t


@staff_command
class AltGuardVerifyCommand(StaffCommand):
    command_type = CommandType.MODERATOR
    group = GROUP
    group_description = GROUP_DESCRIPTION
    name = "verify"
    permission = PERMISSION
    description = "Manually mark a member as AltGuard-verified on a server."
    options = [
        SlashOption("user", "string", "User id or mention.", required=True),
        SlashOption("guild", "string", "Server id where the member is gated.", required=True),
    ]

    def parse_message(self, raw: str) -> dict:
        parts = (raw or "").split()
        return {"user": parts[0] if parts else "", "guild": parts[1] if len(parts) > 1 else ""}

    async def execute(self, ctx):
        guild, member, error = await resolve_target(ctx)
        if error:
            await ctx.send(view=error)
            return

        module, error = await resolve_module(ctx, guild.id)
        if error:
            await ctx.send(view=error)
            return

        await module.verify_member(member, actor_id=ctx.author.id, moddy_staff=True)

        name = await format_member_name(ctx.bot, member)
        await ctx.send(view=design.success(
            t("staff.mod.altguard.verified_title", locale=ctx.locale),
            t("staff.mod.altguard.verified", locale=ctx.locale,
              name=name, id=f"`{member.id}`", guild=f"`{guild.name}`"),
        ))
