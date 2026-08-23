"""
``/config`` → Tickets → panel → **one category**, and its per-role permissions.

A category is the button (or dropdown option) a member clicks, and everything
that follows from it: where the ticket channel is created, who may open one,
what each staff role may do inside it, the language it speaks and the messages
it shows.

The screen is split the way the questions are:

- the **category screen** holds what an admin changes often — destination,
  access, language — as controls that show their own state;
- **identity**, **messages** and **options** are wording and numbers, so they
  live in modals;
- **permissions** get their own screen, because "pick a role, then tick what it
  may do" is inherently two steps.

Persistence: every control is a :class:`discord.ui.DynamicItem` carrying the
panel and category ids (and, on the permissions screen, the role id) in its
``custom_id``, registered through :class:`TicketsConfigPersistence`. The
wrapper views are therefore not registered — same reasoning as
``LogsCategoryView``, see docs/PERSISTENT_VIEWS.md — which is also why every
change here applies immediately.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import discord
from discord import ui

from cogs.error_handler import BaseModal, BaseView
from modules.configs._common import check_guild_perms
from modules.configs.tickets_config import (
    load_config,
    render_panel,
    render_root,
    report_save_error,
    save_config,
)
from modules.tickets import (
    BUTTON_STYLES,
    DEFAULT_ACCENT_COLOR,
    DEFAULT_BUTTON_STYLE,
    DEFAULT_MAX_OPEN_PER_USER,
    DEFAULT_NAME_FORMAT,
    DEFAULT_ROLE_PERMISSIONS,
    MAX_CATEGORY_DESCRIPTION,
    MAX_CATEGORY_NAME,
    MAX_CHANNEL_NAME,
    MAX_OPEN_PER_USER_CEILING,
    MAX_TICKET_MESSAGE,
    NAME_PLACEHOLDERS,
    DEFAULT_TICKET_LOCALE,
    PERM_ADMIN,
    PLACEHOLDERS,
    TICKET_LOCALES,
    TICKET_PERMISSIONS,
    default_close_message,
    default_open_message,
    find_panel,
    get_limits,
    locate_category,
    max_categories_for_style,
    new_category_id,
    parse_emoji,
)
from utils.components_v2 import create_error_message
from utils.emojis import (
    BACK, DELETE, EDIT, INFO, MESSAGE, PAUSE, PLAY, REQUIRED_FIELDS,
    TICKET_ACCESS, TICKET_CATEGORY, TICKET_LANGUAGE, TICKET_PERMISSIONS as PERM_EMOJI,
    SETTINGS, WARNING,
)
from utils.i18n import i18n, t

logger = logging.getLogger('moddy.modules.tickets_category_config')

_ENTRY_ID = r"[a-z0-9_]+"

_CAT_BUTTON_ACTIONS = "back|identity|messages|options|perms|toggle|delete|delconfirm"
_PERM_BUTTON_ACTIONS = "back|clear"
_ROLE_SCOPES = "allowed|denied"

# Flags are the one Unicode emoji Moddy uses (see CLAUDE.md rule 3).
_LOCALE_FLAGS = {
    "en-US": "🇬🇧",
    "fr": "🇫🇷",
    "es-ES": "🇪🇸",
    "pt-BR": "🇧🇷",
    "de": "🇩🇪",
}


def _guarded(callback):
    """Route a dynamic-item callback error to the central handler."""
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as e:  # noqa: BLE001
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, e, self.__class__.__name__)
    return wrapper


# =========================================================================== #
# Navigation helpers
# =========================================================================== #
async def render_category(interaction: discord.Interaction, panel_id: str,
                          category_id: str, *, confirm_delete: bool = False) -> None:
    """(Re)build and show one category's editor, falling back up the tree."""
    bot = interaction.client
    locale = i18n.get_user_locale(interaction)
    config = await load_config(bot, interaction.guild_id)
    panel, category = locate_category(config, panel_id, category_id)
    if not category:
        await (render_panel(interaction, panel_id) if panel
               else render_root(interaction))
        return

    view = TicketCategoryConfigView(bot, interaction.guild_id, locale, panel,
                                    category, confirm_delete=confirm_delete)
    await _edit(interaction, view)


async def render_permissions(interaction: discord.Interaction, panel_id: str,
                             category_id: str,
                             role_id: Optional[int] = None) -> None:
    """(Re)build and show the permissions screen of one category."""
    bot = interaction.client
    locale = i18n.get_user_locale(interaction)
    config = await load_config(bot, interaction.guild_id)
    panel, category = locate_category(config, panel_id, category_id)
    if not category:
        await (render_panel(interaction, panel_id) if panel
               else render_root(interaction))
        return

    view = TicketPermissionsConfigView(bot, interaction.guild_id, locale, panel,
                                       category, role_id)
    await _edit(interaction, view)


async def _edit(interaction: discord.Interaction, view: ui.LayoutView) -> None:
    if interaction.response.is_done():
        await interaction.edit_original_response(view=view)
    else:
        await interaction.response.edit_message(view=view)


