"""
``/config`` → Tickets: the panel list and the panel editor.

The category editor lives next door in ``tickets_category_config.py``; both
share the helpers at the top of this file.

Navigation
----------
::

    Panels  ──► Panel  ──► Category  ──► Permissions
      │           │           └──► Identity / Messages / Options (modals)
      │           └──► Appearance (modal)
      └──► Add a panel (modal)

Every change is applied **immediately** — there is no Save/Cancel batching.
That is not a shortcut: the screens below are built from
:class:`discord.ui.DynamicItem`\\ s that carry the panel (and category) id in
their ``custom_id`` and reconstruct themselves from scratch on every click, so
there is no ``self`` to stage edits on. Same reasoning, and the same trade, as
the Logs category screen — see docs/PERSISTENT_VIEWS.md.

Free servers get ``FREE_MAX_PANELS`` panels and ``FREE_MAX_CATEGORIES``
categories per panel; premium raises both. The limit is re-read from the
database at the moment of the action, never from the rendered panel (a screen
can sit open for hours — see docs/PREMIUM.md).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord import ui

from cogs.error_handler import BaseModal, BaseView
from modules.configs._common import check_guild_perms
from modules.tickets import (
    DEFAULT_ACCENT_COLOR,
    MAX_RETENTION_DAYS,
    SETTING_LOG_CHANNEL,
    SETTING_RETENTION,
    SETTING_SWITCHES,
    MAX_PANEL_DESCRIPTION,
    MAX_PANEL_NAME,
    MAX_PANEL_TITLE,
    MODULE_ID,
    default_panel_description,
    default_panel_title,
    find_panel,
    get_limits,
    new_panel_id,
    normalize_config,
)
from utils.components_v2 import create_error_message
from utils.emojis import (
    ADD, BACK, INFO, PREMIUM, SETTINGS, TICKET, TICKET_PANEL, WARNING,
)
from utils.i18n import i18n, t

logger = logging.getLogger('moddy.modules.tickets_config')

_ENTRY_ID = r"[a-z0-9_]+"

# Root screen: guild-scoped, so static custom_ids are enough.
_CID_ROOT_BACK = "moddy:tickets:cfg:back"
_CID_ROOT_ADD = "moddy:tickets:cfg:add"
_CID_ROOT_MANAGE = "moddy:tickets:cfg:manage"

# Module-wide settings screen — guild-scoped too, so static ids are enough.
_CID_ROOT_SETTINGS = "moddy:tickets:cfg:settings"
_CID_SET_BACK = "moddy:tickets:set:back"
_CID_SET_LOG = "moddy:tickets:set:log"
_CID_SET_EDIT = "moddy:tickets:set:edit"

PREMIUM_URL = "https://dashboard.moddy.app/select-premium-servers"


# =========================================================================== #
# Shared helpers
# =========================================================================== #
async def load_config(bot, guild_id: int) -> Dict[str, Any]:
    """The guild's ticket configuration, always in its normalised shape."""
    saved = await bot.module_manager.get_module_config(guild_id, MODULE_ID)
    return normalize_config(saved)


async def save_config(bot, guild_id: int, config: Dict[str, Any],
                      actor_id: Optional[int] = None,
                      *, repost_panel_id: Optional[str] = None,
                      repost_all: bool = False) -> Tuple[bool, Optional[str]]:
    """Persist the configuration and bring the panel messages back in line.

    ``repost_panel_id`` / ``repost_all`` are explicit because re-posting is a
    delete + send round-trip: it is done for the changes that are *visible on
    the panel message* (its wording, its colour, its buttons, its channel) and
    skipped for the ones that are not (permissions, ticket messages, who may
    open a category).
    """
    success, error = await bot.module_manager.save_module_config(
        guild_id, MODULE_ID, normalize_config(config), actor_id=actor_id)
    if not success:
        return False, error

    if not (repost_panel_id or repost_all):
        return True, None

    module = await bot.module_manager.get_module_instance(guild_id, MODULE_ID)
    if not module:
        return True, None
    try:
        if repost_all:
            await module.refresh_all_panels()
        else:
            panel = module.get_panel(repost_panel_id)
            if panel:
                await module.refresh_panel(panel)
    except Exception as e:  # a failed re-post must not undo a stored config
        logger.error(f"[Tickets] Could not refresh panel(s) for guild {guild_id}: {e}")
    return True, None


