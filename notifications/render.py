"""
Rendering half of the notification system: content → Discord, and the
attribution context every DM needs.

Two things live here because both are pure functions of a notification and its
source — no database writes, no sending:

* :func:`build_content_view` turns a :class:`~notifications.models.NotificationContent`
  into the Components V2 panel a recipient sees. Callers that already build
  their own rich view (a sanction card, a ticket transcript) skip this and keep
  theirs; the attribution row is appended either way.
* :func:`resolve_source_context` answers "what is written on the attribution
  buttons": the service's name and icon, the server's name, whether that server
  is verified or an official Moddy server, and — the one decision everything
  else hangs off — whether the report button is live.

The actual buttons are built in ``utils/notification_views.py`` (they are
persistent components and belong with the rest of the views).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import discord
from discord import ui

from cogs.error_handler import BaseView
from notifications.models import (
    ContentAuthor, NotificationContent, NotificationSource, get_service,
)
from utils.emojis import GROUPS, MODDY, VERIFIED, format_verification_badge
from utils.i18n import t

logger = logging.getLogger("moddy.notifications.render")

#: Guild attributes that earn the verification check on the attribution panel.
VERIFIED_GUILD_ATTRIBUTES = ("VERIFIED", "VERIFIED_ORG", "PARTNER")

#: Guild attribute marking one of Moddy's own servers. Reporting a message from
#: Moddy's own server to Moddy's abuse team is a loop with no exit, so the flag
#: button is rendered greyed out there.
OFFICIAL_GUILD_ATTRIBUTE = "OFFICIAL"


def build_content_view(
    content: NotificationContent,
    variables: Optional[Dict[str, Any]] = None,
    *,
    locale: str = "en-US",
) -> BaseView:
    """Render a uniform notification payload as a Components V2 panel.

    This is the *default* look — a titled container, the body, optional
    labelled sections, a discreet footer, then the call-to-action links as a
    row of link buttons. Features with their own established card (sanctions,
    tickets, transcriptions) pass their view to the service instead.
    """
    resolved = content.render(variables)

    view = BaseView()
    container = ui.Container(
        accent_colour=discord.Colour(resolved.accent_color)
        if resolved.accent_color is not None else None
    )

    heading = f"### {resolved.icon} {resolved.title}".replace("###  ", "### ")
    container.add_item(ui.TextDisplay(heading))
    if resolved.body:
        container.add_item(ui.TextDisplay(resolved.body))

    for section in resolved.sections:
        title = (section.get("title") or "").strip()
        body = (section.get("body") or "").strip()
        if not title and not body:
            continue
        block = f"**{title}**\n{body}" if title else body
        container.add_item(ui.TextDisplay(block))

    if resolved.footer:
        container.add_item(ui.TextDisplay(f"-# {resolved.footer}"))

    view.add_item(container)

    links = [l for l in resolved.links if l.get("url")][:5]
    if links:
        row = ui.ActionRow()
        for link in links:
            row.add_item(ui.Button(
                label=(link.get("label") or link["url"])[:80],
                url=link["url"],
                style=discord.ButtonStyle.link,
            ))
        view.add_item(row)

    return view


async def resolve_source_context(
    bot, source: NotificationSource, *, locale: str = "en-US"
) -> Dict[str, Any]:
    """Everything the attribution row and panel need about a source.

    Returns a plain dict (never raises — a missing guild or a database hiccup
    degrades to "Unknown server" rather than blocking a DM):

    ``service`` / ``service_name`` / ``service_emoji``
        The Moddy feature behind the message, when there is one.
    ``guild_name`` / ``guild_icon_url`` / ``guild_id``
        The server, when the message was sent on one's behalf.
    ``verified`` / ``official`` / ``badge``
        Whether that server carries the check, and whether it is Moddy's own.
    ``reportable``
        The final answer, source *and* guild attributes taken into account.
    ``report_block``
        Why the flag is greyed out, when it is: ``"moddy_authored"``,
        ``"official_guild"`` or ``None``.
    """
    service = get_service(source.service_id)
    ctx: Dict[str, Any] = {
        "kind": source.kind.value,
        "author": source.author.value,
        "service_id": source.service_id,
        "service_name": t(service.i18n_key, locale=locale) if service else None,
        "service_emoji": service.emoji if service else MODDY,
        "guild_id": source.guild_id,
        "guild_name": None,
        "guild_icon_url": None,
        "guild_member_count": None,
        "guild_created_at": None,
        "verified": False,
        "official": False,
        "badge": "",
        "reportable": source.base_reportable,
        "report_block": None if source.base_reportable else "moddy_authored",
    }

    if source.guild_id:
        guild = bot.get_guild(source.guild_id) if bot else None
        if guild is not None:
            ctx["guild_name"] = guild.name
            ctx["guild_icon_url"] = guild.icon.url if guild.icon else None
            ctx["guild_member_count"] = guild.member_count
            ctx["guild_created_at"] = guild.created_at
        else:
            ctx["guild_name"] = t("notifications.attribution.unknown_guild", locale=locale)

        attributes = {}
        try:
            if bot is not None and getattr(bot, "db", None):
                guild_data = await bot.db.get_guild(source.guild_id)
                attributes = (guild_data or {}).get("attributes", {}) or {}
        except Exception as exc:  # noqa: BLE001 — attribution must never block a DM
            logger.warning("Could not read guild attributes for %s: %s", source.guild_id, exc)

        ctx["official"] = bool(attributes.get(OFFICIAL_GUILD_ATTRIBUTE))
        ctx["verified"] = ctx["official"] or any(
            attributes.get(attr) for attr in VERIFIED_GUILD_ATTRIBUTES
        )
        if ctx["verified"]:
            ctx["badge"] = format_verification_badge(VERIFIED)

        # Moddy's own servers are not reportable to Moddy.
        if ctx["official"] and ctx["reportable"]:
            ctx["reportable"] = False
            ctx["report_block"] = "official_guild"

    return ctx


def source_button_emoji(ctx: Dict[str, Any]) -> str:
    """The emoji shown on the *server* attribution button.

    Buttons can only carry a real Discord emoji, never a server icon URL — the
    icon itself is shown as the thumbnail of the panel the button opens.
    """
    return VERIFIED if ctx.get("verified") else GROUPS


def author_is_guild(source: NotificationSource) -> bool:
    """Whether the wording came from the server rather than from Moddy."""
    return source.author is ContentAuthor.GUILD
