"""
Automod images + Moddy team labeling queue repository.

Four global tables (docs/AUTOMOD_AI.md §4.2/§4.3/§9):

* ``automod_label_items``        — decisions copied to the team for labeling;
* ``automod_image_hashes``       — team-validated perceptual hashes (block/allow);
* ``automod_learned_references`` — embedding references taught by the team;
* ``automod_learned_terms``      — blocklist terms typed by the team.

This repository only reads/writes rows; the decisions (what a label does)
live in ``services/automod_label_service.py``.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Union

from db.repositories.precedents import pack_vector, unpack_vector

logger = logging.getLogger("moddy.database")

LABEL_KINDS = ("texte", "image_scam", "image_nsfw")
LABEL_MOTIFS = ("sanction", "doute", "simulation")
LABEL_VERDICTS = ("sanctionnable", "non_sanctionnable", "ignore")
HASH_KINDS = ("scam", "nsfw")
HASH_VERDICTS = ("block", "allow")
TERM_MODES = ("words", "compact")

# Hard caps keeping the in-memory learned sets small (resident memory is the
# first cost line on Railway — see docs/RAILWAY.md).
MAX_LEARNED_REFERENCES = 1000
MAX_IMAGE_HASHES = 20000


def _uuid(value: Union[str, uuid.UUID]) -> Optional[uuid.UUID]:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


class AutomodLearningRepository:
    """CRUD for the automod labeling queue and its learned data."""

    # ------------------------------------------------------------ label items
    async def create_label_item(
        self,
        *,
        kind: str,
        motif: str,
        guild_id: Union[str, int],
        channel_id: Optional[Union[str, int]] = None,
        message_id: Optional[Union[str, int]] = None,
        author_id: Optional[Union[str, int]] = None,
        contenu: str = "",
        phash: Optional[int] = None,
        dhash: Optional[int] = None,
        dedup_key: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        case_id: Optional[Union[str, uuid.UUID]] = None,
    ) -> Optional[str]:
        if kind not in LABEL_KINDS or motif not in LABEL_MOTIFS:
            raise ValueError(f"invalid label item kind/motif: {kind}/{motif}")
        item_id = uuid.uuid4()
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO automod_label_items
                    (id, kind, motif, guild_id, channel_id, message_id, author_id,
                     contenu, phash, dhash, dedup_key, details, case_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, $13)
                """,
                item_id, kind, motif, int(guild_id),
                int(channel_id) if channel_id else None,
                int(message_id) if message_id else None,
                int(author_id) if author_id else None,
                (contenu or "")[:4000], phash, dhash, dedup_key,
                json.dumps(details or {}, ensure_ascii=False, default=str),
                _uuid(case_id) if case_id else None,
            )
        return str(item_id)

    async def find_recent_label_item(self, dedup_key: str, *,
                                     within: timedelta = timedelta(hours=24)
                                     ) -> Optional[Dict[str, Any]]:
        """The still-unlabeled item sharing ``dedup_key`` created within ``within``."""
        since = datetime.now(timezone.utc) - within
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM automod_label_items
                WHERE dedup_key = $1 AND created_at >= $2 AND verdict IS NULL
                ORDER BY created_at DESC LIMIT 1
                """,
                dedup_key, since,
            )
        return self._label_row(row)

    async def bump_label_item(self, item_id: str,
                              sanction: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """``occurrences += 1`` on a duplicate; returns the updated row.

        ``sanction`` (``{guild_id, case_id, author_id, message_id}``) is appended
        to ``details.sanctions`` so a single team label can revoke every bot
        sanction the duplicates caused.
        """
        extra = json.dumps([sanction] if sanction else [], default=str)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE automod_label_items
                SET occurrences = occurrences + 1,
                    details = jsonb_set(
                        COALESCE(details, '{}'::jsonb), '{sanctions}',
                        COALESCE(details->'sanctions', '[]'::jsonb) || $2::jsonb, true)
                WHERE id = $1 RETURNING *
                """,
                _uuid(item_id), extra,
            )
        return self._label_row(row)

    async def set_label_item_card(self, item_id: str, channel_id: int, message_id: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE automod_label_items SET card_channel_id = $2, card_message_id = $3 "
                "WHERE id = $1",
                _uuid(item_id), int(channel_id), int(message_id),
            )

    async def get_label_item(self, item_id: str) -> Optional[Dict[str, Any]]:
        uid = _uuid(item_id)
        if uid is None:
            return None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM automod_label_items WHERE id = $1", uid)
        return self._label_row(row)

    async def label_item(self, item_id: str, *, verdict: str, labeled_by: int,
                         categorie: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Record the team's label ONCE (a labeled item is final). Returns the
        row, or None when the item does not exist or is already labeled."""
        if verdict not in LABEL_VERDICTS:
            raise ValueError(f"invalid label verdict: {verdict}")
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE automod_label_items
                SET verdict = $2, labeled_by = $3, labeled_at = now(),
                    categorie_humaine = COALESCE($4, categorie_humaine)
                WHERE id = $1 AND verdict IS NULL
                RETURNING *
                """,
                _uuid(item_id), verdict, int(labeled_by), categorie,
            )
        return self._label_row(row)

    async def set_label_item_category(self, item_id: str, categorie: str) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE automod_label_items SET categorie_humaine = $2 "
                "WHERE id = $1 AND verdict IS NULL RETURNING *",
                _uuid(item_id), categorie,
            )
        return self._label_row(row)

    async def mark_label_item_revoked(self, item_id: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE automod_label_items SET revoked = TRUE WHERE id = $1", _uuid(item_id))

    async def label_queue_stats(self) -> Dict[str, int]:
        """Pending / labeled-today counts, for ``/mod automod``."""
        since = datetime.now(timezone.utc) - timedelta(days=1)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                  COUNT(*) FILTER (WHERE verdict IS NULL)                  AS pending,
                  COUNT(*) FILTER (WHERE labeled_at >= $1)                 AS labeled_24h,
                  COUNT(*) FILTER (WHERE labeled_at >= $1 AND revoked)     AS revoked_24h,
                  COUNT(*) FILTER (WHERE created_at >= $1)                 AS created_24h
                FROM automod_label_items
                """,
                since,
            )
        return {k: int(row[k] or 0) for k in ("pending", "labeled_24h", "revoked_24h", "created_24h")}

    # ------------------------------------------------------------ image hashes
    async def add_image_hash(self, *, phash: int, dhash: int, kind: str, verdict: str,
                             label_item_id: Optional[str] = None,
                             added_by: Optional[int] = None) -> Optional[int]:
        """Store a signed-64 hash pair. Returns the row id."""
        if kind not in HASH_KINDS or verdict not in HASH_VERDICTS:
            raise ValueError(f"invalid image hash kind/verdict: {kind}/{verdict}")
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                """
                INSERT INTO automod_image_hashes (phash, dhash, kind, verdict, label_item_id, added_by)
                VALUES ($1, $2, $3, $4, $5, $6) RETURNING id
                """,
                int(phash), int(dhash), kind, verdict,
                _uuid(label_item_id) if label_item_id else None,
                int(added_by) if added_by else None,
            )

    async def list_image_hashes(self, limit: int = MAX_IMAGE_HASHES) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, phash, dhash, kind, verdict FROM automod_image_hashes "
                "ORDER BY created_at DESC LIMIT $1",
                limit,
            )
        return [dict(r) for r in rows]

    async def delete_image_hash(self, hash_id: int) -> bool:
        async with self.pool.acquire() as conn:
            res = await conn.execute("DELETE FROM automod_image_hashes WHERE id = $1", int(hash_id))
        return res.endswith("1")

    async def count_image_hashes(self) -> Dict[str, int]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT kind, verdict, COUNT(*) AS n FROM automod_image_hashes GROUP BY kind, verdict")
        return {f"{r['kind']}_{r['verdict']}": int(r["n"]) for r in rows}

    # ------------------------------------------------------- learned references
    async def add_learned_reference(self, *, categorie: str, texte: str, vector,
                                    label_item_id: Optional[str] = None,
                                    added_by: Optional[int] = None,
                                    max_rows: int = MAX_LEARNED_REFERENCES) -> Optional[int]:
        """Insert one reference; the oldest are evicted past ``max_rows``."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                ref_id = await conn.fetchval(
                    """
                    INSERT INTO automod_learned_references
                        (categorie, texte, embedding, label_item_id, added_by)
                    VALUES ($1, $2, $3, $4, $5) RETURNING id
                    """,
                    categorie, (texte or "")[:2000], pack_vector(vector),
                    _uuid(label_item_id) if label_item_id else None,
                    int(added_by) if added_by else None,
                )
                await conn.execute(
                    """
                    DELETE FROM automod_learned_references WHERE id IN (
                        SELECT id FROM automod_learned_references
                        ORDER BY created_at DESC OFFSET $1
                    )
                    """,
                    max_rows,
                )
        return ref_id

    async def list_learned_references(self, limit: int = MAX_LEARNED_REFERENCES) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, categorie, texte, embedding FROM automod_learned_references "
                "ORDER BY created_at DESC LIMIT $1",
                limit,
            )
        out = []
        for r in rows:
            d = dict(r)
            d["vector"] = unpack_vector(d.pop("embedding"))
            out.append(d)
        return out

    async def delete_learned_reference(self, ref_id: int) -> bool:
        async with self.pool.acquire() as conn:
            res = await conn.execute(
                "DELETE FROM automod_learned_references WHERE id = $1", int(ref_id))
        return res.endswith("1")

    async def count_learned_references(self) -> int:
        async with self.pool.acquire() as conn:
            return int(await conn.fetchval("SELECT COUNT(*) FROM automod_learned_references") or 0)

    # ------------------------------------------------------------ learned terms
    async def add_learned_term(self, *, terme: str, categorie: str, mode: str,
                               label_item_id: Optional[str] = None,
                               added_by: Optional[int] = None) -> Optional[int]:
        if mode not in TERM_MODES:
            raise ValueError(f"invalid term mode: {mode}")
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                """
                INSERT INTO automod_learned_terms (terme, categorie, mode, label_item_id, added_by)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (terme, mode) DO UPDATE SET categorie = EXCLUDED.categorie
                RETURNING id
                """,
                terme[:100], categorie, mode,
                _uuid(label_item_id) if label_item_id else None,
                int(added_by) if added_by else None,
            )

    async def list_learned_terms(self) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, terme, categorie, mode FROM automod_learned_terms ORDER BY id")
        return [dict(r) for r in rows]

    async def delete_learned_term(self, term_id: int) -> bool:
        async with self.pool.acquire() as conn:
            res = await conn.execute("DELETE FROM automod_learned_terms WHERE id = $1", int(term_id))
        return res.endswith("1")

    # ------------------------------------------------------------------ utils
    def _label_row(self, row) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        d = dict(row)
        if d.get("details") is not None:
            d["details"] = self._parse_jsonb(d["details"])
        d["id"] = str(d["id"])
        if d.get("case_id") is not None:
            d["case_id"] = str(d["case_id"])
        return d
