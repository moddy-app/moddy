"""
``/config`` -> **Automod**: the home screen, the first-run wizard and the delete
confirmation.

The panel is built around one rule: *the only thing a server must decide is
where Moddy posts its alerts*. Everything else has a sensible ready-made answer.

* **First run** (nothing saved yet) -- a three-step wizard: the alerts channel,
  a protection level (Light / Balanced / Strict, see ``automod/presets.py``),
  then "simulation or live". Every step has a **Skip** button: it applies the
  *Balanced* level in *Simulation* (the recommended level, and the state in
  which nobody can be sanctioned by mistake) and opens the home.
* **Home** -- the generic module state (Active / Simulation / Paused, with its
  buttons), a few sentences saying what the automod does, the alerts channel and
  a menu (outside the container) to the detail screens. The home saves instantly;
  the detail screens use Save / Cancel (see ``automod_ai_common``). The
  "Delete configuration" button sits outside the container too.

The state is the generic one (``guilds.data.modules.automod_ai.state``, see
docs/guides/MODULE_SYSTEM.md): ``simulation`` is the former ``dry_run``.

Persistence
-----------
Every screen here has static ``moddy:automod:cfg:<screen>:<action>`` custom_ids
and is registered as a shell: ``interaction.guild_id`` plus a Manage Server check
(re-run on every click) is the whole auth context a guild panel needs. The state
buttons are the shared :class:`modules.configs._common.ModuleStateButton`. The
screens scoped to an entity (an infraction type, a page of precedents) use
``DynamicItem`` s, registered by :class:`AutomodConfigPersistence`.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import discord
from discord import ui

from automod.presets import DEFAULT_PRESET, PRESETS, preset_config
from cogs.error_handler import BaseView
from config import COLORS
from modules.automod_ai import AutomodModule
from modules.configs import automod_ai_common as common
from modules.configs._common import module_manage_button, state_accent
from modules.configs.automod_ai_common import (
    back_row, button, cid, go, row, select, title, tp, trail,
)
from utils.emojis import (
    BACK, DELETE, GROUPS, IMAGE, LEGAL, MESSAGE, REQUIRED_FIELDS, SEARCH, SETTINGS,
    SHIELD, USER, WARNING,
)
from utils.i18n import i18n, t

logger = logging.getLogger("moddy.modules.automod_ai_config")

#: Detail screens reachable from the home menu: (screen name, emoji).
NAV_ITEMS = (
    ("monitoring", SEARCH),
    ("sanctions", LEGAL),
    ("exemptions", GROUPS),
    ("advanced", SETTINGS),
)

#: What "Skip" and a fresh start apply: the recommended level, in simulation.
SKIP_PRESET = DEFAULT_PRESET
SKIP_STATE = "simulation"


# =========================================================================== #
# Summary text
# =========================================================================== #
def _custom_sanctions(cfg: Dict[str, Any], family: str) -> int:
    return sum(1 for cat in common.type_ids(cfg)[family] if cat in cfg["sanctions"])


def summary_lines(cfg: Dict[str, Any], locale: str) -> str:
    """A few plain sentences about what the automod currently does."""
    feats = cfg["features"]
    lines = []

    if feats["content"]["enabled"]:
        line = tp("summary.messages_on", locale)
        n = _custom_sanctions(cfg, "message")
        if n:
            line += " · " + tp("summary.sanctions_custom", locale, n=n)
        lines.append(f"{MESSAGE} {line}")
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


def setup_text(cfg: Dict[str, Any], locale: str) -> str:
    """The "needs setup" line (empty when Moddy can run)."""
    if not common.needs_setup(cfg):
        return ""
    hint = tp("home.setup_channel" if not cfg.get("notify_channel_id")
              else "home.setup_monitoring", locale)
    return f"{WARNING} **{tp('home.setup_title', locale)}** — {hint}"


# =========================================================================== #
# Rendering entry points
# =========================================================================== #
async def open_automod_panel(bot, guild_id: int, locale: str,
                             module_config: Optional[Dict[str, Any]]) -> ui.LayoutView:
    """First screen of ``/config`` -> Automod: the wizard until something is
    saved, the home afterwards."""
    if not module_config:
        return AutomodWizardChannelView(bot, guild_id, locale)
    return AutomodAIConfigView(bot, guild_id, locale, common.normalize_cfg(module_config))


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


async def render_delete(interaction: discord.Interaction) -> None:
    await common.ack(interaction)
    await common.respond(interaction, AutomodDeleteView(
        interaction.client, interaction.guild_id, i18n.get_user_locale(interaction)))


# =========================================================================== #
# Home
# =========================================================================== #
class AutomodAIConfigView(BaseView):
    """Home of the Automod panel. Saves instantly (no Save / Cancel).

    Persistent: yes. Auth: Manage Server, re-checked on every click (never a
    stored user id -- a restarted shell cannot carry one).
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
        state = cfg["state"]

        container = ui.Container(accent_colour=state_accent(state))
        container.add_item(trail(locale))
        container.add_item(title(locale, "home.title"))
        container.add_item(ui.TextDisplay(f"-# {tp('home.intro', locale)}"))
        setup = setup_text(cfg, locale)
        if setup:
            container.add_item(ui.TextDisplay(setup))
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

        if not common.any_feature_on(cfg) or is_shell:
            container.add_item(row(button(
                self.on_level, custom_id=cid("home", "level"),
                label=tp("home.choose_level", locale), emoji=SHIELD,
                style=discord.ButtonStyle.primary)))
        self.add_item(container)

        # Fine-tune menu: outside the container, a select because each entry
        # deserves a sentence.
        self.add_item(row(select(
            self.on_nav, custom_id=cid("home", "nav"),
            placeholder=tp("home.nav_placeholder", locale),
            options=[
                discord.SelectOption(
                    label=tp(f"nav.{key}", locale), value=key,
                    description=tp(f"nav.{key}_desc", locale)[:100],
                    emoji=common.emoji_of(emoji))
                for key, emoji in NAV_ITEMS
            ])))
        # Generic module management (state Active / Simulation / Paused, delete)
        # lives in the shared Manage panel, opened by one small button.
        bottom = [button(self.on_back, custom_id=cid("home", "back"),
                         label=t("modules.config.buttons.back", locale=locale), emoji=BACK)]
        if is_shell:
            # Old panels may still carry the former Delete button: keep its id.
            bottom.append(button(self.on_delete, custom_id=cid("home", "delete"),
                                 label=t("modules.config.buttons.delete", locale=locale),
                                 emoji=DELETE, style=discord.ButtonStyle.danger))
        else:
            manage = module_manage_button(common.MODULE_ID, state, locale)
            if manage is not None:
                bottom.append(manage)
        self.add_item(row(*bottom))

    # -- callbacks (everything is re-derived from the interaction) -------- #
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await common.gate(interaction)

    async def on_channel(self, interaction: discord.Interaction):
        await common.ack(interaction)
        values = interaction.data.get("values") or []
        if values:
            cfg = await common.load_cfg(interaction.client, interaction.guild_id)
            cfg["notify_channel_id"] = int(values[0])
            await common.save_cfg(interaction, cfg)
        await render_home(interaction)

    async def on_level(self, interaction: discord.Interaction):
        await go(interaction, "wizard_level")

    async def on_nav(self, interaction: discord.Interaction):
        values = interaction.data.get("values") or []
        screen = values[0] if values else ""
        if screen not in {key for key, _ in NAV_ITEMS}:
            await common.ack(interaction)
            return
        await go(interaction, screen)

    async def on_delete(self, interaction: discord.Interaction):
        await go(interaction, "delete")

    async def on_back(self, interaction: discord.Interaction):
        await common.back_to_modules(interaction)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_view(cls())


