"""`/dev announcements` — set up announcement-channel following for a guild."""

import logging

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType, parse_guild_id
from utils.i18n import t
from utils.announcement_setup import setup_announcement_channel

logger = logging.getLogger("moddy.staff.dev.announcements")


@staff_command
class AnnouncementsCommand(StaffCommand):
    command_type = CommandType.DEV
    name = "announcements"
    aliases = ("setup-announcements",)
    description = "Set up announcement-channel following for a guild."
    options = [
        SlashOption("guild_id", "string", "Target guild id.", required=True),
    ]

    async def execute(self, ctx):
        locale = ctx.locale
        gid = parse_guild_id(ctx.opt("guild_id") or "")
        if not gid:
            await ctx.send(view=design.invalid_usage(locale, "d.announcements <guild_id>"))
            return

        guild = ctx.bot.get_guild(gid)
        if not guild:
            await ctx.send(view=design.error(
                t("staff.dev.announcements.notfound_title", locale=locale),
                t("staff.dev.announcements.notfound", locale=locale, id=f"`{gid}`"),
            ))
            return

        msg = await ctx.send(view=design.loading(
            t("staff.dev.announcements.loading_title", locale=locale),
            t("staff.dev.announcements.loading", locale=locale, name=f"**{guild.name}**"),
        ))

        try:
            ok, result = await setup_announcement_channel(guild)
            if ok:
                view = design.success(
                    t("staff.dev.announcements.done_title", locale=locale),
                    t("staff.dev.announcements.done", locale=locale, name=f"**{guild.name}**", id=f"`{gid}`") + f"\n-# {result}",
                )
            else:
                view = design.error(
                    t("staff.dev.announcements.fail_title", locale=locale),
                    t("staff.dev.announcements.fail", locale=locale, name=f"**{guild.name}**") + f"\n-# {result}",
                )
        except Exception as exc:
            logger.error("announcements setup failed for %s: %s", gid, exc, exc_info=True)
            view = design.error(
                t("staff.dev.announcements.fail_title", locale=locale),
                f"```{str(exc)[:400]}```",
            )

        if msg:
            await msg.edit(view=view)
        else:
            await ctx.send(view=view)
