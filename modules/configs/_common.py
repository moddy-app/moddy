"""
Shared helpers for module config panels (modules/configs/*.py).

Every guild config panel (AutoRoleConfigView, StarboardConfigView,
InterServerConfigView, ...) previously re-checked ownership by comparing
interaction.user.id against a self.user_id captured at command time. That
breaks on a restarted persistent-view shell, where self.user_id is None
(docs/PERSISTENT_VIEWS.md Appendix B.5: "interaction.guild_id is already on
the interaction, a static custom_id + re-checked manage_guild is enough" —
no DynamicItem needed for this class of view). check_guild_perms() re-derives
authorization from the interaction alone, matching SocialNotificationsConfigView.

check_bot_perms() is the other half: Moddy never requires Administrator
(CLAUDE.md #12) — each module declares the exact bot permissions it needs in
``ModuleBase.REQUIRED_BOT_PERMISSIONS``, and this is checked right before a
module's config screen opens, instead of gating the whole /config command
behind Administrator.
"""

from typing import List, Mapping, Optional, Sequence

import discord
from discord import ui

from config import COLORS
from utils.emojis import EMOJIS
from utils.i18n import i18n, t
from utils.bot_permissions import (
    format_missing,
    merge_permissions,
    missing_channel_permissions,
    missing_guild_permissions,
)


async def check_guild_perms(interaction: discord.Interaction) -> bool:
    """Authorize a config interaction: requires Manage Server in this guild."""
    perms = getattr(interaction.user, "guild_permissions", None)
    if not interaction.guild_id or perms is None or not perms.manage_guild:
        locale = i18n.get_user_locale(interaction)
        await interaction.response.send_message(
            t("modules.config.errors.no_user_perms", locale=locale),
            ephemeral=True,
        )
        return False
    return True


def missing_bot_permissions(guild: discord.Guild, required: Sequence[str]) -> List[str]:
    """Permission keys from ``required`` that the bot does not hold in ``guild``.

    Never short-circuits: every missing permission is returned at once.
    """
    return missing_guild_permissions(guild, required)


async def check_bot_perms(
    interaction: discord.Interaction,
    required: Sequence[str],
    *,
    module_name: str = "",
    followup: bool = False,
    channel_requirements: Optional[Mapping[int, Sequence[str]]] = None,
) -> bool:
    """Authorize opening a module's config screen: the bot must hold every
    permission the module needs.

    ``required`` is the module's full guild-level list (use
    ``module_class.required_permissions()``: baseline + module specific).
    ``channel_requirements`` (``module_class.channel_requirements(config)``)
    adds the permissions needed in the channels the module is configured for,
    so channel overwrites that deny the bot show up too.

    ALL gaps are listed in one Components V2 message with a re-invite link
    scoped to every missing permission, then False is returned. Pass
    ``followup=True`` when the interaction's initial response was already used
    (e.g. deferred). Cache reads only: no network call.
    """
    guild = interaction.guild
    if guild is None or guild.me is None:
        return True

    missing = missing_guild_permissions(guild, required)
    channel_missing = missing_channel_permissions(
        guild, channel_requirements or {}, skip=missing)
    if not missing and not channel_missing:
        return True

    locale = i18n.get_user_locale(interaction)
    permissions_list = format_missing(missing, channel_missing, locale)

    error_view = ui.LayoutView(timeout=None)
    error_container = ui.Container(accent_colour=discord.Colour(COLORS["error"]))
    error_container.add_item(ui.TextDisplay(
        f"### {EMOJIS['error']} {t('modules.config.errors.missing_bot_perms.title', locale=locale)}"
    ))
    error_container.add_item(ui.TextDisplay(
        t('modules.config.errors.missing_bot_perms.description', locale=locale,
          module=module_name, permissions=permissions_list)
    ))
    error_view.add_item(error_container)

    invite_perms = discord.Permissions.none()
    for key in merge_permissions(missing, *channel_missing.values()):
        if hasattr(invite_perms, key):
            setattr(invite_perms, key, True)

    button_row = ui.ActionRow()
    button_row.add_item(ui.Button(
        label=t('modules.config.errors.missing_bot_perms.button', locale=locale),
        style=discord.ButtonStyle.link,
        url=(
            f"https://discord.com/oauth2/authorize?client_id={interaction.client.user.id}"
            f"&scope=bot&permissions={invite_perms.value}&guild_id={guild.id}"
        ),
    ))
    error_view.add_item(button_row)

    if followup or interaction.response.is_done():
        await interaction.followup.send(view=error_view, ephemeral=True)
    else:
        await interaction.response.send_message(view=error_view, ephemeral=True)
    return False
