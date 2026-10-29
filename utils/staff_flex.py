"""The staff "flex" card — public proof of Moddy team membership.

Built in one place for its two entry points:

- ``t.flex`` / ``/team flex`` (``staff/commands/team/flex.py``);
- the **Flex** button ``/user`` adds when a staffer looks themselves up in a
  DM or a group DM (``cogs/user.py``). Clicking it answers the interaction
  with the card, **not** ephemerally: the person on the other side of the DM
  sees it — that is the whole point, staff commands do not exist there.

The button is a ``DynamicItem`` carrying the staffer's id: only that person
may click it, and their staff roles are re-read on every click, so a button
left in an old DM stops working the moment they leave the team.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import discord
from discord import ui

from cogs.error_handler import BaseView
from staff.framework import design
from utils.emojis import STAFF
from utils.errors import NotYours
from utils.i18n import i18n, t

logger = logging.getLogger("moddy.staff_flex")

# Exact flex design: Moddy square icon + accent colour 27391.
MODDY_ICON = "<:ModdyIconCarre:1517239121322578090>"
FLEX_ACCENT = 27391

ROLE_KEY = {
    "Dev": "developer",
    "Manager": "manager",
    "Supervisor_Mod": "mod_supervisor",
    "Supervisor_Com": "member",
    "Supervisor_Sup": "support",
    "Moderator": "moderator",
    "Communication": "member",
    "Support": "support",
}


async def staff_roles(user_id: int) -> list:
    """The staffer's roles, ``[]`` for anybody else (or before the system is up)."""
    from utils import staff_permissions as sp

    if sp.staff_permissions is None:
        return []
    try:
        return await sp.staff_permissions.get_user_roles(user_id)
    except Exception as exc:  # noqa: BLE001 - a failed lookup means "not staff", never a crash
        logger.warning("Staff role lookup failed for %s: %s", user_id, exc)
        return []


async def build_flex_view(user: discord.abc.User, locale: str) -> Optional[BaseView]:
    """The flex card for ``user``, or ``None`` when they hold no staff role."""
    roles = await staff_roles(user.id)
    if not roles:
        return None

    role_key = ROLE_KEY.get(roles[0].value, "member")
    role_display = t(f"staff.team.flex.roles.{role_key}", locale=locale)

    view = BaseView()
    container = design.make_container(FLEX_ACCENT)
    container.add_item(ui.TextDisplay(
        f"### **{MODDY_ICON} {user.mention} "
        f"{t('staff.team.flex.message', locale=locale, role=role_display)}**"
    ))
    container.add_item(ui.TextDisplay(
        f"-# {t('staff.team.flex.authorized', locale=locale)}\n"
        f"-# {t('staff.team.flex.prevent', locale=locale)}\n"
        "-# [Report Staff](https://moddy.app/report-staff) • "
        "[Support](https://moddy.app/support) • [Documentation](https://moddy.app/docs-flex)"
    ))
    view.add_item(container)
    return view


def is_private_context(interaction: discord.Interaction) -> bool:
    """A DM with Moddy, or a DM / group DM between users (user-installed app)."""
    context = getattr(interaction, "context", None)
    return bool(context and (context.dm_channel or context.private_channel))


def _guarded(callback):
    """Route DynamicItem callback errors to the central error handler.

    A DynamicItem dispatched via ``bot.add_dynamic_items`` has no live
    ``BaseView``, so ``BaseView.on_error`` never fires.
    """
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as e:  # noqa: BLE001 — funnel everything to the handler
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, e, self.__class__.__name__)
    return wrapper


class FlexSendButton(ui.DynamicItem[ui.Button],
                     template=r"moddy:user:flex:send:(?P<owner>\d+)"):
    """Under ``/user`` on oneself, in a DM: post the flex card publicly.

    Auth: the encoded owner only, and they must still be staff.
    """

    def __init__(self, owner_id: int, *, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=t("commands.user.buttons.flex", locale=locale)[:80],
            style=discord.ButtonStyle.primary,
            emoji=discord.PartialEmoji.from_str(STAFF),
            custom_id=f"moddy:user:flex:send:{owner_id}",
        ))
        self.owner_id = int(owner_id)

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button,
                             match: re.Match):
        return cls(int(match["owner"]), locale=i18n.get_user_locale(interaction))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            raise NotYours()
        locale = i18n.get_user_locale(interaction)

        view = await build_flex_view(interaction.user, locale)
        if view is None:
            await interaction.response.send_message(
                view=design.error(
                    t("staff.team.flex.not_staff_title", locale=locale),
                    t("staff.team.flex.not_staff", locale=locale),
                ),
                ephemeral=True,
            )
            return

        # Not ephemeral: the other side of the DM is who this is for.
        await interaction.response.send_message(view=view, ephemeral=False)
        logger.info("Staff %s used flex from /user", interaction.user.id)


class UserFlexPersistence(BaseView):
    """Marker view: registers the /user Flex button at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(FlexSendButton)
