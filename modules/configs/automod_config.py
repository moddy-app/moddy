"""
Configuration UI for the Automod module (`/config`).

This panel follows the **standard module pattern** (like the other
`modules/configs/*` panels): a working copy of the config is edited in memory
and only written to the DB when the user presses **Save**. **Cancel** discards
the pending edits, **Delete** removes the stored config, and **Back** returns to
the module list (disabled while there are unsaved changes).

Two things matter for correctness:

* The **alert channel is mandatory** — automod does not run without it, so the
  panel makes it the first required field and warns until it is set.
* The **indications** (ex-"règlement") are AI-validated against prompt injection
  (`automod.rules_check`) when edited, because they are embedded verbatim into
  the moderation engine's system prompt.
"""

import copy
import logging
from typing import Any, Dict, Optional

import discord
from discord import ui

from utils.i18n import t, i18n
from cogs.error_handler import BaseView, BaseModal
from utils.emojis import (
    SHIELD, BOOK, BACK, DELETE, MESSAGE, GROUPS, SETTINGS, WARNING, SAVE,
    UNDONE, REQUIRED_FIELDS, MANAGE_USER, GREEN_STATUS, RED_STATUS, TOGGLE_ON,
    TOGGLE_OFF,
)
from automod.rules_check import validate_rules, MAX_RULES_LENGTH
from automod import constants as ac

logger = logging.getLogger("moddy.modules.automod_config")

_DEFAULT_CONFIG = {
    "enabled": False,
    "indications": "",
    "notify_channel_id": None,
    "ignore_moderators": True,
    "severity": ac.SEVERITY_DEFAULT,
    "features": {
        "content": {"enabled": False, "exempt_roles": [], "exempt_channels": []},
    },
}


