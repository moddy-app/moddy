"""`/team see` — open **this** channel to the Moddy Team role.

`/team access` grants *guild-wide* permissions; this is the narrow counterpart:
one channel, on the role. A staffer looking at a bug in one channel needs their
colleagues in that channel, not in the whole server.

**There is deliberately no `channel` option.** The command acts on the channel it
was run in, and refuses if the staffer cannot see it themselves — you open a
door you are already standing in. That keeps it impossible to use as a way of
reaching a channel one does not already have.

What it writes is a channel overwrite on the **Moddy Team role**, never on a
person: see the channel, read its history, and write in it — and nothing
moderative. Managing messages or the channel itself stays a `/team access`
decision an administrator accepts. `revoke` removes the overwrite entirely
rather than setting it to a denial, so the channel goes back to exactly the
state it had.

Run in a thread, it acts on the parent channel — a thread inherits its
permissions and has no overwrites of its own.
"""

import logging

import discord
from discord import ui

from staff.framework import (
    StaffCommand, SlashOption, staff_command, design, CommandType,
)
from utils import emojis
from utils.i18n import t
from utils.moddy_team_role import TEAM_ROLE_NAME, find_team_role
from cogs.error_handler import BaseView

logger = logging.getLogger("moddy.staff.team.see")

#: Exactly what the overwrite grants: see the channel, read what was said
#: before, and take part. Nothing moderative — no managing messages, no
#: managing the channel; that stays a `/team access` decision an administrator
#: accepts.
GRANTED = ("view_channel", "read_message_history",
           "send_messages", "send_messages_in_threads")


def _target_channel(channel):
    """The channel the overwrite belongs on.

    A thread carries no overwrites of its own — it inherits its parent's — so
    writing one on the thread would silently do nothing.
    """
    parent = getattr(channel, "parent", None)
    return parent if isinstance(channel, discord.Thread) and parent else channel


@staff_command
class TeamSeeCommand(StaffCommand):
    command_type = CommandType.TEAM
    name = "see"
    aliases = ("channel",)
    defer = True
    description = "Open this channel to the Moddy Team role (read and write)."
    options = [
        SlashOption("action", "string", "grant (default) or revoke.",
                    required=False, choices=["grant", "revoke"], default="grant"),
    ]

    def parse_message(self, raw: str) -> dict:
        action = (raw or "").strip().lower()
        return {"action": action if action in ("grant", "revoke") else "grant"}

    async def execute(self, ctx):
        locale = ctx.locale
        revoke = ctx.opt("action") == "revoke"

        if not ctx.guild or ctx.channel is None:
            await ctx.send(view=design.error(
                t("staff.team.see.failed_title", locale=locale),
                t("staff.team.see.guild_only", locale=locale),
            ))
            return

        channel = _target_channel(ctx.channel)
        member = ctx.guild.get_member(ctx.author.id)

        # The whole point: you open a door you are already standing in.
        if member is None or not channel.permissions_for(member).view_channel:
            await ctx.send(view=design.error(
                t("staff.team.see.failed_title", locale=locale),
                t("staff.team.see.not_yours", locale=locale),
            ))
            return

        role = await find_team_role(ctx.bot, ctx.guild)
        if role is None:
            await ctx.send(view=design.error(
                t("staff.team.see.failed_title", locale=locale),
                t("staff.team.see.no_role", locale=locale,
                  name=f"**{TEAM_ROLE_NAME}**"),
            ))
            return

        me = ctx.guild.me
        # `manage_roles` at channel level is what Discord calls *Manage
        # Permissions*; without it here the overwrite is refused.
        if not channel.permissions_for(me).manage_roles:
            await ctx.send(view=design.error(
                t("staff.team.see.failed_title", locale=locale),
                t("staff.team.see.no_permission", locale=locale,
                  channel=channel.mention),
            ))
            return
        if role >= me.top_role:
            await ctx.send(view=design.error(
                t("staff.team.see.failed_title", locale=locale),
                t("staff.team.see.role_too_high", locale=locale, role=role.mention),
            ))
            return
        # Discord refuses to grant, in an overwrite, a permission the actor does
        # not hold itself — checked here rather than after a 403.
        if not revoke:
            mine = channel.permissions_for(me)
            missing = [p for p in GRANTED if not getattr(mine, p)]
            if missing:
                await ctx.send(view=design.error(
                    t("staff.team.see.failed_title", locale=locale),
                    t("staff.team.see.moddy_cannot_see", locale=locale,
                      channel=channel.mention),
                ))
                return

        current = channel.overwrites_for(role)

        if not revoke:
            # The role's **own** overwrite, deliberately — not its effective
            # permissions. A channel Moddy Team can already read through
            # @everyone is one it reads at somebody else's discretion: the day
            # @everyone is closed, our access goes with it, silently, in the
            # middle of whatever the team was doing there. The explicit
            # overwrite is what makes the access ours, so it is always written.
            if all(getattr(current, p) is True for p in GRANTED):
                await ctx.send(view=design.info(
                    t("staff.team.see.title", locale=locale),
                    t("staff.team.see.already", locale=locale,
                      role=role.mention, channel=channel.mention),
                ))
                return
        elif current.is_empty():
            # Nothing of ours on this channel; deleting an overwrite that does
            # not exist would report a change that never happened.
            await ctx.send(view=design.info(
                t("staff.team.see.title", locale=locale),
                t("staff.team.see.not_set", locale=locale,
                  role=role.mention, channel=channel.mention),
            ))
            return

        reason = f"Moddy Team channel access by {ctx.author} ({ctx.author.id})"
        try:
            if revoke:
                # `None` deletes the overwrite; setting the permissions to False
                # would leave a denial behind and change the channel's meaning.
                await channel.set_permissions(role, overwrite=None, reason=reason)
            else:
                for permission in GRANTED:
                    setattr(current, permission, True)
                await channel.set_permissions(role, overwrite=current, reason=reason)
        except discord.Forbidden:
            await ctx.send(view=design.error(
                t("staff.team.see.failed_title", locale=locale),
                t("staff.team.see.no_permission", locale=locale,
                  channel=channel.mention),
            ))
            return
        except discord.HTTPException as e:
            logger.error("Could not %s the Moddy Team overwrite on channel %s in "
                         "guild %s — HTTP %s: %s",
                         "revoke" if revoke else "grant", channel.id, ctx.guild.id,
                         getattr(e, "status", "?"), getattr(e, "text", None) or e)
            await ctx.send(view=design.error(
                t("staff.team.see.failed_title", locale=locale),
                t("staff.team.role.http_error", locale=locale, error=f"`{e}`"),
            ))
            return

        logger.info("Staff %s %s the Moddy Team role on channel %s in guild %s",
                    ctx.author.id, "revoked" if revoke else "granted",
                    channel.id, ctx.guild.id)

        view = BaseView()
        container = design.make_container("success")
        container.add_item(ui.TextDisplay(
            f"{design.title_line(emojis.STAFF, t('staff.team.see.title', locale=locale))}\n"
            f"{t('staff.team.see.' + ('revoked' if revoke else 'granted'), locale=locale, role=role.mention, channel=channel.mention)}"
        ))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(
            f"-# {t('staff.team.see.scope', locale=locale)}"))
        view.add_item(container)
        await ctx.send(view=view)