async def _update_category(interaction: discord.Interaction, panel_id: str,
                           category_id: str, fields: Dict[str, Any], *,
                           repost: bool = False) -> None:
    """Apply ``fields`` to one category, persist, and re-render its editor.

    ``repost`` is only True for the handful of fields the *panel message*
    shows (name, emoji, colour, description, availability) — everything else
    changes what happens inside a ticket, not what the panel looks like.
    """
    bot = interaction.client
    config = await load_config(bot, interaction.guild_id)
    panel, category = locate_category(config, panel_id, category_id)
    if not category:
        await render_root(interaction)
        return

    category.update(fields)
    if not interaction.response.is_done():
        await interaction.response.defer()

    success, error = await save_config(
        bot, interaction.guild_id, config, interaction.user.id,
        repost_panel_id=panel_id if repost else None)
    if not success:
        await report_save_error(interaction, error)
        return
    await render_category(interaction, panel_id, category_id)


async def start_category_creation(interaction: discord.Interaction,
                                  panel_id: str) -> None:
    """Ask for the new category's identity, then open its editor."""
    bot = interaction.client
    locale = i18n.get_user_locale(interaction)
    config = await load_config(bot, interaction.guild_id)
    panel = find_panel(config, panel_id)
    if not panel:
        await render_root(interaction)
        return

    limits = await get_limits(bot, interaction.guild_id)
    cap = max_categories_for_style(panel['style'], limits['max_categories'])
    if len(panel.get('categories', [])) >= cap:
        description = t('modules.tickets.errors.category_limit', locale=locale, max=cap)
        if not limits.get('premium'):
            from modules.configs.tickets_config import PREMIUM_URL
            description += ("\n\n" + t('modules.tickets.errors.premium_upsell',
                                       locale=locale, url=PREMIUM_URL))
        await interaction.response.send_message(
            view=create_error_message(
                t('modules.tickets.errors.title', locale=locale), description),
            ephemeral=True)
        return

    async def create(inner: discord.Interaction, fields: Dict[str, Any]):
        inner_config = await load_config(bot, inner.guild_id)
        inner_panel = find_panel(inner_config, panel_id)
        if not inner_panel:
            await render_root(inner)
            return
        category_id = new_category_id()
        inner_panel.setdefault('categories', []).append({
            'id': category_id, 'enabled': True, **fields,
        })
        if not inner.response.is_done():
            await inner.response.defer()
        success, error = await save_config(bot, inner.guild_id, inner_config,
                                           inner.user.id, repost_panel_id=panel_id)
        if not success:
            await report_save_error(inner, error)
            return
        await render_category(inner, panel_id, category_id)

    modal = CategoryIdentityModal(locale, None, create)
    modal.bot = bot
    await interaction.response.send_modal(modal)


def _placeholder_help(locale: str, placeholders) -> str:
    """The ``{placeholder}`` cheat-sheet, fetched without kwargs.

    Passing them as translation kwargs would substitute the braces away, which
    is exactly what this block must not do.
    """
    lines = [t('modules.tickets.messages.placeholders_header', locale=locale)]
    for placeholder in placeholders:
        key = placeholder.strip('{}')
        lines.append(f"`{placeholder}` — "
                     f"{t(f'modules.tickets.messages.ph_{key}', locale=locale)}")
    return "\n".join(lines)


# =========================================================================== #
# Modals
# =========================================================================== #
class CategoryIdentityModal(BaseModal):
    """What the member sees on the panel: name, icon, blurb, button colour."""

    def __init__(self, locale: str, category: Optional[Dict[str, Any]], callback_func):
        category = category or {}
        super().__init__(
            title=t('modules.tickets.category.modal_title_new' if not category
                    else 'modules.tickets.category.modal_title_edit',
                    locale=locale)[:45],
            timeout=None,
        )
        self.locale = locale
        self.callback_func = callback_func

        self.name_input = ui.TextInput(
            style=discord.TextStyle.short, required=True,
            max_length=MAX_CATEGORY_NAME, default=category.get('name'),
        )
        self.add_item(ui.Label(
            text=t('modules.tickets.category.name_label', locale=locale)[:45],
            description=t('modules.tickets.category.name_hint', locale=locale)[:100],
            component=self.name_input,
        ))

        self.emoji_input = ui.TextInput(
            style=discord.TextStyle.short, required=False,
            max_length=64, default=category.get('emoji'),
        )
        self.add_item(ui.Label(
            text=t('modules.tickets.category.emoji_label', locale=locale)[:45],
            description=t('modules.tickets.category.emoji_hint', locale=locale)[:100],
            component=self.emoji_input,
        ))

        self.description_input = ui.TextInput(
            style=discord.TextStyle.short, required=False,
            max_length=MAX_CATEGORY_DESCRIPTION, default=category.get('description'),
        )
        self.add_item(ui.Label(
            text=t('modules.tickets.category.description_label', locale=locale)[:45],
            description=t('modules.tickets.category.description_hint',
                          locale=locale)[:100],
            component=self.description_input,
        ))

        current_style = category.get('button_style', DEFAULT_BUTTON_STYLE)
        self.style_select = ui.Select(
            options=[
                discord.SelectOption(
                    label=t(f'modules.tickets.category.color_{name}', locale=locale)[:100],
                    value=name, default=current_style == name,
                ) for name in BUTTON_STYLES
            ],
            min_values=1, max_values=1,
        )
        self.add_item(ui.Label(
            text=t('modules.tickets.category.color_label', locale=locale)[:45],
            description=t('modules.tickets.category.color_hint', locale=locale)[:100],
            component=self.style_select,
        ))

    async def on_submit(self, interaction: discord.Interaction):
        emoji_raw = (self.emoji_input.value or '').strip() or None
        # Reject an emoji Discord would refuse rather than letting it break the
        # whole panel message the next time it is posted.
        emoji = emoji_raw if (emoji_raw is None or parse_emoji(emoji_raw)) else None
        if emoji_raw and emoji is None:
            locale = i18n.get_user_locale(interaction)
            await interaction.response.send_message(
                view=create_error_message(
                    t('modules.tickets.errors.title', locale=locale),
                    t('modules.tickets.errors.invalid_emoji', locale=locale)),
                ephemeral=True)
            return

        values = self.style_select.values
        await self.callback_func(interaction, {
            'name': (self.name_input.value or '').strip(),
            'emoji': emoji,
            'description': (self.description_input.value or '').strip() or None,
            'button_style': values[0] if values else DEFAULT_BUTTON_STYLE,
        })


