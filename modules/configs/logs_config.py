"""Configuration UI for the advanced server logs.

Three screens, no save button:

* **root** — one line per configured category, a picker to open one, and a
  "send everything to one channel" shortcut for the common case;
* **category** — the channels this category posts to, and a paginated
  checklist of its events (everything on by default, tick off what you don't
  want);
* **options** — server-wide ignore lists, bots, transcripts and language.

Every change is applied immediately. That is deliberate: the category screen
is built from ``DynamicItem``s (they carry the category and the page in
their ``custom_id``), and a dynamic item is rebuilt from scratch on *every*
click — there is no ``self`` to stage pending edits on. Applying on the spot
is also what makes the panel simple: what you see is what is stored.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import discord
from discord import ui

from cogs.error_handler import BaseView
from modules.configs._common import check_guild_perms
from modules.logs import (
    MAX_CHANNELS_PER_CATEGORY, MAX_IGNORED_CHANNELS, MAX_IGNORED_ROLES,
    LogsModule, config_from_raw,
)
from serverlogs import registry
from utils.emojis import (
    BACK, DELETE, FILTER, NOTE, SETTINGS, TOGGLE_OFF, TOGGLE_ON, NEXT,
)
from utils.i18n import i18n, t

logger = logging.getLogger('moddy.modules.logs_config')

_MODULE_ID = "logs"

#: Events shown per page in the category checklist (Discord's select limit).
EVENTS_PER_PAGE = 25

#: Languages a server can pin its logs to ("auto" follows the server locale).
LOG_LOCALES = ("auto", "fr", "en-US", "es-ES", "pt-BR", "de")

# Root panel — static custom_ids, guild-scoped authorization.
_CID_CATEGORY = "moddy:logs:config:category"
_CID_MASS_CHANNEL = "moddy:logs:config:mass_channel"
_CID_OPTIONS = "moddy:logs:config:options"
_CID_CLEAR = "moddy:logs:config:clear"
_CID_BACK = "moddy:logs:config:back"

# Options panel.
_CID_OPT_CHANNELS = "moddy:logs:options:channels"
_CID_OPT_ROLES = "moddy:logs:options:roles"
_CID_OPT_BOTS = "moddy:logs:options:bots"
_CID_OPT_TRANSCRIPTS = "moddy:logs:options:transcripts"
_CID_OPT_MERGE = "moddy:logs:options:merge"
_CID_OPT_LOCALE = "moddy:logs:options:locale"
_CID_OPT_BACK = "moddy:logs:options:back"


# --------------------------------------------------------------------------- #
# Storage helpers — the panel edits a LogsModule, never a loose dict, so the
# routing and validation rules live in exactly one place.
# --------------------------------------------------------------------------- #

async def _load(bot, guild_id: int) -> LogsModule:
    raw = await bot.module_manager.get_module_config(guild_id, _MODULE_ID)
    return config_from_raw(bot, guild_id, raw)


async def _save(interaction: discord.Interaction, module: LogsModule) -> Optional[str]:
    """Persist the edited module. Returns an error message, or ``None``."""
    bot = interaction.client
    success, error = await bot.module_manager.save_module_config(
        interaction.guild_id, _MODULE_ID, module.to_config(),
        actor_id=interaction.user.id,
    )
    return None if success else error


async def _refuse(interaction: discord.Interaction, error: str) -> None:
    from utils.components_v2 import create_error_message
    locale = i18n.get_user_locale(interaction)
    view = create_error_message(
        t('modules.logs.config.errors.title', locale=locale), error)
    if interaction.response.is_done():
        await interaction.followup.send(view=view, ephemeral=True)
    else:
        await interaction.response.send_message(view=view, ephemeral=True)


# --------------------------------------------------------------------------- #
# Root panel
# --------------------------------------------------------------------------- #

class LogsConfigView(BaseView):
    """Server logs — main panel.

    Persistent: yes. Auth: Manage Server in the guild (re-checked on every
    click via check_guild_perms — never a stored user_id, which cannot
    survive a restarted shell).
    """

    __persistent__ = True

    def __init__(self, bot=None, guild_id: Optional[int] = None,
                 user_id: Optional[int] = None, locale: str = "en-US",
                 module: Optional[LogsModule] = None):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale
        self.module = module if module is not None else config_from_raw(bot, guild_id or 0, None)
        self._build_view()

    @classmethod
    async def create(cls, bot, guild_id: int, user_id: int, locale: str) -> "LogsConfigView":
        return cls(bot, guild_id, user_id, locale, await _load(bot, guild_id))

    # -- rendering -------------------------------------------------------- #

    def _build_view(self):
        self.clear_items()
        container = ui.Container()

        container.add_item(ui.TextDisplay(
            f"### {NOTE} {t('modules.logs.config.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t('modules.logs.config.description', locale=self.locale)
        ))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(self._summary()))

        # Category picker.
        picker_row = ui.ActionRow()
        picker = ui.Select(
            placeholder=t('modules.logs.config.categories.placeholder', locale=self.locale),
            options=self._category_options(),
            min_values=1, max_values=1,
            custom_id=_CID_CATEGORY,
        )
        picker.callback = self.on_category_select
        picker_row.add_item(picker)
        container.add_item(picker_row)

        # "Everything in one channel" — the setup most servers actually want.
        container.add_item(ui.TextDisplay(
            f"**{t('modules.logs.config.mass.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.logs.config.mass.section_description', locale=self.locale)}"
        ))
        mass_row = ui.ActionRow()
        mass_select = ui.ChannelSelect(
            placeholder=t('modules.logs.config.mass.placeholder', locale=self.locale),
            channel_types=[discord.ChannelType.text, discord.ChannelType.news,
                           discord.ChannelType.public_thread,
                           discord.ChannelType.private_thread],
            min_values=0, max_values=1,
            custom_id=_CID_MASS_CHANNEL,
        )
        mass_select.callback = self.on_mass_channel
        mass_row.add_item(mass_select)
        container.add_item(mass_row)

        self.add_item(container)
        self._add_action_buttons()

    def _summary(self) -> str:
        bound = [c for c in registry.ordered_categories()
                 if self.module.categories.get(c.id)
                 and self.module.categories[c.id].is_bound]
        if not bound:
            return f"-# {t('modules.logs.config.summary.empty', locale=self.locale)}"

        lines = []
        for category in bound:
            config = self.module.categories[category.id]
            channels = " ".join(f"<#{cid}>" for cid in config.channel_ids)
            total = len(category.events)
            lines.append(
                f"{category.emoji} **{t(category.name_key, locale=self.locale)}** → {channels}\n"
                f"-# {t('modules.logs.config.summary.events', locale=self.locale, enabled=config.enabled_count, total=total)}"
            )
        return "\n".join(lines)

    def _category_options(self) -> List[discord.SelectOption]:
        options = []
        for category in registry.ordered_categories():
            config = self.module.categories.get(category.id)
            bound = config.is_bound if config else False
            description = (
                t('modules.logs.config.categories.bound', locale=self.locale,
                  count=len(config.channel_ids), enabled=config.enabled_count,
                  total=len(category.events))
                if bound else
                t('modules.logs.config.categories.unbound', locale=self.locale,
                  total=len(category.events))
            )
            options.append(discord.SelectOption(
                label=t(category.name_key, locale=self.locale)[:100],
                value=category.id,
                description=description[:100],
                emoji=discord.PartialEmoji.from_str(category.emoji),
            ))
        return options

    def _add_action_buttons(self):
        row = ui.ActionRow()

        back = ui.Button(
            emoji=discord.PartialEmoji.from_str(BACK),
            label=t('modules.config.buttons.back', locale=self.locale),
            style=discord.ButtonStyle.secondary,
            custom_id=_CID_BACK,
        )
        back.callback = self.on_back
        row.add_item(back)

        options = ui.Button(
            emoji=discord.PartialEmoji.from_str(SETTINGS),
            label=t('modules.logs.config.options.button', locale=self.locale),
            style=discord.ButtonStyle.secondary,
            custom_id=_CID_OPTIONS,
        )
        options.callback = self.on_options
        row.add_item(options)

        # Rendered on the registration shell too, so its custom_id is known
        # after a restart even when no guild has a configuration yet
        # (docs/PERSISTENT_VIEWS.md, gotcha 2).
        if self.bot is None or self.module.bound_categories:
            clear = ui.Button(
                emoji=discord.PartialEmoji.from_str(DELETE),
                label=t('modules.logs.config.clear.button', locale=self.locale),
                style=discord.ButtonStyle.danger,
                custom_id=_CID_CLEAR,
            )
            clear.callback = self.on_clear
            row.add_item(clear)

        self.add_item(row)

    # -- callbacks -------------------------------------------------------- #

    async def on_category_select(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        values = interaction.data.get("values") or []
        if not values or values[0] not in registry.CATEGORIES:
            return
        view = await LogsCategoryView.create(interaction, values[0], page=0)
        await interaction.response.edit_message(view=view)

    async def on_mass_channel(self, interaction: discord.Interaction):
        """Bind every category to a single channel in one click."""
        if not await check_guild_perms(interaction):
            return
        values = interaction.data.get("values") or []
        if not values:
            return

        await interaction.response.defer()
        channel_id = int(values[0])
        module = await _load(interaction.client, interaction.guild_id)
        for category_id in registry.CATEGORIES:
            module.category(category_id).channel_ids = [channel_id]

        error = await _save(interaction, module)
        if error:
            await _refuse(interaction, error)
            return
        await _rerender_root(interaction)

    async def on_options(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        view = await LogsOptionsView.create(interaction)
        await interaction.response.edit_message(view=view)

    async def on_clear(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await interaction.response.defer()

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        success = await bot.module_manager.delete_module_config(
            interaction.guild_id, _MODULE_ID)
        if not success:
            await _refuse(interaction, t('modules.config.delete.error', locale=locale))
            return
        await _rerender_root(interaction)

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


async def _rerender_root(interaction: discord.Interaction) -> None:
    """Rebuild the root panel from the DB — never resend a shared shell."""
    bot = interaction.client
    locale = i18n.get_user_locale(interaction)
    view = await LogsConfigView.create(
        bot, interaction.guild_id, interaction.user.id, locale)
    await interaction.edit_original_response(view=view)


# --------------------------------------------------------------------------- #
# Category panel — built entirely from DynamicItems
# --------------------------------------------------------------------------- #

def _guarded(callback):
    """Funnel a dynamic-item callback error to the central handler (no live view)."""
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as e:  # noqa: BLE001
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, e, self.__class__.__name__)
    return wrapper


class LogsCategoryView(BaseView):
    """One category: its destinations and its event checklist.

    Not registered as a persistent view: every one of its children is a
    ``DynamicItem`` carrying the category (and page) in its ``custom_id`` and
    registered through :class:`LogsPersistence` — the same arrangement as
    ``utils/transcription_views.py::TranscribePromptView``. There is nothing
    left for a shell instance to register.
    """

    def __init__(self, bot, guild_id: int, category_id: str, page: int,
                 locale: str, module: LogsModule):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.category_id = category_id
        self.page = page
        self.locale = locale
        self.module = module
        self._build_view()

    @classmethod
    async def create(cls, interaction: discord.Interaction, category_id: str,
                     page: int) -> "LogsCategoryView":
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        module = await _load(bot, interaction.guild_id)
        return cls(bot, interaction.guild_id, category_id, page, locale, module)

    # -- rendering -------------------------------------------------------- #

    @property
    def spec(self) -> registry.LogCategorySpec:
        return registry.CATEGORIES[self.category_id]

    @property
    def page_count(self) -> int:
        return max(1, -(-len(self.spec.events) // EVENTS_PER_PAGE))

    def _build_view(self):
        self.clear_items()
        spec = self.spec
        config = self.module.category(self.category_id)
        container = ui.Container()

        container.add_item(ui.TextDisplay(
            f"### {spec.emoji} {t(spec.name_key, locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(t(spec.description_key, locale=self.locale)))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # --- destinations ------------------------------------------------ #
        container.add_item(ui.TextDisplay(
            f"**{t('modules.logs.config.channels.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.logs.config.channels.section_description', locale=self.locale, max=MAX_CHANNELS_PER_CATEGORY)}"
        ))
        channel_row = ui.ActionRow()
        channel_row.add_item(LogsCategoryChannels(
            self.category_id, self.locale, self._resolve_channels(config.channel_ids)))
        container.add_item(channel_row)

        # --- event checklist --------------------------------------------- #
        page_label = t('modules.logs.config.events.section_title', locale=self.locale)
        if self.page_count > 1:
            page_label += f" ({self.page + 1}/{self.page_count})"
        container.add_item(ui.TextDisplay(
            f"**{page_label}**\n"
            f"-# {t('modules.logs.config.events.section_description', locale=self.locale)}\n"
            f"-# {t('modules.logs.config.events.enabled_count', locale=self.locale, enabled=config.enabled_count, total=len(spec.events))}"
        ))
        events_row = ui.ActionRow()
        events_row.add_item(LogsCategoryEvents(
            self.category_id, self.page, self.locale,
            self._page_events(), config.disabled_events))
        container.add_item(events_row)

        self.add_item(container)

        # --- controls ---------------------------------------------------- #
        controls = ui.ActionRow()
        controls.add_item(LogsCategoryButton("back", self.category_id, self.page, self.locale))
        controls.add_item(LogsCategoryButton("all", self.category_id, self.page, self.locale))
        controls.add_item(LogsCategoryButton("none", self.category_id, self.page, self.locale))
        if self.page_count > 1:
            controls.add_item(LogsCategoryButton("prev", self.category_id, self.page, self.locale))
            controls.add_item(LogsCategoryButton("next", self.category_id, self.page, self.locale))
        self.add_item(controls)

    def _page_events(self) -> List[str]:
        start = self.page * EVENTS_PER_PAGE
        return list(self.spec.events[start:start + EVENTS_PER_PAGE])

    def _resolve_channels(self, channel_ids: List[int]) -> List[discord.abc.GuildChannel]:
        if self.bot is None:
            return []
        resolved = []
        for channel_id in channel_ids:
            channel = self.bot.get_channel(channel_id)
            if channel is not None:
                resolved.append(channel)
        return resolved


class LogsCategoryChannels(ui.DynamicItem[ui.ChannelSelect],
                           template=r"moddy:logs:catchan:(?P<cat>[a-z_]+)"):
    """Destination channels of one category."""

    def __init__(self, category_id: str = "server", locale: str = "en-US",
                 defaults: Optional[List[discord.abc.GuildChannel]] = None):
        select = ui.ChannelSelect(
            placeholder=t('modules.logs.config.channels.placeholder', locale=locale),
            channel_types=[discord.ChannelType.text, discord.ChannelType.news,
                           discord.ChannelType.public_thread,
                           discord.ChannelType.private_thread],
            min_values=0, max_values=MAX_CHANNELS_PER_CATEGORY,
            custom_id=f"moddy:logs:catchan:{category_id}",
        )
        if defaults:
            select.default_values = defaults
        super().__init__(select)
        self.category_id = category_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["cat"], i18n.get_user_locale(interaction))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await interaction.response.defer()

        values = interaction.data.get("values") or []
        module = await _load(interaction.client, interaction.guild_id)
        module.category(self.category_id).channel_ids = [int(v) for v in values]

        error = await _save(interaction, module)
        if error:
            await _refuse(interaction, error)
            return
        await _rerender_category(interaction, self.category_id, page=0)


class LogsCategoryEvents(ui.DynamicItem[ui.Select],
                         template=r"moddy:logs:catev:(?P<cat>[a-z_]+):(?P<page>\d+)"):
    """Checklist of the events of one category, 25 per page.

    Selected means "logged": the stored config only keeps the *excluded*
    events, so a category the registry grows later starts enabled — new
    events do not silently stay off.
    """

    def __init__(self, category_id: str = "server", page: int = 0,
                 locale: str = "en-US", events: Optional[List[str]] = None,
                 disabled: Optional[set] = None):
        events = events if events is not None else list(
            registry.CATEGORIES[category_id].events[:EVENTS_PER_PAGE])
        disabled = disabled or set()
        options = [
            discord.SelectOption(
                label=t(f"modules.logs.events.{category_id}.{event}", locale=locale)[:100],
                value=event,
                default=event not in disabled,
            )
            for event in events
        ]
        select = ui.Select(
            placeholder=t('modules.logs.config.events.placeholder', locale=locale),
            options=options or [discord.SelectOption(label="—", value="none")],
            min_values=0, max_values=len(options) or 1,
            custom_id=f"moddy:logs:catev:{category_id}:{page}",
        )
        super().__init__(select)
        self.category_id = category_id
        self.page = page

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["cat"], int(match["page"]), i18n.get_user_locale(interaction))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await interaction.response.defer()

        kept = set(interaction.data.get("values") or [])
        spec = registry.CATEGORIES[self.category_id]
        start = self.page * EVENTS_PER_PAGE
        page_events = set(spec.events[start:start + EVENTS_PER_PAGE])

        module = await _load(interaction.client, interaction.guild_id)
        config = module.category(self.category_id)
        # Only this page's events are affected — the others keep their state.
        config.disabled_events -= page_events
        config.disabled_events |= (page_events - kept)

        error = await _save(interaction, module)
        if error:
            await _refuse(interaction, error)
            return
        await _rerender_category(interaction, self.category_id, self.page)


class LogsCategoryButton(ui.DynamicItem[ui.Button],
                         template=r"moddy:logs:catbtn:(?P<action>back|all|none|prev|next):(?P<cat>[a-z_]+):(?P<page>\d+)"):
    """Back / enable all / disable all / previous page / next page."""

    _STYLES = {
        "back": (discord.ButtonStyle.secondary, BACK),
        "all": (discord.ButtonStyle.success, TOGGLE_ON),
        "none": (discord.ButtonStyle.secondary, TOGGLE_OFF),
        "prev": (discord.ButtonStyle.secondary, BACK),
        "next": (discord.ButtonStyle.secondary, NEXT),
    }

    def __init__(self, action: str = "back", category_id: str = "server",
                 page: int = 0, locale: str = "en-US"):
        style, emoji = self._STYLES[action]
        super().__init__(ui.Button(
            label=t(f'modules.logs.config.buttons.{action}', locale=locale),
            style=style,
            emoji=discord.PartialEmoji.from_str(emoji),
            custom_id=f"moddy:logs:catbtn:{action}:{category_id}:{page}",
        ))
        self.action = action
        self.category_id = category_id
        self.page = page

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["action"], match["cat"], int(match["page"]),
                   i18n.get_user_locale(interaction))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return

        if self.action == "back":
            view = await LogsConfigView.create(
                interaction.client, interaction.guild_id, interaction.user.id,
                i18n.get_user_locale(interaction))
            await interaction.response.edit_message(view=view)
            return

        spec = registry.CATEGORIES[self.category_id]
        page_count = max(1, -(-len(spec.events) // EVENTS_PER_PAGE))

        if self.action in ("prev", "next"):
            step = -1 if self.action == "prev" else 1
            page = (self.page + step) % page_count
            view = await LogsCategoryView.create(interaction, self.category_id, page)
            await interaction.response.edit_message(view=view)
            return

        await interaction.response.defer()
        module = await _load(interaction.client, interaction.guild_id)
        config = module.category(self.category_id)
        # "All"/"None" apply to the whole category, not just the visible page:
        # ticking 35 boxes page by page is exactly what these buttons exist for.
        config.disabled_events = set() if self.action == "all" else set(spec.events)

        error = await _save(interaction, module)
        if error:
            await _refuse(interaction, error)
            return
        await _rerender_category(interaction, self.category_id, self.page)


async def _rerender_category(interaction: discord.Interaction, category_id: str,
                             page: int) -> None:
    view = await LogsCategoryView.create(interaction, category_id, page)
    await interaction.edit_original_response(view=view)


# --------------------------------------------------------------------------- #
# Options panel
# --------------------------------------------------------------------------- #

class LogsOptionsView(BaseView):
    """Server-wide log options.

    Persistent: yes. Auth: Manage Server in the guild.
    """

    __persistent__ = True

    def __init__(self, bot=None, guild_id: Optional[int] = None,
                 locale: str = "en-US", module: Optional[LogsModule] = None):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.locale = locale
        self.module = module if module is not None else config_from_raw(bot, guild_id or 0, None)
        self._build_view()

    @classmethod
    async def create(cls, interaction: discord.Interaction) -> "LogsOptionsView":
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        return cls(bot, interaction.guild_id, locale,
                   await _load(bot, interaction.guild_id))

    def _build_view(self):
        self.clear_items()
        container = ui.Container()

        container.add_item(ui.TextDisplay(
            f"### {FILTER} {t('modules.logs.config.options.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t('modules.logs.config.options.description', locale=self.locale)
        ))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Ignored channels.
        container.add_item(ui.TextDisplay(
            f"**{t('modules.logs.config.options.channels.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.logs.config.options.channels.section_description', locale=self.locale)}"
        ))
        channel_row = ui.ActionRow()
        channel_select = ui.ChannelSelect(
            placeholder=t('modules.logs.config.options.channels.placeholder', locale=self.locale),
            min_values=0, max_values=MAX_IGNORED_CHANNELS,
            custom_id=_CID_OPT_CHANNELS,
        )
        channel_select.default_values = self._resolve(self.module.ignored_channel_ids,
                                                     kind="channel")
        channel_select.callback = self.on_channels
        channel_row.add_item(channel_select)
        container.add_item(channel_row)

        # Ignored roles.
        container.add_item(ui.TextDisplay(
            f"**{t('modules.logs.config.options.roles.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.logs.config.options.roles.section_description', locale=self.locale)}"
        ))
        role_row = ui.ActionRow()
        role_select = ui.RoleSelect(
            placeholder=t('modules.logs.config.options.roles.placeholder', locale=self.locale),
            min_values=0, max_values=MAX_IGNORED_ROLES,
            custom_id=_CID_OPT_ROLES,
        )
        role_select.default_values = self._resolve(self.module.ignored_role_ids, kind="role")
        role_select.callback = self.on_roles
        role_row.add_item(role_select)
        container.add_item(role_row)

        # Language.
        container.add_item(ui.TextDisplay(
            f"**{t('modules.logs.config.options.locale.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.logs.config.options.locale.section_description', locale=self.locale)}"
        ))
        locale_row = ui.ActionRow()
        locale_select = ui.Select(
            placeholder=t('modules.logs.config.options.locale.placeholder', locale=self.locale),
            options=[
                discord.SelectOption(
                    label=t(f'modules.logs.config.options.locale.values.{code}',
                            locale=self.locale),
                    value=code,
                    default=(code == (self.module.locale or "auto")),
                )
                for code in LOG_LOCALES
            ],
            min_values=1, max_values=1,
            custom_id=_CID_OPT_LOCALE,
        )
        locale_select.callback = self.on_locale
        locale_row.add_item(locale_select)
        container.add_item(locale_row)

        # Toggles.
        container.add_item(ui.TextDisplay(
            f"**{t('modules.logs.config.options.toggles.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.logs.config.options.toggles.bots', locale=self.locale)}: "
            f"**{self._state(self.module.ignore_bots)}**\n"
            f"-# {t('modules.logs.config.options.toggles.transcripts', locale=self.locale)}: "
            f"**{self._state(self.module.attach_transcripts)}**\n"
            f"-# {t('modules.logs.config.options.toggles.merge', locale=self.locale)}: "
            f"**{self._state(self.module.merge_duplicates)}**\n"
            f"-# {t('modules.logs.config.options.toggles.merge_description', locale=self.locale)}"
        ))

        self.add_item(container)

        row = ui.ActionRow()
        back = ui.Button(
            emoji=discord.PartialEmoji.from_str(BACK),
            label=t('modules.config.buttons.back', locale=self.locale),
            style=discord.ButtonStyle.secondary,
            custom_id=_CID_OPT_BACK,
        )
        back.callback = self.on_back
        row.add_item(back)

        bots = ui.Button(
            emoji=discord.PartialEmoji.from_str(
                TOGGLE_ON if self.module.ignore_bots else TOGGLE_OFF),
            label=t('modules.logs.config.options.toggles.bots', locale=self.locale),
            style=discord.ButtonStyle.secondary,
            custom_id=_CID_OPT_BOTS,
        )
        bots.callback = self.on_toggle_bots
        row.add_item(bots)

        transcripts = ui.Button(
            emoji=discord.PartialEmoji.from_str(
                TOGGLE_ON if self.module.attach_transcripts else TOGGLE_OFF),
            label=t('modules.logs.config.options.toggles.transcripts', locale=self.locale),
            style=discord.ButtonStyle.secondary,
            custom_id=_CID_OPT_TRANSCRIPTS,
        )
        transcripts.callback = self.on_toggle_transcripts
        row.add_item(transcripts)

        merge = ui.Button(
            emoji=discord.PartialEmoji.from_str(
                TOGGLE_ON if self.module.merge_duplicates else TOGGLE_OFF),
            label=t('modules.logs.config.options.toggles.merge', locale=self.locale),
            style=discord.ButtonStyle.secondary,
            custom_id=_CID_OPT_MERGE,
        )
        merge.callback = self.on_toggle_merge
        row.add_item(merge)

        self.add_item(row)

    def _state(self, value: bool) -> str:
        return t(f"modules.logs.values.{'on' if value else 'off'}", locale=self.locale)

    def _resolve(self, ids: List[int], *, kind: str) -> List[discord.Object]:
        if self.bot is None or not ids:
            return []
        object_type = discord.Role if kind == "role" else discord.abc.GuildChannel
        return [discord.Object(id=item_id, type=object_type) for item_id in ids]

    # -- callbacks -------------------------------------------------------- #

    async def _apply(self, interaction: discord.Interaction, mutate) -> None:
        await interaction.response.defer()
        module = await _load(interaction.client, interaction.guild_id)
        mutate(module)
        error = await _save(interaction, module)
        if error:
            await _refuse(interaction, error)
            return
        view = await LogsOptionsView.create(interaction)
        await interaction.edit_original_response(view=view)

    async def on_channels(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        values = [int(v) for v in (interaction.data.get("values") or [])]
        await self._apply(interaction,
                          lambda module: setattr(module, "ignored_channel_ids", values))

    async def on_roles(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        values = [int(v) for v in (interaction.data.get("values") or [])]
        await self._apply(interaction,
                          lambda module: setattr(module, "ignored_role_ids", values))

    async def on_locale(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        values = interaction.data.get("values") or ["auto"]
        code = values[0] if values[0] in LOG_LOCALES else "auto"
        await self._apply(interaction, lambda module: setattr(module, "locale", code))

    async def on_toggle_bots(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await self._apply(interaction, lambda module: setattr(
            module, "ignore_bots", not module.ignore_bots))

    async def on_toggle_transcripts(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await self._apply(interaction, lambda module: setattr(
            module, "attach_transcripts", not module.attach_transcripts))

    async def on_toggle_merge(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await self._apply(interaction, lambda module: setattr(
            module, "merge_duplicates", not module.merge_duplicates))

    async def on_back(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        view = await LogsConfigView.create(
            interaction.client, interaction.guild_id, interaction.user.id,
            i18n.get_user_locale(interaction))
        await interaction.response.edit_message(view=view)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_guild_perms(interaction)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_view(cls())


class LogsPersistence(BaseView):
    """Marker registering the category panel's dynamic items.

    The panel itself has no shell worth registering (see
    :class:`LogsCategoryView`); its three item classes carry all the state.
    """

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_dynamic_items(LogsCategoryChannels, LogsCategoryEvents,
                              LogsCategoryButton)
