"""
Configuration UI for the Adaptive Slowmode module.
Two views:
  - AdaptiveSlowmodeConfigView       : main list of monitored channels
  - AdaptiveSlowmodeChannelConfigView : add / edit a single channel entry
"""

import copy
import discord
from discord import ui
from typing import Any, Callable, Dict, Optional
import logging

from utils.i18n import t
from cogs.error_handler import BaseView, BaseModal
from utils.emojis import TIME, REQUIRED_FIELDS, EDIT, BACK, SAVE, UNDONE, DELETE, ADD, REMOVE

logger = logging.getLogger('moddy.modules.adaptive_slowmode_config')

_SENSITIVITY_KEYS = ("low", "medium", "high")


# ---------------------------------------------------------------------------
# Shared modal for numeric delay input
# ---------------------------------------------------------------------------

class DelayModal(BaseModal, title="Délai (secondes)"):
    def __init__(self, locale: str, label: str, placeholder: str,
                 current_value: int, callback: Callable):
        super().__init__(timeout=300)
        self.locale   = locale
        self._callback = callback

        self.delay_input = ui.TextInput(
            label=label,
            placeholder=placeholder,
            default=str(current_value),
            style=discord.TextStyle.short,
            max_length=5,
            required=True,
        )
        self.add_item(self.delay_input)

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.delay_input.value.strip()
        if not raw.isdigit():
            await interaction.response.send_message(
                t("modules.adaptive_slowmode.config.delay_modal.error_invalid", locale=self.locale),
                ephemeral=True,
            )
            return
        value = int(raw)
        if not (0 <= value <= 21600):
            await interaction.response.send_message(
                t("modules.adaptive_slowmode.config.delay_modal.error_range", locale=self.locale),
                ephemeral=True,
            )
            return
        await self._callback(interaction, value)


# ---------------------------------------------------------------------------
# Per-channel add / edit view
# ---------------------------------------------------------------------------

