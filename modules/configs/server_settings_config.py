"""
Server settings — the settings that belong to the *server*, not to a module.

Today that is the **language** Moddy speaks in this server. It used to be a
dropdown inside AltGuard (panel language), inside Automod AI
(``langue_serveur``), inside the logs (``locale``) and on every ticket
category, so the same server could be greeted in English and warned in
French. There is now one setting here, read by every module through
``utils.guild_language``.

The panel itself is rendered in the *admin's* Discord language, like every
other ``/config`` screen — only the setting decides what members read.

Persistence note: the language select carries its own state (``default=True``
on the chosen option), so no "current value" line is printed under it
(CLAUDE.md rule #9). The automatic option is the one exception: what it
*resolves to* is invisible, so that resolved language is spelled out in the
section description.
"""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import ui

from cogs.error_handler import BaseView
from modules.configs._common import check_guild_perms
from utils.emojis import BACK, SETTINGS, TRANSLATE
from utils.guild_language import (
    AUTO,
    SUPPORTED_LOCALES,
    auto_locale,
    get_language_setting,
    set_language_setting,
)
from utils.i18n import i18n, t

logger = logging.getLogger('moddy.modules.server_settings_config')

_CID_LANGUAGE = "moddy:config:settings:language"
_CID_BACK = "moddy:config:settings:back"


class ServerSettingsConfigView(BaseView):
    """``/config`` → *Server settings*.

    Persistent: yes. Auth: Manage Server in the guild, re-checked on every
    click via check_guild_perms (never a stored user_id — a restarted shell
    has none). Every callback re-reads the setting from the interaction's
    guild, so the shared shell answers correctly for any server.
    """

    __persistent__ = True

    def __init__(self, bot=None, guild_id: Optional[int] = None,
                 user_id: Optional[int] = None, locale: str = "en-US",
                 language_setting: str = AUTO):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale
        self.language_setting = language_setting or AUTO

        self._build_view()

    @classmethod
    async def create(cls, bot, guild_id: int, user_id: int,
                     locale: str) -> "ServerSettingsConfigView":
        return cls(bot, guild_id, user_id, locale,
                   await get_language_setting(bot, guild_id))

    # ----------------------------------------------------------------- #
    # Rendering
    # ----------------------------------------------------------------- #

    def _guild(self) -> Optional[discord.Guild]:
        if self.bot is None or not self.guild_id:
            return None
        try:
            return self.bot.get_guild(self.guild_id)
        except Exception:
            return None

    def _build_view(self) -> None:
        self.clear_items()

        container = ui.Container()

        container.add_item(ui.TextDisplay(
            f"### {SETTINGS} {t('modules.config.settings.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t('modules.config.settings.description', locale=self.locale)
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # --- Language ---------------------------------------------------- #
        section = (
            f"**{TRANSLATE} {t('modules.config.settings.language.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.config.settings.language.section_description', locale=self.locale)}"
        )
        if self.language_setting == AUTO:
            # The only value the control cannot show: what "automatic" *is*
            # right now (CLAUDE.md rule #9).
            resolved = t(f'languages.{auto_locale(self._guild())}', locale=self.locale)
            section += (
                f"\n-# {t('modules.config.settings.language.auto_resolves_to', locale=self.locale, language=resolved)}"
            )
        container.add_item(ui.TextDisplay(section))

        options = [
            discord.SelectOption(
                label=t('modules.config.settings.language.auto', locale=self.locale),
                value=AUTO,
                description=t('modules.config.settings.language.auto_description',
                              locale=self.locale)[:100],
                default=(self.language_setting == AUTO),
            )
        ]
        options += [
            discord.SelectOption(
                label=t(f'languages.{code}', locale=self.locale),
                value=code,
                default=(self.language_setting == code),
            )
            for code in SUPPORTED_LOCALES
        ]

        language_row = ui.ActionRow()
        language_select = ui.Select(
            placeholder=t('modules.config.settings.language.placeholder', locale=self.locale),
            options=options,
            min_values=1, max_values=1,
            custom_id=_CID_LANGUAGE,
        )
        language_select.callback = self.on_language_select
        language_row.add_item(language_select)
        container.add_item(language_row)

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(
            f"-# {t('modules.config.settings.language.applies_to', locale=self.locale)}"
        ))

        self.add_item(container)

        button_row = ui.ActionRow()
        back_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(BACK),
            label=t('modules.config.buttons.back', locale=self.locale),
            style=discord.ButtonStyle.secondary,
            custom_id=_CID_BACK,
        )
        back_btn.callback = self.on_back
        button_row.add_item(back_btn)
        self.add_item(button_row)

    # ----------------------------------------------------------------- #
    # Callbacks
    # ----------------------------------------------------------------- #

    async def on_language_select(self, interaction: discord.Interaction):
        """Store the picked language. A single setting saves on the spot —
        there is nothing to stage, and no half-saved state to explain."""
        if not await check_guild_perms(interaction):
            return

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        value = (interaction.data.get('values') or [AUTO])[0]

        try:
            stored = await set_language_setting(bot, interaction.guild_id, value)
        except Exception as e:
            logger.error(f"Could not save the language of guild {interaction.guild_id}: {e}",
                         exc_info=True)
            await interaction.response.send_message(
                t('modules.config.save.error', locale=locale, error=str(e)), ephemeral=True)
            return

        view = ServerSettingsConfigView(bot, interaction.guild_id, interaction.user.id,
                                        locale, stored)
        await interaction.response.edit_message(view=view)
        await interaction.followup.send(
            t('modules.config.save.success', locale=locale), ephemeral=True)

        # Panels are messages already written in the previous language: only a
        # re-post brings them in line (AltGuard's gate, the ticket panels).
        manager = getattr(bot, "module_manager", None)
        if manager is not None:
            try:
                await manager.apply_language_change(interaction.guild_id)
            except Exception as e:
                logger.error(
                    f"Could not refresh the panels of guild {interaction.guild_id} "
                    f"after a language change: {e}", exc_info=True)

    async def on_back(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return

        from cogs.config import ConfigMainView
        locale = i18n.get_user_locale(interaction)
        main_view = ConfigMainView(interaction.client, interaction.guild_id,
                                   interaction.user.id, locale)
        await interaction.response.edit_message(view=main_view)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_guild_perms(interaction)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_view(cls())
