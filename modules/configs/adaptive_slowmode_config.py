"""
Configuration UI for the Adaptive Slowmode module.
Two views:
  - AdaptiveSlowmodeConfigView       : main list of monitored channels
  - AdaptiveSlowmodeChannelConfigView : add / edit a single channel entry
"""

import copy
import re
import discord
from discord import ui
from typing import Any, Callable, Dict, Optional
import logging

from utils.i18n import i18n, t
from cogs.error_handler import BaseView, BaseModal
from utils.emojis import TIME, REQUIRED_FIELDS, EDIT, BACK, SAVE, UNDONE, DELETE, ADD
from modules.configs._common import check_guild_perms

logger = logging.getLogger('moddy.modules.adaptive_slowmode_config')

_SENSITIVITY_KEYS = ("low", "medium", "high")

_CID_CHANNEL_LIST_ADD = "moddy:aslow:list:add"
_CID_CHANNEL_LIST_BACK = "moddy:aslow:list:back"
_CID_CHANNEL_LIST_SAVE = "moddy:aslow:list:save"
_CID_CHANNEL_LIST_CANCEL = "moddy:aslow:list:cancel"
_CID_CHANNEL_LIST_DELETE = "moddy:aslow:list:delete"

# Per-channel row buttons (edit/remove) — one pair per configured channel,
# so the target channel_id must be encoded (it is not otherwise on the
# interaction). \d{1,20} rather than a tighter \d{17,20}: a bare shell
# built with channel=0 must still match its own template (see
# docs/PERSISTENT_VIEWS.md Migration log, Step 6).
_CID_LIST_ITEM_TEMPLATE = r"moddy:aslow:listitem:(?P<action>edit|remove):(?P<channel>\d{1,20})"


