"""`/dev official` — mark or unmark a server as an OFFICIAL Moddy server.

OFFICIAL servers are the only places where staff slash commands (/dev, /team, …)
are synced. Toggling re-syncs that guild's command tree immediately.
"""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType, parse_guild_id
from utils import emojis
from utils.i18n import t


@staff_command
class OfficialCommand(StaffCommand):
    command_type = CommandType.DEV
    name = "official"
    description = "Mark/unmark a server as OFFICIAL (controls staff slash command sync)."
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
            await ctx.send(view=design.invalid_usage(locale, "d.official <guild_id> [add|remove]"))
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
            "guild", gid, "OFFICIAL", True if enable else None, ctx.author.id,
            reason=f"{'Marked' if enable else 'Unmarked'} OFFICIAL via staff command",
        )

        # Re-sync this guild so staff commands appear/disappear right away.
        synced = False
        if guild:
            try:
                await bot.sync_guild_commands(guild)
                synced = True
            except Exception:
                synced = False

        if enable:
            description = t("staff.dev.official.added", locale=locale, name=f"**{guild_name}**", id=f"`{gid}`")
        else:
            description = t("staff.dev.official.removed", locale=locale, name=f"**{guild_name}**", id=f"`{gid}`")
        if not guild:
            description += "\n-# " + t("staff.dev.official.not_present", locale=locale)
        elif not synced:
            description += "\n-# " + t("staff.dev.official.sync_failed", locale=locale)

        await ctx.send(view=design.success(
            t("staff.dev.official.title", locale=locale), description, emoji=emojis.STAR,
        ))
