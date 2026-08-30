"""
Support requests — the team's side of ``/bug-report`` and "configure it for me".

One service owns both flows because they are the same object: a user wrote to
the Moddy team about one server, a staffer takes it, answers, and closes it. The
only differences are the channel the card lands in and the wording on it.

What it does:

1. writes the request row (``db/repositories/support_requests.py``);
2. posts the staff card into the right channel and remembers where, so the
   card can be refreshed by a click months later;
3. carries every staff reply to the reporter **through the notification
   system** — never ``user.send`` — so the exchange is stored, attributable and
   visible on the dashboard like everything else Moddy sends;
4. brings the reporter's follow-ups back to the card.

``bot.support_requests`` is the instance. Like the notification service, it is
defensive: a database outage costs the record and the buttons, never the
message the user was trying to send.

See docs/SUPPORT_REQUESTS.md.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import discord

import config
from db.repositories.support_requests import (
    AUTHOR_STAFF, AUTHOR_USER, KIND_BUG, KIND_CONFIG_HELP,
)
from notifications.models import (
    ContentAuthor, NotificationContent, NotificationSource,
)
from utils import emojis
from utils.i18n import t

logger = logging.getLogger("moddy.support_requests")

#: Staff cards and staff-facing text are rendered in one language, like the
#: notification review panels (``notifications/service.STAFF_PANEL_LOCALE``).
STAFF_PANEL_LOCALE = "en-US"

#: How many requests of one kind a user may open in ``SPAM_WINDOW_MINUTES``.
#: ``/bug-report`` is open to everyone: one frustrated user must not be able to
#: fill the team's channel, and a real bug survives a ten-minute wait.
SPAM_LIMIT = 3
SPAM_WINDOW_MINUTES = 10


class SupportRequestService:
    """Opens, routes, answers and closes support requests."""

    def __init__(self, bot):
        self.bot = bot

    @property
    def db(self):
        return getattr(self.bot, "db", None)

    # ------------------------------------------------------------------ #
    # Reading
    # ------------------------------------------------------------------ #

    async def get(self, request_id: Any) -> Optional[Dict[str, Any]]:
        if not self.db:
            return None
        try:
            return await self.db.get_support_request(request_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to load support request %s: %s", request_id, exc)
            return None

    async def messages(self, request_id: Any) -> List[Dict[str, Any]]:
        if not self.db:
            return []
        try:
            return await self.db.get_support_request_messages(request_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to load support request messages %s: %s", request_id, exc)
            return []

    async def is_rate_limited(self, user_id: int, kind: str) -> bool:
        if not self.db:
            return False
        try:
            recent = await self.db.count_recent_support_requests(
                user_id, kind, SPAM_WINDOW_MINUTES)
        except Exception as exc:  # noqa: BLE001 — never block a report on the DB
            logger.error("Failed to count recent support requests: %s", exc)
            return False
        return recent >= SPAM_LIMIT

    # ------------------------------------------------------------------ #
    # Opening
    # ------------------------------------------------------------------ #

    async def open_request(
        self, *, kind: str, user: discord.abc.User, guild: Optional[discord.Guild] = None,
        guild_name: Optional[str] = None, locale: Optional[str] = None,
        subject: Optional[str] = None, body: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Record a request and post its staff card. ``None`` if it could not
        be recorded — the caller then tells the user to use the support
        server, rather than pretending the report went somewhere."""
        if not self.db:
            return None
        try:
            request = await self.db.create_support_request(
                kind=kind, user_id=user.id,
                guild_id=guild.id if guild else None,
                guild_name=guild.name if guild else guild_name,
                locale=locale, subject=subject, body=body, details=details,
            )
        except Exception as exc:  # noqa: BLE001
            # The caller tells the user to use the support server instead, which
            # is the right message — but the failure behind it still needs an
            # error code, a Sentry capture and an internal log entry.
            from cogs.error_handler import report_error
            await report_error(
                self.bot, exc, source=f"SupportRequests:open:{kind}",
                user=user, guild=guild, error_type="Service Error",
            )
            return None

        if request:
            await self._post_card(request, user)
        return request

    def channel_id(self, kind: str) -> int:
        return (config.MODDY_BUG_REPORT_CHANNEL_ID if kind == KIND_BUG
                else config.MODDY_CONFIG_HELP_CHANNEL_ID)

    async def _post_card(self, request: Dict[str, Any],
                         user: Optional[discord.abc.User] = None) -> None:
        from utils.support_request_views import build_request_card

        channel = self.bot.get_channel(self.channel_id(request["kind"]))
        if channel is None:
            logger.warning("Support request channel unavailable — %s not posted",
                           request["id"])
            return
        try:
            message = await channel.send(
                view=build_request_card(request=request, messages=[], user=user,
                                        locale=STAFF_PANEL_LOCALE),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException as exc:
            logger.warning("Could not post support request card: %s", exc)
            return

        await self.db.set_support_request_card(request["id"], channel.id, message.id)
        request["channel_id"] = channel.id
        request["message_id"] = message.id

    async def refresh_card(self, request: Dict[str, Any]) -> None:
        """Re-render the stored card — never the interaction's own message,
        which may be an ephemeral follow-up."""
        from utils.support_request_views import build_request_card

        channel_id, message_id = request.get("channel_id"), request.get("message_id")
        if not channel_id or not message_id:
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            return
        user = self.bot.get_user(request["user_id"])
        try:
            message = await channel.fetch_message(message_id)
            await message.edit(view=build_request_card(
                request=request, messages=await self.messages(request["id"]),
                user=user, locale=STAFF_PANEL_LOCALE))
        except discord.HTTPException as exc:
            logger.warning("Could not refresh support request card: %s", exc)

    def jump_url(self, request: Dict[str, Any]) -> Optional[str]:
        if not request.get("channel_id") or not request.get("message_id"):
            return None
        return (f"https://discord.com/channels/{config.MODDY_TEAM_GUILD_ID}/"
                f"{request['channel_id']}/{request['message_id']}")

    # ------------------------------------------------------------------ #
    # Staff actions
    # ------------------------------------------------------------------ #

    async def claim(self, request_id: Any, staff: discord.abc.User) -> Optional[Dict[str, Any]]:
        if not self.db:
            return None
        request = await self.db.claim_support_request(request_id, staff.id)
        if request:
            await self.refresh_card(request)
        return request

    async def resolve(self, request_id: Any, staff: discord.abc.User) -> Optional[Dict[str, Any]]:
        if not self.db:
            return None
        request = await self.db.resolve_support_request(request_id, staff.id)
        if request:
            await self.refresh_card(request)
            await self._notify_resolved(request, staff)
        return request

    async def reply(self, *, request_id: Any, staff: discord.abc.User,
                    body: str) -> bool:
        """Send a staff reply to the reporter and keep it on the record."""
        request = await self.get(request_id)
        if not request:
            return False

        user = await self._fetch_user(request["user_id"])
        notification_id = None
        if user is not None:
            notification_id = await self._dm_reply(request, staff, body, user)

        try:
            await self.db.add_support_request_message(
                request_id=request["id"], author=AUTHOR_STAFF, author_id=staff.id,
                body=body, notification_id=notification_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to store staff reply: %s", exc)

        # A reply is also an implicit claim: whoever answers owns the request.
        # ``claim`` refreshes the card itself, so only the other branch has to.
        if not request.get("claimed_by") and await self.claim(request["id"], staff):
            return notification_id is not None

        await self.refresh_card(await self.get(request["id"]) or request)
        # A reply that reached nobody (deleted account, closed DMs) is stored
        # but not delivered, and the staffer has to be told which of the two
        # happened rather than shown a green panel.
        return notification_id is not None

    async def _dm_reply(self, request: Dict[str, Any], staff: discord.abc.User,
                        body: str, user: discord.abc.User) -> Optional[str]:
        from utils.support_request_views import build_reply_dm

        locale = request.get("locale") or "en-US"
        content = self._reply_content(request, locale)
        variables = {
            "body": body,
            "reference": str(request["id"]),
            "subject": request.get("subject") or "",
        }
        result = await self.bot.notifications.send_dm(
            user,
            content=content,
            # The words are the staffer's own, written on Moddy's behalf:
            # STAFF authorship keeps the DM out of the abuse-report flow (there
            # is nothing for the abuse team to judge about its own reply) while
            # still naming who is speaking.
            source=NotificationSource.service(
                "support", author=ContentAuthor.STAFF, actor_id=staff.id),
            variables=variables,
            view=build_reply_dm(request=request, body=body, locale=locale),
            locale=locale,
        )
        if result.forbidden:
            logger.info("Support reply could not be delivered (closed DMs) — %s",
                        request["id"])
        return result.notification_id

    def _reply_content(self, request: Dict[str, Any], locale: str) -> NotificationContent:
        """The uniform payload behind a reply — what the dashboard and the mail
        pipeline render when they show the same answer."""
        key = ("support.reply.bug" if request["kind"] == KIND_BUG
               else "support.reply.config_help")
        return NotificationContent(
            title=t(f"{key}.title", locale=locale),
            body="{body}",
            icon=emojis.SUPPORT,
            accent_color=0x3661FF,
            footer=t("support.reply.reference", locale=locale, reference="{reference}"),
            links=[{"label": t("support.links.support", locale=locale),
                    "url": config.SUPPORT_URL}],
            template_id=key,
        )

    async def _notify_resolved(self, request: Dict[str, Any],
                               staff: discord.abc.User) -> None:
        """Tell the reporter their request was closed — the loop the user sees."""
        user = await self._fetch_user(request["user_id"])
        if user is None:
            return
        locale = request.get("locale") or "en-US"
        content = NotificationContent(
            title=t("support.resolved.title", locale=locale),
            body=t(f"support.resolved.{request['kind']}", locale=locale),
            icon=emojis.DONE,
            accent_color=0x57F287,
            footer=t("support.reply.reference", locale=locale, reference="{reference}"),
            template_id="support.resolved",
        )
        await self.bot.notifications.send_dm(
            user, content=content,
            source=NotificationSource.service(
                "support", author=ContentAuthor.STAFF, actor_id=staff.id),
            variables={"reference": str(request["id"])},
            locale=locale,
        )

    # ------------------------------------------------------------------ #
    # Reporter follow-ups
    # ------------------------------------------------------------------ #

    async def user_followup(self, *, request_id: Any, user: discord.abc.User,
                            body: str) -> bool:
        """A reporter answering back from their DM. Lands on the staff card."""
        request = await self.get(request_id)
        if not request or request["user_id"] != user.id:
            return False

        try:
            await self.db.add_support_request_message(
                request_id=request["id"], author=AUTHOR_USER, author_id=user.id,
                body=body)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to store user follow-up: %s", exc)
            return False

        await self.refresh_card(request)
        await self._ping_card(request, user, body)
        return True

    async def _ping_card(self, request: Dict[str, Any], user: discord.abc.User,
                         body: str) -> None:
        """One short message under the card so a follow-up is actually seen —
        an edited card two hundred messages up is not a notification."""
        from utils.support_request_views import build_followup_notice

        channel = self.bot.get_channel(request.get("channel_id") or 0)
        if channel is None:
            return
        try:
            reference = None
            if request.get("message_id"):
                reference = discord.MessageReference(
                    message_id=request["message_id"],
                    channel_id=request["channel_id"],
                    fail_if_not_exists=False,
                )
            await channel.send(
                view=build_followup_notice(request=request, user=user, body=body,
                                           locale=STAFF_PANEL_LOCALE),
                reference=reference,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException as exc:
            logger.warning("Could not post support follow-up notice: %s", exc)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    async def _fetch_user(self, user_id: int) -> Optional[discord.abc.User]:
        user = self.bot.get_user(user_id)
        if user is not None:
            return user
        try:
            return await self.bot.fetch_user(user_id)
        except discord.HTTPException:
            return None


__all__ = [
    "SupportRequestService", "KIND_BUG", "KIND_CONFIG_HELP",
    "SPAM_LIMIT", "SPAM_WINDOW_MINUTES", "STAFF_PANEL_LOCALE",
]
