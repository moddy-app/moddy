"""`/manage customization grant` — grant/revoke staff access to the Bot
Customization identity fields (nickname/avatar/banner/bio) for a server,
independent of its premium subscription.

Stored as the ``BOT_CUSTOMIZATION`` guild attribute — see
``modules/bot_customization.py::has_identity_access`` and
``docs/BOT_CUSTOMIZATION.md``.
"""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType, parse_guild_id
from utils import emojis
from utils.i18n import t


@staff_command
class CustomizationGrantCommand(StaffCommand):
    command_type = CommandType.MANAGEMENT
    group = "customization"
    group_description = "Manage the Bot Customization feature"
    name = "grant"
    permission = "bot_customization_manage"
    description = "Grant/revoke staff access to Bot Customization identity fields for a server."
    options = [
        SlashOption("guild_id", "string", "Target guild id.", required=True),
        SlashOption("action", "string", "add or remove.", required=False, default="add",
                    choices=["add", "remove"]),
    ]

    def parse_message(self, raw: str) -> dict:
        parts = (raw or "").strip().split(None, 1)
        return {
            "guild_id": parts[0] if parts else None,
            "action": (parts[1].strip().lower() if len(parts) > 1 else "add"),
        }

    async def execute(self, ctx):
        bot = ctx.bot
        locale = ctx.locale
        gid = parse_guild_id(ctx.opt("guild_id") or "")
        action = (ctx.opt("action") or "add").lower()

        if not gid:
            await ctx.send(view=design.invalid_usage(locale, "m.customization grant <guild_id> [add|remove]"))
            return
        if not bot.db:
            await ctx.send(view=design.error(
                t("staff.common.error.title", locale=locale),
                t("staff.dev.db_unavailable", locale=locale),
            ))
            return

        guild = bot.get_guild(gid)
        guild_name = guild.name if guild else str(gid)
        enable = action != "remove"

        await bot.db.set_attribute(
            "guild", gid, "BOT_CUSTOMIZATION", True if enable else None, ctx.author.id,
            reason=f"{'Granted' if enable else 'Revoked'} Bot Customization access via staff command",
        )

        if enable:
            description = t("staff.manage.customization.granted", locale=locale,
                             name=f"**{guild_name}**", id=f"`{gid}`")
        else:
            description = t("staff.manage.customization.revoked", locale=locale,
                             name=f"**{guild_name}**", id=f"`{gid}`")
        if not guild:
            description += "\n-# " + t("staff.manage.customization.not_present", locale=locale)

        await ctx.send(view=design.success(
            t("staff.manage.customization.title", locale=locale), description, emoji=emojis.MODDY_SQUARE,
        ))
