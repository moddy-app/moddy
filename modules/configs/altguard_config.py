"""
Configuration UI for the AltGuard module.

Deliberately narrow: an admin picks *where* the gate lives (channel, both
roles, optional log channel). The panel wording itself is not configurable —
every server must state the same thing about the same data processing, so it
ships with the module — and the language it speaks is the *server* language,
set once in ``/config`` -> Server settings (``utils/guild_language.py``)
rather than here.

Saving always re-posts the panel (delete + send). That is what repairs a panel
someone deleted, and the only way a language or channel change reaches the
message.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import discord
from discord import ui

from cogs.error_handler import BaseView
from modules.altguard import MODULE_ID
from modules.configs._common import check_guild_perms
from utils.emojis import ALTGUARD, BACK, DELETE, SAVE, UNDONE
from utils.i18n import i18n, t
from utils.interaction_response import safe_defer

logger = logging.getLogger("moddy.modules.altguard_config")

_CID_CHANNEL = "moddy:altguard:config:channel"
_CID_UNVERIFIED = "moddy:altguard:config:unverified"
_CID_VERIFIED = "moddy:altguard:config:verified"
_CID_LOG_CHANNEL = "moddy:altguard:config:log_channel"
_CID_BACK = "moddy:altguard:config:back"
_CID_SAVE = "moddy:altguard:config:save"
_CID_CANCEL = "moddy:altguard:config:cancel"
_CID_DELETE = "moddy:altguard:config:delete"


class AltGuardConfigView(BaseView):
    """AltGuard configuration panel.

    Persistent: yes. Auth: Manage Server in the guild, re-checked on every
    click via check_guild_perms (never a stored user_id — a restarted shell
    has none).
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

        from modules.altguard import AltGuardModule
        default_config = AltGuardModule(bot, guild_id).get_default_config()

        # `channel_id` is always written on save, so it is a safe marker for
        # "this guild has a stored configuration" (unlike the computed
        # `enabled`, see docs/MODULE_SYSTEM.md).
        if current_config and current_config.get("channel_id") is not None:
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

    def _build_view(self) -> None:
        self.clear_items()

        container = ui.Container()

        container.add_item(ui.TextDisplay(
            f"### {ALTGUARD} {t('modules.altguard.config.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t('modules.altguard.config.description', locale=self.locale)
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # --- Verification channel -------------------------------------- #
        container.add_item(ui.TextDisplay(
            f"**{t('modules.altguard.config.channel.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.altguard.config.channel.section_description', locale=self.locale)}"
        ))
        container.add_item(self._channel_row(
            _CID_CHANNEL, self.working_config.get('channel_id'),
            'modules.altguard.config.channel.placeholder', self.on_channel_select,
            required=True,
        ))

        # --- Unverified role -------------------------------------------- #
        container.add_item(ui.TextDisplay(
            f"**{t('modules.altguard.config.unverified_role.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.altguard.config.unverified_role.section_description', locale=self.locale)}"
        ))
        container.add_item(self._role_row(
            _CID_UNVERIFIED, self.working_config.get('unverified_role_id'),
            'modules.altguard.config.unverified_role.placeholder', self.on_unverified_select,
        ))

        # --- Verified role ---------------------------------------------- #
        container.add_item(ui.TextDisplay(
            f"**{t('modules.altguard.config.verified_role.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.altguard.config.verified_role.section_description', locale=self.locale)}"
        ))
        container.add_item(self._role_row(
            _CID_VERIFIED, self.working_config.get('verified_role_id'),
            'modules.altguard.config.verified_role.placeholder', self.on_verified_select,
        ))

        # --- Log channel (optional) -------------------------------------- #
        container.add_item(ui.TextDisplay(
            f"**{t('modules.altguard.config.log_channel.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.altguard.config.log_channel.section_description', locale=self.locale)}"
        ))
        container.add_item(self._channel_row(
            _CID_LOG_CHANNEL, self.working_config.get('log_channel_id'),
            'modules.altguard.config.log_channel.placeholder', self.on_log_channel_select,
            required=False,
        ))

        # --- Permission reminder ------------------------------------------ #
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(
            f"-# {t('modules.altguard.config.permissions_hint', locale=self.locale)}"
        ))

        self.add_item(container)
        self._add_action_buttons()

    def _channel_row(self, custom_id: str, channel_id: Optional[int], placeholder_key: str,
                     callback, *, required: bool) -> ui.ActionRow:
        row = ui.ActionRow()
        select = ui.ChannelSelect(
            placeholder=t(placeholder_key, locale=self.locale),
            channel_types=[discord.ChannelType.text],
            min_values=1 if required else 0,
            max_values=1,
            custom_id=custom_id,
        )
        if channel_id and self.bot is not None:
            channel = self.bot.get_channel(int(channel_id))
            if channel is not None:
                select.default_values = [channel]
        select.callback = callback
        row.add_item(select)
        return row

    def _role_row(self, custom_id: str, role_id: Optional[int], placeholder_key: str,
                  callback) -> ui.ActionRow:
        row = ui.ActionRow()
        select = ui.RoleSelect(
            placeholder=t(placeholder_key, locale=self.locale),
            min_values=1, max_values=1, custom_id=custom_id,
        )
        if role_id and self.bot is not None and self.guild_id:
            guild = self.bot.get_guild(self.guild_id)
            role = guild.get_role(int(role_id)) if guild else None
            if role is not None:
                select.default_values = [role]
        select.callback = callback
        row.add_item(select)
        return row

    def _add_action_buttons(self) -> None:
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
        from modules.altguard import AltGuardModule
        config = AltGuardModule(bot, interaction.guild_id).get_default_config()
        saved = await bot.module_manager.get_module_config(interaction.guild_id, MODULE_ID)
        if saved and saved.get("channel_id") is not None:
            config.update(saved)
        return config

    async def _rebuild(self, interaction: discord.Interaction,
                       working_config: Dict[str, Any],
                       has_changes: bool) -> "AltGuardConfigView":
        """Always build a NEW instance: `self` may be the shared shell."""
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        saved = await bot.module_manager.get_module_config(interaction.guild_id, MODULE_ID)
        view = AltGuardConfigView(
            bot, interaction.guild_id, interaction.user.id, locale, current_config=saved,
        )
        view.working_config = working_config
        view.has_changes = has_changes
        view._build_view()
        return view

    # ----------------------------------------------------------------- #
    # Callbacks
    # ----------------------------------------------------------------- #

    async def _set_and_refresh(self, interaction: discord.Interaction,
                               key: str, value: Any) -> None:
        # Reads the stored config twice (fresh copy + rebuild): acknowledge
        # before the first round-trip.
        await safe_defer(interaction, thinking=False)
        working_config = await self._fresh_working_config(interaction)
        working_config[key] = value
        view = await self._rebuild(interaction, working_config, has_changes=True)
        await interaction.edit_original_response(view=view)

    @staticmethod
    def _first_id(interaction: discord.Interaction) -> Optional[int]:
        values: List[str] = interaction.data.get("values") or []
        return int(values[0]) if values else None

    async def on_channel_select(self, interaction: discord.Interaction) -> None:
        if not await check_guild_perms(interaction):
            return
        await self._set_and_refresh(interaction, 'channel_id', self._first_id(interaction))

    async def on_unverified_select(self, interaction: discord.Interaction) -> None:
        if not await check_guild_perms(interaction):
            return
        await self._set_and_refresh(interaction, 'unverified_role_id', self._first_id(interaction))

    async def on_verified_select(self, interaction: discord.Interaction) -> None:
        if not await check_guild_perms(interaction):
            return
        await self._set_and_refresh(interaction, 'verified_role_id', self._first_id(interaction))

    async def on_log_channel_select(self, interaction: discord.Interaction) -> None:
        if not await check_guild_perms(interaction):
            return
        await self._set_and_refresh(interaction, 'log_channel_id', self._first_id(interaction))

    # --- action buttons ------------------------------------------------ #

    async def on_back(self, interaction: discord.Interaction) -> None:
        if not await check_guild_perms(interaction):
            return

        from cogs.config import ConfigMainView
        locale = i18n.get_user_locale(interaction)
        main_view = ConfigMainView(
            interaction.client, interaction.guild_id, interaction.user.id, locale,
        )
        await interaction.response.edit_message(view=main_view)

    async def on_save(self, interaction: discord.Interaction) -> None:
        if not await check_guild_perms(interaction):
            return

        await interaction.response.defer()

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        working_config = await self._fresh_working_config(interaction)

        success, error_msg = await bot.module_manager.save_module_config(
            interaction.guild_id, MODULE_ID, working_config, actor_id=interaction.user.id,
        )

        if not success:
            await interaction.followup.send(
                t('modules.config.save.error', locale=locale, error=error_msg), ephemeral=True,
            )
            return

        # Re-post the panel with the saved settings. The module owns the
        # delete-then-send, and writes back the new message id itself.
        module = await bot.module_manager.get_module_instance(interaction.guild_id, MODULE_ID)
        posted = await module.refresh_panel() if module else None

        # Close the gate on every channel. Doing this by hand is how a server
        # ends up with one channel left open to unverified members, so the
        # save applies it rather than telling the admin to.
        recap = await module.sync_channel_permissions() if module else {}

        saved = await bot.module_manager.get_module_config(interaction.guild_id, MODULE_ID)
        view = AltGuardConfigView(
            bot, interaction.guild_id, interaction.user.id, locale, current_config=saved,
        )
        message_key = ('modules.altguard.config.saved_with_panel' if posted
                       else 'modules.altguard.config.saved_without_panel')
        message = t(message_key, locale=locale)

        if recap.get("updated"):
            message += "\n" + t('modules.altguard.config.permissions_synced',
                                locale=locale, count=recap["updated"])
        if recap.get("failed"):
            message += "\n" + t('modules.altguard.config.permissions_failed',
                                locale=locale, count=recap["failed"])

        await interaction.followup.send(message, ephemeral=True)
        await interaction.edit_original_response(view=view)

    async def on_cancel(self, interaction: discord.Interaction) -> None:
        if not await check_guild_perms(interaction):
            return

        # The stored config is read below: acknowledge before the round-trip.
        await safe_defer(interaction, thinking=False)

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        saved = await bot.module_manager.get_module_config(interaction.guild_id, MODULE_ID)
        view = AltGuardConfigView(
            bot, interaction.guild_id, interaction.user.id, locale, current_config=saved,
        )
        await interaction.edit_original_response(view=view)

    async def on_delete(self, interaction: discord.Interaction) -> None:
        if not await check_guild_perms(interaction):
            return

        await interaction.response.defer()

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)

        # Take the panel down before dropping the config: afterwards the module
        # instance is gone and nothing knows which message to delete.
        module = await bot.module_manager.get_module_instance(interaction.guild_id, MODULE_ID)
        if module:
            await module.delete_panel()

        success = await bot.module_manager.delete_module_config(interaction.guild_id, MODULE_ID)

        if success:
            view = AltGuardConfigView(
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
