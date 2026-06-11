"""
Configuration UI for the Adaptive Slowmode module.
Allows admins to select monitored channels, set min/max delays and sensitivity.
"""

import discord
from discord import ui
from typing import Any, Callable, Dict, Optional
import logging

from utils.i18n import t
from cogs.error_handler import BaseView, BaseModal
from utils.emojis import TIME, REQUIRED_FIELDS, EDIT, BACK, SAVE, UNDONE, DELETE

logger = logging.getLogger('moddy.modules.adaptive_slowmode_config')


class DelayModal(BaseModal, title="Délai (secondes)"):
    """Generic modal for editing a numeric slowmode delay value."""

    def __init__(self, locale: str, label: str, placeholder: str,
                 current_value: int, callback: Callable):
        super().__init__(timeout=300)
        self.locale = locale
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


class AdaptiveSlowmodeConfigView(BaseView):
    """Configuration panel for the Adaptive Slowmode module."""

    def __init__(
        self,
        bot,
        guild_id: int,
        user_id: int,
        locale: str,
        current_config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale

        from modules.adaptive_slowmode import AdaptiveSlowmodeModule
        default_config = AdaptiveSlowmodeModule(bot, guild_id).get_default_config()

        if current_config and current_config.get("channel_ids"):
            self.current_config = {**default_config, **current_config}
            self.has_existing_config = True
        else:
            self.current_config = default_config
            self.has_existing_config = False

        self.working_config: Dict[str, Any] = dict(self.current_config)
        self.has_changes = False

        self._build_view()

    # -------------------------------------------------------------------------
    # View construction
    # -------------------------------------------------------------------------

    def _build_view(self):
        self.clear_items()

        container = ui.Container()

        # ── Header ────────────────────────────────────────────────────────────
        container.add_item(ui.TextDisplay(
            f"### {TIME} {t('modules.adaptive_slowmode.config.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t("modules.adaptive_slowmode.config.description", locale=self.locale)
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # ── Channels (required) ───────────────────────────────────────────────
        container.add_item(ui.TextDisplay(
            f"**{t('modules.adaptive_slowmode.config.channels.section_title', locale=self.locale)}"
            f"**{REQUIRED_FIELDS}\n"
            f"-# {t('modules.adaptive_slowmode.config.channels.section_description', locale=self.locale)}"
        ))

        channel_row = ui.ActionRow()
        channel_select = ui.ChannelSelect(
            placeholder=t("modules.adaptive_slowmode.config.channels.placeholder", locale=self.locale),
            channel_types=[discord.ChannelType.text],
            min_values=0,
            max_values=25,
        )
        if self.working_config.get("channel_ids"):
            defaults = [
                self.bot.get_channel(ch_id)
                for ch_id in self.working_config["channel_ids"]
            ]
            defaults = [ch for ch in defaults if ch is not None]
            if defaults:
                channel_select.default_values = defaults
        channel_select.callback = self.on_channels_select
        channel_row.add_item(channel_select)
        container.add_item(channel_row)

        # ── Minimum delay ─────────────────────────────────────────────────────
        current_min = self.working_config.get("min_delay", 0)
        container.add_item(ui.TextDisplay(
            f"**{t('modules.adaptive_slowmode.config.min_delay.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.adaptive_slowmode.config.min_delay.section_description', locale=self.locale)}\n"
            f"-# {t('modules.config.current_value', locale=self.locale)} **{current_min}s**"
        ))

        min_row = ui.ActionRow()
        edit_min_btn = ui.Button(
            label=t("modules.adaptive_slowmode.config.min_delay.edit_button", locale=self.locale),
            style=discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(EDIT),
        )
        edit_min_btn.callback = self.on_edit_min_delay
        min_row.add_item(edit_min_btn)
        container.add_item(min_row)

        # ── Maximum delay ─────────────────────────────────────────────────────
        current_max = self.working_config.get("max_delay", 120)
        container.add_item(ui.TextDisplay(
            f"**{t('modules.adaptive_slowmode.config.max_delay.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.adaptive_slowmode.config.max_delay.section_description', locale=self.locale)}\n"
            f"-# {t('modules.config.current_value', locale=self.locale)} **{current_max}s**"
        ))

        max_row = ui.ActionRow()
        edit_max_btn = ui.Button(
            label=t("modules.adaptive_slowmode.config.max_delay.edit_button", locale=self.locale),
            style=discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(EDIT),
        )
        edit_max_btn.callback = self.on_edit_max_delay
        max_row.add_item(edit_max_btn)
        container.add_item(max_row)

        # ── Sensitivity ───────────────────────────────────────────────────────
        container.add_item(ui.TextDisplay(
            f"**{t('modules.adaptive_slowmode.config.sensitivity.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.adaptive_slowmode.config.sensitivity.section_description', locale=self.locale)}"
        ))

        sensitivity_row = ui.ActionRow()
        current_sensitivity = self.working_config.get("sensitivity", "medium")
        sensitivity_select = ui.Select(
            placeholder=t("modules.adaptive_slowmode.config.sensitivity.placeholder", locale=self.locale),
            options=[
                discord.SelectOption(
                    label=t("modules.adaptive_slowmode.config.sensitivity.low.label", locale=self.locale),
                    value="low",
                    description=t("modules.adaptive_slowmode.config.sensitivity.low.description", locale=self.locale),
                    default=(current_sensitivity == "low"),
                ),
                discord.SelectOption(
                    label=t("modules.adaptive_slowmode.config.sensitivity.medium.label", locale=self.locale),
                    value="medium",
                    description=t("modules.adaptive_slowmode.config.sensitivity.medium.description", locale=self.locale),
                    default=(current_sensitivity == "medium"),
                ),
                discord.SelectOption(
                    label=t("modules.adaptive_slowmode.config.sensitivity.high.label", locale=self.locale),
                    value="high",
                    description=t("modules.adaptive_slowmode.config.sensitivity.high.description", locale=self.locale),
                    default=(current_sensitivity == "high"),
                ),
            ],
            min_values=1,
            max_values=1,
        )
        sensitivity_select.callback = self.on_sensitivity_select
        sensitivity_row.add_item(sensitivity_select)
        container.add_item(sensitivity_row)

        self.add_item(container)
        self._add_action_buttons()

    def _add_action_buttons(self):
        button_row = ui.ActionRow()

        back_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(BACK),
            label=t("modules.config.buttons.back", locale=self.locale),
            style=discord.ButtonStyle.secondary,
            disabled=self.has_changes,
        )
        back_btn.callback = self.on_back
        button_row.add_item(back_btn)

        if self.has_changes:
            save_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(SAVE),
                label=t("modules.config.buttons.save", locale=self.locale),
                style=discord.ButtonStyle.success,
            )
            save_btn.callback = self.on_save
            button_row.add_item(save_btn)

            cancel_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(UNDONE),
                label=t("modules.config.buttons.cancel", locale=self.locale),
                style=discord.ButtonStyle.danger,
            )
            cancel_btn.callback = self.on_cancel
            button_row.add_item(cancel_btn)
        elif self.has_existing_config:
            delete_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(DELETE),
                label=t("modules.config.buttons.delete", locale=self.locale),
                style=discord.ButtonStyle.danger,
            )
            delete_btn.callback = self.on_delete
            button_row.add_item(delete_btn)

        self.add_item(button_row)

    # -------------------------------------------------------------------------
    # Select / button callbacks
    # -------------------------------------------------------------------------

    async def on_channels_select(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        self.working_config["channel_ids"] = [
            int(v) for v in interaction.data.get("values", [])
        ]
        self.has_changes = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_edit_min_delay(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        modal = DelayModal(
            locale=self.locale,
            label=t("modules.adaptive_slowmode.config.delay_modal.min_label", locale=self.locale),
            placeholder="0",
            current_value=self.working_config.get("min_delay", 0),
            callback=self._on_min_delay_set,
        )
        modal.bot = self.bot
        await interaction.response.send_modal(modal)

    async def _on_min_delay_set(self, interaction: discord.Interaction, value: int):
        current_max = self.working_config.get("max_delay", 120)
        if value >= current_max:
            await interaction.response.send_message(
                t("modules.adaptive_slowmode.config.delay_modal.error_min_gte_max", locale=self.locale),
                ephemeral=True,
            )
            return
        self.working_config["min_delay"] = value
        self.has_changes = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_edit_max_delay(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        modal = DelayModal(
            locale=self.locale,
            label=t("modules.adaptive_slowmode.config.delay_modal.max_label", locale=self.locale),
            placeholder="120",
            current_value=self.working_config.get("max_delay", 120),
            callback=self._on_max_delay_set,
        )
        modal.bot = self.bot
        await interaction.response.send_modal(modal)

    async def _on_max_delay_set(self, interaction: discord.Interaction, value: int):
        current_min = self.working_config.get("min_delay", 0)
        if value <= current_min:
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
        self.working_config["max_delay"] = value
        self.has_changes = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_sensitivity_select(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        self.working_config["sensitivity"] = interaction.data["values"][0]
        self.has_changes = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    # -------------------------------------------------------------------------
    # Action button callbacks
    # -------------------------------------------------------------------------

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
            self.current_config = dict(self.working_config)
            self.has_changes = False
            self.has_existing_config = True
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
        self.working_config = dict(self.current_config)
        self.has_changes = False
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_delete(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        await interaction.response.defer()

        success = await self.bot.module_manager.delete_module_config(
            self.guild_id, "adaptive_slowmode"
        )

        if success:
            from modules.adaptive_slowmode import AdaptiveSlowmodeModule
            self.current_config = AdaptiveSlowmodeModule(self.bot, self.guild_id).get_default_config()
            self.working_config = dict(self.current_config)
            self.has_changes = False
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

    # -------------------------------------------------------------------------
    # User guard
    # -------------------------------------------------------------------------

    async def check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                t("modules.config.errors.wrong_user", locale=self.locale), ephemeral=True
            )
            return False
        return True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.check_user(interaction)
