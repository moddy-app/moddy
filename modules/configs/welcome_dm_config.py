"""
Configuration UI for the Welcome DM module (`/config`).

Layout mirrors the Welcome Channel panel — a guild can configure several welcome
DMs (max ``MAX_WELCOME_DMS``, all users combined), so the panel is a list +
add/manage flow rather than a single form:

  Main panel ──► Add message (Modal V2) ──► saved
            └──► Manage message ──► edit message / pause / remove

There is no channel to pick for a DM, so "add" is the customization modal
itself: the full text, the placeholder cheat-sheet and the accent colour in one
Modal V2 (see docs/MODALS_V2.md), submitted straight into the message list.

Actions are applied immediately (no Save/Cancel batching): each one writes the
whole message list back through ``module_manager.save_module_config`` so the
module instance is revalidated and reloaded on every change.

Persistence (see docs/PERSISTENT_VIEWS.md): every interactive component uses a
stable, namespaced ``custom_id`` and the views never time out. Callbacks
re-derive their context from ``interaction`` + the DB.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import discord
from discord import ui

from cogs.error_handler import BaseView, BaseModal
from modules.configs._common import check_guild_perms
from modules.welcome_dm import (
    DEFAULT_ACCENT_COLOR,
    MAX_MESSAGE_LENGTH,
    MAX_WELCOME_DMS,
    PLACEHOLDERS,
    entry_accent_color,
    get_default_message,
    new_message_id,
    normalize_config,
)
from utils.emojis import (
    WAVING_HAND, ADD, BACK, EDIT, DELETE, INFO, WARNING, PAUSE, PLAY,
)
from utils.i18n import i18n, t
from utils.interaction_response import safe_defer

logger = logging.getLogger('moddy.modules.welcome_dm_config')

_MODULE_ID = 'welcome_dm'

# --------------------------------------------------------------------------- #
# Namespaced custom_id constants (persistent dispatch).
# Format: moddy:welcomedm:<view>:<action>. Guild context is re-derived from
# ``interaction.guild_id`` so the ids stay static (one shell, all guilds).
# --------------------------------------------------------------------------- #
_CID_MAIN_ADD = "moddy:welcomedm:main:add"
_CID_MAIN_MANAGE = "moddy:welcomedm:main:manage"
_CID_MAIN_BACK = "moddy:welcomedm:main:back"

_CID_MANAGE_EDIT = "moddy:welcomedm:manage:edit"
_CID_MANAGE_TOGGLE = "moddy:welcomedm:manage:toggle"
_CID_MANAGE_REMOVE = "moddy:welcomedm:manage:remove"
_CID_MANAGE_BACK = "moddy:welcomedm:manage:back"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _parse_hex_color(value: Optional[str]) -> Optional[int]:
    """Parse ``#RRGGBB`` / ``RRGGBB`` into an int, or ``None`` if invalid/empty."""
    if not value:
        return None
    s = value.strip().lstrip("#")
    if len(s) == 6 and all(c in "0123456789abcdefABCDEF" for c in s):
        return int(s, 16)
    return None


def _color_to_hex(value: int) -> str:
    return f"#{value:06X}"


def _placeholder_help(locale: str) -> str:
    """Human-readable list of the placeholders available in a welcome DM.

    Fetched WITHOUT kwargs so the literal ``{placeholder}`` braces survive.
    """
    label_keys = {
        "{server}": "modules.welcome_dm.customize.ph_server",
        "{user}": "modules.welcome_dm.customize.ph_user",
        "{display_name}": "modules.welcome_dm.customize.ph_display_name",
        "{username}": "modules.welcome_dm.customize.ph_username",
        "{member_count}": "modules.welcome_dm.customize.ph_member_count",
        "{timestamp}": "modules.welcome_dm.customize.ph_timestamp",
    }
    lines = [t('modules.welcome_dm.customize.placeholders_header', locale=locale)]
    for ph in PLACEHOLDERS:
        lines.append(f"`{ph}` — {t(label_keys[ph], locale=locale)}")
    return "\n".join(lines)


async def _load_messages(bot, guild_id: int) -> List[Dict[str, Any]]:
    """Current welcome DMs of a guild (v1 configs migrated on read)."""
    saved = await bot.module_manager.get_module_config(guild_id, _MODULE_ID)
    return normalize_config(saved)['messages']