async def report_save_error(interaction: discord.Interaction, error: Optional[str]) -> None:
    locale = i18n.get_user_locale(interaction)
    view = create_error_message(
        t('modules.tickets.errors.title', locale=locale),
        t('modules.config.save.error', locale=locale, error=error or ''))
    if interaction.response.is_done():
        await interaction.followup.send(view=view, ephemeral=True)
    else:
        await interaction.response.send_message(view=view, ephemeral=True)


async def render_root(interaction: discord.Interaction) -> None:
    """(Re)build and show the panel list."""
    view = await TicketsConfigView.create(
        interaction.client, interaction.guild_id, interaction.user.id,
        i18n.get_user_locale(interaction))
    await _edit(interaction, view)


async def render_panel(interaction: discord.Interaction, panel_id: str) -> None:
    """(Re)build and show one panel's editor, or fall back to the list."""
    from modules.configs.tickets_panel_config import TicketPanelConfigView

    bot = interaction.client
    locale = i18n.get_user_locale(interaction)
    config = await load_config(bot, interaction.guild_id)
    panel = find_panel(config, panel_id)
    if not panel:
        await render_root(interaction)
        return
    limits = await get_limits(bot, interaction.guild_id)
    await _edit(interaction, TicketPanelConfigView(
        bot, interaction.guild_id, locale, panel, limits))


async def _edit(interaction: discord.Interaction, view: ui.LayoutView) -> None:
    """Replace the panel in place, whether or not the interaction was deferred."""
    if interaction.response.is_done():
        await interaction.edit_original_response(view=view)
    else:
        await interaction.response.edit_message(view=view)


def parse_hex_color(value: Optional[str]) -> Optional[int]:
    """``#RRGGBB`` / ``RRGGBB`` → int, ``None`` when empty or invalid."""
    if not value:
        return None
    s = value.strip().lstrip("#")
    if len(s) == 6 and all(c in "0123456789abcdefABCDEF" for c in s):
        return int(s, 16)
    return None


def color_to_hex(value: Optional[int]) -> str:
    return f"#{(value if value is not None else DEFAULT_ACCENT_COLOR):06X}"


def premium_hint(locale: str, limits: Dict[str, Any]) -> Optional[str]:
    """The one line that tells a free server what premium would give it."""
    if limits.get('premium'):
        return None
    return (f"-# {PREMIUM} "
            f"{t('modules.tickets.config.premium_hint', locale=locale)}")


