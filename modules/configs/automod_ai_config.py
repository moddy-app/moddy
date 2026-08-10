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
from typing import Any, Callable, Dict, Optional

import discord
from discord import ui

from utils.i18n import t, i18n
from cogs.error_handler import BaseView, BaseModal
from utils.emojis import (
    SHIELD, BOOK, BACK, DELETE, MESSAGE, GROUPS, SETTINGS, WARNING, SAVE,
    UNDONE, REQUIRED_FIELDS, MANAGE_USER, GREEN_STATUS, RED_STATUS, TOGGLE_ON,
    TOGGLE_OFF, SEARCH,
)
from automod.rules_check import validate_rules, MAX_RULES_LENGTH
from automod import constants as ac
from modules.configs._common import check_guild_perms

logger = logging.getLogger("moddy.modules.automod_ai_config")

_CID_TOGGLE_MODULE = "moddy:automod:config:toggle_module"
_CID_ACTIVATIONS = "moddy:automod:config:activations"
_CID_NOTIFY_CHANNEL = "moddy:automod:config:notify_channel"
_CID_SEVERITY = "moddy:automod:config:severity"
_CID_MAX_ACTION = "moddy:automod:config:max_action"
_CID_LANGUAGE = "moddy:automod:config:language"
_CID_EDIT_INDICATIONS = "moddy:automod:config:edit_indications"
_CID_VIEW_PRECEDENTS = "moddy:automod:config:view_precedents"
_CID_EXEMPT_ROLES = "moddy:automod:config:exempt_roles"
_CID_EXEMPT_CHANNELS = "moddy:automod:config:exempt_channels"
_CID_BACK = "moddy:automod:config:back"
_CID_SAVE = "moddy:automod:config:save"
_CID_CANCEL = "moddy:automod:config:cancel"
_CID_DELETE = "moddy:automod:config:delete"

