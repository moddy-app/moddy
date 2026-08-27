"""
The message Moddy sends when it is added to a server.

It goes to the **person who installed it**, not to a channel: they are the one
who just clicked "Add to server", they are the one who will set it up, and a
card posted in a random channel reaches everyone except them. When the
installer cannot be identified (Moddy needs *View Audit Log* to read who added
it), the server owner is the fallback.

Like every message Moddy sends to a human, it goes through the notification
system, so it is stored, attributed and visible on the dashboard.
"""

from __future__ import annotations

import logging
from typing import Optional

import discord

import config
from cogs.error_handler import BaseView
from discord import ui
from notifications.models import ContentAuthor, NotificationContent, NotificationSource
from utils.emojis import MODDY_SQUARE_MIN
from utils.i18n import t
from utils.support_request_views import ConfigHelpButton, shorten

logger = logging.getLogger("moddy.install_welcome")

#: Accent of the card: Moddy blue.
ACCENT = 0x3661FF


def welcome_content(locale: str = "en-US") -> NotificationContent:
    """The uniform payload behind the welcome message."""
    return NotificationContent(
        title=t("notifications.install.title", locale=locale),
        body=t("notifications.install.body", locale=locale,
               server="{server}", config=config.command_label("config"),
               bug_report=config.command_label("bug-report"),
               dashboard=config.DASHBOARD_URL, support=config.SUPPORT_URL,
               docs=config.DOCS_URL),
        icon=MODDY_SQUARE_MIN,
        accent_color=ACCENT,
        links=[
            {"label": t("support.links.dashboard", locale=locale), "url": config.DASHBOARD_URL},
            {"label": t("support.links.support", locale=locale), "url": config.SUPPORT_URL},
        ],
        template_id="notifications.install",
    )


def build_welcome_view(*, guild: discord.Guild, locale: str = "en-US") -> BaseView:
    """The card the installer receives, with the way out of every dead end:
    configure it yourself, have the team do it, ask for help, read the docs."""
    content = welcome_content(locale).render({"server": guild.name})

    view = BaseView()
    container = ui.Container(accent_colour=discord.Colour(ACCENT))
    container.add_item(ui.TextDisplay(f"### {content.icon} {content.title}"))
    container.add_item(ui.TextDisplay(content.body))
    view.add_item(container)

    row = ui.ActionRow()
    row.add_item(ConfigHelpButton(guild_id=guild.id, locale=locale))
    row.add_item(ui.Button(
        label=shorten(t("support.links.dashboard", locale=locale)),
        style=discord.ButtonStyle.link, url=config.DASHBOARD_URL))
    row.add_item(ui.Button(
        label=shorten(t("support.links.support", locale=locale)),
        style=discord.ButtonStyle.link, url=config.SUPPORT_URL))
    row.add_item(ui.Button(
        label=shorten(t("support.links.docs", locale=locale)),
        style=discord.ButtonStyle.link, url=config.DOCS_URL))
    view.add_item(row)
    return view


async def resolve_installer(bot, guild: discord.Guild) -> Optional[discord.abc.User]:
    """Who added Moddy here.

    Read from the audit log, which needs *View Audit Log*; without it (or on a
    server where the entry has already aged out) the owner is the best guess,
    and the one person who can act on the message anyway.
    """
    me = guild.me
    if me is not None and me.guild_permissions.view_audit_log:
        try:
            async for entry in guild.audit_logs(
                limit=5, action=discord.AuditLogAction.bot_add
            ):
                target_id = getattr(entry.target, "id", None)
                if target_id == bot.user.id and entry.user is not None:
                    return entry.user
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.debug("Could not read the audit log of guild %s: %s", guild.id, exc)

    if guild.owner is not None:
        return guild.owner
    try:
        return await bot.fetch_user(guild.owner_id) if guild.owner_id else None
    except discord.HTTPException:
        return None


async def send_install_welcome(bot, guild: discord.Guild) -> None:
    """DM the installer. Never raises: a failed welcome must not break a join."""
    try:
        from utils.guild_language import guild_locale

        installer = await resolve_installer(bot, guild)
        if installer is None or getattr(installer, "bot", False):
            return

        locale = await guild_locale(bot, guild)
        result = await bot.notifications.send_dm(
            installer,
            content=welcome_content(locale),
            # Moddy talking about itself, to the person who just installed it.
            source=NotificationSource.service("moddy", author=ContentAuthor.MODDY),
            variables={"server": guild.name},
            view=build_welcome_view(guild=guild, locale=locale),
            locale=locale,
        )
        if result.delivered:
            logger.info("Install welcome sent to %s for guild %s",
                        installer.id, guild.id)
        elif result.forbidden:
            logger.info("Install welcome not delivered to %s (closed DMs)", installer.id)
    except Exception as exc:  # noqa: BLE001 — a welcome is never worth a failed join
        logger.error("Could not send the install welcome for guild %s: %s",
                     guild.id, exc, exc_info=True)
