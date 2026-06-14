"""
Configuration UI for the Social Notifications module (`/config`).

This panel is *live*: because adding a target requires resolving it through the
external service (a Redis round-trip), actions are applied immediately rather
than batched behind a Save button. The flow is:

  Main panel ──► Add subscription ──► (platform + channel + roles + account + message) ──► confirm
            └──► Manage subscription ──► change channel / roles / message / pause / remove

Message customization is done through **Modals V2** (see docs/MODALS_V2.md):
  - one modal collects the account handle/URL,
  - a second modal customizes the message (full text incl. title), the accent
    colour and the display toggles (avatar thumbnail / media preview).

Persistence (see docs/PERSISTENT_VIEWS.md): every interactive component uses a
stable, namespaced ``custom_id`` and the views never time out. The main panel is
registered as a persistent shell (guild permission auth) so it survives a bot
restart; its callbacks re-derive all context from ``interaction`` + the DB. The
add/manage sub-flows are always re-entered from the main panel.

All contract logic (subscribe / unsubscribe) is delegated to the
``SocialNotifications`` cog so it lives in one place.
"""

import logging
from typing import Any, Dict, List, Optional

import discord
from discord import ui

from cogs.error_handler import BaseView, BaseModal
from modules.social_notifications import (
    SUPPORTED_PLATFORMS,
    MAX_MESSAGE_LENGTH,
    is_platform_disabled,
    platform_subscription_limit,
    supports_avatar,
    supports_media,
    platform_color,
    get_default_message,
    platform_placeholders,
)
from utils.emojis import (
    SOCIAL, ADD, BACK, EDIT, DELETE, DONE, INFO, WARNING,
    REQUIRED_FIELDS, PAUSE, PLAY, get_platform_emoji,
)
from utils.i18n import t, i18n

logger = logging.getLogger('moddy.modules.social_notifications_config')

# Channel types that can receive notifications.
_CHANNEL_TYPES = [discord.ChannelType.text, discord.ChannelType.news]

# Keep the panel under Discord's component/character limits even when a guild
# follows many accounts. The manage selector still exposes up to 25 of them.
_MAX_LIST_DISPLAY = 20

# --------------------------------------------------------------------------- #
# Namespaced custom_id constants (persistent dispatch).
# Format: moddy:social:<view>:<action>. Guild context is re-derived from
# ``interaction.guild_id`` so the ids can stay static (one shell, all guilds).
# --------------------------------------------------------------------------- #
_CID_MAIN_ADD = "moddy:social:main:add"
_CID_MAIN_BACK = "moddy:social:main:back"
_CID_MAIN_MANAGE = "moddy:social:main:manage"

_CID_ADD_PLATFORM = "moddy:social:add:platform"
_CID_ADD_CHANNEL = "moddy:social:add:channel"
_CID_ADD_ROLES = "moddy:social:add:roles"
_CID_ADD_ACCOUNT = "moddy:social:add:account"
_CID_ADD_CUSTOMIZE = "moddy:social:add:customize"
_CID_ADD_CONFIRM = "moddy:social:add:confirm"
_CID_ADD_BACK = "moddy:social:add:back"

_CID_MANAGE_CHANNEL = "moddy:social:manage:channel"
_CID_MANAGE_ROLES = "moddy:social:manage:roles"
_CID_MANAGE_EDIT = "moddy:social:manage:edit"
_CID_MANAGE_TOGGLE = "moddy:social:manage:toggle"
_CID_MANAGE_REMOVE = "moddy:social:manage:remove"
_CID_MANAGE_BACK = "moddy:social:manage:back"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _platform_label(platform: str, locale: str) -> str:
    return t(f"modules.social_notifications.platforms.{platform}", locale=locale)


def _sub_key(sub: Dict[str, Any]) -> str:
    """Stable select value identifying a subscription."""
    return f"{sub['platform']}:{sub['target_id']}"


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