_DEFAULT_CONFIG = {
    "enabled": False,
    "indications": "",
    "notify_channel_id": None,
    "ignore_moderators": True,
    "severity": ac.SEVERITY_DEFAULT,
    "max_action": "ban",
    "langue_serveur": "auto",
    # Kill-switched AI categories (ops/backend-set; no UI selector yet). Carried
    # through the working copy so a Save from this panel never wipes it.
    "categories_desactivees": [],
    "dry_run": False,
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
    max_action = str(current.get("max_action", "ban") or "ban")
    cfg["max_action"] = max_action if max_action in ("warn", "mute", "ban") else "ban"
    langue = str(current.get("langue_serveur", "auto") or "auto")
    cfg["langue_serveur"] = langue if langue in ("auto", "fr", "en-US") else "auto"
    # Preserve any ops/backend-set kill-switch list (no UI selector yet) so
    # saving the panel does not silently drop it from the stored config.
    cfg["categories_desactivees"] = [
        str(c) for c in (current.get("categories_desactivees", []) or [])
    ]
    cfg["dry_run"] = bool(current.get("dry_run", False))
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
    """Edits the ``indications`` field of a working_config draft.

    Takes the draft as a plain dict + a rebuild callback rather than a
    parent view reference — the parent may be the shared registered shell
    once this modal is reopened after a restart, and mutating it in place
    would leak state across guilds (see docs/PERSISTENT_VIEWS.md Migration
    log, Step 8).
    """

    def __init__(self, bot, guild_id: int, locale: str, working_config: Dict[str, Any],
                 rebuild: "Callable[[discord.Interaction, Dict[str, Any], bool], Any]"):
        super().__init__(title=t("modules.automod_ai.config.indications.modal_title", locale=locale)[:45])
        self.bot = bot
        self.guild_id = guild_id
        self.locale = locale
        self.working_config = working_config
        self._rebuild = rebuild
        current = working_config.get("indications", "")
        self.field = ui.Label(
            text=t("modules.automod_ai.config.indications.modal_label", locale=locale)[:45],
            description=t("modules.automod_ai.config.indications.modal_desc", locale=locale)[:100],
            component=ui.TextInput(
                placeholder=t("modules.automod_ai.config.indications.modal_placeholder", locale=locale)[:100],
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

        if not text:  # clearing needs no AI call
            self.working_config["indications"] = ""
            view = await self._rebuild(interaction, self.working_config, True)
            await interaction.response.edit_message(view=view)
            return

        await interaction.response.defer()
        safe, reason = await validate_rules(self.bot, self.guild_id, text)
        if not safe:
            if reason == "too_long":
                msg = t("modules.automod_ai.config.indications.error_too_long", locale=locale)
            elif reason == "unavailable":
                msg = t("modules.automod_ai.config.indications.error_unavailable", locale=locale)
            else:
                msg = t("modules.automod_ai.config.indications.error_unsafe", locale=locale, reason=reason or "—")
            await interaction.followup.send(msg, ephemeral=True)
            return

        self.working_config["indications"] = text
        view = await self._rebuild(interaction, self.working_config, True)
        await interaction.edit_original_response(view=view)
        await interaction.followup.send(
            t("modules.automod_ai.config.indications.checked", locale=locale), ephemeral=True
        )


# --------------------------------------------------------------------------- #
# Main config view
# --------------------------------------------------------------------------- #
class AutomodAIConfigView(BaseView):
    """Automod configuration panel (standard Save/Cancel pattern).

    Persistent: yes. Auth: Manage Server in the guild (checked on every
    click via check_guild_perms — NOT via a stored user_id, which cannot
    survive a restarted shell).
    """

    __persistent__ = True

    def __init__(self, bot=None, guild_id: Optional[int] = None, user_id: Optional[int] = None,
                 locale: str = "en-US", current_config: Optional[Dict[str, Any]] = None):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale

        self.has_existing_config = bool(current_config)
        self.current_config = _deep_default(current_config)
        self.working_config = copy.deepcopy(self.current_config)
        self.has_changes = False
        # Session 7: learned server precedents (loaded lazily via
        # ``load_precedent_stats`` before the panel is first shown).
        self._precedent_count: Optional[int] = None
        self._precedent_last = None

        self._build_view()

    async def load_precedent_stats(self):
        """Load the guild's precedent count + last date, then rebuild the panel.

        Called by the opener before the panel is first shown (the view is built
        synchronously in ``__init__``, so the DB read happens here). Best-effort:
        a failure just leaves the count unknown.
        """
        db = getattr(self.bot, "db", None)
        if not ac.PRECEDENTS_ENABLED or db is None:
            return
        try:
            self._precedent_count = await db.count_precedents(self.guild_id)
            self._precedent_last = await db.last_precedent_at(self.guild_id)
        except Exception:  # noqa: BLE001 — the panel works fine without the count
            return
        self._build_view()

    # -- helpers --------------------------------------------------------- #
    @property
    def _content(self) -> Dict[str, Any]:
        return self.working_config["features"]["content"]

    def _dot(self, value: bool) -> str:
        return GREEN_STATUS if value else RED_STATUS

    def _state(self, value: bool) -> str:
        key = "modules.automod_ai.config.state.on" if value else "modules.automod_ai.config.state.off"
        return t(key, locale=self.locale)

    def _is_live_for(self, interaction: discord.Interaction) -> bool:
        return self.bot is not None and self.guild_id == interaction.guild_id

    async def _fresh_working_config(self, interaction: discord.Interaction) -> Dict[str, Any]:
        if self._is_live_for(interaction):
            return copy.deepcopy(self.working_config)
        bot = interaction.client
        saved = await bot.module_manager.get_module_config(interaction.guild_id, "automod_ai")
        return _deep_default(saved)

    async def _rebuild(self, interaction: discord.Interaction, working_config: Dict[str, Any],
                        has_changes: bool) -> "AutomodAIConfigView":
        """Always construct a NEW view instance rather than mutate/resend
        self — self may be the single shared shell serving every guild after
        a restart."""
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        saved = await bot.module_manager.get_module_config(interaction.guild_id, "automod_ai")
        view = AutomodAIConfigView(bot, interaction.guild_id, interaction.user.id, locale, current_config=saved)
        view.working_config = working_config
        view.has_changes = has_changes
        await view.load_precedent_stats()
        view._build_view()
        return view

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
            f"### {SHIELD} {t('modules.automod_ai.config.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t("modules.automod_ai.config.description", locale=self.locale)
        ))
        if running:
            container.add_item(ui.TextDisplay(
                t("modules.automod_ai.config.summary_active", locale=self.locale)
            ))
        else:
            hint_key = ("summary_need_channel" if not has_channel
                        else "summary_need_module" if not module_on
                        else "summary_need_content")
            container.add_item(ui.TextDisplay(
                f"{t('modules.automod_ai.config.summary_inactive', locale=self.locale)}\n"
                f"-# {t(f'modules.automod_ai.config.{hint_key}', locale=self.locale)}"
            ))

        # ── Module on/off — a single toggle button (exception to selects) ──
        toggle_row = ui.ActionRow()
        toggle_btn = ui.Button(
            label=t(
                "modules.automod_ai.config.buttons."
                + ("disable_module" if module_on else "enable_module"),
                locale=self.locale,
            ),
            style=discord.ButtonStyle.danger if module_on else discord.ButtonStyle.success,
            emoji=discord.PartialEmoji.from_str(TOGGLE_ON if module_on else TOGGLE_OFF),
        )
        toggle_btn.custom_id = _CID_TOGGLE_MODULE
        toggle_btn.callback = self.on_toggle_module
        toggle_row.add_item(toggle_btn)
        container.add_item(toggle_row)

        # Registration shell (self.bot is None): render every conditionally
        # shown button below regardless of state, so a live message stuck
        # in a state this bare shell doesn't happen to be in still has its
        # custom_ids registered post-restart.
        is_shell = self.bot is None

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # ── Options (the remaining toggles, picked in one select) ─────────
        container.add_item(ui.TextDisplay(
            f"{SETTINGS} **{t('modules.automod_ai.config.section_options', locale=self.locale)}**"
        ))
        dry_run_on = bool(cfg.get("dry_run", False))
        opt_row = ui.ActionRow()
        opt_select = ui.Select(
            placeholder=t("modules.automod_ai.config.activations.placeholder", locale=self.locale),
            min_values=0, max_values=3,
            options=[
                discord.SelectOption(
                    label=t("modules.automod_ai.config.content_label", locale=self.locale),
                    value="content",
                    description=t("modules.automod_ai.config.content_desc", locale=self.locale)[:100],
                    emoji=discord.PartialEmoji.from_str(MESSAGE),
                    default=content_on,
                ),
                discord.SelectOption(
                    label=t("modules.automod_ai.config.ignore_mods.label", locale=self.locale),
                    value="ignore",
                    description=t("modules.automod_ai.config.ignore_mods.desc", locale=self.locale)[:100],
                    emoji=discord.PartialEmoji.from_str(MANAGE_USER),
                    default=ignore_on,
                ),
                discord.SelectOption(
                    label=t("modules.automod_ai.config.dry_run.label", locale=self.locale),
                    value="dry_run",
                    description=t("modules.automod_ai.config.dry_run.desc", locale=self.locale)[:100],
                    emoji=discord.PartialEmoji.from_str(SEARCH),
                    default=dry_run_on,
                ),
            ],
        )
        opt_select.custom_id = _CID_ACTIVATIONS
        opt_select.callback = self.on_activations
        opt_row.add_item(opt_select)
        container.add_item(opt_row)
        if dry_run_on:
            container.add_item(ui.TextDisplay(
                f"-# {SEARCH} {t('modules.automod_ai.config.dry_run.active', locale=self.locale)}"))

        # ── Alert channel (REQUIRED) ──────────────────────────────────────
        warn_line = ("" if has_channel
                     else f"\n-# {WARNING} {t('modules.automod_ai.config.notify.missing', locale=self.locale)}")
        container.add_item(ui.TextDisplay(
            f"{MESSAGE} **{t('modules.automod_ai.config.section_notify', locale=self.locale)}**{REQUIRED_FIELDS}\n"
            f"-# {t('modules.automod_ai.config.notify.desc', locale=self.locale)}{warn_line}"
        ))
        notify_row = ui.ActionRow()
        notify_select = ui.ChannelSelect(
            placeholder=t("modules.automod_ai.config.notify.placeholder", locale=self.locale),
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=0, max_values=1,
        )
        self._apply_channel_defaults(notify_select, [cfg.get("notify_channel_id")])
        notify_select.custom_id = _CID_NOTIFY_CHANNEL
        notify_select.callback = self.on_notify_channel
        notify_row.add_item(notify_select)
        container.add_item(notify_row)

        # ── Severity (the select shows the current level) ─────────────────
        severity = cfg.get("severity", ac.SEVERITY_DEFAULT)
        container.add_item(ui.TextDisplay(
            f"{SETTINGS} **{t('modules.automod_ai.config.section_severity', locale=self.locale)}**\n"
            f"-# {t('modules.automod_ai.config.severity.desc', locale=self.locale)}"
        ))
        sev_row = ui.ActionRow()
        sev_select = ui.Select(
            placeholder=t("modules.automod_ai.config.severity.placeholder", locale=self.locale),
            min_values=1, max_values=1,
            options=[
                discord.SelectOption(
                    label=t("modules.automod_ai.config.severity.option", locale=self.locale, n=n),
                    value=str(n),
                    description=t(f"modules.automod_ai.config.severity.level_{n}", locale=self.locale)[:100],
                    default=(n == severity),
                )
                for n in range(ac.SEVERITY_MIN, ac.SEVERITY_MAX + 1)
            ],
        )
        sev_select.custom_id = _CID_SEVERITY
        sev_select.callback = self.on_severity
        sev_row.add_item(sev_select)
        container.add_item(sev_row)

        # ── Limits & language (max sanction the automod may apply + DM tongue) ─
        container.add_item(ui.TextDisplay(
            f"{SETTINGS} **{t('modules.automod_ai.config.section_limits', locale=self.locale)}**\n"
            f"-# {t('modules.automod_ai.config.max_action.desc', locale=self.locale)}"
        ))
        max_action = cfg.get("max_action", "ban")
        maxa_row = ui.ActionRow()
        maxa_select = ui.Select(
            placeholder=t("modules.automod_ai.config.max_action.placeholder", locale=self.locale),
            min_values=1, max_values=1,
            options=[
                discord.SelectOption(
                    label=t(f"modules.automod_ai.config.max_action.option_{v}", locale=self.locale),
                    value=v, default=(v == max_action),
                )
                for v in ("warn", "mute", "ban")
            ],
        )
        maxa_select.custom_id = _CID_MAX_ACTION
        maxa_select.callback = self.on_max_action
        maxa_row.add_item(maxa_select)
        container.add_item(maxa_row)

        container.add_item(ui.TextDisplay(
            f"-# {t('modules.automod_ai.config.language.desc', locale=self.locale)}"
        ))
        langue = cfg.get("langue_serveur", "auto")
        lang_row = ui.ActionRow()
        lang_select = ui.Select(
            placeholder=t("modules.automod_ai.config.language.placeholder", locale=self.locale),
            min_values=1, max_values=1,
            options=[
                discord.SelectOption(
                    label=t(f"modules.automod_ai.config.language.{key}", locale=self.locale),
                    value=value, default=(value == langue),
                )
                for key, value in (("auto", "auto"), ("fr", "fr"), ("en", "en-US"))
            ],
        )
        lang_select.custom_id = _CID_LANGUAGE
        lang_select.callback = self.on_language
        lang_row.add_item(lang_select)
        container.add_item(lang_row)

        # ── Guidance (button+modal → keep a preview, can't show it inline) ─
        indications = cfg.get("indications", "")
        if indications:
            preview = indications[:180] + ("…" if len(indications) > 180 else "")
            ind_state = f"-# {t('modules.automod_ai.config.indications.current', locale=self.locale)} : {preview}"
        else:
            ind_state = f"-# {t('modules.automod_ai.config.indications.empty', locale=self.locale)}"
        container.add_item(ui.TextDisplay(
            f"{BOOK} **{t('modules.automod_ai.config.section_indications', locale=self.locale)}**\n"
            f"-# {t('modules.automod_ai.config.indications.desc', locale=self.locale)}\n"
            f"{ind_state}"
        ))
        ind_row = ui.ActionRow()
        ind_btn = ui.Button(
            label=t("modules.automod_ai.config.buttons.edit_indications", locale=self.locale),
            style=discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(BOOK),
        )
        ind_btn.custom_id = _CID_EDIT_INDICATIONS
        ind_btn.callback = self.on_edit_indications
        ind_row.add_item(ind_btn)
        container.add_item(ind_row)

        # ── Learned precedents (server jurisprudence, session 7) ──────────
        if ac.PRECEDENTS_ENABLED:
            count = self._precedent_count
            if count is None:
                prec_line = t("modules.automod_ai.config.precedents.desc", locale=self.locale)
            elif count == 0:
                prec_line = (
                    f"{t('modules.automod_ai.config.precedents.desc', locale=self.locale)}\n"
                    f"-# {t('modules.automod_ai.config.precedents.empty', locale=self.locale)}")
            else:
                last = ""
                if self._precedent_last is not None:
                    try:
                        last = f" · {t('modules.automod_ai.config.precedents.last', locale=self.locale)} <t:{int(self._precedent_last.timestamp())}:R>"
                    except Exception:  # noqa: BLE001
                        last = ""
                prec_line = (
                    f"{t('modules.automod_ai.config.precedents.desc', locale=self.locale)}\n"
                    f"-# {t('modules.automod_ai.config.precedents.count', locale=self.locale, n=count)}{last}")
            container.add_item(ui.TextDisplay(
                f"{SEARCH} **{t('modules.automod_ai.config.section_precedents', locale=self.locale)}**\n"
                f"-# {prec_line}"))
            if count or is_shell:
                prec_row = ui.ActionRow()
                prec_btn = ui.Button(
                    custom_id=_CID_VIEW_PRECEDENTS,
                    label=t("modules.automod_ai.config.buttons.view_precedents", locale=self.locale),
                    style=discord.ButtonStyle.secondary,
                    emoji=discord.PartialEmoji.from_str(SEARCH),
                )
                prec_btn.callback = self.on_view_precedents
                prec_row.add_item(prec_btn)
                container.add_item(prec_row)

        # ── Exemptions (selects show what's chosen) ───────────────────────
        container.add_item(ui.TextDisplay(
            f"{GROUPS} **{t('modules.automod_ai.config.section_exemptions', locale=self.locale)}**\n"
            f"-# {t('modules.automod_ai.config.exempt_roles.desc', locale=self.locale)}"
        ))
        roles_row = ui.ActionRow()
        roles_select = ui.RoleSelect(
            placeholder=t("modules.automod_ai.config.exempt_roles.placeholder", locale=self.locale),
            min_values=0, max_values=25,
        )
        self._apply_role_defaults(roles_select, self._content.get("exempt_roles", []))
        roles_select.custom_id = _CID_EXEMPT_ROLES
        roles_select.callback = self.on_exempt_roles
        roles_row.add_item(roles_select)
        container.add_item(roles_row)

        container.add_item(ui.TextDisplay(
            f"-# {t('modules.automod_ai.config.exempt_channels.desc', locale=self.locale)}"
        ))
        chan_row = ui.ActionRow()
        chan_select = ui.ChannelSelect(
            placeholder=t("modules.automod_ai.config.exempt_channels.placeholder", locale=self.locale),
            channel_types=[discord.ChannelType.text, discord.ChannelType.news,
                           discord.ChannelType.public_thread, discord.ChannelType.private_thread],
            min_values=0, max_values=25,
        )
        self._apply_channel_defaults(chan_select, self._content.get("exempt_channels", []))
        chan_select.custom_id = _CID_EXEMPT_CHANNELS
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
            custom_id=_CID_BACK,
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
                custom_id=_CID_SAVE,
            )
            save_btn.callback = self.on_save
            btn_row.add_item(save_btn)

            cancel_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(UNDONE),
                label=t("modules.config.buttons.cancel", locale=self.locale),
                style=discord.ButtonStyle.danger,
                custom_id=_CID_CANCEL,
            )
            cancel_btn.callback = self.on_cancel
            btn_row.add_item(cancel_btn)
        if (not self.has_changes and self.has_existing_config) or is_shell:
            delete_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(DELETE),
                label=t("modules.config.buttons.delete", locale=self.locale),
                style=discord.ButtonStyle.danger,
                custom_id=_CID_DELETE,
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

    @staticmethod
    def _content_of(working_config: Dict[str, Any]) -> Dict[str, Any]:
        return working_config["features"]["content"]

    # -- edit callbacks (mutate a fresh working copy) --------------------- #
    async def on_toggle_module(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        working_config = await self._fresh_working_config(interaction)
        working_config["enabled"] = not working_config["enabled"]
        view = await self._rebuild(interaction, working_config, True)
        await interaction.response.edit_message(view=view)

    async def on_activations(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        working_config = await self._fresh_working_config(interaction)
        selected = set(interaction.data.get("values", []))
        self._content_of(working_config)["enabled"] = "content" in selected
        working_config["ignore_moderators"] = "ignore" in selected
        working_config["dry_run"] = "dry_run" in selected
        view = await self._rebuild(interaction, working_config, True)
        await interaction.response.edit_message(view=view)

    async def on_notify_channel(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        working_config = await self._fresh_working_config(interaction)
        values = interaction.data.get("values", [])
        working_config["notify_channel_id"] = int(values[0]) if values else None
        view = await self._rebuild(interaction, working_config, True)
        await interaction.response.edit_message(view=view)

    async def on_severity(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        working_config = await self._fresh_working_config(interaction)
        values = interaction.data.get("values", [])
        if values:
            working_config["severity"] = ac.clamp_severity(values[0])
        view = await self._rebuild(interaction, working_config, True)
        await interaction.response.edit_message(view=view)

    async def on_max_action(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        working_config = await self._fresh_working_config(interaction)
        values = interaction.data.get("values", [])
        if values and values[0] in ("warn", "mute", "ban"):
            working_config["max_action"] = values[0]
        view = await self._rebuild(interaction, working_config, True)
        await interaction.response.edit_message(view=view)

    async def on_language(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        working_config = await self._fresh_working_config(interaction)
        values = interaction.data.get("values", [])
        if values and values[0] in ("auto", "fr", "en-US"):
            working_config["langue_serveur"] = values[0]
        view = await self._rebuild(interaction, working_config, True)
        await interaction.response.edit_message(view=view)

    async def on_exempt_roles(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        working_config = await self._fresh_working_config(interaction)
        self._content_of(working_config)["exempt_roles"] = [int(v) for v in interaction.data.get("values", [])]
        view = await self._rebuild(interaction, working_config, True)
        await interaction.response.edit_message(view=view)

    async def on_exempt_channels(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        working_config = await self._fresh_working_config(interaction)
        self._content_of(working_config)["exempt_channels"] = [int(v) for v in interaction.data.get("values", [])]
        view = await self._rebuild(interaction, working_config, True)
        await interaction.response.edit_message(view=view)

    async def on_edit_indications(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        working_config = await self._fresh_working_config(interaction)
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        await interaction.response.send_modal(
            IndicationsModal(bot, interaction.guild_id, locale, working_config, self._rebuild)
        )

    async def on_view_precedents(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        from modules.configs.automod_ai_precedents_view import AutomodAIPrecedentsView
        view = AutomodAIPrecedentsView(bot, interaction.guild_id, interaction.user.id, locale)
        await view.load()
        await interaction.response.edit_message(view=view)

    # -- action buttons -------------------------------------------------- #
    async def on_save(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        working_config = await self._fresh_working_config(interaction)

        success, error = await bot.module_manager.save_module_config(
            interaction.guild_id, "automod_ai", working_config,
            actor_id=interaction.user.id,
        )
        if not success:
            await interaction.response.send_message(
                t("modules.config.save.error", locale=locale, error=error), ephemeral=True
            )
            return
        view = await self._rebuild(interaction, working_config, False)
        await interaction.response.edit_message(view=view)
        await interaction.followup.send(
            t("modules.config.save.success", locale=locale), ephemeral=True
        )

    async def on_cancel(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        saved = await bot.module_manager.get_module_config(interaction.guild_id, "automod_ai")
        view = AutomodAIConfigView(bot, interaction.guild_id, interaction.user.id, locale, current_config=saved)
        await view.load_precedent_stats()
        await interaction.response.edit_message(view=view)

    async def on_delete(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        await bot.module_manager.delete_module_config(interaction.guild_id, "automod_ai")
        view = AutomodAIConfigView(bot, interaction.guild_id, interaction.user.id, locale, current_config=None)
        await view.load_precedent_stats()
        await interaction.response.edit_message(view=view)
        await interaction.followup.send(
            t("modules.config.delete.success", locale=locale), ephemeral=True
        )

    async def on_back(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        from cogs.config import ConfigMainView
        locale = i18n.get_user_locale(interaction)
        view = ConfigMainView(interaction.client, interaction.guild_id, interaction.user.id, locale)
        await interaction.response.edit_message(view=view)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_view(cls())
