"""
Configuration UI for the Automod module (`/config`).

This panel is **live / immediate-apply**: every toggle, select and the rules
editor persist straight to the DB and re-render from it — there is no Save /
Cancel step. That keeps the UX dead simple and makes the view naturally
**persistent** (see docs/PERSISTENT_VIEWS.md): every component has a stable,
namespaced ``custom_id``, the view never times out, and a shell instance is
registered so clicks keep working after a bot restart. Callbacks re-derive all
context (`bot`, `guild_id`, `locale`) from ``interaction`` + the DB — never from
``self``. Auth: **Manage Server** in the guild, checked on every interaction.

The server rules are AI-validated against prompt injection (`automod.rules_check`)
**before** being saved, because they are embedded verbatim into the moderation
engine's system prompt.
"""

import copy
import logging
from typing import Any, Dict, Optional

import discord
from discord import ui

from utils.i18n import t, i18n
from cogs.error_handler import BaseView, BaseModal
from utils.emojis import (
    FILTER, BOOK, BACK, DELETE, MESSAGE, GROUPS,
    TOGGLE_ON, TOGGLE_OFF,
)
from automod.rules_check import validate_rules, MAX_RULES_LENGTH

logger = logging.getLogger("moddy.modules.automod_config")

# --------------------------------------------------------------------------- #
# Namespaced custom_id constants (persistent dispatch).
# Format: moddy:automod:cfg:<action>. Guild context is re-derived from
# ``interaction.guild_id`` so the ids stay static (one shell, all guilds).
# --------------------------------------------------------------------------- #
_CID_TOGGLE_MODULE = "moddy:automod:cfg:toggle_module"
_CID_TOGGLE_CONTENT = "moddy:automod:cfg:toggle_content"
_CID_TOGGLE_IGNORE = "moddy:automod:cfg:toggle_ignore"
_CID_EDIT_RULES = "moddy:automod:cfg:edit_rules"
_CID_LOG_CHANNEL = "moddy:automod:cfg:log_channel"
_CID_EXEMPT_ROLES = "moddy:automod:cfg:exempt_roles"
_CID_EXEMPT_CHANNELS = "moddy:automod:cfg:exempt_channels"
_CID_BACK = "moddy:automod:cfg:back"
_CID_RESET = "moddy:automod:cfg:reset"

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
    """Return a fully-populated config, merging stored values over the defaults."""
    cfg = copy.deepcopy(_DEFAULT_CONFIG)
    if not current:
        return cfg
    cfg["enabled"] = bool(current.get("enabled", False))
    cfg["rules"] = str(current.get("rules", "") or "")
    cfg["log_channel_id"] = current.get("log_channel_id")
    cfg["ignore_moderators"] = bool(current.get("ignore_moderators", True))
    content = (current.get("features", {}) or {}).get("content", {}) or {}
    cfg["features"]["content"] = {
        "enabled": bool(content.get("enabled", False)),
        "exempt_roles": list(content.get("exempt_roles", [])),
        "exempt_channels": list(content.get("exempt_channels", [])),
    }
    return cfg


async def _check_perms(interaction: discord.Interaction) -> bool:
    """Authorize a config interaction: requires Manage Server in this guild."""
    perms = getattr(interaction.user, "guild_permissions", None)
    if not interaction.guild_id or perms is None or not perms.manage_guild:
        await interaction.response.send_message(
            t("modules.config.errors.no_user_perms", locale=i18n.get_user_locale(interaction)),
            ephemeral=True,
        )
        return False
    return True


async def _load_config(bot, guild_id: int) -> Dict[str, Any]:
    cfg = await bot.module_manager.get_module_config(guild_id, "automod")
    return _deep_default(cfg)


async def _save_and_render(interaction: discord.Interaction, cfg: Dict[str, Any]) -> None:
    """Persist a config change, then re-render the panel from the saved state."""
    bot = interaction.client
    guild_id = interaction.guild_id
    locale = i18n.get_user_locale(interaction)

    success, error = await bot.module_manager.save_module_config(guild_id, "automod", cfg)
    if not success:
        await interaction.response.send_message(
            t("modules.config.save.error", locale=locale, error=error), ephemeral=True
        )
        return

    view = AutomodConfigView(bot, guild_id, locale, cfg)
    if interaction.response.is_done():
        await interaction.edit_original_response(view=view)
    else:
        await interaction.response.edit_message(view=view)


