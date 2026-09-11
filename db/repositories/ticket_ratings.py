"""
Ticket ratings repository — how the opener rated the way they were handled.

One rating per **closure** (``UNIQUE (transcript_id)``), not per channel: a
ticket that is reopened and closed again is a new interaction and deserves its
own rating.

Like ``ticket_transcripts``, this table holds no foreign key to ``tickets``:
the member may leave their rating days later, from a DM button, long after the
channel — and its ``tickets`` row — has been deleted. Everything needed to
attribute the rating is snapshotted at rating time.

``rated_staff_id`` defaults to whoever claimed the ticket, else whoever closed
it, but the member can change it in the modal; it is NULL when they picked
"nobody in particular".

See docs/TICKETS.md.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger('moddy.database')

# Where a rating was collected. Kept in the schema so the dashboard can tell
# "asked at closing time" apart from "came back later through the DM button".
TRIGGER_CLOSE_REQUEST = "close_request"
TRIGGER_SELF_CLOSE = "self_close"
TRIGGER_DM_BUTTON = "dm_button"
RATING_TRIGGERS = (TRIGGER_CLOSE_REQUEST, TRIGGER_SELF_CLOSE, TRIGGER_DM_BUTTON)

MIN_SCORE = 1
MAX_SCORE = 5


class TicketRatingRepository:
    """CRUD and aggregates for the ``ticket_ratings`` table."""

    # ------------------------------------------------------------------ #
    # Writing
    # ------------------------------------------------------------------ #

    async def create_ticket_rating(
        self, *,
        guild_id: int,
        channel_id: int,
        transcript_id: Optional[int],
        ticket_number: int,
        category_id: str,
        rated_staff_id: Optional[int],
        rated_by: int,
        score: int,
        comment: Optional[str] = None,
        trigger: str = TRIGGER_DM_BUTTON,
    ) -> Optional[Dict[str, Any]]:
        """Store one rating, or ``None`` if this closure was already rated.

        The duplicate is caught by the unique constraint rather than by a
        prior SELECT: two clicks on the DM button a few milliseconds apart
        would both pass a check-then-insert.
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO ticket_ratings (
                    guild_id, channel_id, transcript_id, ticket_number,
                    category_id, rated_staff_id, rated_by, score, comment, trigger
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                ON CONFLICT ON CONSTRAINT ticket_ratings_once DO NOTHING
                RETURNING *
                """,
                guild_id, channel_id, transcript_id, ticket_number,
                category_id, rated_staff_id, rated_by, int(score), comment,
                trigger)
        return dict(row) if row else None

    # ------------------------------------------------------------------ #
    # Reading
    # ------------------------------------------------------------------ #

    async def get_ticket_rating(self, transcript_id: int) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM ticket_ratings WHERE transcript_id = $1",
                transcript_id)
        return dict(row) if row else None

    async def list_staff_ratings(self, guild_id: int, staff_id: int, *,
                                 days: int = 30, limit: int = 10,
                                 categories: Optional[List[str]] = None
                                 ) -> List[Dict[str, Any]]:
        """This staffer's individual ratings, newest first."""
        args: List[Any] = [guild_id, staff_id, str(int(days))]
        clauses = ["guild_id = $1", "rated_staff_id = $2",
                   "created_at >= now() - ($3 || ' days')::interval"]
        if categories is not None:
            args.append(categories)
            clauses.append(f"category_id = ANY(${len(args)})")
        args.append(limit)
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM ticket_ratings WHERE {' AND '.join(clauses)} "
                f"ORDER BY created_at DESC LIMIT ${len(args)}", *args)
        return [dict(r) for r in rows]

    async def staff_rating_summary(self, guild_id: int, staff_id: int, *,
                                   days: int = 30,
                                   categories: Optional[List[str]] = None
                                   ) -> Dict[str, Any]:
        """Count, average and 1..5 breakdown for one staffer."""
        args: List[Any] = [guild_id, staff_id, str(int(days))]
        clauses = ["guild_id = $1", "rated_staff_id = $2",
                   "created_at >= now() - ($3 || ' days')::interval"]
        if categories is not None:
            args.append(categories)
            clauses.append(f"category_id = ANY(${len(args)})")
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT COUNT(*)::int AS ratings,
                       AVG(score)::float AS average,
                       COUNT(*) FILTER (WHERE score = 1)::int AS s1,
                       COUNT(*) FILTER (WHERE score = 2)::int AS s2,
                       COUNT(*) FILTER (WHERE score = 3)::int AS s3,
                       COUNT(*) FILTER (WHERE score = 4)::int AS s4,
                       COUNT(*) FILTER (WHERE score = 5)::int AS s5
                FROM ticket_ratings WHERE {' AND '.join(clauses)}
                """, *args)
        return dict(row) if row else {}

    async def guild_rating_leaderboard(self, guild_id: int, *, days: int = 30,
                                       limit: int = 25,
                                       categories: Optional[List[str]] = None
                                       ) -> List[Dict[str, Any]]:
        """Every rated staffer of this guild: volume and average.

        Ordered by volume, not by average: a single 5/5 must not outrank
        someone who handled fifty tickets. The caller decides what minimum
        volume it is willing to display.
        """
        args: List[Any] = [guild_id, str(int(days))]
        clauses = ["guild_id = $1", "rated_staff_id IS NOT NULL",
                   "created_at >= now() - ($2 || ' days')::interval"]
        if categories is not None:
            args.append(categories)
            clauses.append(f"category_id = ANY(${len(args)})")
        args.append(limit)
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT rated_staff_id,
                       COUNT(*)::int AS ratings,
                       AVG(score)::float AS average,
                       COUNT(*) FILTER (WHERE score <= 2)::int AS negative
                FROM ticket_ratings WHERE {' AND '.join(clauses)}
                GROUP BY rated_staff_id
                ORDER BY ratings DESC, average DESC NULLS LAST
                LIMIT ${len(args)}
                """, *args)
        return [dict(r) for r in rows]

    async def staff_handled_counts(self, guild_id: int, *, days: int = 30,
                                   categories: Optional[List[str]] = None
                                   ) -> Dict[int, int]:
        """How many tickets each staffer handled — claimed if so, else closed.

        Read from ``ticket_transcripts`` rather than ``tickets``: the closed
        tickets whose channel was deleted have to keep counting, otherwise
        tidying up a support category would erase its staff's workload.
        """
        args: List[Any] = [guild_id, str(int(days))]
        clauses = ["guild_id = $1",
                   "closed_at >= now() - ($2 || ' days')::interval"]
        if categories is not None:
            args.append(categories)
            clauses.append(f"category_id = ANY(${len(args)})")
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT COALESCE(claimed_by, closed_by) AS staff_id,
                       COUNT(*)::int AS handled
                FROM ticket_transcripts WHERE {' AND '.join(clauses)}
                GROUP BY 1
                """, *args)
        return {r['staff_id']: r['handled'] for r in rows if r['staff_id']}
