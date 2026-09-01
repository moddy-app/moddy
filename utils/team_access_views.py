"""`/team access` — asking a server's administrator for permissions, in front of them.

The Moddy team sometimes needs real permissions in a server to do what it was
asked to do: read a log channel, reproduce a bug in a private category, fix a
broken overwrite. This is how that is asked for, and the shape of it is the
point:

- **The staffer picks what they need**, from a fixed catalogue — never
  `administrator`, which is not on the list at all and cannot be requested
  through this surface.
- **The administrator answers**, on a card posted in the channel where the
  conversation is happening (a ticket, usually), with a plain *Accept* or
  *Refuse*. Nothing happens until they click.
- **Everything granted goes to a Moddy Team role, and only to one** — the base
  role by default, the manager one when the staffer asks for it; never to a
  staff member's account, never to a role the server uses for anything else.
  Removing the team's access later is therefore one role edit the server can do
  without us, and the permissions disappear from every Moddy staffer at once.
  The card always names the role it would grant to, because which of the two it
  is changes who ends up holding the permissions.

Permissions are **added** to the role, never replaced: two requests accepted a
week apart both stand, and accepting a request never quietly drops what an
earlier one granted.

Persistence: the picker and the request card are all ``DynamicItem``s carrying
the requested permission bitfield, the requester's id and which role is being
asked for, so a card answered three weeks and two restarts later behaves exactly
like a fresh one. The role segment is optional in every template: cards posted
before there were two roles are still answerable, and mean the base role. See
docs/PERSISTENT_VIEWS.md and docs/LINKED_ROLES.md.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional, Tuple

import discord
from discord import ui

from cogs.error_handler import BaseView
from staff.framework import design
from utils import emojis
from utils.components_v2 import create_error_message
from utils.i18n import i18n, t
from utils.moddy_team_role import TEAM, can_manage, find_team_role, kind_from_key

logger = logging.getLogger('moddy.team_access')

# --------------------------------------------------------------------------- #
# The catalogue
#
# Twenty-five permissions — Discord's ceiling for one select — covering what
# support work in somebody else's server actually needs. `administrator` is
# deliberately absent: a request for it could not be answered informedly by
# clicking a button, and every legitimate need is one of the entries below.
#
# The labels are the ones the server logs already translate
# (`modules.logs.permissions.*`), so an admin reads the same wording here as in
# their own audit log.
# --------------------------------------------------------------------------- #
ACCESS_PERMISSIONS: Tuple[str, ...] = (
    "view_channel",
    "read_message_history",
    "send_messages",
    "send_messages_in_threads",
    "embed_links",
    "attach_files",
    "add_reactions",
    "manage_messages",
    "manage_threads",
    "create_public_threads",
    "create_private_threads",
    "manage_channels",
    "manage_roles",
    "manage_webhooks",
    "manage_guild",
    "manage_nicknames",
    "view_audit_log",
    "kick_members",
    "ban_members",
    "moderate_members",
    "manage_events",
    "mention_everyone",
    "connect",
    "speak",
    "move_members",
)

# The role segment is optional on purpose: a card posted before the manager
# role existed carries no third field, and must keep working — it means the
# base role, which is what it was posted for.
_KIND = r"(?::(?P<kind>team|manager))?"
_CID_PICK = r"moddy:teamaccess:pick:(?P<requester>\d{1,20}):(?P<perms>\d{1,20})" + _KIND
_CID_SEND = r"moddy:teamaccess:send:(?P<requester>\d{1,20}):(?P<perms>\d{1,20})" + _KIND
_CID_DECIDE = (r"moddy:teamaccess:(?P<action>accept|refuse):"
               r"(?P<requester>\d{1,20}):(?P<perms>\d{1,20})" + _KIND)

STATE_PENDING = "pending"
STATE_GRANTED = "granted"
STATE_REFUSED = "refused"


# --------------------------------------------------------------------------- #
# Permission bitfield <-> keys
# --------------------------------------------------------------------------- #
def permission_label(key: str, locale: str) -> str:
    """Discord's own wording for a permission, in ``locale``."""
    return t(f"modules.logs.permissions.{key}", locale=locale)