def _placeholder_help(platform: str, locale: str) -> str:
    """Human-readable list of the placeholders available for a platform.

    Fetched WITHOUT kwargs so the literal ``{placeholder}`` braces survive
    (``i18n.t`` only runs ``str.format`` when kwargs are passed).
    """
    label_keys = {
        "{author}": "modules.social_notifications.customize.ph_author",
        "{title}": "modules.social_notifications.customize.ph_title",
        "{platform}": "modules.social_notifications.customize.ph_platform",
        "{url}": "modules.social_notifications.customize.ph_url",
        "{timestamp}": "modules.social_notifications.customize.ph_timestamp",
    }
    header = t("modules.social_notifications.customize.placeholders_header", locale=locale)
    lines = [header]
    for ph in platform_placeholders(platform):
        label = t(label_keys.get(ph, ""), locale=locale) if label_keys.get(ph) else ph
        lines.append(f"`{ph}` — {label}")
    return "\n".join(lines)


async def _check_perms(interaction: discord.Interaction) -> bool:
    """Authorize a config interaction: requires Manage Server in this guild."""
    perms = getattr(interaction.user, "guild_permissions", None)
    if not interaction.guild_id or perms is None or not perms.manage_guild:
        await interaction.response.send_message(
            t('modules.social_notifications.errors.no_permission',
              locale=i18n.get_user_locale(interaction)),
            ephemeral=True,
        )
        return False
    return True


async def _render_main(interaction: discord.Interaction) -> None:
    """(Re)build and show the main panel from a live interaction."""
    bot = interaction.client
    locale = i18n.get_user_locale(interaction)
    view = await SocialNotificationsConfigView.create(
        bot, interaction.guild_id, interaction.user.id, locale
    )
    if interaction.response.is_done():
        await interaction.edit_original_response(view=view)
    else:
        await interaction.response.edit_message(view=view)


# =========================================================================== #
# Modals (V2)
# =========================================================================== #
class AccountModal(BaseModal):
    """Collects the platform account (handle / username / channel or feed URL)."""

    def __init__(self, locale: str, current_identifier: str, callback_func):
        super().__init__(
            title=t('modules.social_notifications.account.modal_title', locale=locale),
            timeout=None,
        )
        self.locale = locale
        self.callback_func = callback_func

        self.identifier_input = ui.TextInput(
            style=discord.TextStyle.short,
            placeholder=t('modules.social_notifications.account.placeholder', locale=locale),
            default=current_identifier or None,
            max_length=300,
            required=True,
        )
        self.add_item(ui.Label(
            text=t('modules.social_notifications.account.label', locale=locale),
            description=t('modules.social_notifications.account.description', locale=locale),
            component=self.identifier_input,
        ))

    async def on_submit(self, interaction: discord.Interaction):
        await self.callback_func(interaction, self.identifier_input.value.strip())


