"""
``/config`` -> Automod AI -> the four smaller screens.

* **What is monitored** — messages, the three kinds of image, the three parts of
  a member profile.
* **AI guidance** (indications) — free text the AI takes into account, checked
  against prompt injection before it is stored.
* **Exemptions** — roles and channels the automod never checks, and whether
  moderators are ignored.
* **Advanced** — simulation mode, the maximum sanction, the detection
  sensitivity, the learned precedents and deleting the configuration.

Rule 9 (CLAUDE.md): a select or a channel/role picker shows its own selection,
so no screen here prints "current value" under one. Text is only printed for a
setting edited through a button that opens a Modal (the guidance).

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
from automod.rules_check import MAX_RULES_LENGTH, validate_rules
from cogs.error_handler import BaseModal, BaseView
from modules.configs import automod_ai_common as common
from modules.configs.automod_ai_common import back_row, button, cid, go, row, select, title, tp
from utils.emojis import (
    BOOK, DELETE, EDIT, GROUPS, IMAGE, MESSAGE, SEARCH, SETTINGS,
    TOGGLE_OFF, TOGGLE_ON, USER, WARNING,
)
from utils.i18n import i18n

logger = logging.getLogger("moddy.modules.automod_ai_advanced")

_PREVIEW_CHARS = 700
_MAX_ACTIONS = ("warn", "mute", "ban")


async def _screen_state(interaction: discord.Interaction):
    return (interaction.client, interaction.guild_id, i18n.get_user_locale(interaction),
            await common.load_cfg(interaction.client, interaction.guild_id))


async def render_monitoring(interaction: discord.Interaction) -> None:
    bot, gid, locale, cfg = await _screen_state(interaction)
    await common.respond(interaction, AutomodMonitoringView(bot, gid, locale, cfg))


async def render_indications(interaction: discord.Interaction) -> None:
    bot, gid, locale, cfg = await _screen_state(interaction)
    await common.respond(interaction, AutomodIndicationsView(bot, gid, locale, cfg))


async def render_exemptions(interaction: discord.Interaction) -> None:
    bot, gid, locale, cfg = await _screen_state(interaction)
    await common.respond(interaction, AutomodExemptionsView(bot, gid, locale, cfg))


class _ScreenView(BaseView):
    """Frame shared by the four screens: state in, shell-safe, guild-gated."""

    __persistent__ = True
    SCREEN = ""

    def __init__(self, bot=None, guild_id: Optional[int] = None, locale: str = "en-US",
                 cfg: Optional[Dict[str, Any]] = None, **extra: Any):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.locale = locale
        self.cfg = common.normalize_cfg(cfg)
        self.extra = extra
        self._build_view()

    def _build_view(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await common.gate(interaction)

    async def on_back(self, interaction: discord.Interaction):
        await go(interaction, "home")

    async def _commit(self, interaction: discord.Interaction, cfg: Dict[str, Any],
                      render) -> None:
        """Save, then re-render from the database (also after a refusal)."""
        await interaction.response.defer()
        await common.save_cfg(interaction, cfg)
        await render(interaction)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_view(cls())


# =========================================================================== #
# What is monitored
# =========================================================================== #
_CONTENT_OPTIONS = (("content", MESSAGE), ("image_nsfw", IMAGE),
                    ("image_gore", WARNING), ("image_scam", SEARCH))
_PROFILE_OPTIONS = (("avatar", USER), ("banner", IMAGE), ("pseudo", EDIT))


class AutomodMonitoringView(_ScreenView):
    """Two multi-selects: messages & images, and member profiles."""

    def _build_view(self) -> None:
        self.clear_items()
        locale, cfg = self.locale, self.cfg
        feats = cfg["features"]
        parts = common.profile_parts(cfg)

        container = ui.Container()
        container.add_item(title(locale, "monitoring.title", SEARCH))
        container.add_item(ui.TextDisplay(tp("monitoring.intro", locale)))
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
        container.add_item(ui.TextDisplay(f"-# {tp('monitoring.footer', locale)}"))
        self.add_item(container)
        self.add_item(back_row(self.on_back, "mon", locale))

    async def on_content(self, interaction: discord.Interaction):
        chosen = set(interaction.data.get("values") or [])
        cfg = await common.load_cfg(interaction.client, interaction.guild_id)
        for key, _ in _CONTENT_OPTIONS:
            cfg["features"][key]["enabled"] = key in chosen
        await self._commit(interaction, cfg, render_monitoring)

    async def on_profile(self, interaction: discord.Interaction):
        chosen = set(interaction.data.get("values") or [])
        cfg = await common.load_cfg(interaction.client, interaction.guild_id)
        profile = cfg["features"]["profile"]
        for key, _ in _PROFILE_OPTIONS:
            profile[key] = key in chosen
        profile["enabled"] = bool(chosen)
        await self._commit(interaction, cfg, render_monitoring)


# =========================================================================== #
# AI guidance
# =========================================================================== #
class IndicationsModal(BaseModal):
    """Edit the free-text guidance (checked against prompt injection)."""

    def __init__(self, locale: str, current: str = ""):
        super().__init__(title=tp("indications.modal_title", locale)[:45])
        self.locale = locale
        self.field = ui.Label(
            text=tp("indications.modal_label", locale)[:45],
            description=tp("indications.modal_desc", locale)[:100],
            component=ui.TextInput(
                placeholder=tp("indications.modal_placeholder", locale)[:100],
                default=current[:MAX_RULES_LENGTH] or None,
                style=discord.TextStyle.paragraph, max_length=MAX_RULES_LENGTH,
                required=False))
        self.add_item(self.field)

    async def on_submit(self, interaction: discord.Interaction):
        locale = self.locale
        text = (self.field.component.value or "").strip()
        await interaction.response.defer()
        if text:  # clearing needs no AI call
            safe, reason = await validate_rules(interaction.client, interaction.guild_id, text)
            if not safe:
                key = {"too_long": "indications.error_too_long",
                       "unavailable": "indications.error_unavailable"}.get(reason)
                body = tp(key, locale) if key else tp("indications.error_unsafe", locale,
                                                      reason=reason or "—")
                await common.send_error(interaction, body)
                return
        cfg = await common.load_cfg(interaction.client, interaction.guild_id)
        cfg["indications"] = text
        if await common.save_cfg(interaction, cfg):
            await render_indications(interaction)
            await interaction.followup.send(
                tp("indications.saved" if text else "indications.cleared", locale), ephemeral=True)
        else:
            await render_indications(interaction)


class AutomodIndicationsView(_ScreenView):
    """The guidance text, its preview and the edit button."""

    def _build_view(self) -> None:
        self.clear_items()
        locale, text = self.locale, self.cfg["indications"]
        container = ui.Container()
        container.add_item(title(locale, "indications.title", BOOK))
        container.add_item(ui.TextDisplay(tp("indications.intro", locale)))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        # A button opens a Modal here, so the stored text must be shown (rule 9).
        if text:
            preview = common.safe_text(text[:_PREVIEW_CHARS]) + ("…" if len(text) > _PREVIEW_CHARS else "")
            quoted = "\n".join(f"> {line}" for line in preview.splitlines() or [""])
            container.add_item(ui.TextDisplay(f"**{tp('indications.current', locale)}**\n{quoted}"))
        else:
            container.add_item(ui.TextDisplay(f"-# {tp('indications.empty', locale)}"))
        buttons = [button(self.on_edit, custom_id=cid("ind", "edit"),
                          label=tp("indications.edit", locale), emoji=EDIT,
                          style=discord.ButtonStyle.primary)]
        if text or self.bot is None:
            buttons.append(button(self.on_clear, custom_id=cid("ind", "clear"),
                                  label=tp("indications.clear", locale), emoji=DELETE,
                                  style=discord.ButtonStyle.danger))
        container.add_item(row(*buttons))
        self.add_item(container)
        self.add_item(back_row(self.on_back, "ind", locale))

    async def on_edit(self, interaction: discord.Interaction):
        cfg = await common.load_cfg(interaction.client, interaction.guild_id)
        modal = IndicationsModal(i18n.get_user_locale(interaction), cfg["indications"])
        modal.bot = interaction.client
        await interaction.response.send_modal(modal)

    async def on_clear(self, interaction: discord.Interaction):
        cfg = await common.load_cfg(interaction.client, interaction.guild_id)
        cfg["indications"] = ""
        await self._commit(interaction, cfg, render_indications)


# =========================================================================== #
# Exemptions
# =========================================================================== #
class AutomodExemptionsView(_ScreenView):
    """Roles and channels that are never checked, and the moderators switch."""

    def _build_view(self) -> None:
        self.clear_items()
        locale, cfg = self.locale, self.cfg
        shared = cfg["features"]["content"]

        container = ui.Container()
        container.add_item(title(locale, "exemptions.title", GROUPS))
        container.add_item(ui.TextDisplay(tp("exemptions.intro", locale)))
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
        self.add_item(back_row(self.on_back, "exm", locale))

    async def on_roles(self, interaction: discord.Interaction):
        ids = [int(v) for v in interaction.data.get("values") or []]
        cfg = await common.load_cfg(interaction.client, interaction.guild_id)
        for fid in common.EXEMPT_FEATURES:
            cfg["features"][fid]["exempt_roles"] = list(ids)
        await self._commit(interaction, cfg, render_exemptions)

    async def on_channels(self, interaction: discord.Interaction):
        ids = [int(v) for v in interaction.data.get("values") or []]
        cfg = await common.load_cfg(interaction.client, interaction.guild_id)
        for fid in common.EXEMPT_FEATURES:
            # Profiles are checked on member events: they have no channel.
            cfg["features"][fid]["exempt_channels"] = [] if fid == "profile" else list(ids)
        await self._commit(interaction, cfg, render_exemptions)

    async def on_moderators(self, interaction: discord.Interaction):
        cfg = await common.load_cfg(interaction.client, interaction.guild_id)
        cfg["ignore_moderators"] = not cfg["ignore_moderators"]
        await self._commit(interaction, cfg, render_exemptions)


# =========================================================================== #
# Advanced
# =========================================================================== #
class AutomodAdvancedView(_ScreenView):
    """Simulation, maximum sanction, sensitivity, precedents, delete."""

    def _build_view(self) -> None:
        self.clear_items()
        locale, cfg = self.locale, self.cfg
        is_shell = self.bot is None
        confirm = bool(self.extra.get("confirm_delete"))
        count: Optional[int] = self.extra.get("precedent_count")

        container = ui.Container()
        container.add_item(title(locale, "advanced.title", SETTINGS))

        if confirm and not is_shell:
            container.add_item(ui.TextDisplay(
                f"{WARNING} **{tp('advanced.delete_title', locale)}**\n"
                f"{tp('advanced.delete_body', locale)}"))
            container.add_item(row(
                button(self.on_delete_no, custom_id=cid("adv", "delno"),
                       label=tp("advanced.cancel", locale)),
                button(self.on_delete_yes, custom_id=cid("adv", "delyes"),
                       label=tp("advanced.delete_confirm", locale), emoji=DELETE,
                       style=discord.ButtonStyle.danger)))
            self.add_item(container)
            return

        container.add_item(ui.TextDisplay(tp("advanced.intro", locale)))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        dry = cfg["dry_run"]
        container.add_item(ui.TextDisplay(
            f"**{tp('advanced.simulation_title', locale)}**\n-# {tp('advanced.simulation_hint', locale)}"))
        container.add_item(row(button(
            self.on_dry_run, custom_id=cid("adv", "dryrun"),
            label=tp("advanced.simulation", locale), emoji=TOGGLE_ON if dry else TOGGLE_OFF,
            style=discord.ButtonStyle.success if dry else discord.ButtonStyle.secondary)))

        container.add_item(ui.TextDisplay(
            f"**{tp('advanced.max_title', locale)}**\n-# {tp('advanced.max_hint', locale)}"))
        container.add_item(row(select(
            self.on_max_action, custom_id=cid("adv", "maxaction"),
            placeholder=tp("advanced.max_title", locale),
            options=[discord.SelectOption(
                label=tp(f"advanced.max_{v}", locale), value=v,
                emoji=common.emoji_of(common.action_icon("ban" if v == "ban" else v)),
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

        buttons = []
        if ac.PRECEDENTS_ENABLED:
            container.add_item(ui.TextDisplay(
                f"**{tp('advanced.precedents_title', locale)}**\n-# {tp('advanced.precedents_hint', locale)}"
                + (f"\n-# {tp('advanced.precedents_count', locale, n=count)}" if count else "")))
            if count or is_shell:
                buttons.append(button(
                    self.on_precedents, custom_id=cid("adv", "precedents"),
                    label=tp("advanced.precedents", locale), emoji=SEARCH))
        buttons.append(button(
            self.on_delete, custom_id=cid("adv", "delete"),
            label=tp("advanced.delete", locale), emoji=DELETE, style=discord.ButtonStyle.danger))
        container.add_item(row(*buttons))
        self.add_item(container)
        self.add_item(back_row(self.on_back, "adv", locale))

        if is_shell:  # register the confirmation controls too
            self.add_item(row(
                button(self.on_delete_no, custom_id=cid("adv", "delno"), label="-"),
                button(self.on_delete_yes, custom_id=cid("adv", "delyes"), label="-")))

    async def on_dry_run(self, interaction: discord.Interaction):
        cfg = await common.load_cfg(interaction.client, interaction.guild_id)
        cfg["dry_run"] = not cfg["dry_run"]
        await self._commit(interaction, cfg, render_advanced)

    async def on_max_action(self, interaction: discord.Interaction):
        values = interaction.data.get("values") or []
        cfg = await common.load_cfg(interaction.client, interaction.guild_id)
        if values and values[0] in _MAX_ACTIONS:
            cfg["max_action"] = values[0]
        await self._commit(interaction, cfg, render_advanced)

    async def on_severity(self, interaction: discord.Interaction):
        values = interaction.data.get("values") or []
        cfg = await common.load_cfg(interaction.client, interaction.guild_id)
        if values:
            cfg["severity"] = ac.clamp_severity(values[0])
        await self._commit(interaction, cfg, render_advanced)

    async def on_precedents(self, interaction: discord.Interaction):
        await go(interaction, "precedents", page=0)

    async def on_delete(self, interaction: discord.Interaction):
        await render_advanced(interaction, confirm_delete=True)

    async def on_delete_no(self, interaction: discord.Interaction):
        await render_advanced(interaction)

    async def on_delete_yes(self, interaction: discord.Interaction):
        await interaction.client.module_manager.delete_module_config(
            interaction.guild_id, common.MODULE_ID)
        await go(interaction, "wizard_channel")


async def render_advanced(interaction: discord.Interaction, *, confirm_delete: bool = False) -> None:
    """Advanced screen; loads the precedent count shown next to its button."""
    bot, gid, locale, cfg = await _screen_state(interaction)
    count = None
    db = getattr(bot, "db", None)
    if ac.PRECEDENTS_ENABLED and db is not None:
        try:
            count = await db.count_precedents(gid)
        except Exception:  # noqa: BLE001 - the screen works without the count
            count = None
    await common.respond(interaction, AutomodAdvancedView(
        bot, gid, locale, cfg, confirm_delete=confirm_delete, precedent_count=count))

