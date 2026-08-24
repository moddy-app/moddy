"""
Notifications repository — every message Moddy sends to a human.

Four tables, one idea per table:

``notification_contents``
    The **template** bodies, keyed by their SHA-256. Ten thousand welcome DMs
    configured with the same text write this row once; ``uses`` counts how
    often it has been sent. Placeholders are *not* resolved here, which is what
    makes the de-duplication work (see ``notifications/models.py``).

``notifications``
    One row per (message, recipient): its uuid — the reference a user or a
    staffer quotes — who sent it, who receives it, which platforms it targets,
    and the ``variables`` needed to rebuild the exact wording later.

``notification_deliveries``
    One row per (notification, platform): did Discord accept it, which message
    id did it get, why did the mail fail. Primary-keyed on the pair so a retry
    updates instead of duplicating.

``notification_reports``
    Abuse reports filed by recipients, with the review-panel message they were
    posted to and the staff decision.

See docs/NOTIFICATIONS.md.
"""

from __future__ import annotations

import json
import logging
import uuid as uuid_module
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger('moddy.database')


def _as_uuid(value: Any) -> Optional[uuid_module.UUID]:
    """Coerce a str/UUID into a UUID, or ``None`` when it is not one."""
    if value is None:
        return None
    if isinstance(value, uuid_module.UUID):
        return value
    try:
        return uuid_module.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


