"""
Configuration UI for the AI Detector module.

The module runs everywhere by default (``DEFAULT_ENABLED``), so this screen is
about narrowing it down, never about switching it on:

- which verdicts Moddy announces (AI-generated, partially AI, authenticated
  photos, unsigned generation metadata) — a multi-select whose ticked options
  are the current setting;
- channels where Moddy never answers.

Pausing the whole module is the generic state, in the Manage panel (the button
next to Back), like every other module.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import discord
from discord import ui

from cogs.error_handler import BaseView
from modules.ai_detector import CATEGORIES, MAX_IGNORED_CHANNELS, normalize_config
from modules.configs._common import (
    ack,
    check_guild_perms,
    config_state,
    crumb,
    module_manage_button,
    open_main_config,
    show,
    state_accent,
)
from utils.emojis import AI_SHIELD, BACK, SAVE, UNDONE
from utils.i18n import i18n, t

logger = logging.getLogger("moddy.modules.ai_detector_config")

_CID_CATEGORIES = "moddy:ai_detector:config:categories"
_CID_CHANNELS = "moddy:ai_detector:config:channels"
_CID_BACK = "moddy:ai_detector:config:back"
_CID_SAVE = "moddy:ai_detector:config:save"
_CID_CANCEL = "moddy:ai_detector:config:cancel"

_MODULE_ID = "ai_detector"


class AiDetectorConfigView(BaseView):
    """AI Detector configuration panel.

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

        # No stored config = the defaults, active: the module is opt-out.
        self.current_config = normalize_config(current_config)
        self.working_config = dict(self.current_config)
        self.has_changes = False

        self._build_view()

    # ----------------------------------------------------------------- #
    # Rendering
    # ----------------------------------------------------------------- #

    def _tc(self, key: str, **kwargs) -> str:
        return t(f"modules.ai_detector.config.{key}", locale=self.locale, **kwargs)

    def _build_view(self):
        self.clear_items()

        container = ui.Container(accent_colour=state_accent(config_state(self.current_config)))
        container.add_item(crumb(self.locale, _MODULE_ID))
        container.add_item(ui.TextDisplay(f"### {AI_SHIELD} {self._tc('title')}"))
        container.add_item(ui.TextDisplay(self._tc("description")))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # --- Verdicts announced --------------------------------------- #
        container.add_item(ui.TextDisplay(
            f"**{self._tc('categories.section_title')}**\n"
            f"-# {self._tc('categories.section_description')}"
        ))
        enabled = self.working_config.get("categories") or []
        select = ui.Select(
            placeholder=self._tc("categories.placeholder"),
            options=[
                discord.SelectOption(
                    label=self._tc(f"categories.{value}.label")[:100],
                    description=self._tc(f"categories.{value}.description")[:100],
                    value=value,
                    default=value in enabled,
                )
                for value in CATEGORIES
            ],
            min_values=0,
            max_values=len(CATEGORIES),
            custom_id=_CID_CATEGORIES,
        )
        select.callback = self.on_categories_select
        container.add_item(ui.ActionRow(select))

        # --- Ignored channels ----------------------------------------- #
        container.add_item(ui.TextDisplay(
            f"**{self._tc('channels.section_title')}**\n"
            f"-# {self._tc('channels.section_description')}"
        ))
        channel_select = ui.ChannelSelect(
            placeholder=self._tc("channels.placeholder"),
            channel_types=[discord.ChannelType.text, discord.ChannelType.news,
                           discord.ChannelType.forum, discord.ChannelType.voice,
                           discord.ChannelType.public_thread, discord.ChannelType.private_thread],
            min_values=0,
            max_values=MAX_IGNORED_CHANNELS,
            custom_id=_CID_CHANNELS,
        )
        ignored = self.working_config.get("ignored_channel_ids") or []
        if ignored and self.bot is not None:
            defaults = [c for c in (self.bot.get_channel(int(i)) for i in ignored) if c is not None]
            if defaults:
                channel_select.default_values = defaults
        channel_select.callback = self.on_channels_select
        container.add_item(ui.ActionRow(channel_select))

        self.add_item(container)
        self._add_action_buttons()

    def _add_action_buttons(self):
        row = ui.ActionRow()

        back_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(BACK),
            label=t("modules.config.buttons.back", locale=self.locale),
            style=discord.ButtonStyle.secondary,
            custom_id=_CID_BACK,
            disabled=self.has_changes,
        )
        back_btn.callback = self.on_back
        row.add_item(back_btn)

        # Registration shell (self.bot is None): render every conditional
        # button so discord.py learns each custom_id — see
        # docs/guides/PERSISTENT_VIEWS.md, gotcha 2.
        if self.has_changes or self.bot is None:
            save_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(SAVE),
                label=t("modules.config.buttons.save", locale=self.locale),
                style=discord.ButtonStyle.success,
                custom_id=_CID_SAVE,
            )
            save_btn.callback = self.on_save
            row.add_item(save_btn)

            cancel_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(UNDONE),
                label=t("modules.config.buttons.cancel", locale=self.locale),
                style=discord.ButtonStyle.danger,
                custom_id=_CID_CANCEL,
            )
            cancel_btn.callback = self.on_cancel
            row.add_item(cancel_btn)

        # Always has a state (default-enabled), so Manage is always offered.
        if not self.has_changes:
            row.add_item(module_manage_button(_MODULE_ID, self.locale))

        self.add_item(row)

    # ----------------------------------------------------------------- #
    # Persistence helpers
    # ----------------------------------------------------------------- #

    def _is_live_for(self, interaction: discord.Interaction) -> bool:
        return self.bot is not None and self.guild_id == interaction.guild_id

    async def _fresh_working_config(self, interaction: discord.Interaction) -> Dict[str, Any]:
        """Working copy to mutate — from memory when live, from the DB otherwise."""
        if self._is_live_for(interaction):
            return dict(self.working_config)
        saved = await interaction.client.module_manager.get_module_config(
            interaction.guild_id, _MODULE_ID)
        return normalize_config(saved)

    async def _rebuild(self, interaction: discord.Interaction,
                       working_config: Dict[str, Any]) -> "AiDetectorConfigView":
        """Always build a NEW instance: `self` may be the shared shell."""
        bot = interaction.client
        saved = await bot.module_manager.get_module_config(interaction.guild_id, _MODULE_ID)
        view = AiDetectorConfigView(bot, interaction.guild_id, interaction.user.id,
                                    i18n.get_user_locale(interaction), current_config=saved)
        view.working_config = working_config
        view.has_changes = True
        view._build_view()
        return view

    # ----------------------------------------------------------------- #
    # Callbacks
    # ----------------------------------------------------------------- #

    async def on_categories_select(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await ack(interaction)
        values: List[str] = interaction.data.get("values") or []
        working_config = await self._fresh_working_config(interaction)
        working_config["categories"] = [c for c in CATEGORIES if c in values]
        await show(interaction, await self._rebuild(interaction, working_config))

    async def on_channels_select(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await ack(interaction)
        values: List[str] = interaction.data.get("values") or []
        working_config = await self._fresh_working_config(interaction)
        working_config["ignored_channel_ids"] = [int(v) for v in values if str(v).isdigit()]
        await show(interaction, await self._rebuild(interaction, working_config))

    async def on_back(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await open_main_config(interaction)

    async def on_save(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await ack(interaction)

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        working_config = await self._fresh_working_config(interaction)
        success, error_msg = await bot.module_manager.save_module_config(
            interaction.guild_id, _MODULE_ID, working_config, actor_id=interaction.user.id,
        )
        if success:
            saved = await bot.module_manager.get_module_config(interaction.guild_id, _MODULE_ID)
            view = AiDetectorConfigView(bot, interaction.guild_id, interaction.user.id,
                                        locale, current_config=saved)
            await interaction.followup.send(t("modules.config.save.success", locale=locale),
                                            ephemeral=True)
            await interaction.edit_original_response(view=view)
        else:
            await interaction.followup.send(
                t("modules.config.save.error", locale=locale, error=error_msg), ephemeral=True)

    async def on_cancel(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await ack(interaction)
        bot = interaction.client
        saved = await bot.module_manager.get_module_config(interaction.guild_id, _MODULE_ID)
        view = AiDetectorConfigView(bot, interaction.guild_id, interaction.user.id,
                                    i18n.get_user_locale(interaction), current_config=saved)
        await show(interaction, view)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_guild_perms(interaction)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_view(cls())
