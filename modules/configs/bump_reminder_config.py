"""
Configuration UI for the Bump Reminder module (`/config`).

Two screens and one modal:

  Main panel ──► (pick a directory) ──► modal ──► created
            └──► Manage a reminder ──► modal / pause / delete

The modal is where a whole reminder is defined in one pass — directory, channel,
roles, how the last bumper gets mentioned, and the delay — because a bump
reminder has exactly five things to say and a Modal V2 holds exactly five
components. Nothing is a wizard that could be abandoned half-done.

**Why adding starts from a select rather than a button.** The delay field must
open pre-filled with the directory's own cooldown, and a Discord modal is static
— it cannot react to a choice made inside itself. Picking the directory on the
panel means the modal opens already knowing it, so every field including the
delay arrives filled in. Same number of clicks, nothing left for the reader to
look up.

Actions apply immediately (no Save/Cancel batching): each one writes the whole
list back through ``module_manager.save_module_config``, so the module is
revalidated and reloaded on every change.

Persistence (docs/PERSISTENT_VIEWS.md): stable namespaced custom_ids, no
timeouts, and every callback re-derives its context from ``interaction`` + the
database rather than from ``self``.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord import ui

from bumpreminder import BUMP_BOTS, bot_by_key, format_interval, parse_interval
from cogs.error_handler import BaseModal, BaseView
from modules.bump_reminder import (
    CHANNEL_TYPES,
    DEFAULT_PING_MODE,
    MAX_ROLE_MENTIONS,
    PING_MODES,
    count_by_bot,
    new_entry,
    normalize_config,
    reminders_per_bot,
)
from modules.configs._common import check_guild_perms
from utils.components_v2 import create_success_message
from utils.emojis import (
    ADD, BACK, DELETE, EDIT, INFO, PAUSE, PLAY, REQUIRED_FIELDS, ROCKET_LAUNCH, TIME,
)
from utils.i18n import i18n, t

logger = logging.getLogger('moddy.modules.bump_reminder_config')

_MODULE_ID = 'bump_reminder'

# --------------------------------------------------------------------------- #
# Namespaced custom_id constants (persistent dispatch).
# Format: moddy:bump:<view>:<action>. Guild context is re-derived from
# ``interaction.guild_id`` so the ids stay static (one shell, all guilds).
# --------------------------------------------------------------------------- #
_CID_MAIN_ADD = "moddy:bump:main:add"
_CID_MAIN_MANAGE = "moddy:bump:main:manage"
_CID_MAIN_BACK = "moddy:bump:main:back"

_CID_MANAGE_EDIT = "moddy:bump:manage:edit"
_CID_MANAGE_TOGGLE = "moddy:bump:manage:toggle"
_CID_MANAGE_REMOVE = "moddy:bump:manage:remove"
_CID_MANAGE_BACK = "moddy:bump:manage:back"

_CID_MODAL_BOT = "moddy:bump:modal:bot"
_CID_MODAL_CHANNEL = "moddy:bump:modal:channel"
_CID_MODAL_ROLES = "moddy:bump:modal:roles"
_CID_MODAL_PING = "moddy:bump:modal:ping"
_CID_MODAL_INTERVAL = "moddy:bump:modal:interval"


# --------------------------------------------------------------------------- #
# Storage helpers
# --------------------------------------------------------------------------- #
async def _load(bot, guild_id: int) -> List[Dict[str, Any]]:
    saved = await bot.module_manager.get_module_config(guild_id, _MODULE_ID)
    return normalize_config(saved)['reminders']


async def _save(bot, guild_id: int, reminders: List[Dict[str, Any]],
                actor_id: Optional[int] = None) -> Tuple[bool, Optional[str]]:
    """Persist the whole list (revalidates + reloads the module instance)."""
    return await bot.module_manager.save_module_config(
        guild_id, _MODULE_ID, normalize_config({'reminders': reminders}),
        actor_id=actor_id,
    )


async def _render_main(interaction: discord.Interaction) -> None:
    bot = interaction.client
    locale = i18n.get_user_locale(interaction)
    view = await BumpReminderConfigView.create(
        bot, interaction.guild_id, interaction.user.id, locale)
    if interaction.response.is_done():
        await interaction.edit_original_response(view=view)
    else:
        await interaction.response.edit_message(view=view)


def _ping_label(mode: str, locale: str) -> str:
    return t(f'modules.bump_reminder.ping.{mode}', locale=locale)


# =========================================================================== #
# Modal (V2) — one reminder, start to finish
# =========================================================================== #
class BumpReminderModal(BaseModal):
    """Creates or edits one reminder.

    Exactly five top-level components, which is the Modal V2 ceiling — the form
    is full, so anything else a reminder can do (pause, delete) lives on the
    manage screen rather than being squeezed in here.

    Every field opens on its current value: the directory pre-selected, the
    channel and roles pre-picked, the ping mode on its radio, and the delay
    written out as the caller stored it — or, for a brand-new reminder, as the
    directory itself advertises it.
    """

    def __init__(self, locale: str, *, bot_key: Optional[str] = None, callback_func=None,
                 channel_id: Optional[int] = None,
                 role_ids: Optional[List[int]] = None,
                 ping_mode: str = "button",
                 interval: Optional[int] = None,
                 available: Optional[List[str]] = None):
        super().__init__(
            title=t('modules.bump_reminder.modal.title', locale=locale)[:45],
            timeout=None,
        )
        self.locale = locale
        self.callback_func = callback_func
        self.original_bot = bot_key

        # 1 — the directory. Only the ones with room left are offered, plus
        #     whichever one is already selected (editing must never be blocked
        #     by the quota an entry is itself part of).
        offered = list(dict.fromkeys(([bot_key] if bot_key else []) + list(available or [])))
        self.bot_select = ui.Select(
            options=[
                discord.SelectOption(
                    label=other.name,
                    value=other.key,
                    description=t('modules.bump_reminder.modal.bot_option',
                                  locale=locale,
                                  interval=format_interval(other.default_interval),
                                  command=other.command_hint)[:100],
                    emoji=discord.PartialEmoji.from_str(other.emoji),
                    default=(other.key == bot_key),
                )
                for other in BUMP_BOTS if other.key in offered
            ],
            min_values=1, max_values=1, required=True, custom_id=_CID_MODAL_BOT,
        )
        self.add_item(ui.Label(
            text=f"{t('modules.bump_reminder.modal.bot_label', locale=locale)}",
            description=t('modules.bump_reminder.modal.bot_description', locale=locale)[:100],
            component=self.bot_select,
        ))

        # 2 — the channel. Also the channel Moddy watches for the bump: a
        #     reminder answers where the server bumps.
        self.channel_select = ui.ChannelSelect(
            channel_types=CHANNEL_TYPES,
            min_values=1, max_values=1, required=True,
            custom_id=_CID_MODAL_CHANNEL,
            default_values=([discord.Object(id=channel_id)] if channel_id else []),
        )
        self.add_item(ui.Label(
            text=t('modules.bump_reminder.modal.channel_label', locale=locale),
            description=t('modules.bump_reminder.modal.channel_description', locale=locale)[:100],
            component=self.channel_select,
        ))

        # 3 — roles to ping. Optional: plenty of servers just want the bumper.
        self.role_select = ui.RoleSelect(
            min_values=0, max_values=MAX_ROLE_MENTIONS, required=False,
            custom_id=_CID_MODAL_ROLES,
            default_values=[discord.Object(id=r) for r in (role_ids or [])],
        )
        self.add_item(ui.Label(
            text=t('modules.bump_reminder.modal.roles_label', locale=locale),
            description=t('modules.bump_reminder.modal.roles_description', locale=locale)[:100],
            component=self.role_select,
        ))

        # 4 — how the last bumper is mentioned.
        self.ping_group = ui.RadioGroup(
            options=[
                discord.RadioGroupOption(
                    label=_ping_label(mode, locale)[:100],
                    value=mode,
                    description=t(f'modules.bump_reminder.ping.{mode}_description',
                                  locale=locale)[:100],
                    default=(mode == ping_mode),
                )
                for mode in PING_MODES
            ],
            required=True, custom_id=_CID_MODAL_PING,
        )
        self.add_item(ui.Label(
            text=t('modules.bump_reminder.modal.ping_label', locale=locale),
            description=t('modules.bump_reminder.modal.ping_description', locale=locale)[:100],
            component=self.ping_group,
        ))

        # 5 — the delay. Pre-filled when editing, left blank when creating:
        #     a Discord modal is static, so it cannot fill this in from the
        #     directory picked in the select above it. Blank therefore means
        #     "whatever that directory enforces", which is both the right
        #     default and one less thing to look up.
        self.interval_input = ui.TextInput(
            style=discord.TextStyle.short,
            default=format_interval(interval) if interval is not None else None,
            placeholder=t('modules.bump_reminder.modal.interval_placeholder',
                          locale=locale)[:100],
            max_length=8, required=False, custom_id=_CID_MODAL_INTERVAL,
        )
        self.add_item(ui.Label(
            text=t('modules.bump_reminder.modal.interval_label', locale=locale),
            description=t('modules.bump_reminder.modal.interval_description', locale=locale)[:100],
            component=self.interval_input,
        ))

    async def on_submit(self, interaction: discord.Interaction):
        channels = self.channel_select.values
        await self.callback_func(
            interaction,
            bot_key=self.bot_select.values[0],
            channel_id=channels[0].id if channels else None,
            role_ids=[role.id for role in self.role_select.values],
            # RadioGroup is single-choice: `.value`, not the `.values` list
            # every select and CheckboxGroup exposes. See docs/MODALS_V2.md.
            ping_mode=self.ping_group.value or DEFAULT_PING_MODE,
            raw_interval=self.interval_input.value,
        )


# =========================================================================== #
# Main panel
# =========================================================================== #
class BumpReminderConfigView(BaseView):
    """Lists the guild's reminders and opens the add/manage flows.

    Persistent: yes. Auth: Manage Server, re-checked on every click via
    ``check_guild_perms`` — never a stored user_id, which a restarted shell
    cannot carry.
    """

    __persistent__ = True

    def __init__(self, bot=None, guild_id: Optional[int] = None,
                 user_id: Optional[int] = None, locale: str = "en-US",
                 reminders: Optional[List[Dict[str, Any]]] = None,
                 states: Optional[Dict[str, Dict[str, Any]]] = None,
                 cap: int = 1):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale
        self.reminders = reminders or []
        self.states = states or {}
        self.cap = cap

        self._build_view()

    @classmethod
    async def create(cls, bot, guild_id: int, user_id: int, locale: str):
        """Async factory: config, live state and quota in one go.

        The live state is what lets each row show its real countdown instead of
        a dead form — reading the panel answers "is this working?" without
        anyone having to wait for the next bump to find out.
        """
        reminders = await _load(bot, guild_id)
        states = await bot.db.get_guild_bump_states(guild_id)
        cap = await reminders_per_bot(bot, guild_id)
        return cls(bot, guild_id, user_id, locale, reminders, states, cap)

    # -- construction ----------------------------------------------------- #
    def _available_bots(self) -> List[str]:
        """Directories that still have room under this guild's quota."""
        counts = count_by_bot(self.reminders)
        return [spec.key for spec in BUMP_BOTS if counts.get(spec.key, 0) < self.cap]

    def _build_view(self):
        self.clear_items()
        container = ui.Container()

        container.add_item(ui.TextDisplay(
            f"### {ROCKET_LAUNCH} {t('modules.bump_reminder.config.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t('modules.bump_reminder.config.description', locale=self.locale)
        ))
        container.add_item(ui.TextDisplay(
            f"-# {t('modules.bump_reminder.config.limit', locale=self.locale, max=self.cap)}"
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        if self.reminders:
            container.add_item(ui.TextDisplay(
                f"**{t('modules.bump_reminder.config.list.title', locale=self.locale)}**\n"
                f"-# {t('modules.bump_reminder.config.list.count', locale=self.locale, count=len(self.reminders))}"
            ))
            for entry in self.reminders:
                container.add_item(ui.TextDisplay(self._render_entry(entry)))
        else:
            container.add_item(ui.TextDisplay(
                f"{INFO} {t('modules.bump_reminder.config.list.empty', locale=self.locale)}"
            ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        available = self._available_bots()
        if not available:
            container.add_item(ui.TextDisplay(
                f"-# {t('modules.bump_reminder.config.all_configured', locale=self.locale)}"
            ))

        # Manage: the dropdown edits an existing reminder, never creates one —
        # creating is the Add button below, as on every other /config panel.
        # Always registered so a restarted shell can still dispatch it.
        manage_row = ui.ActionRow()
        manage_select = ui.Select(
            placeholder=t('modules.bump_reminder.config.manage_placeholder', locale=self.locale),
            options=[
                discord.SelectOption(
                    label=self._entry_label(entry)[:100],
                    value=entry['id'],
                    description=self._entry_channel_name(entry)[:100] or None,
                    emoji=discord.PartialEmoji.from_str(bot_by_key(entry['bot']).emoji),
                )
                for entry in self.reminders[:25]
            ] or [discord.SelectOption(label="—", value="none")],
            min_values=1, max_values=1, custom_id=_CID_MAIN_MANAGE,
            disabled=not self.reminders,
        )
        manage_select.callback = self.on_manage_select
        manage_row.add_item(manage_select)
        container.add_item(manage_row)

        self.add_item(container)

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
            label=t('modules.bump_reminder.buttons.add', locale=self.locale),
            style=discord.ButtonStyle.success, custom_id=_CID_MAIN_ADD,
            disabled=not available,
        )
        add_btn.callback = self.on_add
        button_row.add_item(add_btn)
        self.add_item(button_row)

    def _entry_channel_name(self, entry: Dict[str, Any]) -> str:
        channel = (self.bot.get_channel(entry['channel_id'])
                   if self.bot and entry.get('channel_id') else None)
        return f"#{channel.name}" if channel else f"#{entry.get('channel_id', '')}"

    def _entry_label(self, entry: Dict[str, Any]) -> str:
        spec = bot_by_key(entry['bot'])
        return f"{spec.name if spec else entry['bot']} → {self._entry_channel_name(entry)}"

    def _render_entry(self, entry: Dict[str, Any]) -> str:
        """One reminder as it reads on the panel: what it is, then how it is doing."""
        spec = bot_by_key(entry['bot'])
        channel = (self.bot.get_channel(entry['channel_id'])
                   if self.bot and entry.get('channel_id') else None)
        channel_ref = channel.mention if channel else f"`{entry.get('channel_id', '')}`"

        line = f"{spec.emoji if spec else ''} **{spec.name if spec else entry['bot']}** → {channel_ref}"

        extras = [t('modules.bump_reminder.config.list.every', locale=self.locale,
                    interval=format_interval(entry['interval']))]
        if entry['role_ids']:
            extras.append(" ".join(f"<@&{r}>" for r in entry['role_ids']))
        extras.append(_ping_label(entry['ping_mode'], self.locale))
        if not entry.get('enabled', True):
            extras.append(t('modules.bump_reminder.config.list.paused', locale=self.locale))
        line += f"\n-# {' · '.join(extras)}"
        line += f"\n-# {self._render_state(entry)}"
        return line

    def _render_state(self, entry: Dict[str, Any]) -> str:
        """The live half: counting down, ready to bump, or never bumped yet."""
        state = self.states.get(entry['bot'])
        if not state:
            return t('modules.bump_reminder.config.state.waiting', locale=self.locale)
        if state.get('sent'):
            return t('modules.bump_reminder.config.state.ready', locale=self.locale)
        return t('modules.bump_reminder.config.state.scheduled', locale=self.locale,
                 timestamp=f"<t:{int(state['due_at'].timestamp())}:R>")

    # -- callbacks -------------------------------------------------------- #
    async def on_add(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)

        # Re-check against live config: the panel may have been open a while.
        reminders = await _load(bot, interaction.guild_id)
        cap = await reminders_per_bot(bot, interaction.guild_id)
        counts = count_by_bot(reminders)
        available = [s.key for s in BUMP_BOTS if counts.get(s.key, 0) < cap]
        if not available:
            await interaction.response.send_message(
                t('modules.bump_reminder.config.all_configured', locale=locale),
                ephemeral=True)
            return

        modal = BumpReminderModal(
            locale, callback_func=_create_reminder, available=available)
        modal.bot = bot
        await interaction.response.send_modal(modal)

    async def on_manage_select(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        entry_id = interaction.data['values'][0]
        reminders = await _load(bot, interaction.guild_id)
        entry = next((r for r in reminders if r['id'] == entry_id), None)
        if not entry:
            await _render_main(interaction)
            return
        states = await bot.db.get_guild_bump_states(interaction.guild_id)
        view = ManageBumpReminderView(bot, interaction.guild_id, locale, entry,
                                      states.get(entry['bot']))
        await interaction.response.edit_message(view=view)

    async def on_back(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        from cogs.config import ConfigMainView
        locale = i18n.get_user_locale(interaction)
        main_view = ConfigMainView(interaction.client, interaction.guild_id,
                                   interaction.user.id, locale)
        await interaction.response.edit_message(view=main_view)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_guild_perms(interaction)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_view(cls())


# =========================================================================== #
# Manage one reminder
# =========================================================================== #
class ManageBumpReminderView(BaseView):
    """One reminder: what it does, and the four things you can do to it."""

    __persistent__ = True

    def __init__(self, bot=None, guild_id: Optional[int] = None,
                 locale: str = "en-US", entry: Optional[Dict[str, Any]] = None,
                 state: Optional[Dict[str, Any]] = None):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.locale = locale
        self.entry = entry or {}
        self.state = state

        self._build_view()

    def _build_view(self):
        self.clear_items()
        container = ui.Container()
        spec = bot_by_key(self.entry.get('bot') or '')

        if spec is None:
            container.add_item(ui.TextDisplay(
                f"{INFO} {t('modules.bump_reminder.errors.unknown_bot', locale=self.locale)}"
            ))
        else:
            container.add_item(ui.TextDisplay(f"### {spec.emoji} {spec.name}"))
            container.add_item(ui.TextDisplay(
                f"-# {t('modules.bump_reminder.manage.subtitle', locale=self.locale, interval=format_interval(self.entry.get('interval') or spec.default_interval), command=spec.command)}"
            ))
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(ui.TextDisplay(self._render_summary()))

        self.add_item(container)

        button_row = ui.ActionRow()
        back_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(BACK),
            label=t('modules.config.buttons.back', locale=self.locale),
            style=discord.ButtonStyle.secondary, custom_id=_CID_MANAGE_BACK,
        )
        back_btn.callback = self.on_back
        button_row.add_item(back_btn)

        edit_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(EDIT),
            label=t('modules.bump_reminder.manage.edit', locale=self.locale),
            style=discord.ButtonStyle.primary, custom_id=_CID_MANAGE_EDIT,
        )
        edit_btn.callback = self.on_edit
        button_row.add_item(edit_btn)

        enabled = self.entry.get('enabled', True)
        toggle_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(PAUSE if enabled else PLAY),
            label=t('modules.bump_reminder.manage.pause' if enabled
                    else 'modules.bump_reminder.manage.resume', locale=self.locale),
            style=discord.ButtonStyle.secondary, custom_id=_CID_MANAGE_TOGGLE,
        )
        toggle_btn.callback = self.on_toggle
        button_row.add_item(toggle_btn)

        remove_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(DELETE),
            label=t('modules.bump_reminder.manage.remove', locale=self.locale),
            style=discord.ButtonStyle.danger, custom_id=_CID_MANAGE_REMOVE,
        )
        remove_btn.callback = self.on_remove
        button_row.add_item(remove_btn)
        self.add_item(button_row)

    def _render_summary(self) -> str:
        """Every stored value, because none of them is shown by a live control.

        The manage screen has no channel picker or role picker of its own — the
        modal owns all of that — so this text is the only place the current
        configuration can be read.
        """
        channel = (self.bot.get_channel(self.entry['channel_id'])
                   if self.bot and self.entry.get('channel_id') else None)
        channel_ref = channel.mention if channel else f"`{self.entry.get('channel_id', '')}`"

        roles = self.entry.get('role_ids') or []
        roles_ref = (" ".join(f"<@&{r}>" for r in roles) if roles
                     else t('modules.bump_reminder.manage.no_roles', locale=self.locale))

        lines = [
            t('modules.bump_reminder.manage.channel', locale=self.locale, channel=channel_ref),
            t('modules.bump_reminder.manage.roles', locale=self.locale, roles=roles_ref),
            t('modules.bump_reminder.manage.ping', locale=self.locale,
              mode=_ping_label(self.entry.get('ping_mode', 'button'), self.locale)),
        ]
        if not self.entry.get('enabled', True):
            lines.append(t('modules.bump_reminder.manage.paused_notice', locale=self.locale))

        state_line = self._state_line()
        if state_line:
            lines.append(f"-# {state_line}")
        return "\n".join(lines)

    def _state_line(self) -> str:
        if not self.state:
            return t('modules.bump_reminder.config.state.waiting', locale=self.locale)
        if self.state.get('sent'):
            return t('modules.bump_reminder.config.state.ready', locale=self.locale)
        return t('modules.bump_reminder.config.state.scheduled', locale=self.locale,
                 timestamp=f"<t:{int(self.state['due_at'].timestamp())}:R>")

    # -- write-back ------------------------------------------------------- #
    async def _apply(self, interaction: discord.Interaction,
                     **fields) -> Optional[Tuple[bool, Optional[str]]]:
        """Write ``fields`` onto this entry inside the guild's list.

        ``None`` means the entry is gone — deleted from another panel, or from
        the dashboard — and the caller falls back to the main screen.
        """
        bot = interaction.client
        reminders = await _load(bot, interaction.guild_id)
        target = next((r for r in reminders if r['id'] == self.entry.get('id')), None)
        if not target:
            return None
        target.update(fields)
        success, error = await _save(bot, interaction.guild_id, reminders, interaction.user.id)
        if success:
            self.entry = target
        return success, error

    async def _refresh(self, interaction: discord.Interaction,
                       result: Optional[Tuple[bool, Optional[str]]]) -> None:
        locale = i18n.get_user_locale(interaction)
        if result is None:
            await _render_main(interaction)
            return
        success, error = result
        if not success:
            await interaction.response.send_message(
                t('modules.config.save.error', locale=locale, error=error or ''),
                ephemeral=True)
            return
        bot = interaction.client
        states = await bot.db.get_guild_bump_states(interaction.guild_id)
        view = ManageBumpReminderView(bot, interaction.guild_id, locale, self.entry,
                                      states.get(self.entry['bot']))
        await interaction.response.edit_message(view=view)

    # -- callbacks -------------------------------------------------------- #
    async def on_edit(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)

        reminders = await _load(bot, interaction.guild_id)
        entry = next((r for r in reminders if r['id'] == self.entry.get('id')), None)
        if not entry:
            await _render_main(interaction)
            return

        cap = await reminders_per_bot(bot, interaction.guild_id)
        counts = count_by_bot(reminders)
        available = [s.key for s in BUMP_BOTS if counts.get(s.key, 0) < cap]

        modal = BumpReminderModal(
            locale, bot_key=entry['bot'],
            callback_func=_edit_reminder(entry['id']),
            channel_id=entry.get('channel_id'),
            role_ids=entry.get('role_ids'),
            ping_mode=entry.get('ping_mode', 'button'),
            interval=entry.get('interval'),
            available=available,
        )
        modal.bot = bot
        await interaction.response.send_modal(modal)

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
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        await interaction.response.defer()

        reminders = await _load(bot, interaction.guild_id)
        entry = next((r for r in reminders if r['id'] == self.entry.get('id')), None)
        remaining = [r for r in reminders if r['id'] != self.entry.get('id')]
        success, error = await _save(bot, interaction.guild_id, remaining, interaction.user.id)

        if success and entry:
            # Nothing left points at this directory: forget the pending row too,
            # or a reminder deleted at 3pm still fires at 4.
            if not any(r['bot'] == entry['bot'] for r in remaining):
                await bot.db.drop_bump_reminder(interaction.guild_id, entry['bot'])

        await _render_main(interaction)
        if success:
            await interaction.followup.send(
                t('modules.bump_reminder.manage.removed', locale=locale), ephemeral=True)
        else:
            await interaction.followup.send(
                t('modules.config.save.error', locale=locale, error=error or ''),
                ephemeral=True)

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
# Modal submit handlers
# =========================================================================== #
def _resolve_interval(raw: Optional[str], bot_key: str) -> Optional[int]:
    """Read the delay field, treating blank as "this directory's own cooldown".

    The field cannot be pre-filled when creating — a Discord modal is static and
    cannot react to the directory picked in its own select — so blank has to
    mean something useful rather than being rejected.
    """
    if raw is None or not raw.strip():
        spec = bot_by_key(bot_key)
        return spec.default_interval if spec else None
    return parse_interval(raw)


async def _write(interaction: discord.Interaction, reminders: List[Dict[str, Any]],
                 success_title_key: str, success_description_key: str) -> None:
    bot = interaction.client
    locale = i18n.get_user_locale(interaction)
    success, error = await _save(bot, interaction.guild_id, reminders, interaction.user.id)
    await _render_main(interaction)
    if success:
        await interaction.followup.send(
            view=create_success_message(
                t(success_title_key, locale=locale),
                t(success_description_key, locale=locale),
            ),
            ephemeral=True,
        )
    else:
        await interaction.followup.send(
            t('modules.config.save.error', locale=locale, error=error or ''),
            ephemeral=True,
        )


async def _create_reminder(interaction: discord.Interaction, *, bot_key: str,
                           channel_id: Optional[int], role_ids: List[int],
                           ping_mode: str, raw_interval: str) -> None:
    bot = interaction.client
    locale = i18n.get_user_locale(interaction)
    await interaction.response.defer()

    interval = _resolve_interval(raw_interval, bot_key)
    if interval is None:
        await interaction.followup.send(
            t('modules.bump_reminder.errors.invalid_interval', locale=locale), ephemeral=True)
        return

    reminders = await _load(bot, interaction.guild_id)
    reminders.append(new_entry(
        bot_key, channel_id=channel_id, role_ids=role_ids,
        ping_mode=ping_mode, interval=interval, created_by=interaction.user.id,
    ))
    await _write(interaction, reminders,
                'modules.bump_reminder.add.title', 'modules.bump_reminder.add.success')


def _edit_reminder(entry_id: str):
    """Bind the entry being edited to a submit handler.

    A closure rather than a bound method: the modal outlives the view that
    opened it, and the entry id is the only thing the write actually needs.
    """
    async def handler(interaction: discord.Interaction, *, bot_key: str,
                      channel_id: Optional[int], role_ids: List[int],
                      ping_mode: str, raw_interval: str) -> None:
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        await interaction.response.defer()

        interval = _resolve_interval(raw_interval, bot_key)
        if interval is None:
            await interaction.followup.send(
                t('modules.bump_reminder.errors.invalid_interval', locale=locale),
                ephemeral=True)
            return

        reminders = await _load(bot, interaction.guild_id)
        target = next((r for r in reminders if r['id'] == entry_id), None)
        if target is None:
            await _render_main(interaction)
            return

        previous_bot = target['bot']
        target.update({
            'bot': bot_key,
            'channel_id': channel_id,
            'role_ids': role_ids,
            'ping_mode': ping_mode,
            'interval': interval,
        })
        success, error = await _save(bot, interaction.guild_id, reminders,
                                     interaction.user.id)

        # Re-pointed at another directory: the old one's pending row is now
        # orphaned unless a sibling entry still claims it.
        if success and previous_bot != bot_key:
            if not any(r['bot'] == previous_bot for r in reminders):
                await bot.db.drop_bump_reminder(interaction.guild_id, previous_bot)

        await _render_main(interaction)
        await interaction.followup.send(
            t('modules.bump_reminder.manage.saved', locale=locale) if success
            else t('modules.config.save.error', locale=locale, error=error or ''),
            ephemeral=True,
        )

    return handler
