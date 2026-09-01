"""`/team role` — create (or inspect) a server's **Moddy Team** roles.

Two roles exist, and a server takes as many of them as it needs:

- **Moddy Team**, bound to the ``team`` linked-role requirement — everybody on
  the team. This is the default, and most servers never want anything else.
- **Moddy Team Manager**, bound to ``manager`` — the accounts that lead the
  team. A manager holds both, so this one is additive: asking for it later
  never disturbs the base role, and a server that started with one role can
  come back for the second with `t.role <id> manager`.

Discord hands them out on its own from the metadata the backend publishes. That
is the whole point: a server never has to keep a list of our staff up to date,
and a destitution reaches every server at once.

No API can attach the requirement — a bot token is answered
``20001 — Bots cannot use this endpoint``. So when a role is not linked yet,
this command opens a **window** in which the staffer who ran it holds
`Manage Roles` and can do it themselves, inside the containment described in
:mod:`services.team_link_session`. One window covers every role being linked
rather than stripping the staffer twice. The card then reports what came of it,
and falls back on the manual instructions when the window could not be opened.

See docs/LINKED_ROLES.md.
"""

import logging
from typing import List, Optional, Tuple

import discord
from discord import ui

from staff.framework import (
    StaffCommand, SlashOption, staff_command, design, CommandType, parse_guild_id,
)
from utils import emojis
from utils.i18n import t
from utils.moddy_team_role import (
    KINDS, LINKED_ROLES_URL, MANAGER, TEAM, TeamRoleKind, can_manage,
    create_team_role, find_team_role, is_linked,
)
from services import team_link_session as linking
from cogs.error_handler import BaseView

logger = logging.getLogger("moddy.staff.team.role")

#: What `roles` may be. ``team`` alone is the default: the manager role is the
#: exception, not the norm, and creating it in every server that ever needed
#: support would be handing out a distinction nobody asked for.
SCOPE_TEAM = "team"
SCOPE_MANAGER = "manager"
SCOPE_BOTH = "both"
SCOPES = (SCOPE_TEAM, SCOPE_MANAGER, SCOPE_BOTH)


def kinds_for_scope(scope: Optional[str]) -> Tuple[TeamRoleKind, ...]:
    """The roles a scope asks for. Anything unrecognised means the base role."""
    lowered = (scope or "").strip().lower()
    if lowered == SCOPE_BOTH:
        return KINDS
    if lowered == SCOPE_MANAGER:
        return (MANAGER,)
    return (TEAM,)