class CategoryMessagesModal(BaseModal):
    """The two messages a ticket shows: when it opens, and when it closes.

    Both fields are pre-filled with the wording the ticket would use anyway.
    They are written in the **category's** language, not the admin's: these
    are the words the member will read, and a ticket speaks one language
    whoever configured it. Only the labels around them follow the admin.
    """

    def __init__(self, locale: str, category: Dict[str, Any], callback_func):
        super().__init__(
            title=t('modules.tickets.messages.modal_title', locale=locale)[:45],
            timeout=None,
        )
        self.callback_func = callback_func
        ticket_locale = category.get('locale') or DEFAULT_TICKET_LOCALE

        self.open_input = ui.TextInput(
            style=discord.TextStyle.paragraph, required=False,
            max_length=MAX_TICKET_MESSAGE,
            default=category.get('open_message') or default_open_message(ticket_locale),
        )
        self.add_item(ui.Label(
            text=t('modules.tickets.messages.open_label', locale=locale)[:45],
            description=t('modules.tickets.messages.open_hint', locale=locale)[:100],
            component=self.open_input,
        ))

        self.add_item(ui.TextDisplay(_placeholder_help(locale, PLACEHOLDERS)))

        self.close_input = ui.TextInput(
            style=discord.TextStyle.paragraph, required=False,
            max_length=MAX_TICKET_MESSAGE,
            default=category.get('close_message') or default_close_message(ticket_locale),
        )
        self.add_item(ui.Label(
            text=t('modules.tickets.messages.close_label', locale=locale)[:45],
            description=t('modules.tickets.messages.close_hint', locale=locale)[:100],
            component=self.close_input,
        ))

    async def on_submit(self, interaction: discord.Interaction):
        await self.callback_func(interaction, {
            'open_message': (self.open_input.value or '').strip() or None,
            'close_message': (self.close_input.value or '').strip() or None,
        })


class CategoryOptionsModal(BaseModal):
    """Channel naming, the anti-spam limit, and who gets pinged on a new ticket."""

    def __init__(self, locale: str, category: Dict[str, Any], callback_func):
        super().__init__(
            title=t('modules.tickets.options.modal_title', locale=locale)[:45],
            timeout=None,
        )
        self.locale = locale
        self.callback_func = callback_func

        self.name_format_input = ui.TextInput(
            style=discord.TextStyle.short, required=False,
            max_length=MAX_CHANNEL_NAME,
            default=category.get('name_format') or DEFAULT_NAME_FORMAT,
        )
        self.add_item(ui.Label(
            text=t('modules.tickets.options.name_format_label', locale=locale)[:45],
            description=t('modules.tickets.options.name_format_hint',
                          locale=locale)[:100],
            component=self.name_format_input,
        ))

        self.add_item(ui.TextDisplay(_placeholder_help(locale, NAME_PLACEHOLDERS)))

        self.max_open_input = ui.TextInput(
            style=discord.TextStyle.short, required=False, max_length=2,
            default=str(category.get('max_open_per_user', DEFAULT_MAX_OPEN_PER_USER)),
        )
        self.add_item(ui.Label(
            text=t('modules.tickets.options.max_open_label', locale=locale)[:45],
            description=t('modules.tickets.options.max_open_hint',
                          locale=locale, max=MAX_OPEN_PER_USER_CEILING)[:100],
            component=self.max_open_input,
        ))

        self.ping_select = ui.RoleSelect(min_values=0, max_values=10, required=False)
        self.ping_select.default_values = [
            discord.Object(id=rid) for rid in category.get('ping_role_ids', [])
        ]
        self.add_item(ui.Label(
            text=t('modules.tickets.options.ping_label', locale=locale)[:45],
            description=t('modules.tickets.options.ping_hint', locale=locale)[:100],
            component=self.ping_select,
        ))

    async def on_submit(self, interaction: discord.Interaction):
        raw = (self.max_open_input.value or '').strip()
        try:
            max_open = int(raw) if raw else DEFAULT_MAX_OPEN_PER_USER
        except ValueError:
            max_open = DEFAULT_MAX_OPEN_PER_USER
        max_open = max(1, min(max_open, MAX_OPEN_PER_USER_CEILING))

        await self.callback_func(interaction, {
            'name_format': (self.name_format_input.value or '').strip()
                           or DEFAULT_NAME_FORMAT,
            'max_open_per_user': max_open,
            'ping_role_ids': [role.id for role in self.ping_select.values],
        })