def _deep_default(current: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return a fully-populated config, merging stored values over the defaults."""
    cfg = copy.deepcopy(_DEFAULT_CONFIG)
    if not current:
        return cfg
    cfg["enabled"] = bool(current.get("enabled", False))
    # Read legacy keys ("rules", "log_channel_id") transparently.
    cfg["indications"] = str(current.get("indications", current.get("rules", "")) or "")
    cfg["notify_channel_id"] = current.get("notify_channel_id", current.get("log_channel_id"))
    cfg["ignore_moderators"] = bool(current.get("ignore_moderators", True))
    cfg["severity"] = ac.clamp_severity(current.get("severity", ac.SEVERITY_DEFAULT))
    content = (current.get("features", {}) or {}).get("content", {}) or {}
    cfg["features"]["content"] = {
        "enabled": bool(content.get("enabled", False)),
        "exempt_roles": list(content.get("exempt_roles", [])),
        "exempt_channels": list(content.get("exempt_channels", [])),
    }
    return cfg


# --------------------------------------------------------------------------- #
# Indications modal (AI-validated). Writes into the parent's working copy.
# --------------------------------------------------------------------------- #
class IndicationsModal(BaseModal):
    def __init__(self, parent: "AutomodConfigView", current: str):
        locale = parent.locale
        super().__init__(title=t("modules.automod.config.indications.modal_title", locale=locale)[:45])
        self.bot = parent.bot
        self.parent_view = parent
        self.locale = locale
        self.field = ui.Label(
            text=t("modules.automod.config.indications.modal_label", locale=locale)[:45],
            description=t("modules.automod.config.indications.modal_desc", locale=locale)[:100],
            component=ui.TextInput(
                placeholder=t("modules.automod.config.indications.modal_placeholder", locale=locale)[:100],
                default=current[:MAX_RULES_LENGTH] if current else None,
                style=discord.TextStyle.paragraph,
                max_length=MAX_RULES_LENGTH,
                required=False,
            ),
        )
        self.add_item(self.field)

    async def on_submit(self, interaction: discord.Interaction):
        locale = self.locale
        text = (self.field.component.value or "").strip()
        parent = self.parent_view

        if not text:  # clearing needs no AI call
            parent.working_config["indications"] = ""
            parent.has_changes = True
            parent._build_view()
            await interaction.response.edit_message(view=parent)
            return

        await interaction.response.defer()
        safe, reason = await validate_rules(self.bot, parent.guild_id, text)
        if not safe:
            if reason == "too_long":
                msg = t("modules.automod.config.indications.error_too_long", locale=locale)
            elif reason == "unavailable":
                msg = t("modules.automod.config.indications.error_unavailable", locale=locale)
            else:
                msg = t("modules.automod.config.indications.error_unsafe", locale=locale, reason=reason or "—")
            await interaction.followup.send(msg, ephemeral=True)
            return

        parent.working_config["indications"] = text
        parent.has_changes = True
        parent._build_view()
        await interaction.edit_original_response(view=parent)
        await interaction.followup.send(
            t("modules.automod.config.indications.checked", locale=locale), ephemeral=True
        )


# --------------------------------------------------------------------------- #
# Main config view
# --------------------------------------------------------------------------- #
class AutomodConfigView(BaseView):
    """Automod configuration panel (standard Save/Cancel pattern)."""

    def __init__(self, bot, guild_id: int, user_id: int, locale: str = "en-US",
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

        self._build_view()

    # -- helpers --------------------------------------------------------- #
    @property
    def _content(self) -> Dict[str, Any]:
        return self.working_config["features"]["content"]

    def _dot(self, value: bool) -> str:
        return GREEN_STATUS if value else RED_STATUS

    def _state(self, value: bool) -> str:
        key = "modules.automod.config.state.on" if value else "modules.automod.config.state.off"
        return t(key, locale=self.locale)

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                t("modules.config.errors.wrong_user", locale=i18n.get_user_locale(interaction)),
                ephemeral=True,
            )
            return False
        return True

    # -- build ----------------------------------------------------------- #
    def _build_view(self):
        self.clear_items()
        container = ui.Container()

        cfg = self.working_config
        module_on = cfg["enabled"]
        content_on = self._content["enabled"]
        ignore_on = cfg["ignore_moderators"]
        has_channel = cfg.get("notify_channel_id") is not None
        running = module_on and content_on and has_channel

        # ── Header + one-line status ──────────────────────────────────────
        container.add_item(ui.TextDisplay(
            f"### {SHIELD} {t('modules.automod.config.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t("modules.automod.config.description", locale=self.locale)
        ))
        if running:
            container.add_item(ui.TextDisplay(
                t("modules.automod.config.summary_active", locale=self.locale)
            ))
        else:
            hint_key = ("summary_need_channel" if not has_channel
                        else "summary_need_module" if not module_on
                        else "summary_need_content")
            container.add_item(ui.TextDisplay(
                f"{t('modules.automod.config.summary_inactive', locale=self.locale)}\n"
                f"-# {t(f'modules.automod.config.{hint_key}', locale=self.locale)}"
            ))

        # ── Module on/off — a single toggle button (exception to selects) ──
        toggle_row = ui.ActionRow()
        toggle_btn = ui.Button(
            label=t(
                "modules.automod.config.buttons."
                + ("disable_module" if module_on else "enable_module"),
                locale=self.locale,
            ),
            style=discord.ButtonStyle.danger if module_on else discord.ButtonStyle.success,
            emoji=discord.PartialEmoji.from_str(TOGGLE_ON if module_on else TOGGLE_OFF),
        )
        toggle_btn.callback = self.on_toggle_module
        toggle_row.add_item(toggle_btn)
        container.add_item(toggle_row)

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # ── Options (the remaining toggles, picked in one select) ─────────
        container.add_item(ui.TextDisplay(
            f"{SETTINGS} **{t('modules.automod.config.section_options', locale=self.locale)}**"
        ))
        opt_row = ui.ActionRow()
        opt_select = ui.Select(
            placeholder=t("modules.automod.config.activations.placeholder", locale=self.locale),
            min_values=0, max_values=2,
            options=[
                discord.SelectOption(
                    label=t("modules.automod.config.content_label", locale=self.locale),
                    value="content",
                    description=t("modules.automod.config.content_desc", locale=self.locale)[:100],
                    emoji=discord.PartialEmoji.from_str(MESSAGE),
                    default=content_on,
                ),
                discord.SelectOption(
                    label=t("modules.automod.config.ignore_mods.label", locale=self.locale),
                    value="ignore",
                    description=t("modules.automod.config.ignore_mods.desc", locale=self.locale)[:100],
                    emoji=discord.PartialEmoji.from_str(MANAGE_USER),
                    default=ignore_on,
                ),
            ],
        )
        opt_select.callback = self.on_activations
        opt_row.add_item(opt_select)
        container.add_item(opt_row)

        # ── Alert channel (REQUIRED) ──────────────────────────────────────
        warn_line = ("" if has_channel
                     else f"\n-# {WARNING} {t('modules.automod.config.notify.missing', locale=self.locale)}")
        container.add_item(ui.TextDisplay(
            f"{MESSAGE} **{t('modules.automod.config.section_notify', locale=self.locale)}**{REQUIRED_FIELDS}\n"
            f"-# {t('modules.automod.config.notify.desc', locale=self.locale)}{warn_line}"
        ))
        notify_row = ui.ActionRow()
        notify_select = ui.ChannelSelect(
            placeholder=t("modules.automod.config.notify.placeholder", locale=self.locale),
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=0, max_values=1,
        )
        self._apply_channel_defaults(notify_select, [cfg.get("notify_channel_id")])
        notify_select.callback = self.on_notify_channel
        notify_row.add_item(notify_select)
        container.add_item(notify_row)

        # ── Severity (the select shows the current level) ─────────────────
        severity = cfg.get("severity", ac.SEVERITY_DEFAULT)
        container.add_item(ui.TextDisplay(
            f"{SETTINGS} **{t('modules.automod.config.section_severity', locale=self.locale)}**\n"
            f"-# {t('modules.automod.config.severity.desc', locale=self.locale)}"
        ))
        sev_row = ui.ActionRow()
        sev_select = ui.Select(
            placeholder=t("modules.automod.config.severity.placeholder", locale=self.locale),
            min_values=1, max_values=1,
            options=[
                discord.SelectOption(
                    label=t("modules.automod.config.severity.option", locale=self.locale, n=n),
                    value=str(n),
                    description=t(f"modules.automod.config.severity.level_{n}", locale=self.locale)[:100],
                    default=(n == severity),
                )
                for n in range(ac.SEVERITY_MIN, ac.SEVERITY_MAX + 1)
            ],
        )
        sev_select.callback = self.on_severity
        sev_row.add_item(sev_select)
        container.add_item(sev_row)

        # ── Guidance (button+modal → keep a preview, can't show it inline) ─
        indications = cfg.get("indications", "")
        if indications:
            preview = indications[:180] + ("…" if len(indications) > 180 else "")
            ind_state = f"-# {t('modules.automod.config.indications.current', locale=self.locale)} : {preview}"
        else:
            ind_state = f"-# {t('modules.automod.config.indications.empty', locale=self.locale)}"
        container.add_item(ui.TextDisplay(
            f"{BOOK} **{t('modules.automod.config.section_indications', locale=self.locale)}**\n"
            f"-# {t('modules.automod.config.indications.desc', locale=self.locale)}\n"
            f"{ind_state}"
        ))
        ind_row = ui.ActionRow()
        ind_btn = ui.Button(
            label=t("modules.automod.config.buttons.edit_indications", locale=self.locale),
            style=discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(BOOK),
        )
        ind_btn.callback = self.on_edit_indications
        ind_row.add_item(ind_btn)
        container.add_item(ind_row)

        # ── Exemptions (selects show what's chosen) ───────────────────────
        container.add_item(ui.TextDisplay(
            f"{GROUPS} **{t('modules.automod.config.section_exemptions', locale=self.locale)}**\n"
            f"-# {t('modules.automod.config.exempt_roles.desc', locale=self.locale)}"
        ))
        roles_row = ui.ActionRow()
        roles_select = ui.RoleSelect(
            placeholder=t("modules.automod.config.exempt_roles.placeholder", locale=self.locale),
            min_values=0, max_values=25,
        )
        self._apply_role_defaults(roles_select, self._content.get("exempt_roles", []))
        roles_select.callback = self.on_exempt_roles
        roles_row.add_item(roles_select)
        container.add_item(roles_row)

        container.add_item(ui.TextDisplay(
            f"-# {t('modules.automod.config.exempt_channels.desc', locale=self.locale)}"
        ))
        chan_row = ui.ActionRow()
        chan_select = ui.ChannelSelect(
            placeholder=t("modules.automod.config.exempt_channels.placeholder", locale=self.locale),
            channel_types=[discord.ChannelType.text, discord.ChannelType.news,
                           discord.ChannelType.public_thread, discord.ChannelType.private_thread],
            min_values=0, max_values=25,
        )
        self._apply_channel_defaults(chan_select, self._content.get("exempt_channels", []))
        chan_select.callback = self.on_exempt_channels
        chan_row.add_item(chan_select)
        container.add_item(chan_row)

        self.add_item(container)
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

    def _apply_channel_defaults(self, select: ui.ChannelSelect, ids):
        guild = self.bot.get_guild(self.guild_id) if (self.bot and self.guild_id) else None
        if not guild:
            return
        resolved = [guild.get_channel(int(cid)) for cid in ids if cid]
        resolved = [c for c in resolved if c]
        if resolved:
            select.default_values = resolved

    def _apply_role_defaults(self, select: ui.RoleSelect, ids):
        guild = self.bot.get_guild(self.guild_id) if (self.bot and self.guild_id) else None
        if not guild:
            return
        resolved = [guild.get_role(int(rid)) for rid in ids if rid]
        resolved = [r for r in resolved if r]
        if resolved:
            select.default_values = resolved

    async def _rerender(self, interaction: discord.Interaction):
        self._build_view()
        await interaction.response.edit_message(view=self)

    # -- edit callbacks (mutate working copy) ---------------------------- #
    async def on_toggle_module(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        self.working_config["enabled"] = not self.working_config["enabled"]
        self.has_changes = True
        await self._rerender(interaction)

    async def on_activations(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        selected = set(interaction.data.get("values", []))
        self._content["enabled"] = "content" in selected
        self.working_config["ignore_moderators"] = "ignore" in selected
        self.has_changes = True
        await self._rerender(interaction)

    async def on_notify_channel(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        values = interaction.data.get("values", [])
        self.working_config["notify_channel_id"] = int(values[0]) if values else None
        self.has_changes = True
        await self._rerender(interaction)

    async def on_severity(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        values = interaction.data.get("values", [])
        if values:
            self.working_config["severity"] = ac.clamp_severity(values[0])
        self.has_changes = True
        await self._rerender(interaction)

    async def on_exempt_roles(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        self._content["exempt_roles"] = [int(v) for v in interaction.data.get("values", [])]
        self.has_changes = True
        await self._rerender(interaction)

    async def on_exempt_channels(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        self._content["exempt_channels"] = [int(v) for v in interaction.data.get("values", [])]
        self.has_changes = True
        await self._rerender(interaction)

    async def on_edit_indications(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        await interaction.response.send_modal(
            IndicationsModal(self, self.working_config.get("indications", ""))
        )

    # -- action buttons -------------------------------------------------- #
    async def on_save(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        success, error = await self.bot.module_manager.save_module_config(
            self.guild_id, "automod", self.working_config
        )
        if not success:
            await interaction.response.send_message(
                t("modules.config.save.error", locale=self.locale, error=error), ephemeral=True
            )
            return
        self.current_config = copy.deepcopy(self.working_config)
        self.has_existing_config = True
        self.has_changes = False
        self._build_view()
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            t("modules.config.save.success", locale=self.locale), ephemeral=True
        )

    async def on_cancel(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        self.working_config = copy.deepcopy(self.current_config)
        self.has_changes = False
        await self._rerender(interaction)

    async def on_delete(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        await self.bot.module_manager.delete_module_config(self.guild_id, "automod")
        self.current_config = _deep_default(None)
        self.working_config = copy.deepcopy(self.current_config)
        self.has_existing_config = False
        self.has_changes = False
        self._build_view()
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            t("modules.config.delete.success", locale=self.locale), ephemeral=True
        )

    async def on_back(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        from cogs.config import ConfigMainView
        view = ConfigMainView(self.bot, self.guild_id, self.user_id, self.locale)
        await interaction.response.edit_message(view=view)