def _blocker(guild: discord.Guild, member, roles: List[discord.Role]) -> str:
    """Why the window cannot be opened here, or ``""``.

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
    # A staffer sitting at or above Moddy is no longer refused: the window sets
    # aside what it can and says so. See `linking.unstrippable_roles`.
    if any(role >= me.top_role for role in roles):
        # The only genuine floor. Everything else the window needs, it makes:
        # a new role is inserted at position 1 and pushes the rest up, so
        # creating the throwaway role produces the slots by itself. What cannot
        # be produced is authority over a role that is *not* below Moddy, since
        # Discord refuses to move it and refuses to let the bot raise itself.
        return "no_room"
    return ""


def requirement_list(pairs: List[Tuple[TeamRoleKind, discord.Role]]) -> str:
    """``**Moddy Team** → `team``` — the role and the requirement to put on it.

    The metadata *key* is named rather than the label Discord shows in the
    requirement picker: the key is what the backend registered and what we can
    state for certain, and a staffer reading it out can match it on screen.
    """
    return "\n".join(f"• **{role.name}** → `{kind.metadata}`" for kind, role in pairs)


def _window_card(locale: str, pairs: List[Tuple[TeamRoleKind, discord.Role]],
                 staying=()) -> BaseView:
    """The instructions the staffer has one window to follow.

    ``staying`` are the roles Moddy cannot set aside (they sit at or above its
    own). When there are any, the card says the containment is partial rather
    than letting the staffer believe in a box that is not closed.
    """
    view = BaseView()
    container = design.make_container("warning")
    container.add_item(ui.TextDisplay(
        f"{design.title_line(emojis.PENDING, t('staff.team.role.window_title', locale=locale, seconds=linking.WINDOW_SECONDS))}\n"
        f"{t('staff.team.role.window', locale=locale)}\n"
        f"{requirement_list(pairs)}"
    ))
    container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    container.add_item(ui.TextDisplay(f"-# {t('staff.team.role.window_rules', locale=locale)}"))
    if staying:
        container.add_item(ui.TextDisplay(
            f"-# {t('staff.team.role.window_kept_roles', locale=locale, roles=', '.join(r.mention for r in staying))}"))
    view.add_item(container)
    return view


@staff_command
class TeamRoleCommand(StaffCommand):
    command_type = CommandType.TEAM
    name = "role"
    defer = True
    description = "Create the Moddy Team roles (linked roles) in a server."
    options = [
        SlashOption("guild_id", "string",
                    "Target guild id — defaults to the server you run this in.",
                    required=False),
        SlashOption("roles", "string",
                    "Which role — the team role by default, or the manager one, or both.",
                    required=False, default=SCOPE_TEAM, choices=list(SCOPES)),
    ]

    def parse_message(self, raw: str) -> dict:
        """``t.role [guild_id] [team|manager|both]``, in either order.

        A scope is a word from a three-item list and a guild id is digits, so
        the two can never be confused for one another.
        """
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
                locale, "t.role [guild_id] [team|manager|both]"))
            return

        guild = ctx.bot.get_guild(gid)
        if not guild:
            await ctx.send(view=design.error(
                t("staff.team.server_notfound_title", locale=locale),
                t("staff.team.server_notfound", locale=locale, id=f"`{gid}`"),
            ))
            return

        kinds = kinds_for_scope(ctx.opt("roles"))

        # --- 1. the roles exist -------------------------------------------
        roles: List[Tuple[TeamRoleKind, discord.Role]] = []
        created_keys = set()
        for kind in kinds:
            role = await find_team_role(ctx.bot, guild, kind)
            if role is None:
                if not can_manage(guild):
                    await ctx.send(view=design.error(
                        t("staff.team.role.failed_title", locale=locale),
                        t("staff.team.role.no_permission", locale=locale,
                          name=f"**{guild.name}**"),
                    ))
                    return
                try:
                    role = await create_team_role(ctx.bot, guild, actor=ctx.author,
                                                  kind=kind)
                except discord.Forbidden:
                    await ctx.send(view=design.error(
                        t("staff.team.role.failed_title", locale=locale),
                        t("staff.team.role.no_permission", locale=locale,
                          name=f"**{guild.name}**"),
                    ))
                    return
                except discord.HTTPException as e:
                    logger.warning("Could not create the %s role in %s: %s",
                                   kind.name, gid, e)
                    await ctx.send(view=design.error(
                        t("staff.team.role.failed_title", locale=locale),
                        t("staff.team.role.http_error", locale=locale, error=f"`{e}`"),
                    ))
                    return
                created_keys.add(kind.key)
                logger.info("Staff %s created the %s role in %s",
                            ctx.author.id, kind.name, gid)
            roles.append((kind, role))

        # --- 2. the ones that still need a requirement ---------------------
        pending = [(kind, role) for kind, role in roles if not is_linked(role)]
        outcome = None
        blocker = ""
        linked_now = set()
        if pending:
            member = guild.get_member(ctx.author.id)
            blocker = _blocker(guild, member, [role for _, role in pending])
            if not blocker:
                # Tell them what to do *before* the clock starts: the card is
                # the instructions, and the window is not long enough to read
                # them afterwards.
                await ctx.send(view=_window_card(
                    locale, pending,
                    staying=linking.unstrippable_roles(guild, member)))
                result = await linking.run_window(ctx.bot, guild, member,
                                                  [role for _, role in pending])
                outcome = result.outcome
                # The window's own answer, not `role.tags`: a role fetched to
                # confirm the outcome is not the cached object this holds.
                linked_now = result.linked_ids

        await ctx.send(view=self._report(locale, guild, roles, created_keys,
                                         outcome, blocker, linked_now))

    # ---------------------------------------------------------------- report
    def _report(self, locale: str, guild: discord.Guild,
                roles: List[Tuple[TeamRoleKind, discord.Role]],
                created_keys: set, outcome: Optional[str],
                blocker: str, linked_now: set) -> BaseView:
        """One state block per role, then why anything is still unlinked."""
        states = [(kind, role, is_linked(role) or role.id in linked_now)
                  for kind, role in roles]
        all_linked = all(linked for _, _, linked in states)

        view = BaseView()
        container = design.make_container("success" if all_linked else "warning")
        created = [role for kind, role in roles if kind.key in created_keys]
        headline = ("created" if created else "exists")
        container.add_item(ui.TextDisplay(
            f"{design.title_line(emojis.STAFF, t('staff.team.role.title', locale=locale))}\n"
            f"{t('staff.team.role.' + headline, locale=locale, roles=', '.join(r.mention for r in (created or [r for _, r in roles])), guild=f'**{guild.name}**')}"
        ))

        for kind, role, linked in states:
            granted = [name for name, value in role.permissions if value]
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(ui.TextDisplay(
                f"**{role.name}**\n"
                f"-# {t('staff.team.role.id', locale=locale)}: `{role.id}`\n"
                f"-# {t('staff.team.role.metadata', locale=locale)}: `{kind.metadata}`\n"
                f"-# {t('staff.team.role.linked', locale=locale)}: "
                f"{emojis.DONE if linked else emojis.UNDONE} "
                f"{t('staff.team.role.linked_' + ('yes' if linked else 'no'), locale=locale)}\n"
                f"-# {t('staff.team.role.permissions', locale=locale)}: `{len(granted)}`"
            ))

        if not all_linked:
            # Why the window did not get there, then the manual path — which is
            # what a staffer reads out loud to an administrator when all else
            # fails.
            missing = [(kind, role) for kind, role, linked in states if not linked]
            names = ", ".join(f"**{role.name}**" for _, role in missing)
            reason = f"window_{outcome}" if outcome else f"blocked_{blocker}"
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(ui.TextDisplay(
                f"**{t('staff.team.role.howto_title', locale=locale)}**\n"
                f"-# {t('staff.team.role.' + reason, locale=locale, roles=names)}\n"
                f"{t('staff.team.role.howto', locale=locale)}\n"
                f"{requirement_list(missing)}"
            ))
        container.add_item(ui.TextDisplay(
            f"-# {t('staff.team.role.hint', locale=locale)}"))
        view.add_item(container)

        row = ui.ActionRow()
        row.add_item(ui.Button(label=t("staff.team.role.docs", locale=locale),
                               url=LINKED_ROLES_URL, style=discord.ButtonStyle.link))
        view.add_item(row)
        return view