# =========================================================================== #
# Category screen — dynamic items
# =========================================================================== #
class TicketCategoryButton(
    ui.DynamicItem[ui.Button],
    template=(rf"moddy:tickets:catbtn:(?P<action>{_CAT_BUTTON_ACTIONS}):"
              rf"(?P<panel_id>{_ENTRY_ID}):(?P<category_id>{_ENTRY_ID})"),
):
    """An action button on the category editor. Auth: Manage Server."""

    def __init__(self, action: str, panel_id: str, category_id: str, *,
                 label: str = "—", style: discord.ButtonStyle = discord.ButtonStyle.secondary,
                 emoji: Optional[str] = None):
        super().__init__(ui.Button(
            label=label[:80], style=style,
            emoji=discord.PartialEmoji.from_str(emoji) if emoji else None,
            custom_id=f"moddy:tickets:catbtn:{action}:{panel_id}:{category_id}",
        ))
        self.action = action
        self.panel_id = panel_id
        self.category_id = category_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match['action'], match['panel_id'], match['category_id'])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await getattr(self, f"_on_{self.action}")(interaction)

    async def _category(self, interaction) -> Optional[Dict[str, Any]]:
        config = await load_config(interaction.client, interaction.guild_id)
        _, category = locate_category(config, self.panel_id, self.category_id)
        return category

    # -- actions ----------------------------------------------------------- #
    async def _on_back(self, interaction: discord.Interaction):
        await render_panel(interaction, self.panel_id)

    async def _on_identity(self, interaction: discord.Interaction):
        category = await self._category(interaction)
        if not category:
            await render_panel(interaction, self.panel_id)
            return
        modal = CategoryIdentityModal(
            i18n.get_user_locale(interaction), category, self._apply_identity)
        modal.bot = interaction.client
        await interaction.response.send_modal(modal)

    async def _apply_identity(self, interaction, fields):
        await _update_category(interaction, self.panel_id, self.category_id,
                               fields, repost=True)

    async def _on_messages(self, interaction: discord.Interaction):
        category = await self._category(interaction)
        if not category:
            await render_panel(interaction, self.panel_id)
            return
        modal = CategoryMessagesModal(
            i18n.get_user_locale(interaction), category, self._apply_plain)
        modal.bot = interaction.client
        await interaction.response.send_modal(modal)

    async def _on_options(self, interaction: discord.Interaction):
        category = await self._category(interaction)
        if not category:
            await render_panel(interaction, self.panel_id)
            return
        modal = CategoryOptionsModal(
            i18n.get_user_locale(interaction), category, self._apply_plain)
        modal.bot = interaction.client
        await interaction.response.send_modal(modal)

    async def _apply_plain(self, interaction, fields):
        await _update_category(interaction, self.panel_id, self.category_id, fields)

    async def _on_perms(self, interaction: discord.Interaction):
        await render_permissions(interaction, self.panel_id, self.category_id)

    async def _on_toggle(self, interaction: discord.Interaction):
        category = await self._category(interaction)
        if not category:
            await render_panel(interaction, self.panel_id)
            return
        await _update_category(interaction, self.panel_id, self.category_id,
                               {'enabled': not category.get('enabled', True)},
                               repost=True)

    async def _on_delete(self, interaction: discord.Interaction):
        await render_category(interaction, self.panel_id, self.category_id,
                              confirm_delete=True)

    async def _on_delconfirm(self, interaction: discord.Interaction):
        bot = interaction.client
        config = await load_config(bot, interaction.guild_id)
        panel = find_panel(config, self.panel_id)
        if not panel:
            await render_root(interaction)
            return

        panel['categories'] = [c for c in panel.get('categories', [])
                               if c['id'] != self.category_id]
        await interaction.response.defer()
        success, error = await save_config(bot, interaction.guild_id, config,
                                           interaction.user.id,
                                           repost_panel_id=self.panel_id)
        if not success:
            await report_save_error(interaction, error)
            return
        await render_panel(interaction, self.panel_id)