def _guarded(callback):
    """Route DynamicItem callback errors to the central error handler.

    A DynamicItem dispatched via ``bot.add_dynamic_items`` has no live
    ``BaseView``, so ``BaseView.on_error`` never fires. Copied from
    ``utils/appeal_views.py``.
    """
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as e:  # noqa: BLE001 — funnel everything to the handler
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, e, self.__class__.__name__)
    return wrapper


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

    Persistent: NO (deliberate exclusion). This is a scratch editor for one
    channel's settings, held only in memory until its Save button folds the
    result into the parent list view's working_config — which is itself an
    unsaved draft until *that* view's own Save is clicked. Losing an
    in-progress channel edit on a restart is the same accepted loss as
    CaseCreationView (docs/PERSISTENT_VIEWS.md Appendix B.1.c): there is
    nothing in the DB yet to recover, so the user reopens Add/Edit and
    re-enters it. See Migration log, Step 11.

    Does NOT take a parent_view reference (Appendix B.1.b: a live view
    object cannot be encoded anywhere and cannot survive a restart even
    within the same live session's Back/Save flow if that parent instance
    happened to be a shared shell). Instead Back/Save are handed the
    parent's working_config as a plain dict and construct a *fresh*
    AdaptiveSlowmodeConfigView to return to, exactly as Appendix B.1.b
    proposes.
    """

    def __init__(
        self,
        bot=None,
        guild_id: Optional[int] = None,
        user_id: Optional[int] = None,
        locale: str = "en-US",
        working_config: Optional[Dict[str, Any]] = None,
        has_existing_config: bool = False,
        channel_id:     Optional[int]       = None,
        channel_config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(timeout=300)  # deliberately non-persistent, see docstring
        self.bot         = bot
        self.guild_id    = guild_id
        self.user_id     = user_id
        self.locale      = locale
        # Plain dict, NOT a view reference — the in-progress channel list
        # this edit will be folded back into on Back/Save.
        self.working_config = working_config if working_config is not None else {"channels": {}}
        self.has_existing_config = has_existing_config

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
            if self.channel_id and self.bot is not None:
                ch = self.bot.get_channel(self.channel_id)
                if ch:
                    ch_select.default_values = [ch]
            ch_select.callback = self.on_channel_select
            ch_row.add_item(ch_select)
            container.add_item(ch_row)
        else:
            guild   = self.bot.get_guild(self.guild_id) if self.bot is not None else None
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
        existing = self.working_config.get("channels", {})
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
        parent = AdaptiveSlowmodeConfigView(
            self.bot, self.guild_id, self.user_id, self.locale,
        )
        parent.working_config = self.working_config
        parent.has_changes = self.working_config != parent.current_config
        parent._build_view()
        await interaction.response.edit_message(view=parent)

    async def on_save(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        if self.channel_id is None:
            await interaction.response.send_message(
                t("modules.adaptive_slowmode.config.channel_settings.channel.placeholder", locale=self.locale),
                ephemeral=True,
            )
            return

        self.working_config.setdefault("channels", {})[str(self.channel_id)] = dict(self.config)
        parent = AdaptiveSlowmodeConfigView(
            self.bot, self.guild_id, self.user_id, self.locale,
        )
        parent.working_config = self.working_config
        parent.has_changes = True
        parent.has_existing_config = self.has_existing_config
        parent._build_view()
        await interaction.response.edit_message(view=parent)

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
    """Main configuration panel showing the list of monitored channels.

    Persistent: yes. Auth: Manage Server in the guild (checked on every
    click via check_guild_perms — NOT via a stored user_id, which cannot
    survive a restarted shell). Per-channel edit/remove buttons are
    DynamicItems keyed on channel_id (Appendix B.2: not otherwise on the
    interaction); the always-present add/back/save/cancel/delete buttons
    use static namespaced custom_ids like every other guild config panel.
    """

    __persistent__ = True

    def __init__(
        self,
        bot=None,
        guild_id: Optional[int] = None,
        user_id: Optional[int] = None,
        locale: str = "en-US",
        current_config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__()  # timeout=None
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
            guild = self.bot.get_guild(self.guild_id) if self.bot is not None else None
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
                row.add_item(SlowmodeListButton("edit", ch_id, locale=self.locale))
                row.add_item(SlowmodeListButton("remove", ch_id, locale=self.locale))
                container.add_item(row)

        # ── Add button ────────────────────────────────────────────────
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        add_row = ui.ActionRow()
        add_btn = ui.Button(
            label=t("modules.adaptive_slowmode.config.channel_list.add_button", locale=self.locale),
            style=discord.ButtonStyle.primary,
            emoji=discord.PartialEmoji.from_str(ADD),
            custom_id=_CID_CHANNEL_LIST_ADD,
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
            custom_id=_CID_CHANNEL_LIST_BACK,
            disabled=self.has_changes,
        )
        back_btn.callback = self.on_back
        btn_row.add_item(back_btn)

        # Registration shell (self.bot is None): register EVERY button's
        # custom_id regardless of has_changes/has_existing_config.
        is_shell = self.bot is None

        if self.has_changes or is_shell:
            save_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(SAVE),
                label=t("modules.config.buttons.save", locale=self.locale),
                style=discord.ButtonStyle.success,
                custom_id=_CID_CHANNEL_LIST_SAVE,
            )
            save_btn.callback = self.on_save
            btn_row.add_item(save_btn)

            cancel_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(UNDONE),
                label=t("modules.config.buttons.cancel", locale=self.locale),
                style=discord.ButtonStyle.danger,
                custom_id=_CID_CHANNEL_LIST_CANCEL,
            )
            cancel_btn.callback = self.on_cancel
            btn_row.add_item(cancel_btn)
        if (not self.has_changes and self.has_existing_config) or is_shell:
            delete_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(DELETE),
                label=t("modules.config.buttons.delete", locale=self.locale),
                style=discord.ButtonStyle.danger,
                custom_id=_CID_CHANNEL_LIST_DELETE,
            )
            delete_btn.callback = self.on_delete
            btn_row.add_item(delete_btn)

        self.add_item(btn_row)

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _is_live_for(self, interaction: discord.Interaction) -> bool:
        return self.bot is not None and self.guild_id == interaction.guild_id

    async def _fresh_working_config(self, interaction: discord.Interaction) -> Dict[str, Any]:
        if self._is_live_for(interaction):
            return copy.deepcopy(self.working_config)
        bot = interaction.client
        saved = await bot.module_manager.get_module_config(interaction.guild_id, "adaptive_slowmode")
        if saved and saved.get("channels"):
            return {"channels": copy.deepcopy(saved["channels"])}
        return {"channels": {}}

    async def _rebuild(self, interaction: discord.Interaction, working_config: Dict[str, Any],
                        has_changes: bool) -> "AdaptiveSlowmodeConfigView":
        """Always construct a NEW view instance rather than mutate/resend
        self — self may be the single shared shell serving every guild after
        a restart."""
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        saved = await bot.module_manager.get_module_config(interaction.guild_id, "adaptive_slowmode")
        view = AdaptiveSlowmodeConfigView(bot, interaction.guild_id, interaction.user.id, locale, current_config=saved)
        view.working_config = working_config
        view.has_changes = has_changes
        view._build_view()
        return view

    # ------------------------------------------------------------------
    # Main callbacks
    # ------------------------------------------------------------------

    async def on_add_channel(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        working_config = await self._fresh_working_config(interaction)
        locale = i18n.get_user_locale(interaction)
        channel_view = AdaptiveSlowmodeChannelConfigView(
            bot=interaction.client,
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            locale=locale,
            working_config=working_config,
            has_existing_config=self.has_existing_config if self._is_live_for(interaction) else bool(working_config.get("channels")),
        )
        await interaction.response.edit_message(view=channel_view)

    async def on_back(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        from cogs.config import ConfigMainView
        locale = i18n.get_user_locale(interaction)
        await interaction.response.edit_message(
            view=ConfigMainView(interaction.client, interaction.guild_id, interaction.user.id, locale)
        )

    async def on_save(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await interaction.response.defer()

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        working_config = await self._fresh_working_config(interaction)

        success, error_msg = await bot.module_manager.save_module_config(
            interaction.guild_id, "adaptive_slowmode", working_config
        )

        if success:
            view = AdaptiveSlowmodeConfigView(bot, interaction.guild_id, interaction.user.id, locale,
                                               current_config=working_config)
            await interaction.followup.send(t("modules.config.save.success", locale=locale), ephemeral=True)
            await interaction.edit_original_response(view=view)
        else:
            await interaction.followup.send(
                t("modules.config.save.error", locale=locale, error=error_msg), ephemeral=True,
            )

    async def on_cancel(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        saved = await bot.module_manager.get_module_config(interaction.guild_id, "adaptive_slowmode")
        view = AdaptiveSlowmodeConfigView(bot, interaction.guild_id, interaction.user.id, locale, current_config=saved)
        await interaction.response.edit_message(view=view)

    async def on_delete(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await interaction.response.defer()

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        success = await bot.module_manager.delete_module_config(interaction.guild_id, "adaptive_slowmode")

        if success:
            view = AdaptiveSlowmodeConfigView(bot, interaction.guild_id, interaction.user.id, locale, current_config=None)
            await interaction.followup.send(t("modules.config.delete.success", locale=locale), ephemeral=True)
            await interaction.edit_original_response(view=view)
        else:
            await interaction.followup.send(t("modules.config.delete.error", locale=locale), ephemeral=True)

    # ------------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_guild_perms(interaction)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click).
        Also registers the per-channel edit/remove DynamicItem."""
        bot.add_view(cls())
        bot.add_dynamic_items(SlowmodeListButton)


