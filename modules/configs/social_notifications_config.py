"""
Configuration UI for the Social Notifications module (`/config`).

This panel is *live*: because adding a target requires resolving it through the
external service (a Redis round-trip), actions are applied immediately rather
than batched behind a Save button. The flow is:

  Main panel ──► Add subscription ──► (platform + channel + roles + source) ──► confirm
            └──► Manage subscription ──► change channel / roles / message / remove

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
    PLATFORMS,
    MAX_MESSAGE_LENGTH,
    is_platform_disabled,
)
from utils.emojis import (
    SOCIAL, ADD, BACK, EDIT, DELETE, UNDONE, DONE, INFO, WARNING,
    REQUIRED_FIELDS, TOGGLE_ON, TOGGLE_OFF, get_platform_emoji,
)
from utils.i18n import t

logger = logging.getLogger('moddy.modules.social_notifications_config')

# Channel types that can receive notifications.
_CHANNEL_TYPES = [discord.ChannelType.text, discord.ChannelType.news]

# Keep the panel under Discord's component/character limits even when a guild
# follows many accounts. The manage selector still exposes up to 25 of them.
_MAX_LIST_DISPLAY = 20


def _platform_label(platform: str, locale: str) -> str:
    return t(f"modules.social_notifications.platforms.{platform}", locale=locale)


def _sub_key(sub: Dict[str, Any]) -> str:
    """Stable select value identifying a subscription."""
    return f"{sub['platform']}:{sub['target_id']}"


# =========================================================================== #
# Main panel
# =========================================================================== #
class SocialNotificationsConfigView(BaseView):
    """Lists current subscriptions and entry points to add/manage them."""

    def __init__(self, bot, guild_id: int, user_id: int, locale: str,
                 subscriptions: List[Dict[str, Any]], service_alive: bool, is_premium: bool):
        super().__init__(timeout=600)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale
        self.subscriptions = subscriptions
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

    async def refresh(self, interaction: discord.Interaction):
        """Reload state from the DB and re-render in place."""
        self.subscriptions = await self.bot.db.list_social_subscriptions(self.guild_id)
        try:
            if getattr(self.bot, "feeds_client", None):
                self.service_alive = await self.bot.feeds_client.is_service_alive()
        except Exception:
            pass
        self._build_view()
        if interaction.response.is_done():
            await interaction.edit_original_response(view=self)
        else:
            await interaction.response.edit_message(view=self)

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

            # Manage selector.
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
                options=options, min_values=1, max_values=1,
            )
            manage_select.callback = self.on_manage_select
            manage_row.add_item(manage_select)
            container.add_item(manage_row)
        else:
            container.add_item(ui.TextDisplay(
                f"{INFO} {t('modules.social_notifications.config.list.empty', locale=self.locale)}"
            ))

        self.add_item(container)

        # Action buttons.
        button_row = ui.ActionRow()
        back_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(BACK),
            label=t('modules.config.buttons.back', locale=self.locale),
            style=discord.ButtonStyle.secondary,
        )
        back_btn.callback = self.on_back
        button_row.add_item(back_btn)

        add_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(ADD),
            label=t('modules.social_notifications.buttons.add', locale=self.locale),
            style=discord.ButtonStyle.success,
        )
        add_btn.callback = self.on_add
        button_row.add_item(add_btn)
        self.add_item(button_row)

    def _render_entry(self, sub: Dict[str, Any]) -> str:
        emoji = get_platform_emoji(sub['platform'])
        name = sub.get('display_name') or sub.get('identifier') or sub['target_id']
        channel = self.bot.get_channel(sub['channel_id'])
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

    # -- callbacks -------------------------------------------------------- #
    async def on_add(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        add_view = AddSubscriptionView(self.bot, self.guild_id, self.user_id, self.locale, self)
        await interaction.response.edit_message(view=add_view)

    async def on_manage_select(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        key = interaction.data['values'][0]
        sub = next((s for s in self.subscriptions if _sub_key(s) == key), None)
        if not sub:
            await self.refresh(interaction)
            return
        manage_view = ManageSubscriptionView(self.bot, self.guild_id, self.user_id, self.locale, sub, self)
        await interaction.response.edit_message(view=manage_view)

    async def on_back(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        from cogs.config import ConfigMainView
        main_view = ConfigMainView(self.bot, self.guild_id, self.user_id, self.locale)
        await interaction.response.edit_message(view=main_view)

    async def check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                t('modules.config.errors.wrong_user', locale=self.locale), ephemeral=True
            )
            return False
        return True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.check_user(interaction)


# =========================================================================== #
# Add subscription
# =========================================================================== #
class AddSourceModal(BaseModal):
    """Collects the platform identifier and an optional custom message."""

    def __init__(self, locale: str, callback_func, current_identifier: str = "", current_message: str = ""):
        super().__init__(title=t('modules.social_notifications.add.modal.title', locale=locale), timeout=600)
        self.locale = locale
        self.callback_func = callback_func

        self.identifier_input = ui.TextInput(
            label=t('modules.social_notifications.add.modal.identifier_label', locale=locale),
            placeholder=t('modules.social_notifications.add.modal.identifier_placeholder', locale=locale),
            default=current_identifier or None,
            style=discord.TextStyle.short, max_length=300, required=True,
        )
        self.add_item(self.identifier_input)

        self.message_input = ui.TextInput(
            label=t('modules.social_notifications.add.modal.message_label', locale=locale),
            placeholder=t('modules.social_notifications.add.modal.message_placeholder', locale=locale),
            default=current_message or None,
            style=discord.TextStyle.paragraph, max_length=MAX_MESSAGE_LENGTH, required=False,
        )
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        await self.callback_func(
            interaction,
            self.identifier_input.value.strip(),
            self.message_input.value.strip() or None,
        )


class AddSubscriptionView(BaseView):
    """Guided flow to add a new subscription."""

    def __init__(self, bot, guild_id: int, user_id: int, locale: str, parent_view: SocialNotificationsConfigView):
        super().__init__(timeout=600)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale
        self.parent_view = parent_view

        self.platform: Optional[str] = None
        self.channel_id: Optional[int] = None
        self.role_ids: List[int] = []
        self.identifier: Optional[str] = None
        self.message: Optional[str] = None

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
        container.add_item(ui.TextDisplay(
            f"**{t('modules.social_notifications.add.platform.title', locale=self.locale)}**{REQUIRED_FIELDS}\n"
            f"-# {t('modules.social_notifications.add.platform.description', locale=self.locale)}"
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
            options=platform_options, min_values=1, max_values=1,
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
            channel_types=_CHANNEL_TYPES, min_values=1, max_values=1,
        )
        if self.channel_id:
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
            min_values=0, max_values=10,
        )
        if self.role_ids:
            guild = self.bot.get_guild(self.guild_id)
            if guild:
                selected = [guild.get_role(r) for r in self.role_ids if guild.get_role(r)]
                if selected:
                    role_select.default_values = selected
        role_select.callback = self.on_role_select
        role_row.add_item(role_select)
        container.add_item(role_row)

        # 4. Source + message.
        if self.identifier:
            src = f"`{discord.utils.escape_markdown(self.identifier)}`"
        else:
            src = t('modules.social_notifications.add.source.not_set', locale=self.locale)
        source_text = (
            f"**{t('modules.social_notifications.add.source.title', locale=self.locale)}**{REQUIRED_FIELDS}\n"
            f"-# {t('modules.social_notifications.add.source.description', locale=self.locale)}\n"
            f"-# {t('modules.social_notifications.add.source.current', locale=self.locale)} {src}"
        )
        if self.message:
            source_text += f"\n-# {t('modules.social_notifications.add.source.message_set', locale=self.locale)}"
        container.add_item(ui.TextDisplay(source_text))
        source_row = ui.ActionRow()
        source_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(EDIT),
            label=t('modules.social_notifications.add.source.button', locale=self.locale),
            style=discord.ButtonStyle.primary,
        )
        source_btn.callback = self.on_set_source
        source_row.add_item(source_btn)
        container.add_item(source_row)

        self.add_item(container)

        # Action buttons.
        button_row = ui.ActionRow()
        back_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(BACK),
            label=t('modules.config.buttons.back', locale=self.locale),
            style=discord.ButtonStyle.secondary,
        )
        back_btn.callback = self.on_back
        button_row.add_item(back_btn)

        confirm_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(DONE),
            label=t('modules.social_notifications.buttons.confirm', locale=self.locale),
            style=discord.ButtonStyle.success,
            disabled=not self._can_confirm,
        )
        confirm_btn.callback = self.on_confirm
        button_row.add_item(confirm_btn)
        self.add_item(button_row)

    # -- callbacks -------------------------------------------------------- #
    async def on_platform_select(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        self.platform = interaction.data['values'][0]
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_channel_select(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        values = interaction.data.get('values')
        self.channel_id = int(values[0]) if values else None
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_role_select(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        self.role_ids = [int(r) for r in interaction.data.get('values', [])]
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_set_source(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        modal = AddSourceModal(self.locale, self._on_source_set, self.identifier or "", self.message or "")
        modal.bot = self.bot
        await interaction.response.send_modal(modal)

    async def _on_source_set(self, interaction: discord.Interaction, identifier: str, message: Optional[str]):
        self.identifier = identifier or None
        self.message = message
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_confirm(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        if not self._can_confirm:
            return
        await interaction.response.defer()

        cog = self.bot.get_cog("SocialNotifications")
        if not cog:
            await interaction.followup.send(
                t('modules.social_notifications.errors.service_unavailable', locale=self.locale),
                ephemeral=True,
            )
            return

        ok, reply = await cog.add_subscription(
            guild=self.bot.get_guild(self.guild_id),
            platform=self.platform,
            identifier=self.identifier,
            channel_id=self.channel_id,
            role_ids=self.role_ids,
            message=self.message,
            created_by=self.user_id,
        )

        if ok:
            await self.parent_view.refresh(interaction)
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
        if not await self.check_user(interaction):
            return
        await self.parent_view.refresh(interaction)

    async def check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                t('modules.config.errors.wrong_user', locale=self.locale), ephemeral=True
            )
            return False
        return True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.check_user(interaction)


# =========================================================================== #
# Manage subscription
# =========================================================================== #
class EditMessageModal(BaseModal):
    """Edits the custom message of an existing subscription."""

    def __init__(self, locale: str, current_message: str, callback_func):
        super().__init__(title=t('modules.social_notifications.manage.message.modal_title', locale=locale), timeout=600)
        self.locale = locale
        self.callback_func = callback_func
        self.message_input = ui.TextInput(
            label=t('modules.social_notifications.manage.message.label', locale=locale),
            placeholder=t('modules.social_notifications.manage.message.placeholder', locale=locale),
            default=current_message or None,
            style=discord.TextStyle.paragraph, max_length=MAX_MESSAGE_LENGTH, required=False,
        )
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        await self.callback_func(interaction, self.message_input.value.strip() or None)


class ManageSubscriptionView(BaseView):
    """Edit channel / roles / message, pause or remove an existing subscription."""

    def __init__(self, bot, guild_id: int, user_id: int, locale: str,
                 subscription: Dict[str, Any], parent_view: SocialNotificationsConfigView):
        super().__init__(timeout=600)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale
        self.sub = subscription
        self.parent_view = parent_view

        self._build_view()

    def _build_view(self):
        self.clear_items()
        container = ui.Container()

        emoji = get_platform_emoji(self.sub['platform'])
        name = self.sub.get('display_name') or self.sub.get('identifier') or self.sub['target_id']
        container.add_item(ui.TextDisplay(
            f"### {emoji} {discord.utils.escape_markdown(name)}"
        ))
        container.add_item(ui.TextDisplay(
            f"-# {_platform_label(self.sub['platform'], self.locale)} · `{self.sub['target_id']}`"
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
            channel_types=_CHANNEL_TYPES, min_values=1, max_values=1,
        )
        ch = self.bot.get_channel(self.sub['channel_id'])
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
            min_values=0, max_values=10,
        )
        guild = self.bot.get_guild(self.guild_id)
        if guild and self.sub.get('mention_role_ids'):
            selected = [guild.get_role(r) for r in self.sub['mention_role_ids'] if guild.get_role(r)]
            if selected:
                role_select.default_values = selected
        role_select.callback = self.on_role_select
        role_row.add_item(role_select)
        container.add_item(role_row)

        # Message.
        if self.sub.get('message'):
            preview = self.sub['message']
            preview = preview[:120] + ('…' if len(preview) > 120 else '')
            msg_state = f"`{discord.utils.escape_markdown(preview)}`"
        else:
            msg_state = t('modules.social_notifications.manage.message.none', locale=self.locale)
        container.add_item(ui.TextDisplay(
            f"**{t('modules.social_notifications.manage.message.title', locale=self.locale)}**\n"
            f"-# {t('modules.social_notifications.manage.message.current', locale=self.locale)} {msg_state}"
        ))
        msg_row = ui.ActionRow()
        msg_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(EDIT),
            label=t('modules.social_notifications.manage.message.button', locale=self.locale),
            style=discord.ButtonStyle.primary,
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
            style=discord.ButtonStyle.secondary,
        )
        back_btn.callback = self.on_back
        button_row.add_item(back_btn)

        enabled = self.sub.get('enabled', True)
        toggle_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(TOGGLE_OFF if enabled else TOGGLE_ON),
            label=t(
                'modules.social_notifications.manage.pause' if enabled
                else 'modules.social_notifications.manage.resume',
                locale=self.locale,
            ),
            style=discord.ButtonStyle.secondary,
        )
        toggle_btn.callback = self.on_toggle
        button_row.add_item(toggle_btn)

        remove_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(DELETE),
            label=t('modules.social_notifications.manage.remove', locale=self.locale),
            style=discord.ButtonStyle.danger,
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
        if not await self.check_user(interaction):
            return
        values = interaction.data.get('values')
        if values:
            await self._persist(channel_id=int(values[0]))
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_role_select(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        role_ids = [int(r) for r in interaction.data.get('values', [])]
        await self._persist(mention_role_ids=role_ids)
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_edit_message(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        modal = EditMessageModal(self.locale, self.sub.get('message') or "", self._on_message_edited)
        modal.bot = self.bot
        await interaction.response.send_modal(modal)

    async def _on_message_edited(self, interaction: discord.Interaction, message: Optional[str]):
        await self._persist(message=message)
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_toggle(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        await self._persist(enabled=not self.sub.get('enabled', True))
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_remove(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        await interaction.response.defer()
        cog = self.bot.get_cog("SocialNotifications")
        if cog:
            await cog.remove_subscription(self.guild_id, self.sub['platform'], self.sub['target_id'])
        else:
            await self.bot.db.remove_social_subscription(
                self.guild_id, self.sub['platform'], self.sub['target_id']
            )
        await self.parent_view.refresh(interaction)
        await interaction.followup.send(
            t('modules.social_notifications.manage.removed', locale=self.locale),
            ephemeral=True,
        )

    async def on_back(self, interaction: discord.Interaction):
        if not await self.check_user(interaction):
            return
        await self.parent_view.refresh(interaction)

    async def check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                t('modules.config.errors.wrong_user', locale=self.locale), ephemeral=True
            )
            return False
        return True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.check_user(interaction)