# --------------------------------------------------------------------------- #
# Rules modal (AI-validated). Modals are one-shot, not persisted.
# --------------------------------------------------------------------------- #
class RulesModal(BaseModal, title="Règlement du serveur"):
    def __init__(self, bot, guild_id: int, locale: str, current: str):
        super().__init__(timeout=600)
        self.bot = bot
        self.guild_id = guild_id
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
        locale = i18n.get_user_locale(interaction)
        text = (self.rules_input.value or "").strip()
        cfg = await _load_config(self.bot, self.guild_id)

        # Clearing the rules needs no AI call.
        if not text:
            cfg["rules"] = ""
            await _save_and_render(interaction, cfg)
            await interaction.followup.send(
                t("modules.automod.config.rules.cleared", locale=locale), ephemeral=True
            )
            return

        # The AI safety check can take >1s → defer (panel update).
        await interaction.response.defer()
        safe, reason = await validate_rules(self.bot, self.guild_id, text)
        if not safe:
            if reason == "too_long":
                msg = t("modules.automod.config.rules.error_too_long", locale=locale)
            elif reason == "unavailable":
                msg = t("modules.automod.config.rules.error_unavailable", locale=locale)
            else:
                msg = t("modules.automod.config.rules.error_unsafe", locale=locale, reason=reason or "—")
            await interaction.followup.send(msg, ephemeral=True)
            return

        cfg["rules"] = text
        await _save_and_render(interaction, cfg)
        await interaction.followup.send(
            t("modules.automod.config.rules.saved", locale=locale), ephemeral=True
        )


