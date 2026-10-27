"""
Bump prospect UI — the team card, its buttons, and the manual-scan summary.

One card per prospect server, in ``config.MODDY_BUMP_PROSPECT_CHANNEL_ID``:
what the server is, which directories it bumps on, and where the team stands
on it. Its buttons:

* **Invite** — a single-use, 24 h invite, created on click and shown only to
  the staffer who asked (never stored on the card: anyone reading the channel
  would burn it);
* **Claim** / **Unclaim** — "I'm on it", so two staffers don't pitch the same
  server; only the claimant can release it;
* **Close** / **Reopen** — done with it, either way.

``converted`` (the module got enabled) and ``left`` (Moddy was removed) are set
by the bot and leave the card without buttons.

Persistence
-----------
Every button is a :class:`discord.ui.DynamicItem` whose ``custom_id`` carries
the server id, registered through :class:`BumpProspectPersistence`; every
click re-reads the row and re-checks the ``bump_prospect`` staff node.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

import discord
from discord import ui

from bumpreminder import bot_by_key
from cogs.error_handler import BaseView
from db.repositories.bump_prospects import (
    STATUS_CLAIMED, STATUS_CLOSED, STATUS_CONVERTED, STATUS_LEFT, STATUS_OPEN,
)
from utils.components_v2 import create_error_message
from utils.emojis import (
    CLAIM, DONE, GROUPS, HAND, LINK, REFRESH, ROCKET_LAUNCH, TIME, USER,
)
from utils.i18n import i18n, t

logger = logging.getLogger("moddy.bump_prospect_views")

#: Staff node every button checks (``utils/staff_role_permissions.py``).
STAFF_NODE = "bump_prospect"

_GUILD = r"\d{1,20}"

_STATUS_ACCENT = {
    STATUS_OPEN: 0x3661FF,
    STATUS_CLAIMED: 0xFEE75C,
    STATUS_CLOSED: 0x99AAB5,
    STATUS_CONVERTED: 0x57F287,
    STATUS_LEFT: 0xED4245,
}


def _guarded(callback):
    """Route unknown errors from a dynamic item to the central error handler
    (no live ``BaseView`` behind a dynamic item, so ``on_error`` never fires)."""
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as exc:  # noqa: BLE001 — funnel everything to the handler
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, exc, self.__class__.__name__)
    return wrapper


def _short(text: str, limit: int = 80) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


# =========================================================================== #
# Cards
# =========================================================================== #

def directories_line(keys) -> str:
    parts = []
    for key in keys or []:
        spec = bot_by_key(key)
        parts.append(f"{spec.emoji} **{spec.name}**" if spec else f"`{key}`")
    return ", ".join(parts)


def build_prospect_card(*, prospect: Dict[str, Any], guild: Optional[discord.Guild] = None,
                        locale: str = "en-US") -> BaseView:
    """The card the team acts on. ``guild`` is ``None`` once Moddy left it."""
    status = prospect.get("status") or STATUS_OPEN
    guild_id = prospect["guild_id"]
    name = guild.name if guild is not None else (
        prospect.get("guild_name") or t("bump_prospects.card.unknown_guild", locale=locale))

    view = BaseView()
    container = ui.Container(accent_colour=discord.Colour(_STATUS_ACCENT.get(status, 0x3661FF)))
    container.add_item(ui.TextDisplay(
        f"### {ROCKET_LAUNCH} {t('bump_prospects.card.title', locale=locale)}"))
    container.add_item(ui.TextDisplay(
        f"{t('bump_prospects.card.status_label', locale=locale)} "
        f"**{t(f'bump_prospects.status.{status}', locale=locale)}**\n"
        f"-# {t('bump_prospects.card.why', locale=locale)}"))
    container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

    lines = [f"{GROUPS} **{t('bump_prospects.card.server', locale=locale)}** "
             f"`{_short(name, 100).replace('`', chr(39))}` (`{guild_id}`)"]
    if guild is not None:
        lines.append(f"{USER} **{t('bump_prospects.card.owner', locale=locale)}** "
                     f"<@{guild.owner_id}> (`{guild.owner_id}`)")
        lines.append(f"{GROUPS} **{t('bump_prospects.card.members', locale=locale)}** "
                     f"`{guild.member_count or 0}`")
    lines.append(f"{TIME} **{t('bump_prospects.card.detected', locale=locale)}** "
                 + (f"<t:{int(prospect['created_at'].timestamp())}:f>"
                    if prospect.get("created_at") else "`—`"))
    lines.append(f"**{t('bump_prospects.card.directories', locale=locale)}** "
                 f"{directories_line(prospect.get('directories'))}")
    container.add_item(ui.TextDisplay("\n".join(lines)))

    footer = []
    if prospect.get("claimed_by"):
        footer.append(f"-# {HAND} {t('bump_prospects.card.claimed_by', locale=locale)} "
                      f"<@{prospect['claimed_by']}>")
    if status == STATUS_CLOSED and prospect.get("closed_by"):
        footer.append(f"-# {DONE} {t('bump_prospects.card.closed_by', locale=locale)} "
                      f"<@{prospect['closed_by']}>")
    if status in (STATUS_CONVERTED, STATUS_LEFT):
        footer.append(f"-# {t(f'bump_prospects.card.{status}', locale=locale)}")
    if footer:
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay("\n".join(footer)))
    view.add_item(container)

    if status in (STATUS_OPEN, STATUS_CLAIMED):
        row = ui.ActionRow()
        row.add_item(ProspectInviteButton(guild_id, locale=locale))
        row.add_item(ProspectClaimButton(guild_id, claimed=status == STATUS_CLAIMED, locale=locale))
        row.add_item(ProspectCloseButton(guild_id, locale=locale))
        view.add_item(row)
    elif status == STATUS_CLOSED:
        row = ui.ActionRow()
        row.add_item(ProspectReopenButton(guild_id, locale=locale))
        view.add_item(row)
    return view


def build_scan_summary(run: Dict[str, Any], *, duration: int, locale: str = "en-US") -> BaseView:
    """Posted in the prospect channel when a ``/dev bumpscan`` run finishes."""
    view = BaseView()
    container = ui.Container(accent_colour=discord.Colour(0x3661FF))
    container.add_item(ui.TextDisplay(
        f"### {REFRESH} {t('bump_prospects.scan.summary.title', locale=locale)}"))
    container.add_item(ui.TextDisplay(t(
        "bump_prospects.scan.summary.description", locale=locale,
        scanned=f"`{run.get('scanned', 0)}`", new=f"`{run.get('new', 0)}`",
        converted=f"`{run.get('converted', 0)}`", skipped=f"`{run.get('skipped', 0)}`",
        minutes=f"`{max(1, round(duration / 60))}`")))
    if run.get("requested_by"):
        container.add_item(ui.TextDisplay(
            f"-# {t('bump_prospects.scan.summary.requested_by', locale=locale)} "
            f"<@{run['requested_by']}>"))
    view.add_item(container)
    return view


# =========================================================================== #
# Buttons (persistent)
# =========================================================================== #

async def _say(interaction: discord.Interaction, view) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(view=view, ephemeral=True)
    else:
        await interaction.response.send_message(view=view, ephemeral=True)


async def _error(interaction: discord.Interaction, key: str, locale: str, **kwargs) -> None:
    await _say(interaction, create_error_message(
        t(f"bump_prospects.errors.{key}.title", locale=locale),
        t(f"bump_prospects.errors.{key}.description", locale=locale, **kwargs)))


async def _prepare(interaction: discord.Interaction, guild_id: int, locale: str):
    """Defer, check the staff node, load the row. ``None`` when refused."""
    from utils.staff_permissions import has_staff_node

    await interaction.response.defer(ephemeral=True, thinking=False)
    if not await has_staff_node(interaction.client, interaction.user.id, STAFF_NODE):
        await _error(interaction, "no_permission", locale)
        return None
    service = getattr(interaction.client, "bump_prospects", None)
    prospect = await service.get(guild_id) if service else None
    if prospect is None:
        await _error(interaction, "unknown", locale, guild=f"`{guild_id}`")
        return None
    return prospect


class ProspectInviteButton(ui.DynamicItem[ui.Button],
                           template=rf"moddy:bumpprospect:invite:(?P<guild>{_GUILD})"):
    """Hand the clicker a single-use invite to the server."""

    def __init__(self, guild_id: int, *, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=_short(t("bump_prospects.buttons.invite", locale=locale)),
            style=discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(LINK),
            custom_id=f"moddy:bumpprospect:invite:{guild_id}",
        ))
        self.guild_id = int(guild_id)

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["guild"]))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        if await _prepare(interaction, self.guild_id, locale) is None:
            return
        url = await interaction.client.bump_prospects.invite(self.guild_id, interaction.user)
        if not url:
            await _error(interaction, "invite_failed", locale)
            return
        view = BaseView()
        container = ui.Container()
        container.add_item(ui.TextDisplay(
            f"### {LINK} {t('bump_prospects.invite.title', locale=locale)}\n"
            f"{t('bump_prospects.invite.description', locale=locale)}\n`{url}`"))
        view.add_item(container)
        row = ui.ActionRow()
        row.add_item(ui.Button(label=_short(t("bump_prospects.invite.open", locale=locale)),
                               url=url, style=discord.ButtonStyle.link))
        view.add_item(row)
        await interaction.followup.send(view=view, ephemeral=True)


class ProspectClaimButton(ui.DynamicItem[ui.Button],
                          template=rf"moddy:bumpprospect:claim:(?P<guild>{_GUILD})"):
    """Claim an open card; on a claimed card, release it (claimant only)."""

    def __init__(self, guild_id: int, *, claimed: bool = False, locale: str = "en-US"):
        key = "bump_prospects.buttons.unclaim" if claimed else "bump_prospects.buttons.claim"
        super().__init__(ui.Button(
            label=_short(t(key, locale=locale)),
            style=discord.ButtonStyle.secondary if claimed else discord.ButtonStyle.primary,
            emoji=discord.PartialEmoji.from_str(CLAIM),
            custom_id=f"moddy:bumpprospect:claim:{guild_id}",
        ))
        self.guild_id = int(guild_id)

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["guild"]))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        prospect = await _prepare(interaction, self.guild_id, locale)
        if prospect is None:
            return
        service = interaction.client.bump_prospects
        # The row decides, not the label the card happened to show.
        if prospect["status"] == STATUS_CLAIMED:
            if prospect["claimed_by"] != interaction.user.id:
                await _error(interaction, "not_claimant", locale,
                             user=f"<@{prospect['claimed_by']}>")
                return
            done = await service.unclaim(self.guild_id, interaction.user)
        else:
            done = await service.claim(self.guild_id, interaction.user)
        if done is None:
            await _error(interaction, "stale", locale)


class ProspectCloseButton(ui.DynamicItem[ui.Button],
                          template=rf"moddy:bumpprospect:close:(?P<guild>{_GUILD})"):
    """Done with this server, whatever the outcome."""

    def __init__(self, guild_id: int, *, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=_short(t("bump_prospects.buttons.close", locale=locale)),
            style=discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(DONE),
            custom_id=f"moddy:bumpprospect:close:{guild_id}",
        ))
        self.guild_id = int(guild_id)

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["guild"]))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        if await _prepare(interaction, self.guild_id, locale) is None:
            return
        if await interaction.client.bump_prospects.close(self.guild_id, interaction.user) is None:
            await _error(interaction, "stale", locale)


class ProspectReopenButton(ui.DynamicItem[ui.Button],
                           template=rf"moddy:bumpprospect:reopen:(?P<guild>{_GUILD})"):
    """Bring a closed card back."""

    def __init__(self, guild_id: int, *, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=_short(t("bump_prospects.buttons.reopen", locale=locale)),
            style=discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(REFRESH),
            custom_id=f"moddy:bumpprospect:reopen:{guild_id}",
        ))
        self.guild_id = int(guild_id)

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["guild"]))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        if await _prepare(interaction, self.guild_id, locale) is None:
            return
        if await interaction.client.bump_prospects.reopen(self.guild_id) is None:
            await _error(interaction, "stale", locale)


# =========================================================================== #
# Persistence
# =========================================================================== #

class BumpProspectPersistence(BaseView):
    """Marker view: registers the prospect card's dynamic items at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(
            ProspectInviteButton,
            ProspectClaimButton,
            ProspectCloseButton,
            ProspectReopenButton,
        )
