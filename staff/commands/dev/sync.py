"""`/dev sync` — re-sync application commands with Discord.

Targets:
- ``global`` (default): global app commands.
- ``guilds``: re-sync every guild tree (guild-only + staff groups on OFFICIAL).
- a guild id: re-sync that single guild.
"""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType, parse_guild_id
from utils.i18n import t


@staff_command
class SyncCommand(StaffCommand):
    command_type = CommandType.DEV
    name = "sync"
    description = "Re-sync application commands (global | guilds | <guild_id>)."
    options = [
        SlashOption("target", "string", "global, guilds, or a guild id.", required=False, default="global"),
    ]

    async def execute(self, ctx):
        bot = ctx.bot
        locale = ctx.locale
        target = (ctx.opt("target") or "global").strip().lower()

        msg = await ctx.send(view=design.loading(
            t("staff.dev.sync.loading_title", locale=locale),
            t("staff.dev.sync.loading", locale=locale),
        ))

        try:
            if target in ("", "global"):
                synced = await bot.tree.sync()
                view = design.success(
                    t("staff.dev.sync.done_title", locale=locale),
                    t("staff.dev.sync.global_done", locale=locale, count=len(synced)),
                )
            elif target in ("guilds", "all", "official"):
                await bot.sync_all_guild_commands()
                view = design.success(
                    t("staff.dev.sync.done_title", locale=locale),
                    t("staff.dev.sync.guilds_done", locale=locale, count=len(bot.guilds)),
                )
            else:
                gid = parse_guild_id(target)
                guild = bot.get_guild(gid) if gid else None
                if not guild:
                    view = design.error(
                        t("staff.dev.sync.invalid_title", locale=locale),
                        t("staff.dev.sync.invalid", locale=locale),
                    )
                else:
                    await bot.sync_guild_commands(guild)
                    view = design.success(
                        t("staff.dev.sync.done_title", locale=locale),
                        t("staff.dev.sync.guild_done", locale=locale, name=f"**{guild.name}**"),
                    )
        except Exception as exc:
            view = design.error(
                t("staff.dev.sync.fail_title", locale=locale),
                f"```{str(exc)[:500]}```",
            )

        if msg:
            await msg.edit(view=view)
        else:
            await ctx.send(view=view)