class MessageCustomizationModal(BaseModal):
    """Customizes the message, accent colour and display toggles.

    The two display toggles (avatar thumbnail / media preview) are only offered
    for platforms that actually expose them.
    """

    def __init__(
        self,
        locale: str,
        platform: str,
        current_message: Optional[str],
        current_color: Optional[int],
        show_avatar: bool,
        show_media: bool,
        callback_func,
    ):
        super().__init__(
            title=t('modules.social_notifications.customize.modal_title', locale=locale),
            timeout=None,
        )
        self.locale = locale
        self.platform = platform
        self.callback_func = callback_func

        # 1. Message (defaults to the current message, or the platform default
        #    so the user can see/edit it — not just a placeholder).
        default_msg = current_message if current_message else get_default_message(platform)
        self.message_input = ui.TextInput(
            style=discord.TextStyle.paragraph,
            default=default_msg,
            max_length=MAX_MESSAGE_LENGTH,
            required=True,
        )
        self.add_item(ui.Label(
            text=t('modules.social_notifications.customize.message_label', locale=locale),
            description=t('modules.social_notifications.customize.message_description', locale=locale),
            component=self.message_input,
        ))

        # 2. Placeholder cheat-sheet (static text, adapts to the platform).
        self.add_item(ui.TextDisplay(_placeholder_help(platform, locale)))

        # 3. Accent colour (hex), defaults to the platform colour.
        default_color = current_color if current_color is not None else platform_color(platform)
        self.color_input = ui.TextInput(
            style=discord.TextStyle.short,
            default=_color_to_hex(default_color),
            min_length=0,
            max_length=7,
            required=False,
        )
        self.add_item(ui.Label(
            text=t('modules.social_notifications.customize.color_label', locale=locale),
            description=t('modules.social_notifications.customize.color_description', locale=locale),
            component=self.color_input,
        ))

        # 4. Display toggles — only those the platform supports.
        self.options_group: Optional[ui.CheckboxGroup] = None
        options = []
        if supports_avatar(platform):
            options.append(discord.CheckboxGroupOption(
                label=t('modules.social_notifications.customize.option_avatar', locale=locale),
                value="avatar", default=show_avatar,
            ))
        if supports_media(platform):
            options.append(discord.CheckboxGroupOption(
                label=t('modules.social_notifications.customize.option_media', locale=locale),
                value="media", default=show_media,
            ))
        if options:
            self.options_group = ui.CheckboxGroup(
                options=options, min_values=0, max_values=len(options), required=False,
            )
            self.add_item(ui.Label(
                text=t('modules.social_notifications.customize.display_label', locale=locale),
                component=self.options_group,
            ))

    async def on_submit(self, interaction: discord.Interaction):
        message = self.message_input.value.strip()
        color = _parse_hex_color(self.color_input.value)
        if self.options_group is not None:
            selected = set(self.options_group.values)
            show_avatar = "avatar" in selected
            show_media = "media" in selected
        else:
            # Platform exposes neither — keep them off (renderer ignores anyway).
            show_avatar = False
            show_media = False
        await self.callback_func(interaction, message, color, show_avatar, show_media)


