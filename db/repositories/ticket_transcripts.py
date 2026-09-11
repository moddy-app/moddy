"""
Ticket transcripts repository — the archived conversation of a closed ticket.

One row per **closure**, not per channel: a ticket that is reopened and closed
again produces a second transcript, and both stay readable. That is why
``channel_id`` is not unique here while it is in ``tickets``.

This table deliberately holds no foreign key to ``tickets``. That row is
``DELETE``d the moment the Discord channel disappears
(``cogs/tickets.py::on_guild_channel_delete`` -> ``forget_channel``), and an
archive that dies with the channel it archives would be pointless. Everything
the dashboard needs to render a transcript — guild, number, category name,
owner, participants, who claimed and who closed — is snapshotted at closing
time for exactly that reason.

``key`` is the public handle: a UUID, so the dashboard URL cannot be walked by
incrementing an id. ``payload`` is the JSON body compressed with ``codec``
(``utils/compression.py``); its schema is documented field by field in
docs/TICKETS_INTEGRATION.md, which is what the backend implements against.

Authors live in their own table rather than inside the payload so that listing
transcripts ("who spoke in this ticket") never has to decompress anything.

See docs/TICKETS.md.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger('moddy.database')


def _row_to_dict(row) -> Optional[Dict[str, Any]]:
    """One ``ticket_transcripts`` row as a plain dict, ``key`` as text."""
    if row is None:
        return None
    data = dict(row)
    if data.get('key') is not None:
        data['key'] = str(data['key'])
    if data.get('participants') is not None:
        data['participants'] = list(data['participants'])
    return data


class TicketTranscriptRepository:
    """CRUD for ``ticket_transcripts`` and ``ticket_transcript_authors``."""

    # ------------------------------------------------------------------ #
    # Writing
    # ------------------------------------------------------------------ #

    async def create_ticket_transcript(
        self,
        *,
        guild_id: int,
        channel_id: int,
        ticket_number: int,
        panel_id: str,
        category_id: str,
        category_name: str,
        owner_id: int,
        participants: Sequence[int],
        claimed_by: Optional[int],
        closed_by: int,
        close_reason: Optional[str],
        opened_at,
        message_count: int,
        truncated: bool,
        codec: str,
        payload: bytes,
        payload_size: int,
        authors: Sequence[Dict[str, Any]] = (),
    ) -> Dict[str, Any]:
        """Store one closure. Returns the inserted row.

        The transcript and its authors go in together: a transcript whose
        authors failed to write would render as a wall of raw user ids.
        """
        key = uuid.uuid4()
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    INSERT INTO ticket_transcripts (
                        key, guild_id, channel_id, ticket_number, panel_id,
                        category_id, category_name, owner_id, participants,
                        claimed_by, closed_by, close_reason, opened_at,
                        message_count, truncated, codec, payload, payload_size
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,
                              $14,$15,$16,$17,$18)
                    RETURNING *
                    """,
                    key, guild_id, channel_id, ticket_number, panel_id,
                    category_id, category_name, owner_id, list(participants),
                    claimed_by, closed_by, close_reason, opened_at,
                    message_count, truncated, codec, payload, payload_size,
                )
                if authors:
                    await conn.executemany(
                        """
                        INSERT INTO ticket_transcript_authors (
                            transcript_id, author_id, username, display_name,
                            avatar_url, is_bot
                        ) VALUES ($1,$2,$3,$4,$5,$6)
                        ON CONFLICT (transcript_id, author_id) DO NOTHING
                        """,
                        [(row['id'], a['author_id'], a['username'],
                          a['display_name'], a.get('avatar_url'),
                          bool(a.get('is_bot'))) for a in authors],
                    )
        return _row_to_dict(row)

    async def set_transcript_log_message(self, transcript_id: int, *,
                                         channel_id: Optional[int],
                                         message_id: Optional[int]) -> None:
        """Remember where the closing log card was posted.

        A rating can land days after the closure; without this the card could
        never be edited to show it.
        """
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE ticket_transcripts SET log_channel_id = $2, "
                "log_message_id = $3 WHERE id = $1",
                transcript_id, channel_id, message_id)

    # ------------------------------------------------------------------ #
    # Reading
    # ------------------------------------------------------------------ #

    async def get_ticket_transcript(self, transcript_id: int) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            return _row_to_dict(await conn.fetchrow(
                "SELECT * FROM ticket_transcripts WHERE id = $1", transcript_id))

    async def get_ticket_transcript_by_key(self, key: str) -> Optional[Dict[str, Any]]:
        """Look one up by its public handle — what a DM button clicks with."""
        try:
            parsed = uuid.UUID(str(key))
        except (ValueError, AttributeError, TypeError):
            return None
        async with self.pool.acquire() as conn:
            return _row_to_dict(await conn.fetchrow(
                "SELECT * FROM ticket_transcripts WHERE key = $1", parsed))

    async def get_latest_ticket_transcript(self, channel_id: int) -> Optional[Dict[str, Any]]:
        """The most recent closure of this channel."""
        async with self.pool.acquire() as conn:
            return _row_to_dict(await conn.fetchrow(
                "SELECT * FROM ticket_transcripts WHERE channel_id = $1 "
                "ORDER BY closed_at DESC LIMIT 1", channel_id))

    async def list_ticket_transcripts(self, guild_id: int, *,
                                      owner_id: Optional[int] = None,
                                      limit: int = 25,
                                      offset: int = 0) -> List[Dict[str, Any]]:
        """Transcripts of a guild, newest first, **without** their payload.

        The body is the heavy part and a listing never needs it.
        """
        clauses = ["guild_id = $1"]
        args: List[Any] = [guild_id]
        if owner_id is not None:
            args.append(owner_id)
            clauses.append(f"owner_id = ${len(args)}")
        args.extend([limit, offset])
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, key, guild_id, channel_id, ticket_number, panel_id, "
                "category_id, category_name, owner_id, participants, claimed_by, "
                "closed_by, close_reason, opened_at, closed_at, message_count, "
                "truncated, payload_size FROM ticket_transcripts "
                f"WHERE {' AND '.join(clauses)} "
                f"ORDER BY closed_at DESC LIMIT ${len(args) - 1} OFFSET ${len(args)}",
                *args)
        return [_row_to_dict(r) for r in rows]

    async def get_transcript_authors(self, transcript_id: int) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT author_id, username, display_name, avatar_url, is_bot "
                "FROM ticket_transcript_authors WHERE transcript_id = $1",
                transcript_id)
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Retention
    # ------------------------------------------------------------------ #

    async def purge_expired_transcripts(self, guild_id: int, days: int) -> int:
        """Drop this guild's transcripts older than ``days``. Returns the count.

        ``days <= 0`` means "keep forever" and deletes nothing. The authors go
        with them through ``ON DELETE CASCADE``.
        """
        if days <= 0:
            return 0
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM ticket_transcripts WHERE guild_id = $1 "
                "AND closed_at < now() - ($2 || ' days')::interval",
                guild_id, str(int(days)))
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0

    async def guilds_with_transcripts(self) -> List[int]:
        """Guild ids that own at least one transcript — drives the purge loop."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT guild_id FROM ticket_transcripts")
        return [r['guild_id'] for r in rows]