def keys_to_value(keys) -> int:
    """The Discord permission bitfield for these catalogue keys."""
    permissions = discord.Permissions.none()
    for key in keys:
        if key in ACCESS_PERMISSIONS:
            setattr(permissions, key, True)
    return permissions.value


def value_to_keys(value: int) -> List[str]:
    """Catalogue keys set in this bitfield, in catalogue order.

    Anything outside the catalogue is dropped rather than trusted: the bitfield
    travels in a custom_id, and a hand-edited one must not be able to smuggle
    ``administrator`` past the picker.
    """
    permissions = discord.Permissions(value)
    return [key for key in ACCESS_PERMISSIONS if getattr(permissions, key, False)]


def missing_from_bot(guild: discord.Guild, keys) -> List[str]:
    """Requested permissions Moddy does not itself hold.

    Discord refuses to let a bot grant a permission it does not have, so this
    is checked before the request goes out *and* before the role edit — an
    administrator must never accept something that then fails.
    """
    mine = guild.me.guild_permissions if guild.me else discord.Permissions.none()
    return [key for key in keys if not getattr(mine, key, False)]


def _guarded(callback):
    """Route DynamicItem callback errors to the central error handler.

    Dynamic items dispatched through ``add_dynamic_items`` have no live
    ``BaseView``, so ``BaseView.on_error`` never fires.
    """
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as e:  # noqa: BLE001 — funnel everything to the handler
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, e, self.__class__.__name__)
    return wrapper


async def _reject_stranger(interaction: discord.Interaction, requester_id: int) -> bool:
    """True when the clicker is not the staffer who opened this picker."""
    if interaction.user.id == requester_id:
        return False
    locale = i18n.get_user_locale(interaction)
    await interaction.response.send_message(
        view=create_error_message(
            t("errors.not_your_message.title", locale=locale),
            t("errors.not_your_message.description", locale=locale),
        ),
        ephemeral=True,
    )
    return True


# --------------------------------------------------------------------------- #
# 1. The picker (ephemeral, staff side)
# --------------------------------------------------------------------------- #
class TeamAccessSelect(ui.DynamicItem[ui.Select], template=_CID_PICK):
    """Which permissions to ask for. Auth: the staffer who ran the command.

    The selection is carried in the *next* view's custom_ids rather than on
    ``self``: a DynamicItem is rebuilt from scratch on every click, so there is
    no instance to stage a choice on.
    """

    def __init__(self, requester_id: int, value: int = 0, locale: str = "en-US",
                 kind_key: str = TEAM.key):
        chosen = set(value_to_keys(value))
        super().__init__(
            ui.Select(
                custom_id=f"moddy:teamaccess:pick:{requester_id}:{value}:{kind_key}",
                placeholder=t("staff.team.access.placeholder", locale=locale)[:150],
                min_values=1,
                max_values=len(ACCESS_PERMISSIONS),
                options=[
                    discord.SelectOption(
                        label=permission_label(key, locale)[:100],
                        value=key,
                        default=key in chosen,
                    )
                    for key in ACCESS_PERMISSIONS
                ],
            )
        )
        self.requester_id = requester_id
        self.value = value
        self.locale = locale
        self.kind_key = kind_key

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction,
                             item: ui.Select, match: re.Match):
        return cls(int(match["requester"]), int(match["perms"]),
                   locale=i18n.get_user_locale(interaction),
                   kind_key=kind_from_key(match["kind"]).key)

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if await _reject_stranger(interaction, self.requester_id):
            return
        value = keys_to_value(self.item.values)
        await interaction.response.edit_message(
            view=TeamAccessPickerView(self.requester_id, value,
                                      locale=i18n.get_user_locale(interaction),
                                      kind_key=self.kind_key))


