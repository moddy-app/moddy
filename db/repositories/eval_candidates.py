"""
Automod evaluation-candidate repository (session 3).

Stores the annotation corpus that feeds the offline golden set
(``automod/eval``). Rows come from two sources:

* **shadow mode** (``dry_run``) — every simulated sanction card is written here,
  then a moderator annotates it via the card's ✅ / ❌ / ⚠️ buttons;
* **human corrections** — an accepted appeal or a "false positive" click records
  a candidate too (so the model learns from real rulings).

A curator (``make eval-import``) later reviews the annotated candidates and folds
the useful ones into ``golden.jsonl``. This repository only reads/writes the
table; it never decides anything.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger("moddy.database")

# Allowed human verdicts (mirror the CHECK constraint in db/base.py).
EVAL_VERDICTS = ("correct", "faux_positif", "disproportionne")


class EvalCandidateRepository:
    """CRUD for ``automod_eval_candidates``."""

    async def create_eval_candidate(
        self,
        *,
        guild_id: Union[str, int],
        source: str,
        contenu: str,
        contexte: Optional[List[str]] = None,
        verdict: Optional[Dict[str, Any]] = None,
        cran: Optional[int] = None,
        bareme: Optional[Any] = None,
        channel_id: Optional[Union[str, int]] = None,
        message_id: Optional[Union[str, int]] = None,
        author_id: Optional[Union[str, int]] = None,
    ) -> Optional[str]:
        """Insert a candidate; returns its id (str UUID) or None on failure."""
        candidate_id = uuid.uuid4()
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO automod_eval_candidates (
                        id, guild_id, channel_id, message_id, author_id,
                        contenu, contexte, verdict, cran, bareme, source
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9,$10::jsonb,$11)
                    """,
                    candidate_id, int(guild_id),
                    int(channel_id) if channel_id is not None else None,
                    int(message_id) if message_id is not None else None,
                    int(author_id) if author_id is not None else None,
                    (contenu or "")[:4000],
                    json.dumps(contexte or []),
                    json.dumps(verdict or {}),
                    int(cran) if cran is not None else None,
                    json.dumps(bareme) if bareme is not None else None,
                    source,
                )
            return str(candidate_id)
        except Exception as e:  # noqa: BLE001 — annotation is best-effort
            logger.error("eval candidate insert failed: %s", e)
            return None

    async def get_eval_candidate(self, candidate_id: Union[str, uuid.UUID]
                                 ) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM automod_eval_candidates WHERE id = $1",
                uuid.UUID(str(candidate_id)),
            )
        return self._eval_row(row)

    async def annotate_eval_candidate(
        self,
        candidate_id: Union[str, uuid.UUID],
        verdict_humain: str,
        annotated_by: Optional[Union[str, int]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Record a moderator's judgement on a candidate; returns the updated row.

        Idempotent per-value: annotating with the same verdict twice is a no-op
        beyond refreshing ``annotated_by``/``annotated_at``. Returns None if the
        candidate does not exist or the verdict is invalid.
        """
        if verdict_humain not in EVAL_VERDICTS:
            return None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE automod_eval_candidates
                SET verdict_humain = $2,
                    annotated_by = $3,
                    annotated_at = now()
                WHERE id = $1
                RETURNING *
                """,
                uuid.UUID(str(candidate_id)), verdict_humain,
                int(annotated_by) if annotated_by is not None else None,
            )
        return self._eval_row(row)

    async def list_eval_candidates(
        self,
        *,
        guild_id: Optional[Union[str, int]] = None,
        annotated_only: bool = False,
        pending_import_only: bool = False,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """List candidates (most recent first) with optional filters."""
        conds: List[str] = []
        params: List[Any] = []
        if guild_id is not None:
            params.append(int(guild_id))
            conds.append(f"guild_id = ${len(params)}")
        if annotated_only:
            conds.append("verdict_humain IS NOT NULL")
        if pending_import_only:
            conds.append("imported = FALSE")
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        params.append(limit)
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM automod_eval_candidates{where} "
                f"ORDER BY created_at DESC LIMIT ${len(params)}",
                *params,
            )
        return [self._eval_row(r) for r in rows if r is not None]

    async def mark_eval_candidates_imported(
        self, ids: List[Union[str, uuid.UUID]]
    ) -> int:
        """Flag candidates as folded into the golden set. Returns the count."""
        if not ids:
            return 0
        uuids = [uuid.UUID(str(i)) for i in ids]
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE automod_eval_candidates SET imported = TRUE "
                "WHERE id = ANY($1::uuid[])",
                uuids,
            )
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0

    async def count_eval_candidates(
        self, guild_id: Optional[Union[str, int]] = None,
        annotated_only: bool = False,
    ) -> int:
        conds: List[str] = []
        params: List[Any] = []
        if guild_id is not None:
            params.append(int(guild_id))
            conds.append(f"guild_id = ${len(params)}")
        if annotated_only:
            conds.append("verdict_humain IS NOT NULL")
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                f"SELECT COUNT(*) FROM automod_eval_candidates{where}", *params)

    # ------------------------------------------------------------------ utils
    def _eval_row(self, row) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        d = dict(row)
        for k in ("contexte", "verdict", "bareme"):
            if k in d and d[k] is not None:
                d[k] = self._parse_jsonb(d[k])
        d["id"] = str(d["id"])
        return d
