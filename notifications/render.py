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
from utils.emojis import MODDY_SQUARE_MIN, VERIFIED
from utils.i18n import t

logger = logging.getLogger("moddy.notifications.render")

#: Guild attributes that earn the verification check on the attribution panel.
VERIFIED_GUILD_ATTRIBUTES = ("VERIFIED", "VERIFIED_ORG", "PARTNER")

#: Services that ARE Moddy: their attribution line carries the check mark.
OFFICIAL_SERVICES = ("moddy", "moddy_team")

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
    ``guild_name`` / ``guild_id``
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
        "service_emoji": service.emoji if service else MODDY_SQUARE_MIN,
        "guild_id": source.guild_id,
        "guild_name": None,
        "verified": False,
        "official": False,
        "badge": "",
        "reportable": source.base_reportable,
        "report_block": None if source.base_reportable else "moddy_authored",
    }

    if source.service_id in OFFICIAL_SERVICES and not source.guild_id:
        # Moddy speaking as itself (or as its team): the check mark is what
        # tells a member this DM is not an impersonation. No database read —
        # it is true by construction.
        ctx["verified"] = True
        ctx["badge"] = VERIFIED

    if source.guild_id:
        # Everything below is best-effort: this runs on the delivery path, and
        # a missing guild or an unreachable database must cost the badge, never
        # the message.
        try:
            guild = bot.get_guild(source.guild_id) if bot else None
            ctx["guild_name"] = guild.name if guild is not None else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not resolve guild %s: %s", source.guild_id, exc)
        if not ctx["guild_name"]:
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
            ctx["badge"] = VERIFIED

        # Moddy's own servers are not reportable to Moddy.
        if ctx["official"] and ctx["reportable"]:
            ctx["reportable"] = False
            ctx["report_block"] = "official_guild"

    return ctx


#: Deep link to a server. Discord opens it when the reader is a member.
GUILD_URL = "https://discord.com/channels/{guild_id}"


def build_attribution_line(ctx: Dict[str, Any], *, locale: str = "en-US") -> Optional[str]:
    """The one greyed line closing every attributable notification.

    ``-# Sent by [**Server**](link) (`id`)`` — the same shape the sanction DMs
    have always used (``commands.moderation.dm.sent_by``), so a member sees one
    consistent sentence at the bottom of anything Moddy sends them, whatever
    feature produced it. The verification badge is appended right after the
    name when the server carries one — as the plain emoji, not the hyperlinked
    form from CLAUDE.md's badge rule, since the link breaks in this context.

    A notification with no server (a reminder, an appeal outcome) names the
    Moddy service instead. An official notice gets no line at all — the caller
    does not even reach here.
    """
    if ctx.get("guild_id") and ctx.get("guild_name"):
        return "-# " + t(
            "notifications.attribution.sent_by", locale=locale,
            guild=ctx["guild_name"],
            guild_url=GUILD_URL.format(guild_id=ctx["guild_id"]),
            guild_id=ctx["guild_id"],
            badge=ctx.get("badge") or "",
        )
    if ctx.get("service_name"):
        return "-# " + t("notifications.attribution.sent_by_service",
                         locale=locale, service=ctx["service_name"],
                         badge=ctx.get("badge") or "")
    return None


def author_is_guild(source: NotificationSource) -> bool:
    """Whether the wording came from the server rather than from Moddy."""
    return source.author is ContentAuthor.GUILD