# =========================================================================== #
# Add-a-panel modal
# =========================================================================== #
class PanelAppearanceModal(BaseModal):
    """Name, wording, colour and rendering of a panel — the whole message."""

    def __init__(self, locale: str, panel: Optional[Dict[str, Any]], callback_func):
        panel = panel or {}
        creating = not panel
        super().__init__(
            title=t('modules.tickets.panel.modal_title_new' if creating
                    else 'modules.tickets.panel.modal_title_edit', locale=locale)[:45],
            timeout=None,
        )
        self.locale = locale
        self.callback_func = callback_func

        self.name_input = ui.TextInput(
            style=discord.TextStyle.short, required=True,
            max_length=MAX_PANEL_NAME, default=panel.get('name'),
        )
        self.add_item(ui.Label(
            text=t('modules.tickets.panel.name_label', locale=locale)[:45],
            description=t('modules.tickets.panel.name_hint', locale=locale)[:100],
            component=self.name_input,
        ))

        # Pre-filled with what the panel would show anyway, so the admin edits
        # a real message instead of guessing in front of an empty box.
        self.title_input = ui.TextInput(
            style=discord.TextStyle.short, required=False,
            max_length=MAX_PANEL_TITLE,
            default=panel.get('title') or default_panel_title(locale),
        )
        self.add_item(ui.Label(
            text=t('modules.tickets.panel.title_label', locale=locale)[:45],
            description=t('modules.tickets.panel.title_hint', locale=locale)[:100],
            component=self.title_input,
        ))

        self.description_input = ui.TextInput(
            style=discord.TextStyle.paragraph, required=False,
            max_length=MAX_PANEL_DESCRIPTION,
            default=panel.get('description') or default_panel_description(locale),
        )
        self.add_item(ui.Label(
            text=t('modules.tickets.panel.description_label', locale=locale)[:45],
            description=t('modules.tickets.panel.description_hint', locale=locale)[:100],
            component=self.description_input,
        ))

        self.color_input = ui.TextInput(
            style=discord.TextStyle.short, required=False,
            min_length=0, max_length=7,
            default=color_to_hex(panel.get('accent_color')),
        )
        self.add_item(ui.Label(
            text=t('modules.tickets.panel.color_label', locale=locale)[:45],
            description=t('modules.tickets.panel.color_hint', locale=locale)[:100],
            component=self.color_input,
        ))

        # The dropdown placeholder lives here rather than on the panel screen:
        # it is wording, like everything else in this modal. It is simply
        # ignored while the panel renders as buttons.
        self.placeholder_input = ui.TextInput(
            style=discord.TextStyle.short, required=False,
            max_length=150, default=panel.get('placeholder'),
        )
        self.add_item(ui.Label(
            text=t('modules.tickets.panel.placeholder_label', locale=locale)[:45],
            description=t('modules.tickets.panel.placeholder_hint', locale=locale)[:100],
            component=self.placeholder_input,
        ))

    async def on_submit(self, interaction: discord.Interaction):
        await self.callback_func(interaction, {
            'name': (self.name_input.value or '').strip(),
            'title': (self.title_input.value or '').strip() or None,
            'description': (self.description_input.value or '').strip() or None,
            'accent_color': parse_hex_color(self.color_input.value),
            'placeholder': (self.placeholder_input.value or '').strip() or None,
        })


