"""
Configuration UI for the Automod module (Components V2).

Single panel where a server manager can:
  - enable / disable the whole module
  - enable / disable each detection feature (today: "content" — insults)
  - write the server rules (AI-validated against prompt injection before saving)
  - pick a log channel
  - exempt roles / channels
  - toggle the moderator exemption

The server rules are embedded verbatim into the moderation engine's system
prompt, so they are run past the AI safety check (``automod.rules_check``) the
moment they are edited — not at save time — to avoid an API call on every save.
"""

import copy
import logging
from typing import Any, Dict, Optional

import discord
from discord import ui

from utils.i18n import t
from cogs.error_handler import BaseView, BaseModal
from utils.emojis import (
    FILTER, BOOK, EDIT, BACK, SAVE, UNDONE, DELETE,
    TOGGLE_ON, TOGGLE_OFF, MESSAGE, LEGAL,
)
from automod.rules_check import validate_rules, MAX_RULES_LENGTH

logger = logging.getLogger("moddy.modules.automod_config")

_DEFAULT_CONFIG = {
    "enabled": False,
    "rules": "",
    "log_channel_id": None,
    "ignore_moderators": True,
    "features": {
        "content": {"enabled": False, "exempt_roles": [], "exempt_channels": []},
    },
}


def _deep_default(current: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cfg = copy.deepcopy(_DEFAULT_CONFIG)
    if not current:
        return cfg
    cfg["enabled"] = bool(current.get("enabled", False))
    cfg["rules"] = str(current.get("rules", "") or "")
    cfg["log_channel_id"] = current.get("log_channel_id")
    cfg["ignore_moderators"] = bool(current.get("ignore_moderators", True))
    features = current.get("features", {}) or {}
    content = features.get("content", {}) or {}
    cfg["features"]["content"] = {
        "enabled": bool(content.get("enabled", False)),
        "exempt_roles": list(content.get("exempt_roles", [])),
        "exempt_channels": list(content.get("exempt_channels", [])),
    }
    return cfg


# ---------------------------------------------------------------------------
# Rules modal
# ---------------------------------------------------------------------------

class RulesModal(BaseModal, title="Règlement du serveur"):
    """Edit the server rules. AI-validated on submit before being stored."""

    def __init__(self, bot, view: "AutomodConfigView", locale: str, current: str):
        super().__init__(timeout=600)
        self.bot = bot
        self.view = view
        self.locale = locale

        self.rules_input = ui.TextInput(
            label=t("modules.automod.config.rules.modal_label", locale=locale)[:45],
            placeholder=t("modules.automod.config.rules.modal_placeholder", locale=locale)[:100],
            default=current[:MAX_RULES_LENGTH] if current else None,
            style=discord.TextStyle.paragraph,
            max_length=MAX_RULES_LENGTH,
            required=False,
        )
        self.add_item(self.rules_input)

    async def on_submit(self, interaction: discord.Interaction):
        text = (self.rules_input.value or "").strip()

        # Empty rules are allowed (clears them) without an AI call.
        if not text:
            self.view.working_config["rules"] = ""
            self.view.has_changes = True
            self.view._build_view()
            await interaction.response.edit_message(view=self.view)
            return

        # Run the AI safety check (may take >1s) → defer first.
        await interaction.response.defer(ephemeral=True, thinking=True)
        safe, reason = await validate_rules(self.bot, self.view.guild_id, text)

        if not safe:
            if reason == "too_long":
                msg = t("modules.automod.config.rules.error_too_long", locale=self.locale)
            elif reason == "unavailable":
                msg = t("modules.automod.config.rules.error_unavailable", locale=self.locale)
            else:
                msg = t("modules.automod.config.rules.error_unsafe", locale=self.locale,
                        reason=reason or "—")
            await interaction.followup.send(msg, ephemeral=True)
            return

        self.view.working_config["rules"] = text
        self.view.has_changes = True
        self.view._build_view()
        try:
            if self.view._panel_message is not None:
                await self.view._panel_message.edit(view=self.view)
        except discord.HTTPException:
            pass
        await interaction.followup.send(
            t("modules.automod.config.rules.saved_pending", locale=self.locale),
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Main config view
# ---------------------------------------------------------------------------

class AutomodConfigView(BaseView):
    """Single-panel configuration for the Automod module."""

    def __init__(self, bot, guild_id: int, user_id: int, locale: str,
                 current_config: Optional[Dict[str, Any]] = None):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale

        self.has_existing_config = bool(current_config)
        self.current_config = _deep_default(current_config)
        self.working_config = copy.deepcopy(self.current_config)
        self.has_changes = False
        self._panel_message: Optional[discord.Message] = None

        self._build_view()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def _content(self) -> Dict[str, Any]:
        return self.working_config["features"]["content"]

    def _toggle_emoji(self, value: bool) -> str:
        return TOGGLE_ON if value else TOGGLE_OFF

    def _state_label(self, value: bool) -> str:
        key = "modules.automod.config.state.on" if value else "modules.automod.config.state.off"
        return t(key, locale=self.locale)

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build_view(self):
        self.clear_items()
        container = ui.Container()

        # Header
        container.add_item(ui.TextDisplay(
            f"### {FILTER} {t('modules.automod.config.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t("modules.automod.config.description", locale=self.locale)
        ))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        module_on = self.working_config["enabled"]
        content_on = self._content["enabled"]
        ignore_mods = self.working_config["ignore_moderators"]

        # Status block
        container.add_item(ui.TextDisplay(
            f"{self._toggle_emoji(module_on)} **{t('modules.automod.config.module_state', locale=self.locale)}** "
            f"· {self._state_label(module_on)}\n"
            f"{self._toggle_emoji(content_on)} **{t('modules.automod.config.feature_content', locale=self.locale)}** "
            f"· {self._state_label(content_on)}\n"
            f"-# {t('modules.automod.config.feature_content_desc', locale=self.locale)}\n"
            f"{self._toggle_emoji(ignore_mods)} **{t('modules.automod.config.ignore_mods', locale=self.locale)}** "
            f"· {self._state_label(ignore_mods)}"
        ))

        # Toggle + rules buttons row
        toggles = ui.ActionRow()

        module_btn = ui.Button(
            label=t("modules.automod.config.buttons.toggle_module", locale=self.locale),
            style=discord.ButtonStyle.success if module_on else discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(self._toggle_emoji(module_on)),
        )
        module_btn.callback = self._toggle_module
        toggles.add_item(module_btn)

        content_btn = ui.Button(
            label=t("modules.automod.config.buttons.toggle_content", locale=self.locale),
            style=discord.ButtonStyle.success if content_on else discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(self._toggle_emoji(content_on)),
        )
        content_btn.callback = self._toggle_content
        toggles.add_item(content_btn)

        ignore_btn = ui.Button(
            label=t("modules.automod.config.buttons.toggle_ignore_mods", locale=self.locale),
            style=discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(self._toggle_emoji(ignore_mods)),
        )
        ignore_btn.callback = self._toggle_ignore_mods
        toggles.add_item(ignore_btn)

        rules_btn = ui.Button(
            label=t("modules.automod.config.buttons.edit_rules", locale=self.locale),
            style=discord.ButtonStyle.primary,
            emoji=discord.PartialEmoji.from_str(BOOK),
        )
        rules_btn.callback = self._edit_rules
        toggles.add_item(rules_btn)

        container.add_item(toggles)

        # Rules preview
        rules = self.working_config.get("rules", "")
        if rules:
            preview = rules[:200] + ("…" if len(rules) > 200 else "")
            container.add_item(ui.TextDisplay(
                f"{BOOK} **{t('modules.automod.config.rules.section_title', locale=self.locale)}**\n"
                f"-# {preview}"
            ))
        else:
            container.add_item(ui.TextDisplay(
                f"{BOOK} **{t('modules.automod.config.rules.section_title', locale=self.locale)}**\n"
                f"-# {t('modules.automod.config.rules.empty', locale=self.locale)}"
            ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Log channel
        container.add_item(ui.TextDisplay(
            f"{MESSAGE} **{t('modules.automod.config.log_channel.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.automod.config.log_channel.section_description', locale=self.locale)}"
        ))
        guild = self.bot.get_guild(self.guild_id)
        log_row = ui.ActionRow()
        log_select = ui.ChannelSelect(
            placeholder=t("modules.automod.config.log_channel.placeholder", locale=self.locale),
            channel_types=[discord.ChannelType.text],
            min_values=0,
            max_values=1,
        )
        log_id = self.working_config.get("log_channel_id")
        if log_id and guild:
            ch = guild.get_channel(int(log_id))
            if ch:
                log_select.default_values = [ch]
        log_select.callback = self._on_log_channel
        log_row.add_item(log_select)
        container.add_item(log_row)

        # Exempt roles
        container.add_item(ui.TextDisplay(
            f"**{t('modules.automod.config.exempt_roles.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.automod.config.exempt_roles.section_description', locale=self.locale)}"
        ))
        roles_row = ui.ActionRow()
        roles_select = ui.RoleSelect(
            placeholder=t("modules.automod.config.exempt_roles.placeholder", locale=self.locale),
            min_values=0,
            max_values=25,
        )
        if guild:
            defaults = [guild.get_role(rid) for rid in self._content.get("exempt_roles", [])]
            roles_select.default_values = [r for r in defaults if r]
        roles_select.callback = self._on_exempt_roles
        roles_row.add_item(roles_select)
        container.add_item(roles_row)

        # Exempt channels
        container.add_item(ui.TextDisplay(
            f"**{t('modules.automod.config.exempt_channels.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.automod.config.exempt_channels.section_description', locale=self.locale)}"
        ))
        chan_row = ui.ActionRow()
        chan_select = ui.ChannelSelect(
            placeholder=t("modules.automod.config.exempt_channels.placeholder", locale=self.locale),
            channel_types=[discord.ChannelType.text, discord.ChannelType.public_thread,
                           discord.ChannelType.private_thread, discord.ChannelType.news],
            min_values=0,
            max_values=25,
        )
        if guild:
            defaults = [guild.get_channel(cid) for cid in self._content.get("exempt_channels", [])]
            chan_select.default_values = [c for c in defaults if c]
        chan_select.callback = self._on_exempt_channels
        chan_row.add_item(chan_select)
        container.add_item(chan_row)

        self.add_item(container)

        # Bottom actions
        self._add_action_buttons()

    def _add_action_buttons(self):
        row = ui.ActionRow()

        back_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(BACK),
            label=t("modules.config.buttons.back", locale=self.locale),
            style=discord.ButtonStyle.secondary,
            disabled=self.has_changes,
        )
        back_btn.callback = self.on_back
        row.add_item(back_btn)

        if self.has_changes:
            save_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(SAVE),
                label=t("modules.config.buttons.save", locale=self.locale),
                style=discord.ButtonStyle.success,
            )
            save_btn.callback = self.on_save
            row.add_item(save_btn)

            cancel_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(UNDONE),
                label=t("modules.config.buttons.cancel", locale=self.locale),
                style=discord.ButtonStyle.danger,
            )
            cancel_btn.callback = self.on_cancel
            row.add_item(cancel_btn)
        elif self.has_existing_config:
            delete_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(DELETE),
                label=t("modules.config.buttons.delete", locale=self.locale),
                style=discord.ButtonStyle.danger,
            )
            delete_btn.callback = self.on_delete
            row.add_item(delete_btn)

        self.add_item(row)

    # ------------------------------------------------------------------
    # Toggle callbacks
    # ------------------------------------------------------------------

    async def _toggle_module(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        self.working_config["enabled"] = not self.working_config["enabled"]
        self.has_changes = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def _toggle_content(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        self._content["enabled"] = not self._content["enabled"]
        self.has_changes = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def _toggle_ignore_mods(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        self.working_config["ignore_moderators"] = not self.working_config["ignore_moderators"]
        self.has_changes = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def _edit_rules(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        # Capture the panel message so the modal can refresh it after the AI check.
        self._panel_message = interaction.message
        modal = RulesModal(self.bot, self, self.locale, self.working_config.get("rules", ""))
        await interaction.response.send_modal(modal)

    # ------------------------------------------------------------------
    # Select callbacks
    # ------------------------------------------------------------------

    async def _on_log_channel(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        values = interaction.data.get("values", [])
        self.working_config["log_channel_id"] = int(values[0]) if values else None
        self.has_changes = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def _on_exempt_roles(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        values = interaction.data.get("values", [])
        self._content["exempt_roles"] = [int(v) for v in values]
        self.has_changes = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def _on_exempt_channels(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        values = interaction.data.get("values", [])
        self._content["exempt_channels"] = [int(v) for v in values]
        self.has_changes = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    # ------------------------------------------------------------------
    # Bottom actions
    # ------------------------------------------------------------------

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
            self.guild_id, "automod", self.working_config
        )
        if success:
            self.current_config = copy.deepcopy(self.working_config)
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
        self.working_config = copy.deepcopy(self.current_config)
        self.has_changes = False
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_delete(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        await interaction.response.defer()

        success = await self.bot.module_manager.delete_module_config(self.guild_id, "automod")
        if success:
            self.current_config = _deep_default(None)
            self.working_config = copy.deepcopy(self.current_config)
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
