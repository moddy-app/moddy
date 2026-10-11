"""
``/config`` -> **Automod AI**: the home screen and the first-run wizard.

The panel is built around one rule: *the only thing a server must decide is
where Moddy posts its alerts*. Everything else has a sensible ready-made answer.

* **First run** (no alerts channel yet) — a three-step wizard: the alerts
  channel, a protection level (Light / Balanced / Strict, see
  ``automod/presets.py``), then "simulation or live".
* **Home** — one status line, a few sentences saying what the automod does,
  the on/off switch, the alerts channel and a menu to the detail screens
  (what is monitored, sanctions, AI guidance, exemptions, advanced).

Every change is saved the moment it is made (``automod_ai_common.save_cfg``);
there is no Save/Cancel. The other screens live in
``automod_ai_sanctions_config.py`` and ``automod_ai_advanced_config.py``.

Persistence
-----------
Every screen here has static ``moddy:automod:cfg:<screen>:<action>`` custom_ids
and is registered as a shell: ``interaction.guild_id`` plus a Manage Server check
(re-run on every click) is the whole auth context a guild panel needs. The
screens scoped to an entity (an infraction type, a page of precedents) use
``DynamicItem`` s, registered by :class:`AutomodConfigPersistence`.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import discord
from discord import ui

from automod.presets import PRESETS, preset_config
from cogs.error_handler import BaseView
from modules.configs import automod_ai_common as common
from modules.configs.automod_ai_common import back_row, button, cid, go, row, select, title, tp
from utils.emojis import (
    BOOK, GREEN_STATUS, GROUPS, IMAGE, LEGAL, MESSAGE, PLAY, RED_STATUS,
    REQUIRED_FIELDS, SEARCH, SETTINGS, SHIELD, TOGGLE_OFF, TOGGLE_ON, USER,
    WARNING, YELLOW_STATUS,
)
from utils.i18n import i18n

logger = logging.getLogger("moddy.modules.automod_ai_config")

#: Detail screens reachable from the home menu: (screen name, emoji).
NAV_ITEMS = (
    ("monitoring", SEARCH),
    ("sanctions", LEGAL),
    ("indications", BOOK),
    ("exemptions", GROUPS),
    ("advanced", SETTINGS),
)

_STATUS_DOT = {
    "active": GREEN_STATUS,
    "simulation": YELLOW_STATUS,
    "off": RED_STATUS,
    "setup": WARNING,
}


# =========================================================================== #
# Status / summary text
# =========================================================================== #
def status_text(cfg: Dict[str, Any], locale: str) -> str:
    """The one status line of the home screen."""
    status = common.status_of(cfg)
    if status == "setup":
        hint = tp("home.setup_channel" if not cfg.get("notify_channel_id")
                  else "home.setup_monitoring", locale)
        return f"{_STATUS_DOT[status]} **{tp('status.setup', locale)}** — {hint}"
    return (f"{_STATUS_DOT[status]} **{tp(f'status.{status}', locale)}** — "
            f"{tp(f'status.{status}_hint', locale)}")


def _custom_sanctions(cfg: Dict[str, Any], family: str) -> int:
    ids = common.type_ids(cfg)[family]
    return sum(1 for cat in ids if cat in cfg["sanctions"]
               and not common.entry_is_default(cat, cfg["sanctions"][cat]))


def summary_lines(cfg: Dict[str, Any], locale: str) -> str:
    """A few plain sentences about what the automod currently does."""
    feats = cfg["features"]
    lines = []

    if feats["content"]["enabled"]:
        n = _custom_sanctions(cfg, "message")
        sanctions = (tp("summary.sanctions_default", locale) if not n
                     else tp("summary.sanctions_custom", locale, n=n))
        lines.append(f"{MESSAGE} {tp('summary.messages_on', locale, sanctions=sanctions)}")
    else:
        lines.append(f"{MESSAGE} {tp('summary.messages_off', locale)}")

    images = [tp(f"summary.{key}", locale) for key, fid in (
        ("img_nsfw", "image_nsfw"), ("img_gore", "image_gore"), ("img_scam", "image_scam"))
        if feats[fid]["enabled"]]
    lines.append(f"{IMAGE} " + (tp("summary.images_on", locale, what=", ".join(images))
                                if images else tp("summary.images_off", locale)))

    parts = [tp(f"summary.prof_{p}", locale) for p in common.profile_parts(cfg)]
    lines.append(f"{USER} " + (tp("summary.profiles_on", locale, what=", ".join(parts))
                               if parts else tp("summary.profiles_off", locale)))

    custom = len(cfg["infractions_perso"])
    lines.append(f"{SETTINGS} " + (tp("summary.custom_n", locale, n=custom)
                                   if custom else tp("summary.custom_none", locale)))
    return "\n".join(lines)


# =========================================================================== #
# Rendering entry points
# =========================================================================== #
async def open_automod_panel(bot, guild_id: int, locale: str,
                             module_config: Optional[Dict[str, Any]]) -> ui.LayoutView:
    """First screen of ``/config`` -> Automod AI: the wizard until a channel is
    chosen, the home afterwards."""
    cfg = common.normalize_cfg(module_config)
    if not cfg.get("notify_channel_id"):
        return AutomodWizardChannelView(bot, guild_id, locale)
    return AutomodAIConfigView(bot, guild_id, locale, cfg)


async def render_home(interaction: discord.Interaction) -> None:
    await common.ack(interaction)
    cfg = await common.load_cfg(interaction.client, interaction.guild_id)
    await common.respond(interaction, AutomodAIConfigView(
        interaction.client, interaction.guild_id, i18n.get_user_locale(interaction), cfg))


async def render_wizard_channel(interaction: discord.Interaction) -> None:
    await common.ack(interaction)
    await common.respond(interaction, AutomodWizardChannelView(
        interaction.client, interaction.guild_id, i18n.get_user_locale(interaction)))


async def render_wizard_level(interaction: discord.Interaction) -> None:
    await common.ack(interaction)
    await common.respond(interaction, AutomodWizardLevelView(
        interaction.client, interaction.guild_id, i18n.get_user_locale(interaction)))


async def render_wizard_mode(interaction: discord.Interaction) -> None:
    await common.ack(interaction)
    await common.respond(interaction, AutomodWizardModeView(
        interaction.client, interaction.guild_id, i18n.get_user_locale(interaction)))


# =========================================================================== #
# Home
# =========================================================================== #
class AutomodAIConfigView(BaseView):
    """Home of the Automod AI panel.

    Persistent: yes. Auth: Manage Server, re-checked on every click (never a
    stored user id — a restarted shell cannot carry one).
    """

    __persistent__ = True

    def __init__(self, bot=None, guild_id: Optional[int] = None, locale: str = "en-US",
                 cfg: Optional[Dict[str, Any]] = None):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.locale = locale
        self.cfg = common.normalize_cfg(cfg)
        self._build_view()

    # -- build ----------------------------------------------------------- #
    def _build_view(self) -> None:
        self.clear_items()
        locale, cfg = self.locale, self.cfg
        # The registered shell renders every conditional control so that their
        # custom_ids are known to discord.py after a restart.
        is_shell = self.bot is None

        container = ui.Container()
        container.add_item(title(locale, "home.title"))
        container.add_item(ui.TextDisplay(status_text(cfg, locale)))
        container.add_item(ui.TextDisplay(f"-# {tp('home.intro', locale)}"))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(summary_lines(cfg, locale)))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Alerts channel: the select shows the chosen channel by itself.
        missing = "" if cfg.get("notify_channel_id") else f" {REQUIRED_FIELDS}"
        container.add_item(ui.TextDisplay(
            f"**{tp('home.channel_title', locale)}**{missing}\n-# {tp('home.channel_hint', locale)}"))
        channel_select = ui.ChannelSelect(
            placeholder=tp("home.channel_placeholder", locale),
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=1, max_values=1, custom_id=cid("home", "channel"))
        channel_select.callback = self.on_channel
        resolved = common.resolve_channels(self.bot, self.guild_id, [cfg.get("notify_channel_id")])
        if resolved:
            channel_select.default_values = resolved
        container.add_item(row(channel_select))

        # Main switch + the contextual shortcuts.
        on = bool(cfg["enabled"])
        buttons = [button(
            self.on_toggle, custom_id=cid("home", "toggle"),
            label=tp("home.turn_off" if on else "home.turn_on", locale),
            emoji=TOGGLE_ON if on else TOGGLE_OFF,
            style=discord.ButtonStyle.danger if on else discord.ButtonStyle.success)]
        if cfg["dry_run"] or is_shell:
            buttons.append(button(
                self.on_live, custom_id=cid("home", "live"), label=tp("home.go_live", locale),
                emoji=PLAY, style=discord.ButtonStyle.primary))
        if not common.any_feature_on(cfg) or is_shell:
            buttons.append(button(
                self.on_level, custom_id=cid("home", "level"),
                label=tp("home.choose_level", locale), emoji=SHIELD,
                style=discord.ButtonStyle.primary))
        container.add_item(row(*buttons))

        # The menu is a select, because each entry deserves a sentence.
        container.add_item(row(select(
            self.on_nav, custom_id=cid("home", "nav"),
            placeholder=tp("home.nav_placeholder", locale),
            options=[
                discord.SelectOption(
                    label=tp(f"nav.{key}", locale), value=key,
                    description=tp(f"nav.{key}_desc", locale)[:100],
                    emoji=common.emoji_of(emoji))
                for key, emoji in NAV_ITEMS
            ])))
        self.add_item(container)
        self.add_item(back_row(self.on_back, "home", locale))

    # -- callbacks (everything is re-derived from the interaction) -------- #
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await common.gate(interaction)

    async def on_channel(self, interaction: discord.Interaction):
        await common.ack(interaction)
        values = interaction.data.get("values") or []
        if not values:
            await common.ack(interaction)
            return
        cfg = await common.load_cfg(interaction.client, interaction.guild_id)
        cfg["notify_channel_id"] = int(values[0])
        await common.ack(interaction)
        await common.save_cfg(interaction, cfg)
        await render_home(interaction)

    async def on_toggle(self, interaction: discord.Interaction):
        await common.ack(interaction)
        cfg = await common.load_cfg(interaction.client, interaction.guild_id)
        turning_on = not cfg["enabled"]
        if turning_on and not cfg.get("notify_channel_id"):
            await go(interaction, "wizard_channel")
            return
        if turning_on and not common.any_feature_on(cfg):
            await go(interaction, "wizard_level")
            return
        cfg["enabled"] = turning_on
        await common.ack(interaction)
        await common.save_cfg(interaction, cfg)
        await render_home(interaction)

    async def on_live(self, interaction: discord.Interaction):
        await common.ack(interaction)
        cfg = await common.load_cfg(interaction.client, interaction.guild_id)
        cfg["dry_run"] = False
        await common.ack(interaction)
        await common.save_cfg(interaction, cfg)
        await render_home(interaction)

    async def on_level(self, interaction: discord.Interaction):
        await common.ack(interaction)
        await go(interaction, "wizard_level")

    async def on_nav(self, interaction: discord.Interaction):
        values = interaction.data.get("values") or []
        screen = values[0] if values else ""
        if screen not in {key for key, _ in NAV_ITEMS}:
            await common.ack(interaction)
            return
        await go(interaction, screen)

    async def on_back(self, interaction: discord.Interaction):
        await common.back_to_modules(interaction)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_view(cls())


# =========================================================================== #
# First-run wizard
# =========================================================================== #
class _WizardView(BaseView):
    """Common frame of the three wizard steps."""

    __persistent__ = True
    STEP = 0

    def __init__(self, bot=None, guild_id: Optional[int] = None, locale: str = "en-US"):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.locale = locale
        self._build_view()

    def _frame(self, question_key: str, hint_key: str) -> ui.Container:
        container = ui.Container()
        container.add_item(title(self.locale, "wizard.title"))
        container.add_item(ui.TextDisplay(
            f"-# {tp('wizard.step', self.locale, n=self.STEP, total=3)}\n"
            f"**{tp(question_key, self.locale)}**\n{tp(hint_key, self.locale)}"))
        return container

    def _build_view(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await common.gate(interaction)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_view(cls())


class AutomodWizardChannelView(_WizardView):
    """Step 1 — where Moddy posts its alerts (the one mandatory setting)."""

    STEP = 1

    def _build_view(self) -> None:
        self.clear_items()
        container = self._frame("wizard.channel_question", "wizard.channel_hint")
        channel_select = ui.ChannelSelect(
            placeholder=tp("wizard.channel_placeholder", self.locale),
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=1, max_values=1, custom_id=cid("wiz1", "channel"))
        channel_select.callback = self.on_channel
        container.add_item(row(channel_select))
        self.add_item(container)
        self.add_item(back_row(self.on_back, "wiz1", self.locale))

    async def on_channel(self, interaction: discord.Interaction):
        await common.ack(interaction)
        values = interaction.data.get("values") or []
        if not values:
            await common.ack(interaction)
            return
        cfg = await common.load_cfg(interaction.client, interaction.guild_id)
        cfg["notify_channel_id"] = int(values[0])
        await common.ack(interaction)
        if await common.save_cfg(interaction, cfg):
            await go(interaction, "wizard_level")

    async def on_back(self, interaction: discord.Interaction):
        await common.back_to_modules(interaction)


class AutomodWizardLevelView(_WizardView):
    """Step 2 — Light / Balanced / Strict."""

    STEP = 2

    def _build_view(self) -> None:
        self.clear_items()
        container = self._frame("wizard.level_question", "wizard.level_hint")
        for preset in PRESETS:
            recommended = preset == "balanced"
            section = ui.Section(
                ui.TextDisplay(f"**{tp(f'preset.{preset}', self.locale)}**"
                               f"{' — ' + tp('wizard.recommended', self.locale) if recommended else ''}\n"
                               f"{tp(f'preset.{preset}_desc', self.locale)}"),
                accessory=button(
                    self.on_level, custom_id=cid("wiz2", preset),
                    label=tp("wizard.choose", self.locale),
                    style=(discord.ButtonStyle.success if recommended
                           else discord.ButtonStyle.secondary)))
            container.add_item(section)
        self.add_item(container)
        self.add_item(back_row(self.on_back, "wiz2", self.locale))

    async def on_level(self, interaction: discord.Interaction):
        await common.ack(interaction)
        preset = (interaction.data.get("custom_id") or "").rsplit(":", 1)[-1]
        if preset not in PRESETS:
            await common.ack(interaction)
            return
        cfg = await common.load_cfg(interaction.client, interaction.guild_id)
        if not cfg.get("notify_channel_id"):
            await go(interaction, "wizard_channel")
            return
        # The level is applied switched off and in simulation: step 3 decides.
        new_cfg = preset_config(preset, cfg["notify_channel_id"], dry_run=True,
                                enabled=False, base=cfg)
        await common.ack(interaction)
        if await common.save_cfg(interaction, new_cfg):
            await go(interaction, "wizard_mode")

    async def on_back(self, interaction: discord.Interaction):
        await go(interaction, "home")


class AutomodWizardModeView(_WizardView):
    """Step 3 — start in simulation (recommended) or enable right away."""

    STEP = 3

    def _build_view(self) -> None:
        self.clear_items()
        container = self._frame("wizard.mode_question", "wizard.mode_hint")
        for action, dry_run in (("sim", True), ("live", False)):
            container.add_item(ui.Section(
                ui.TextDisplay(
                    f"**{tp(f'wizard.mode_{action}', self.locale)}**"
                    f"{' — ' + tp('wizard.recommended', self.locale) if dry_run else ''}\n"
                    f"{tp(f'wizard.mode_{action}_desc', self.locale)}"),
                accessory=button(
                    self.on_mode, custom_id=cid("wiz3", action),
                    label=tp(f"wizard.mode_{action}_button", self.locale),
                    style=(discord.ButtonStyle.success if dry_run
                           else discord.ButtonStyle.secondary))))
        self.add_item(container)
        self.add_item(back_row(self.on_back, "wiz3", self.locale))

    async def on_mode(self, interaction: discord.Interaction):
        await common.ack(interaction)
        action = (interaction.data.get("custom_id") or "").rsplit(":", 1)[-1]
        if action not in ("sim", "live"):
            await common.ack(interaction)
            return
        cfg = await common.load_cfg(interaction.client, interaction.guild_id)
        cfg["enabled"] = True
        cfg["dry_run"] = action == "sim"
        await common.ack(interaction)
        if await common.save_cfg(interaction, cfg):
            await render_home(interaction)

    async def on_back(self, interaction: discord.Interaction):
        await go(interaction, "wizard_level")


# =========================================================================== #
# Persistence marker
# =========================================================================== #
class AutomodConfigPersistence(BaseView):
    """Marker view: registers the dynamic items of the entity-scoped screens.

    The infraction detail screen and the precedents browser are built from
    ``DynamicItem`` s (an infraction id / a page number cannot ride in a static
    custom_id), so their wrappers are not registered — see
    docs/PERSISTENT_VIEWS.md "Deliberate exclusions".
    """

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        from modules.configs.automod_ai_precedents_view import (
            PrecedentsDeleteSelect, PrecedentsNavButton,
        )
        from modules.configs.automod_ai_sanctions_config import (
            TypeActionSelect, TypeButton,
        )
        bot.add_dynamic_items(TypeButton, TypeActionSelect,
                              PrecedentsNavButton, PrecedentsDeleteSelect)