# =========================================================================== #
# Main panel
# =========================================================================== #
class SocialNotificationsConfigView(BaseView):
    """Lists current subscriptions and entry points to add/manage them.

    Persistent: yes. Auth: Manage Server in the guild.
    """

    __persistent__ = True

    def __init__(self, bot=None, guild_id: Optional[int] = None, user_id: Optional[int] = None,
                 locale: str = "en-US", subscriptions: Optional[List[Dict[str, Any]]] = None,
                 service_alive: bool = True, is_premium: bool = False):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale
        self.subscriptions = subscriptions or []
        self.service_alive = service_alive
        self.is_premium = is_premium

        self._build_view()

    # -- construction ----------------------------------------------------- #
    @classmethod
    async def create(cls, bot, guild_id: int, user_id: int, locale: str):
        """Async factory: load subscriptions + service health before building."""
        subscriptions = await bot.db.list_social_subscriptions(guild_id)
        service_alive = True
        is_premium = False
        try:
            if getattr(bot, "feeds_client", None):
                service_alive = await bot.feeds_client.is_service_alive()
            is_premium = await bot.db.has_attribute('guild', guild_id, 'PREMIUM')
        except Exception:
            pass
        return cls(bot, guild_id, user_id, locale, subscriptions, service_alive, is_premium)

    def _build_view(self):
        self.clear_items()
        container = ui.Container()

        container.add_item(ui.TextDisplay(
            f"### {SOCIAL} {t('modules.social_notifications.config.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t('modules.social_notifications.config.description', locale=self.locale)
        ))

        # Polling-speed hint (premium = faster checks).
        speed_key = 'premium_note' if self.is_premium else 'free_note'
        container.add_item(ui.TextDisplay(
            f"-# {t(f'modules.social_notifications.config.{speed_key}', locale=self.locale)}"
        ))

        # Service health warning.
        if not self.service_alive:
            container.add_item(ui.TextDisplay(
                f"{WARNING} {t('modules.social_notifications.config.service_down', locale=self.locale)}"
            ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        if self.subscriptions:
            container.add_item(ui.TextDisplay(
                f"**{t('modules.social_notifications.config.list.title', locale=self.locale)}**\n"
                f"-# {t('modules.social_notifications.config.list.count', locale=self.locale, count=len(self.subscriptions))}"
            ))
            for sub in self.subscriptions[:_MAX_LIST_DISPLAY]:
                container.add_item(ui.TextDisplay(self._render_entry(sub)))
            if len(self.subscriptions) > _MAX_LIST_DISPLAY:
                container.add_item(ui.TextDisplay(
                    f"-# {t('modules.social_notifications.config.list.more', locale=self.locale, count=len(self.subscriptions) - _MAX_LIST_DISPLAY)}"
                ))

            # Manage selector (real options).
            manage_row = ui.ActionRow()
            options = []
            for sub in self.subscriptions[:25]:
                name = sub.get('display_name') or sub.get('identifier') or sub['target_id']
                options.append(discord.SelectOption(
                    label=name[:100],
                    value=_sub_key(sub),
                    description=_platform_label(sub['platform'], self.locale)[:100],
                    emoji=discord.PartialEmoji.from_str(get_platform_emoji(sub['platform'])),
                ))
            manage_select = ui.Select(
                placeholder=t('modules.social_notifications.config.manage_placeholder', locale=self.locale),
                options=options, min_values=1, max_values=1, custom_id=_CID_MAIN_MANAGE,
            )
            manage_select.callback = self.on_manage_select
            manage_row.add_item(manage_select)
            container.add_item(manage_row)
        else:
            container.add_item(ui.TextDisplay(
                f"{INFO} {t('modules.social_notifications.config.list.empty', locale=self.locale)}"
            ))
            # Shell (no live bot) still needs the select registered for dispatch.
            if self.bot is None:
                manage_row = ui.ActionRow()
                shell_select = ui.Select(
                    placeholder=t('modules.social_notifications.config.manage_placeholder', locale=self.locale),
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
            label=t('modules.social_notifications.buttons.add', locale=self.locale),
            style=discord.ButtonStyle.success, custom_id=_CID_MAIN_ADD,
        )
        add_btn.callback = self.on_add
        button_row.add_item(add_btn)
        self.add_item(button_row)

    def _render_entry(self, sub: Dict[str, Any]) -> str:
        emoji = get_platform_emoji(sub['platform'])
        name = sub.get('display_name') or sub.get('identifier') or sub['target_id']
        channel = self.bot.get_channel(sub['channel_id']) if self.bot else None
        channel_ref = channel.mention if channel else f"`{sub['channel_id']}`"
        line = f"{emoji} **{discord.utils.escape_markdown(name)}** → {channel_ref}"
        extras = []
        if sub.get('mention_role_ids'):
            extras.append(t('modules.social_notifications.config.list.roles',
                            locale=self.locale, count=len(sub['mention_role_ids'])))
        if not sub.get('enabled', True):
            extras.append(t('modules.social_notifications.config.list.paused', locale=self.locale))
        if extras:
            line += f"\n-# {' · '.join(extras)}"
        return line

    # -- callbacks (re-derive context from interaction) ------------------- #
    async def on_add(self, interaction: discord.Interaction):
        if not await _check_perms(interaction):
            return
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        is_premium = False
        try:
            is_premium = await bot.db.has_attribute('guild', interaction.guild_id, 'PREMIUM')
        except Exception:
            pass
        add_view = AddSubscriptionView(bot, interaction.guild_id, locale, is_premium)
        await interaction.response.edit_message(view=add_view)

    async def on_manage_select(self, interaction: discord.Interaction):
        if not await _check_perms(interaction):
            return
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        key = interaction.data['values'][0]
        subs = await bot.db.list_social_subscriptions(interaction.guild_id)
        sub = next((s for s in subs if _sub_key(s) == key), None)
        if not sub:
            await _render_main(interaction)
            return
        manage_view = ManageSubscriptionView(bot, interaction.guild_id, locale, sub)
        await interaction.response.edit_message(view=manage_view)

    async def on_back(self, interaction: discord.Interaction):
        if not await _check_perms(interaction):
            return
        from cogs.config import ConfigMainView
        locale = i18n.get_user_locale(interaction)
        main_view = ConfigMainView(interaction.client, interaction.guild_id, interaction.user.id, locale)
        await interaction.response.edit_message(view=main_view)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _check_perms(interaction)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_view(cls())


# =========================================================================== #
# Add subscription
# =========================================================================== #
class AddSubscriptionView(BaseView):
    """Guided flow to add a new subscription. Auth: Manage Server."""

    def __init__(self, bot=None, guild_id: Optional[int] = None, locale: str = "en-US",
                 is_premium: bool = False):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.locale = locale
        self.is_premium = is_premium

        self.platform: Optional[str] = None
        self.channel_id: Optional[int] = None
        self.role_ids: List[int] = []
        self.identifier: Optional[str] = None
        # Message customization (None / defaults until the user opens the modal).
        self.message: Optional[str] = None
        self.embed_color: Optional[int] = None
        self.show_avatar: bool = True
        self.show_media: bool = True
        self.customized: bool = False

        self._build_view()

    @property
    def _can_confirm(self) -> bool:
        return bool(
            self.platform and not is_platform_disabled(self.platform)
            and self.channel_id and self.identifier
        )

    def _build_view(self):
        self.clear_items()
        container = ui.Container()

        container.add_item(ui.TextDisplay(
            f"### {ADD} {t('modules.social_notifications.add.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t('modules.social_notifications.add.description', locale=self.locale)
        ))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # 1. Platform.
        limit = platform_subscription_limit(self.is_premium)
        limit_key = 'add.limit_premium' if self.is_premium else 'add.limit_free'
        container.add_item(ui.TextDisplay(
            f"**{t('modules.social_notifications.add.platform.title', locale=self.locale)}**{REQUIRED_FIELDS}\n"
            f"-# {t('modules.social_notifications.add.platform.description', locale=self.locale)}\n"
            f"-# {t(f'modules.social_notifications.{limit_key}', locale=self.locale, max=limit)}"
        ))
        platform_row = ui.ActionRow()
        platform_options = []
        for p in SUPPORTED_PLATFORMS:
            disabled = is_platform_disabled(p)
            label = _platform_label(p, self.locale)
            if disabled:
                label = f"{label} ({t('modules.social_notifications.add.soon', locale=self.locale)})"
            platform_options.append(discord.SelectOption(
                label=label[:100], value=p,
                emoji=discord.PartialEmoji.from_str(get_platform_emoji(p)),
                default=(self.platform == p),
            ))
        platform_select = ui.Select(
            placeholder=t('modules.social_notifications.add.platform.placeholder', locale=self.locale),
            options=platform_options, min_values=1, max_values=1, custom_id=_CID_ADD_PLATFORM,
        )
        platform_select.callback = self.on_platform_select
        platform_row.add_item(platform_select)
        container.add_item(platform_row)

        if self.platform and is_platform_disabled(self.platform):
            container.add_item(ui.TextDisplay(
                f"{WARNING} {t('modules.social_notifications.add.platform_disabled', locale=self.locale)}"
            ))

        # 2. Channel.
        container.add_item(ui.TextDisplay(
            f"**{t('modules.social_notifications.add.channel.title', locale=self.locale)}**{REQUIRED_FIELDS}\n"
            f"-# {t('modules.social_notifications.add.channel.description', locale=self.locale)}"
        ))
        channel_row = ui.ActionRow()
        channel_select = ui.ChannelSelect(
            placeholder=t('modules.social_notifications.add.channel.placeholder', locale=self.locale),
            channel_types=_CHANNEL_TYPES, min_values=1, max_values=1, custom_id=_CID_ADD_CHANNEL,
        )
        if self.channel_id and self.bot:
            ch = self.bot.get_channel(self.channel_id)
            if ch:
                channel_select.default_values = [ch]
        channel_select.callback = self.on_channel_select
        channel_row.add_item(channel_select)
        container.add_item(channel_row)

        # 3. Roles (optional).
        container.add_item(ui.TextDisplay(
            f"**{t('modules.social_notifications.add.roles.title', locale=self.locale)}**\n"
            f"-# {t('modules.social_notifications.add.roles.description', locale=self.locale)}"
        ))
        role_row = ui.ActionRow()
        role_select = ui.RoleSelect(
            placeholder=t('modules.social_notifications.add.roles.placeholder', locale=self.locale),
            min_values=0, max_values=10, custom_id=_CID_ADD_ROLES,
        )
        if self.role_ids and self.bot:
            guild = self.bot.get_guild(self.guild_id)
            if guild:
                selected = [guild.get_role(r) for r in self.role_ids if guild.get_role(r)]
                if selected:
                    role_select.default_values = selected
        role_select.callback = self.on_role_select
        role_row.add_item(role_select)
        container.add_item(role_row)

        # 4. Account.
        if self.identifier:
            src = f"`{discord.utils.escape_markdown(self.identifier)}`"
        else:
            src = t('modules.social_notifications.account.not_set', locale=self.locale)
        container.add_item(ui.TextDisplay(
            f"**{t('modules.social_notifications.account.title', locale=self.locale)}**{REQUIRED_FIELDS}\n"
            f"-# {t('modules.social_notifications.account.section_description', locale=self.locale)}\n"
            f"-# {t('modules.social_notifications.account.current', locale=self.locale)} {src}"
        ))
        account_row = ui.ActionRow()
        account_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(EDIT),
            label=t('modules.social_notifications.account.button', locale=self.locale),
            style=discord.ButtonStyle.primary, custom_id=_CID_ADD_ACCOUNT,
        )
        account_btn.callback = self.on_set_account
        account_row.add_item(account_btn)
        container.add_item(account_row)

        # 5. Message customization.
        state_key = 'custom_state' if self.customized else 'default_state'
        container.add_item(ui.TextDisplay(
            f"**{t('modules.social_notifications.customize.title', locale=self.locale)}**\n"
            f"-# {t('modules.social_notifications.customize.section_description', locale=self.locale)}\n"
            f"-# {t(f'modules.social_notifications.customize.{state_key}', locale=self.locale)}"
        ))
        customize_row = ui.ActionRow()
        customize_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(EDIT),
            label=t('modules.social_notifications.customize.button', locale=self.locale),
            style=discord.ButtonStyle.secondary, custom_id=_CID_ADD_CUSTOMIZE,
            disabled=not self.platform,
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
            label=t('modules.social_notifications.buttons.confirm', locale=self.locale),
            style=discord.ButtonStyle.success, custom_id=_CID_ADD_CONFIRM,
            disabled=not self._can_confirm,
        )
        confirm_btn.callback = self.on_confirm
        button_row.add_item(confirm_btn)
        self.add_item(button_row)

    # -- callbacks -------------------------------------------------------- #
    async def on_platform_select(self, interaction: discord.Interaction):
        if not await _check_perms(interaction):
            return
        self.platform = interaction.data['values'][0]
        # Reset platform-specific customization to that platform's defaults.
        self.message = None
        self.embed_color = None
        self.show_avatar = True
        self.show_media = True
        self.customized = False
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_channel_select(self, interaction: discord.Interaction):
        if not await _check_perms(interaction):
            return
        values = interaction.data.get('values')
        self.channel_id = int(values[0]) if values else None
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_role_select(self, interaction: discord.Interaction):
        if not await _check_perms(interaction):
            return
        self.role_ids = [int(r) for r in interaction.data.get('values', [])]
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_set_account(self, interaction: discord.Interaction):
        if not await _check_perms(interaction):
            return
        modal = AccountModal(self.locale, self.identifier or "", self._on_account_set)
        modal.bot = self.bot
        await interaction.response.send_modal(modal)

    async def _on_account_set(self, interaction: discord.Interaction, identifier: str):
        self.identifier = identifier or None
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_customize(self, interaction: discord.Interaction):
        if not await _check_perms(interaction):
            return
        if not self.platform:
            return
        modal = MessageCustomizationModal(
            self.locale, self.platform, self.message, self.embed_color,
            self.show_avatar, self.show_media, self._on_customized,
        )
        modal.bot = self.bot
        await interaction.response.send_modal(modal)

    async def _on_customized(self, interaction: discord.Interaction, message: str,
                             color: Optional[int], show_avatar: bool, show_media: bool):
        self.message = message
        self.embed_color = color
        self.show_avatar = show_avatar
        self.show_media = show_media
        self.customized = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_confirm(self, interaction: discord.Interaction):
        if not await _check_perms(interaction):
            return
        if not self._can_confirm:
            return
        await interaction.response.defer()

        cog = interaction.client.get_cog("SocialNotifications")
        if not cog:
            await interaction.followup.send(
                t('modules.social_notifications.errors.service_unavailable', locale=self.locale),
                ephemeral=True,
            )
            return

        ok, reply = await cog.add_subscription(
            guild=interaction.client.get_guild(interaction.guild_id),
            platform=self.platform,
            identifier=self.identifier,
            channel_id=self.channel_id,
            role_ids=self.role_ids,
            message=self.message,
            embed_color=self.embed_color,
            show_avatar=self.show_avatar,
            show_media=self.show_media,
            created_by=interaction.user.id,
        )

        if ok:
            await _render_main(interaction)
            name = reply.get('display_name') or self.identifier
            await interaction.followup.send(
                t('modules.social_notifications.add.success', locale=self.locale, name=name),
                ephemeral=True,
            )
        else:
            error_code = reply.get('error', 'internal_error')
            await interaction.followup.send(self._error_text(error_code), ephemeral=True)

    def _error_text(self, code: str) -> str:
        msg = t(f'modules.social_notifications.errors.{code}', locale=self.locale)
        if msg.startswith('['):
            msg = t('modules.social_notifications.errors.internal_error', locale=self.locale)
        return msg

    async def on_back(self, interaction: discord.Interaction):
        if not await _check_perms(interaction):
            return
        await _render_main(interaction)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _check_perms(interaction)


