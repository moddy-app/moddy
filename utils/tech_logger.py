"""
Technical logs for the staff team.

These are INTERNAL technical logs, separate from user-facing UI. They are not
sent by the bot through a channel.send() — they are pushed through Discord
**webhooks**, one channel per event type. Each category resolves its webhook URL
from an environment variable (see config.LOG_WEBHOOK_ENV).

Design goals (per the team request):
- Compact, information-dense, easy to scan and exploit.
- English only.
- Rendered with Components V2 (Container + TextDisplay) with a coloured accent
  bar matching the event type — technical, but still clean.
- Booleans are shown with the `done` / `undone` custom emojis.

Everything here is best-effort: a logging failure must NEVER break a command or
an event handler, so every public method swallows its own exceptions.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp
import discord
from discord import ui, SeparatorSpacing

from config import LOG_WEBHOOKS, LOG_WEBHOOK_DEFAULT, ENV_MODE
from utils.emojis import (
    DONE, UNDONE, ADD, LOGOUT, MODDY, BUG, MODDYTEAM_BADGE, SETTINGS, COMMANDS,
    SAVE, MANAGE_USER, BLACKLIST, TIME, PAUSE, CODE,
)

logger = logging.getLogger("moddy.tech_logger")

# Accent colour (left border) per event type — technical but readable.
_ACCENTS = {
    "guild_join": 0x57F287,     # green
    "guild_remove": 0xED4245,   # red
    "error": 0xED4245,          # red
    "error_warn": 0xFEE75C,     # yellow (non-fatal)
    "lifecycle": 0x5865F2,      # blue
    "shutdown": 0x99AAB5,       # grey
    "staff_command": 0x9B59B6,  # purple
    "staff_action": 0x9B59B6,   # purple
    "command": 0x5865F2,        # blue
    "database": 0xFEE75C,       # yellow
    "security": 0xED4245,       # red
    "api_call": 0x99AAB5,       # grey (high-volume, neutral)
}

# Webhook display name per category (helps when several feeds are watched).
_USERNAMES = {
    "guild_join": "Moddy • Guild Join",
    "guild_remove": "Moddy • Guild Leave",
    "error": "Moddy • Errors",
    "lifecycle": "Moddy • Lifecycle",
    "staff_command": "Moddy • Staff Commands",
    "staff_action": "Moddy • Staff Actions",
    "command": "Moddy • Commands",
    "database": "Moddy • Database",
    "security": "Moddy • Security",
    "api_call": "Moddy • API Gateway",
}


def _b(value: Any) -> str:
    """Render a boolean as the done / undone custom emoji."""
    return DONE if value else UNDONE


def _ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _trunc(text: Any, limit: int = 300) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


class TechLogger:
    """Webhook-based technical logger. One instance lives on ``bot.tech_logger``."""

    def __init__(self, bot):
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None
        # category -> resolved url (specific or default fallback)
        self._urls = dict(LOG_WEBHOOKS)
        active = ", ".join(sorted(self._urls)) or "none"
        if LOG_WEBHOOK_DEFAULT:
            active += " (+default fallback)"
        logger.info("Tech logger ready — configured categories: %s", active)

    # ------------------------------------------------------------------ core

    def _url_for(self, category: str) -> Optional[str]:
        return self._urls.get(category) or LOG_WEBHOOK_DEFAULT or None

    async def _dispatch(self, category: str, view: ui.LayoutView, *, allow_mentions: bool = False):
        """Send a Components V2 view through the category webhook (best-effort)."""
        url = self._url_for(category)
        if not url:
            return
        try:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession()
            webhook = discord.Webhook.from_url(url, session=self._session)
            avatar = None
            if self.bot.user and self.bot.user.display_avatar:
                avatar = self.bot.user.display_avatar.url
            mentions = (
                discord.AllowedMentions(users=True, everyone=False, roles=False)
                if allow_mentions else discord.AllowedMentions.none()
            )
            await webhook.send(
                view=view,
                username=_USERNAMES.get(category, "Moddy • Logs"),
                avatar_url=avatar,
                allowed_mentions=mentions,
            )
        except Exception as exc:  # never let logging break the caller
            logger.warning("Tech log dispatch failed (%s): %s", category, exc)

    def _card(
        self,
        accent_key: str,
        emoji: str,
        title: str,
        body_lines: list[str],
        *,
        subtitle: Optional[str] = None,
        footer_extra: Optional[str] = None,
        prefix_mention: Optional[str] = None,
    ) -> ui.LayoutView:
        """Build a standardized, compact technical log card."""
        view = ui.LayoutView(timeout=None)
        container = ui.Container(accent_colour=discord.Colour(_ACCENTS.get(accent_key, 0x5865F2)))

        if prefix_mention:
            container.add_item(ui.TextDisplay(prefix_mention))

        container.add_item(ui.TextDisplay(f"### {emoji} {title}"))
        if subtitle:
            container.add_item(ui.TextDisplay(subtitle))

        if body_lines:
            container.add_item(ui.Separator(spacing=SeparatorSpacing.small))
            container.add_item(ui.TextDisplay("\n".join(body_lines)))

        footer = f"-# {TIME} <t:{_ts()}:F> • `{ENV_MODE}`"
        if footer_extra:
            footer += f" • {footer_extra}"
        container.add_item(ui.TextDisplay(footer))

        view.add_item(container)
        return view

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # -------------------------------------------------------------- guilds

    async def log_guild_join(self, guild: discord.Guild):
        try:
            humans = sum(1 for m in guild.members if not m.bot) if guild.members else None
            bots = sum(1 for m in guild.members if m.bot) if guild.members else None
            members = guild.member_count or 0
            owner = f"`{guild.owner}`" if guild.owner else "unknown"

            # Who added Moddy (requires View Audit Log permission).
            added_by = None
            try:
                async for entry in guild.audit_logs(limit=8, action=discord.AuditLogAction.bot_add):
                    if entry.target and self.bot.user and entry.target.id == self.bot.user.id:
                        added_by = entry.user
                        break
            except (discord.Forbidden, discord.HTTPException):
                pass

            lines = [
                f"**Guild** `{guild.name}` `{guild.id}`",
                f"**Owner** {owner} `{guild.owner_id}`",
            ]
            if added_by:
                lines.append(f"**Added by** `{added_by}` `{added_by.id}`")
            else:
                lines.append("**Added by** `unknown` (no audit log access)")
            lines += [
                f"**Members** `{members}`"
                + (f" • humans `{humans}` / bots `{bots}`" if humans is not None else ""),
                f"**Created** <t:{int(guild.created_at.timestamp())}:R>",
                f"**Channels** `{len(guild.channels)}` • **Roles** `{len(guild.roles)}`",
                f"**Boosts** `{guild.premium_subscription_count or 0}` (tier `{guild.premium_tier}`)"
                f" • **Locale** `{guild.preferred_locale}`",
                f"**Now serving** `{len(self.bot.guilds)}` guilds",
            ]
            view = self._card(
                "guild_join", ADD, "Guild Joined", lines,
                footer_extra=f"shard `{guild.shard_id}`",
            )
            await self._dispatch("guild_join", view)
        except Exception as exc:
            logger.warning("log_guild_join failed: %s", exc)

    async def log_guild_remove(self, guild: discord.Guild):
        try:
            owner = f"`{guild.owner}`" if guild.owner else "unknown"
            lines = [
                f"**Guild** `{guild.name}` `{guild.id}`",
                f"**Owner** {owner} `{guild.owner_id}`",
                f"**Members** `{guild.member_count or 0}`",
                f"**Created** <t:{int(guild.created_at.timestamp())}:R>",
                f"**Now serving** `{len(self.bot.guilds)}` guilds",
            ]
            view = self._card(
                "guild_remove", LOGOUT, "Guild Left", lines,
                footer_extra=f"shard `{guild.shard_id}`",
            )
            await self._dispatch("guild_remove", view)
        except Exception as exc:
            logger.warning("log_guild_remove failed: %s", exc)

    # ------------------------------------------------------------ lifecycle

    async def log_startup(
        self,
        results: list[tuple[str, bool, str]],
        *,
        version: Optional[str] = None,
        latency_ms: Optional[int] = None,
        guild_count: int = 0,
        user_count: int = 0,
        boot_seconds: Optional[float] = None,
    ):
        try:
            all_ok = all(ok for _, ok, _ in results)
            user = f"`{self.bot.user}` `{self.bot.user.id}`" if self.bot.user else "unknown"
            head = [
                f"**Bot** {user}",
                f"**Version** `{version or 'Unknown'}` • **Mode** `{ENV_MODE}`",
                f"**Latency** `{latency_ms if latency_ms is not None else '?'}ms`"
                + (f" • **Boot** `{boot_seconds:.1f}s`" if boot_seconds is not None else ""),
                f"**Guilds** `{guild_count}` • **Users** `{user_count}`",
                "",
                "**Health checks**",
            ]
            for name, ok, detail in results:
                head.append(f"{_b(ok)} **{name}** — `{_trunc(detail, 80)}`")

            subtitle = (
                f"{DONE} All systems operational."
                if all_ok else
                f"{UNDONE} Degraded: `{', '.join(n for n, ok, _ in results if not ok)}`"
            )
            view = self._card(
                "lifecycle", MODDY, "Bot Started", head,
                subtitle=subtitle,
            )
            await self._dispatch("lifecycle", view)
        except Exception as exc:
            logger.warning("log_startup failed: %s", exc)

    async def log_shutdown(self, reason: Optional[str] = None):
        try:
            uptime = None
            launch = getattr(self.bot, "launch_time", None)
            if launch:
                uptime = datetime.now(timezone.utc) - launch
            lines = [
                f"**Bot** `{self.bot.user}` `{self.bot.user.id}`" if self.bot.user else "**Bot** unknown",
                f"**Uptime** `{str(uptime).split('.')[0]}`" if uptime else "**Uptime** unknown",
                f"**Reason** `{reason}`" if reason else "**Reason** `clean shutdown`",
            ]
            view = self._card("shutdown", PAUSE, "Bot Shutting Down", lines)
            await self._dispatch("lifecycle", view)
        except Exception as exc:
            logger.warning("log_shutdown failed: %s", exc)

    # --------------------------------------------------------------- errors

    async def log_error(self, error_code: str, error_details: dict, is_fatal: bool = False):
        try:
            accent = "error" if is_fatal else "error_warn"
            lines = [
                f"**Code** `{error_code}` • **Type** `{error_details.get('type', '?')}`",
                f"**Location** `{error_details.get('file', '?')}:{error_details.get('line', '?')}`",
                f"**Message**\n```{_trunc(error_details.get('message', ''), 500)}```",
            ]
            if error_details.get("command"):
                lines.append(
                    f"**Context** `{error_details.get('command')}`"
                    f" • user `{error_details.get('user', '?')}`"
                    f" • guild `{error_details.get('guild', '?')}`"
                    f" • channel `{error_details.get('channel', '?')}`"
                )
            if is_fatal and error_details.get("traceback"):
                lines.append(f"**Traceback**\n```py\n{_trunc(error_details['traceback'], 600)}```")
            lines.append(f"{_b(bool(self.bot.db))} saved to database")

            prefix = None
            if is_fatal and getattr(self.bot, "_dev_team_ids", None):
                dev_id = next(iter(self.bot._dev_team_ids))
                prefix = f"-# <@{dev_id}> fatal error"

            view = self._card(
                accent, BUG, "Fatal Error" if is_fatal else "Error", lines,
                prefix_mention=prefix,
            )
            await self._dispatch("error", view, allow_mentions=is_fatal)
        except Exception as exc:
            logger.warning("log_error failed: %s", exc)

    # ----------------------------------------------------------------- staff

    async def log_staff_command(
        self,
        command_type: str,
        command_name: str,
        executor: discord.abc.User,
        *,
        args: str = "",
        guild: Optional[discord.Guild] = None,
        channel: Any = None,
        success: bool = True,
        transport: str = "message",
    ):
        try:
            where = f"`{guild.name}` `{guild.id}`" if guild else "DM"
            if channel is not None and getattr(channel, "id", None):
                where += f" • <#{channel.id}>"
            lines = [
                f"**Command** `{command_type}.{command_name}` • via `{transport}`",
                f"**Executor** `{executor}` `{executor.id}`",
                f"**Location** {where}",
            ]
            if args:
                lines.append(f"**Args** `{_trunc(args, 200)}`")
            lines.append(f"{_b(success)} **Executed**")
            view = self._card("staff_command", MODDYTEAM_BADGE, "Staff Command", lines)
            await self._dispatch("staff_command", view)
        except Exception as exc:
            logger.warning("log_staff_command failed: %s", exc)

    async def log_staff_action(
        self,
        action: str,
        executor: discord.abc.User,
        description: str,
        *,
        target: Optional[str] = None,
        success: bool = True,
        additional_info: Optional[dict] = None,
    ):
        try:
            lines = [
                f"**Executor** `{executor}` `{executor.id}`",
            ]
            if target:
                lines.append(f"**Target** {_trunc(target, 200)}")
            if additional_info:
                for key, value in list(additional_info.items())[:8]:
                    lines.append(f"**{key}** `{_trunc(value, 150)}`")
            lines.append(f"{_b(success)} **Done**")
            view = self._card(
                "staff_action", SETTINGS, f"Staff Action — {action}", lines,
                subtitle=_trunc(description, 400) if description else None,
            )
            await self._dispatch("staff_action", view)
        except Exception as exc:
            logger.warning("log_staff_action failed: %s", exc)

    # --------------------------------------------------------- user commands

    async def log_command(
        self,
        name: str,
        *,
        kind: str = "slash",
        user: discord.abc.User,
        guild: Optional[discord.Guild] = None,
        channel: Any = None,
        success: bool = True,
        error: Optional[str] = None,
        duration_ms: Optional[float] = None,
        args: str = "",
    ):
        """Log a non-staff command usage."""
        try:
            where = f"`{guild.name}` `{guild.id}`" if guild else "DM"
            if channel is not None and getattr(channel, "id", None):
                where += f" • <#{channel.id}>"
            status = f"{_b(success)} **Success**" if success else f"{_b(False)} **Failed**"
            if duration_ms is not None:
                status += f" • `{duration_ms:.0f}ms`"
            lines = [
                f"**Command** `{name}` • `{kind}`",
                f"**User** `{user}` `{user.id}`",
                f"**Location** {where}",
            ]
            if args:
                lines.append(f"**Args** `{_trunc(args, 200)}`")
            lines.append(status)
            if error:
                lines.append(f"**Error** `{_trunc(error, 200)}`")
            view = self._card("command", COMMANDS, "Command Used", lines)
            await self._dispatch("command", view)
        except Exception as exc:
            logger.warning("log_command failed: %s", exc)

    # ------------------------------------------------------------- database

    async def log_attribute_change(
        self,
        entity_type: str,
        entity_id: int,
        attribute: str,
        old_value: Any,
        new_value: Any,
        changed_by: Optional[int],
        reason: Optional[str],
    ):
        """Important DB write: a user/guild attribute changed (blacklist, premium,
        verified, official, staff, …)."""
        try:
            # Security-sensitive attributes get the security feed + red accent.
            sensitive = attribute.upper() in {"BLACKLISTED", "OFFICIAL", "VERIFIED", "VERIFIED_ORG", "PREMIUM", "BETA"}
            accent = "security" if attribute.upper() == "BLACKLISTED" else "database"
            emoji = BLACKLIST if attribute.upper() == "BLACKLISTED" else MANAGE_USER
            old_disp = "—" if old_value is None else f"`{_trunc(old_value, 60)}`"
            new_disp = "removed" if new_value is None else f"`{_trunc(new_value, 60)}`"
            lines = [
                f"**Entity** `{entity_type}` `{entity_id}`",
                f"**Attribute** `{attribute}`",
                f"**Change** {old_disp} → {new_disp}",
                f"**By** `{changed_by}`" if changed_by else "**By** `system`",
            ]
            if reason:
                lines.append(f"**Reason** `{_trunc(reason, 200)}`")
            view = self._card(accent, emoji, "Attribute Changed", lines)
            category = "security" if accent == "security" else "database"
            await self._dispatch(category, view)
        except Exception as exc:
            logger.warning("log_attribute_change failed: %s", exc)

    async def log_data_change(self, table: str, entity_id: int, path: str, value: Any):
        """Important DB write: a guild config / module setting changed.

        Filtered to keep the feed signal-rich (only meaningful config paths)."""
        try:
            if table != "guilds":
                return
            top = path.split(".", 1)[0]
            if top not in {"config", "modules", "logging"}:
                return
            lines = [
                f"**Guild** `{entity_id}`",
                f"**Path** `{path}`",
                f"**Value** `{_trunc(value, 200)}`",
            ]
            view = self._card("database", SAVE, "Config Changed", lines)
            await self._dispatch("database", view)
        except Exception as exc:
            logger.warning("log_data_change failed: %s", exc)

    # ------------------------------------------------------------- security

    async def log_security(self, title: str, lines: list[str]):
        """Generic sensitive-event log (e.g. blacklisted user blocked)."""
        try:
            view = self._card("security", BLACKLIST, title, lines)
            await self._dispatch("security", view)
        except Exception as exc:
            logger.warning("log_security failed: %s", exc)

    # ---------------------------------------------------------- api gateway

    async def log_api_call(self, entry: dict) -> None:
        """Log every outbound API call from the gateway (success or failure).

        ``entry`` is the dict produced by GatewayLogger.record().
        This method always routes through the standard _card / _dispatch
        pipeline so it appears in the configured api_call webhook channel.
        """
        try:
            provider = entry.get("provider", "?")
            operation = entry.get("operation", "?")
            call_type = entry.get("call_type", "?")
            success = entry.get("success", False)
            latency_ms = entry.get("latency_ms", 0)
            attempts = entry.get("attempts", 1)
            tokens_total = entry.get("tokens_total") or 0
            error_type = entry.get("error_type")
            model = entry.get("model")
            guild_id = entry.get("guild_id")
            user_id = entry.get("user_id")
            cost = entry.get("estimated_cost")
            cid = _trunc(entry.get("correlation_id", "?"), 36)

            title = f"API Call — {provider}/{operation}"
            status = f"{_b(success)} **{'OK' if success else 'FAILED'}**"
            if error_type:
                status += f" `{error_type}`"
            status += f" • `{latency_ms}ms`"
            if attempts > 1:
                status += f" • {attempts} attempts"

            lines = [
                f"**Type** `{call_type}` • **Model** `{model or 'N/A'}`",
                status,
            ]
            if tokens_total:
                lines.append(
                    f"**Tokens** `{tokens_total}`"
                    + (f" • **Cost** `${cost:.6f}`" if cost else "")
                )
            if guild_id:
                lines.append(f"**Guild** `{guild_id}`")
            if user_id:
                lines.append(f"**User** `{user_id}`")
            lines.append(f"**CID** `{cid}`")

            view = self._card("api_call", CODE, title, lines)
            await self._dispatch("api_call", view)
        except Exception as exc:
            logger.warning("log_api_call failed: %s", exc)


def init_tech_logger(bot) -> TechLogger:
    """Create the tech logger and attach it to the bot."""
    bot.tech_logger = TechLogger(bot)
    return bot.tech_logger
