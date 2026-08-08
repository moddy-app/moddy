"""
Server-precedents repository (session 7).

Stores the per-guild jurisprudence that lets the automod learn a server's local
culture from its human rulings — see docs/AUTOMOD_AI.md §2quinquies. Each row is a
past message together with the FINAL human verdict and the **normalised** message
embedding (the one the funnel already computed at detection time), packed as
float32 bytes so no pgvector extension is required — cosine similarity is a plain
dot product in Python (``automod/precedents.py``).

This repository only reads/writes the ``automod_precedents`` table; it never
decides anything and never calls the embedding API (the service does, once, when
recording a precedent).
"""

from __future__ import annotations

import array
import logging
import struct
import sys
from typing import Any, Dict, List, Optional, Sequence, Union

logger = logging.getLogger("moddy.database")

# Vectors are held in memory as float32 ``array.array`` rather than ``list[float]``.
# The on-disk format is already float32, so this loses no precision — a plain list
# unpacks each component into a distinct 64-bit Python float object, which costs
# ~49 KB per 1536-dim vector against ~6 KB for the array (8x). With up to
# PRECEDENT_MAX_PER_GUILD vectors cached per guild, that difference dominates the
# bot's resident memory. ``array.array`` is a Sequence, so it supports len(),
# iteration, indexing and zip() — everything the pure matcher in
# ``automod/precedents.py`` needs — and empty arrays are falsy like empty lists.
VECTOR_TYPECODE = "f"


def empty_vector() -> "array.array":
    """A shared-shape empty float32 vector (the 'no embedding' value)."""
    return array.array(VECTOR_TYPECODE)

# Allowed human verdicts (mirror the CHECK constraint in db/base.py).
PRECEDENT_VERDICTS = ("non_sanctionnable", "sanctionnable")


def pack_vector(vector: Sequence[float]) -> bytes:
    """Pack a float vector into little-endian float32 bytes for BYTEA storage."""
    return struct.pack(f"<{len(vector)}f", *[float(x) for x in vector])


def unpack_vector(blob: Union[bytes, memoryview, None]) -> "array.array":
    """Unpack float32 BYTEA into a float32 ``array.array`` (empty on failure).

    Returns an ``array.array('f')`` rather than a ``list[float]``: the stored
    bytes are float32 already, so nothing is lost, and the array holds the
    components as packed machine floats instead of ~1536 separate Python float
    objects (see :data:`VECTOR_TYPECODE`).
    """
    if not blob:
        return empty_vector()
    try:
        raw = bytes(blob)
        if len(raw) % 4:  # not a whole number of float32 — treat as corrupt
            return empty_vector()
        vec = array.array(VECTOR_TYPECODE)
        vec.frombytes(raw)
        if sys.byteorder != "little":  # on-disk format is little-endian ('<f')
            vec.byteswap()
        return vec
    except Exception:  # noqa: BLE001 — a corrupt row must not break matching
        return empty_vector()


class PrecedentRepository:
    """CRUD + eviction for ``automod_precedents``."""

    async def add_precedent(
        self,
        *,
        guild_id: Union[str, int],
        contenu_norm: str,
        embedding: Sequence[float],
        verdict_humain: str,
        source: str,
        categorie: Optional[str] = None,
        gravite: Optional[str] = None,
        max_per_guild: int = 500,
    ) -> Optional[int]:
        """Insert a precedent (and evict the guild's oldest past the cap).

        Returns the new row id, or None on failure / invalid verdict.
        """
        if verdict_humain not in PRECEDENT_VERDICTS or not embedding:
            return None
        try:
            blob = pack_vector(embedding)
            async with self.pool.acquire() as conn:
                new_id = await conn.fetchval(
                    """
                    INSERT INTO automod_precedents (
                        guild_id, contenu_norm, embedding, verdict_humain,
                        categorie, gravite, source
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7)
                    RETURNING id
                    """,
                    int(guild_id), (contenu_norm or "")[:2000], blob,
                    verdict_humain, categorie, gravite, source,
                )
                # Evict the oldest beyond the per-guild cap (bounded storage).
                await conn.execute(
                    """
                    DELETE FROM automod_precedents
                    WHERE guild_id = $1 AND id NOT IN (
                        SELECT id FROM automod_precedents
                        WHERE guild_id = $1
                        ORDER BY created_at DESC, id DESC
                        LIMIT $2
                    )
                    """,
                    int(guild_id), int(max_per_guild),
                )
            return int(new_id) if new_id is not None else None
        except Exception as e:  # noqa: BLE001 — precedent feeding is best-effort
            logger.error("precedent insert failed: %s", e)
            return None

    async def list_precedents(
        self, guild_id: Union[str, int], *, limit: int = 500,
        with_vectors: bool = True,
    ) -> List[Dict[str, Any]]:
        """The guild's precedents, newest first. ``with_vectors`` unpacks the
        embedding into a float list under ``vector`` (the matching input)."""
        cols = ("id, guild_id, contenu_norm, verdict_humain, categorie, gravite, "
                "source, created_at")
        if with_vectors:
            cols += ", embedding"
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    f"SELECT {cols} FROM automod_precedents "
                    "WHERE guild_id = $1 ORDER BY created_at DESC, id DESC LIMIT $2",
                    int(guild_id), int(limit),
                )
        except Exception as e:  # noqa: BLE001
            logger.error("precedent list failed: %s", e)
            return []
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            if with_vectors:
                d["vector"] = unpack_vector(d.pop("embedding", None))
            d["id"] = int(d["id"])
            out.append(d)
        return out

    async def count_precedents(self, guild_id: Union[str, int]) -> int:
        try:
            async with self.pool.acquire() as conn:
                return int(await conn.fetchval(
                    "SELECT COUNT(*) FROM automod_precedents WHERE guild_id = $1",
                    int(guild_id)) or 0)
        except Exception as e:  # noqa: BLE001
            logger.error("precedent count failed: %s", e)
            return 0

    async def last_precedent_at(self, guild_id: Union[str, int]):
        """Timestamp of the most recent precedent for this guild (or None)."""
        try:
            async with self.pool.acquire() as conn:
                return await conn.fetchval(
                    "SELECT max(created_at) FROM automod_precedents WHERE guild_id = $1",
                    int(guild_id))
        except Exception as e:  # noqa: BLE001
            logger.error("precedent last_at failed: %s", e)
            return None

    async def delete_precedent(self, guild_id: Union[str, int],
                               precedent_id: Union[str, int]) -> bool:
        """Delete one precedent (scoped to the guild). Returns True if removed."""
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM automod_precedents WHERE id = $1 AND guild_id = $2",
                    int(precedent_id), int(guild_id))
            return result.split()[-1] != "0"
        except Exception as e:  # noqa: BLE001
            logger.error("precedent delete failed: %s", e)
            return False
