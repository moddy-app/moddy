"""Member Applications — the API calls and the review card lifecycle.

Everything that touches Discord's join requests or the review card goes through
here, whatever triggered it: a gateway event, the reconciliation poll, or a
moderator clicking a button on the card. Three entry points:

- :meth:`MemberApplicationService.ingest` — a join request as Discord sent it.
  A ``SUBMITTED`` one gets its card (once, whoever saw it first); an
  ``APPROVED`` / ``REJECTED`` one closes the card it belongs to.
- :meth:`MemberApplicationService.decide` — a moderator's decision from the
  card: calls the API, then records who decided.
- :meth:`MemberApplicationService.sync_guild` — the poll. Lists what Discord
  still has pending and reconciles both ways, because gateway delivery of join
  request events to bots is undocumented and must not be relied on alone.

The only two join request routes Discord's OpenAPI spec opens to bot tokens
are used here: ``GET /guilds/{id}/requests`` and ``PATCH
/guilds/{id}/requests/{request_id}``. Both need Kick Members. There is no
route to fetch one request by id, which is why the card is always rebuilt from
the snapshot stored in ``member_applications.request``.

See docs/MEMBER_APPLICATIONS.md.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import discord

from db.repositories.member_applications import (
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_SUBMITTED,
    STATUS_WITHDRAWN,
)
from modules.member_applications import MODULE_ID, MAX_REJECTION_REASON_LENGTH

logger = logging.getLogger("moddy.services.member_applications")

PAGE_SIZE = 100
# Upper bound on pages read per list call. 500 pending applications on one
# server is already far beyond anything a review channel can absorb; the cap
# keeps one broken pagination from turning a poll into a request storm.
MAX_PAGES = 5

# Why a decision from the card did not go through.
ERR_NOT_FOUND = "not_found"          # no such application for this server
ERR_ALREADY = "already_decided"      # someone else decided first
ERR_FORBIDDEN = "forbidden"          # Moddy lost Kick Members
ERR_GONE = "gone"                    # Discord no longer has the request
ERR_DISCORD = "discord_error"        # anything else Discord refused


# --------------------------------------------------------------------------- #
# Raw API
# --------------------------------------------------------------------------- #
async def list_join_requests(bot, guild_id: int, status: str = STATUS_SUBMITTED, *,
                             after: Optional[int] = None,
                             max_pages: int = MAX_PAGES) -> Tuple[Optional[List[Dict[str, Any]]], bool]:
    """``GET /guilds/{guild_id}/requests`` — every request with ``status``.

    Returns ``(requests, complete)``. ``requests`` is ``None`` when Discord's
    answer does not carry the list at all — developers have reported
    ``{"total": 1}`` with no ``guild_join_requests`` key — which the caller
    must read as "unknown", never as "empty": treating it as empty would close
    every pending card. ``complete`` is False when ``max_pages`` stopped the
    walk before the end.
    """
    collected: List[Dict[str, Any]] = []
    cursor = after
    for _ in range(max_pages):
        params: Dict[str, Any] = {"status": status, "limit": PAGE_SIZE}
        if cursor is not None:
            params["after"] = cursor
        route = discord.http.Route("GET", "/guilds/{guild_id}/requests", guild_id=guild_id)
        data = await bot.http.request(route, params=params)
        page = data.get("guild_join_requests") if isinstance(data, dict) else None
        if not isinstance(page, list):
            return None, False
        collected.extend(r for r in page if isinstance(r, dict) and r.get("id"))
        if len(page) < PAGE_SIZE:
            return collected, True
        cursor = max(int(r["id"]) for r in page if r.get("id"))
    return collected, False


async def action_join_request(bot, guild_id: int, request_id: int, action: str,
                              rejection_reason: Optional[str] = None) -> Dict[str, Any]:
    """``PATCH /guilds/{guild_id}/requests/{request_id}`` — approve or reject."""
    payload: Dict[str, Any] = {"action": action}
    if action == STATUS_REJECTED and rejection_reason:
        payload["rejection_reason"] = rejection_reason[:MAX_REJECTION_REASON_LENGTH]
    route = discord.http.Route(
        "PATCH", "/guilds/{guild_id}/requests/{request_id}",
        guild_id=guild_id, request_id=request_id,
    )
    return await bot.http.request(route, json=payload)


def _parse_time(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _user_id_of(request: Dict[str, Any]) -> Optional[int]:
    raw = request.get("user_id") or (request.get("user") or {}).get("id")
    return int(raw) if str(raw or "").isdigit() else None


def _reviewer_id_of(request: Dict[str, Any]) -> Optional[int]:
    raw = (request.get("actioned_by_user") or {}).get("id")
    return int(raw) if str(raw or "").isdigit() else None


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #
class MemberApplicationService:
    """Card lifecycle for membership applications, shared by every trigger."""

    def __init__(self, bot):
        self.bot = bot

    async def _module(self, guild_id: int):
        manager = getattr(self.bot, "module_manager", None)
        if manager is None:
            return None
        module = await manager.get_module_instance(guild_id, MODULE_ID)
        return module if module is not None and module.enabled else None

    # ------------------------------------------------------------- ingest
    async def ingest(self, guild: discord.Guild, request: Dict[str, Any]) -> None:
        """Handle one join request, from the gateway or the poll."""
        module = await self._module(guild.id)
        if module is None:
            return

        try:
            request_id = int(request["id"])
        except (KeyError, TypeError, ValueError):
            return
        status = request.get("application_status")

        if status == STATUS_SUBMITTED:
            await self._on_submitted(guild, module, request_id, request)
        elif status in (STATUS_APPROVED, STATUS_REJECTED):
            await self._on_decided(guild, request_id, status, request)
        # STARTED never reaches moderators; anything else is ignored.

    async def _on_submitted(self, guild: discord.Guild, module, request_id: int,
                            request: Dict[str, Any]) -> None:
        user_id = _user_id_of(request)
        if user_id is None:
            return

        row = await self.bot.db.claim_member_application(
            request_id, guild.id, user_id, request,
            _parse_time(request.get("created_at")),
        )
        if row is None:
            # Known already. Post only if an earlier attempt failed to.
            row = await self.bot.db.get_member_application(request_id)
            if row is None or row["status"] != STATUS_SUBMITTED or row["message_id"]:
                return

        await self.post_card(guild, module, row)

    async def _on_decided(self, guild: discord.Guild, request_id: int, status: str,
                          request: Dict[str, Any]) -> None:
        reviewer_id = _reviewer_id_of(request)
        # A decision Moddy made itself comes back as an event naming the bot:
        # decide() records the moderator who clicked, and must not be
        # overwritten by the bot's own id.
        if reviewer_id is not None and self.bot.user and reviewer_id == self.bot.user.id:
            return

        row = await self.bot.db.resolve_member_application(
            request_id, status,
            reviewed_by=reviewer_id,
            rejection_reason=request.get("rejection_reason"),
            decided_in="discord",
        )
        if row is not None:
            await self.refresh_card(guild, row)
            self._count(guild.id, f"{status.lower()}_in_discord")

    async def withdraw(self, guild: discord.Guild, request_id: int) -> None:
        """Discord deleted the request — the applicant took it back."""
        row = await self.bot.db.resolve_member_application(
            request_id, STATUS_WITHDRAWN,
            reviewed_by=None, rejection_reason=None, decided_in="discord",
        )
        if row is not None:
            await self.refresh_card(guild, row)

    # ------------------------------------------------------------- decide
    async def decide(self, guild: discord.Guild, request_id: int, action: str, *,
                     reviewer_id: int,
                     rejection_reason: Optional[str] = None) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """Approve or reject from the card. Returns ``(error, row)``.

        ``error`` is ``None`` on success. On ``ERR_ALREADY`` the row is the
        decision somebody else made, so the caller can show it.
        """
        row = await self.bot.db.get_member_application(request_id)
        if row is None or row["guild_id"] != guild.id:
            return ERR_NOT_FOUND, None
        if row["status"] != STATUS_SUBMITTED:
            return ERR_ALREADY, row

        reason = (rejection_reason or "").strip()[:MAX_REJECTION_REASON_LENGTH] or None
        try:
            await action_join_request(self.bot, guild.id, request_id, action, reason)
        except discord.Forbidden:
            return ERR_FORBIDDEN, row
        except discord.NotFound:
            # The applicant withdrew between the card and the click.
            gone = await self.bot.db.resolve_member_application(
                request_id, STATUS_WITHDRAWN,
                reviewed_by=None, rejection_reason=None, decided_in="discord",
            )
            return ERR_GONE, gone or row
        except discord.HTTPException as e:
            logger.warning(
                f"Join request {request_id} in guild {guild.id} refused by Discord "
                f"({e.status} {e.code}): {e.text}")
            return ERR_DISCORD, row

        updated = await self.bot.db.resolve_member_application(
            request_id, action,
            reviewed_by=reviewer_id,
            rejection_reason=reason if action == STATUS_REJECTED else None,
            decided_in="moddy",
        )
        if updated is None:
            # Decided elsewhere in the same instant; Discord accepted ours too,
            # but the first one recorded is the one the card shows.
            return ERR_ALREADY, await self.bot.db.get_member_application(request_id)

        self._count(guild.id, action.lower())
        return None, updated

    # ------------------------------------------------------------- poll
    async def sync_guild(self, guild: discord.Guild) -> None:
        """Reconcile one server with what Discord actually has pending."""
        if await self._module(guild.id) is None:
            return

        submitted, _ = await list_join_requests(self.bot, guild.id, STATUS_SUBMITTED)
        if submitted is None:
            logger.warning(
                f"Join request list for guild {guild.id} came back without its "
                "list — skipping this pass")
            return

        for request in submitted:
            await self.ingest(guild, request)

        still_pending = {int(r["id"]) for r in submitted}
        orphans = [row for row in await self.bot.db.list_pending_member_applications(guild.id)
                   if row["request_id"] not in still_pending]
        if not orphans:
            return

        # Decided (or withdrawn) without Moddy seeing an event. Find out which.
        after = min(row["request_id"] for row in orphans) - 1
        found: Dict[int, Dict[str, Any]] = {}
        complete = True
        for status in (STATUS_APPROVED, STATUS_REJECTED):
            requests, done = await list_join_requests(self.bot, guild.id, status, after=after)
            if requests is None:
                return
            complete = complete and done
            for request in requests:
                found[int(request["id"])] = request

        for row in orphans:
            request = found.get(row["request_id"])
            if request is not None:
                await self.ingest(guild, request)
            elif complete:
                # In neither list, and both lists were read to the end: Discord
                # no longer has it at all.
                await self.withdraw(guild, row["request_id"])

    # ------------------------------------------------------------- cards
    async def post_card(self, guild: discord.Guild, module, row: Dict[str, Any]) -> None:
        from notifications.models import NotificationContent, NotificationSource
        from utils.emojis import SHAPES
        from utils.guild_language import guild_locale
        from utils.member_application_views import build_card

        channel = guild.get_channel(module.channel_id) if module.channel_id else None
        if not isinstance(channel, discord.TextChannel):
            logger.warning(f"Member applications channel gone in guild {guild.id}")
            return
        perms = channel.permissions_for(guild.me)
        if not perms.view_channel or not perms.send_messages:
            logger.warning(
                f"Cannot post application card in guild {guild.id} channel "
                f"{channel.id} — missing permissions")
            return

        locale = await guild_locale(self.bot, guild)
        roles = [r for r in (guild.get_role(rid) for rid in module.ping_role_ids) if r]
        view = await build_card(self.bot, guild, row, locale=locale,
                                mention_role_ids=[r.id for r in roles])

        result = await self.bot.notifications.send_channel(
            channel,
            content=NotificationContent(
                title=guild.name,
                body="New membership application from {user} on {server}.",
                icon=SHAPES,
                template_id="member_applications.card",
            ),
            source=NotificationSource.service_guild(MODULE_ID, guild.id),
            guild_id=guild.id,
            variables={"user": f"<@{row['user_id']}>", "server": guild.name},
            view=view,
            allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=roles),
            locale=locale,
            attribution=False,
        )
        if not result.delivered or result.message is None:
            logger.warning(
                f"Application card not posted in guild {guild.id}: {result.error}")
            return

        await self.bot.db.set_member_application_message(
            row["request_id"], channel.id, result.message.id)
        self._count(guild.id, "card_posted")

    async def refresh_card(self, guild: discord.Guild, row: Dict[str, Any]) -> None:
        """Re-render a card after its application changed outside a click."""
        if not row.get("channel_id") or not row.get("message_id"):
            return
        channel = guild.get_channel(row["channel_id"])
        if channel is None:
            return

        from utils.guild_language import guild_locale
        from utils.member_application_views import build_card

        locale = await guild_locale(self.bot, guild)
        view = await build_card(self.bot, guild, row, locale=locale)
        try:
            await channel.get_partial_message(row["message_id"]).edit(
                view=view, allowed_mentions=discord.AllowedMentions.none())
        except discord.NotFound:
            pass
        except discord.HTTPException as e:
            logger.warning(
                f"Could not refresh application card {row['request_id']} "
                f"in guild {guild.id}: {e}")

    def _count(self, guild_id: int, action: str) -> None:
        stats = getattr(self.bot, "stats", None)
        if stats is None:
            return
        stats.incr("module.action", guild_id=guild_id,
                   dims={"module": MODULE_ID, "action": action})


def get_service(bot) -> MemberApplicationService:
    """The bot's single service instance, created on first use."""
    service = getattr(bot, "_member_application_service", None)
    if service is None:
        service = MemberApplicationService(bot)
        bot._member_application_service = service
    return service


def requests_from_gateway(kind: str, data: Dict[str, Any]) -> Sequence[Dict[str, Any]]:
    """The join request carried by a GUILD_JOIN_REQUEST_* payload, if any."""
    request = data.get("request") if isinstance(data, dict) else None
    if not isinstance(request, dict):
        return []
    # The event carries the status next to the request; trust it over a
    # partial request object that might lack `application_status`.
    if data.get("status") and not request.get("application_status"):
        request = {**request, "application_status": data["status"]}
    return [request]
