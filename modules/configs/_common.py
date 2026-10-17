"""
Shared helpers for module config panels (modules/configs/*.py).

Every guild config panel (AutoRoleConfigView, StarboardConfigView,
InterServerConfigView, ...) previously re-checked ownership by comparing
interaction.user.id against a self.user_id captured at command time. That
breaks on a restarted persistent-view shell, where self.user_id is None
(docs/guides/PERSISTENT_VIEWS.md Appendix B.5: "interaction.guild_id is already on
the interaction, a static custom_id + re-checked manage_guild is enough" —
no DynamicItem needed for this class of view). check_guild_perms() re-derives
authorization from the interaction alone, matching SocialNotificationsConfigView.

check_bot_perms() is the other half: Moddy never requires Administrator
(CLAUDE.md #12) — each module declares the exact bot permissions it needs in
``ModuleBase.REQUIRED_BOT_PERMISSIONS``, and this is checked right before a
module's config screen opens, instead of gating the whole /config command
behind Administrator.
"""

import asyncio
import logging
import re
from typing import Any, Awaitable, Dict, List, Mapping, Optional, Sequence, Tuple

import discord
from discord import ui

from config import COLORS
from utils.emojis import BACK, DELETE, EMOJIS, LABS, PAUSE, PLAY, SETTINGS, UNDONE, WARNING
from utils.i18n import i18n, t
from cogs.error_handler import BaseView
from modules.module_manager import (
    STATE_ACTIVE, STATE_PAUSED, STATE_SIMULATION, STATE_KEY, STATUS_NOT_CONFIGURED,
    normalize_state,
)
from utils.bot_permissions import (
    format_missing,
    merge_permissions,
    missing_channel_permissions,
    missing_guild_permissions,
)


async def check_guild_perms(interaction: discord.Interaction) -> bool:
    """Authorize a config interaction: requires Manage Server in this guild."""
    perms = getattr(interaction.user, "guild_permissions", None)
    if not interaction.guild_id or perms is None or not perms.manage_guild:
        locale = i18n.get_user_locale(interaction)
        message = t("modules.config.errors.no_user_perms", locale=locale)
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
        return False
    return True


def missing_bot_permissions(guild: discord.Guild, required: Sequence[str]) -> List[str]:
    """Permission keys from ``required`` that the bot does not hold in ``guild``.

    Never short-circuits: every missing permission is returned at once.
    """
    return missing_guild_permissions(guild, required)


async def check_bot_perms(
    interaction: discord.Interaction,
    required: Sequence[str],
    *,
    module_name: str = "",
    followup: bool = False,
    channel_requirements: Optional[Mapping[int, Sequence[str]]] = None,
) -> bool:
    """Authorize opening a module's config screen: the bot must hold every
    permission the module needs.

    ``required`` is the module's full guild-level list (use
    ``module_class.required_permissions()``: baseline + module specific).
    ``channel_requirements`` (``module_class.channel_requirements(config)``)
    adds the permissions needed in the channels the module is configured for,
    so channel overwrites that deny the bot show up too.

    ALL gaps are listed in one Components V2 message with a re-invite link
    scoped to every missing permission, then False is returned. Pass
    ``followup=True`` when the interaction's initial response was already used
    (e.g. deferred). Cache reads only: no network call.
    """
    guild = interaction.guild
    if guild is None or guild.me is None:
        return True

    missing = missing_guild_permissions(guild, required)
    channel_missing = missing_channel_permissions(
        guild, channel_requirements or {}, skip=missing)
    if not missing and not channel_missing:
        return True

    locale = i18n.get_user_locale(interaction)
    permissions_list = format_missing(missing, channel_missing, locale)

    error_view = ui.LayoutView(timeout=None)
    error_container = ui.Container(accent_colour=discord.Colour(COLORS["error"]))
    error_container.add_item(ui.TextDisplay(
        f"### {EMOJIS['error']} {t('modules.config.errors.missing_bot_perms.title', locale=locale)}"
    ))
    error_container.add_item(ui.TextDisplay(
        t('modules.config.errors.missing_bot_perms.description', locale=locale,
          module=module_name, permissions=permissions_list)
    ))
    error_view.add_item(error_container)

    invite_perms = discord.Permissions.none()
    for key in merge_permissions(missing, *channel_missing.values()):
        if hasattr(invite_perms, key):
            setattr(invite_perms, key, True)

    button_row = ui.ActionRow()
    button_row.add_item(ui.Button(
        label=t('modules.config.errors.missing_bot_perms.button', locale=locale),
        style=discord.ButtonStyle.link,
        url=(
            f"https://discord.com/oauth2/authorize?client_id={interaction.client.user.id}"
            f"&scope=bot&permissions={invite_perms.value}&guild_id={guild.id}"
        ),
    ))
    error_view.add_item(button_row)

    if followup or interaction.response.is_done():
        await interaction.followup.send(view=error_view, ephemeral=True)
    else:
        await interaction.response.send_message(view=error_view, ephemeral=True)
    return False