class AutomodDeleteView(BaseView):
    """Confirmation before deleting the whole configuration (red)."""

    __persistent__ = True

    def __init__(self, bot=None, guild_id: Optional[int] = None, locale: str = "en-US"):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.locale = locale
        self._build_view()

    def _build_view(self) -> None:
        self.clear_items()
        locale = self.locale
        container = ui.Container(accent_colour=discord.Colour(COLORS["error"]))
        container.add_item(trail(locale, t("modules.config.buttons.delete", locale=locale)))
        container.add_item(ui.TextDisplay(f"### {WARNING} {tp('delete.title', locale)}"))
        container.add_item(ui.TextDisplay(tp("delete.body", locale)))
        self.add_item(container)
        self.add_item(row(
            button(self.on_no, custom_id=cid("del", "no"), label=tp("delete.cancel", locale)),
            button(self.on_yes, custom_id=cid("del", "yes"), label=tp("delete.confirm", locale),
                   emoji=DELETE, style=discord.ButtonStyle.danger)))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await common.gate(interaction)

    async def on_no(self, interaction: discord.Interaction):
        await go(interaction, "home")

    async def on_yes(self, interaction: discord.Interaction):
        await common.ack(interaction)
        locale = i18n.get_user_locale(interaction)
        deleted = await interaction.client.module_manager.delete_module_config(
            interaction.guild_id, common.MODULE_ID)
        if not deleted:
            await common.send_error(interaction, t("modules.config.delete.error", locale=locale))
            await go(interaction, "home")
            return
        common.draft_clear_guild(interaction.guild_id)
        await go(interaction, "wizard_channel")
        await interaction.followup.send(t("modules.config.delete.success", locale=locale),
                                        ephemeral=True)

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
    SCREEN = ""

    def __init__(self, bot=None, guild_id: Optional[int] = None, locale: str = "en-US"):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.locale = locale
        self._build_view()

    def _frame(self, question_key: str, hint_key: str) -> ui.Container:
        container = ui.Container(accent_colour=discord.Colour(COLORS["primary"]))
        container.add_item(trail(self.locale))
        container.add_item(title(self.locale, "wizard.title"))
        container.add_item(ui.TextDisplay(
            f"-# {tp('wizard.step', self.locale, n=self.STEP, total=3)}\n"
            f"**{tp(question_key, self.locale)}**\n{tp(hint_key, self.locale)}"))
        return container

    def _footer(self) -> None:
        """Container closing line + the Back / Skip row, shared by every step."""
        self.add_item(row(
            button(self.on_back, custom_id=cid(self.SCREEN, "back"),
                   label=t("modules.config.buttons.back", locale=self.locale), emoji=BACK),
            button(self.on_skip, custom_id=cid(self.SCREEN, "skip"),
                   label=tp("wizard.skip", self.locale))))

    def _skip_hint(self) -> ui.TextDisplay:
        return ui.TextDisplay(f"-# {tp('wizard.skip_hint', self.locale)}")

    def _build_view(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    async def on_back(self, interaction: discord.Interaction):  # pragma: no cover
        raise NotImplementedError

    async def on_skip(self, interaction: discord.Interaction):
        """Balanced level, simulation, the channel already chosen (if any)."""
        await common.ack(interaction)
        cfg = await common.load_cfg(interaction.client, interaction.guild_id)
        new_cfg = preset_config(SKIP_PRESET, cfg.get("notify_channel_id"),
                                state=SKIP_STATE, base=cfg)
        if await common.save_cfg(interaction, new_cfg):
            await render_home(interaction)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await common.gate(interaction)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_view(cls())


class AutomodWizardChannelView(_WizardView):
    """Step 1 -- where Moddy posts its alerts (the one mandatory setting)."""

    STEP = 1
    SCREEN = "wiz1"

    def _build_view(self) -> None:
        self.clear_items()
        container = self._frame("wizard.channel_question", "wizard.channel_hint")
        channel_select = ui.ChannelSelect(
            placeholder=tp("wizard.channel_placeholder", self.locale),
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=1, max_values=1, custom_id=cid("wiz1", "channel"))
        channel_select.callback = self.on_channel
        container.add_item(row(channel_select))
        container.add_item(self._skip_hint())
        self.add_item(container)
        self._footer()

    async def on_channel(self, interaction: discord.Interaction):
        await common.ack(interaction)
        values = interaction.data.get("values") or []
        if not values:
            return
        cfg = await common.load_cfg(interaction.client, interaction.guild_id)
        cfg["notify_channel_id"] = int(values[0])
        if await common.save_cfg(interaction, common.with_state(cfg, cfg["state"])):
            await go(interaction, "wizard_level")

    async def on_back(self, interaction: discord.Interaction):
        await common.back_to_modules(interaction)


class AutomodWizardLevelView(_WizardView):
    """Step 2 -- Light / Balanced / Strict."""

    STEP = 2
    SCREEN = "wiz2"

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
        container.add_item(self._skip_hint())
        self.add_item(container)
        self._footer()

    async def on_level(self, interaction: discord.Interaction):
        await common.ack(interaction)
        preset = (interaction.data.get("custom_id") or "").rsplit(":", 1)[-1]
        if preset not in PRESETS:
            return
        cfg = await common.load_cfg(interaction.client, interaction.guild_id)
        if not cfg.get("notify_channel_id"):
            await go(interaction, "wizard_channel")
            return
        # The level is applied while the module is still paused: step 3 decides
        # whether Moddy starts in simulation or for real.
        new_cfg = preset_config(preset, cfg["notify_channel_id"], state="paused", base=cfg)
        if await common.save_cfg(interaction, new_cfg):
            await go(interaction, "wizard_mode")

    async def on_back(self, interaction: discord.Interaction):
        await go(interaction, "home")


class AutomodWizardModeView(_WizardView):
    """Step 3 -- start in simulation (recommended) or enable right away."""

    STEP = 3
    SCREEN = "wiz3"

    def _build_view(self) -> None:
        self.clear_items()
        container = self._frame("wizard.mode_question", "wizard.mode_hint")
        for action, simulated in (("sim", True), ("live", False)):
            container.add_item(ui.Section(
                ui.TextDisplay(
                    f"**{tp(f'wizard.mode_{action}', self.locale)}**"
                    f"{' — ' + tp('wizard.recommended', self.locale) if simulated else ''}\n"
                    f"{tp(f'wizard.mode_{action}_desc', self.locale)}"),
                accessory=button(
                    self.on_mode, custom_id=cid("wiz3", action),
                    label=tp(f"wizard.mode_{action}_button", self.locale),
                    style=(discord.ButtonStyle.success if simulated
                           else discord.ButtonStyle.secondary))))
        container.add_item(self._skip_hint())
        self.add_item(container)
        self._footer()

    async def on_mode(self, interaction: discord.Interaction):
        await common.ack(interaction)
        action = (interaction.data.get("custom_id") or "").rsplit(":", 1)[-1]
        if action not in ("sim", "live"):
            return
        cfg = await common.load_cfg(interaction.client, interaction.guild_id)
        state = "simulation" if action == "sim" else "active"
        if await common.save_cfg(interaction, common.with_state(cfg, state)):
            await render_home(interaction)

    async def on_back(self, interaction: discord.Interaction):
        await go(interaction, "wizard_level")


# =========================================================================== #
# Persistence marker
# =========================================================================== #
class AutomodConfigPersistence(BaseView):
    """Marker view: registers the dynamic items of the entity-scoped screens.

    The infraction type screen and the precedents browser are built from
    ``DynamicItem`` s (an infraction id / a page number cannot ride in a static
    custom_id), so their wrappers are not registered -- see
    docs/guides/PERSISTENT_VIEWS.md "Deliberate exclusions".
    """

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        from modules.configs.automod_ai_precedents_view import (
            PrecedentsDeleteSelect, PrecedentsNavButton,
        )
        from modules.configs.automod_ai_sanctions_config import (
            TypeActionSelect, TypeActionsSelect, TypeButton,
        )
        bot.add_dynamic_items(TypeButton, TypeActionSelect, TypeActionsSelect,
                              PrecedentsNavButton, PrecedentsDeleteSelect)
