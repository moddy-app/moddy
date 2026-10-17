"""
``/config`` -> Automod -> three of the four detail screens.

* **What is monitored** -- messages, the three kinds of image, the three parts of
  a member profile.
* **Exemptions** -- roles and channels the automod never checks, and whether
  moderators are ignored.
* **Advanced** -- the maximum sanction, the detection sensitivity and the
  learned precedents.

(The fourth, **Sanctions**, lives in ``automod_ai_sanctions_config.py``.)

Each of them is a *main sub-view*: its edits are held as a draft and written only
when the admin presses Save (Save / Cancel appear while something is pending,
Back is locked meanwhile) -- see ``automod_ai_common.SectionView``. The
simulation switch and the deletion of the configuration are not here any more:
they are the generic module state and a button of the home screen.

Rule 9 (CLAUDE.md): a select or a channel/role picker shows its own selection,
so no screen here prints "current value" under one.

Persistence: every screen is a registered shell with static
``moddy:automod:cfg:<screen>:<action>`` custom_ids; Manage Server is re-checked
on every click.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import discord
from discord import ui

from automod import constants as ac
from config import COLORS
from modules.configs import automod_ai_common as common
from modules.configs.automod_ai_common import (
    SectionView, button, cid, go, row, select, title, tp, trail,
)
from utils.emojis import (
    EDIT, GROUPS, IMAGE, MESSAGE, SEARCH, SETTINGS, TOGGLE_OFF, TOGGLE_ON, USER, WARNING,
)
from utils.i18n import i18n

logger = logging.getLogger("moddy.modules.automod_ai_advanced")

_MAX_ACTIONS = ("warn", "mute", "ban")
_ACCENT = discord.Colour(COLORS["primary"])


async def _render(interaction: discord.Interaction, section: str, view_cls, **extra) -> None:
    await common.ack(interaction)
    _stored, working, pending = await common.load_working(interaction, section)
    await common.respond(interaction, view_cls(
        interaction.client, interaction.guild_id, i18n.get_user_locale(interaction),
        working, pending, **extra))


async def render_monitoring(interaction: discord.Interaction) -> None:
    await _render(interaction, "monitoring", AutomodMonitoringView)


async def render_exemptions(interaction: discord.Interaction) -> None:
    await _render(interaction, "exemptions", AutomodExemptionsView)


async def render_advanced(interaction: discord.Interaction) -> None:
    """Advanced screen; loads the precedent count shown next to its button."""
    count = None
    db = getattr(interaction.client, "db", None)
    if ac.PRECEDENTS_ENABLED and db is not None:
        try:
            count = await db.count_precedents(interaction.guild_id)
        except Exception:  # noqa: BLE001 - the screen works without the count
            count = None
    await _render(interaction, "advanced", AutomodAdvancedView, precedent_count=count)


async def _edit(interaction: discord.Interaction, section: str, mutate, render) -> None:
    """Load the working config, apply ``mutate(working)``, keep it as the draft."""
    await common.ack(interaction)
    stored, working, _pending = await common.load_working(interaction, section)
    mutate(working)
    await common.stash(interaction, section, stored, working)
    await render(interaction)


# =========================================================================== #
# What is monitored
# =========================================================================== #
_CONTENT_OPTIONS = (("content", MESSAGE), ("image_nsfw", IMAGE),
                    ("image_gore", WARNING), ("image_scam", SEARCH))
_PROFILE_OPTIONS = (("avatar", USER), ("banner", IMAGE), ("pseudo", EDIT))


class AutomodMonitoringView(SectionView):
    """Two multi-selects: messages & images, and member profiles."""

    SECTION = "monitoring"
    SCREEN = "mon"

    def _build_view(self) -> None:
        self.clear_items()
        locale, cfg = self.locale, self.cfg
        feats = cfg["features"]
        parts = common.profile_parts(cfg)

        container = ui.Container(accent_colour=_ACCENT)
        container.add_item(trail(locale, tp("nav.monitoring", locale)))
        container.add_item(title(locale, "monitoring.title", SEARCH))
        container.add_item(ui.TextDisplay(tp("monitoring.intro", locale)))
        for item in self.pending_notice():
            container.add_item(item)
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        container.add_item(ui.TextDisplay(f"**{tp('monitoring.content_title', locale)}**"))
        container.add_item(row(select(
            self.on_content, custom_id=cid("mon", "content"), min_values=0,
            max_values=len(_CONTENT_OPTIONS),
            placeholder=tp("monitoring.content_placeholder", locale),
            options=[discord.SelectOption(
                label=tp(f"monitoring.{key}", locale),
                description=tp(f"monitoring.{key}_desc", locale)[:100],
                value=key, emoji=common.emoji_of(emoji),
                default=bool(feats[key]["enabled"]))
                for key, emoji in _CONTENT_OPTIONS])))

        container.add_item(ui.TextDisplay(f"**{tp('monitoring.profile_title', locale)}**"))
        container.add_item(row(select(
            self.on_profile, custom_id=cid("mon", "profile"), min_values=0,
            max_values=len(_PROFILE_OPTIONS),
            placeholder=tp("monitoring.profile_placeholder", locale),
            options=[discord.SelectOption(
                label=tp(f"monitoring.prof_{key}", locale),
                description=tp(f"monitoring.prof_{key}_desc", locale)[:100],
                value=key, emoji=common.emoji_of(emoji), default=key in parts)
                for key, emoji in _PROFILE_OPTIONS])))
        self.add_item(container)
        self.add_item(self.footer())

    async def on_content(self, interaction: discord.Interaction):
        chosen = set(interaction.data.get("values") or [])

        def mutate(cfg):
            for key, _ in _CONTENT_OPTIONS:
                cfg["features"][key]["enabled"] = key in chosen
        await _edit(interaction, self.SECTION, mutate, render_monitoring)

    async def on_profile(self, interaction: discord.Interaction):
        chosen = set(interaction.data.get("values") or [])

        def mutate(cfg):
            profile = cfg["features"]["profile"]
            for key, _ in _PROFILE_OPTIONS:
                profile[key] = key in chosen
            profile["enabled"] = bool(chosen)
        await _edit(interaction, self.SECTION, mutate, render_monitoring)


# =========================================================================== #
# Exemptions
# =========================================================================== #
class AutomodExemptionsView(SectionView):
    """Roles and channels that are never checked, and the moderators switch."""

    SECTION = "exemptions"
    SCREEN = "exm"

    def _build_view(self) -> None:
        self.clear_items()
        locale, cfg = self.locale, self.cfg
        shared = cfg["features"]["content"]

        container = ui.Container(accent_colour=_ACCENT)
        container.add_item(trail(locale, tp("nav.exemptions", locale)))
        container.add_item(title(locale, "exemptions.title", GROUPS))
        container.add_item(ui.TextDisplay(tp("exemptions.intro", locale)))
        for item in self.pending_notice():
            container.add_item(item)
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        container.add_item(ui.TextDisplay(f"**{tp('exemptions.roles_title', locale)}**"))
        roles = ui.RoleSelect(placeholder=tp("exemptions.roles_placeholder", locale),
                              min_values=0, max_values=25, custom_id=cid("exm", "roles"))
        roles.callback = self.on_roles
        resolved_roles = common.resolve_roles(self.bot, self.guild_id, shared["exempt_roles"])
        if resolved_roles:
            roles.default_values = resolved_roles
        container.add_item(row(roles))

        container.add_item(ui.TextDisplay(f"**{tp('exemptions.channels_title', locale)}**"))
        channels = ui.ChannelSelect(
            placeholder=tp("exemptions.channels_placeholder", locale),
            channel_types=[discord.ChannelType.text, discord.ChannelType.news,
                           discord.ChannelType.public_thread, discord.ChannelType.private_thread],
            min_values=0, max_values=25, custom_id=cid("exm", "channels"))
        channels.callback = self.on_channels
        resolved_channels = common.resolve_channels(
            self.bot, self.guild_id, shared["exempt_channels"])
        if resolved_channels:
            channels.default_values = resolved_channels
        container.add_item(row(channels))

        container.add_item(ui.TextDisplay(f"-# {tp('exemptions.moderators_hint', locale)}"))
        on = cfg["ignore_moderators"]
        container.add_item(row(button(
            self.on_moderators, custom_id=cid("exm", "moderators"),
            label=tp("exemptions.moderators", locale), emoji=TOGGLE_ON if on else TOGGLE_OFF,
            style=discord.ButtonStyle.success if on else discord.ButtonStyle.secondary)))
        self.add_item(container)
        self.add_item(self.footer())

    async def on_roles(self, interaction: discord.Interaction):
        ids = [int(v) for v in interaction.data.get("values") or []]

        def mutate(cfg):
            for fid in common.EXEMPT_FEATURES:
                cfg["features"][fid]["exempt_roles"] = list(ids)
        await _edit(interaction, self.SECTION, mutate, render_exemptions)

    async def on_channels(self, interaction: discord.Interaction):
        ids = [int(v) for v in interaction.data.get("values") or []]

        def mutate(cfg):
            for fid in common.EXEMPT_FEATURES:
                # Profiles are checked on member events: they have no channel.
                cfg["features"][fid]["exempt_channels"] = [] if fid == "profile" else list(ids)
        await _edit(interaction, self.SECTION, mutate, render_exemptions)

    async def on_moderators(self, interaction: discord.Interaction):
        def mutate(cfg):
            cfg["ignore_moderators"] = not cfg["ignore_moderators"]
        await _edit(interaction, self.SECTION, mutate, render_exemptions)


# =========================================================================== #
# Advanced
# =========================================================================== #
class AutomodAdvancedView(SectionView):
    """Maximum sanction, sensitivity, learned precedents."""

    SECTION = "advanced"
    SCREEN = "adv"

    def _build_view(self) -> None:
        self.clear_items()
        locale, cfg = self.locale, self.cfg
        is_shell = self.bot is None
        count: Optional[int] = self.extra.get("precedent_count")

        container = ui.Container(accent_colour=_ACCENT)
        container.add_item(trail(locale, tp("nav.advanced", locale)))
        container.add_item(title(locale, "advanced.title", SETTINGS))
        container.add_item(ui.TextDisplay(tp("advanced.intro", locale)))
        for item in self.pending_notice():
            container.add_item(item)
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        container.add_item(ui.TextDisplay(
            f"**{tp('advanced.max_title', locale)}**\n-# {tp('advanced.max_hint', locale)}"))
        container.add_item(row(select(
            self.on_max_action, custom_id=cid("adv", "maxaction"),
            placeholder=tp("advanced.max_title", locale),
            options=[discord.SelectOption(
                label=tp(f"advanced.max_{v}", locale), value=v,
                emoji=common.emoji_of(common.action_icon("timeout" if v == "mute" else v)),
                default=(v == cfg["max_action"])) for v in _MAX_ACTIONS])))

        container.add_item(ui.TextDisplay(
            f"**{tp('advanced.sensitivity_title', locale)}**\n-# {tp('advanced.sensitivity_hint', locale)}"))
        container.add_item(row(select(
            self.on_severity, custom_id=cid("adv", "severity"),
            placeholder=tp("advanced.sensitivity_title", locale),
            options=[discord.SelectOption(
                label=tp(f"advanced.level_{n}", locale), value=str(n),
                description=tp(f"advanced.level_{n}_desc", locale)[:100],
                default=(n == cfg["severity"]))
                for n in range(ac.SEVERITY_MIN, ac.SEVERITY_MAX + 1)])))

        if ac.PRECEDENTS_ENABLED:
            container.add_item(ui.TextDisplay(
                f"**{tp('advanced.precedents_title', locale)}**\n-# {tp('advanced.precedents_hint', locale)}"
                + (f"\n-# {tp('advanced.precedents_count', locale, n=count)}" if count else "")))
            if count or is_shell:
                container.add_item(row(button(
                    self.on_precedents, custom_id=cid("adv", "precedents"),
                    label=tp("advanced.precedents", locale), emoji=SEARCH)))
        self.add_item(container)
        self.add_item(self.footer())

    async def on_max_action(self, interaction: discord.Interaction):
        values = interaction.data.get("values") or []

        def mutate(cfg):
            if values and values[0] in _MAX_ACTIONS:
                cfg["max_action"] = values[0]
        await _edit(interaction, self.SECTION, mutate, render_advanced)

    async def on_severity(self, interaction: discord.Interaction):
        values = interaction.data.get("values") or []

        def mutate(cfg):
            if values:
                cfg["severity"] = ac.clamp_severity(values[0])
        await _edit(interaction, self.SECTION, mutate, render_advanced)

    async def on_precedents(self, interaction: discord.Interaction):
        await go(interaction, "precedents", page=0)