# ---------------------------------------------------------------------------
# Ack-first helpers
#
# Discord requires the initial response within 3 s. Config callbacks read and
# write the database, so they acknowledge the interaction FIRST (a deferred
# update for component interactions) and only then do the slow work. Never
# call ``interaction.response.edit_message`` after slow awaits; use ``show``.
# ---------------------------------------------------------------------------

async def ack(interaction: discord.Interaction) -> None:
    """Acknowledge ``interaction`` right away if nothing answered it yet.

    For a component / modal-submit interaction this is a deferred update (the
    original message is edited later with ``show``). Safe to call twice and
    safe after any other response (no-op).
    """
    if interaction.response.is_done():
        return
    try:
        await interaction.response.defer()
    except (discord.InteractionResponded, discord.NotFound):
        # Already answered (e.g. by the global guard) or expired: nothing to do.
        pass


async def show(interaction: discord.Interaction, view: discord.ui.LayoutView) -> None:
    """Replace the panel message with ``view``.

    Uses the fast ``edit_message`` path when the interaction is still
    unanswered, ``edit_original_response`` once it was acknowledged.
    """
    if interaction.response.is_done():
        await interaction.edit_original_response(view=view)
    else:
        await interaction.response.edit_message(view=view)


async def say(interaction: discord.Interaction, *args, **kwargs) -> None:
    """Send an ephemeral message whether or not the interaction was acked."""
    kwargs.setdefault("ephemeral", True)
    if interaction.response.is_done():
        await interaction.followup.send(*args, **kwargs)
    else:
        await interaction.response.send_message(*args, **kwargs)


# A modal must be the interaction's FIRST response (it cannot follow a defer),
# so a callback that opens a pre-filled modal cannot ack first. When the
# pre-fill needs the database, read it under a bound well below Discord's 3 s.
MODAL_LOAD_TIMEOUT = 1.5


async def load_before_modal(interaction: discord.Interaction,
                            coro: Awaitable[Any]) -> Tuple[bool, Any]:
    """Await ``coro`` (a DB read feeding a modal) with a hard time bound.

    Returns ``(True, value)``. If the read is too slow (or fails), tells the
    user to click again, as the first response, and returns ``(False, None)``;
    the caller must then return without opening the modal.
    """
    try:
        return True, await asyncio.wait_for(coro, timeout=MODAL_LOAD_TIMEOUT)
    except Exception as e:  # noqa: BLE001 - timeout or DB error: never let the click fail
        logging.getLogger("moddy").warning(
            "Modal pre-fill load failed (%s); asking the user to retry", type(e).__name__)
        locale = i18n.get_user_locale(interaction)
        await say(interaction, t("modules.config.errors.slow_retry", locale=locale))
        return False, None


async def open_main_config(interaction: discord.Interaction) -> None:
    """Back to the /config main screen (with module states and the limited banner)."""
    from cogs.config import ConfigMainView
    from utils import global_sanctions
    await ack(interaction)
    bot = interaction.client
    limited = await global_sanctions.is_limited(
        bot, user_id=interaction.user.id, guild_id=interaction.guild_id)
    view = await ConfigMainView.create(
        bot, interaction.guild_id, interaction.user.id,
        i18n.get_user_locale(interaction), limited=limited)
    await show(interaction, view)


