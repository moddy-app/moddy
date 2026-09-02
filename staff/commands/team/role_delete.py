"""`/team role_delete` — remove a server's **Moddy Team** roles.

The counterpart of `/team role`, and it takes the same scope: the base role by
default, `manager` for the manager one, `both` for the pair. A server that no
longer wants us on site, a role created by mistake, a linking that has to be
redone from scratch: deleting the role is the whole undo, because everything
else hangs off it. Discord drops the linked-role requirement with the role, and
takes the role off everybody who held it.

Deliberately destructive and deliberately simple — the roles carry no
permissions of their own and `/team role` recreates them in one command, so
there is nothing here worth a confirmation dialog. What it does refuse is
deleting a role out from under a running linking window, which would leave that
window putting back a role that no longer exists.

The stored ids under ``guilds.data.moddy_team`` are forgotten too, so a later
`/team role` creates fresh ones instead of pointing at a ghost.
"""

import logging
from typing import List, Tuple

import discord
from discord import ui

from staff.framework import (
    StaffCommand, SlashOption, staff_command, design, CommandType, parse_guild_id,
)
from services import team_link_session as linking
from staff.commands.team.team_role import SCOPES, SCOPE_TEAM, kinds_for_scope
from utils import emojis
from utils.i18n import t
from utils.moddy_team_role import TeamRoleKind, find_team_role, remember_role
from cogs.error_handler import BaseView

logger = logging.getLogger("moddy.staff.team.role_delete")


@staff_command
class TeamRoleDeleteCommand(StaffCommand):
    command_type = CommandType.TEAM
    name = "role_delete"
    aliases = ("unrole",)
    defer = True
    description = "Delete the Moddy Team roles in a server."
    options = [
        SlashOption("guild_id", "string",
                    "Target guild id — defaults to the server you run this in.",
                    required=False),
        SlashOption("roles", "string",
                    "Which role — the team role by default, or the manager one, or both.",
                    required=False, default=SCOPE_TEAM, choices=list(SCOPES)),
    ]

    def parse_message(self, raw: str) -> dict:
        """``t.unrole [guild_id] [team|manager|both]``, in either order."""
        guild_id, scope = "", ""
        for token in (raw or "").split():
            if token.lower() in SCOPES:
                scope = token.lower()
            elif not guild_id:
                guild_id = token
        return {"guild_id": guild_id, "roles": scope or SCOPE_TEAM}

    async def execute(self, ctx):
        locale = ctx.locale
        raw = (ctx.opt("guild_id") or "").strip()
        gid = parse_guild_id(raw) if raw else (ctx.guild.id if ctx.guild else None)
        if not gid:
            await ctx.send(view=design.invalid_usage(
                locale, "t.role_delete [guild_id] [team|manager|both]"))
            return

        guild = ctx.bot.get_guild(gid)
        if not guild:
            await ctx.send(view=design.error(
                t("staff.team.server_notfound_title", locale=locale),
                t("staff.team.server_notfound", locale=locale, id=f"`{gid}`"),
            ))
            return

        if linking.active_session(guild.id) is not None:
            # The window is holding somebody's roles and expects these roles to
            # still be there when it puts them back.
            await ctx.send(view=design.error(
                t("staff.team.role_delete.failed_title", locale=locale),
                t("staff.team.role.blocked_busy", locale=locale),
            ))
            return

        kinds = kinds_for_scope(ctx.opt("roles"))
        found: List[Tuple[TeamRoleKind, discord.Role]] = []
        for kind in kinds:
            role = await find_team_role(ctx.bot, guild, kind)
            if role is not None:
                found.append((kind, role))

        if not found:
            await ctx.send(view=design.error(
                t("staff.team.role_delete.failed_title", locale=locale),
                t("staff.team.role_delete.no_role", locale=locale,
                  roles=", ".join(f"**{k.name}**" for k in kinds),
                  guild=f"**{guild.name}**"),
            ))
            return

        deleted = []
        for kind, role in found:
            name, role_id, holders = role.name, role.id, len(role.members)
            try:
                await role.delete(
                    reason=f"{kind.name} role deleted by {ctx.author} ({ctx.author.id})")
            except discord.Forbidden:
                await ctx.send(view=design.error(
                    t("staff.team.role_delete.failed_title", locale=locale),
                    t("staff.team.role_delete.forbidden", locale=locale,
                      role=f"**{name}**"),
                ))
                return
            except discord.HTTPException as e:
                logger.error("Could not delete the %s role %s in guild %s — "
                             "HTTP %s: %s", kind.name, role_id, guild.id,
                             getattr(e, "status", "?"), getattr(e, "text", None) or e)
                await ctx.send(view=design.error(
                    t("staff.team.role_delete.failed_title", locale=locale),
                    t("staff.team.role.http_error", locale=locale, error=f"`{e}`"),
                ))
                return

            # Forget the id, or the next `/team role` points at a role that is
            # gone. Done per role, right after its deletion: a failure on the
            # second must not leave the first one remembered.
            await remember_role(ctx.bot, guild.id, None, kind)
            deleted.append((name, role_id, holders))
            logger.info("Staff %s deleted the %s role %s in guild %s",
                        ctx.author.id, kind.name, role_id, guild.id)

        view = BaseView()
        container = design.make_container("success")
        container.add_item(ui.TextDisplay(
            f"{design.title_line(emojis.STAFF, t('staff.team.role_delete.title', locale=locale))}\n"
            f"{t('staff.team.role_delete.done', locale=locale, roles=', '.join(f'**{n}**' for n, _, _ in deleted), guild=f'**{guild.name}**')}"
        ))
        for name, role_id, holders in deleted:
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(ui.TextDisplay(
                f"**{name}**\n"
                f"-# {t('staff.team.role.id', locale=locale)}: `{role_id}`\n"
                f"-# {t('staff.team.role_delete.holders', locale=locale, count=f'`{holders}`')}"
            ))
        view.add_item(container)
        await ctx.send(view=view)
