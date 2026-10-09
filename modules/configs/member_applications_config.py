"""
Configuration UI for the Member Applications module.

The application form itself is Discord's and is edited in the server settings;
this panel only decides where Moddy puts the review cards and who may act on
them:

- the review channel (required — a server with a stored configuration is a
  server using the module; there is no separate on/off switch, deleting the
  configuration is how the module is turned off);
- roles pinged when an application arrives;
- roles allowed to decide in addition to members with Kick Members;
- preset rejection reasons, edited in a Modal V2 and offered in the reject
  modal of every card.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import discord
from discord import ui

from cogs.error_handler import BaseModal, BaseView
from modules.configs._common import check_guild_perms
from modules.member_applications import (
    CHANNEL_TYPES,
    DISCORD_FEATURE,
    MAX_PING_ROLES,
    MAX_PRESET_REASON_LENGTH,
    MAX_PRESET_REASONS,
    MAX_REVIEWER_ROLES,
    MODULE_ID,
    inline_code,
    normalize_reasons,
)
from utils.emojis import (
    BACK, DELETE, DONE, EDIT, SAVE, SHAPES, UNDONE, WARNING,
)
from utils.i18n import i18n, t

logger = logging.getLogger("moddy.modules.member_applications_config")

_CID_CHANNEL = "moddy:member_apps:config:channel"
_CID_PING_ROLES = "moddy:member_apps:config:ping_roles"
_CID_REVIEWER_ROLES = "moddy:member_apps:config:reviewer_roles"
_CID_REASONS = "moddy:member_apps:config:reasons"
_CID_BACK = "moddy:member_apps:config:back"
_CID_SAVE = "moddy:member_apps:config:save"
_CID_CANCEL = "moddy:member_apps:config:cancel"
_CID_DELETE = "moddy:member_apps:config:delete"
_CID_REASONS_INPUT = "moddy:member_apps:config:reasons_input"

_P = "modules.member_applications.config"


def _default_config(bot, guild_id) -> Dict[str, Any]:
    from modules.member_applications import MemberApplicationsModule
    return MemberApplicationsModule(bot, guild_id).get_default_config()


# A preset reason as the panel lists it. normalize_reasons() guarantees a
# reason holds no backtick, so the inline code span round-trips exactly.
_REASON_LINE = re.compile(r"^- `([^`]+)`$")


def draft_from_message(message: Any) -> Optional[Dict[str, Any]]:
    """The configuration exactly as the panel message shows it, or ``None``.

    Unsaved changes are never kept in memory alone: every change re-renders the
    panel, so the message *is* the draft. Reading it back makes Save correct on
    any click — after a restart, on a registration shell, or when a different
    bot process answers the interaction — instead of silently re-saving the
    stored configuration and reporting success.
    """
    components = getattr(message, "components", None)
    if not components:
        return None

    selects: Dict[str, List[int]] = {}
    reasons: List[str] = []

    def walk(items):
        for component in items or []:
            custom_id = getattr(component, "custom_id", None)
            if custom_id in (_CID_CHANNEL, _CID_PING_ROLES, _CID_REVIEWER_ROLES):
                selects[custom_id] = [int(v.id) for v in (getattr(component, "default_values", None) or [])]
            content = getattr(component, "content", None)
            if isinstance(content, str):
                for line in content.splitlines():
                    match = _REASON_LINE.match(line.strip())
                    if match:
                        reasons.append(match.group(1))
            walk(getattr(component, "children", None))
            accessory = getattr(component, "accessory", None)
            if accessory is not None:
                walk([accessory])

    walk(components)
    if _CID_CHANNEL not in selects:
        return None  # not this panel
    channel = selects[_CID_CHANNEL]
    return {
        "channel_id": channel[0] if channel else None,
        "ping_role_ids": selects.get(_CID_PING_ROLES, [])[:MAX_PING_ROLES],
        "reviewer_role_ids": selects.get(_CID_REVIEWER_ROLES, [])[:MAX_REVIEWER_ROLES],
        "rejection_reasons": normalize_reasons(reasons),
    }


# =========================================================================== #
# Modal V2 — preset rejection reasons
# =========================================================================== #
class PresetReasonsModal(BaseModal):
    """One reason per line; blank lines and duplicates are dropped.

    A plain paragraph field rather than one input per reason: a Modal V2 holds
    five components at most, and a server wanting ten reasons should not have
    to open the form twice.
    """

    def __init__(self, locale: str, reasons: List[str], callback_func):
        super().__init__(title=t(f"{_P}.reasons.modal_title", locale=locale)[:45], timeout=None)
        self.callback_func = callback_func

        self.add_item(ui.TextDisplay(
            t(f"{_P}.reasons.modal_help", locale=locale,
              max=MAX_PRESET_REASONS, length=MAX_PRESET_REASON_LENGTH)
        ))
        self.reasons_input = ui.TextInput(
            style=discord.TextStyle.paragraph,
            default="\n".join(reasons) or None,
            placeholder=t(f"{_P}.reasons.modal_placeholder", locale=locale)[:100],
            max_length=MAX_PRESET_REASONS * (MAX_PRESET_REASON_LENGTH + 1),
            required=False,
            custom_id=_CID_REASONS_INPUT,
        )
        self.add_item(ui.Label(
            text=t(f"{_P}.reasons.modal_label", locale=locale)[:45],
            description=t(f"{_P}.reasons.modal_description", locale=locale)[:100],
            component=self.reasons_input,
        ))

    async def on_submit(self, interaction: discord.Interaction):
        await self.callback_func(interaction, normalize_reasons(self.reasons_input.value or ""))


# =========================================================================== #
# Panel
# =========================================================================== #
class MemberApplicationsConfigView(BaseView):
    """Member Applications configuration panel.

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

        default_config = _default_config(bot, guild_id)
        # `channel_id` is required to save, so it marks a stored configuration
        # — and a stored configuration is the module being active.
        if current_config and current_config.get("channel_id") is not None:
            self.current_config = default_config.copy()
            self.current_config.update(current_config)
            self.has_existing_config = True
        else:
            self.current_config = default_config
            self.has_existing_config = False

        self.working_config = dict(self.current_config)
        self.has_changes = False

        self._build_view()

    # ----------------------------------------------------------------- #
    # Rendering
    # ----------------------------------------------------------------- #

    def _discord_status_line(self) -> Optional[str]:
        """Whether the server has applications switched on in Discord.

        Shown because nothing else on the panel says it, and a configured
        module on a server without the feature would sit there silently.
        """
        guild = self.bot.get_guild(self.guild_id) if self.bot and self.guild_id else None
        if guild is None:
            return None
        if DISCORD_FEATURE in guild.features:
            return f"{DONE} {t(f'{_P}.discord.enabled', locale=self.locale)}"
        return f"{WARNING} {t(f'{_P}.discord.disabled', locale=self.locale)}"

    def _build_view(self):
        self.clear_items()
        loc = self.locale
        container = ui.Container()

        container.add_item(ui.TextDisplay(f"### {SHAPES} {t(f'{_P}.title', locale=loc)}"))
        container.add_item(ui.TextDisplay(t(f"{_P}.description", locale=loc)))
        discord_line = self._discord_status_line()
        if discord_line:
            container.add_item(ui.TextDisplay(f"-# {discord_line}"))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # --- Review channel ------------------------------------------------ #
        container.add_item(ui.TextDisplay(
            f"**{t(f'{_P}.channel.section_title', locale=loc)}**\n"
            f"-# {t(f'{_P}.channel.section_description', locale=loc)}"
        ))
        channel_select = ui.ChannelSelect(
            placeholder=t(f"{_P}.channel.placeholder", locale=loc),
            channel_types=CHANNEL_TYPES,
            min_values=0, max_values=1,
            custom_id=_CID_CHANNEL,
        )
        channel_id = self.working_config.get("channel_id")
        if channel_id:
            channel_select.default_values = [discord.Object(id=int(channel_id))]
        channel_select.callback = self.on_channel_select
        container.add_item(ui.ActionRow(channel_select))

        # --- Ping roles ---------------------------------------------------- #
        container.add_item(ui.TextDisplay(
            f"**{t(f'{_P}.ping_roles.section_title', locale=loc)}**\n"
            f"-# {t(f'{_P}.ping_roles.section_description', locale=loc, max=MAX_PING_ROLES)}"
        ))
        ping_select = ui.RoleSelect(
            placeholder=t(f"{_P}.ping_roles.placeholder", locale=loc),
            min_values=0, max_values=MAX_PING_ROLES,
            custom_id=_CID_PING_ROLES,
        )
        ping_ids = self.working_config.get("ping_role_ids") or []
        if ping_ids:
            ping_select.default_values = [discord.Object(id=int(r)) for r in ping_ids]
        ping_select.callback = self.on_ping_roles_select
        container.add_item(ui.ActionRow(ping_select))

        # --- Reviewer roles ------------------------------------------------ #
        container.add_item(ui.TextDisplay(
            f"**{t(f'{_P}.reviewer_roles.section_title', locale=loc)}**\n"
            f"-# {t(f'{_P}.reviewer_roles.section_description', locale=loc)}"
        ))
        reviewer_select = ui.RoleSelect(
            placeholder=t(f"{_P}.reviewer_roles.placeholder", locale=loc),
            min_values=0, max_values=MAX_REVIEWER_ROLES,
            custom_id=_CID_REVIEWER_ROLES,
        )
        reviewer_ids = self.working_config.get("reviewer_role_ids") or []
        if reviewer_ids:
            reviewer_select.default_values = [discord.Object(id=int(r)) for r in reviewer_ids]
        reviewer_select.callback = self.on_reviewer_roles_select
        container.add_item(ui.ActionRow(reviewer_select))

        # --- Preset rejection reasons (edited in a modal: listed here) ---- #
        reasons = normalize_reasons(self.working_config.get("rejection_reasons"))
        # One "- `reason`" line per reason: draft_from_message() reads them back.
        listed = ("\n".join(f"- {inline_code(r)}" for r in reasons)
                  if reasons else f"-# {t(f'{_P}.reasons.none', locale=loc)}")
        container.add_item(ui.TextDisplay(
            f"**{t(f'{_P}.reasons.section_title', locale=loc)}**\n"
            f"-# {t(f'{_P}.reasons.section_description', locale=loc)}\n{listed}"
        ))
        reasons_btn = ui.Button(
            label=t(f"{_P}.reasons.edit", locale=loc),
            style=discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(EDIT),
            custom_id=_CID_REASONS,
        )
        reasons_btn.callback = self.on_edit_reasons
        container.add_item(ui.ActionRow(reasons_btn))

        self.add_item(container)
        self._add_action_buttons()

    def _add_action_buttons(self):
        loc = self.locale
        button_row = ui.ActionRow()

        back_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(BACK),
            label=t("modules.config.buttons.back", locale=loc),
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
                label=t("modules.config.buttons.save", locale=loc),
                style=discord.ButtonStyle.success,
                custom_id=_CID_SAVE,
            )
            save_btn.callback = self.on_save
            button_row.add_item(save_btn)

            cancel_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(UNDONE),
                label=t("modules.config.buttons.cancel", locale=loc),
                style=discord.ButtonStyle.danger,
                custom_id=_CID_CANCEL,
            )
            cancel_btn.callback = self.on_cancel
            button_row.add_item(cancel_btn)

        if (not self.has_changes and self.has_existing_config) or is_shell:
            delete_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(DELETE),
                label=t("modules.config.buttons.delete", locale=loc),
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
        """The draft to mutate or save.

        1. This process rendered the panel: its in-memory draft.
        2. Otherwise (restart, registration shell, another bot process
           answering): what the panel message shows (``draft_from_message``).
        3. Only when the message cannot be read: the stored configuration.
        """
        if self._is_live_for(interaction):
            return dict(self.working_config)
        draft = draft_from_message(getattr(interaction, "message", None))
        if draft is not None:
            return draft

        bot = interaction.client
        config = _default_config(bot, interaction.guild_id)
        saved = await bot.module_manager.get_module_config(interaction.guild_id, MODULE_ID)
        if saved and saved.get("channel_id") is not None:
            config.update(saved)
        return config

    async def _rebuild(self, interaction: discord.Interaction,
                       working_config: Dict[str, Any],
                       has_changes: bool) -> "MemberApplicationsConfigView":
        """Always build a NEW instance: `self` may be the shared shell."""
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        saved = await bot.module_manager.get_module_config(interaction.guild_id, MODULE_ID)
        view = MemberApplicationsConfigView(
            bot, interaction.guild_id, interaction.user.id, locale, current_config=saved,
        )
        view.working_config = working_config
        view.has_changes = has_changes
        view._build_view()
        return view

    async def _update(self, interaction: discord.Interaction, key: str, value: Any):
        working_config = await self._fresh_working_config(interaction)
        working_config[key] = value
        view = await self._rebuild(interaction, working_config, has_changes=True)
        await interaction.response.edit_message(view=view)

    # ----------------------------------------------------------------- #
    # Callbacks
    # ----------------------------------------------------------------- #

    async def on_channel_select(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        values = interaction.data.get("values") or []
        await self._update(interaction, "channel_id", int(values[0]) if values else None)

    async def on_ping_roles_select(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        values = interaction.data.get("values") or []
        await self._update(interaction, "ping_role_ids", [int(v) for v in values][:MAX_PING_ROLES])

    async def on_reviewer_roles_select(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        values = interaction.data.get("values") or []
        await self._update(interaction, "reviewer_role_ids",
                           [int(v) for v in values][:MAX_REVIEWER_ROLES])

    async def on_edit_reasons(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        working_config = await self._fresh_working_config(interaction)
        locale = i18n.get_user_locale(interaction)

        async def on_reasons(modal_interaction: discord.Interaction, reasons: List[str]):
            if not await check_guild_perms(modal_interaction):
                return
            working_config["rejection_reasons"] = reasons
            view = await self._rebuild(modal_interaction, working_config, has_changes=True)
            await modal_interaction.response.edit_message(view=view)

        await interaction.response.send_modal(PresetReasonsModal(
            locale, normalize_reasons(working_config.get("rejection_reasons")), on_reasons,
        ))

    # --- action buttons ------------------------------------------------ #

    async def on_back(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        from cogs.config import ConfigMainView
        main_view = ConfigMainView(
            interaction.client, interaction.guild_id, interaction.user.id,
            i18n.get_user_locale(interaction),
        )
        await interaction.response.edit_message(view=main_view)

    async def on_save(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await interaction.response.defer()

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        working_config = await self._fresh_working_config(interaction)
        working_config["rejection_reasons"] = normalize_reasons(
            working_config.get("rejection_reasons"))

        success, error_msg = await bot.module_manager.save_module_config(
            interaction.guild_id, MODULE_ID, working_config, actor_id=interaction.user.id,
        )
        if success:
            view = MemberApplicationsConfigView(
                bot, interaction.guild_id, interaction.user.id, locale,
                current_config=working_config,
            )
            await interaction.followup.send(
                t("modules.config.save.success", locale=locale), ephemeral=True)
            await interaction.edit_original_response(view=view)
        else:
            await interaction.followup.send(
                t("modules.config.save.error", locale=locale, error=error_msg), ephemeral=True)

    async def on_cancel(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        bot = interaction.client
        saved = await bot.module_manager.get_module_config(interaction.guild_id, MODULE_ID)
        view = MemberApplicationsConfigView(
            bot, interaction.guild_id, interaction.user.id,
            i18n.get_user_locale(interaction), current_config=saved,
        )
        await interaction.response.edit_message(view=view)

    async def on_delete(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await interaction.response.defer()

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        if await bot.module_manager.delete_module_config(interaction.guild_id, MODULE_ID):
            view = MemberApplicationsConfigView(
                bot, interaction.guild_id, interaction.user.id, locale, current_config=None,
            )
            await interaction.followup.send(
                t("modules.config.delete.success", locale=locale), ephemeral=True)
            await interaction.edit_original_response(view=view)
        else:
            await interaction.followup.send(
                t("modules.config.delete.error", locale=locale), ephemeral=True)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_guild_perms(interaction)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_view(cls())