# ---------------------------------------------------------------------------
# Module state (play / pause / simulation) -- see docs/guides/MODULE_SYSTEM.md
# ---------------------------------------------------------------------------

#: Container accent per state (docs/guides/DESIGN.md): active = primary, paused = neutral
#: gray, simulation = warning yellow. ``None`` / not configured = primary.
_STATE_ACCENTS = {
    STATE_ACTIVE: "primary",
    STATE_PAUSED: "neutral",
    STATE_SIMULATION: "warning",
}
_STATE_EMOJIS = {STATE_ACTIVE: PLAY, STATE_PAUSED: PAUSE, STATE_SIMULATION: LABS}


def state_accent(state: Optional[str]) -> discord.Colour:
    """Accent colour of a module screen for ``state`` (pass it to ``ui.Container``)."""
    return discord.Colour(COLORS[_STATE_ACCENTS.get(state or STATE_ACTIVE, "primary")])


def config_state(config: Optional[Mapping[str, Any]], *,
                 supports_simulation: bool = False) -> Optional[str]:
    """State stored in a module config dict, or None when there is no saved config."""
    if not config:
        return None
    return normalize_state(config.get(STATE_KEY), supports_simulation)


def state_label(status: Optional[str], locale: str) -> str:
    """Localised word for a state (or for ``STATUS_NOT_CONFIGURED`` / None)."""
    key = status if status in (STATE_ACTIVE, STATE_PAUSED, STATE_SIMULATION) else "not_configured"
    return t(f"modules.config.state.{key}", locale=locale)


def breadcrumb(locale: str, module_name: Optional[str] = None, *segments: str) -> ui.TextDisplay:
    """Discreet navigation trail for every /config sub-view (not the main screen).

    ``-# Config › Automod › Sanctions › Doxxing``. Put it as the FIRST item of
    the container, above the ``###`` title. ``module_name`` is the module's
    display name (``module_class.MODULE_NAME``); ``segments`` are already
    translated, innermost last. Dynamic names (an infraction, a panel) go in
    as they are -- no backticks, it is navigation, not data.
    """
    parts = [t("modules.config.breadcrumb.root", locale=locale)]
    if module_name:
        parts.append(module_name)
    parts.extend(s for s in segments if s)
    return ui.TextDisplay("-# " + " › ".join(parts))


_MODULE_NAME_FALLBACKS = {
    "logs": "Logs",
    "social_notifications": "Social Notifications",
    "tickets": "Tickets",
}


def module_display_name(locale: str, module_id: str) -> str:
    """Localised display name of a module (``modules.<id>.name``, with fallbacks)."""
    name = t(f"modules.{module_id}.name", locale=locale)
    if name.startswith("["):
        name = _MODULE_NAME_FALLBACKS.get(module_id, module_id)
    return name


def crumb(locale: str, module_id: str, *segments: str) -> ui.TextDisplay:
    """:func:`breadcrumb` for a module id: resolves the localised module name."""
    return breadcrumb(locale, module_display_name(locale, module_id), *segments)


def crumb_segment(locale: str, key: str) -> str:
    """Generic breadcrumb segment: add / manage / settings / permissions / options."""
    return t(f"modules.config.breadcrumb.{key}", locale=locale)


_CID_STATE_PREFIX = "moddy:config:state"


def _state_button(module_id: str, target: str, current: str, locale: str) -> "ModuleStateButton":
    return ModuleStateButton(module_id, target, current=current, locale=locale)


def module_state_row(module_id: str, state: Optional[str], locale: str, *,
                     supports_simulation: bool = False) -> List[Any]:
    """The state line + play/pause buttons, to add to a module's home container.

    ``state`` comes from :func:`config_state` (None = no saved config -> returns
    ``[]``: nothing to pause yet). Usage::

        for item in module_state_row("auto_role", state, locale):
            container.add_item(item)

    The text line is needed because a button does not show state. Buttons offer
    the OTHER states: Pause when active, Resume when paused; modules with
    ``SUPPORTS_SIMULATION`` also get Simulation / Activate. Clicks are handled
    by the persistent :class:`ModuleStateButton`, which rebuilds the module's
    home screen afterwards, so the view needs no callback of its own.
    """
    if state is None:
        return []
    line = (
        f"**{t('modules.config.state.label', locale=locale)}** "
        f"{_STATE_EMOJIS[state]} {state_label(state, locale)}\n"
        f"-# {t(f'modules.config.state.description.{state}', locale=locale)}"
    )
    targets = [s for s in (STATE_ACTIVE, STATE_SIMULATION, STATE_PAUSED)
               if s != state and (s != STATE_SIMULATION or supports_simulation)]
    row = ui.ActionRow()
    for target in targets:
        row.add_item(_state_button(module_id, target, state, locale))
    return [ui.TextDisplay(line), row]


