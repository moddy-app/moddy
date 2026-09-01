"""`/team role` — create (or inspect) a server's **Moddy Team** role.

One role per server, bound to Moddy's *Moddy Team* linked-role requirement, so
Discord hands it to whoever is on the team **at that moment** and takes it back
the second they are not. That is the whole point: a server never has to keep a
list of our staff up to date, and a destitution reaches every server at once.

No API can attach the requirement — a bot token is answered
``20001 — Bots cannot use this endpoint``. So when the role is not linked yet,
this command opens a **thirty-second window** in which the staffer who ran it
holds `Manage Roles` and can do it themselves, inside the containment described
in :mod:`services.team_link_session`. The card then reports what came of it, and
falls back on the manual instructions when the window could not be opened.

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
    LINKED_ROLES_URL, TEAM_ROLE_NAME, can_manage, create_team_role,
    find_team_role, is_linked,
)
from services import team_link_session as linking
from cogs.error_handler import BaseView

logger = logging.getLogger("moddy.staff.team.role")


def _blocker(guild: discord.Guild, member) -> str:
    """Why the thirty-second window cannot be opened here, or ``""``.

    Checked before anything is created or moved: a window that fails halfway
    leaves a staffer without their roles, so it is never started on a hope.
    """
    me = guild.me
    if member is None:
        return "not_member"
    if member.id == guild.owner_id:
        # The owner already has every permission; lending them one is absurd,
        # and Discord refuses to edit the owner's roles anyway.
        return "owner"
    if linking.active_session(guild.id) is not None:
        return "busy"
    if not me or not me.guild_permissions.manage_roles:
        return "no_permission"
    if member.top_role >= me.top_role:
        # Discord refuses to touch the roles of anybody whose highest role is
        # not below the bot's, and the window starts by setting them aside.
        # Nobody this blocks actually needs the window: they already hold
        # Manage Roles and can do the clicks themselves.
        return "above_moddy"
    if me.top_role.position < 3:
        # Moddy Team at 1 and the throwaway role at 2 must both fit under Moddy.
        return "no_room"
    return ""


def _window_card(locale: str, role: discord.Role, guild: discord.Guild) -> BaseView:
    """The instructions the staffer has thirty seconds to follow."""
    view = BaseView()
    container = design.make_container("warning")
    container.add_item(ui.TextDisplay(
        f"{design.title_line(emojis.PENDING, t('staff.team.role.window_title', locale=locale, seconds=linking.WINDOW_SECONDS))}\n"
        f"{t('staff.team.role.window', locale=locale, role=role.name, name=f'**{TEAM_ROLE_NAME}**')}"
    ))
    container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    container.add_item(ui.TextDisplay(f"-# {t('staff.team.role.window_rules', locale=locale)}"))
    view.add_item(container)
    return view


@staff_command
class TeamRoleCommand(StaffCommand):
    command_type = CommandType.TEAM
    name = "role"
    defer = True
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
        outcome = None
        blocker = ""
        if not linked:
            member = guild.get_member(ctx.author.id)
            blocker = _blocker(guild, member)
            if not blocker:
                # Tell them what to do *before* the clock starts: the card is
                # the instructions, and thirty seconds is not long enough to
                # read them afterwards.
                await ctx.send(view=_window_card(locale, role, guild))
                outcome = await linking.run_window(ctx.bot, guild, member, role)
                linked = outcome == linking.DONE

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
            f"{t('staff.team.role.linked_' + ('yes' if linked else 'no'), locale=locale)}\n"
            f"-# {t('staff.team.role.permissions', locale=locale)}: "
            f"`{len(granted)}`"
        ))

        if not linked:
            # Why the window did not get there, then the manual path — which is
            # what a staffer reads out loud to an administrator when all else
            # fails.
            reason = f"window_{outcome}" if outcome else f"blocked_{blocker}"
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(ui.TextDisplay(
                f"**{t('staff.team.role.howto_title', locale=locale)}**\n"
                f"-# {t('staff.team.role.' + reason, locale=locale)}\n"
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
