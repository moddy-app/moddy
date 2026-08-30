"""
Configuration UI for the Voice Transcription module.

Three settings, nothing more: on/off, how the transcription is offered
(button or automatic) and, optionally, which channels it applies to.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import discord
from discord import ui

from cogs.error_handler import BaseView
from modules.configs._common import check_guild_perms
from modules.voice_transcription import MAX_CHANNELS, MODE_BUTTON, MODES
from utils.emojis import (
    BACK, DELETE, SAVE, TOGGLE_OFF, TOGGLE_ON, UNDONE, VOICE_CHAT,
)
from utils.i18n import i18n, t
from utils.interaction_response import safe_defer

logger = logging.getLogger("moddy.modules.voice_transcription_config")

_CID_TOGGLE = "moddy:voice_transcription:config:toggle"
_CID_MODE = "moddy:voice_transcription:config:mode"
_CID_CHANNELS = "moddy:voice_transcription:config:channels"
_CID_BACK = "moddy:voice_transcription:config:back"
_CID_SAVE = "moddy:voice_transcription:config:save"
_CID_CANCEL = "moddy:voice_transcription:config:cancel"
_CID_DELETE = "moddy:voice_transcription:config:delete"

_MODULE_ID = "voice_transcription"


class VoiceTranscriptionConfigView(BaseView):
    """Voice Transcription configuration panel.

    Persistent: yes. Auth: Manage Server in the guild (re-checked on every
    click via check_guild_perms — never a stored user_id, which cannot
    survive a restarted shell).
    """

    __persistent__ = True

    def __init__(self, bot=None, guild_id: Optional[int] = None,
                 user_id: Optional[int] = None, locale: str = "en-US",
                 current_config: Optional[Dict[str, Any]] = None):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale

        from modules.voice_transcription import VoiceTranscriptionModule
        default_config = VoiceTranscriptionModule(bot, guild_id).get_default_config()

        # `mode` is always written on save — unlike `enabled`, it is a safe
        # marker for "this guild has a stored configuration".
        if current_config and current_config.get("mode") is not None:
            self.current_config = default_config.copy()
            self.current_config.update(current_config)
            self.has_existing_config = True
        else:
            self.current_config = default_config
            self.has_existing_config = False

        self.working_config = self.current_config.copy()
        self.has_changes = False

        self._build_view()

    # ----------------------------------------------------------------- #
    # Rendering
    # ----------------------------------------------------------------- #

    def _build_view(self):
        self.clear_items()

        container = ui.Container()

        container.add_item(ui.TextDisplay(
            f"### {VOICE_CHAT} {t('modules.voice_transcription.config.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t('modules.voice_transcription.config.description', locale=self.locale)
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # --- Status -------------------------------------------------- #
        enabled = bool(self.working_config.get("enabled"))
        status_key = "enabled" if enabled else "disabled"
        container.add_item(ui.TextDisplay(
            f"**{t('modules.voice_transcription.config.status.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.voice_transcription.config.status.section_description', locale=self.locale)}\n"
            f"-# {t('modules.config.current_value', locale=self.locale)} "
            f"**{t(f'modules.voice_transcription.config.status.{status_key}', locale=self.locale)}**"
        ))

        toggle_row = ui.ActionRow()
        toggle_btn = ui.Button(
            label=t(
                'modules.voice_transcription.config.status.disable' if enabled
                else 'modules.voice_transcription.config.status.enable',
                locale=self.locale,
            ),
            style=discord.ButtonStyle.secondary if enabled else discord.ButtonStyle.success,
            emoji=discord.PartialEmoji.from_str(TOGGLE_ON if enabled else TOGGLE_OFF),
            custom_id=_CID_TOGGLE,
        )
        toggle_btn.callback = self.on_toggle
        toggle_row.add_item(toggle_btn)
        container.add_item(toggle_row)

        # --- Mode ------------------------------------------------------ #
        container.add_item(ui.TextDisplay(
            f"**{t('modules.voice_transcription.config.mode.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.voice_transcription.config.mode.section_description', locale=self.locale)}"
        ))

        mode = self.working_config.get("mode") or MODE_BUTTON
        mode_row = ui.ActionRow()
        mode_select = ui.Select(
            placeholder=t('modules.voice_transcription.config.mode.placeholder', locale=self.locale),
            options=[
                discord.SelectOption(
                    label=t(f'modules.voice_transcription.config.mode.{value}.label', locale=self.locale),
                    description=t(f'modules.voice_transcription.config.mode.{value}.description',
                                  locale=self.locale)[:100],
                    value=value,
                    default=(value == mode),
                )
                for value in MODES
            ],
            min_values=1,
            max_values=1,
            custom_id=_CID_MODE,
        )
        mode_select.callback = self.on_mode_select
        mode_row.add_item(mode_select)
        container.add_item(mode_row)

        # --- Channels -------------------------------------------------- #
        channel_ids = self.working_config.get("channel_ids") or []
        scope_key = "all" if not channel_ids else "selected"
        container.add_item(ui.TextDisplay(
            f"**{t('modules.voice_transcription.config.channels.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.voice_transcription.config.channels.section_description', locale=self.locale)}\n"
            f"-# {t('modules.config.current_value', locale=self.locale)} "
            f"**{t(f'modules.voice_transcription.config.channels.{scope_key}', locale=self.locale, count=len(channel_ids))}**"
        ))

        channel_row = ui.ActionRow()
        channel_select = ui.ChannelSelect(
            placeholder=t('modules.voice_transcription.config.channels.placeholder', locale=self.locale),
            channel_types=[discord.ChannelType.text, discord.ChannelType.voice,
                           discord.ChannelType.public_thread, discord.ChannelType.private_thread],
            min_values=0,
            max_values=MAX_CHANNELS,
            custom_id=_CID_CHANNELS,
        )
        if channel_ids and self.bot is not None:
            defaults = []
            for channel_id in channel_ids:
                channel = self.bot.get_channel(int(channel_id))
                if channel is not None:
                    defaults.append(channel)
            if defaults:
                channel_select.default_values = defaults
        channel_select.callback = self.on_channels_select
        channel_row.add_item(channel_select)
        container.add_item(channel_row)

        self.add_item(container)
        self._add_action_buttons()

    def _add_action_buttons(self):
        button_row = ui.ActionRow()

        back_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(BACK),
            label=t('modules.config.buttons.back', locale=self.locale),
            style=discord.ButtonStyle.secondary,
            custom_id=_CID_BACK,
            disabled=self.has_changes,
        )
        back_btn.callback = self.on_back
        button_row.add_item(back_btn)

        # Registration shell (self.bot is None): render every conditional
        # button so discord.py learns each custom_id — see
        # docs/PERSISTENT_VIEWS.md, gotcha 2.
        is_shell = self.bot is None

        if self.has_changes or is_shell:
            save_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(SAVE),
                label=t('modules.config.buttons.save', locale=self.locale),
                style=discord.ButtonStyle.success,
                custom_id=_CID_SAVE,
            )
            save_btn.callback = self.on_save
            button_row.add_item(save_btn)

            cancel_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(UNDONE),
                label=t('modules.config.buttons.cancel', locale=self.locale),
                style=discord.ButtonStyle.danger,
                custom_id=_CID_CANCEL,
            )
            cancel_btn.callback = self.on_cancel
            button_row.add_item(cancel_btn)

        if (not self.has_changes and self.has_existing_config) or is_shell:
            delete_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(DELETE),
                label=t('modules.config.buttons.delete', locale=self.locale),
                style=discord.ButtonStyle.danger,
                custom_id=_CID_DELETE,
            )
            delete_btn.callback = self.on_delete
            button_row.add_item(delete_btn)

        self.add_item(button_row)

    # ----------------------------------------------------------------- #
    # Persistence helpers
    # ----------------------------------------------------------------- #

    def _is_live_for(self, interaction: discord.Interaction) -> bool:
        return self.bot is not None and self.guild_id == interaction.guild_id

    async def _fresh_working_config(self, interaction: discord.Interaction) -> Dict[str, Any]:
        """Working copy to mutate — from memory when live, from the DB otherwise."""
        if self._is_live_for(interaction):
            return self.working_config.copy()

        bot = interaction.client
        from modules.voice_transcription import VoiceTranscriptionModule
        config = VoiceTranscriptionModule(bot, interaction.guild_id).get_default_config()
        saved = await bot.module_manager.get_module_config(interaction.guild_id, _MODULE_ID)
        if saved and saved.get("mode") is not None:
            config.update(saved)
        return config

    async def _rebuild(self, interaction: discord.Interaction,
                       working_config: Dict[str, Any],
                       has_changes: bool) -> "VoiceTranscriptionConfigView":
        """Always build a NEW instance: `self` may be the shared shell."""
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        saved = await bot.module_manager.get_module_config(interaction.guild_id, _MODULE_ID)
        view = VoiceTranscriptionConfigView(
            bot, interaction.guild_id, interaction.user.id, locale, current_config=saved,
        )
        view.working_config = working_config
        view.has_changes = has_changes
        view._build_view()
        return view

    # ----------------------------------------------------------------- #
    # Callbacks
    # ----------------------------------------------------------------- #

    async def on_toggle(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return

        # The stored config is read below: acknowledge before the round-trip.
        await safe_defer(interaction, thinking=False)

        working_config = await self._fresh_working_config(interaction)
        working_config["enabled"] = not bool(working_config.get("enabled"))

        view = await self._rebuild(interaction, working_config, has_changes=True)
        await interaction.edit_original_response(view=view)

    async def on_mode_select(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return

        await safe_defer(interaction, thinking=False)

        values = interaction.data.get("values") or []
        mode = values[0] if values else MODE_BUTTON
        working_config = await self._fresh_working_config(interaction)
        working_config["mode"] = mode if mode in MODES else MODE_BUTTON

        view = await self._rebuild(interaction, working_config, has_changes=True)
        await interaction.edit_original_response(view=view)

    async def on_channels_select(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return

        await safe_defer(interaction, thinking=False)

        values: List[str] = interaction.data.get("values") or []
        working_config = await self._fresh_working_config(interaction)
        working_config["channel_ids"] = [int(v) for v in values]

        view = await self._rebuild(interaction, working_config, has_changes=True)
        await interaction.edit_original_response(view=view)

    # --- action buttons ------------------------------------------------ #

    async def on_back(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return

        from cogs.config import ConfigMainView
        locale = i18n.get_user_locale(interaction)
        main_view = ConfigMainView(
            interaction.client, interaction.guild_id, interaction.user.id, locale,
        )
        await interaction.response.edit_message(view=main_view)

    async def on_save(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return

        await interaction.response.defer()

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        working_config = await self._fresh_working_config(interaction)

        success, error_msg = await bot.module_manager.save_module_config(
            interaction.guild_id, _MODULE_ID, working_config,
            actor_id=interaction.user.id,
        )

        if success:
            view = VoiceTranscriptionConfigView(
                bot, interaction.guild_id, interaction.user.id, locale,
                current_config=working_config,
            )
            await interaction.followup.send(
                t('modules.config.save.success', locale=locale), ephemeral=True,
            )
            await interaction.edit_original_response(view=view)
        else:
            await interaction.followup.send(
                t('modules.config.save.error', locale=locale, error=error_msg),
                ephemeral=True,
            )

    async def on_cancel(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return

        await safe_defer(interaction, thinking=False)

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        saved = await bot.module_manager.get_module_config(interaction.guild_id, _MODULE_ID)
        view = VoiceTranscriptionConfigView(
            bot, interaction.guild_id, interaction.user.id, locale, current_config=saved,
        )
        await interaction.edit_original_response(view=view)

    async def on_delete(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return

        await interaction.response.defer()

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        success = await bot.module_manager.delete_module_config(interaction.guild_id, _MODULE_ID)

        if success:
            view = VoiceTranscriptionConfigView(
                bot, interaction.guild_id, interaction.user.id, locale, current_config=None,
            )
            await interaction.followup.send(
                t('modules.config.delete.success', locale=locale), ephemeral=True,
            )
            await interaction.edit_original_response(view=view)
        else:
            await interaction.followup.send(
                t('modules.config.delete.error', locale=locale), ephemeral=True,
            )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_guild_perms(interaction)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_view(cls())
