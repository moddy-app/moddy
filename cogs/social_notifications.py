"""
Social Notifications cog.

Wires the Social Notifications module together:
  - owns the :class:`FeedsClient` (Redis transport to ``moddy-feeds``),
  - dispatches incoming notification events to every guild that follows a target,
  - centralises the subscribe / unsubscribe business logic enforced by the
    integration contract (subscribe on first follow, unsubscribe only when the
    last guild drops a target, relaxed poll interval otherwise),
  - cleans up a guild's subscriptions when the bot is removed from it.

The configuration UI (``modules/configs/social_notifications_config.py``) calls
back into this cog so the contract logic lives in exactly one place.
"""

import logging
from typing import Any, Dict, Optional, Tuple

import discord
from discord.ext import commands

from services.feeds_client import FeedsClient
from modules.social_notifications import (
    build_notification_view,
    desired_poll_interval,
    platform_subscription_limit,
    normalize_identifier,
)

logger = logging.getLogger('moddy.cogs.social_notifications')

# For realtime platforms the poll interval is ignored by the service, but we
# still must send *some* value on a partial unsubscribe so the target is kept
# alive (an omitted interval means "remove the target").
REALTIME_KEEP_INTERVAL = 60


class SocialNotifications(commands.Cog):
    """Owns the feeds client and dispatches social notifications."""

    def __init__(self, bot):
        self.bot = bot
        self.feeds = FeedsClient(bot)
        # Expose for the configuration views.
        bot.feeds_client = self.feeds

    async def cog_load(self):
        try:
            await self.feeds.start(self._dispatch_event)
        except Exception as e:
            logger.error(f"[Social] Failed to start feeds client: {e}", exc_info=True)

    async def cog_unload(self):
        await self.feeds.stop()

    # ------------------------------------------------------------------ #
    # Business logic (called by the config UI and guild events)
    # ------------------------------------------------------------------ #
    async def add_subscription(
        self,
        *,
        guild: discord.Guild,
        platform: str,
        identifier: str,
        channel_id: int,
        role_ids: Optional[list] = None,
        message: Optional[str] = None,
        embed_color: Optional[int] = None,
        show_avatar: bool = True,
        show_media: bool = True,
        created_by: Optional[int] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Resolve a target via the service then persist the subscription.

        Returns ``(ok, reply)`` where ``reply`` is the service response dict
        (on success it carries ``target_id``/``display_name``/``avatar_url``).
        """
        # Normalize a pasted profile URL into a clean handle (idempotent).
        identifier = normalize_identifier(platform, identifier)

        is_premium = False
        try:
            is_premium = await self.bot.db.is_guild_premium(guild.id)
        except Exception:
            pass

        # Per-platform quota (free vs premium). Measured BEFORE resolving so we
        # can short-circuit; the "update an already-followed target" case is
        # allowed below even when at the cap.
        limit = platform_subscription_limit(is_premium)
        existing_count = await self.bot.db.count_platform_subscriptions(guild.id, platform)

        poll = desired_poll_interval(platform, is_premium)
        reply = await self.feeds.subscribe(platform, identifier, poll)

        if not reply.get("ok"):
            return False, reply

        target_id = reply.get("target_id")
        if not target_id:
            return False, {"ok": False, "error": "internal_error"}

        # Enforce the quota. Re-adding a target the guild already follows is an
        # update (always allowed); a *new* target over the cap is rejected — and
        # we reconcile so the just-issued service subscription isn't orphaned.
        if existing_count >= limit:
            already = await self.bot.db.get_social_subscription(guild.id, platform, target_id)
            if not already:
                await self._reconcile_target(platform, target_id)
                code = "limit_reached_premium" if is_premium else "limit_reached_free"
                return False, {"ok": False, "error": code, "limit": limit}

        await self.bot.db.add_social_subscription(
            guild_id=guild.id,
            platform=platform,
            target_id=target_id,
            channel_id=channel_id,
            identifier=identifier,
            display_name=reply.get("display_name"),
            avatar_url=reply.get("avatar_url"),
            message=message,
            embed_color=embed_color,
            show_avatar=show_avatar,
            show_media=show_media,
            mention_role_ids=role_ids or [],
            poll_interval=poll,
            created_by=created_by,
        )
        return True, reply

    async def remove_subscription(self, guild_id: int, platform: str, target_id: str) -> None:
        """Delete a guild's subscription and reconcile with the service."""
        await self.bot.db.remove_social_subscription(guild_id, platform, target_id)
        await self._reconcile_target(platform, target_id)

    async def _reconcile_target(self, platform: str, target_id: str) -> None:
        """Tell the service whether a target should be kept or removed.

        Must be called AFTER the guild's row has been deleted from the DB.
        """
        remaining = await self.bot.db.count_target_guilds(platform, target_id)
        if remaining == 0:
            # No guild left -> fully remove the target.
            await self.feeds.unsubscribe(platform, target_id)
            return

        # Other guilds still follow it: keep it alive with the most demanding
        # (smallest) remaining interval. We must ALWAYS pass a non-None interval
        # here — omitting it would tell the service to remove the target. For
        # realtime platforms (no stored interval) the value is just a sentinel
        # the service ignores.
        min_interval = await self.bot.db.get_target_min_poll_interval(platform, target_id)
        if min_interval is None:
            min_interval = REALTIME_KEEP_INTERVAL
        await self.feeds.unsubscribe(platform, target_id, poll_interval=min_interval)

    async def handle_backend_task(self, action: str, guild_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a subscription action requested by the backend (moddy:tasks).

        Centralises the contract so the backend never has to duplicate the
        subscribe/DB logic — it just pushes a task. Returns a result dict that
        the bot relays on `moddy:dashboard` (correlated by ``request_id``).

        Supported actions / payload fields:
          - ``subscribe``:   platform, identifier, channel_id, role_ids?, message?, created_by?
          - ``unsubscribe`` / ``remove``: platform, target_id
          - ``update``:      platform, target_id, + any of channel_id, message,
                             mention_role_ids, enabled (DB-only)
        """
        platform = payload.get("platform")

        if action == "subscribe":
            guild = self.bot.get_guild(guild_id)
            if not guild:
                return {"ok": False, "error": "guild_not_found"}
            if not platform or not payload.get("identifier") or not payload.get("channel_id"):
                return {"ok": False, "error": "missing_fields"}
            ok, reply = await self.add_subscription(
                guild=guild,
                platform=platform,
                identifier=str(payload["identifier"]),
                channel_id=int(payload["channel_id"]),
                role_ids=[int(r) for r in payload.get("role_ids", [])],
                message=payload.get("message"),
                embed_color=payload.get("embed_color"),
                show_avatar=bool(payload.get("show_avatar", True)),
                show_media=bool(payload.get("show_media", True)),
                created_by=payload.get("created_by"),
            )
            return {
                "ok": ok,
                "platform": platform,
                "target_id": reply.get("target_id"),
                "display_name": reply.get("display_name"),
                "avatar_url": reply.get("avatar_url"),
                "error": reply.get("error"),
            }

        if action in ("unsubscribe", "remove"):
            target_id = payload.get("target_id")
            if not platform or not target_id:
                return {"ok": False, "error": "missing_fields"}
            await self.remove_subscription(guild_id, platform, str(target_id))
            return {"ok": True, "removed": True, "platform": platform, "target_id": target_id}

        if action == "update":
            target_id = payload.get("target_id")
            if not platform or not target_id:
                return {"ok": False, "error": "missing_fields"}
            fields: Dict[str, Any] = {}
            if "channel_id" in payload and payload["channel_id"] is not None:
                fields["channel_id"] = int(payload["channel_id"])
            if "message" in payload:
                fields["message"] = payload["message"]
            if "embed_color" in payload:
                fields["embed_color"] = payload["embed_color"]
            if "show_avatar" in payload:
                fields["show_avatar"] = bool(payload["show_avatar"])
            if "show_media" in payload:
                fields["show_media"] = bool(payload["show_media"])
            if "mention_role_ids" in payload:
                fields["mention_role_ids"] = [int(r) for r in payload["mention_role_ids"]]
            if "enabled" in payload:
                fields["enabled"] = bool(payload["enabled"])
            if not fields:
                return {"ok": False, "error": "missing_fields"}
            ok = await self.bot.db.update_social_subscription(guild_id, platform, str(target_id), **fields)
            return {"ok": ok, "platform": platform, "target_id": target_id}

        return {"ok": False, "error": "unknown_action"}

    # ------------------------------------------------------------------ #
    # Dispatch
    # ------------------------------------------------------------------ #
    async def _dispatch_event(self, event: Dict[str, Any]) -> None:
        """Send a notification event to every guild that follows the target."""
        platform = event.get("platform")
        target_id = event.get("target_id")
        if not platform or not target_id:
            return

        followers = await self.bot.db.get_target_followers(platform, target_id)
        if not followers:
            return

        for sub in followers:
            guild = self.bot.get_guild(sub["guild_id"])
            if not guild:
                continue
            channel = guild.get_channel(sub["channel_id"])
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                continue

            locale = str(guild.preferred_locale) if guild.preferred_locale else 'en-US'
            try:
                view, allowed = build_notification_view(event, sub, locale)
                await channel.send(view=view, allowed_mentions=allowed)
            except discord.Forbidden:
                logger.warning(
                    f"[Social] Missing permissions to post in channel {sub['channel_id']} "
                    f"(guild {sub['guild_id']})"
                )
            except Exception as e:
                logger.error(
                    f"[Social] Failed to dispatch event to guild {sub['guild_id']}: {e}",
                    exc_info=True,
                )

    # ------------------------------------------------------------------ #
    # Guild cleanup
    # ------------------------------------------------------------------ #
    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        """Drop a guild's subscriptions and reconcile each affected target."""
        try:
            pairs = await self.bot.db.delete_guild_social_subscriptions(guild.id)
            for platform, target_id in pairs:
                await self._reconcile_target(platform, target_id)
            if pairs:
                logger.info(f"[Social] Cleaned up {len(pairs)} subscriptions for guild {guild.id}")
        except Exception as e:
            logger.error(f"[Social] Error cleaning up guild {guild.id}: {e}", exc_info=True)


async def setup(bot):
    await bot.add_cog(SocialNotifications(bot))
