"""`/team ticket` — open a Moddy staff ticket in a server.

A channel to talk to a server's administrators, opened by us, without going
through — or even needing — that server's own ticket configuration. It is a
real ticket underneath (same table, same buttons, same `/ticket` verbs), so
closing, reopening, adding somebody or opening a staff thread all work exactly
as they do everywhere else.

Who sees it: the server's administrators (Discord's own rule) and the **Moddy
Team** role, which is why the role has to exist before a ticket can be opened —
otherwise we would be opening a channel we cannot read ourselves. Nobody else,
including the server's own support team, unless somebody adds them.

See docs/TICKETS.md and docs/LINKED_ROLES.md.
"""

import logging

import discord
from discord import ui

from staff.framework import (
    StaffCommand, SlashOption, staff_command, design, CommandType, parse_guild_id,
)
from services.ticket_service import TicketError
from utils import emojis
from utils.i18n import t
from utils.moddy_team_role import can_manage, create_team_role, find_team_role, is_linked
from cogs.error_handler import BaseView

logger = logging.getLogger("moddy.staff.team.ticket")


@staff_command
class TeamTicketCommand(StaffCommand):
    command_type = CommandType.TEAM
    name = "ticket"
    description = "Open a Moddy staff ticket in a server."
    options = [
        SlashOption("reason", "string", "What this ticket is about — shown to the server.",
                    required=False),
        SlashOption("guild_id", "string",
                    "Target guild id — defaults to the server you run this in.",
                    required=False),
    ]

    def parse_message(self, raw: str) -> dict:
        """``t.ticket [guild_id] [reason]`` — a leading id, then free text.

        The id comes first so the reason can be a sentence: splitting the other
        way round would make ``t.ticket the logo is broken 123…`` ambiguous.
        """
        parts = (raw or "").strip().split(None, 1)
        if parts and parse_guild_id(parts[0]):
            return {"guild_id": parts[0],
                    "reason": parts[1].strip() if len(parts) > 1 else ""}
        return {"guild_id": "", "reason": (raw or "").strip()}

    async def execute(self, ctx):
        locale = ctx.locale
        raw = (ctx.opt("guild_id") or "").strip()
        gid = parse_guild_id(raw) if raw else (ctx.guild.id if ctx.guild else None)
        reason = (ctx.opt("reason") or "").strip() or None

        if not gid:
            await ctx.send(view=design.invalid_usage(locale, "t.ticket [guild_id] [reason]"))
            return

        guild = ctx.bot.get_guild(gid)
        if not guild:
            await ctx.send(view=design.error(
                t("staff.team.server_notfound_title", locale=locale),
                t("staff.team.server_notfound", locale=locale, id=f"`{gid}`"),
            ))
            return

        # The Moddy Team role is always on a staff ticket — without it the
        # channel would be readable by the server's administrators and by
        # nobody on our side. A server that does not have one yet gets it here
        # rather than being sent away to run another command first.
        role = await find_team_role(ctx.bot, guild)
        role_created = False
        if not role:
            if not can_manage(guild):
                await ctx.send(view=design.error(
                    t("staff.team.ticket.failed_title", locale=locale),
                    t("staff.team.role.no_permission", locale=locale,
                      name=f"**{guild.name}**"),
                ))
                return
            try:
                role = await create_team_role(ctx.bot, guild, actor=ctx.author)
                role_created = True
            except discord.HTTPException as e:
                logger.warning("Could not create the Moddy Team role in %s: %s", gid, e)
                await ctx.send(view=design.error(
                    t("staff.team.ticket.failed_title", locale=locale),
                    t("staff.team.role.http_error", locale=locale, error=f"`{e}`"),
                ))
                return

        tickets = getattr(ctx.bot, 'tickets', None)
        if not tickets:
            await ctx.send(view=design.error(
                t("staff.team.ticket.failed_title", locale=locale),
                t("modules.tickets.errors.unavailable", locale=locale),
            ))
            return

        try:
            channel = await tickets.open_staff_ticket(guild, ctx.author, reason)
        except TicketError as e:
            await ctx.send(view=design.error(
                t("staff.team.ticket.failed_title", locale=locale),
                e.message(locale),
            ))
            return

        view = BaseView()
        container = design.make_container("primary")
        container.add_item(ui.TextDisplay(
            f"{design.title_line(emojis.TICKET, t('staff.team.ticket.opened_title', locale=locale))}\n"
            f"{t('staff.team.ticket.opened', locale=locale, channel=channel.mention, guild=f'**{guild.name}**')}"
        ))
        container.add_item(ui.TextDisplay(
            f"-# {t('staff.team.ticket.opened_hint', locale=locale, role=role.mention)}"))
        if role_created:
            container.add_item(ui.TextDisplay(
                f"-# {t('staff.team.ticket.role_created', locale=locale, role=role.mention)}"))
        if not is_linked(role):
            # The channel exists and the role is on it, but nobody holds the
            # role until an administrator adds the requirement — say so now
            # rather than let a staffer wonder why they see nothing.
            container.add_item(ui.TextDisplay(
                f"-# {emojis.WARNING} {t('staff.team.ticket.not_linked', locale=locale)}"))
        view.add_item(container)

        row = ui.ActionRow()
        row.add_item(ui.Button(label=t("staff.team.ticket.open_channel", locale=locale),
                               url=channel.jump_url, style=discord.ButtonStyle.link))
        view.add_item(row)
        await ctx.send(view=view)
        logger.info("Staff %s opened a staff ticket in guild %s (channel %s)",
                    ctx.author.id, guild.id, channel.id)
