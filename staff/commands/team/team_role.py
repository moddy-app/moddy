"""`/team role` — create (or inspect) a server's **Moddy Team** role.

One role per server, bound to Moddy's *Moddy Team* linked-role requirement, so
Discord hands it to whoever is on the team **at that moment** and takes it back
the second they are not. That is the whole point: a server never has to keep a
list of our staff up to date, and a destitution reaches every server at once.

The linking half cannot be automated — Discord exposes no API for role-connection
requirements, they are set by a human in *Server Settings → Roles → Links*. So
this command creates the role, checks whether the requirement is in place
(``RoleTags.is_guild_connection``), and prints the three clicks that are left.
Run it again afterwards and it says whether the binding took.

See docs/LINKED_ROLES.md.
"""

import logging

import discord
from discord import ui

from staff.framework import (
    StaffCommand, SlashOption, staff_command, design, CommandType, parse_guild_id,
)
from utils import emojis
from utils.i18n import t
from utils.moddy_team_role import (
    LINKED_ROLES_URL, TEAM_ROLE_NAME, can_manage, create_team_role, find_team_role,
    is_linked,
)
from cogs.error_handler import BaseView

logger = logging.getLogger("moddy.staff.team.role")


@staff_command
class TeamRoleCommand(StaffCommand):
    command_type = CommandType.TEAM
    name = "role"
    description = "Create the Moddy Team role (linked role) in a server."
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
            await ctx.send(view=design.invalid_usage(locale, "t.role [guild_id]"))
            return

        guild = ctx.bot.get_guild(gid)
        if not guild:
            await ctx.send(view=design.error(
                t("staff.team.server_notfound_title", locale=locale),
                t("staff.team.server_notfound", locale=locale, id=f"`{gid}`"),
            ))
            return

        role = await find_team_role(ctx.bot, guild)
        created = False

        if role is None:
            if not can_manage(guild):
                await ctx.send(view=design.error(
                    t("staff.team.role.failed_title", locale=locale),
                    t("staff.team.role.no_permission", locale=locale,
                      name=f"**{guild.name}**"),
                ))
                return
            try:
                role = await create_team_role(ctx.bot, guild, actor=ctx.author)
                created = True
            except discord.Forbidden:
                await ctx.send(view=design.error(
                    t("staff.team.role.failed_title", locale=locale),
                    t("staff.team.role.no_permission", locale=locale,
                      name=f"**{guild.name}**"),
                ))
                return
            except discord.HTTPException as e:
                logger.warning("Could not create the Moddy Team role in %s: %s", gid, e)
                await ctx.send(view=design.error(
                    t("staff.team.role.failed_title", locale=locale),
                    t("staff.team.role.http_error", locale=locale, error=f"`{e}`"),
                ))
                return
            logger.info("Staff %s created the Moddy Team role in %s", ctx.author.id, gid)

        linked = is_linked(role)
        granted = [name for name, value in role.permissions if value]

        view = BaseView()
        container = design.make_container("success" if linked else "warning")
        container.add_item(ui.TextDisplay(
            f"{design.title_line(emojis.STAFF, t('staff.team.role.title', locale=locale))}\n"
            f"{t('staff.team.role.' + ('created' if created else 'exists'), locale=locale, role=role.mention, guild=f'**{guild.name}**')}"
        ))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(
            f"**{t('staff.team.role.state', locale=locale)}**\n"
            f"-# {t('staff.team.role.id', locale=locale)}: `{role.id}`\n"
            f"-# {t('staff.team.role.linked', locale=locale)}: "
            f"{emojis.DONE if linked else emojis.UNDONE} "
            f"{t('staff.team.role.' + ('linked_yes' if linked else 'linked_no'), locale=locale)}\n"
            f"-# {t('staff.team.role.permissions', locale=locale)}: "
            f"`{len(granted)}`"
        ))

        if not linked:
            # The one thing the bot cannot do for them, spelled out — this is
            # what a staffer reads out loud to the administrator.
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(ui.TextDisplay(
                f"**{t('staff.team.role.howto_title', locale=locale)}**\n"
                f"{t('staff.team.role.howto', locale=locale, name=f'**{TEAM_ROLE_NAME}**', role=role.name)}"
            ))
        container.add_item(ui.TextDisplay(
            f"-# {t('staff.team.role.hint', locale=locale)}"))
        view.add_item(container)

        row = ui.ActionRow()
        row.add_item(ui.Button(label=t("staff.team.role.docs", locale=locale),
                               url=LINKED_ROLES_URL, style=discord.ButtonStyle.link))
        view.add_item(row)
        await ctx.send(view=view)
