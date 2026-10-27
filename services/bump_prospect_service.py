"""
Bump prospects — find servers that bump by hand and hand them to the team.

A server listed on a bump directory (DISBOARD, DiscordL, French.gg…) with no
reminder at all — no third-party reminder bot, and Moddy's own Bump Reminder
module not running — misses bump windows. That is the exact problem Moddy's
module solves, so such a server gets a card in the team channel
(``config.MODDY_BUMP_PROSPECT_CHANNEL_ID``) with an invite, Claim and Close.

How a server gets looked at:

* **a scan queue**, worked by one task at :data:`SCAN_DELAY` seconds per
  server. Each look is one gateway member query for the handful of watched ids
  (:data:`bumpreminder.prospects.WATCHED_IDS`) — cheap, but it shares the
  shard's gateway budget with everything else, hence the pace;
* fed at startup with every server that has no row yet, by
  ``/dev bumpscan`` with every server, and live by the cog when Moddy joins a
  server or a directory / reminder bot joins or leaves one.

Rows that are still open are followed up every :data:`FOLLOW_UP_MINUTES` by the
cog: Moddy's module enabled since → ``converted``; Moddy removed → ``left``.

``bot.bump_prospects`` is the instance. See docs/features/BUMP_PROSPECTS.md.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Deque, Dict, Iterable, Optional, Set

import discord

import config
from bumpreminder.prospects import WATCHED_IDS, prospect_directories
from db.repositories.bump_prospects import (
    ACTIVE_STATUSES, STATUS_CONVERTED, STATUS_LEFT,
)
from utils.invites import create_guild_invite

logger = logging.getLogger("moddy.bump_prospects")

#: Staff cards are rendered in one language, like support requests.
STAFF_PANEL_LOCALE = "en-US"

#: Seconds between two servers in the scan queue. One gateway request each;
#: a shard allows 120 per minute for *everything*, so this keeps us well under.
SCAN_DELAY = 1.0

#: Gateway member query timeout, per server.
QUERY_TIMEOUT = 15.0

#: Minutes between two follow-ups of the still-open cards.
FOLLOW_UP_MINUTES = 30

#: The module whose presence settles a prospect.
BUMP_MODULE_ID = "bump_reminder"

#: Module states in which Moddy already reminds the server.
_RUNNING_STATES = ("active", "simulation")

# Outcomes of one scan, also the keys of a manual run's summary.
OUTCOME_SKIPPED = "skipped"      # unavailable, the team guild, lookup failed
OUTCOME_NONE = "none"            # no directory, or already reminded
OUTCOME_KNOWN = "known"          # a row already exists
OUTCOME_NEW = "new"              # a card was just posted
OUTCOME_CONVERTED = "converted"  # an open card settled by the module


class BumpProspectService:
    """Scans servers, keeps one card per prospect, carries the staff actions."""

    def __init__(self, bot):
        self.bot = bot
        self._queue: Deque[int] = deque()
        self._queued: Set[int] = set()
        self._wakeup = asyncio.Event()
        self._worker: Optional[asyncio.Task] = None
        # Counters of the manual scan in progress, ``None`` when there is none.
        self._run: Optional[Dict[str, Any]] = None

    @property
    def db(self):
        return getattr(self.bot, "db", None)

    @property
    def enabled(self) -> bool:
        return bool(config.MODDY_BUMP_PROSPECT_CHANNEL_ID)

    # ------------------------------------------------------------------ #
    # Queue
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._work(), name="bump-prospects")

    def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._worker = None

    @property
    def pending(self) -> int:
        return len(self._queue)

    @property
    def manual_run_active(self) -> bool:
        return self._run is not None

    def enqueue(self, guild_ids: Iterable[int], *, priority: bool = False) -> int:
        """Queue servers for a look; returns how many were actually added.

        ``priority`` puts them first: a live event (Moddy just joined, a
        directory was just added) should not wait behind a full rescan.
        """
        added = 0
        for guild_id in guild_ids:
            if guild_id in self._queued:
                continue
            self._queued.add(guild_id)
            if priority:
                self._queue.appendleft(guild_id)
            else:
                self._queue.append(guild_id)
            added += 1
        if added:
            self._wakeup.set()
        return added

    def scan_all(self, *, requested_by: Optional[int] = None) -> int:
        """Queue every server Moddy is in and report the outcome when done."""
        self._run = {
            "requested_by": requested_by,
            "started": time.monotonic(),
            "scanned": 0,
            OUTCOME_NEW: 0,
            OUTCOME_CONVERTED: 0,
            OUTCOME_SKIPPED: 0,
        }
        guild_ids = [guild.id for guild in self.bot.guilds]
        self.enqueue(guild_ids)
        return len(guild_ids)

    async def enqueue_unknown(self) -> int:
        """Startup pass: every server that has no row yet."""
        if not self.db:
            return 0
        try:
            known = await self.db.known_bump_prospect_guilds()
        except Exception as exc:  # noqa: BLE001 — a DB hiccup must not kill startup
            logger.warning("Could not read known bump prospects: %s", exc)
            return 0
        return self.enqueue(g.id for g in self.bot.guilds if g.id not in known)

    async def _work(self) -> None:
        await self.bot.wait_until_ready()
        while True:
            if not self._queue:
                if self._run is not None:
                    await self._finish_run()
                self._wakeup.clear()
                await self._wakeup.wait()
                continue
            guild_id = self._queue.popleft()
            self._queued.discard(guild_id)
            guild = self.bot.get_guild(guild_id)
            try:
                outcome = await self.scan_guild(guild) if guild else OUTCOME_SKIPPED
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — one server never stops the queue
                logger.exception("Bump prospect scan failed for guild %s", guild_id)
                outcome = OUTCOME_SKIPPED
            if self._run is not None:
                self._run["scanned"] += 1
                if outcome in self._run:
                    self._run[outcome] += 1
            await asyncio.sleep(SCAN_DELAY)

    # ------------------------------------------------------------------ #
    # One server
    # ------------------------------------------------------------------ #

    async def scan_guild(self, guild: discord.Guild) -> str:
        """Look at one server and post its card if it is a new prospect."""
        if not self.enabled or not self.db:
            return OUTCOME_SKIPPED
        if guild.unavailable or guild.id == config.MODDY_TEAM_GUILD_ID:
            return OUTCOME_SKIPPED

        present = await self._present_ids(guild)
        if present is None:
            return OUTCOME_SKIPPED
        directories = prospect_directories(present)

        existing = await self.db.get_bump_prospect(guild.id)
        if existing is not None:
            if existing["status"] in ACTIVE_STATUSES and await self.module_running(guild.id):
                await self.settle(guild.id, STATUS_CONVERTED)
                return OUTCOME_CONVERTED
            return OUTCOME_KNOWN

        if not directories or await self.module_running(guild.id):
            return OUTCOME_NONE

        prospect = await self.db.create_bump_prospect(
            guild.id, guild.name, [spec.key for spec in directories])
        if prospect is None:  # a concurrent scan won the insert
            return OUTCOME_KNOWN
        await self._post_card(prospect)
        logger.info("Bump prospect: %s (%s) — %s", guild.name, guild.id,
                    ", ".join(prospect["directories"]))
        return OUTCOME_NEW

    async def _present_ids(self, guild: discord.Guild) -> Optional[Set[int]]:
        """Which watched ids are members of ``guild``; ``None`` if unknown."""
        if guild.chunked:
            return {uid for uid in WATCHED_IDS if guild.get_member(uid) is not None}
        try:
            members = await asyncio.wait_for(guild.query_members(
                user_ids=list(WATCHED_IDS), limit=len(WATCHED_IDS), cache=False,
            ), timeout=QUERY_TIMEOUT)
        except (asyncio.TimeoutError, discord.ClientException, discord.HTTPException) as exc:
            logger.debug("Member query failed for guild %s: %s", guild.id, exc)
            return None
        return {member.id for member in members}

    async def module_running(self, guild_id: int) -> bool:
        manager = getattr(self.bot, "module_manager", None)
        if manager is None:
            return False
        try:
            state = await manager.get_module_state(guild_id, BUMP_MODULE_ID)
        except Exception as exc:  # noqa: BLE001 — unknown counts as "not running"
            logger.debug("Module state unreadable for guild %s: %s", guild_id, exc)
            return False
        return state in _RUNNING_STATES

    # ------------------------------------------------------------------ #
    # Follow-up of open cards
    # ------------------------------------------------------------------ #

    async def follow_up(self) -> None:
        """Settle open cards whose server enabled the module or dropped Moddy."""
        if not self.db:
            return
        for prospect in await self.db.list_active_bump_prospects():
            guild_id = prospect["guild_id"]
            if self.bot.get_guild(guild_id) is None:
                await self.settle(guild_id, STATUS_LEFT)
            elif await self.module_running(guild_id):
                await self.settle(guild_id, STATUS_CONVERTED)

    async def settle(self, guild_id: int, status: str) -> Optional[Dict[str, Any]]:
        if not self.db:
            return None
        prospect = await self.db.settle_bump_prospect(guild_id, status)
        if prospect:
            await self.refresh_card(prospect)
        return prospect

    # ------------------------------------------------------------------ #
    # Staff actions — each returns the updated row, or None if refused
    # ------------------------------------------------------------------ #

    async def get(self, guild_id: int) -> Optional[Dict[str, Any]]:
        return await self.db.get_bump_prospect(guild_id) if self.db else None

    async def claim(self, guild_id: int, staff: discord.abc.User) -> Optional[Dict[str, Any]]:
        return await self._act(self.db.claim_bump_prospect(guild_id, staff.id))

    async def unclaim(self, guild_id: int, staff: discord.abc.User) -> Optional[Dict[str, Any]]:
        return await self._act(self.db.unclaim_bump_prospect(guild_id, staff.id))

    async def close(self, guild_id: int, staff: discord.abc.User) -> Optional[Dict[str, Any]]:
        return await self._act(self.db.close_bump_prospect(guild_id, staff.id))

    async def reopen(self, guild_id: int) -> Optional[Dict[str, Any]]:
        return await self._act(self.db.reopen_bump_prospect(guild_id))

    async def _act(self, query) -> Optional[Dict[str, Any]]:
        prospect = await query
        if prospect:
            await self.refresh_card(prospect)
        return prospect

    async def invite(self, guild_id: int, staff: discord.abc.User) -> Optional[str]:
        """A single-use, 24 h invite for a staffer — created on demand only,
        so a server the team never looks at never sees an invite in its logs."""
        return await create_guild_invite(
            self.bot.get_guild(guild_id),
            reason=f"Moddy team follow-up (requested by {staff.id})",
        )

    # ------------------------------------------------------------------ #
    # Cards
    # ------------------------------------------------------------------ #

    def _channel(self):
        return self.bot.get_channel(config.MODDY_BUMP_PROSPECT_CHANNEL_ID)

    def _render(self, prospect: Dict[str, Any]):
        from utils.bump_prospect_views import build_prospect_card
        return build_prospect_card(
            prospect=prospect, guild=self.bot.get_guild(prospect["guild_id"]),
            locale=STAFF_PANEL_LOCALE)

    async def _post_card(self, prospect: Dict[str, Any]) -> None:
        channel = self._channel()
        if channel is None:
            logger.warning("Bump prospect channel unavailable — %s not posted",
                           prospect["guild_id"])
            return
        try:
            message = await channel.send(
                view=self._render(prospect),
                allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as exc:
            logger.warning("Could not post bump prospect card: %s", exc)
            return
        await self.db.set_bump_prospect_card(prospect["guild_id"], channel.id, message.id)

    async def refresh_card(self, prospect: Dict[str, Any]) -> None:
        channel = self.bot.get_channel(prospect.get("channel_id") or 0)
        if channel is None or not prospect.get("message_id"):
            return
        try:
            message = await channel.fetch_message(prospect["message_id"])
            await message.edit(view=self._render(prospect),
                               allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as exc:
            logger.warning("Could not refresh bump prospect card: %s", exc)

    async def _finish_run(self) -> None:
        run, self._run = self._run, None
        channel = self._channel()
        if channel is None or run is None:
            return
        from utils.bump_prospect_views import build_scan_summary
        try:
            await channel.send(
                view=build_scan_summary(
                    run, duration=int(time.monotonic() - run["started"]),
                    locale=STAFF_PANEL_LOCALE),
                allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as exc:
            logger.warning("Could not post bump prospect scan summary: %s", exc)

    def eta_seconds(self) -> int:
        return int(self.pending * SCAN_DELAY)

