"""
Global sanction service — the Moddy-team sanction workflow, end to end.

A global sanction is never a lone case. One breach of the Moddy rules usually
hits **several subjects at once** — the offending user *and* the server they
run — so the whole thing is modelled as a **case group**: one `group_id`, one
case per target, one notice, one countdown. This service owns that workflow:

1. **Apply** — open one `global` case per target, all sharing a fresh
   `group_id`, with the same reason and level.
2. **Notify** — send the subject **one** DM for the whole group (never one per
   case), listing every case reference and the deferred consequences.
3. **Schedule** — some consequences are deferred by a grace period so the
   subject can appeal (see ``db/repositories/enforcements.py``):
   a premium subscription is cancelled without refund, and Moddy leaves the
   suspended servers and lets their data be dropped.
4. **Halt** — a staff member stops the countdown the moment an appeal comes in.
5. **Execute** — when a deadline passes unappealed, the bot leaves the
   sanctioned servers and tells the backend to run the billing side.

Every step publishes on ``moddy:sanctions`` so the backend can react (billing,
data retention, dashboards). See ``docs/GLOBAL_SANCTIONS.md``.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

import discord

from utils import global_sanctions as gs
from utils.global_sanctions import GlobalLevel
from utils.moderation_cases import SubjectType

logger = logging.getLogger('moddy.global_sanction_service')

#: Redis channel the bot publishes global-sanction events on (bot → backend).
SANCTION_CHANNEL = "moddy:sanctions"

#: Grace period, in hours, before the deferred consequences are executed.
#: The subject is told this deadline in their notice DM and can stop it by
#: appealing (a staff member then runs ``/mod global halt``).
ENFORCEMENT_GRACE_HOURS = 48

#: Public URLs quoted in the notice DM.
URL_VIOLATIONS = "https://moddy.app/violations"
URL_SUPPORT = "https://moddy.app/support"
URL_TERMS = "https://moddy.app/terms"

#: Levels whose consequences are deferred (and therefore need a countdown).
#: A plain warning changes nothing, so it never schedules one.
ENFORCED_LEVELS = (GlobalLevel.LIMITED, GlobalLevel.SUSPENDED)


class GlobalSanctionService:
    """Applies, lifts and enforces Moddy-team sanctions (``bot.global_sanctions``)."""

    def __init__(self, bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    # ------------------------------------------------------------------ apply

    async def apply(
        self,
        *,
        level: GlobalLevel,
        reason: str,
        issuer_id: int,
        user_id: Optional[int] = None,
        guild_ids: Sequence[int] = (),
        expires_at: Optional[datetime] = None,
        note: Optional[str] = None,
        grace_hours: int = ENFORCEMENT_GRACE_HOURS,
        notify: bool = True,
    ) -> Dict[str, Any]:
        """Sanction a user and/or servers as **one grouped infraction**.

        Returns ``{"group_id", "cases": [...], "enforcement": dict|None,
        "notified": bool}``. ``cases`` items carry ``subject_type``,
        ``subject_id``, ``reference`` and ``id``.
        """
        if not self.db:
            raise RuntimeError("database unavailable")
        if user_id is None and not guild_ids:
            raise ValueError("a global sanction needs at least one target")

        action = gs.LEVEL_TO_ACTION[level]
        group_id = uuid.uuid4()
        cases: List[Dict[str, Any]] = []

        targets: List[tuple] = []
        if user_id is not None:
            targets.append((SubjectType.DISCORD_USER, int(user_id)))
        for guild_id in guild_ids:
            targets.append((SubjectType.DISCORD_GUILD, int(guild_id)))

        for subject_type, subject_id in targets:
            result = await self.db.create_case(
                case_type="global",
                subject_type=subject_type.value,
                subject_id=subject_id,
                issuer_type="moddy_staff",
                issuer_id=issuer_id,
                scope_type="platform",
                scope_id=None,
                reason=reason,
                action=action,
                sanction_expires_at=expires_at,
                sanction_note=note,
                group_id=group_id,
            )
            gs.invalidate(self.bot, subject_type.value, subject_id)
            cases.append({
                "id": str(result["id"]),
                "reference": result["reference"],
                "subject_type": subject_type.value,
                "subject_id": str(subject_id),
            })

        # Who hears about this and gets the chance to appeal: the sanctioned
        # user, or — when only servers were hit — the owner of the first
        # server we can resolve. Someone must always be told, otherwise the
        # grace period protects nobody.
        notice_user_id = user_id if user_id is not None else self._first_owner(guild_ids)

        # Deferred consequences — only for levels that actually carry any.
        enforcement = None
        premium = False
        if level in ENFORCED_LEVELS and grace_hours > 0:
            premium = (await self._is_premium(notice_user_id)
                       if notice_user_id is not None else False)
            deadline = datetime.now(timezone.utc) + timedelta(hours=grace_hours)
            if notice_user_id is not None:
                subject_type, subject_id = SubjectType.DISCORD_USER, notice_user_id
            else:
                # Nobody reachable — the countdown still has to run so Moddy
                # actually leaves the server it suspended.
                subject_type, subject_id = SubjectType.DISCORD_GUILD, int(guild_ids[0])
            enforcement = await self.db.create_enforcement(
                group_id=group_id,
                subject_type=subject_type.value,
                subject_id=subject_id,
                level=level.value,
                deadline=deadline,
                premium=premium,
            )

        notified = False
        if notify and notice_user_id is not None:
            notified = await self.notify_group(
                user_id=notice_user_id, level=level, reason=reason,
                cases=cases, enforcement=enforcement, premium=premium,
            )
            if notified and enforcement:
                await self.db.mark_enforcement_notified(group_id)

        await self._publish("global_sanction_applied", {
            "group_id": str(group_id),
            "level": level.value,
            "action": action,
            "reason": reason,
            "issuer_id": str(issuer_id),
            "expires_at": expires_at.isoformat() if expires_at else None,
            "premium": premium,
            "targets": cases,
            "enforcement": self._enforcement_payload(enforcement),
            "notified": notified,
        })

        await self._log_group_event(
            cases, "sanction_applied",
            f"Global sanction `{level.value}` applied to {len(cases)} target(s).",
        )

        return {
            "group_id": str(group_id),
            "cases": cases,
            "enforcement": enforcement,
            "notified": notified,
        }

    # ------------------------------------------------------------------- lift

    async def lift(
        self, group_id, *, by_id: int, reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """Revoke every active sanction of a group and drop its countdown."""
        if not self.db:
            raise RuntimeError("database unavailable")

        group_cases = await self.db.list_group_cases(group_id)
        revoked = 0
        for case in group_cases:
            for sanction in case.get("sanctions", []):
                if str(sanction.get("status")) != "active":
                    continue
                if await self.db.revoke_sanction(sanction["id"], "moddy_staff", by_id):
                    revoked += 1
            gs.invalidate(self.bot, str(case["subject_type"]), case["subject_id"])

        cancelled = await self.db.cancel_enforcement(group_id)

        await self._publish("global_sanction_lifted", {
            "group_id": str(group_id),
            "by_id": str(by_id),
            "reason": reason,
            "revoked": revoked,
            "enforcement_cancelled": bool(cancelled),
            "targets": [self._case_summary(c) for c in group_cases],
        })

        await self._log_group_event(
            [self._case_summary(c) for c in group_cases], "sanction_lifted",
            reason or "Global sanction lifted by the Moddy team.",
        )

        return {"revoked": revoked, "cases": len(group_cases), "cancelled": bool(cancelled)}

    # ------------------------------------------------------------------- halt

    async def halt(
        self, group_id, *, by_id: int, reason: Optional[str] = None,
        notify: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Stop a running countdown because an appeal was filed.

        Returns the updated enforcement row, or ``None`` when there was no
        *running* countdown to stop (already halted, executed or never
        scheduled).
        """
        if not self.db:
            raise RuntimeError("database unavailable")

        row = await self.db.halt_enforcement(group_id, by_id=by_id, reason=reason)
        if row is None:
            return None

        await self._publish("enforcement_halted", {
            "group_id": str(group_id),
            "level": row.get("level"),
            "subject_id": row.get("subject_id"),
            "premium": bool(row.get("premium")),
            "halted_by": str(by_id),
            "reason": reason,
            "original_deadline": self._iso(row.get("deadline")),
        })

        group_cases = await self.db.list_group_cases(group_id)
        await self._log_group_event(
            [self._case_summary(c) for c in group_cases], "enforcement_halted",
            reason or "Appeal received — enforcement countdown stopped.",
        )

        if notify and row.get("subject_id"):
            await self._notify_halt(row, group_cases)

        return row

    # ---------------------------------------------------------------- execute

    async def run_due(self) -> int:
        """Execute every countdown whose deadline has passed. Returns the count.

        Called by the periodic sweeper in ``bot.py``. Rows are claimed
        atomically, so a restart mid-sweep can never double-execute a group.
        """
        if not self.db:
            return 0
        due = await self.db.claim_due_enforcements()
        for row in due:
            try:
                await self._execute(row)
            except Exception as exc:
                logger.error("[Enforcement] Execution failed for group %s: %s",
                             row.get("group_id"), exc, exc_info=True)
        return len(due)

    async def _execute(self, row: Dict[str, Any]) -> None:
        """Run the deferred consequences of one group."""
        group_id = row["group_id"]
        group_cases = await self.db.list_group_cases(group_id)

        # Servers named in the group and suspended…
        targets: List[int] = [
            int(case["subject_id"])
            for case in group_cases
            if str(case["subject_type"]) == SubjectType.DISCORD_GUILD.value
            and self._has_active_action(case, "ban")
        ]

        # …plus every other server the suspended subject owns. A suspension is
        # "no Moddy at all", and the notice promised exactly that.
        if row.get("level") == GlobalLevel.SUSPENDED.value and \
                str(row.get("subject_type")) == SubjectType.DISCORD_USER.value:
            for guild_id in self.owned_guild_ids(row.get("subject_id")):
                if guild_id not in targets:
                    targets.append(guild_id)

        left_guilds: List[str] = []
        for guild_id in targets:
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            try:
                await guild.leave()
                left_guilds.append(str(guild_id))
                logger.info("[Enforcement] Left suspended guild %s (group %s)",
                            guild_id, group_id)
            except discord.HTTPException as exc:
                logger.error("[Enforcement] Could not leave guild %s: %s",
                             guild_id, exc)

        await self._publish("enforcement_executed", {
            "group_id": str(group_id),
            "level": row.get("level"),
            "subject_id": row.get("subject_id"),
            "premium": bool(row.get("premium")),
            "deadline": self._iso(row.get("deadline")),
            "left_guilds": left_guilds,
            # The backend owns billing and data retention: cancel the
            # subscription without refund (Terms) and purge what it must.
            "cancel_subscription": bool(row.get("premium")),
            "purge_guild_data": left_guilds,
            "targets": [self._case_summary(c) for c in group_cases],
        })

        await self._log_group_event(
            [self._case_summary(c) for c in group_cases], "enforcement_executed",
            "Grace period elapsed without an appeal — deferred consequences applied.",
        )

    # ------------------------------------------------------------- DM notices

    async def notify_group(
        self,
        *,
        user_id: int,
        level: GlobalLevel,
        reason: str,
        cases: List[Dict[str, Any]],
        enforcement: Optional[Dict[str, Any]] = None,
        premium: bool = False,
    ) -> bool:
        """Send the subject **one** notice covering the whole group."""
        from utils.global_sanction_views import build_sanction_notice

        try:
            user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
        except discord.HTTPException:
            logger.warning("[GlobalSanction] Unknown user %s — no notice sent", user_id)
            return False

        # Whether the notice should talk about servers at all: either the group
        # hits one, or the subject owns one that a suspension would cost them.
        has_servers = (
            any(c.get("subject_type") == SubjectType.DISCORD_GUILD.value for c in cases)
            or bool(self.owned_guild_ids(user_id))
        )

        view = build_sanction_notice(
            bot=self.bot, level=level, reason=reason, cases=cases,
            deadline=(enforcement or {}).get("deadline"), premium=premium,
            has_servers=has_servers,
        )
        try:
            await user.send(view=view)
            return True
        except discord.Forbidden:
            logger.info("[GlobalSanction] Cannot DM user %s (DMs closed)", user_id)
        except discord.HTTPException as exc:
            logger.error("[GlobalSanction] Notice DM to %s failed: %s", user_id, exc)
        return False

    async def _notify_halt(self, row: Dict[str, Any], group_cases: List[Dict[str, Any]]) -> None:
        """Tell the subject their countdown was stopped while the appeal runs."""
        from utils.global_sanction_views import build_halt_notice

        try:
            user = await self.bot.fetch_user(int(row["subject_id"]))
            await user.send(view=build_halt_notice(
                level=self._level_of(row), cases=[self._case_summary(c) for c in group_cases],
            ))
        except (discord.HTTPException, ValueError, TypeError):
            pass

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _level_of(row: Dict[str, Any]) -> GlobalLevel:
        try:
            return GlobalLevel(row.get("level") or "none")
        except ValueError:
            return GlobalLevel.NONE

    @staticmethod
    def _case_summary(case: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": str(case["id"]),
            "reference": case["reference"],
            "subject_type": str(case["subject_type"]),
            "subject_id": str(case["subject_id"]),
        }

    @staticmethod
    def _has_active_action(case: Dict[str, Any], action: str) -> bool:
        now = datetime.now(timezone.utc)
        for sanction in case.get("sanctions", []):
            if str(sanction.get("action")) != action:
                continue
            if str(sanction.get("status")) != "active":
                continue
            expires_at = sanction.get("expires_at")
            if expires_at is not None:
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if expires_at <= now:
                    continue
            return True
        return False

    @staticmethod
    def _iso(value) -> Optional[str]:
        return value.isoformat() if isinstance(value, datetime) else None

    def _enforcement_payload(self, row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not row:
            return None
        return {
            "deadline": self._iso(row.get("deadline")),
            "status": row.get("status"),
            "premium": bool(row.get("premium")),
        }

    def owned_guild_ids(self, user_id: Optional[int]) -> List[int]:
        """Servers Moddy is in that this user owns.

        A suspension costs the subject Moddy everywhere, not only on the
        servers named in the group — the notice says so, and the enforcement
        does it.
        """
        if user_id is None:
            return []
        return [g.id for g in self.bot.guilds if g.owner_id == int(user_id)]

    def _first_owner(self, guild_ids: Sequence[int]) -> Optional[int]:
        """Owner of the first resolvable server — who to notify and bill."""
        for guild_id in guild_ids:
            guild = self.bot.get_guild(int(guild_id))
            if guild is not None and guild.owner_id:
                return guild.owner_id
        return None

    async def _is_premium(self, user_id: int) -> bool:
        """Whether the subject was paying **before** the sanction applied.

        ``utils.subscription.is_subscribed`` already returns False for a
        sanctioned user, so this reads the raw subscription instead — the point
        is precisely to warn a paying user that their subscription is about to
        be cancelled.
        """
        try:
            from utils.subscription import get_subscription
            sub = await get_subscription(self.bot, int(user_id))
            return bool(sub and sub.get("is_active"))
        except Exception as exc:
            logger.debug("[GlobalSanction] Premium lookup failed for %s: %s", user_id, exc)
            return False

    async def _log_group_event(
        self, cases: Iterable[Dict[str, Any]], kind: str, content: str
    ) -> None:
        """Write one system event on every case of the group (audit trail)."""
        if not self.db:
            return
        for case in cases:
            try:
                await self.db.add_event(
                    uuid.UUID(str(case["id"])), "note",
                    author_type="system", author_id=None,
                    content=content, payload={"kind": kind},
                )
            except Exception as exc:
                logger.debug("[GlobalSanction] Timeline event failed: %s", exc)

    async def _publish(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Publish a global-sanction event for the backend (fire-and-forget)."""
        if not getattr(self.bot, "redis", None):
            return
        try:
            await self.bot.redis.publish(SANCTION_CHANNEL, json.dumps(
                {"type": event_type, "ts": datetime.now(timezone.utc).isoformat(), **payload},
                default=str,
            ))
        except Exception as exc:
            logger.error("[GlobalSanction] Could not publish %s: %s", event_type, exc)
