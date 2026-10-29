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
    """Authorize a config interaction: requires Manage Server in this guild.

    The one other key is a Moddy staffer working in the private panel
    ``t.config`` handed them (``utils/staff_config_views.py``); a public panel
    never lets staff through.
    """
    perms = getattr(interaction.user, "guild_permissions", None)
    if interaction.guild_id and perms is not None and perms.manage_guild:
        return True
    from utils.staff_config_views import is_staff_config_session
    if not interaction.guild_id or not await is_staff_config_session(interaction):
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


def crumb(locale: str, module_id: str, *segments: str) -> ui.TextDisplay:
    """:func:`breadcrumb` for a module id: resolves the localised module name."""
    name = t(f"modules.{module_id}.name", locale=locale)
    return breadcrumb(locale, name, *segments)


def crumb_segment(locale: str, key: str) -> str:
    """Generic breadcrumb segment: add / manage / settings / permissions / options."""
    return t(f"modules.config.breadcrumb.{key}", locale=locale)


_CID_MANAGE_PREFIX = "moddy:config:manage"

#: Order of the states in the Manage panel's select.
_STATE_ORDER = (STATE_ACTIVE, STATE_SIMULATION, STATE_PAUSED)


def module_manage_button(module_id: str, locale: str) -> "ModuleManageButton":
    """The small "Manage" button a module home puts OUTSIDE its container.

    It opens the generic Manage panel (state, delete configuration). Show it
    only once a config is saved, and hide it while the home holds unsaved
    edits (leaving the screen would drop them)::

        if state is not None and not self.has_changes:
            button_row.add_item(module_manage_button("auto_role", self.locale))
    """
    return ModuleManageButton(module_id, "open", locale=locale)


def _module_class(bot, module_id: str):
    manager = getattr(bot, "module_manager", None)
    return getattr(manager, "registered_modules", {}).get(module_id)


async def build_manage_view(bot, guild_id: int, locale: str, module_id: str, *,
                            confirm_delete: bool = False) -> Optional[ui.LayoutView]:
    """The Manage panel of ``module_id``, or None when there is nothing to
    manage (unknown module or no saved config)."""
    module_class = _module_class(bot, module_id)
    if module_class is None:
        return None
    config = await bot.module_manager.get_module_config(guild_id, module_id)
    if not config and getattr(module_class, "DEFAULT_ENABLED", False):
        # Never saved, but running with its defaults: still manageable.
        config = module_class(bot, guild_id).get_default_config()
    if not config:
        return None
    return ModuleManageView(module_id, module_class.stored_state(config), locale,
                            supports_simulation=module_class.SUPPORTS_SIMULATION,
                            deletable=module_class.CONFIG_DELETABLE,
                            confirm_delete=confirm_delete)


async def _show_module_home(interaction: discord.Interaction, module_id: str) -> None:
    from cogs.config import build_module_config_view
    bot = interaction.client
    config = await bot.module_manager.get_module_config(interaction.guild_id, module_id)
    view = await build_module_config_view(
        bot, interaction.guild_id, interaction.user.id,
        i18n.get_user_locale(interaction), module_id, config)
    if view is not None:
        await show(interaction, view)


async def _show_manage(interaction: discord.Interaction, module_id: str, *,
                       confirm_delete: bool = False) -> None:
    view = await build_manage_view(interaction.client, interaction.guild_id,
                                   i18n.get_user_locale(interaction), module_id,
                                   confirm_delete=confirm_delete)
    if view is None:  # deleted meanwhile (dashboard, another panel)
        await _show_module_home(interaction, module_id)
    else:
        await show(interaction, view)


class ModuleManageView(BaseView):
    """``/config`` -> <module> -> Manage: the module state and the deletion.

    Not registered itself: every interactive child is a ``DynamicItem``
    (:class:`ModuleManageButton`, :class:`ModuleStateSelect`) carrying the
    module id, registered once by :class:`ModuleStatePersistence`.
    """

    def __init__(self, module_id: str, state: str, locale: str, *,
                 supports_simulation: bool = False, deletable: bool = False,
                 confirm_delete: bool = False):
        super().__init__()  # timeout=None
        tm = lambda key, **kw: t(f"modules.config.manage.{key}", locale=locale, **kw)  # noqa: E731

        container = ui.Container(accent_colour=state_accent(state))
        container.add_item(crumb(locale, module_id, crumb_segment(locale, "manage")))
        container.add_item(ui.TextDisplay(f"### {SETTINGS} {tm('title')}"))
        container.add_item(ui.TextDisplay(f"-# {tm('intro')}"))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # The state line stays: a select's placeholder is not a status.
        container.add_item(ui.TextDisplay(
            f"**{t('modules.config.state.label', locale=locale)}** "
            f"{_STATE_EMOJIS[state]} {state_label(state, locale)}\n"
            f"-# {t(f'modules.config.state.description.{state}', locale=locale)}"
        ))
        container.add_item(ui.ActionRow(ModuleStateSelect(
            module_id, current=state, locale=locale, supports_simulation=supports_simulation)))

        if deletable:
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            if confirm_delete:
                container.add_item(ui.TextDisplay(
                    f"{WARNING} **{tm('delete.confirm_title')}**\n{tm('delete.confirm_body')}"))
                container.add_item(ui.ActionRow(
                    ModuleManageButton(module_id, "cancel", locale=locale),
                    ModuleManageButton(module_id, "confirm", locale=locale)))
            else:
                container.add_item(ui.TextDisplay(
                    f"**{tm('delete.title')}**\n-# {tm('delete.hint')}"))
                container.add_item(ui.ActionRow(
                    ModuleManageButton(module_id, "delete", locale=locale)))
        self.add_item(container)
        self.add_item(ui.ActionRow(ModuleManageButton(module_id, "back", locale=locale)))


