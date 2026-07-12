"""
Automod evaluation harness (session 3).

A regression harness for the detection pipeline: replay the whole funnel
(prefilter → trivial → blocklist → embedding → nano → grounding → barème) over a
committed **golden set** and measure precision/recall/F1, a category confusion
matrix and — crucially — whether any known false positive silently became
sanctionnable again.

Two ingredients live next to this package:

* ``golden.jsonl`` — one labelled case per line (input + ``attendu`` + ``tags``).
* ``fixtures.json`` — recorded model outputs (embedding score + nano raw verdict)
  per case id, so ``--replay`` reproduces a run **offline, for free, in CI** —
  no gateway, no network, deterministic. ``--live`` re-measures against the real
  gateway (costs a few cents) and can refresh the fixtures.

The runner is :mod:`automod.eval.run` (``python -m automod.eval.run``). It is a
pure evaluation tool: it never applies a sanction and never touches the DB.
"""

from __future__ import annotations

from .run import (
    GoldenCase,
    CaseResult,
    EvalReport,
    load_golden,
    load_fixtures,
    run_eval,
)

__all__ = [
    "GoldenCase",
    "CaseResult",
    "EvalReport",
    "load_golden",
    "load_fixtures",
    "run_eval",
]
