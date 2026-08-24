"""
``/config`` → Tickets → **one panel**.

Where the panel message goes, how it renders, and the categories it offers.

Persistence
-----------
Every control here is a :class:`discord.ui.DynamicItem` carrying the panel id
in its ``custom_id`` (``moddy:tickets:pnl…:<panel_id>``), registered through
:class:`~modules.configs.tickets_category_config.TicketsConfigPersistence`.
The wrapper view is therefore **not** registered: it cannot be built without a
panel, and there is nothing left for it to carry once its children reconstruct
themselves on every click. Same pattern as ``LogsCategoryView`` — see
docs/PERSISTENT_VIEWS.md "Deliberate exclusions".

That is also why changes apply immediately: a ``DynamicItem`` has no ``self``
to stage them on.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

import discord
from discord import ui

from cogs.error_handler import BaseView
from modules.configs._common import check_guild_perms
from modules.configs.tickets_config import (
    PanelAppearanceModal,
    load_config,
    premium_hint,
    render_panel,
    render_root,
    report_save_error,
    save_config,
)
from modules.tickets import (
    DEFAULT_ACCENT_COLOR,
    PANEL_CHANNEL_TYPES,
    STYLE_BUTTONS,
    STYLE_SELECT,
    find_panel,
    get_limits,
    max_categories_for_style,
)
from utils.components_v2 import create_error_message
from utils.emojis import (
    ADD, BACK, DELETE, EDIT, INFO, PAUSE, PLAY, REQUIRED_FIELDS, SYNC,
    TICKET_CATEGORY, TICKET_PANEL, WARNING,
)
from utils.i18n import i18n, t

logger = logging.getLogger('moddy.modules.tickets_panel_config')

_ENTRY_ID = r"[a-z0-9_]+"

# One template per component *type*, with disjoint action alternations, so no
# two DynamicItem classes can ever cross-match (see docs/PERSISTENT_VIEWS.md).
_BUTTON_ACTIONS = "back|edit|addcat|repost|toggle|delete|delconfirm"
_SELECT_ACTIONS = "style|category"


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
# Dynamic items
# =========================================================================== #
class TicketPanelButton(
    ui.DynamicItem[ui.Button],
    template=rf"moddy:tickets:pnlbtn:(?P<action>{_BUTTON_ACTIONS}):(?P<panel_id>{_ENTRY_ID})",
):
    """An action button on the panel editor. Auth: Manage Server."""

    def __init__(self, action: str, panel_id: str, *, label: str = "—",
                 style: discord.ButtonStyle = discord.ButtonStyle.secondary,
                 emoji: Optional[str] = None, disabled: bool = False):
        super().__init__(
            ui.Button(
                label=label[:80], style=style, disabled=disabled,
                emoji=discord.PartialEmoji.from_str(emoji) if emoji else None,
                custom_id=f"moddy:tickets:pnlbtn:{action}:{panel_id}",
            )
        )
        self.action = action
        self.panel_id = panel_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match['action'], match['panel_id'])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        handler = getattr(self, f"_on_{self.action}")
        await handler(interaction)

    # -- actions ----------------------------------------------------------- #
    async def _on_back(self, interaction: discord.Interaction):
        await render_root(interaction)

    async def _on_edit(self, interaction: discord.Interaction):
        panel = await _fetch_panel(interaction, self.panel_id)
        if panel is None:
            await render_root(interaction)
            return
        modal = PanelAppearanceModal(
            i18n.get_user_locale(interaction), panel, self._apply_edit)
        modal.bot = interaction.client
        await interaction.response.send_modal(modal)

    async def _apply_edit(self, interaction: discord.Interaction,
                          fields: Dict[str, Any]):
        await _update_panel(interaction, self.panel_id, fields, repost=True)

    async def _on_addcat(self, interaction: discord.Interaction):
        from modules.configs.tickets_category_config import start_category_creation
        await start_category_creation(interaction, self.panel_id)

    async def _on_repost(self, interaction: discord.Interaction):
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        await interaction.response.defer()

        module = await bot.module_manager.get_module_instance(
            interaction.guild_id, 'tickets')
        panel = module.get_panel(self.panel_id) if module else None
        message = await module.refresh_panel(panel) if panel else None

        await render_panel(interaction, self.panel_id)
        if message is None:
            await interaction.followup.send(
                view=create_error_message(
                    t('modules.tickets.errors.title', locale=locale),
                    t('modules.tickets.panel.repost_failed', locale=locale)),
                ephemeral=True)

    async def _on_toggle(self, interaction: discord.Interaction):
        panel = await _fetch_panel(interaction, self.panel_id)
        if panel is None:
            await render_root(interaction)
            return
        await _update_panel(interaction, self.panel_id,
                            {'enabled': not panel.get('enabled', True)}, repost=True)

    async def _on_delete(self, interaction: discord.Interaction):
        """First click: ask. The panel screen re-renders in confirmation mode."""
        panel = await _fetch_panel(interaction, self.panel_id)
        if panel is None:
            await render_root(interaction)
            return
        bot = interaction.client
        limits = await get_limits(bot, interaction.guild_id)
        await interaction.response.edit_message(view=TicketPanelConfigView(
            bot, interaction.guild_id, i18n.get_user_locale(interaction),
            panel, limits, confirm_delete=True))

    async def _on_delconfirm(self, interaction: discord.Interaction):
        bot = interaction.client
        await interaction.response.defer()

        # Take the message down before forgetting where it is.
        module = await bot.module_manager.get_module_instance(
            interaction.guild_id, 'tickets')
        panel = module.get_panel(self.panel_id) if module else None
        if panel:
            await module.delete_panel(panel, persist=False)

        config = await load_config(bot, interaction.guild_id)
        config['panels'] = [p for p in config['panels'] if p['id'] != self.panel_id]
        success, error = await save_config(bot, interaction.guild_id, config,
                                           interaction.user.id)
        if not success:
            await report_save_error(interaction, error)
            return
        await render_root(interaction)


class TicketPanelSelect(
    ui.DynamicItem[ui.Select],
    template=rf"moddy:tickets:pnlsel:(?P<action>{_SELECT_ACTIONS}):(?P<panel_id>{_ENTRY_ID})",
):
    """A dropdown on the panel editor (channel, style, category picker)."""

    def __init__(self, action: str, panel_id: str, *,
                 item: Optional[ui.Item] = None):
        super().__init__(item or ui.Select(
            options=[discord.SelectOption(label="—", value="none")],
            custom_id=f"moddy:tickets:pnlsel:{action}:{panel_id}",
        ))
        self.action = action
        self.panel_id = panel_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match['action'], match['panel_id'])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        values = interaction.data.get('values') or []

        if self.action == 'style':
            if not values:
                await interaction.response.defer()
                return
            await _update_panel(interaction, self.panel_id,
                                {'style': values[0]}, repost=True)
            return

        # 'category'
        if not values or values[0] == "none":
            await interaction.response.defer()
            return
        from modules.configs.tickets_category_config import render_category
        await render_category(interaction, self.panel_id, values[0])


class TicketPanelChannelSelect(
    ui.DynamicItem[ui.ChannelSelect],
    template=rf"moddy:tickets:pnlchan:(?P<panel_id>{_ENTRY_ID})",
):
    """Where the panel message is posted. Auth: Manage Server."""

    def __init__(self, panel_id: str, *, placeholder: Optional[str] = None,
                 channel: Optional[discord.abc.GuildChannel] = None):
        select = ui.ChannelSelect(
            placeholder=placeholder, channel_types=PANEL_CHANNEL_TYPES,
            min_values=0, max_values=1,
            custom_id=f"moddy:tickets:pnlchan:{panel_id}",
        )
        if channel is not None:
            select.default_values = [channel]
        super().__init__(select)
        self.panel_id = panel_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match['panel_id'])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        values = interaction.data.get('values') or []
        await _update_panel(interaction, self.panel_id,
                            {'channel_id': int(values[0]) if values else None},
                            repost=True)


# =========================================================================== #
# Shared write path
# =========================================================================== #
async def _fetch_panel(interaction: discord.Interaction,
                       panel_id: str) -> Optional[Dict[str, Any]]:
    config = await load_config(interaction.client, interaction.guild_id)
    return find_panel(config, panel_id)


async def _update_panel(interaction: discord.Interaction, panel_id: str,
                        fields: Dict[str, Any], *, repost: bool) -> None:
    """Apply ``fields`` to one panel, persist, and re-render the editor."""
    bot = interaction.client
    config = await load_config(bot, interaction.guild_id)
    panel = find_panel(config, panel_id)
    if not panel:
        await render_root(interaction)
        return

    panel.update(fields)
    if not interaction.response.is_done():
        await interaction.response.defer()

    success, error = await save_config(
        bot, interaction.guild_id, config, interaction.user.id,
        repost_panel_id=panel_id if repost else None)
    if not success:
        await report_save_error(interaction, error)
        return
    await render_panel(interaction, panel_id)


# =========================================================================== #
# The panel editor
# =========================================================================== #
class TicketPanelConfigView(BaseView):
    """One panel: its message, its rendering, its categories.

    Not registered — see the module docstring. Auth: Manage Server, re-checked
    inside every dynamic item's callback.
    """

    def __init__(self, bot, guild_id: int, locale: str, panel: Dict[str, Any],
                 limits: Dict[str, Any], *, confirm_delete: bool = False):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.locale = locale
        self.panel = panel
        self.limits = limits
        self.confirm_delete = confirm_delete

        self._build_view()

    # -- construction ------------------------------------------------------ #
    def _build_view(self):
        self.clear_items()
        container = ui.Container(accent_colour=discord.Colour(
            self.panel.get('accent_color') or DEFAULT_ACCENT_COLOR))

        container.add_item(ui.TextDisplay(
            f"### {TICKET_PANEL} {self.panel['name']}"))
        container.add_item(ui.TextDisplay(
            f"-# {t('modules.tickets.panel.subtitle', locale=self.locale)}"))

        if not self.panel.get('enabled', True):
            container.add_item(ui.TextDisplay(
                f"{WARNING} {t('modules.tickets.panel.paused_notice', locale=self.locale)}"))

        if self.confirm_delete:
            self._build_delete_confirmation(container)
            self.add_item(container)
            return

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # 1. Channel.
        container.add_item(ui.TextDisplay(
            f"**{t('modules.tickets.panel.channel_title', locale=self.locale)}**{REQUIRED_FIELDS}\n"
            f"-# {t('modules.tickets.panel.channel_hint', locale=self.locale)}"))
        channel = self.bot.get_channel(self.panel['channel_id']) \
            if self.bot and self.panel.get('channel_id') else None
        channel_row = ui.ActionRow()
        channel_row.add_item(TicketPanelChannelSelect(
            self.panel['id'],
            placeholder=t('modules.tickets.panel.channel_placeholder',
                          locale=self.locale),
            channel=channel,
        ))
        container.add_item(channel_row)

        # 2. Rendering style.
        container.add_item(ui.TextDisplay(
            f"**{t('modules.tickets.panel.style_title', locale=self.locale)}**\n"
            f"-# {t('modules.tickets.panel.style_hint', locale=self.locale)}"))
        style_row = ui.ActionRow()
        style_row.add_item(TicketPanelSelect(
            'style', self.panel['id'],
            item=ui.Select(
                options=[
                    discord.SelectOption(
                        label=t('modules.tickets.panel.style_buttons', locale=self.locale),
                        value=STYLE_BUTTONS,
                        description=t('modules.tickets.panel.style_buttons_hint',
                                      locale=self.locale)[:100],
                        default=self.panel['style'] == STYLE_BUTTONS,
                    ),
                    discord.SelectOption(
                        label=t('modules.tickets.panel.style_select', locale=self.locale),
                        value=STYLE_SELECT,
                        description=t('modules.tickets.panel.style_select_hint',
                                      locale=self.locale)[:100],
                        default=self.panel['style'] == STYLE_SELECT,
                    ),
                ],
                min_values=1, max_values=1,
                custom_id=f"moddy:tickets:pnlsel:style:{self.panel['id']}",
            ),
        ))
        container.add_item(style_row)

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # 3. Categories.
        self._build_categories(container)

        self.add_item(container)
        self._build_buttons()

    def _build_categories(self, container: ui.Container):
        categories = self.panel.get('categories', [])
        cap = max_categories_for_style(self.panel['style'],
                                       self.limits.get('max_categories', 0))
        container.add_item(ui.TextDisplay(
            f"**{t('modules.tickets.panel.categories_title', locale=self.locale)}**{REQUIRED_FIELDS}\n"
            f"-# {t('modules.tickets.panel.categories_hint', locale=self.locale, count=len(categories), max=cap)}"))
        hint = premium_hint(self.locale, self.limits)
        if hint:
            container.add_item(ui.TextDisplay(hint))

        if not categories:
            container.add_item(ui.TextDisplay(
                f"{INFO} {t('modules.tickets.panel.no_category', locale=self.locale)}"))
            return

        for category in categories:
            container.add_item(ui.TextDisplay(self._render_category(category)))

        row = ui.ActionRow()
        row.add_item(TicketPanelSelect(
            'category', self.panel['id'],
            item=ui.Select(
                placeholder=t('modules.tickets.panel.category_placeholder',
                              locale=self.locale),
                options=[
                    discord.SelectOption(
                        label=category['name'][:100], value=category['id'],
                        description=(category.get('description') or None),
                    ) for category in categories[:25]
                ],
                min_values=1, max_values=1,
                custom_id=f"moddy:tickets:pnlsel:category:{self.panel['id']}",
            ),
        ))
        container.add_item(row)

    def _render_category(self, category: Dict[str, Any]) -> str:
        parent = self.bot.get_channel(category['discord_category_id']) \
            if self.bot and category.get('discord_category_id') else None
        target = f"`{parent.name}`" if parent else \
            f"{WARNING} {t('modules.tickets.panel.no_destination', locale=self.locale)}"

        prefix = category.get('emoji') or TICKET_CATEGORY
        line = f"{prefix} **{category['name']}** — {target}"
        if not category.get('enabled', True):
            line += f" · {t('modules.tickets.config.paused', locale=self.locale)}"

        roles = len(category.get('permissions', {}))
        line += (f"\n-# {t('modules.tickets.panel.category_summary', locale=self.locale, roles=roles)}")
        return line

    def _build_delete_confirmation(self, container: ui.Container):
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(
            f"{WARNING} **{t('modules.tickets.panel.delete_title', locale=self.locale)}**\n"
            f"{t('modules.tickets.panel.delete_description', locale=self.locale, categories=len(self.panel.get('categories', [])))}"))

        row = ui.ActionRow()
        row.add_item(TicketPanelButton(
            'back', self.panel['id'],
            label=t('modules.config.buttons.cancel', locale=self.locale),
            style=discord.ButtonStyle.secondary, emoji=BACK))
        row.add_item(TicketPanelButton(
            'delconfirm', self.panel['id'],
            label=t('modules.tickets.panel.delete_confirm', locale=self.locale),
            style=discord.ButtonStyle.danger, emoji=DELETE))
        container.add_item(row)

    def _build_buttons(self):
        panel_id = self.panel['id']
        enabled = self.panel.get('enabled', True)
        cap = max_categories_for_style(self.panel['style'],
                                       self.limits.get('max_categories', 0))

        row = ui.ActionRow()
        row.add_item(TicketPanelButton(
            'back', panel_id,
            label=t('modules.config.buttons.back', locale=self.locale),
            style=discord.ButtonStyle.secondary, emoji=BACK))
        row.add_item(TicketPanelButton(
            'edit', panel_id,
            label=t('modules.tickets.panel.edit', locale=self.locale),
            style=discord.ButtonStyle.primary, emoji=EDIT))
        row.add_item(TicketPanelButton(
            'addcat', panel_id,
            label=t('modules.tickets.panel.add_category', locale=self.locale),
            style=discord.ButtonStyle.success, emoji=ADD,
            disabled=len(self.panel.get('categories', [])) >= cap))
        self.add_item(row)

        row2 = ui.ActionRow()
        row2.add_item(TicketPanelButton(
            'repost', panel_id,
            label=t('modules.tickets.panel.repost', locale=self.locale),
            style=discord.ButtonStyle.secondary, emoji=SYNC,
            disabled=not (enabled and self.panel.get('channel_id'))))
        row2.add_item(TicketPanelButton(
            'toggle', panel_id,
            label=t('modules.tickets.panel.pause' if enabled
                    else 'modules.tickets.panel.resume', locale=self.locale),
            style=discord.ButtonStyle.secondary, emoji=PAUSE if enabled else PLAY))
        row2.add_item(TicketPanelButton(
            'delete', panel_id,
            label=t('modules.tickets.panel.delete', locale=self.locale),
            style=discord.ButtonStyle.danger, emoji=DELETE))
        self.add_item(row2)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_guild_perms(interaction)
