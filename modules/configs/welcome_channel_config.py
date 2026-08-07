"""
Configuration UI for the Welcome Channel module (`/config`).

Layout mirrors the Social Notifications panel — a guild can configure several
welcome messages (max ``MAX_WELCOME_MESSAGES``, all users combined), so the
panel is a list + add/manage flow rather than a single form:

  Main panel ──► Add message ──► (channel + message customization) ──► confirm
            └──► Manage message ──► change channel / edit message / pause / remove

Message customization goes through a **Modal V2** (see docs/MODALS_V2.md): the
full text, the placeholder cheat-sheet and the accent colour in one modal.

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
from modules.welcome_channel import (
    CHANNEL_TYPES,
    DEFAULT_ACCENT_COLOR,
    MAX_MESSAGE_LENGTH,
    MAX_WELCOME_MESSAGES,
    PLACEHOLDERS,
    entry_accent_color,
    get_default_message,
    new_message_id,
    normalize_config,
)
from utils.emojis import (
    WAVING_HAND, ADD, BACK, EDIT, DELETE, DONE, INFO, WARNING,
    REQUIRED_FIELDS, PAUSE, PLAY,
)
from utils.i18n import i18n, t

logger = logging.getLogger('moddy.modules.welcome_channel_config')

_MODULE_ID = 'welcome_channel'

# --------------------------------------------------------------------------- #
# Namespaced custom_id constants (persistent dispatch).
# Format: moddy:welcomechan:<view>:<action>. Guild context is re-derived from
# ``interaction.guild_id`` so the ids stay static (one shell, all guilds).
# --------------------------------------------------------------------------- #
_CID_MAIN_ADD = "moddy:welcomechan:main:add"
_CID_MAIN_MANAGE = "moddy:welcomechan:main:manage"
_CID_MAIN_BACK = "moddy:welcomechan:main:back"

_CID_ADD_CHANNEL = "moddy:welcomechan:add:channel"
_CID_ADD_CUSTOMIZE = "moddy:welcomechan:add:customize"
_CID_ADD_CONFIRM = "moddy:welcomechan:add:confirm"
_CID_ADD_BACK = "moddy:welcomechan:add:back"

_CID_MANAGE_CHANNEL = "moddy:welcomechan:manage:channel"
_CID_MANAGE_EDIT = "moddy:welcomechan:manage:edit"
_CID_MANAGE_TOGGLE = "moddy:welcomechan:manage:toggle"
_CID_MANAGE_REMOVE = "moddy:welcomechan:manage:remove"
_CID_MANAGE_BACK = "moddy:welcomechan:manage:back"


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
    """Human-readable list of the placeholders available in a welcome message.

    Fetched WITHOUT kwargs so the literal ``{placeholder}`` braces survive.
    """
    label_keys = {
        "{server}": "modules.welcome_channel.customize.ph_server",
        "{user}": "modules.welcome_channel.customize.ph_user",
        "{display_name}": "modules.welcome_channel.customize.ph_display_name",
        "{username}": "modules.welcome_channel.customize.ph_username",
        "{member_count}": "modules.welcome_channel.customize.ph_member_count",
        "{timestamp}": "modules.welcome_channel.customize.ph_timestamp",
    }
    lines = [t('modules.welcome_channel.customize.placeholders_header', locale=locale)]
    for ph in PLACEHOLDERS:
        lines.append(f"`{ph}` — {t(label_keys[ph], locale=locale)}")
    return "\n".join(lines)


async def _load_messages(bot, guild_id: int) -> List[Dict[str, Any]]:
    """Current welcome messages of a guild (v1 configs migrated on read)."""
    saved = await bot.module_manager.get_module_config(guild_id, _MODULE_ID)
    return normalize_config(saved)['messages']


async def _save_messages(bot, guild_id: int,
                         messages: List[Dict[str, Any]]) -> tuple[bool, Optional[str]]:
    """Persist the whole message list (revalidates + reloads the module)."""
    return await bot.module_manager.save_module_config(
        guild_id, _MODULE_ID, normalize_config({'messages': messages}),
    )


async def _render_main(interaction: discord.Interaction) -> None:
    """(Re)build and show the main panel from a live interaction."""
    bot = interaction.client
    locale = i18n.get_user_locale(interaction)
    view = await WelcomeChannelConfigView.create(
        bot, interaction.guild_id, interaction.user.id, locale
    )
    if interaction.response.is_done():
        await interaction.edit_original_response(view=view)
    else:
        await interaction.response.edit_message(view=view)


# =========================================================================== #
# Modal (V2)
# =========================================================================== #
class WelcomeMessageModal(BaseModal):
    """Customizes the welcome message text and its accent colour."""

    def __init__(self, locale: str, current_message: Optional[str],
                 current_color: Optional[int], callback_func):
        super().__init__(
            title=t('modules.welcome_channel.customize.modal_title', locale=locale),
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
            text=t('modules.welcome_channel.customize.message_label', locale=locale),
            description=t('modules.welcome_channel.customize.message_description', locale=locale),
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
            text=t('modules.welcome_channel.customize.color_label', locale=locale),
            description=t('modules.welcome_channel.customize.color_description', locale=locale),
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
class WelcomeChannelConfigView(BaseView):
    """Lists the guild's welcome messages and opens the add/manage flows.

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
            f"### {WAVING_HAND} {t('modules.welcome_channel.config.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t('modules.welcome_channel.config.description', locale=self.locale)
        ))
        container.add_item(ui.TextDisplay(
            f"-# {t('modules.welcome_channel.config.limit', locale=self.locale, max=MAX_WELCOME_MESSAGES)}"
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        if self.messages:
            container.add_item(ui.TextDisplay(
                f"**{t('modules.welcome_channel.config.list.title', locale=self.locale)}**\n"
                f"-# {t('modules.welcome_channel.config.list.count', locale=self.locale, count=len(self.messages), max=MAX_WELCOME_MESSAGES)}"
            ))
            for entry in self.messages:
                container.add_item(ui.TextDisplay(self._render_entry(entry)))

            manage_row = ui.ActionRow()
            options = [
                discord.SelectOption(
                    label=self._entry_label(entry)[:100],
                    value=entry['id'],
                    description=self._entry_preview(entry)[:100] or None,
                )
                for entry in self.messages[:MAX_WELCOME_MESSAGES]
            ]
            manage_select = ui.Select(
                placeholder=t('modules.welcome_channel.config.manage_placeholder', locale=self.locale),
                options=options, min_values=1, max_values=1, custom_id=_CID_MAIN_MANAGE,
            )
            manage_select.callback = self.on_manage_select
            manage_row.add_item(manage_select)
            container.add_item(manage_row)
        else:
            container.add_item(ui.TextDisplay(
                f"{INFO} {t('modules.welcome_channel.config.list.empty', locale=self.locale)}"
            ))
            # Shell (no live bot) still needs the select registered for dispatch.
            if self.bot is None:
                manage_row = ui.ActionRow()
                shell_select = ui.Select(
                    placeholder=t('modules.welcome_channel.config.manage_placeholder', locale=self.locale),
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
            label=t('modules.welcome_channel.buttons.add', locale=self.locale),
            style=discord.ButtonStyle.success, custom_id=_CID_MAIN_ADD,
            disabled=len(self.messages) >= MAX_WELCOME_MESSAGES,
        )
        add_btn.callback = self.on_add
        button_row.add_item(add_btn)
        self.add_item(button_row)

    def _entry_label(self, entry: Dict[str, Any]) -> str:
        channel = self.bot.get_channel(entry['channel_id']) if self.bot and entry.get('channel_id') else None
        return f"#{channel.name}" if channel else f"#{entry.get('channel_id', '')}"

    def _entry_preview(self, entry: Dict[str, Any]) -> str:
        """First line of the message, flattened for a select description."""
        text = (entry.get('message') or '').replace('\n', ' ').strip()
        return text[:100]

    def _render_entry(self, entry: Dict[str, Any]) -> str:
        channel = self.bot.get_channel(entry['channel_id']) if self.bot and entry.get('channel_id') else None
        channel_ref = channel.mention if channel else f"`{entry.get('channel_id', '')}`"
        line = f"{WAVING_HAND} {channel_ref}"
        extras = [f"`{_color_to_hex(entry_accent_color(entry))}`"]
        if not entry.get('enabled', True):
            extras.append(t('modules.welcome_channel.config.list.paused', locale=self.locale))
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
        if len(messages) >= MAX_WELCOME_MESSAGES:
            await interaction.response.send_message(
                t('modules.welcome_channel.errors.too_many', locale=locale,
                  max=MAX_WELCOME_MESSAGES),
                ephemeral=True,
            )
            return
        add_view = AddWelcomeMessageView(bot, interaction.guild_id, locale)
        await interaction.response.edit_message(view=add_view)

    async def on_manage_select(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        entry_id = interaction.data['values'][0]
        messages = await _load_messages(bot, interaction.guild_id)
        entry = next((m for m in messages if m['id'] == entry_id), None)
        if not entry:
            await _render_main(interaction)
            return
        manage_view = ManageWelcomeMessageView(bot, interaction.guild_id, locale, entry)
        await interaction.response.edit_message(view=manage_view)

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
# Add a welcome message
# =========================================================================== #
class AddWelcomeMessageView(BaseView):
    """Guided flow to add a welcome message. Auth: Manage Server.

    Persistent: yes. The in-progress wizard state (channel picked, message
    typed) is not persisted anywhere — on a restart the shell rebuilds an
    empty form, same accepted loss as AddSubscriptionView (see
    docs/PERSISTENT_VIEWS.md Appendix B.1.c). Every callback re-checks
    check_guild_perms(interaction), so no auth state depends on self either.
    """

    __persistent__ = True

    def __init__(self, bot=None, guild_id: Optional[int] = None, locale: str = "en-US"):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.locale = locale

        self.channel_id: Optional[int] = None
        self.message: Optional[str] = None
        self.accent_color: Optional[int] = None
        self.customized: bool = False

        self._build_view()

    @property
    def _can_confirm(self) -> bool:
        return bool(self.channel_id)

    def _build_view(self):
        self.clear_items()
        container = ui.Container()

        container.add_item(ui.TextDisplay(
            f"### {ADD} {t('modules.welcome_channel.add.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t('modules.welcome_channel.add.description', locale=self.locale)
        ))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # 1. Channel.
        container.add_item(ui.TextDisplay(
            f"**{t('modules.welcome_channel.add.channel.title', locale=self.locale)}**{REQUIRED_FIELDS}\n"
            f"-# {t('modules.welcome_channel.add.channel.description', locale=self.locale)}"
        ))
        channel_row = ui.ActionRow()
        channel_select = ui.ChannelSelect(
            placeholder=t('modules.welcome_channel.add.channel.placeholder', locale=self.locale),
            channel_types=CHANNEL_TYPES, min_values=1, max_values=1, custom_id=_CID_ADD_CHANNEL,
        )
        if self.channel_id and self.bot:
            ch = self.bot.get_channel(self.channel_id)
            if ch:
                channel_select.default_values = [ch]
        channel_select.callback = self.on_channel_select
        channel_row.add_item(channel_select)
        container.add_item(channel_row)

        # 2. Message customization.
        state_key = 'custom_state' if self.customized else 'default_state'
        container.add_item(ui.TextDisplay(
            f"**{t('modules.welcome_channel.customize.title', locale=self.locale)}**\n"
            f"-# {t('modules.welcome_channel.customize.section_description', locale=self.locale)}\n"
            f"-# {t(f'modules.welcome_channel.customize.{state_key}', locale=self.locale)}"
        ))
        customize_row = ui.ActionRow()
        customize_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(EDIT),
            label=t('modules.welcome_channel.customize.button', locale=self.locale),
            style=discord.ButtonStyle.primary, custom_id=_CID_ADD_CUSTOMIZE,
        )
        customize_btn.callback = self.on_customize
        customize_row.add_item(customize_btn)
        container.add_item(customize_row)

        self.add_item(container)

        # Action buttons.
        button_row = ui.ActionRow()
        back_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(BACK),
            label=t('modules.config.buttons.back', locale=self.locale),
            style=discord.ButtonStyle.secondary, custom_id=_CID_ADD_BACK,
        )
        back_btn.callback = self.on_back
        button_row.add_item(back_btn)

        confirm_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(DONE),
            label=t('modules.welcome_channel.buttons.confirm', locale=self.locale),
            style=discord.ButtonStyle.success, custom_id=_CID_ADD_CONFIRM,
            disabled=not self._can_confirm,
        )
        confirm_btn.callback = self.on_confirm
        button_row.add_item(confirm_btn)
        self.add_item(button_row)

    # -- callbacks -------------------------------------------------------- #
    async def on_channel_select(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        values = interaction.data.get('values')
        self.channel_id = int(values[0]) if values else None
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_customize(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        locale = i18n.get_user_locale(interaction)
        modal = WelcomeMessageModal(locale, self.message, self.accent_color, self._on_customized)
        modal.bot = interaction.client
        await interaction.response.send_modal(modal)

    async def _on_customized(self, interaction: discord.Interaction, message: str,
                             color: Optional[int]):
        self.message = message
        self.accent_color = color
        self.customized = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_confirm(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        if not self._can_confirm:
            return
        await interaction.response.defer()

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        messages = await _load_messages(bot, interaction.guild_id)

        if len(messages) >= MAX_WELCOME_MESSAGES:
            await interaction.followup.send(
                t('modules.welcome_channel.errors.too_many', locale=locale,
                  max=MAX_WELCOME_MESSAGES),
                ephemeral=True,
            )
            return

        messages.append({
            'id': new_message_id(),
            'channel_id': self.channel_id,
            'message': self.message or get_default_message(locale),
            'accent_color': self.accent_color,
            'enabled': True,
            'created_by': interaction.user.id,
            'created_at': datetime.now(timezone.utc).isoformat(),
        })

        success, error = await _save_messages(bot, interaction.guild_id, messages)
        if success:
            await _render_main(interaction)
            await interaction.followup.send(
                t('modules.welcome_channel.add.success', locale=locale), ephemeral=True,
            )
        else:
            await interaction.followup.send(
                t('modules.config.save.error', locale=locale, error=error), ephemeral=True,
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


# =========================================================================== #
# Manage a welcome message
# =========================================================================== #
class ManageWelcomeMessageView(BaseView):
    """Edit channel / message, pause or remove one welcome message.

    Auth: Manage Server.

    Persistent: yes. ``self.entry`` (the message shown) is not reconstructible
    from a bare custom_id — same accepted-loss UX as ManageSubscriptionView:
    the shell renders an empty card and every callback re-checks
    check_guild_perms(interaction), so a click on a stale shell is safe, it
    just has nothing to act on until the user re-opens the manage panel.
    """

    __persistent__ = True

    def __init__(self, bot=None, guild_id: Optional[int] = None, locale: str = "en-US",
                 entry: Optional[Dict[str, Any]] = None):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.locale = locale
        self.entry = entry or {}

        self._build_view()

    def _build_view(self):
        self.clear_items()
        container = ui.Container(
            accent_colour=discord.Colour(entry_accent_color(self.entry)),
        )

        channel = self.bot.get_channel(self.entry['channel_id']) \
            if self.bot and self.entry.get('channel_id') else None
        channel_ref = channel.mention if channel else f"`{self.entry.get('channel_id', '')}`"

        container.add_item(ui.TextDisplay(
            f"### {WAVING_HAND} {t('modules.welcome_channel.manage.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            f"-# {channel_ref} · `{_color_to_hex(entry_accent_color(self.entry))}`"
        ))

        if not self.entry.get('enabled', True):
            container.add_item(ui.TextDisplay(
                f"{WARNING} {t('modules.welcome_channel.manage.paused_notice', locale=self.locale)}"
            ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Current message preview.
        preview = (self.entry.get('message') or '').strip()
        if preview:
            container.add_item(ui.TextDisplay(
                f"**{t('modules.welcome_channel.manage.preview_title', locale=self.locale)}**\n"
                f"-# {t('modules.welcome_channel.manage.preview_description', locale=self.locale)}"
            ))
            container.add_item(ui.TextDisplay(f"```\n{preview[:900]}\n```"))

        # Channel.
        container.add_item(ui.TextDisplay(
            f"**{t('modules.welcome_channel.manage.channel.title', locale=self.locale)}**{REQUIRED_FIELDS}\n"
            f"-# {t('modules.welcome_channel.manage.channel.description', locale=self.locale)}"
        ))
        channel_row = ui.ActionRow()
        channel_select = ui.ChannelSelect(
            placeholder=t('modules.welcome_channel.manage.channel.placeholder', locale=self.locale),
            channel_types=CHANNEL_TYPES, min_values=1, max_values=1, custom_id=_CID_MANAGE_CHANNEL,
        )
        if channel:
            channel_select.default_values = [channel]
        channel_select.callback = self.on_channel_select
        channel_row.add_item(channel_select)
        container.add_item(channel_row)

        # Message customization.
        container.add_item(ui.TextDisplay(
            f"**{t('modules.welcome_channel.customize.title', locale=self.locale)}**\n"
            f"-# {t('modules.welcome_channel.customize.section_description', locale=self.locale)}"
        ))
        msg_row = ui.ActionRow()
        msg_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(EDIT),
            label=t('modules.welcome_channel.customize.button', locale=self.locale),
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
                'modules.welcome_channel.manage.pause' if enabled
                else 'modules.welcome_channel.manage.resume',
                locale=self.locale,
            ),
            style=discord.ButtonStyle.secondary, custom_id=_CID_MANAGE_TOGGLE,
        )
        toggle_btn.callback = self.on_toggle
        button_row.add_item(toggle_btn)

        remove_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(DELETE),
            label=t('modules.welcome_channel.manage.remove', locale=self.locale),
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
        messages = await _load_messages(bot, interaction.guild_id)
        target = next((m for m in messages if m['id'] == entry_id), None)
        if not target:
            return None
        target.update(fields)
        success, error = await _save_messages(bot, interaction.guild_id, messages)
        if success:
            self.entry = target
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
            await interaction.response.send_message(
                t('modules.config.save.error', locale=locale, error=error or ''),
                ephemeral=True,
            )
            return
        view = ManageWelcomeMessageView(interaction.client, interaction.guild_id, locale, self.entry)
        await interaction.response.edit_message(view=view)

    # -- callbacks -------------------------------------------------------- #
    async def on_channel_select(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        values = interaction.data.get('values')
        if not values:
            await _render_main(interaction)
            return
        await self._refresh(interaction, await self._apply(interaction, channel_id=int(values[0])))

    async def on_edit_message(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        locale = i18n.get_user_locale(interaction)
        modal = WelcomeMessageModal(
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
        success, error = await _save_messages(bot, interaction.guild_id, messages)

        await _render_main(interaction)
        if success:
            await interaction.followup.send(
                t('modules.welcome_channel.manage.removed', locale=locale), ephemeral=True,
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