class NotificationRepository:
    """CRUD for the notification tables."""

    # ------------------------------------------------------------------ #
    # Content
    # ------------------------------------------------------------------ #

    async def store_notification_content(self, content_hash: str, payload: Dict[str, Any]) -> None:
        """Insert the template body, or bump its counters if already known."""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO notification_contents (hash, payload, uses)
                VALUES ($1, $2::jsonb, 1)
                ON CONFLICT (hash) DO UPDATE
                    SET uses = notification_contents.uses + 1,
                        last_seen_at = now()
            """, content_hash, json.dumps(payload, ensure_ascii=False))

    async def get_notification_content(self, content_hash: str) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM notification_contents WHERE hash = $1", content_hash)
            if not row:
                return None
            return {
                "hash": row["hash"],
                "payload": self._parse_jsonb(row["payload"]),
                "uses": row["uses"],
                "first_seen_at": row["first_seen_at"],
                "last_seen_at": row["last_seen_at"],
            }

    # ------------------------------------------------------------------ #
    # Notifications
    # ------------------------------------------------------------------ #

    async def create_notification(
        self,
        *,
        kind: str,
        author: str,
        recipient_type: str,
        content_hash: str,
        content_payload: Dict[str, Any],
        variables: Optional[Dict[str, Any]] = None,
        platforms: Sequence[str] = ("discord",),
        source_service: Optional[str] = None,
        source_guild_id: Optional[int] = None,
        actor_id: Optional[int] = None,
        recipient_id: Optional[int] = None,
        recipient_ref: Optional[str] = None,
        reportable: bool = False,
        locale: Optional[str] = None,
        batch_id: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Write the notification row (and its content) and return it.

        The uuid is generated here so the caller can put it on the message it is
        about to send — the button custom_ids carry it, and a recipient quoting
        "notification `…`" to support must land on this row.
        """
        await self.store_notification_content(content_hash, content_payload)

        notification_id = uuid_module.uuid4()
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO notifications (
                    id, batch_id, kind, author, source_service, source_guild_id,
                    actor_id, recipient_type, recipient_id, recipient_ref,
                    content_hash, variables, platforms, reportable, locale
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, $13, $14, $15)
                RETURNING *
            """,
                notification_id, _as_uuid(batch_id), kind, author, source_service,
                source_guild_id, actor_id, recipient_type, recipient_id, recipient_ref,
                content_hash, json.dumps(variables or {}, ensure_ascii=False),
                list(platforms), reportable, locale,
            )

            # Every targeted platform starts pending; the sender flips its own.
            for platform in platforms:
                await conn.execute("""
                    INSERT INTO notification_deliveries (notification_id, platform, status)
                    VALUES ($1, $2, 'pending')
                    ON CONFLICT (notification_id, platform) DO NOTHING
                """, notification_id, platform)

        return self._notification_row(row)

    def _notification_row(self, row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "batch_id": row["batch_id"],
            "kind": row["kind"],
            "author": row["author"],
            "source_service": row["source_service"],
            "source_guild_id": row["source_guild_id"],
            "actor_id": row["actor_id"],
            "recipient_type": row["recipient_type"],
            "recipient_id": row["recipient_id"],
            "recipient_ref": row["recipient_ref"],
            "content_hash": row["content_hash"],
            "variables": self._parse_jsonb(row["variables"]),
            "platforms": list(row["platforms"] or []),
            "reportable": row["reportable"],
            "locale": row["locale"],
            "created_at": row["created_at"],
        }

    async def get_notification(self, notification_id: Any) -> Optional[Dict[str, Any]]:
        """The full notification: row + template payload + every delivery."""
        nid = _as_uuid(notification_id)
        if nid is None:
            return None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM notifications WHERE id = $1", nid)
            if not row:
                return None
            data = self._notification_row(row)
            content = await conn.fetchrow(
                "SELECT payload, uses FROM notification_contents WHERE hash = $1",
                data["content_hash"])
            data["content"] = self._parse_jsonb(content["payload"]) if content else {}
            data["content_uses"] = content["uses"] if content else 0
            deliveries = await conn.fetch("""
                SELECT * FROM notification_deliveries
                WHERE notification_id = $1 ORDER BY platform
            """, nid)
            data["deliveries"] = [{
                "platform": d["platform"],
                "status": d["status"],
                "channel_id": d["channel_id"],
                "message_id": d["message_id"],
                "error": d["error"],
                "updated_at": d["updated_at"],
            } for d in deliveries]
        return data

    async def set_notification_delivery(
        self,
        notification_id: Any,
        platform: str,
        status: str,
        *,
        channel_id: Optional[int] = None,
        message_id: Optional[int] = None,
        error: Optional[str] = None,
    ) -> None:
        """Record the outcome of one platform's delivery attempt."""
        nid = _as_uuid(notification_id)
        if nid is None:
            return
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO notification_deliveries
                    (notification_id, platform, status, channel_id, message_id, error, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, now())
                ON CONFLICT (notification_id, platform) DO UPDATE
                    SET status = EXCLUDED.status,
                        channel_id = COALESCE(EXCLUDED.channel_id, notification_deliveries.channel_id),
                        message_id = COALESCE(EXCLUDED.message_id, notification_deliveries.message_id),
                        error = EXCLUDED.error,
                        updated_at = now()
            """, nid, platform, status, channel_id, message_id,
                 (error[:500] if error else None))

    async def list_notifications(
        self,
        *,
        recipient_id: Optional[int] = None,
        source_guild_id: Optional[int] = None,
        batch_id: Optional[Any] = None,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        """Recent notifications for a recipient, a server, or a batch."""
        clauses, params = [], []
        if recipient_id is not None:
            params.append(recipient_id)
            clauses.append(f"recipient_id = ${len(params)}")
        if source_guild_id is not None:
            params.append(source_guild_id)
            clauses.append(f"source_guild_id = ${len(params)}")
        if batch_id is not None:
            params.append(_as_uuid(batch_id))
            clauses.append(f"batch_id = ${len(params)}")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 200)))
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM notifications {where} "
                f"ORDER BY created_at DESC LIMIT ${len(params)}", *params)
        return [self._notification_row(r) for r in rows]

    async def get_batch_stats(self, batch_id: Any) -> Dict[str, int]:
        """How a broadcast went: one count per delivery status."""
        bid = _as_uuid(batch_id)
        if bid is None:
            return {}
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT d.status, COUNT(*) AS total
                FROM notifications n
                JOIN notification_deliveries d ON d.notification_id = n.id
                WHERE n.batch_id = $1
                GROUP BY d.status
            """, bid)
        return {r["status"]: r["total"] for r in rows}

    # ------------------------------------------------------------------ #
    # Reports
    # ------------------------------------------------------------------ #

    async def create_notification_report(
        self, *, notification_id: Any, reporter_id: int, reason: str,
    ) -> Optional[Dict[str, Any]]:
        """File an abuse report. Returns ``None`` if this user already filed one."""
        nid = _as_uuid(notification_id)
        if nid is None:
            return None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO notification_reports (id, notification_id, reporter_id, reason)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (notification_id, reporter_id) DO NOTHING
                RETURNING *
            """, uuid_module.uuid4(), nid, reporter_id, reason[:1000])
        return self._report_row(row) if row else None

    def _report_row(self, row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "notification_id": row["notification_id"],
            "reporter_id": row["reporter_id"],
            "reason": row["reason"],
            "status": row["status"],
            "claimed_by": row["claimed_by"],
            "claimed_at": row["claimed_at"],
            "decided_by": row["decided_by"],
            "decided_at": row["decided_at"],
            "decision_note": row["decision_note"],
            "review_channel_id": row["review_channel_id"],
            "review_message_id": row["review_message_id"],
            "created_at": row["created_at"],
        }

    async def get_notification_report(self, report_id: Any) -> Optional[Dict[str, Any]]:
        rid = _as_uuid(report_id)
        if rid is None:
            return None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM notification_reports WHERE id = $1", rid)
        return self._report_row(row) if row else None

    async def get_reports_for_notification(self, notification_id: Any) -> List[Dict[str, Any]]:
        nid = _as_uuid(notification_id)
        if nid is None:
            return []
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM notification_reports
                WHERE notification_id = $1 ORDER BY created_at
            """, nid)
        return [self._report_row(r) for r in rows]

    async def set_report_review_message(
        self, report_id: Any, channel_id: int, message_id: int
    ) -> None:
        """Remember where the review panel lives so decisions can edit it."""
        rid = _as_uuid(report_id)
        if rid is None:
            return
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE notification_reports
                SET review_channel_id = $2, review_message_id = $3
                WHERE id = $1
            """, rid, channel_id, message_id)

    async def claim_notification_report(self, report_id: Any, staff_id: int) -> Optional[Dict[str, Any]]:
        """Assign a pending report. Returns ``None`` when someone got there first."""
        rid = _as_uuid(report_id)
        if rid is None:
            return None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE notification_reports
                SET status = 'claimed', claimed_by = $2, claimed_at = now()
                WHERE id = $1 AND status = 'pending'
                RETURNING *
            """, rid, staff_id)
        return self._report_row(row) if row else None

    async def decide_notification_report(
        self, report_id: Any, staff_id: int, status: str, note: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Accept or refuse a report that has not been decided yet."""
        rid = _as_uuid(report_id)
        if rid is None or status not in ("accepted", "refused"):
            return None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE notification_reports
                SET status = $3, decided_by = $2, decided_at = now(), decision_note = $4
                WHERE id = $1 AND status IN ('pending', 'claimed')
                RETURNING *
            """, rid, staff_id, status, (note or None))
        return self._report_row(row) if row else None