class TeamAccessSendButton(ui.DynamicItem[ui.Button], template=_CID_SEND):
    """Post the request card in the channel. Auth: the staffer who ran the command."""

    def __init__(self, requester_id: int, value: int = 0, locale: str = "en-US",
                 kind_key: str = TEAM.key):
        super().__init__(
            ui.Button(
                label=t("staff.team.access.send", locale=locale),
                style=discord.ButtonStyle.primary,
                emoji=discord.PartialEmoji.from_str(emojis.MESSAGE),
                custom_id=f"moddy:teamaccess:send:{requester_id}:{value}:{kind_key}",
                disabled=not value,
            )
        )
        self.requester_id = requester_id
        self.value = value
        self.kind_key = kind_key

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction,
                             item: ui.Button, match: re.Match):
        return cls(int(match["requester"]), int(match["perms"]),
                   locale=i18n.get_user_locale(interaction),
                   kind_key=kind_from_key(match["kind"]).key)

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if await _reject_stranger(interaction, self.requester_id):
            return

        locale = i18n.get_user_locale(interaction)
        bot = interaction.client
        guild = interaction.guild

        # Being the requester is not enough: this is the one click that reaches
        # outside the ephemeral message, and a picker outlives a demotion.
        from utils.staff_permissions import CommandType, staff_permissions
        if not await staff_permissions.can_use_command_type(
                interaction.user.id, CommandType.TEAM):
            await interaction.response.edit_message(view=design.permission_denied(
                locale, t("staff.team.access.not_staff", locale=locale)))
            return

        keys = value_to_keys(self.value)
        if not guild or not keys:
            await interaction.response.edit_message(view=design.error(
                t("staff.team.access.failed_title", locale=locale),
                t("staff.team.access.nothing_picked", locale=locale),
            ))
            return

        kind = kind_from_key(self.kind_key)
        role = await find_team_role(bot, guild, kind)
        if not role:
            await interaction.response.edit_message(view=design.error(
                t("staff.team.access.no_role_title", locale=locale),
                t("staff.team.access.no_role", locale=locale, name=f"**{kind.name}**"),
            ))
            return

        # The card is read by the server's side of the conversation, so it
        # speaks the server's language — not the staffer's.
        from utils.guild_language import guild_locale
        card_locale = await guild_locale(bot, guild)
        card = await build_access_card(
            bot, guild, requester_id=self.requester_id, value=self.value,
            locale=card_locale, state=STATE_PENDING, kind_key=kind.key,
        )

        try:
            await interaction.channel.send(view=card)
        except discord.HTTPException as e:
            logger.warning("Could not post the access request in %s: %s",
                           getattr(interaction.channel, 'id', None), e)
            await interaction.response.edit_message(view=design.error(
                t("staff.team.access.failed_title", locale=locale),
                t("staff.team.access.cannot_post", locale=locale),
            ))
            return

        await interaction.response.edit_message(view=design.success(
            t("staff.team.access.sent_title", locale=locale),
            t("staff.team.access.sent", locale=locale, role=role.mention),
        ))
        logger.info("Staff %s requested %s on the %s role in guild %s",
                    self.requester_id, ",".join(keys), kind.key, guild.id)


