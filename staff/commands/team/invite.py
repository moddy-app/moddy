"""`/team invite` — create an invite to a server Moddy is in."""

import logging

import discord
from discord import ui

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType, parse_guild_id
from utils import emojis
from utils.i18n import t
from cogs.error_handler import BaseView

logger = logging.getLogger("moddy.staff.team.invite")


@staff_command
class InviteCommand(StaffCommand):
    command_type = CommandType.TEAM
    name = "invite"
    description = "Create an invite to a server Moddy is in."
    options = [
        SlashOption("guild_id", "string", "Target guild id.", required=True),
    ]

    async def execute(self, ctx):
        locale = ctx.locale
        gid = parse_guild_id(ctx.opt("guild_id") or "")
        if not gid:
            await ctx.send(view=design.invalid_usage(locale, "t.invite <guild_id>"))
            return

        guild = ctx.bot.get_guild(gid)
        if not guild:
            await ctx.send(view=design.error(
                t("staff.team.server_notfound_title", locale=locale),
                t("staff.team.server_notfound", locale=locale, id=f"`{gid}`"),
            ))
            return

        channel = guild.system_channel
        if not channel or not channel.permissions_for(guild.me).create_instant_invite:
            channel = next(
                (ch for ch in guild.text_channels
                 if ch.permissions_for(guild.me).create_instant_invite),
                None,
            )

        if not channel:
            await ctx.send(view=design.error(
                t("staff.team.invite.fail_title", locale=locale),
                t("staff.team.invite.no_perm", locale=locale, name=f"**{guild.name}**"),
            ))
            return

        try:
            invite = await channel.create_invite(
                max_age=604800, max_uses=5, unique=True,
                reason=f"Staff invite requested by {ctx.author}",
            )
        except discord.Forbidden:
            await ctx.send(view=design.error(
                t("staff.team.invite.fail_title", locale=locale),
                t("staff.team.invite.no_perm", locale=locale, name=f"**{guild.name}**"),
            ))
            return

        view = BaseView()
        container = design.make_container("primary")
        container.add_item(ui.TextDisplay(design.title_line(emojis.WEB, t("staff.team.invite.title", locale=locale))))
        container.add_item(ui.TextDisplay(f"**{guild.name}**\n`{invite.url}`"))
        view.add_item(container)
        row = ui.ActionRow()
        row.add_item(ui.Button(label=t("staff.team.invite.open", locale=locale), url=invite.url,
                               style=discord.ButtonStyle.link))
        view.add_item(row)
        await ctx.send(view=view)
        logger.info("Staff %s requested invite for %s (%s)", ctx.author.id, guild.name, guild.id)