async def _save_messages(bot, guild_id: int,
                         messages: List[Dict[str, Any]],
                         actor_id: Optional[int] = None) -> tuple[bool, Optional[str]]:
    """Persist the whole message list (revalidates + reloads the module)."""
    return await bot.module_manager.save_module_config(
        guild_id, _MODULE_ID, normalize_config({'messages': messages}),
        actor_id=actor_id,
    )


async def _render_main(interaction: discord.Interaction) -> None:
    """(Re)build and show the main panel from a live interaction."""
    bot = interaction.client
    locale = i18n.get_user_locale(interaction)
    # Acknowledge first: rebuilding the panel reads the DB, and an interaction
    # left unacknowledged for 3s dies with Discord's own "did not respond".
    await safe_defer(interaction, thinking=False)
    view = await WelcomeDmConfigView.create(
        bot, interaction.guild_id, interaction.user.id, locale
    )
    await interaction.edit_original_response(view=view)


# =========================================================================== #
# Modal (V2)
# =========================================================================== #
class WelcomeDmMessageModal(BaseModal):
    """Customizes the welcome DM text and its accent colour."""

    def __init__(self, locale: str, current_message: Optional[str],
                 current_color: Optional[int], callback_func):
        super().__init__(
            title=t('modules.welcome_dm.customize.modal_title', locale=locale),
            timeout=None,
        )
        self.locale = locale
        self.callback_func = callback_func

        # 1. The message itself — pre-filled with the current text, or the
        #    translated default so the user can edit it rather than start blank.
        self.message_input = ui.TextInput(
            style=discord.TextStyle.paragraph,
            default=current_message or get_default_message(locale),
            max_length=MAX_MESSAGE_LENGTH,
            required=True,
        )
        self.add_item(ui.Label(
            text=t('modules.welcome_dm.customize.message_label', locale=locale),
            description=t('modules.welcome_dm.customize.message_description', locale=locale),
            component=self.message_input,
        ))

        # 2. Placeholder cheat-sheet (static text).
        self.add_item(ui.TextDisplay(_placeholder_help(locale)))

        # 3. Accent colour (hex).
        self.color_input = ui.TextInput(
            style=discord.TextStyle.short,
            default=_color_to_hex(current_color if current_color is not None else DEFAULT_ACCENT_COLOR),
            min_length=0,
            max_length=7,
            required=False,
        )
        self.add_item(ui.Label(
            text=t('modules.welcome_dm.customize.color_label', locale=locale),
            description=t('modules.welcome_dm.customize.color_description', locale=locale),
            component=self.color_input,
        ))

    async def on_submit(self, interaction: discord.Interaction):
        await self.callback_func(
            interaction,
            self.message_input.value.strip(),
            _parse_hex_color(self.color_input.value),
        )