class AdaptiveSlowmodeChannelConfigView(BaseView):
    """
    Add or edit the settings for a single monitored channel.

    add  mode: channel_id=None  — user must select a channel via the select
    edit mode: channel_id=int   — channel is fixed, only settings change
    """

    def __init__(
        self,
        bot,
        guild_id: int,
        user_id:  int,
        locale:   str,
        parent_view: "AdaptiveSlowmodeConfigView",
        channel_id:     Optional[int]       = None,
        channel_config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(timeout=300)
        self.bot         = bot
        self.guild_id    = guild_id
        self.user_id     = user_id
        self.locale      = locale
        self.parent_view = parent_view

        self.edit_mode  = channel_id is not None
        self.channel_id = channel_id   # set in add mode once channel is chosen

        self.config: Dict[str, Any] = dict(channel_config) if channel_config else {
            "min_delay":   0,
            "max_delay":   120,
            "sensitivity": "medium",
        }

        self._build_view()

    # ------------------------------------------------------------------

    def _build_view(self):
        self.clear_items()
        container = ui.Container()

        # ── Header ────────────────────────────────────────────────────
        title_key = (
            "modules.adaptive_slowmode.config.channel_settings.title_edit"
            if self.edit_mode else
            "modules.adaptive_slowmode.config.channel_settings.title_add"
        )
        container.add_item(ui.TextDisplay(
            f"### {TIME} {t(title_key, locale=self.locale)}"
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # ── Channel selector (add mode only) ──────────────────────────
        if not self.edit_mode:
            container.add_item(ui.TextDisplay(
                f"**{t('modules.adaptive_slowmode.config.channel_settings.channel.section_title', locale=self.locale)}"
                f"**{REQUIRED_FIELDS}\n"
                f"-# {t('modules.adaptive_slowmode.config.channel_settings.channel.section_description', locale=self.locale)}"
            ))

            ch_row = ui.ActionRow()
            ch_select = ui.ChannelSelect(
                placeholder=t("modules.adaptive_slowmode.config.channel_settings.channel.placeholder", locale=self.locale),
                channel_types=[discord.ChannelType.text],
                min_values=0,
                max_values=1,
            )
            if self.channel_id:
                ch = self.bot.get_channel(self.channel_id)
                if ch:
                    ch_select.default_values = [ch]
            ch_select.callback = self.on_channel_select
            ch_row.add_item(ch_select)
            container.add_item(ch_row)
        else:
            guild   = self.bot.get_guild(self.guild_id)
            channel = guild.get_channel(self.channel_id) if guild else None
            mention = channel.mention if channel else f"`{self.channel_id}`"
            container.add_item(ui.TextDisplay(
                f"**{t('modules.adaptive_slowmode.config.channel_settings.channel.section_title', locale=self.locale)}**\n"
                f"-# {mention}"
            ))

        # ── Min delay ─────────────────────────────────────────────────
        current_min = self.config.get("min_delay", 0)
        container.add_item(ui.TextDisplay(
            f"**{t('modules.adaptive_slowmode.config.channel_settings.min_delay.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.adaptive_slowmode.config.channel_settings.min_delay.section_description', locale=self.locale)}\n"
            f"-# {t('modules.config.current_value', locale=self.locale)} **{current_min}s**"
        ))
        min_row = ui.ActionRow()
        min_btn = ui.Button(
            label=t("modules.adaptive_slowmode.config.channel_settings.min_delay.edit_button", locale=self.locale),
            style=discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(EDIT),
        )
        min_btn.callback = self.on_edit_min_delay
        min_row.add_item(min_btn)
        container.add_item(min_row)

        # ── Max delay ─────────────────────────────────────────────────
        current_max = self.config.get("max_delay", 120)
        container.add_item(ui.TextDisplay(
            f"**{t('modules.adaptive_slowmode.config.channel_settings.max_delay.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.adaptive_slowmode.config.channel_settings.max_delay.section_description', locale=self.locale)}\n"
            f"-# {t('modules.config.current_value', locale=self.locale)} **{current_max}s**"
        ))
        max_row = ui.ActionRow()
        max_btn = ui.Button(
            label=t("modules.adaptive_slowmode.config.channel_settings.max_delay.edit_button", locale=self.locale),
            style=discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(EDIT),
        )
        max_btn.callback = self.on_edit_max_delay
        max_row.add_item(max_btn)
        container.add_item(max_row)

        # ── Sensitivity ───────────────────────────────────────────────
        container.add_item(ui.TextDisplay(
            f"**{t('modules.adaptive_slowmode.config.channel_settings.sensitivity.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.adaptive_slowmode.config.channel_settings.sensitivity.section_description', locale=self.locale)}"
        ))
        current_sens = self.config.get("sensitivity", "medium")
        sens_row = ui.ActionRow()
        sens_select = ui.Select(
            placeholder=t("modules.adaptive_slowmode.config.channel_settings.sensitivity.placeholder", locale=self.locale),
            options=[
                discord.SelectOption(
                    label=t(f"modules.adaptive_slowmode.config.channel_settings.sensitivity.{k}.label", locale=self.locale),
                    value=k,
                    description=t(f"modules.adaptive_slowmode.config.channel_settings.sensitivity.{k}.description", locale=self.locale),
                    default=(current_sens == k),
                )
                for k in _SENSITIVITY_KEYS
            ],
            min_values=1,
            max_values=1,
        )
        sens_select.callback = self.on_sensitivity_select
        sens_row.add_item(sens_select)
        container.add_item(sens_row)

        self.add_item(container)

        # ── Action buttons ────────────────────────────────────────────
        btn_row = ui.ActionRow()

        back_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(BACK),
            label=t("modules.config.buttons.back", locale=self.locale),
            style=discord.ButtonStyle.secondary,
        )
        back_btn.callback = self.on_back
        btn_row.add_item(back_btn)

        save_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(SAVE),
            label=t("modules.config.buttons.save", locale=self.locale),
            style=discord.ButtonStyle.success,
            disabled=(not self.edit_mode and self.channel_id is None),
        )
        save_btn.callback = self.on_save
        btn_row.add_item(save_btn)

        self.add_item(btn_row)

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    async def on_channel_select(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return

        values = interaction.data.get("values", [])
        if not values:
            self.channel_id = None
            self._build_view()
            await interaction.response.edit_message(view=self)
            return

        selected_id = int(values[0])

        # Prevent selecting a channel that is already configured.
        existing = self.parent_view.working_config.get("channels", {})
        if str(selected_id) in existing and selected_id != self.channel_id:
            await interaction.response.send_message(
                t("modules.adaptive_slowmode.config.channel_settings.already_configured", locale=self.locale),
                ephemeral=True,
            )
            return

        self.channel_id = selected_id
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_edit_min_delay(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        modal = DelayModal(
            locale=self.locale,
            label=t("modules.adaptive_slowmode.config.delay_modal.min_label", locale=self.locale),
            placeholder="0",
            current_value=self.config.get("min_delay", 0),
            callback=self._on_min_delay_set,
        )
        modal.bot = self.bot
        await interaction.response.send_modal(modal)

    async def _on_min_delay_set(self, interaction: discord.Interaction, value: int):
        if value >= self.config.get("max_delay", 120):
            await interaction.response.send_message(
                t("modules.adaptive_slowmode.config.delay_modal.error_min_gte_max", locale=self.locale),
                ephemeral=True,
            )
            return
        self.config["min_delay"] = value
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_edit_max_delay(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        modal = DelayModal(
            locale=self.locale,
            label=t("modules.adaptive_slowmode.config.delay_modal.max_label", locale=self.locale),
            placeholder="120",
            current_value=self.config.get("max_delay", 120),
            callback=self._on_max_delay_set,
        )
        modal.bot = self.bot
        await interaction.response.send_modal(modal)

    async def _on_max_delay_set(self, interaction: discord.Interaction, value: int):
        if value <= self.config.get("min_delay", 0):
            await interaction.response.send_message(
                t("modules.adaptive_slowmode.config.delay_modal.error_max_lte_min", locale=self.locale),
                ephemeral=True,
            )
            return
        if value < 1:
            await interaction.response.send_message(
                t("modules.adaptive_slowmode.config.delay_modal.error_range", locale=self.locale),
                ephemeral=True,
            )
            return
        self.config["max_delay"] = value
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_sensitivity_select(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        self.config["sensitivity"] = interaction.data["values"][0]
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_back(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        self.parent_view._build_view()
        await interaction.response.edit_message(view=self.parent_view)

    async def on_save(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        if self.channel_id is None:
            await interaction.response.send_message(
                t("modules.adaptive_slowmode.config.channel_settings.channel.placeholder", locale=self.locale),
                ephemeral=True,
            )
            return

        self.parent_view.working_config.setdefault("channels", {})[str(self.channel_id)] = dict(self.config)
        self.parent_view.has_changes = True
        self.parent_view._build_view()
        await interaction.response.edit_message(view=self.parent_view)

    # ------------------------------------------------------------------

    async def check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                t("modules.config.errors.wrong_user", locale=self.locale), ephemeral=True
            )
            return False
        return True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.check_user(interaction)


# ---------------------------------------------------------------------------
# Main config view — list of configured channels
# ---------------------------------------------------------------------------

class AdaptiveSlowmodeConfigView(BaseView):
    """Main configuration panel showing the list of monitored channels."""

    def __init__(
        self,
        bot,
        guild_id: int,
        user_id:  int,
        locale:   str,
        current_config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(timeout=300)
        self.bot      = bot
        self.guild_id = guild_id
        self.user_id  = user_id
        self.locale   = locale

        if current_config and current_config.get("channels"):
            self.current_config    = {"channels": copy.deepcopy(current_config["channels"])}
            self.has_existing_config = True
        else:
            self.current_config    = {"channels": {}}
            self.has_existing_config = False

        self.working_config: Dict[str, Any] = {
            "channels": copy.deepcopy(self.current_config["channels"])
        }
        self.has_changes = False

        self._build_view()

    # ------------------------------------------------------------------

    def _build_view(self):
        self.clear_items()
        container = ui.Container()

        # ── Header ────────────────────────────────────────────────────
        container.add_item(ui.TextDisplay(
            f"### {TIME} {t('modules.adaptive_slowmode.config.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t("modules.adaptive_slowmode.config.description", locale=self.locale)
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # ── Channel list ──────────────────────────────────────────────
        channels = self.working_config.get("channels", {})

        container.add_item(ui.TextDisplay(
            f"**{t('modules.adaptive_slowmode.config.channel_list.section_title', locale=self.locale)}**"
        ))

        if not channels:
            container.add_item(ui.TextDisplay(
                f"-# {t('modules.adaptive_slowmode.config.channel_list.empty', locale=self.locale)}"
            ))
        else:
            guild = self.bot.get_guild(self.guild_id)
            for ch_id_str, ch_cfg in channels.items():
                ch_id   = int(ch_id_str)
                channel = guild.get_channel(ch_id) if guild else None
                mention = channel.mention if channel else f"`{ch_id}`"

                sensitivity_key = ch_cfg.get("sensitivity", "medium")
                sensitivity_label = t(
                    f"modules.adaptive_slowmode.config.channel_settings.sensitivity.{sensitivity_key}.label",
                    locale=self.locale,
                )

                container.add_item(ui.TextDisplay(
                    f"{mention}\n"
                    f"-# `{ch_cfg.get('min_delay', 0)}s` → `{ch_cfg.get('max_delay', 120)}s`"
                    f"  ·  {sensitivity_label}"
                ))

                row = ui.ActionRow()

                edit_btn = ui.Button(
                    label=t("modules.adaptive_slowmode.config.channel_list.edit_button", locale=self.locale),
                    style=discord.ButtonStyle.secondary,
                    emoji=discord.PartialEmoji.from_str(EDIT),
                )
                edit_btn.callback = self._make_edit_cb(ch_id, ch_cfg)
                row.add_item(edit_btn)

                remove_btn = ui.Button(
                    label=t("modules.adaptive_slowmode.config.channel_list.remove_button", locale=self.locale),
                    style=discord.ButtonStyle.danger,
                    emoji=discord.PartialEmoji.from_str(REMOVE),
                )
                remove_btn.callback = self._make_remove_cb(ch_id_str)
                row.add_item(remove_btn)

                container.add_item(row)

        # ── Add button ────────────────────────────────────────────────
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        add_row = ui.ActionRow()
        add_btn = ui.Button(
            label=t("modules.adaptive_slowmode.config.channel_list.add_button", locale=self.locale),
            style=discord.ButtonStyle.primary,
            emoji=discord.PartialEmoji.from_str(ADD),
        )
        add_btn.callback = self.on_add_channel
        add_row.add_item(add_btn)
        container.add_item(add_row)

        self.add_item(container)

        # ── Bottom action buttons ─────────────────────────────────────
        self._add_action_buttons()

    def _add_action_buttons(self):
        btn_row = ui.ActionRow()

        back_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(BACK),
            label=t("modules.config.buttons.back", locale=self.locale),
            style=discord.ButtonStyle.secondary,
            disabled=self.has_changes,
        )
        back_btn.callback = self.on_back
        btn_row.add_item(back_btn)

        if self.has_changes:
            save_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(SAVE),
                label=t("modules.config.buttons.save", locale=self.locale),
                style=discord.ButtonStyle.success,
            )
            save_btn.callback = self.on_save
            btn_row.add_item(save_btn)

            cancel_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(UNDONE),
                label=t("modules.config.buttons.cancel", locale=self.locale),
                style=discord.ButtonStyle.danger,
            )
            cancel_btn.callback = self.on_cancel
            btn_row.add_item(cancel_btn)
        elif self.has_existing_config:
            delete_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(DELETE),
                label=t("modules.config.buttons.delete", locale=self.locale),
                style=discord.ButtonStyle.danger,
            )
            delete_btn.callback = self.on_delete
            btn_row.add_item(delete_btn)

        self.add_item(btn_row)

    # ------------------------------------------------------------------
    # Closure helpers for per-channel buttons
    # ------------------------------------------------------------------

    def _make_edit_cb(self, ch_id: int, ch_cfg: Dict[str, Any]):
        async def callback(interaction: discord.Interaction):
            if not await self.check_user(interaction):
                return
            channel_view = AdaptiveSlowmodeChannelConfigView(
                bot=self.bot,
                guild_id=self.guild_id,
                user_id=self.user_id,
                locale=self.locale,
                parent_view=self,
                channel_id=ch_id,
                channel_config=dict(ch_cfg),
            )
            await interaction.response.edit_message(view=channel_view)
        return callback

    def _make_remove_cb(self, ch_id_str: str):
        async def callback(interaction: discord.Interaction):
            if not await self.check_user(interaction):
                return
            self.working_config.get("channels", {}).pop(ch_id_str, None)
            self.has_changes = True
            self._build_view()
            await interaction.response.edit_message(view=self)
        return callback

    # ------------------------------------------------------------------
    # Main callbacks
    # ------------------------------------------------------------------

    async def on_add_channel(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        channel_view = AdaptiveSlowmodeChannelConfigView(
            bot=self.bot,
            guild_id=self.guild_id,
            user_id=self.user_id,
            locale=self.locale,
            parent_view=self,
        )
        await interaction.response.edit_message(view=channel_view)

    async def on_back(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        from cogs.config import ConfigMainView
        await interaction.response.edit_message(
            view=ConfigMainView(self.bot, self.guild_id, self.user_id, self.locale)
        )

    async def on_save(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        await interaction.response.defer()

        success, error_msg = await self.bot.module_manager.save_module_config(
            self.guild_id, "adaptive_slowmode", self.working_config
        )

        if success:
            self.current_config      = {"channels": copy.deepcopy(self.working_config["channels"])}
            self.has_changes         = False
            self.has_existing_config = bool(self.working_config.get("channels"))
            self._build_view()
            await interaction.followup.send(
                t("modules.config.save.success", locale=self.locale), ephemeral=True
            )
            await interaction.edit_original_response(view=self)
        else:
            await interaction.followup.send(
                t("modules.config.save.error", locale=self.locale, error=error_msg),
                ephemeral=True,
            )

    async def on_cancel(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        self.working_config = {"channels": copy.deepcopy(self.current_config["channels"])}
        self.has_changes    = False
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_delete(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        await interaction.response.defer()

        success = await self.bot.module_manager.delete_module_config(self.guild_id, "adaptive_slowmode")

        if success:
            self.current_config      = {"channels": {}}
            self.working_config      = {"channels": {}}
            self.has_changes         = False
            self.has_existing_config = False
            self._build_view()
            await interaction.followup.send(
                t("modules.config.delete.success", locale=self.locale), ephemeral=True
            )
            await interaction.edit_original_response(view=self)
        else:
            await interaction.followup.send(
                t("modules.config.delete.error", locale=self.locale), ephemeral=True
            )

    # ------------------------------------------------------------------

    async def check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                t("modules.config.errors.wrong_user", locale=self.locale), ephemeral=True
            )
            return False
        return True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.check_user(interaction)
