"""`t.config` — the server's /config panel, opened by a Moddy staffer.

When the team has to step into a server to set something up, it gets the very
same panel as ``/config`` (``cogs.config.ConfigMainView``) — not a copy that
could drift from it.

**Only staff can ever touch it.** The panel is always delivered as an
*ephemeral* message: Discord shows it to the staffer alone, and nobody else
can click it. A message command cannot answer ephemerally, so on that
transport ``t.config`` posts a small launcher card whose button opens the
panel privately — and that button re-checks, on every click, that the clicker
may run ``t.config``. Anybody else gets a refusal.

Every screen below the root then authorizes through
``modules.configs._common.check_guild_perms``, which lets a staffer without
*Manage Server* through on exactly one condition: the click comes from an
ephemeral message (so it was handed to them, by this flow) **and** they may run
``t.config``. A public ``/config`` panel posted in a channel never qualifies —
there, Manage Server stays the only key.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, Optional, Tuple

import discord
from discord import ui

from config import COLORS
from cogs.error_handler import BaseView
from utils.emojis import SETTINGS
from utils.i18n import i18n, t

logger = logging.getLogger("moddy.staff_config")

_CID_OPEN = "moddy:staffconfig:launcher:open"

#: How long a staff verdict is reused. Each click in the panel is checked once
#: by the view and often again by its callback, before a reply Discord wants
#: within 3 s (some open a modal, which must be the first response); a revoked
#: staffer keeps access for at most this long.
_STAFF_CACHE_TTL = 60.0
_staff_cache: Dict[int, Tuple[float, bool]] = {}


async def can_configure_as_staff(bot, user_id: int) -> bool:
    """Whether ``user_id`` may run ``t.config`` (staff, not denied the command)."""
    from utils import staff_permissions as sp
    from utils.staff_permissions import CommandType

    cached = _staff_cache.get(user_id)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    if sp.staff_permissions is None:
        return False
    try:
        allowed, _ = await sp.staff_permissions.check_command_permission(
            user_id, CommandType.TEAM, "config")
    except Exception as exc:  # noqa: BLE001 - a failed lookup is a refusal, never a pass
        logger.warning("Staff check for t.config failed for %s: %s", user_id, exc)
        return False
    _staff_cache[user_id] = (time.monotonic() + _STAFF_CACHE_TTL, allowed)
    return allowed


async def build_staff_config_panel(bot, guild_id: int, user_id: int,
                                   locale: str) -> BaseView:
    """The /config main screen for ``guild_id``, exactly as /config builds it."""
    from cogs.config import ConfigMainView
    from utils import global_sanctions

    # The "limited" banner is about the server: the staffer's own record is
    # irrelevant to what this server may configure.
    limited = await global_sanctions.is_limited(bot, guild_id=guild_id)
    return await ConfigMainView.create(bot, guild_id, user_id, locale, limited=limited)


class StaffConfigLauncherView(BaseView):
    """The card ``t.config`` posts: one button opening the panel privately.

    Persistent: yes. Auth: re-checked on every click from ``interaction.user``
    (``can_configure_as_staff``) and ``interaction.guild_id`` — a static
    custom_id is enough, the card stores nothing.
    """

    __persistent__ = True

    def __init__(self, bot=None, locale: str = "en-US"):
        super().__init__()  # timeout=None
        self.bot = bot
        self.locale = locale

        container = ui.Container(accent_colour=discord.Colour(COLORS["developer"]))
        container.add_item(ui.TextDisplay(
            f"### {SETTINGS} {t('staff.team.config.title', locale=locale)}"
        ))
        container.add_item(ui.TextDisplay(t("staff.team.config.description", locale=locale)))
        container.add_item(ui.TextDisplay(f"-# {t('staff.team.config.staff_only', locale=locale)}"))

        row = ui.ActionRow()
        button = ui.Button(
            label=t("staff.team.config.open", locale=locale),
            style=discord.ButtonStyle.primary,
            emoji=discord.PartialEmoji.from_str(SETTINGS),
            custom_id=_CID_OPEN,
        )
        button.callback = self.on_open
        row.add_item(button)
        container.add_item(row)
        self.add_item(container)

    async def on_open(self, interaction: discord.Interaction):
        # Ephemeral from the first byte: whatever follows is for the clicker only.
        await interaction.response.defer(ephemeral=True, thinking=True)
        locale = i18n.get_user_locale(interaction)
        bot = interaction.client

        if not await can_configure_as_staff(bot, interaction.user.id):
            await interaction.followup.send(
                t("staff.team.config.not_staff", locale=locale), ephemeral=True)
            return
        if interaction.guild is None or interaction.guild.me is None:
            await interaction.followup.send(
                t("modules.config.errors.bot_not_in_guild", locale=locale), ephemeral=True)
            return

        logger.info("Staff %s opened the /config panel of guild %s",
                    interaction.user.id, interaction.guild_id)
        view = await build_staff_config_panel(
            bot, interaction.guild_id, interaction.user.id, locale)
        await interaction.followup.send(view=view, ephemeral=True)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: the clicker must be allowed to run t.config."""
        bot.add_view(cls())


async def is_staff_config_session(interaction: discord.Interaction) -> bool:
    """A staffer clicking in a panel ``t.config`` handed them.

    That panel only ever exists as an ephemeral message, which Discord lets
    its recipient alone click — so "ephemeral message + may run t.config" is
    exactly a staff session. Anything public is refused here.
    """
    message: Optional[discord.Message] = getattr(interaction, "message", None)
    if message is None or not message.flags.ephemeral:
        return False
    return await can_configure_as_staff(interaction.client, interaction.user.id)