class ModuleStateButton(
    ui.DynamicItem[ui.Button],
    template=rf"{_CID_STATE_PREFIX}:(?P<module>[a-z0-9_]{{1,40}}):(?P<target>active|paused|simulation)",
):
    """Switch a module to ``target`` state. Persistent for every module panel.

    Auth: Manage Server, re-checked on every click; the guild comes from the
    interaction, the module and target from the custom_id, so the button works
    on any view and survives restarts without per-view registration.
    """

    def __init__(self, module_id: str, target: str, *, current: str = STATE_ACTIVE,
                 locale: str = "en-US"):
        if target == STATE_PAUSED:
            key, emoji, style = "pause", PAUSE, discord.ButtonStyle.secondary
        elif target == STATE_SIMULATION:
            key, emoji, style = "simulation", LABS, discord.ButtonStyle.secondary
        else:
            key = "resume" if current == STATE_PAUSED else "activate"
            emoji, style = PLAY, discord.ButtonStyle.primary
        super().__init__(ui.Button(
            label=t(f"modules.config.state.buttons.{key}", locale=locale),
            emoji=discord.PartialEmoji.from_str(emoji),
            style=style,
            custom_id=f"{_CID_STATE_PREFIX}:{module_id}:{target}",
        ))
        self.module_id = module_id
        self.target = target

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button,
                             match: "re.Match"):
        return cls(match["module"], match["target"])

    async def callback(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await ack(interaction)

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        ok, error = await bot.module_manager.set_module_state(
            interaction.guild_id, self.module_id, self.target,
            actor_id=interaction.user.id,
        )
        if not ok:
            key = "not_configured" if error == "not_configured" else "failed"
            await say(interaction, t(f"modules.config.state.errors.{key}", locale=locale))
            return

        # Rebuild the module's home screen so the state line, accent and
        # buttons reflect the new state.
        from cogs.config import build_module_config_view
        config = await bot.module_manager.get_module_config(interaction.guild_id, self.module_id)
        view = await build_module_config_view(
            bot, interaction.guild_id, interaction.user.id, locale, self.module_id, config)
        if view is not None:
            await show(interaction, view)


class ModuleStatePersistence(BaseView):
    """Marker view: registers :class:`ModuleStateButton` at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(ModuleStateButton, ModuleManageButton, ModuleStateSelect,
                              ModuleManageAction)


# ---------------------------------------------------------------------------
# "Manage" panel: one small button outside every module home container opens
# it; it holds the module state (select) and the configuration deletion.
# ---------------------------------------------------------------------------

_CID_MANAGE_PREFIX = "moddy:config:manage"
_MODULE_RE = r"[a-z0-9_]{1,40}"


def _module_class(bot, module_id: str):
    manager = getattr(bot, "module_manager", None)
    return (getattr(manager, "registered_modules", None) or {}).get(module_id)


def module_manage_button(module_id: str, state: Optional[str],
                         locale: str) -> Optional["ModuleManageButton"]:
    """Small "Manage" button for a module home screen, or None when unconfigured.

    Put it in the screen's bottom action row (outside the container), next to
    Back. Usage::

        self._manage_button = module_manage_button("auto_role", state, locale)
        ...
        if self._manage_button is not None:
            button_row.add_item(self._manage_button)
    """
    if state is None:
        return None
    return ModuleManageButton(module_id, locale=locale)


def _state_option(state: str, current: str, locale: str) -> discord.SelectOption:
    return discord.SelectOption(
        label=t(f"modules.config.manage.options.{state}.label", locale=locale),
        description=t(f"modules.config.manage.options.{state}.description", locale=locale),
        value=state,
        emoji=discord.PartialEmoji.from_str(_STATE_EMOJIS[state]),
        default=state == current,
    )


def build_manage_view(module_id: str, state: str, locale: str, *,
                      module_name: str, supports_simulation: bool = False,
                      deletable: bool = True, confirm_delete: bool = False) -> BaseView:
    """The Manage panel (or its delete confirmation when ``confirm_delete``)."""
    view = BaseView(timeout=None)
    if confirm_delete:
        container = ui.Container(accent_colour=discord.Colour(COLORS["error"]))
        container.add_item(crumb(locale, module_id, t("modules.config.manage.button", locale=locale)))
        container.add_item(ui.TextDisplay(
            f"### {DELETE} {t('modules.config.manage.confirm_title', locale=locale)}\n"
            f"{t('modules.config.manage.confirm_text', locale=locale, module=module_name)}"))
        view.add_item(container)
        row = ui.ActionRow()
        row.add_item(ModuleManageAction(module_id, "open", locale=locale,
                                        label_key="cancel_button", emoji=UNDONE))
        row.add_item(ModuleManageAction(module_id, "delete_yes", locale=locale,
                                        label_key="confirm_button", emoji=DELETE,
                                        style=discord.ButtonStyle.danger))
        view.add_item(row)
        return view

    container = ui.Container(accent_colour=state_accent(state))
    container.add_item(crumb(locale, module_id, t("modules.config.manage.button", locale=locale)))
    container.add_item(ui.TextDisplay(f"### {SETTINGS} {t('modules.config.manage.title', locale=locale)}"))
    container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    container.add_item(ui.TextDisplay(
        f"**{t('modules.config.manage.state_title', locale=locale)}**\n"
        f"-# {t('modules.config.manage.state_hint', locale=locale)}"))
    # The select shows the current state itself (default option): no extra line.
    states = [STATE_ACTIVE] + ([STATE_SIMULATION] if supports_simulation else []) + [STATE_PAUSED]
    select_row = ui.ActionRow()
    select_row.add_item(ModuleStateSelect(module_id, [
        _state_option(s, state, locale) for s in states], locale=locale))
    container.add_item(select_row)
    if deletable:
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(
            f"**{t('modules.config.manage.danger_title', locale=locale)}**\n"
            f"-# {t('modules.config.manage.delete_hint', locale=locale)}"))
        danger_row = ui.ActionRow()
        danger_row.add_item(ModuleManageAction(module_id, "delete", locale=locale,
                                               label_key="delete_button", emoji=DELETE,
                                               style=discord.ButtonStyle.danger))
        container.add_item(danger_row)
    view.add_item(container)

    row = ui.ActionRow()
    row.add_item(ModuleManageAction(module_id, "home", locale=locale, emoji=BACK))
    view.add_item(row)
    return view


async def _open_manage(interaction: discord.Interaction, module_id: str, *,
                       confirm_delete: bool = False) -> None:
    bot = interaction.client
    locale = i18n.get_user_locale(interaction)
    module_cls = _module_class(bot, module_id)
    if module_cls is None:
        return
    config = await bot.module_manager.get_module_config(interaction.guild_id, module_id)
    if not config:
        # Nothing left to manage (deleted elsewhere): back to the module home.
        await _open_module_home(interaction, module_id)
        return
    view = build_manage_view(
        module_id, module_cls.stored_state(config), locale,
        module_name=module_display_name(locale, module_id),
        supports_simulation=bool(getattr(module_cls, "SUPPORTS_SIMULATION", False)),
        deletable=bool(getattr(module_cls, "CONFIG_DELETABLE", True)),
        confirm_delete=confirm_delete,
    )
    await show(interaction, view)


async def _open_module_home(interaction: discord.Interaction, module_id: str) -> None:
    from cogs.config import build_module_config_view
    bot = interaction.client
    locale = i18n.get_user_locale(interaction)
    config = await bot.module_manager.get_module_config(interaction.guild_id, module_id)
    view = await build_module_config_view(
        bot, interaction.guild_id, interaction.user.id, locale, module_id, config)
    if view is not None:
        await show(interaction, view)
    else:
        await open_main_config(interaction)


class ModuleManageButton(
    ui.DynamicItem[ui.Button],
    template=rf"{_CID_MANAGE_PREFIX}:(?P<module>{_MODULE_RE})",
):
    """Opens the Manage panel of a module. Auth: Manage Server, every click."""

    def __init__(self, module_id: str, *, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=t("modules.config.manage.button", locale=locale),
            emoji=discord.PartialEmoji.from_str(SETTINGS),
            style=discord.ButtonStyle.secondary,
            custom_id=f"{_CID_MANAGE_PREFIX}:{module_id}",
        ))
        self.module_id = module_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: "re.Match"):
        return cls(match["module"])

    async def callback(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await ack(interaction)
        await _open_manage(interaction, self.module_id)


class ModuleStateSelect(
    ui.DynamicItem[ui.Select],
    template=rf"{_CID_MANAGE_PREFIX}:(?P<module>{_MODULE_RE}):state",
):
    """State picker of the Manage panel; applies the chosen state at once."""

    def __init__(self, module_id: str, options: Optional[List[discord.SelectOption]] = None,
                 *, locale: str = "en-US"):
        super().__init__(ui.Select(
            placeholder=t("modules.config.manage.placeholder", locale=locale),
            options=options or [discord.SelectOption(label=STATE_ACTIVE, value=STATE_ACTIVE)],
            min_values=1, max_values=1,
            custom_id=f"{_CID_MANAGE_PREFIX}:{module_id}:state",
        ))
        self.module_id = module_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: "re.Match"):
        return cls(match["module"])

    async def callback(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await ack(interaction)
        values = (interaction.data or {}).get("values") or []
        target = values[0] if values else None
        if target not in (STATE_ACTIVE, STATE_PAUSED, STATE_SIMULATION):
            return
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        ok, error = await bot.module_manager.set_module_state(
            interaction.guild_id, self.module_id, target, actor_id=interaction.user.id)
        if not ok:
            key = "not_configured" if error == "not_configured" else "failed"
            await say(interaction, t(f"modules.config.state.errors.{key}", locale=locale))
            return
        await _open_manage(interaction, self.module_id)


class ModuleManageAction(
    ui.DynamicItem[ui.Button],
    template=rf"{_CID_MANAGE_PREFIX}:(?P<module>{_MODULE_RE}):(?P<action>home|open|delete|delete_yes)",
):
    """Back to the module home / delete flow buttons of the Manage panel."""

    def __init__(self, module_id: str, action: str, *, locale: str = "en-US",
                 label_key: Optional[str] = None, emoji: Optional[str] = None,
                 style: discord.ButtonStyle = discord.ButtonStyle.secondary):
        label = (t(f"modules.config.manage.{label_key}", locale=locale) if label_key
                 else t("modules.config.buttons.back", locale=locale))
        super().__init__(ui.Button(
            label=label,
            emoji=discord.PartialEmoji.from_str(emoji) if emoji else None,
            style=style,
            custom_id=f"{_CID_MANAGE_PREFIX}:{module_id}:{action}",
        ))
        self.module_id = module_id
        self.action = action

    @classmethod
    async def from_custom_id(cls, interaction, item, match: "re.Match"):
        return cls(match["module"], match["action"])

    async def callback(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await ack(interaction)
        if self.action == "home":
            await _open_module_home(interaction, self.module_id)
        elif self.action == "open":
            await _open_manage(interaction, self.module_id)
        elif self.action == "delete":
            await _open_manage(interaction, self.module_id, confirm_delete=True)
        elif self.action == "delete_yes":
            module_cls = _module_class(interaction.client, self.module_id)
            if module_cls is None or not getattr(module_cls, "CONFIG_DELETABLE", True):
                return
            ok = await interaction.client.module_manager.delete_module_config(
                interaction.guild_id, self.module_id)
            if not ok:
                locale = i18n.get_user_locale(interaction)
                await say(interaction, t("modules.config.delete.error", locale=locale))
                return
            # The module home renders its "not configured" screen (or wizard).
            await _open_module_home(interaction, self.module_id)