# =========================================================================== #
# Root screen — the panel list
# =========================================================================== #
class TicketsConfigView(BaseView):
    """The Tickets entry in ``/config``: every panel of the guild.

    Persistent: yes. Auth: Manage Server in the guild, re-checked on every
    click via ``check_guild_perms`` — never a stored ``user_id``, which cannot
    survive a restarted shell.
    """

    __persistent__ = True

    def __init__(self, bot=None, guild_id: Optional[int] = None,
                 user_id: Optional[int] = None, locale: str = "en-US",
                 panels: Optional[List[Dict[str, Any]]] = None,
                 limits: Optional[Dict[str, Any]] = None):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale
        self.panels = panels or []
        self.limits = limits or {'premium': False, 'max_panels': 0, 'max_categories': 0}

        self._build_view()

    @classmethod
    async def create(cls, bot, guild_id: int, user_id: int, locale: str):
        config = await load_config(bot, guild_id)
        limits = await get_limits(bot, guild_id)
        return cls(bot, guild_id, user_id, locale, config['panels'], limits)

    # -- construction ------------------------------------------------------ #
    def _build_view(self):
        self.clear_items()
        container = ui.Container()

        container.add_item(ui.TextDisplay(
            f"### {TICKET} {t('modules.tickets.config.title', locale=self.locale)}"))
        container.add_item(ui.TextDisplay(
            t('modules.tickets.config.description', locale=self.locale)))

        container.add_item(ui.TextDisplay(
            f"-# {t('modules.tickets.config.quota', locale=self.locale, count=len(self.panels), max=self.limits['max_panels'], categories=self.limits['max_categories'])}"))
        hint = premium_hint(self.locale, self.limits)
        if hint:
            container.add_item(ui.TextDisplay(hint))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        if self.panels:
            for panel in self.panels:
                container.add_item(ui.TextDisplay(self._render_entry(panel)))

            row = ui.ActionRow()
            select = ui.Select(
                placeholder=t('modules.tickets.config.manage_placeholder',
                              locale=self.locale),
                options=[
                    discord.SelectOption(
                        label=panel['name'][:100], value=panel['id'],
                        description=self._entry_summary(panel)[:100] or None,
                    ) for panel in self.panels[:25]
                ],
                min_values=1, max_values=1, custom_id=_CID_ROOT_MANAGE,
            )
            select.callback = self.on_manage
            row.add_item(select)
            container.add_item(row)
        else:
            container.add_item(ui.TextDisplay(
                f"{INFO} {t('modules.tickets.config.empty', locale=self.locale)}"))
            if self.bot is None:
                # Registration shell: render the select anyway so a real
                # message's dropdown still dispatches after a restart.
                row = ui.ActionRow()
                select = ui.Select(
                    placeholder=t('modules.tickets.config.manage_placeholder',
                                  locale=self.locale),
                    options=[discord.SelectOption(label="—", value="none")],
                    min_values=1, max_values=1, custom_id=_CID_ROOT_MANAGE,
                    disabled=True,
                )
                select.callback = self.on_manage
                row.add_item(select)
                container.add_item(row)

        self.add_item(container)

        button_row = ui.ActionRow()
        back = ui.Button(
            emoji=discord.PartialEmoji.from_str(BACK),
            label=t('modules.config.buttons.back', locale=self.locale),
            style=discord.ButtonStyle.secondary, custom_id=_CID_ROOT_BACK,
        )
        back.callback = self.on_back
        button_row.add_item(back)

        add = ui.Button(
            emoji=discord.PartialEmoji.from_str(ADD),
            label=t('modules.tickets.config.add_panel', locale=self.locale),
            style=discord.ButtonStyle.success, custom_id=_CID_ROOT_ADD,
            disabled=len(self.panels) >= self.limits['max_panels'],
        )
        add.callback = self.on_add
        button_row.add_item(add)

        settings = ui.Button(
            emoji=discord.PartialEmoji.from_str(SETTINGS),
            label=t('modules.tickets.settings.button', locale=self.locale),
            style=discord.ButtonStyle.secondary, custom_id=_CID_ROOT_SETTINGS,
        )
        settings.callback = self.on_settings
        button_row.add_item(settings)
        self.add_item(button_row)

    def _entry_summary(self, panel: Dict[str, Any]) -> str:
        categories = len(panel.get('categories', []))
        style = t(f"modules.tickets.panel.style_{panel['style']}", locale=self.locale)
        return t('modules.tickets.config.entry_summary', locale=self.locale,
                 categories=categories, style=style)

    def _render_entry(self, panel: Dict[str, Any]) -> str:
        channel = self.bot.get_channel(panel['channel_id']) \
            if self.bot and panel.get('channel_id') else None
        target = channel.mention if channel else \
            t('modules.tickets.config.no_channel', locale=self.locale)

        state = "" if panel.get('enabled') else \
            f" · {t('modules.tickets.config.paused', locale=self.locale)}"
        line = f"{TICKET_PANEL} **{panel['name']}** — {target}{state}"
        line += f"\n-# {self._entry_summary(panel)}"
        if panel.get('enabled') and panel.get('channel_id') and not panel.get('message_id'):
            line += (f"\n-# {WARNING} "
                     f"{t('modules.tickets.config.not_posted', locale=self.locale)}")
        return line

    # -- callbacks --------------------------------------------------------- #
    async def on_settings(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await render_settings(interaction)

    async def on_add(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)

        config = await load_config(bot, interaction.guild_id)
        limits = await get_limits(bot, interaction.guild_id)
        if len(config['panels']) >= limits['max_panels']:
            await self._limit_reached(interaction, locale, limits)
            return

        modal = PanelAppearanceModal(locale, None, self._create_panel)
        modal.bot = bot
        await interaction.response.send_modal(modal)

    async def _create_panel(self, interaction: discord.Interaction,
                            fields: Dict[str, Any]):
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        config = await load_config(bot, interaction.guild_id)
        limits = await get_limits(bot, interaction.guild_id)

        if len(config['panels']) >= limits['max_panels']:
            await self._limit_reached(interaction, locale, limits)
            return

        panel_id = new_panel_id()
        config['panels'].append({
            'id': panel_id, 'enabled': True, 'categories': [], **fields,
        })
        success, error = await save_config(bot, interaction.guild_id, config,
                                           interaction.user.id)
        if not success:
            await report_save_error(interaction, error)
            return
        await render_panel(interaction, panel_id)

    async def _limit_reached(self, interaction: discord.Interaction, locale: str,
                             limits: Dict[str, Any]):
        description = t('modules.tickets.errors.panel_limit', locale=locale,
                        max=limits['max_panels'])
        if not limits.get('premium'):
            description += ("\n\n" +
                            t('modules.tickets.errors.premium_upsell', locale=locale,
                              url=PREMIUM_URL))
        view = create_error_message(
            t('modules.tickets.errors.title', locale=locale), description)
        if interaction.response.is_done():
            await interaction.followup.send(view=view, ephemeral=True)
        else:
            await interaction.response.send_message(view=view, ephemeral=True)

    async def on_manage(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        values = interaction.data.get('values') or []
        if not values or values[0] == "none":
            await interaction.response.defer()
            return
        await render_panel(interaction, values[0])

    async def on_back(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        from cogs.config import ConfigMainView
        locale = i18n.get_user_locale(interaction)
        await interaction.response.edit_message(view=ConfigMainView(
            interaction.client, interaction.guild_id, interaction.user.id, locale))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_guild_perms(interaction)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_view(cls())


# =========================================================================== #
# Module-wide settings (transcripts, closure detection, ratings, log channel)
# =========================================================================== #
async def render_settings(interaction: discord.Interaction) -> None:
    """(Re)build and show the ticket settings screen."""
    view = await TicketsSettingsView.create(
        interaction.client, interaction.guild_id,
        i18n.get_user_locale(interaction))
    await _edit(interaction, view)


class TicketsSettingsModal(BaseModal):
    """The three switches and the retention window.

    Grouped into one modal rather than spread over the screen because a modal
    is the only place where showing the stored value as text is right: a
    checkbox and a text field carry their own state, a button opening a form
    carries none (CLAUDE.md rule 9).
    """

    def __init__(self, locale: str, settings: Dict[str, Any], callback_func):
        super().__init__(
            title=t('modules.tickets.settings.modal_title', locale=locale)[:45],
            timeout=None,
        )
        self.locale = locale
        self.callback_func = callback_func

        self.switches = ui.CheckboxGroup(
            options=[
                discord.CheckboxGroupOption(
                    label=t(f'modules.tickets.settings.{key}_label',
                            locale=locale)[:100],
                    value=key,
                    description=t(f'modules.tickets.settings.{key}_hint',
                                  locale=locale)[:100],
                    default=bool(settings.get(key)),
                ) for key in SETTING_SWITCHES
            ],
            min_values=0, max_values=len(SETTING_SWITCHES), required=False,
        )
        self.add_item(ui.Label(
            text=t('modules.tickets.settings.switches_label', locale=locale)[:45],
            description=t('modules.tickets.settings.switches_hint',
                          locale=locale)[:100],
            component=self.switches,
        ))

        self.retention_input = ui.TextInput(
            style=discord.TextStyle.short, required=False, max_length=4,
            default=str(settings.get(SETTING_RETENTION, 0) or 0),
        )
        self.add_item(ui.Label(
            text=t('modules.tickets.settings.retention_label', locale=locale)[:45],
            description=t('modules.tickets.settings.retention_hint',
                          locale=locale, max=MAX_RETENTION_DAYS)[:100],
            component=self.retention_input,
        ))

    async def on_submit(self, interaction: discord.Interaction):
        chosen = set(self.switches.values or [])
        raw = (self.retention_input.value or '').strip()
        try:
            retention = max(0, min(int(raw), MAX_RETENTION_DAYS)) if raw else 0
        except ValueError:
            retention = 0
        await self.callback_func(interaction, {
            **{key: key in chosen for key in SETTING_SWITCHES},
            SETTING_RETENTION: retention,
        })


class TicketsSettingsView(BaseView):
    """``/config`` → Tickets → Settings.

    Persistent: yes. Auth: Manage Server, re-checked on every click.
    """

    __persistent__ = True

    def __init__(self, bot=None, guild_id: Optional[int] = None,
                 locale: str = "en-US",
                 settings: Optional[Dict[str, Any]] = None):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.locale = locale
        self.settings = settings or {}
        self._build_view()

    @classmethod
    async def create(cls, bot, guild_id: int, locale: str):
        config = await load_config(bot, guild_id)
        return cls(bot, guild_id, locale, config['settings'])

    def _build_view(self):
        self.clear_items()
        container = ui.Container()
        container.add_item(ui.TextDisplay(
            f"### {SETTINGS} {t('modules.tickets.settings.title', locale=self.locale)}"))
        container.add_item(ui.TextDisplay(
            t('modules.tickets.settings.description', locale=self.locale)))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # The three switches and the retention window live behind a modal, so
        # nothing on screen shows their state — hence these lines. The channel
        # select below gets none, because it displays its own (rule 9).
        lines = []
        for key in SETTING_SWITCHES:
            state = t('modules.tickets.settings.on' if self.settings.get(key)
                      else 'modules.tickets.settings.off', locale=self.locale)
            lines.append(
                f"**{t(f'modules.tickets.settings.{key}_label', locale=self.locale)}** "
                f"`{state}`")
        retention = self.settings.get(SETTING_RETENTION, 0) or 0
        lines.append(
            f"**{t('modules.tickets.settings.retention_label', locale=self.locale)}** "
            f"`{retention}`" if retention else
            f"**{t('modules.tickets.settings.retention_label', locale=self.locale)}** "
            f"`{t('modules.tickets.settings.retention_forever', locale=self.locale)}`")
        container.add_item(ui.TextDisplay("\n".join(lines)))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(
            f"**{t('modules.tickets.settings.log_label', locale=self.locale)}**\n"
            f"-# {t('modules.tickets.settings.log_hint', locale=self.locale)}"))

        row = ui.ActionRow()
        log_select = ui.ChannelSelect(
            channel_types=[discord.ChannelType.text],
            placeholder=t('modules.tickets.settings.log_placeholder',
                          locale=self.locale),
            min_values=0, max_values=1, custom_id=_CID_SET_LOG,
        )
        current = self.settings.get(SETTING_LOG_CHANNEL)
        if current and self.bot is not None:
            channel = self.bot.get_channel(int(current))
            if channel is not None:
                log_select.default_values = [channel]
        log_select.callback = self.on_log_channel
        row.add_item(log_select)
        container.add_item(row)

        self.add_item(container)

        buttons = ui.ActionRow()
        back = ui.Button(
            emoji=discord.PartialEmoji.from_str(BACK),
            label=t('modules.config.buttons.back', locale=self.locale),
            style=discord.ButtonStyle.secondary, custom_id=_CID_SET_BACK,
        )
        back.callback = self.on_back
        buttons.add_item(back)

        edit = ui.Button(
            emoji=discord.PartialEmoji.from_str(SETTINGS),
            label=t('modules.tickets.settings.edit', locale=self.locale),
            style=discord.ButtonStyle.primary, custom_id=_CID_SET_EDIT,
        )
        edit.callback = self.on_edit
        buttons.add_item(edit)
        self.add_item(buttons)

    # -- callbacks --------------------------------------------------------- #
    async def _apply(self, interaction: discord.Interaction,
                     changes: Dict[str, Any]) -> None:
        """Merge changes into the stored settings. No panel re-post: none of
        these settings is visible on a panel message."""
        bot = interaction.client
        config = await load_config(bot, interaction.guild_id)
        config['settings'].update(changes)
        ok, error = await save_config(bot, interaction.guild_id, config,
                                      interaction.user.id)
        if not ok:
            await report_save_error(interaction, error)
            return
        await render_settings(interaction)

    async def on_log_channel(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        values: List[str] = interaction.data.get('values') or []
        await self._apply(interaction,
                          {SETTING_LOG_CHANNEL: int(values[0]) if values else None})

    async def on_edit(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        locale = i18n.get_user_locale(interaction)
        config = await load_config(interaction.client, interaction.guild_id)
        await interaction.response.send_modal(
            TicketsSettingsModal(locale, config['settings'], self._apply))

    async def on_back(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await render_root(interaction)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_guild_perms(interaction)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_view(cls())