class TicketCategoryDestination(
    ui.DynamicItem[ui.ChannelSelect],
    template=(rf"moddy:tickets:catdest:(?P<panel_id>{_ENTRY_ID}):"
              rf"(?P<category_id>{_ENTRY_ID})"),
):
    """Which Discord category the ticket channels are created in."""

    def __init__(self, panel_id: str, category_id: str, *,
                 placeholder: Optional[str] = None,
                 current: Optional[discord.CategoryChannel] = None):
        select = ui.ChannelSelect(
            placeholder=placeholder,
            channel_types=[discord.ChannelType.category],
            min_values=0, max_values=1,
            custom_id=f"moddy:tickets:catdest:{panel_id}:{category_id}",
        )
        if current is not None:
            select.default_values = [current]
        super().__init__(select)
        self.panel_id = panel_id
        self.category_id = category_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match['panel_id'], match['category_id'])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        values = interaction.data.get('values') or []
        # A category with no destination cannot be opened, so it disappears
        # from the panel — hence the re-post.
        await _update_category(
            interaction, self.panel_id, self.category_id,
            {'discord_category_id': int(values[0]) if values else None}, repost=True)


class TicketCategoryRoles(
    ui.DynamicItem[ui.RoleSelect],
    template=(rf"moddy:tickets:catroles:(?P<scope>{_ROLE_SCOPES}):"
              rf"(?P<panel_id>{_ENTRY_ID}):(?P<category_id>{_ENTRY_ID})"),
):
    """The allow list / deny list deciding who may open this category."""

    def __init__(self, scope: str, panel_id: str, category_id: str, *,
                 placeholder: Optional[str] = None,
                 current: Optional[List[int]] = None):
        select = ui.RoleSelect(
            placeholder=placeholder, min_values=0, max_values=10,
            custom_id=f"moddy:tickets:catroles:{scope}:{panel_id}:{category_id}",
        )
        select.default_values = [discord.Object(id=rid) for rid in (current or [])]
        super().__init__(select)
        self.scope = scope
        self.panel_id = panel_id
        self.category_id = category_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match['scope'], match['panel_id'], match['category_id'])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        role_ids = [int(v) for v in (interaction.data.get('values') or [])]
        field = 'allowed_role_ids' if self.scope == 'allowed' else 'denied_role_ids'
        await _update_category(interaction, self.panel_id, self.category_id,
                               {field: role_ids})


class TicketCategoryLocale(
    ui.DynamicItem[ui.Select],
    template=(rf"moddy:tickets:catlang:(?P<panel_id>{_ENTRY_ID}):"
              rf"(?P<category_id>{_ENTRY_ID})"),
):
    """The language every message of this category's tickets is written in."""

    def __init__(self, panel_id: str, category_id: str, *,
                 locale: str = "en-US", current: Optional[str] = None):
        super().__init__(ui.Select(
            options=[
                discord.SelectOption(
                    label=t(f'languages.{code}', locale=locale),
                    value=code, emoji=_LOCALE_FLAGS.get(code),
                    default=code == current,
                ) for code in TICKET_LOCALES
            ],
            min_values=1, max_values=1,
            custom_id=f"moddy:tickets:catlang:{panel_id}:{category_id}",
        ))
        self.panel_id = panel_id
        self.category_id = category_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match['panel_id'], match['category_id'])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        values = interaction.data.get('values') or []
        if not values:
            await interaction.response.defer()
            return
        await _update_category(interaction, self.panel_id, self.category_id,
                               {'locale': values[0]})


