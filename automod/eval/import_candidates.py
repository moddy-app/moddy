"""
Convert annotated eval candidates → golden-set JSONL (``make eval-import``).

Shadow-mode cards and human corrections accumulate in ``automod_eval_candidates``
(see ``db/repositories/eval_candidates.py``). This script reads the **annotated**
ones and prints golden-shaped JSONL lines on stdout for a curator (Jules) to
review and paste into ``automod/eval/golden.jsonl`` — the "manual review" step
the plan calls for. Nothing is auto-committed.

A moderator's ruling maps to an expected verdict:

* ``correct``          → ``sanctionnable: true`` (+ the model's category);
* ``faux_positif``     → ``sanctionnable: false`` (tagged ``faux_positif_reel``);
* ``disproportionne``  → ``sanctionnable: true`` but tagged ``disproportionne``
  (the qualification stood; the *sanction* was too harsh — a barème calibration
  signal, not a detection one).

Requires ``DATABASE_URL``. It only reads (and optionally flags rows imported); it
never applies anything. This is a dev/ops tool and is not exercised in CI.

Usage::

    DATABASE_URL=... python -m automod.eval.import_candidates [--guild ID]
    DATABASE_URL=... python -m automod.eval.import_candidates --mark-imported
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any, Dict, List, Optional

_ATTENDU_BY_VERDICT = {
    "correct": {"sanctionnable": True},
    "faux_positif": {"sanctionnable": False},
    "disproportionne": {"sanctionnable": True},
}
_EXTRA_TAGS = {
    "correct": [],
    "faux_positif": ["faux_positif_reel"],
    "disproportionne": ["disproportionne"],
}


def candidate_to_golden(row: Dict[str, Any], seq: int) -> Dict[str, Any]:
    """Map one annotated candidate row to a golden-set line (for review)."""
    verdict = row.get("verdict") or {}
    human = row.get("verdict_humain") or "faux_positif"
    attendu = dict(_ATTENDU_BY_VERDICT.get(human, {"sanctionnable": False}))
    if attendu.get("sanctionnable") and verdict.get("categorie"):
        attendu["categorie"] = verdict["categorie"]
    tags = ["from_annotation", *_EXTRA_TAGS.get(human, [])]
    return {
        "id": f"cand-{seq:04d}",
        "contenu": row.get("contenu") or "",
        "contexte": row.get("contexte") or [],
        "attendu": attendu,
        "tags": tags,
        "origine": f"annotation guild={row.get('guild_id')} human={human}",
    }


async def _run(guild_id: Optional[int], mark_imported: bool, limit: int) -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required for eval-import", flush=True)
        return 2

    from db.base import ModdyDatabase

    db = ModdyDatabase(database_url)
    await db.connect()
    try:
        rows: List[Dict[str, Any]] = await db.list_eval_candidates(
            guild_id=guild_id, annotated_only=True,
            pending_import_only=True, limit=limit,
        )
        for i, row in enumerate(rows, 1):
            print(json.dumps(candidate_to_golden(row, i), ensure_ascii=False))
        if mark_imported and rows:
            n = await db.mark_eval_candidates_imported([r["id"] for r in rows])
            print(f"# marked {n} candidate(s) imported", flush=True)
        elif not rows:
            print("# no annotated candidates pending import", flush=True)
    finally:
        await db.close()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Convert eval candidates to golden JSONL")
    parser.add_argument("--guild", type=int, default=None, help="filter by guild id")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--mark-imported", action="store_true",
                        help="flag the emitted candidates as imported")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.guild, args.mark_imported, args.limit))


if __name__ == "__main__":
    raise SystemExit(main())