class TeamAccessPickerView(BaseView):
    """Ephemeral picker shown to the staffer. Persistent: yes (dynamic items).

    Auth: the requester id encoded in every child's custom_id, compared to
    ``interaction.user.id`` on click — an ``interaction_check`` could not work
    on a restarted shell.
    """

    __persistent__ = True

    def __init__(self, requester_id: int = 0, value: int = 0, locale: str = "en-US",
                 kind_key: str = TEAM.key):
        super().__init__()  # timeout=None
        self.requester_id = requester_id
        self.value = value
        self.locale = locale
        self.kind_key = kind_key
        kind = kind_from_key(kind_key)

        keys = value_to_keys(value)
        container = design.make_container("primary")
        container.add_item(ui.TextDisplay(
            f"{design.title_line(emojis.LEGAL, t('staff.team.access.picker_title', locale=locale))}\n"
            f"{t('staff.team.access.picker_desc', locale=locale, name=f'**{kind.name}**')}"
        ))
        if keys:
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(ui.TextDisplay("\n".join(
                f"-# • {permission_label(key, locale)}" for key in keys)))
        self.add_item(container)

        select_row = ui.ActionRow()
        select_row.add_item(TeamAccessSelect(requester_id, value, locale=locale,
                                             kind_key=kind.key))
        self.add_item(select_row)

        button_row = ui.ActionRow()
        button_row.add_item(TeamAccessSendButton(requester_id, value, locale=locale,
                                                 kind_key=kind.key))
        self.add_item(button_row)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: requester-scoped — the staffer's id is encoded in every
        child custom_id and compared to ``interaction.user.id`` on click."""
        bot.add_dynamic_items(TeamAccessSelect, TeamAccessSendButton)


# --------------------------------------------------------------------------- #
# 2. The request card (public, administrator side)
# --------------------------------------------------------------------------- #
class TeamAccessDecisionButton(ui.DynamicItem[ui.Button], template=_CID_DECIDE):
    """*Accept* / *Refuse* on a request card. Auth: a guild administrator.

    Granting permissions to a role is an administrator's decision and nothing
    less — the check is re-derived from ``interaction.user`` on every click, so
    a member who was an admin when the card was posted and is not one now
    cannot answer it.
    """

    def __init__(self, action: str, requester_id: int, value: int = 0,
                 locale: str = "en-US", kind_key: str = TEAM.key):
        accept = action == "accept"
        super().__init__(
            ui.Button(
                label=t(f"staff.team.access.{'accept' if accept else 'refuse'}",
                        locale=locale),
                style=discord.ButtonStyle.success if accept else discord.ButtonStyle.secondary,
                emoji=discord.PartialEmoji.from_str(
                    emojis.DONE if accept else emojis.UNDONE),
                custom_id=f"moddy:teamaccess:{action}:{requester_id}:{value}:{kind_key}",
            )
        )
        self.action = action
        self.requester_id = requester_id
        self.value = value
        self.kind_key = kind_key

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction,
                             item: ui.Button, match: re.Match):
        return cls(match["action"], int(match["requester"]), int(match["perms"]),
                   kind_key=kind_from_key(match["kind"]).key)

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        bot = interaction.client
        guild = interaction.guild
        actor = interaction.user
        locale = i18n.get_user_locale(interaction)

        if not guild or not getattr(actor, 'guild_permissions', None) \
                or not actor.guild_permissions.administrator:
            await interaction.response.send_message(
                view=create_error_message(
                    t("staff.team.access.denied_title", locale=locale),
                    t("staff.team.access.denied", locale=locale),
                ),
                ephemeral=True,
            )
            return

        from utils.guild_language import guild_locale
        card_locale = await guild_locale(bot, guild)

        kind = kind_from_key(self.kind_key)
        if self.action == "refuse":
            await interaction.response.edit_message(view=await build_access_card(
                bot, guild, requester_id=self.requester_id, value=self.value,
                locale=card_locale, state=STATE_REFUSED, actor=actor,
                kind_key=kind.key,
            ))
            logger.info("Access request from %s refused in guild %s by %s",
                        self.requester_id, guild.id, actor.id)
            return

        keys = value_to_keys(self.value)
        role = await find_team_role(bot, guild, kind)
        problem = None
        if not role:
            problem = t("staff.team.access.no_role", locale=card_locale,
                        name=f"**{kind.name}**")
        elif not can_manage(guild, role):
            problem = t("staff.team.access.cannot_edit", locale=card_locale,
                        role=role.mention)
        else:
            missing = missing_from_bot(guild, keys)
            if missing:
                problem = t("staff.team.access.bot_missing", locale=card_locale,
                            permissions=", ".join(
                                f"`{permission_label(k, card_locale)}`" for k in missing))

        if problem:
            await interaction.response.send_message(
                view=create_error_message(
                    t("staff.team.access.failed_title", locale=card_locale), problem),
                ephemeral=True,
            )
            return

        # Added, never replaced: an earlier accepted request keeps standing.
        wanted = discord.Permissions(role.permissions.value | self.value)
        try:
            await role.edit(
                permissions=wanted,
                reason=(f"{kind.name} access granted by {actor} ({actor.id}) — "
                        f"requested by {self.requester_id}"),
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                view=create_error_message(
                    t("staff.team.access.failed_title", locale=card_locale),
                    t("staff.team.access.cannot_edit", locale=card_locale,
                      role=role.mention)),
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(view=await build_access_card(
            bot, guild, requester_id=self.requester_id, value=self.value,
            locale=card_locale, state=STATE_GRANTED, actor=actor, role=role,
            kind_key=kind.key,
        ))
        logger.info("Access %s granted to role %s in guild %s by %s (requested by %s)",
                    ",".join(keys), role.id, guild.id, actor.id, self.requester_id)


class TeamAccessRequestView(BaseView):
    """The request card. Persistent: yes. Auth: guild administrator, per click.

    The requested bitfield and the requester travel in the buttons' custom_ids,
    so the card carries its own meaning: nothing is looked up from a table that
    could have been cleaned up, and a restart changes nothing.
    """

    __persistent__ = True

    def __init__(self, container: Optional[ui.Container] = None,
                 requester_id: int = 0, value: int = 0,
                 locale: str = "en-US", state: str = STATE_PENDING,
                 kind_key: str = TEAM.key):
        super().__init__()  # timeout=None
        if container is not None:
            self.add_item(container)
        if state == STATE_PENDING:
            row = ui.ActionRow()
            row.add_item(TeamAccessDecisionButton("accept", requester_id, value,
                                                  locale=locale, kind_key=kind_key))
            row.add_item(TeamAccessDecisionButton("refuse", requester_id, value,
                                                  locale=locale, kind_key=kind_key))
            self.add_item(row)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: guild administrator, re-derived from ``interaction.user``
        on every click; the request itself is encoded in the custom_id."""
        bot.add_dynamic_items(TeamAccessDecisionButton)