# =========================================================================== #
# Category screen
# =========================================================================== #
class TicketCategoryConfigView(BaseView):
    """One category: destination, access, language, and the way in to the rest.

    Not registered — see the module docstring. Auth: Manage Server, re-checked
    inside every dynamic item's callback.
    """

    def __init__(self, bot, guild_id: int, locale: str, panel: Dict[str, Any],
                 category: Dict[str, Any], *, confirm_delete: bool = False):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.locale = locale
        self.panel = panel
        self.category = category
        self.confirm_delete = confirm_delete

        self._build_view()

    def _build_view(self):
        self.clear_items()
        container = ui.Container(accent_colour=discord.Colour(
            self.panel.get('accent_color') or DEFAULT_ACCENT_COLOR))

        icon = self.category.get('emoji') or TICKET_CATEGORY
        container.add_item(ui.TextDisplay(f"### {icon} {self.category['name']}"))
        container.add_item(ui.TextDisplay(
            f"-# {t('modules.tickets.category.subtitle', locale=self.locale, panel=self.panel['name'])}"))

        if not self.category.get('enabled', True):
            container.add_item(ui.TextDisplay(
                f"{WARNING} {t('modules.tickets.category.paused_notice', locale=self.locale)}"))

        if self.confirm_delete:
            self._build_delete_confirmation(container)
            self.add_item(container)
            return

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # 1. Destination.
        container.add_item(ui.TextDisplay(
            f"**{t('modules.tickets.category.destination_title', locale=self.locale)}**{REQUIRED_FIELDS}\n"
            f"-# {t('modules.tickets.category.destination_hint', locale=self.locale)}"))
        current = self.bot.get_channel(self.category['discord_category_id']) \
            if self.bot and self.category.get('discord_category_id') else None
        dest_row = ui.ActionRow()
        dest_row.add_item(TicketCategoryDestination(
            self.panel['id'], self.category['id'],
            placeholder=t('modules.tickets.category.destination_placeholder',
                          locale=self.locale),
            current=current if isinstance(current, discord.CategoryChannel) else None,
        ))
        container.add_item(dest_row)

        # 2. Access.
        container.add_item(ui.TextDisplay(
            f"**{TICKET_ACCESS} {t('modules.tickets.category.access_title', locale=self.locale)}**\n"
            f"-# {t('modules.tickets.category.access_hint', locale=self.locale)}"))
        allowed_row = ui.ActionRow()
        allowed_row.add_item(TicketCategoryRoles(
            'allowed', self.panel['id'], self.category['id'],
            placeholder=t('modules.tickets.category.allowed_placeholder',
                          locale=self.locale),
            current=self.category.get('allowed_role_ids', []),
        ))
        container.add_item(allowed_row)

        container.add_item(ui.TextDisplay(
            f"-# {t('modules.tickets.category.denied_hint', locale=self.locale)}"))
        denied_row = ui.ActionRow()
        denied_row.add_item(TicketCategoryRoles(
            'denied', self.panel['id'], self.category['id'],
            placeholder=t('modules.tickets.category.denied_placeholder',
                          locale=self.locale),
            current=self.category.get('denied_role_ids', []),
        ))
        container.add_item(denied_row)

        # 3. Language.
        container.add_item(ui.TextDisplay(
            f"**{TICKET_LANGUAGE} {t('modules.tickets.category.language_title', locale=self.locale)}**\n"
            f"-# {t('modules.tickets.category.language_hint', locale=self.locale)}"))
        lang_row = ui.ActionRow()
        lang_row.add_item(TicketCategoryLocale(
            self.panel['id'], self.category['id'],
            locale=self.locale, current=self.category.get('locale')))
        container.add_item(lang_row)

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # 4. What is configured behind the buttons below.
        container.add_item(ui.TextDisplay(self._summary()))

        self.add_item(container)
        self._build_buttons()

    def _summary(self) -> str:
        """One line per modal-backed setting — the only way to read them here."""
        roles = len(self.category.get('permissions', {}))
        messages = sum(1 for key in ('open_message', 'close_message')
                       if self.category.get(key))
        return (
            f"**{t('modules.tickets.category.summary_title', locale=self.locale)}**\n"
            f"-# {PERM_EMOJI} "
            f"{t('modules.tickets.category.summary_permissions', locale=self.locale, roles=roles)}\n"
            f"-# {MESSAGE} "
            f"{t('modules.tickets.category.summary_messages', locale=self.locale, count=messages)}\n"
            f"-# {SETTINGS} "
            f"{t('modules.tickets.category.summary_options', locale=self.locale, format=self.category.get('name_format', ''), max=self.category.get('max_open_per_user', 1))}"
        )

    def _build_delete_confirmation(self, container: ui.Container):
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(
            f"{WARNING} **{t('modules.tickets.category.delete_title', locale=self.locale)}**\n"
            f"{t('modules.tickets.category.delete_description', locale=self.locale)}"))

        row = ui.ActionRow()
        row.add_item(TicketCategoryButton(
            'back', self.panel['id'], self.category['id'],
            label=t('modules.config.buttons.cancel', locale=self.locale),
            style=discord.ButtonStyle.secondary, emoji=BACK))
        row.add_item(TicketCategoryButton(
            'delconfirm', self.panel['id'], self.category['id'],
            label=t('modules.tickets.category.delete_confirm', locale=self.locale),
            style=discord.ButtonStyle.danger, emoji=DELETE))
        container.add_item(row)

    def _build_buttons(self):
        panel_id, category_id = self.panel['id'], self.category['id']
        enabled = self.category.get('enabled', True)

        row = ui.ActionRow()
        row.add_item(TicketCategoryButton(
            'back', panel_id, category_id,
            label=t('modules.config.buttons.back', locale=self.locale),
            style=discord.ButtonStyle.secondary, emoji=BACK))
        row.add_item(TicketCategoryButton(
            'identity', panel_id, category_id,
            label=t('modules.tickets.category.identity', locale=self.locale),
            style=discord.ButtonStyle.primary, emoji=EDIT))
        row.add_item(TicketCategoryButton(
            'perms', panel_id, category_id,
            label=t('modules.tickets.category.permissions', locale=self.locale),
            style=discord.ButtonStyle.primary, emoji=PERM_EMOJI))
        row.add_item(TicketCategoryButton(
            'messages', panel_id, category_id,
            label=t('modules.tickets.category.messages', locale=self.locale),
            style=discord.ButtonStyle.secondary, emoji=MESSAGE))
        row.add_item(TicketCategoryButton(
            'options', panel_id, category_id,
            label=t('modules.tickets.category.options', locale=self.locale),
            style=discord.ButtonStyle.secondary, emoji=SETTINGS))
        self.add_item(row)

        row2 = ui.ActionRow()
        row2.add_item(TicketCategoryButton(
            'toggle', panel_id, category_id,
            label=t('modules.tickets.category.pause' if enabled
                    else 'modules.tickets.category.resume', locale=self.locale),
            style=discord.ButtonStyle.secondary, emoji=PAUSE if enabled else PLAY))
        row2.add_item(TicketCategoryButton(
            'delete', panel_id, category_id,
            label=t('modules.tickets.category.delete', locale=self.locale),
            style=discord.ButtonStyle.danger, emoji=DELETE))
        self.add_item(row2)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_guild_perms(interaction)


