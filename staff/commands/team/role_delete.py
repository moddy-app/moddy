"""`/team role_delete` — remove a server's **Moddy Team** role.

The counterpart of `/team role`. A server that no longer wants us on site, a
role created by mistake, a linking that has to be redone from scratch: deleting
the role is the whole undo, because everything else hangs off it. Discord drops
the linked-role requirement with the role, and takes the role off everybody who
held it.

Deliberately destructive and deliberately simple — the role carries no
permissions of its own and `/team role` recreates it in one command, so there is
nothing here worth a confirmation dialog. What it does refuse is deleting a role
out from under a running linking window, which would leave that window putting
back a role that no longer exists.

The stored id in ``guilds.data.moddy_team.role_id`` is forgotten too, so a later
`/team role` creates a fresh one instead of pointing at a ghost.
"""

import logging

import discord
from discord import ui

from staff.framework import (
    StaffCommand, SlashOption, staff_command, design, CommandType, parse_guild_id,
)
from services import team_link_session as linking
from utils import emojis
from utils.i18n import t
from utils.moddy_team_role import TEAM_ROLE_NAME, find_team_role, remember_role
from cogs.error_handler import BaseView

logger = logging.getLogger("moddy.staff.team.role_delete")


@staff_command
class TeamRoleDeleteCommand(StaffCommand):
    command_type = CommandType.TEAM
    name = "role_delete"
    aliases = ("unrole",)
    defer = True
    description = "Delete the Moddy Team role in a server."
    options = [
        SlashOption("guild_id", "string",
                    "Target guild id — defaults to the server you run this in.",
                    required=False),
    ]

    def parse_message(self, raw: str) -> dict:
        return {"guild_id": (raw or "").strip()}

    async def execute(self, ctx):
        locale = ctx.locale
        raw = (ctx.opt("guild_id") or "").strip()
        gid = parse_guild_id(raw) if raw else (ctx.guild.id if ctx.guild else None)
        if not gid:
            await ctx.send(view=design.invalid_usage(locale, "t.role_delete [guild_id]"))
            return

        guild = ctx.bot.get_guild(gid)
        if not guild:
            await ctx.send(view=design.error(
                t("staff.team.server_notfound_title", locale=locale),
                t("staff.team.server_notfound", locale=locale, id=f"`{gid}`"),
            ))
            return

        if linking.active_session(guild.id) is not None:
            # The window is holding somebody's roles and expects this role to
            # still be there when it puts them back.
            await ctx.send(view=design.error(
                t("staff.team.role_delete.failed_title", locale=locale),
                t("staff.team.role.blocked_busy", locale=locale),
            ))
            return

        role = await find_team_role(ctx.bot, guild)
        if role is None:
            await ctx.send(view=design.error(
                t("staff.team.role_delete.failed_title", locale=locale),
                t("staff.team.role_delete.no_role", locale=locale,
                  name=f"**{TEAM_ROLE_NAME}**", guild=f"**{guild.name}**"),
            ))
            return

        name, role_id, holders = role.name, role.id, len(role.members)
        try:
            await role.delete(
                reason=f"Moddy Team role deleted by {ctx.author} ({ctx.author.id})")
        except discord.Forbidden:
            await ctx.send(view=design.error(
                t("staff.team.role_delete.failed_title", locale=locale),
                t("staff.team.role_delete.forbidden", locale=locale,
                  role=f"**{name}**"),
            ))
            return
        except discord.HTTPException as e:
            logger.error("Could not delete the Moddy Team role %s in guild %s — "
                         "HTTP %s: %s", role_id, guild.id, getattr(e, "status", "?"),
                         getattr(e, "text", None) or e)
            await ctx.send(view=design.error(
                t("staff.team.role_delete.failed_title", locale=locale),
                t("staff.team.role.http_error", locale=locale, error=f"`{e}`"),
            ))
            return

        # Forget the id, or the next `/team role` points at a role that is gone.
        await remember_role(ctx.bot, guild.id, None)
        logger.info("Staff %s deleted the Moddy Team role %s in guild %s",
                    ctx.author.id, role_id, guild.id)

        view = BaseView()
        container = design.make_container("success")
        container.add_item(ui.TextDisplay(
            f"{design.title_line(emojis.STAFF, t('staff.team.role_delete.title', locale=locale))}\n"
            f"{t('staff.team.role_delete.done', locale=locale, role=f'**{name}**', guild=f'**{guild.name}**')}"
        ))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(
            f"-# {t('staff.team.role.id', locale=locale)}: `{role_id}`\n"
            f"-# {t('staff.team.role_delete.holders', locale=locale, count=f'`{holders}`')}\n"
            f"-# {t('staff.team.role_delete.hint', locale=locale)}"
        ))
        view.add_item(container)
        await ctx.send(view=view)