#: action -> (label key, emoji, style)
_MANAGE_BUTTONS = {
    "open": ("modules.config.manage.button", SETTINGS, discord.ButtonStyle.secondary),
    "back": ("modules.config.buttons.back", BACK, discord.ButtonStyle.secondary),
    "delete": ("modules.config.buttons.delete", DELETE, discord.ButtonStyle.danger),
    "cancel": ("modules.config.buttons.cancel", UNDONE, discord.ButtonStyle.secondary),
    "confirm": ("modules.config.manage.delete.confirm", DELETE, discord.ButtonStyle.danger),
}


class ModuleManageButton(
    ui.DynamicItem[ui.Button],
    template=rf"{_CID_MANAGE_PREFIX}:(?P<module>[a-z0-9_]{{1,40}}):(?P<action>open|back|delete|cancel|confirm)",
):
    """Every button of the Manage panel, plus the "Manage" entry on a module home.

    Auth: Manage Server, re-checked on every click; the guild comes from the
    interaction, the module and action from the custom_id.
    """

    def __init__(self, module_id: str, action: str, *, locale: str = "en-US"):
        key, emoji, style = _MANAGE_BUTTONS[action]
        super().__init__(ui.Button(
            label=t(key, locale=locale),
            emoji=discord.PartialEmoji.from_str(emoji),
            style=style,
            custom_id=f"{_CID_MANAGE_PREFIX}:{module_id}:{action}",
        ))
        self.module_id = module_id
        self.action = action

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button,
                             match: "re.Match"):
        return cls(match["module"], match["action"])

    async def callback(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await ack(interaction)
        if self.action == "back":
            await _show_module_home(interaction, self.module_id)
        elif self.action == "confirm":
            await self._delete(interaction)
        else:
            await _show_manage(interaction, self.module_id,
                               confirm_delete=self.action == "delete")

    async def _delete(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        module_class = _module_class(bot, self.module_id)
        if module_class is None or not module_class.CONFIG_DELETABLE:
            await _show_manage(interaction, self.module_id)
            return
        await module_class.before_config_delete(bot, interaction.guild_id)
        if not await bot.module_manager.delete_module_config(interaction.guild_id, self.module_id):
            await say(interaction, t("modules.config.delete.error", locale=locale))
            return
        await _show_module_home(interaction, self.module_id)
        await say(interaction, t("modules.config.delete.success", locale=locale))


class ModuleStateSelect(
    ui.DynamicItem[ui.Select],
    template=rf"{_CID_MANAGE_PREFIX}:(?P<module>[a-z0-9_]{{1,40}}):state",
):
    """Pick the module state (Active / Simulation / Paused), each option
    explained in one line. Persistent for every module."""

    def __init__(self, module_id: str, *, current: Optional[str] = None,
                 locale: str = "en-US", supports_simulation: bool = False):
        options = [
            discord.SelectOption(
                label=state_label(s, locale), value=s,
                description=t(f"modules.config.manage.options.{s}", locale=locale)[:100],
                emoji=discord.PartialEmoji.from_str(_STATE_EMOJIS[s]),
                default=s == current)
            for s in _STATE_ORDER
            if s != STATE_SIMULATION or supports_simulation or current == STATE_SIMULATION
        ]
        super().__init__(ui.Select(
            placeholder=t("modules.config.manage.state_placeholder", locale=locale),
            options=options, min_values=1, max_values=1,
            custom_id=f"{_CID_MANAGE_PREFIX}:{module_id}:state",
        ))
        self.module_id = module_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Select,
                             match: "re.Match"):
        return cls(match["module"])

    async def callback(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        await ack(interaction)
        values = interaction.data.get("values") or []
        target = values[0] if values else None
        if target in _STATE_ORDER:
            ok, error = await interaction.client.module_manager.set_module_state(
                interaction.guild_id, self.module_id, target, actor_id=interaction.user.id)
            if not ok:
                key = "not_configured" if error == "not_configured" else "failed"
                await say(interaction, t(f"modules.config.state.errors.{key}",
                                         locale=i18n.get_user_locale(interaction)))
        await _show_manage(interaction, self.module_id)


class ModuleStatePersistence(BaseView):
    """Marker view: registers the Manage panel's dynamic items at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(ModuleManageButton, ModuleStateSelect)