# =========================================================================== #
# Permissions screen — dynamic items
# =========================================================================== #
class TicketPermRoleSelect(
    ui.DynamicItem[ui.RoleSelect],
    template=(rf"moddy:tickets:permrole:(?P<panel_id>{_ENTRY_ID}):"
              rf"(?P<category_id>{_ENTRY_ID})"),
):
    """Pick the role whose permissions are being edited."""

    def __init__(self, panel_id: str, category_id: str, *,
                 placeholder: Optional[str] = None,
                 current: Optional[int] = None):
        select = ui.RoleSelect(
            placeholder=placeholder, min_values=0, max_values=1,
            custom_id=f"moddy:tickets:permrole:{panel_id}:{category_id}",
        )
        if current:
            select.default_values = [discord.Object(id=current)]
        super().__init__(select)
        self.panel_id = panel_id
        self.category_id = category_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match['panel_id'], match['category_id'])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        values = interaction.data.get('values') or []
        await render_permissions(interaction, self.panel_id, self.category_id,
                                 int(values[0]) if values else None)


class TicketPermSelect(
    ui.DynamicItem[ui.Select],
    template=(rf"moddy:tickets:permset:(?P<panel_id>{_ENTRY_ID}):"
              rf"(?P<category_id>{_ENTRY_ID}):(?P<role_id>[0-9]+)"),
):
    """What the selected role may do inside this category's tickets."""

    def __init__(self, panel_id: str, category_id: str, role_id: int, *,
                 locale: str = "en-US", granted: Optional[List[str]] = None,
                 placeholder: Optional[str] = None):
        granted = granted or []
        super().__init__(ui.Select(
            placeholder=placeholder,
            options=[
                discord.SelectOption(
                    label=t(f'modules.tickets.permissions.{perm}.name', locale=locale)[:100],
                    value=perm,
                    description=t(f'modules.tickets.permissions.{perm}.description',
                                  locale=locale)[:100],
                    default=perm in granted,
                ) for perm in TICKET_PERMISSIONS
            ],
            min_values=0, max_values=len(TICKET_PERMISSIONS),
            custom_id=f"moddy:tickets:permset:{panel_id}:{category_id}:{role_id}",
        ))
        self.panel_id = panel_id
        self.category_id = category_id
        self.role_id = role_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match['panel_id'], match['category_id'], int(match['role_id']))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        chosen = [p for p in TICKET_PERMISSIONS
                  if p in (interaction.data.get('values') or [])]

        bot = interaction.client
        config = await load_config(bot, interaction.guild_id)
        _, category = locate_category(config, self.panel_id, self.category_id)
        if not category:
            await render_root(interaction)
            return

        permissions = dict(category.get('permissions', {}))
        if chosen:
            permissions[str(self.role_id)] = chosen
        else:
            # No permission left is the same thing as no entry — keeping an
            # empty one would show a role in the list that can do nothing.
            permissions.pop(str(self.role_id), None)
        category['permissions'] = permissions

        await interaction.response.defer()
        success, error = await save_config(bot, interaction.guild_id, config,
                                           interaction.user.id)
        if not success:
            await report_save_error(interaction, error)
            return
        await render_permissions(interaction, self.panel_id, self.category_id,
                                 self.role_id if chosen else None)


class TicketPermButton(
    ui.DynamicItem[ui.Button],
    template=(rf"moddy:tickets:permbtn:(?P<action>{_PERM_BUTTON_ACTIONS}):"
              rf"(?P<panel_id>{_ENTRY_ID}):(?P<category_id>{_ENTRY_ID}):"
              rf"(?P<role_id>[0-9]+)"),
):
    """Back to the category, or revoke everything from the selected role.

    ``role_id`` is ``0`` when no role is selected — always encoding it keeps
    one template for both buttons instead of two that could cross-match.
    """

    def __init__(self, action: str, panel_id: str, category_id: str,
                 role_id: int = 0, *, label: str = "—",
                 style: discord.ButtonStyle = discord.ButtonStyle.secondary,
                 emoji: Optional[str] = None):
        super().__init__(ui.Button(
            label=label[:80], style=style,
            emoji=discord.PartialEmoji.from_str(emoji) if emoji else None,
            custom_id=f"moddy:tickets:permbtn:{action}:{panel_id}:{category_id}:{role_id}",
        ))
        self.action = action
        self.panel_id = panel_id
        self.category_id = category_id
        self.role_id = role_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match['action'], match['panel_id'], match['category_id'],
                   int(match['role_id']))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return

        if self.action == 'back':
            await render_category(interaction, self.panel_id, self.category_id)
            return

        # 'clear'
        bot = interaction.client
        config = await load_config(bot, interaction.guild_id)
        _, category = locate_category(config, self.panel_id, self.category_id)
        if not category or not self.role_id:
            await render_permissions(interaction, self.panel_id, self.category_id)
            return

        permissions = dict(category.get('permissions', {}))
        permissions.pop(str(self.role_id), None)
        category['permissions'] = permissions

        await interaction.response.defer()
        success, error = await save_config(bot, interaction.guild_id, config,
                                           interaction.user.id)
        if not success:
            await report_save_error(interaction, error)
            return
        await render_permissions(interaction, self.panel_id, self.category_id)


