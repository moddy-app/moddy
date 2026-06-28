"""
Moderation Commands — /ban, /mute, /warn

Guild-only slash commands that open a V2 Modal to collect sanction details,
then apply the Discord action and record a case via the case system.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Union

import discord
from discord import app_commands, ui
from discord.ext import commands

from cogs.error_handler import BaseModal, BaseView
from config import COLORS
from utils import emojis
from utils.i18n import t, get_locale

logger = logging.getLogger("moddy.moderation_commands")

MODDY_CASE_URL = "https://moddy.app/cases?{ref}"

# Accent colours for DM messages
_DM_ACCENT_WARN = 0xF28500
_DM_ACCENT_BAN = 0xDA3E27
_DM_ACCENT_MUTE = 0xDA3E27
# Accent colour for the moderator's confirmation panel
_CONFIRM_ACCENT = 0x38B04B


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_duration(raw: str) -> Optional[timedelta]:
    """Parse strings like '7d', '24h', '30m', '1w' into a timedelta.

    Returns None for permanent ('', 'permanent', 'perm').
    Raises ValueError for unrecognised formats.
    """
    if not raw:
        return None
    raw = raw.strip().lower()
    if raw in ("permanent", "perm", "∞"):
        return None

    total = timedelta()
    found = False
    for amount_str, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([wdhm])", raw):
        amount = float(amount_str)
        if unit == "w":
            total += timedelta(weeks=amount)
        elif unit == "d":
            total += timedelta(days=amount)
        elif unit == "h":
            total += timedelta(hours=amount)
        elif unit == "m":
            total += timedelta(minutes=amount)
        found = True

    if not found:
        try:
            return timedelta(hours=float(raw))
        except ValueError:
            raise ValueError(f"Invalid duration: {raw!r}")

    return total if total.total_seconds() > 0 else None


def _expires_text(expires_at: Optional[datetime], locale: str) -> str:
    """Return a formatted expiry string for the confirmation panel."""
    if expires_at is None:
        return t("commands.moderation.confirm.permanent", locale=locale)
    return f"<t:{int(expires_at.timestamp())}:R>"


def _make_error_view(title: str, desc: str) -> BaseView:
    view = BaseView()
    container = ui.Container(
        ui.TextDisplay(f"### {emojis.ERROR} {title}\n{desc}"),
        accent_colour=discord.Colour(COLORS["error"]),
    )
    view.add_item(container)
    return view


# ---------------------------------------------------------------------------
# V2 Modal
# ---------------------------------------------------------------------------

class SanctionModal(BaseModal):
    """V2 Modal collecting sanction details for /ban, /mute, /warn."""

    def __init__(
        self,
        *,
        action: str,
        initial_users: List[Union[discord.Member, discord.User]],
        incognito: bool,
        guild: discord.Guild,
        mod: Union[discord.Member, discord.User],
        bot,
        locale: str,
    ):
        modal_title = t(f"commands.moderation.modal.title_{action}", locale=locale)
        super().__init__(title=modal_title[:45])
        self.action = action
        self.incognito = incognito
        self.guild = guild
        self.mod = mod
        self.bot = bot
        self.locale = locale

        # ── 1. Target users ─────────────────────────────────────────────────
        user_select = ui.UserSelect(min_values=1, max_values=10, required=True)
        if initial_users:
            user_select.default_values = initial_users
        self.users_label = ui.Label(
            text=t("commands.moderation.modal.users_label", locale=locale)[:45],
            description=t("commands.moderation.modal.users_description", locale=locale)[:100],
            component=user_select,
        )
        self.add_item(self.users_label)

        # ── 2. Reason ────────────────────────────────────────────────────────
        self.reason_label = ui.Label(
            text=t("commands.moderation.modal.reason_label", locale=locale)[:45],
            component=ui.TextInput(
                style=discord.TextStyle.paragraph,
                placeholder=t("commands.moderation.modal.reason_placeholder", locale=locale)[:100],
                required=True,
                max_length=1000,
            ),
        )
        self.add_item(self.reason_label)

        # ── 3. Duration (ban / mute only) ────────────────────────────────────
        self.duration_label: Optional[ui.Label] = None
        if action in ("ban", "mute"):
            self.duration_label = ui.Label(
                text=t("commands.moderation.modal.duration_label", locale=locale)[:45],
                description=t("commands.moderation.modal.duration_description", locale=locale)[:100],
                component=ui.TextInput(
                    style=discord.TextStyle.short,
                    placeholder=t("commands.moderation.modal.duration_placeholder", locale=locale)[:100],
                    required=False,
                    max_length=50,
                ),
            )
            self.add_item(self.duration_label)

        # ── 4. Evidence files ────────────────────────────────────────────────
        self.evidence_label = ui.Label(
            text=t("commands.moderation.modal.evidence_label", locale=locale)[:45],
            description=t("commands.moderation.modal.evidence_description", locale=locale)[:100],
            component=ui.FileUpload(min_values=0, max_values=10, required=False),
        )
        self.add_item(self.evidence_label)

        # ── 5. Notify DM checkbox ────────────────────────────────────────────
        self.notify_label = ui.Label(
            text=t("commands.moderation.modal.notify_dm_label", locale=locale)[:45],
            component=ui.Checkbox(default=True),
        )
        self.add_item(self.notify_label)

    # ── Submit ───────────────────────────────────────────────────────────────

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=self.incognito)

        users: List[Union[discord.Member, discord.User]] = self.users_label.component.values
        reason: str = self.reason_label.component.value.strip()
        notify_dm: bool = self.notify_label.component.value
        attachments: List[discord.Attachment] = self.evidence_label.component.values

        # Duration
        raw_duration = ""
        if self.duration_label is not None:
            raw_duration = self.duration_label.component.value or ""
        try:
            duration = _parse_duration(raw_duration)
        except ValueError:
            await interaction.followup.send(
                view=_make_error_view(
                    t("commands.moderation.errors.invalid_duration_title", locale=self.locale),
                    t("commands.moderation.errors.invalid_duration", locale=self.locale),
                ),
                ephemeral=True,
            )
            return

        if not users:
            await interaction.followup.send(
                view=_make_error_view(
                    t("commands.moderation.errors.no_users_title", locale=self.locale),
                    t("commands.moderation.errors.no_users", locale=self.locale),
                ),
                ephemeral=True,
            )
            return

        expires_at = (datetime.now(timezone.utc) + duration) if duration is not None else None

        # Group ID links cases together when sanctioning multiple users
        group_id = uuid.uuid4() if len(users) > 1 else None

        results = []
        for user in users:
            r = await self._apply_sanction(
                user, reason, duration, expires_at, group_id, notify_dm, attachments
            )
            results.append(r)

        await self._send_confirmation(interaction, results, reason, expires_at)

    # ── Single-user sanction ─────────────────────────────────────────────────

    async def _apply_sanction(
        self,
        user: Union[discord.Member, discord.User],
        reason: str,
        duration: Optional[timedelta],
        expires_at: Optional[datetime],
        group_id: Optional[uuid.UUID],
        notify_dm: bool,
        attachments: List[discord.Attachment],
    ) -> dict:
        """Record case, execute Discord action, optionally DM the user."""
        # Mark as Moddy-initiated so case_sync does not double-record
        if not hasattr(self.bot, "_moddy_initiated_sanctions"):
            self.bot._moddy_initiated_sanctions = {}
        self.bot._moddy_initiated_sanctions[(self.guild.id, user.id, self.action)] = time.time()

        case_result = None
        try:
            case_result = await self.bot.cases.record_sanction(
                "guild",
                subject_id=user.id,
                action=self.action,
                reason=reason,
                issuer_type="discord_user",
                issuer_id=self.mod.id,
                scope_id=self.guild.id,
                expires_at=expires_at,
                group_id=group_id,
            )
        except Exception as exc:
            logger.error("Failed to record case for %s in guild %s: %s", user.id, self.guild.id, exc)

        discord_ok = await self._discord_action(user, reason, duration)

        if notify_dm and case_result:
            guild_locale = _guild_locale(self.guild)
            await self._send_dm(user, reason, expires_at, case_result["reference"], guild_locale, attachments)

        return {"user": user, "case": case_result, "discord_ok": discord_ok}

    async def _discord_action(
        self,
        user: Union[discord.Member, discord.User],
        reason: str,
        duration: Optional[timedelta],
    ) -> bool:
        """Execute the Discord-level sanction; returns True on success."""
        try:
            if self.action == "ban":
                await self.guild.ban(
                    discord.Object(id=user.id),
                    reason=reason[:512],
                    delete_message_seconds=0,
                )
            elif self.action == "mute":
                if not isinstance(user, discord.Member):
                    return False
                # Discord timeout: max 28 days
                effective = min(duration, timedelta(days=28)) if duration else timedelta(days=28)
                await user.timeout(effective, reason=reason[:512])
            # warn: no Discord action — case + DM only
            return True
        except discord.Forbidden:
            logger.warning("Missing permissions to %s %s in guild %s", self.action, user.id, self.guild.id)
            return False
        except Exception as exc:
            logger.error("Error applying %s to %s: %s", self.action, user.id, exc)
            return False

    async def _send_dm(
        self,
        user: Union[discord.Member, discord.User],
        reason: str,
        expires_at: Optional[datetime],
        reference: str,
        dm_locale: str,
        attachments: List[discord.Attachment],
    ):
        """Send a sanction DM notification to the sanctioned user."""
        try:
            accent = {
                "ban": _DM_ACCENT_BAN,
                "mute": _DM_ACCENT_MUTE,
                "warn": _DM_ACCENT_WARN,
            }.get(self.action, _DM_ACCENT_BAN)

            sanction_emoji = {
                "ban": emojis.LEGAL,
                "mute": emojis.MIC_OFF,
                "warn": emojis.WARNING,
            }.get(self.action, emojis.WARNING)

            title = t(f"commands.moderation.dm.{self.action}_title", locale=dm_locale)

            if expires_at:
                expires_text = f"<t:{int(expires_at.timestamp())}:R>"
            else:
                expires_text = t("commands.moderation.dm.permanent", locale=dm_locale)

            guild_name = self.guild.name
            guild_id = self.guild.id
            guild_url = f"https://discord.com/channels/{guild_id}"

            text = (
                f"### {sanction_emoji} {title}\n"
                f"> **{t('commands.moderation.dm.reason', locale=dm_locale)}:** {reason}\n"
                f"> **{t('commands.moderation.dm.responsible', locale=dm_locale)}:** <@{self.mod.id}>\n"
                f"> **{t('commands.moderation.dm.expires', locale=dm_locale)}:** {expires_text}\n"
                f"> **{t('commands.moderation.dm.case_id', locale=dm_locale)}:**"
                f" [``{reference}``]({MODDY_CASE_URL.format(ref=reference)})\n"
                f"-# {t('commands.moderation.dm.sent_by', locale=dm_locale, guild=guild_name, guild_id=guild_id, guild_url=guild_url)}"
            )

            dm_view = BaseView()
            container = ui.Container(
                ui.TextDisplay(text),
                accent_colour=discord.Colour(accent),
            )
            dm_view.add_item(container)

            # Evidence files as a media gallery (images / videos)
            if attachments:
                gallery = ui.MediaGallery(
                    *[discord.MediaGalleryItem(media=att.url) for att in attachments[:10]]
                )
                dm_view.add_item(gallery)

            await user.send(view=dm_view)
        except discord.Forbidden:
            pass  # DMs disabled
        except Exception as exc:
            logger.warning("Could not send DM to %s: %s", user.id, exc)

    # ── Confirmation panel ───────────────────────────────────────────────────

    async def _send_confirmation(
        self,
        interaction: discord.Interaction,
        results: List[dict],
        reason: str,
        expires_at: Optional[datetime],
    ):
        action_label = t(f"commands.moderation.confirm.action_{self.action}", locale=self.locale)
        exp_text = _expires_text(expires_at, self.locale)

        if len(results) == 1:
            r = results[0]
            user = r["user"]
            case = r["case"]
            ref = case["reference"] if case else "N/A"
            text = (
                f"### {emojis.CHECK} {user.mention} **{action_label}**\n"
                f"> **{t('commands.moderation.confirm.reason', locale=self.locale)}:** {reason}\n"
                f"> **{t('commands.moderation.confirm.duration', locale=self.locale)}:** {exp_text}\n"
                f"> **{t('commands.moderation.confirm.case_id', locale=self.locale)}:** [``{ref}``]({MODDY_CASE_URL.format(ref=ref)})"
            )
        else:
            lines = [
                f"### {emojis.CHECK} `{len(results)}` "
                f"{t('commands.moderation.confirm.users_sanctioned', locale=self.locale)} **{action_label}**\n"
                f"> **{t('commands.moderation.confirm.reason', locale=self.locale)}:** {reason}\n"
                f"> **{t('commands.moderation.confirm.duration', locale=self.locale)}:** {exp_text}",
            ]
            for r in results:
                user = r["user"]
                case = r["case"]
                ref = case["reference"] if case else "N/A"
                lines.append(f"- {user.mention} → [``{ref}``]({MODDY_CASE_URL.format(ref=ref)})")
            text = "\n".join(lines)

        view = BaseView()
        container = ui.Container(
            ui.TextDisplay(text),
            accent_colour=discord.Colour(_CONFIRM_ACCENT),
        )
        view.add_item(container)
        await interaction.followup.send(view=view, ephemeral=self.incognito)


# ---------------------------------------------------------------------------
# Helper: guild locale for DM messages
# ---------------------------------------------------------------------------

def _guild_locale(guild: discord.Guild) -> str:
    """Return the guild's preferred locale string ('fr', 'en-US', …)."""
    try:
        loc = str(guild.preferred_locale)
        # discord.py returns e.g. "fr" or "en-US"
        return loc if loc else "en-US"
    except Exception:
        return "en-US"