# =========================================================================== #
# Main panel
# =========================================================================== #
class WelcomeDmConfigView(BaseView):
    """Lists the guild's welcome DMs and opens the add/manage flows.

    Persistent: yes. Auth: Manage Server in the guild (re-checked on every
    click via check_guild_perms — NOT via a stored user_id, which cannot
    survive a restarted shell).
    """

    __persistent__ = True

    def __init__(self, bot=None, guild_id: Optional[int] = None, user_id: Optional[int] = None,
                 locale: str = "en-US", messages: Optional[List[Dict[str, Any]]] = None):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale
        self.messages = messages or []

        self._build_view()

    @classmethod
    async def create(cls, bot, guild_id: int, user_id: int, locale: str):
        """Async factory: load the guild's messages before building the panel."""
        messages = await _load_messages(bot, guild_id)
        return cls(bot, guild_id, user_id, locale, messages)

    # -- construction ----------------------------------------------------- #
    def _build_view(self):
        self.clear_items()
        container = ui.Container()

        container.add_item(ui.TextDisplay(
            f"### {WAVING_HAND} {t('modules.welcome_dm.config.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t('modules.welcome_dm.config.description', locale=self.locale)
        ))
        container.add_item(ui.TextDisplay(
            f"-# {t('modules.welcome_dm.config.limit', locale=self.locale, max=MAX_WELCOME_DMS)}"
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        if self.messages:
            container.add_item(ui.TextDisplay(
                f"**{t('modules.welcome_dm.config.list.title', locale=self.locale)}**\n"
                f"-# {t('modules.welcome_dm.config.list.count', locale=self.locale, count=len(self.messages), max=MAX_WELCOME_DMS)}"
            ))
            for index, entry in enumerate(self.messages, start=1):
                container.add_item(ui.TextDisplay(self._render_entry(index, entry)))

            manage_row = ui.ActionRow()
            options = [
                discord.SelectOption(
                    label=self._entry_label(index)[:100],
                    value=entry['id'],
                    description=self._entry_preview(entry)[:100] or None,
                )
                for index, entry in enumerate(self.messages[:MAX_WELCOME_DMS], start=1)
            ]
            manage_select = ui.Select(
                placeholder=t('modules.welcome_dm.config.manage_placeholder', locale=self.locale),
                options=options, min_values=1, max_values=1, custom_id=_CID_MAIN_MANAGE,
            )
            manage_select.callback = self.on_manage_select
            manage_row.add_item(manage_select)
            container.add_item(manage_row)
        else:
            container.add_item(ui.TextDisplay(
                f"{INFO} {t('modules.welcome_dm.config.list.empty', locale=self.locale)}"
            ))
            # Shell (no live bot) still needs the select registered for dispatch.
            if self.bot is None:
                manage_row = ui.ActionRow()
                shell_select = ui.Select(
                    placeholder=t('modules.welcome_dm.config.manage_placeholder', locale=self.locale),
                    options=[discord.SelectOption(label="—", value="none")],
                    min_values=1, max_values=1, custom_id=_CID_MAIN_MANAGE, disabled=True,
                )
                shell_select.callback = self.on_manage_select
                manage_row.add_item(shell_select)
                container.add_item(manage_row)

        self.add_item(container)

        # Action buttons.
        button_row = ui.ActionRow()
        back_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(BACK),
            label=t('modules.config.buttons.back', locale=self.locale),
            style=discord.ButtonStyle.secondary, custom_id=_CID_MAIN_BACK,
        )
        back_btn.callback = self.on_back
        button_row.add_item(back_btn)

        add_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(ADD),
            label=t('modules.welcome_dm.buttons.add', locale=self.locale),
            style=discord.ButtonStyle.success, custom_id=_CID_MAIN_ADD,
            disabled=len(self.messages) >= MAX_WELCOME_DMS,
        )
        add_btn.callback = self.on_add
        button_row.add_item(add_btn)
        self.add_item(button_row)

    def _entry_label(self, index: int) -> str:
        """A DM entry has no channel — number them in configuration order."""
        return t('modules.welcome_dm.config.list.entry_label',
                 locale=self.locale, index=index)

    def _entry_preview(self, entry: Dict[str, Any]) -> str:
        """First line of the message, flattened for a select description."""
        text = (entry.get('message') or '').replace('\n', ' ').strip()
        return text[:100]

    def _render_entry(self, index: int, entry: Dict[str, Any]) -> str:
        line = f"{WAVING_HAND} **{self._entry_label(index)}**"
        extras = [f"`{_color_to_hex(entry_accent_color(entry))}`"]
        if not entry.get('enabled', True):
            extras.append(t('modules.welcome_dm.config.list.paused', locale=self.locale))
        line += f"\n-# {' · '.join(extras)}"
        preview = self._entry_preview(entry)
        if preview:
            line += f"\n-# {discord.utils.escape_markdown(preview)}"
        return line

    # -- callbacks (re-derive context from interaction) ------------------- #
    async def on_add(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        messages = await _load_messages(bot, interaction.guild_id)
        if len(messages) >= MAX_WELCOME_DMS:
            await interaction.response.send_message(
                t('modules.welcome_dm.errors.too_many', locale=locale, max=MAX_WELCOME_DMS),
                ephemeral=True,
            )
            return
        modal = WelcomeDmMessageModal(locale, None, None, self._on_added)
        modal.bot = bot
        await interaction.response.send_modal(modal)

    async def _on_added(self, interaction: discord.Interaction, message: str,
                        color: Optional[int]):
        """Append the new entry, then re-render the main panel."""
        await interaction.response.defer()

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        messages = await _load_messages(bot, interaction.guild_id)

        if len(messages) >= MAX_WELCOME_DMS:
            await interaction.followup.send(
                t('modules.welcome_dm.errors.too_many', locale=locale, max=MAX_WELCOME_DMS),
                ephemeral=True,
            )
            return

        messages.append({
            'id': new_message_id(),
            'message': message or get_default_message(locale),
            'accent_color': color,
            'enabled': True,
            'created_by': interaction.user.id,
            'created_at': datetime.now(timezone.utc).isoformat(),
        })

        success, error = await _save_messages(bot, interaction.guild_id, messages,
                                              interaction.user.id)
        if success:
            await _render_main(interaction)
            await interaction.followup.send(
                t('modules.welcome_dm.add.success', locale=locale), ephemeral=True,
            )
        else:
            await interaction.followup.send(
                t('modules.config.save.error', locale=locale, error=error or ''), ephemeral=True,
            )

    async def on_manage_select(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        entry_id = interaction.data['values'][0]
        # The entry list is read from the DB below: acknowledge first.
        await safe_defer(interaction, thinking=False)
        messages = await _load_messages(bot, interaction.guild_id)
        entry = next((m for m in messages if m['id'] == entry_id), None)
        if not entry:
            await _render_main(interaction)
            return
        index = messages.index(entry) + 1
        manage_view = ManageWelcomeDmView(bot, interaction.guild_id, locale, entry, index)
        await interaction.edit_original_response(view=manage_view)

    async def on_back(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        from cogs.config import ConfigMainView
        locale = i18n.get_user_locale(interaction)
        main_view = ConfigMainView(interaction.client, interaction.guild_id, interaction.user.id, locale)
        await interaction.response.edit_message(view=main_view)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_guild_perms(interaction)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_view(cls())


# =========================================================================== #
# Manage a welcome DM
# =========================================================================== #
class ManageWelcomeDmView(BaseView):
    """Edit the message, pause or remove one welcome DM.

    Auth: Manage Server.

    Persistent: yes. ``self.entry`` (the message shown) is not reconstructible
    from a bare custom_id — same accepted-loss UX as ManageWelcomeMessageView:
    the shell renders an empty card and every callback re-checks
    check_guild_perms(interaction), so a click on a stale shell is safe, it
    just has nothing to act on until the user re-opens the manage panel.
    """

    __persistent__ = True

    def __init__(self, bot=None, guild_id: Optional[int] = None, locale: str = "en-US",
                 entry: Optional[Dict[str, Any]] = None, index: int = 1):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.locale = locale
        self.entry = entry or {}
        self.index = index

        self._build_view()

    def _build_view(self):
        self.clear_items()
        container = ui.Container(
            accent_colour=discord.Colour(entry_accent_color(self.entry)),
        )

        container.add_item(ui.TextDisplay(
            f"### {WAVING_HAND} {t('modules.welcome_dm.manage.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            f"-# {t('modules.welcome_dm.config.list.entry_label', locale=self.locale, index=self.index)} · "
            f"`{_color_to_hex(entry_accent_color(self.entry))}`"
        ))

        if not self.entry.get('enabled', True):
            container.add_item(ui.TextDisplay(
                f"{WARNING} {t('modules.welcome_dm.manage.paused_notice', locale=self.locale)}"
            ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Current message preview.
        preview = (self.entry.get('message') or '').strip()
        if preview:
            container.add_item(ui.TextDisplay(
                f"**{t('modules.welcome_dm.manage.preview_title', locale=self.locale)}**\n"
                f"-# {t('modules.welcome_dm.manage.preview_description', locale=self.locale)}"
            ))
            container.add_item(ui.TextDisplay(f"```\n{preview[:900]}\n```"))

        # Message customization.
        container.add_item(ui.TextDisplay(
            f"**{t('modules.welcome_dm.customize.title', locale=self.locale)}**\n"
            f"-# {t('modules.welcome_dm.customize.section_description', locale=self.locale)}"
        ))
        msg_row = ui.ActionRow()
        msg_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(EDIT),
            label=t('modules.welcome_dm.customize.button', locale=self.locale),
            style=discord.ButtonStyle.primary, custom_id=_CID_MANAGE_EDIT,
        )
        msg_btn.callback = self.on_edit_message
        msg_row.add_item(msg_btn)
        container.add_item(msg_row)

        self.add_item(container)

        # Action buttons.
        button_row = ui.ActionRow()
        back_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(BACK),
            label=t('modules.config.buttons.back', locale=self.locale),
            style=discord.ButtonStyle.secondary, custom_id=_CID_MANAGE_BACK,
        )
        back_btn.callback = self.on_back
        button_row.add_item(back_btn)

        enabled = self.entry.get('enabled', True)
        toggle_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(PAUSE if enabled else PLAY),
            label=t(
                'modules.welcome_dm.manage.pause' if enabled
                else 'modules.welcome_dm.manage.resume',
                locale=self.locale,
            ),
            style=discord.ButtonStyle.secondary, custom_id=_CID_MANAGE_TOGGLE,
        )
        toggle_btn.callback = self.on_toggle
        button_row.add_item(toggle_btn)

        remove_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(DELETE),
            label=t('modules.welcome_dm.manage.remove', locale=self.locale),
            style=discord.ButtonStyle.danger, custom_id=_CID_MANAGE_REMOVE,
        )
        remove_btn.callback = self.on_remove
        button_row.add_item(remove_btn)
        self.add_item(button_row)

    # -- persistence helpers ---------------------------------------------- #
    async def _apply(self, interaction: discord.Interaction,
                     **fields) -> Optional[tuple[bool, Optional[str]]]:
        """Write ``fields`` onto this entry inside the guild's message list.

        Returns ``None`` when the entry no longer exists (stale shell, or it was
        removed from another panel) — the caller falls back to the main panel.
        """
        bot = interaction.client
        entry_id = self.entry.get('id')
        # A read plus a write: acknowledge before either can burn the 3s window.
        await safe_defer(interaction, thinking=False)
        messages = await _load_messages(bot, interaction.guild_id)
        target = next((m for m in messages if m['id'] == entry_id), None)
        if not target:
            return None
        target.update(fields)
        success, error = await _save_messages(bot, interaction.guild_id, messages,
                                              interaction.user.id)
        if success:
            self.entry = target
            self.index = messages.index(target) + 1
        return success, error

    async def _refresh(self, interaction: discord.Interaction,
                       result: Optional[tuple[bool, Optional[str]]]) -> None:
        """Re-render the manage card, or report why the write did not happen."""
        locale = i18n.get_user_locale(interaction)
        if result is None:
            await _render_main(interaction)
            return
        success, error = result
        if not success:
            await interaction.followup.send(
                t('modules.config.save.error', locale=locale, error=error or ''),
                ephemeral=True,
            )
            return
        view = ManageWelcomeDmView(interaction.client, interaction.guild_id, locale,
                                   self.entry, self.index)
        await interaction.edit_original_response(view=view)

    # -- callbacks -------------------------------------------------------- #
    async def on_edit_message(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        locale = i18n.get_user_locale(interaction)
        modal = WelcomeDmMessageModal(
            locale, self.entry.get('message'), self.entry.get('accent_color'),
            self._on_message_edited,
        )
        modal.bot = interaction.client
        await interaction.response.send_modal(modal)

    async def _on_message_edited(self, interaction: discord.Interaction, message: str,
                                 color: Optional[int]):
        await self._refresh(
            interaction, await self._apply(interaction, message=message, accent_color=color)
        )

    async def on_toggle(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await self._refresh(
            interaction,
            await self._apply(interaction, enabled=not self.entry.get('enabled', True)),
        )

    async def on_remove(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await interaction.response.defer()

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        entry_id = self.entry.get('id')
        messages = [m for m in await _load_messages(bot, interaction.guild_id)
                    if m['id'] != entry_id]
        success, error = await _save_messages(bot, interaction.guild_id, messages,
                                              interaction.user.id)

        await _render_main(interaction)
        if success:
            await interaction.followup.send(
                t('modules.welcome_dm.manage.removed', locale=locale), ephemeral=True,
            )
        else:
            await interaction.followup.send(
                t('modules.config.save.error', locale=locale, error=error or ''), ephemeral=True,
            )

    async def on_back(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await _render_main(interaction)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_guild_perms(interaction)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_view(cls())