# =========================================================================== #
# Manage subscription
# =========================================================================== #
class ManageSubscriptionView(BaseView):
    """Edit channel / roles / message, pause or remove an existing subscription.

    Auth: Manage Server.
    """

    def __init__(self, bot=None, guild_id: Optional[int] = None, locale: str = "en-US",
                 subscription: Optional[Dict[str, Any]] = None):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.locale = locale
        self.sub = subscription or {}

        self._build_view()

    def _build_view(self):
        self.clear_items()
        container = ui.Container()

        platform = self.sub.get('platform', '')
        emoji = get_platform_emoji(platform)
        name = self.sub.get('display_name') or self.sub.get('identifier') or self.sub.get('target_id', '')
        container.add_item(ui.TextDisplay(
            f"### {emoji} {discord.utils.escape_markdown(str(name))}"
        ))
        container.add_item(ui.TextDisplay(
            f"-# {_platform_label(platform, self.locale)} · `{self.sub.get('target_id', '')}`"
        ))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Channel.
        container.add_item(ui.TextDisplay(
            f"**{t('modules.social_notifications.manage.channel.title', locale=self.locale)}**{REQUIRED_FIELDS}\n"
            f"-# {t('modules.social_notifications.manage.channel.description', locale=self.locale)}"
        ))
        channel_row = ui.ActionRow()
        channel_select = ui.ChannelSelect(
            placeholder=t('modules.social_notifications.manage.channel.placeholder', locale=self.locale),
            channel_types=_CHANNEL_TYPES, min_values=1, max_values=1, custom_id=_CID_MANAGE_CHANNEL,
        )
        ch = self.bot.get_channel(self.sub['channel_id']) if self.bot and self.sub.get('channel_id') else None
        if ch:
            channel_select.default_values = [ch]
        channel_select.callback = self.on_channel_select
        channel_row.add_item(channel_select)
        container.add_item(channel_row)

        # Roles.
        container.add_item(ui.TextDisplay(
            f"**{t('modules.social_notifications.manage.roles.title', locale=self.locale)}**\n"
            f"-# {t('modules.social_notifications.manage.roles.description', locale=self.locale)}"
        ))
        role_row = ui.ActionRow()
        role_select = ui.RoleSelect(
            placeholder=t('modules.social_notifications.manage.roles.placeholder', locale=self.locale),
            min_values=0, max_values=10, custom_id=_CID_MANAGE_ROLES,
        )
        guild = self.bot.get_guild(self.guild_id) if self.bot else None
        if guild and self.sub.get('mention_role_ids'):
            selected = [guild.get_role(r) for r in self.sub['mention_role_ids'] if guild.get_role(r)]
            if selected:
                role_select.default_values = selected
        role_select.callback = self.on_role_select
        role_row.add_item(role_select)
        container.add_item(role_row)

        # Message customization state.
        state_key = 'custom_state' if self.sub.get('message') else 'default_state'
        container.add_item(ui.TextDisplay(
            f"**{t('modules.social_notifications.customize.title', locale=self.locale)}**\n"
            f"-# {t('modules.social_notifications.customize.section_description', locale=self.locale)}\n"
            f"-# {t(f'modules.social_notifications.customize.{state_key}', locale=self.locale)}"
        ))
        msg_row = ui.ActionRow()
        msg_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(EDIT),
            label=t('modules.social_notifications.customize.button', locale=self.locale),
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

        enabled = self.sub.get('enabled', True)
        toggle_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(PAUSE if enabled else PLAY),
            label=t(
                'modules.social_notifications.manage.pause' if enabled
                else 'modules.social_notifications.manage.resume',
                locale=self.locale,
            ),
            style=discord.ButtonStyle.secondary, custom_id=_CID_MANAGE_TOGGLE,
        )
        toggle_btn.callback = self.on_toggle
        button_row.add_item(toggle_btn)

        remove_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(DELETE),
            label=t('modules.social_notifications.manage.remove', locale=self.locale),
            style=discord.ButtonStyle.danger, custom_id=_CID_MANAGE_REMOVE,
        )
        remove_btn.callback = self.on_remove
        button_row.add_item(remove_btn)
        self.add_item(button_row)

    # -- callbacks -------------------------------------------------------- #
    async def _persist(self, **fields):
        await self.bot.db.update_social_subscription(
            self.guild_id, self.sub['platform'], self.sub['target_id'], **fields
        )
        self.sub.update(fields)

    async def on_channel_select(self, interaction: discord.Interaction):
        if not await _check_perms(interaction):
            return
        values = interaction.data.get('values')
        if values:
            await self._persist(channel_id=int(values[0]))
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_role_select(self, interaction: discord.Interaction):
        if not await _check_perms(interaction):
            return
        role_ids = [int(r) for r in interaction.data.get('values', [])]
        await self._persist(mention_role_ids=role_ids)
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_edit_message(self, interaction: discord.Interaction):
        if not await _check_perms(interaction):
            return
        modal = MessageCustomizationModal(
            self.locale, self.sub['platform'], self.sub.get('message'),
            self.sub.get('embed_color'),
            self.sub.get('show_avatar', True), self.sub.get('show_media', True),
            self._on_message_edited,
        )
        modal.bot = self.bot
        await interaction.response.send_modal(modal)

    async def _on_message_edited(self, interaction: discord.Interaction, message: str,
                                 color: Optional[int], show_avatar: bool, show_media: bool):
        await self._persist(
            message=message, embed_color=color,
            show_avatar=show_avatar, show_media=show_media,
        )
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_toggle(self, interaction: discord.Interaction):
        if not await _check_perms(interaction):
            return
        await self._persist(enabled=not self.sub.get('enabled', True))
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_remove(self, interaction: discord.Interaction):
        if not await _check_perms(interaction):
            return
        await interaction.response.defer()
        cog = interaction.client.get_cog("SocialNotifications")
        if cog:
            await cog.remove_subscription(self.guild_id, self.sub['platform'], self.sub['target_id'])
        else:
            await self.bot.db.remove_social_subscription(
                self.guild_id, self.sub['platform'], self.sub['target_id']
            )
        await _render_main(interaction)
        await interaction.followup.send(
            t('modules.social_notifications.manage.removed', locale=self.locale),
            ephemeral=True,
        )

    async def on_back(self, interaction: discord.Interaction):
        if not await _check_perms(interaction):
            return
        await _render_main(interaction)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _check_perms(interaction)