async def _resolve_incognito(bot, user_id: int, default: bool = True) -> bool:
    """Resolve incognito from user preference or the given default."""
    try:
        if bot.db:
            pref = await bot.db.get_attribute("user", user_id, "DEFAULT_INCOGNITO")
            if pref is not None:
                return bool(pref)
    except Exception:
        pass
    return default


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class ModerationCommands(commands.Cog):
    """Guild-only moderation slash commands: /ban, /mute, /warn."""

    def __init__(self, bot):
        self.bot = bot

    # ── /ban ─────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="ban",
        description="Ban one or more users from the server.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(ban_members=True)
    @app_commands.describe(
        user="User to ban (you can add more in the modal)",
        incognito="Show the confirmation only to you (default: True)",
    )
    async def ban(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        incognito: Optional[bool] = None,
    ):
        locale = get_locale(interaction)
        if incognito is None:
            incognito = await _resolve_incognito(self.bot, interaction.user.id, default=True)
        initial = [user]
        modal = SanctionModal(
            action="ban",
            initial_users=initial,
            incognito=incognito,
            guild=interaction.guild,
            mod=interaction.user,
            bot=self.bot,
            locale=locale,
        )
        await interaction.response.send_modal(modal)

    # ── /mute ────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="mute",
        description="Timeout (mute) one or more members.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.describe(
        user="User to timeout (you can add more in the modal)",
        incognito="Show the confirmation only to you (default: True)",
    )
    async def mute(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        incognito: Optional[bool] = None,
    ):
        locale = get_locale(interaction)
        if incognito is None:
            incognito = await _resolve_incognito(self.bot, interaction.user.id, default=True)
        initial = [user]
        modal = SanctionModal(
            action="mute",
            initial_users=initial,
            incognito=incognito,
            guild=interaction.guild,
            mod=interaction.user,
            bot=self.bot,
            locale=locale,
        )
        await interaction.response.send_modal(modal)

    # ── /warn ────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="warn",
        description="Warn one or more users.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.describe(
        user="User to warn (you can add more in the modal)",
        incognito="Show the confirmation only to you (default: True)",
    )
    async def warn(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        incognito: Optional[bool] = None,
    ):
        locale = get_locale(interaction)
        if incognito is None:
            incognito = await _resolve_incognito(self.bot, interaction.user.id, default=True)
        initial = [user]
        modal = SanctionModal(
            action="warn",
            initial_users=initial,
            incognito=incognito,
            guild=interaction.guild,
            mod=interaction.user,
            bot=self.bot,
            locale=locale,
        )
        await interaction.response.send_modal(modal)


async def setup(bot):
    await bot.add_cog(ModerationCommands(bot))