class SlowmodeListButton(ui.DynamicItem[ui.Button], template=_CID_LIST_ITEM_TEMPLATE):
    """Edit/Remove button on one channel row of the monitored-channels list.

    Not owner-scoped (guild permission model, like the rest of this view) —
    the channel_id is encoded because it is the one piece of per-row state
    that isn't already on the interaction (Appendix B.2).
    """

    _STYLE = {"edit": discord.ButtonStyle.secondary, "remove": discord.ButtonStyle.danger}
    _EMOJI = {"edit": EDIT, "remove": DELETE}
    _LABEL_KEY = {
        "edit": "modules.adaptive_slowmode.config.channel_list.edit_button",
        "remove": "modules.adaptive_slowmode.config.channel_list.remove_button",
    }

    def __init__(self, action: str, channel_id: int, *, locale: str = "en-US"):
        super().__init__(
            ui.Button(
                label=t(self._LABEL_KEY[action], locale=locale)[:80],
                style=self._STYLE[action],
                emoji=discord.PartialEmoji.from_str(self._EMOJI[action]),
                custom_id=f"moddy:aslow:listitem:{action}:{channel_id}",
            )
        )
        self.action = action
        self.channel_id = channel_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match: "re.Match"):
        return cls(match["action"], int(match["channel"]), locale=i18n.get_user_locale(interaction))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        saved = await bot.module_manager.get_module_config(interaction.guild_id, "adaptive_slowmode")
        working_config = {"channels": copy.deepcopy(saved["channels"])} if saved and saved.get("channels") else {"channels": {}}
        has_existing_config = bool(working_config.get("channels"))

        if self.action == "edit":
            ch_cfg = working_config.get("channels", {}).get(str(self.channel_id))
            if ch_cfg is None:
                # Channel was removed from another session since this button
                # was rendered — nothing to edit anymore.
                return
            channel_view = AdaptiveSlowmodeChannelConfigView(
                bot=bot, guild_id=interaction.guild_id, user_id=interaction.user.id, locale=locale,
                working_config=working_config, has_existing_config=has_existing_config,
                channel_id=self.channel_id, channel_config=dict(ch_cfg),
            )
            await interaction.response.edit_message(view=channel_view)
            return

        # remove
        working_config.get("channels", {}).pop(str(self.channel_id), None)
        view = AdaptiveSlowmodeConfigView(bot, interaction.guild_id, interaction.user.id, locale,
                                           current_config=saved)
        view.working_config = working_config
        view.has_changes = True
        view._build_view()
        await interaction.response.edit_message(view=view)