# --------------------------------------------------------------------------- #
# Main config view (persistent)
# --------------------------------------------------------------------------- #
class AutomodConfigView(BaseView):
    """Automod configuration panel. Persistent: yes. Auth: Manage Server."""

    __persistent__ = True

    def __init__(self, bot=None, guild_id: Optional[int] = None,
                 locale: str = "en-US", current_config: Optional[Dict[str, Any]] = None):
        super().__init__()  # timeout=None (BaseView default)
        self.bot = bot
        self.guild_id = guild_id
        self.locale = locale
        self.config = _deep_default(current_config)
        self.has_existing_config = bool(current_config)
        self._build_view()

    # -- helpers --------------------------------------------------------- #
    @property
    def _content(self) -> Dict[str, Any]:
        return self.config["features"]["content"]

    def _toggle(self, value: bool) -> str:
        return TOGGLE_ON if value else TOGGLE_OFF

    def _state(self, value: bool) -> str:
        key = "modules.automod.config.state.on" if value else "modules.automod.config.state.off"
        return t(key, locale=self.locale)

    # -- build ----------------------------------------------------------- #
    def _build_view(self):
        self.clear_items()
        container = ui.Container()

        module_on = self.config["enabled"]
        content_on = self._content["enabled"]
        ignore_on = self.config["ignore_moderators"]
        running = module_on and content_on

        # Header
        container.add_item(ui.TextDisplay(
            f"### {FILTER} {t('modules.automod.config.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t("modules.automod.config.description", locale=self.locale)
        ))

        # Live summary
        if running:
            container.add_item(ui.TextDisplay(
                t("modules.automod.config.summary_active", locale=self.locale)
            ))
        else:
            hint_key = ("summary_need_module" if not module_on else "summary_need_content")
            container.add_item(ui.TextDisplay(
                f"{t('modules.automod.config.summary_inactive', locale=self.locale)}\n"
                f"-# {t(f'modules.automod.config.{hint_key}', locale=self.locale)}"
            ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # --- Status section ---
        container.add_item(ui.TextDisplay(
            f"**{t('modules.automod.config.section_status', locale=self.locale)}**\n"
            f"{self._toggle(module_on)} **{t('modules.automod.config.module_label', locale=self.locale)}** "
            f"· {self._state(module_on)}\n"
            f"-# {t('modules.automod.config.module_desc', locale=self.locale)}\n"
            f"{self._toggle(content_on)} **{t('modules.automod.config.content_label', locale=self.locale)}** "
            f"· {self._state(content_on)}\n"
            f"-# {t('modules.automod.config.content_desc', locale=self.locale)}"
        ))
        status_row = ui.ActionRow()
        module_btn = ui.Button(
            label=t(f"modules.automod.config.buttons.{'disable' if module_on else 'enable'}_module",
                    locale=self.locale),
            style=discord.ButtonStyle.secondary if module_on else discord.ButtonStyle.success,
            emoji=discord.PartialEmoji.from_str(self._toggle(module_on)),
            custom_id=_CID_TOGGLE_MODULE,
        )
        module_btn.callback = self.on_toggle_module
        status_row.add_item(module_btn)
        content_btn = ui.Button(
            label=t(f"modules.automod.config.buttons.{'disable' if content_on else 'enable'}_content",
                    locale=self.locale),
            style=discord.ButtonStyle.secondary if content_on else discord.ButtonStyle.success,
            emoji=discord.PartialEmoji.from_str(self._toggle(content_on)),
            custom_id=_CID_TOGGLE_CONTENT,
        )
        content_btn.callback = self.on_toggle_content
        status_row.add_item(content_btn)
        container.add_item(status_row)

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # --- Rules section ---
        rules = self.config.get("rules", "")
        if rules:
            preview = rules[:180] + ("…" if len(rules) > 180 else "")
            rules_state = f"-# {t('modules.automod.config.rules.current', locale=self.locale)} : {preview}"
        else:
            rules_state = f"-# {t('modules.automod.config.rules.empty', locale=self.locale)}"
        container.add_item(ui.TextDisplay(
            f"{BOOK} **{t('modules.automod.config.section_rules', locale=self.locale)}**\n"
            f"-# {t('modules.automod.config.rules.desc', locale=self.locale)}\n"
            f"{rules_state}"
        ))
        rules_row = ui.ActionRow()
        rules_btn = ui.Button(
            label=t("modules.automod.config.buttons.edit_rules", locale=self.locale),
            style=discord.ButtonStyle.primary,
            emoji=discord.PartialEmoji.from_str(BOOK),
            custom_id=_CID_EDIT_RULES,
        )
        rules_btn.callback = self.on_edit_rules
        rules_row.add_item(rules_btn)
        container.add_item(rules_row)

        # --- Log channel section ---
        container.add_item(ui.TextDisplay(
            f"{MESSAGE} **{t('modules.automod.config.section_logs', locale=self.locale)}**\n"
            f"-# {t('modules.automod.config.log_channel.desc', locale=self.locale)}"
        ))
        log_row = ui.ActionRow()
        log_select = ui.ChannelSelect(
            placeholder=t("modules.automod.config.log_channel.placeholder", locale=self.locale),
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=0, max_values=1, custom_id=_CID_LOG_CHANNEL,
        )
        self._apply_channel_defaults(log_select, [self.config.get("log_channel_id")])
        log_select.callback = self.on_log_channel
        log_row.add_item(log_select)
        container.add_item(log_row)

        # --- Exemptions section ---
        container.add_item(ui.TextDisplay(
            f"{GROUPS} **{t('modules.automod.config.section_exemptions', locale=self.locale)}**\n"
            f"**{t('modules.automod.config.exempt_roles.label', locale=self.locale)}**\n"
            f"-# {t('modules.automod.config.exempt_roles.desc', locale=self.locale)}"
        ))
        roles_row = ui.ActionRow()
        roles_select = ui.RoleSelect(
            placeholder=t("modules.automod.config.exempt_roles.placeholder", locale=self.locale),
            min_values=0, max_values=25, custom_id=_CID_EXEMPT_ROLES,
        )
        self._apply_role_defaults(roles_select, self._content.get("exempt_roles", []))
        roles_select.callback = self.on_exempt_roles
        roles_row.add_item(roles_select)
        container.add_item(roles_row)

        container.add_item(ui.TextDisplay(
            f"**{t('modules.automod.config.exempt_channels.label', locale=self.locale)}**\n"
            f"-# {t('modules.automod.config.exempt_channels.desc', locale=self.locale)}"
        ))
        chan_row = ui.ActionRow()
        chan_select = ui.ChannelSelect(
            placeholder=t("modules.automod.config.exempt_channels.placeholder", locale=self.locale),
            channel_types=[discord.ChannelType.text, discord.ChannelType.news,
                           discord.ChannelType.public_thread, discord.ChannelType.private_thread],
            min_values=0, max_values=25, custom_id=_CID_EXEMPT_CHANNELS,
        )
        self._apply_channel_defaults(chan_select, self._content.get("exempt_channels", []))
        chan_select.callback = self.on_exempt_channels
        chan_row.add_item(chan_select)
        container.add_item(chan_row)

        # --- Options section ---
        container.add_item(ui.TextDisplay(
            f"**{t('modules.automod.config.section_options', locale=self.locale)}**\n"
            f"{self._toggle(ignore_on)} **{t('modules.automod.config.ignore_mods.label', locale=self.locale)}** "
            f"· {self._state(ignore_on)}\n"
            f"-# {t('modules.automod.config.ignore_mods.desc', locale=self.locale)}"
        ))
        opt_row = ui.ActionRow()
        ignore_btn = ui.Button(
            label=t(f"modules.automod.config.buttons.{'disable' if ignore_on else 'enable'}_ignore",
                    locale=self.locale),
            style=discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(self._toggle(ignore_on)),
            custom_id=_CID_TOGGLE_IGNORE,
        )
        ignore_btn.callback = self.on_toggle_ignore
        opt_row.add_item(ignore_btn)
        container.add_item(opt_row)

        container.add_item(ui.TextDisplay(
            f"-# {t('modules.automod.config.apply_hint', locale=self.locale)}"
        ))

        self.add_item(container)

        # --- Bottom actions ---
        bottom = ui.ActionRow()
        back_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(BACK),
            label=t("modules.config.buttons.back", locale=self.locale),
            style=discord.ButtonStyle.secondary, custom_id=_CID_BACK,
        )
        back_btn.callback = self.on_back
        bottom.add_item(back_btn)
        # Always present (even when there's nothing saved yet) so its custom_id
        # is registered on the persistent shell and keeps dispatching after a
        # restart. Disabled when there is no stored config to remove.
        reset_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(DELETE),
            label=t("modules.config.buttons.delete", locale=self.locale),
            style=discord.ButtonStyle.danger, custom_id=_CID_RESET,
            disabled=(self.bot is not None and not self.has_existing_config),
        )
        reset_btn.callback = self.on_reset
        bottom.add_item(reset_btn)
        self.add_item(bottom)

    def _apply_channel_defaults(self, select: ui.ChannelSelect, ids):
        if self.bot is None or not self.guild_id:
            return
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            return
        resolved = [guild.get_channel(int(cid)) for cid in ids if cid]
        resolved = [c for c in resolved if c]
        if resolved:
            select.default_values = resolved

    def _apply_role_defaults(self, select: ui.RoleSelect, ids):
        if self.bot is None or not self.guild_id:
            return
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            return
        resolved = [guild.get_role(int(rid)) for rid in ids if rid]
        resolved = [r for r in resolved if r]
        if resolved:
            select.default_values = resolved

    # -- callbacks (re-derive everything from interaction + DB) ---------- #
    async def on_toggle_module(self, interaction: discord.Interaction):
        if not await _check_perms(interaction):
            return
        cfg = await _load_config(interaction.client, interaction.guild_id)
        cfg["enabled"] = not cfg["enabled"]
        await _save_and_render(interaction, cfg)

    async def on_toggle_content(self, interaction: discord.Interaction):
        if not await _check_perms(interaction):
            return
        cfg = await _load_config(interaction.client, interaction.guild_id)
        cfg["features"]["content"]["enabled"] = not cfg["features"]["content"]["enabled"]
        await _save_and_render(interaction, cfg)

    async def on_toggle_ignore(self, interaction: discord.Interaction):
        if not await _check_perms(interaction):
            return
        cfg = await _load_config(interaction.client, interaction.guild_id)
        cfg["ignore_moderators"] = not cfg["ignore_moderators"]
        await _save_and_render(interaction, cfg)

    async def on_log_channel(self, interaction: discord.Interaction):
        if not await _check_perms(interaction):
            return
        cfg = await _load_config(interaction.client, interaction.guild_id)
        values = interaction.data.get("values", [])
        cfg["log_channel_id"] = int(values[0]) if values else None
        await _save_and_render(interaction, cfg)

    async def on_exempt_roles(self, interaction: discord.Interaction):
        if not await _check_perms(interaction):
            return
        cfg = await _load_config(interaction.client, interaction.guild_id)
        cfg["features"]["content"]["exempt_roles"] = [int(v) for v in interaction.data.get("values", [])]
        await _save_and_render(interaction, cfg)

    async def on_exempt_channels(self, interaction: discord.Interaction):
        if not await _check_perms(interaction):
            return
        cfg = await _load_config(interaction.client, interaction.guild_id)
        cfg["features"]["content"]["exempt_channels"] = [int(v) for v in interaction.data.get("values", [])]
        await _save_and_render(interaction, cfg)

    async def on_edit_rules(self, interaction: discord.Interaction):
        if not await _check_perms(interaction):
            return
        locale = i18n.get_user_locale(interaction)
        cfg = await _load_config(interaction.client, interaction.guild_id)
        modal = RulesModal(interaction.client, interaction.guild_id, locale, cfg.get("rules", ""))
        await interaction.response.send_modal(modal)

    async def on_reset(self, interaction: discord.Interaction):
        if not await _check_perms(interaction):
            return
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        await bot.module_manager.delete_module_config(interaction.guild_id, "automod")
        view = AutomodConfigView(bot, interaction.guild_id, locale, None)
        await interaction.response.edit_message(view=view)
        await interaction.followup.send(
            t("modules.config.delete.success", locale=locale), ephemeral=True
        )

    async def on_back(self, interaction: discord.Interaction):
        if not await _check_perms(interaction):
            return
        from cogs.config import ConfigMainView
        locale = i18n.get_user_locale(interaction)
        view = ConfigMainView(interaction.client, interaction.guild_id, interaction.user.id, locale)
        await interaction.response.edit_message(view=view)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _check_perms(interaction)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_view(cls())