# =========================================================================== #
# Permissions screen
# =========================================================================== #
class TicketPermissionsConfigView(BaseView):
    """Pick a role, tick what it may do. Not registered — see module docstring."""

    def __init__(self, bot, guild_id: int, locale: str, panel: Dict[str, Any],
                 category: Dict[str, Any], role_id: Optional[int] = None):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.locale = locale
        self.panel = panel
        self.category = category
        self.role_id = role_id

        self._build_view()

    def _build_view(self):
        self.clear_items()
        container = ui.Container(accent_colour=discord.Colour(
            self.panel.get('accent_color') or DEFAULT_ACCENT_COLOR))

        container.add_item(ui.TextDisplay(
            f"### {PERM_EMOJI} "
            f"{t('modules.tickets.permissions.title', locale=self.locale)}"))
        container.add_item(ui.TextDisplay(
            f"-# {t('modules.tickets.permissions.subtitle', locale=self.locale, category=self.category['name'])}"))
        container.add_item(ui.TextDisplay(
            t('modules.tickets.permissions.description', locale=self.locale)))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Who is already configured.
        configured = self.category.get('permissions', {})
        if configured:
            container.add_item(ui.TextDisplay(
                f"**{t('modules.tickets.permissions.configured_title', locale=self.locale)}**"))
            for role_id, perms in configured.items():
                container.add_item(ui.TextDisplay(self._render_role(role_id, perms)))
        else:
            container.add_item(ui.TextDisplay(
                f"{WARNING} "
                f"{t('modules.tickets.permissions.nobody', locale=self.locale)}"))

        # Role picker.
        container.add_item(ui.TextDisplay(
            f"**{t('modules.tickets.permissions.pick_role', locale=self.locale)}**\n"
            f"-# {t('modules.tickets.permissions.pick_role_hint', locale=self.locale)}"))
        role_row = ui.ActionRow()
        role_row.add_item(TicketPermRoleSelect(
            self.panel['id'], self.category['id'],
            placeholder=t('modules.tickets.permissions.role_placeholder',
                          locale=self.locale),
            current=self.role_id,
        ))
        container.add_item(role_row)

        # Permission picker for the selected role.
        if self.role_id:
            granted = configured.get(str(self.role_id))
            if granted is None:
                # First time this role is touched: start from a sane set
                # instead of an empty one nobody would understand.
                granted = list(DEFAULT_ROLE_PERMISSIONS)
                container.add_item(ui.TextDisplay(
                    f"-# {INFO} "
                    f"{t('modules.tickets.permissions.defaults_hint', locale=self.locale)}"))
            perm_row = ui.ActionRow()
            perm_row.add_item(TicketPermSelect(
                self.panel['id'], self.category['id'], self.role_id,
                locale=self.locale, granted=granted,
                placeholder=t('modules.tickets.permissions.perms_placeholder',
                              locale=self.locale),
            ))
            container.add_item(perm_row)

        self.add_item(container)

        row = ui.ActionRow()
        row.add_item(TicketPermButton(
            'back', self.panel['id'], self.category['id'], 0,
            label=t('modules.config.buttons.back', locale=self.locale),
            style=discord.ButtonStyle.secondary, emoji=BACK))
        if self.role_id and str(self.role_id) in configured:
            row.add_item(TicketPermButton(
                'clear', self.panel['id'], self.category['id'], self.role_id,
                label=t('modules.tickets.permissions.clear', locale=self.locale),
                style=discord.ButtonStyle.danger, emoji=DELETE))
        self.add_item(row)

    def _render_role(self, role_id: str, perms: List[str]) -> str:
        if PERM_ADMIN in perms:
            summary = t('modules.tickets.permissions.admin.name', locale=self.locale)
        else:
            summary = ", ".join(
                t(f'modules.tickets.permissions.{p}.name', locale=self.locale)
                for p in TICKET_PERMISSIONS if p in perms)
        return f"<@&{role_id}>\n-# {summary or '—'}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_guild_perms(interaction)


# =========================================================================== #
# Persistence marker
# =========================================================================== #
class TicketsConfigPersistence(BaseView):
    """Marker view: registers every dynamic item of the ticket config screens.

    The panel, category and permission screens are all built from dynamic
    items rather than registered views, because each of them is scoped to an
    entity (a panel, a category, a role) that a static custom_id cannot carry.
    """

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        from modules.configs.tickets_panel_config import (
            TicketPanelButton, TicketPanelChannelSelect, TicketPanelSelect,
        )
        bot.add_dynamic_items(
            TicketPanelButton, TicketPanelSelect, TicketPanelChannelSelect,
            TicketCategoryButton, TicketCategoryDestination, TicketCategoryRoles,
            TicketCategoryLocale,
            TicketPermRoleSelect, TicketPermSelect, TicketPermButton,
        )