async def build_access_card(bot, guild: discord.Guild, *, requester_id: int,
                            value: int, locale: str, state: str = STATE_PENDING,
                            actor: Optional[discord.abc.User] = None,
                            role: Optional[discord.Role] = None,
                            kind_key: str = TEAM.key
                            ) -> TeamAccessRequestView:
    """The permission-request card, in whichever of its three states it is in."""
    from utils.altguard_views import format_member_name

    kind = kind_from_key(kind_key)
    keys = value_to_keys(value)
    requester = bot.get_user(requester_id)
    if requester is None:
        try:
            requester = await bot.fetch_user(requester_id)
        except discord.HTTPException:
            requester = None
    # No hyperlink on the badge here: this card is read by the server's
    # administrator, not by the team.
    who = (await format_member_name(bot, requester, link=False)) if requester else f"`{requester_id}`"

    accent, icon, title = {
        STATE_PENDING: ("warning", emojis.LEGAL, "title"),
        STATE_GRANTED: ("success", emojis.DONE, "granted_title"),
        STATE_REFUSED: ("neutral", emojis.UNDONE, "refused_title"),
    }[state]

    container = design.make_container(accent)
    container.add_item(ui.TextDisplay(
        f"{design.title_line(icon, t(f'staff.team.access.{title}', locale=locale))}\n"
        f"{t('staff.team.access.body', locale=locale, user=who, name=f'**{kind.name}**')}"
    ))
    container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    container.add_item(ui.TextDisplay(
        f"**{t('staff.team.access.requested', locale=locale)}**\n"
        + "\n".join(f"• {permission_label(key, locale)}" for key in keys)
    ))

    if state == STATE_PENDING:
        container.add_item(ui.TextDisplay(
            f"-# {t('staff.team.access.notice', locale=locale, name=f'**{kind.name}**')}"))
    elif state == STATE_GRANTED:
        target = role.mention if role else f"**{kind.name}**"
        container.add_item(ui.TextDisplay(
            f"-# {t('staff.team.access.granted_by', locale=locale, user=actor.mention if actor else '—', role=target)}"))
    else:
        container.add_item(ui.TextDisplay(
            f"-# {t('staff.team.access.refused_by', locale=locale, user=actor.mention if actor else '—')}"))

    return TeamAccessRequestView(container, requester_id, value,
                                 locale=locale, state=state, kind_key=kind.key)
