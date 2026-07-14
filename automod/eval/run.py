"""
Offline regression runner for the automod pipeline (session 3).

Replays the whole funnel over the golden set and reports precision / recall / F1,
a per-category confusion matrix and the list of cases whose verdict changed vs
the committed baseline. The **hard gate** is intentionally narrow and loud: if
any case tagged ``faux_positif_reel`` becomes sanctionnable again, the run exits
non-zero so CI blocks the regression.

Two modes:

* ``--replay`` (default) — reproduce the run from ``fixtures.json`` (recorded
  embedding scores + nano raw verdicts). No gateway, no network, deterministic
  and free. This is what CI and the pytest suite use.
* ``--live`` — run the *real* funnel through ``bot.gateway`` (embeddings + nano).
  Costs a few cents; use it to re-measure after a prompt change and to refresh
  the fixtures (``--update-fixtures``). Requires a configured environment.

Either way the runner only **decides** — it never applies a sanction, never
writes to the DB. See docs/AUTOMOD.md (Évaluation) and docs/AUTOMOD_V2_PLAN.md
(Session 3).

Usage::

    python -m automod.eval.run                 # replay, compare to baseline
    python -m automod.eval.run --update-baseline
    python -m automod.eval.run --live --update-baseline --update-fixtures
    python -m automod.eval.run --json           # machine-readable report
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from automod import bareme as ab
from automod import constants as ac
from automod import nano
from automod import routing
from automod.blocklist import get_blocklist
from automod.prefiltre import pre_filter
from automod.schemas import Signal
from automod.triviaux import est_trivial

# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN_PATH = os.path.join(_HERE, "golden.jsonl")
FIXTURES_PATH = os.path.join(_HERE, "fixtures.json")
BASELINE_PATH = os.path.join(_HERE, "golden_baseline.json")

# Gravity ordering for the `gravite_min` check + reporting.
_GRAVITE_ORDER = {"basse": 0, "moyenne": 1, "haute": 2, "critique": 3}

# The barème inputs are held NEUTRAL for the golden set: a first offender, on the
# server long enough to dodge the fresh-account malus but not long enough to earn
# veteran clemency, at default guild severity. The barème has its own dedicated
# 36-case suite (tests/automod/test_bareme.py); here the cran is reported for
# context, not asserted, so the golden set stays focused on qualification.
_NEUTRAL_MEMBRE = ab.MembreInfo(anciennete_jours=30.0)
_NEUTRAL_SEVERITE = 3


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class GoldenCase:
    id: str
    contenu: str
    contexte: List[str] = field(default_factory=list)
    attendu: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    origine: str = ""
    # Session 5: an optional trusted relation block (familiarite + reaction_cible)
    # injected into nano on a --live run. Inert under --replay (nano isn't rerun;
    # the fixture already encodes the relation-aware verdict).
    relation: Optional[Dict[str, Any]] = None
    # Session 7: optional server precedents (past messages + human ruling +
    # similarity) injected into nano on a --live run. Inert under --replay (the
    # fixture already encodes the precedent-aware verdict), exactly like relation.
    precedents: Optional[List[Dict[str, Any]]] = None


@dataclass
class CaseResult:
    """The outcome of replaying/running one golden case."""
    id: str
    tags: List[str]
    predicted_sanctionnable: bool
    predicted_categorie: str
    predicted_gravite: str
    expected_sanctionnable: Optional[bool]
    expected_categorie: Optional[str]
    stop_reason: str = ""          # where the funnel stopped ("nano" = reached nano)
    rejet_grounding: Optional[str] = None
    cran: Optional[int] = None
    # Session 6: the difficulty the router would assign this case ("evident" →
    # nano, "ambigu" → mini). Reported for context (not in the baseline key), so a
    # --live run can show banter/ambigu cases routing to the smarter model.
    difficulte: Optional[str] = None
    # Evaluation flags.
    sanct_correct: bool = True     # predicted matches expected sanctionnable
    categorie_ok: Optional[bool] = None
    gravite_ok: Optional[bool] = None

    def key(self) -> Dict[str, Any]:
        """The stable, comparable projection stored in the baseline."""
        return {
            "sanctionnable": self.predicted_sanctionnable,
            "categorie": self.predicted_categorie,
        }


@dataclass
class EvalReport:
    results: List[CaseResult]
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    per_category: Dict[str, Dict[str, int]] = field(default_factory=dict)
    confusion: Dict[str, Dict[str, int]] = field(default_factory=dict)
    # Known-false-positive regressions (the CI gate).
    fp_reels_regressed: List[str] = field(default_factory=list)
    # Cases whose (sanctionnable, categorie) changed vs the baseline.
    changed_vs_baseline: List[Dict[str, Any]] = field(default_factory=list)
    mode: str = "replay"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": self.mode,
            "counts": {"tp": self.tp, "fp": self.fp, "fn": self.fn, "tn": self.tn,
                       "total": len(self.results)},
            "metrics": {"precision": round(self.precision, 4),
                        "recall": round(self.recall, 4),
                        "f1": round(self.f1, 4)},
            "per_category": self.per_category,
            "confusion": self.confusion,
            "fp_reels_regressed": self.fp_reels_regressed,
            "changed_vs_baseline": self.changed_vs_baseline,
            "cases": {r.id: r.key() for r in self.results},
        }


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_golden(path: str = GOLDEN_PATH) -> List[GoldenCase]:
    cases: List[GoldenCase] = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            obj = json.loads(raw)
            cases.append(GoldenCase(
                id=obj["id"],
                contenu=obj.get("contenu", ""),
                contexte=obj.get("contexte", []) or [],
                attendu=obj.get("attendu", {}) or {},
                tags=obj.get("tags", []) or [],
                origine=obj.get("origine", ""),
                relation=obj.get("relation"),
                precedents=obj.get("precedents"),
            ))
    return cases


def load_fixtures(path: str = FIXTURES_PATH) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Replay funnel (offline, deterministic)
# ---------------------------------------------------------------------------

def _apply_bareme(categorie: str, gravite: str, confiance: str) -> Optional[int]:
    """Neutral-context cran for a qualified verdict (reporting only)."""
    verdict = ab_verdict(categorie, gravite, confiance)
    res = ab.calculer(
        verdict, [], _NEUTRAL_MEMBRE,
        severite_guild=_NEUTRAL_SEVERITE, config=ab.ConfigBareme(),
    )
    return res.cran


def ab_verdict(categorie: str, gravite: str, confiance: str):
    """A tiny object the barème can read (`categorie`/`gravite`/`confiance`)."""
    return dataclasses.make_dataclass(
        "V", ["categorie", "gravite", "confiance"]
    )(categorie, gravite, confiance)


def replay_case(case: GoldenCase, fixture: Dict[str, Any],
                *, severity: int = _NEUTRAL_SEVERITE) -> CaseResult:
    """Reproduce the funnel for one case from its recorded fixture."""
    blocklist = get_blocklist()

    def _stopped(reason: str) -> CaseResult:
        return _mk_result(case, False, "", "basse", reason)

    # Step 1 — pre-filter.
    if not pre_filter(case.contenu, is_bot=False, is_system=False):
        return _stopped("prefiltre")
    # Step 2 — trivial allowlist.
    if est_trivial(case.contenu):
        return _stopped("trivial")

    # Step 3 — regex blocklist (routes straight to nano, skipping embedding).
    entry = blocklist.match(case.contenu)
    if entry is not None:
        signal = Signal(
            source=ac.SOURCE_REGEX, categorie=entry.categorie,
            score_confiance=ac.GRAVITE_TO_SCORE.get(entry.gravite_indicative, 0.7),
        )
    else:
        # Step 4 — embedding routing (score recorded in the fixture).
        embed = (fixture or {}).get("embedding") or {}
        score = float(embed.get("score", 0.0))
        threshold = ac.embedding_threshold_for(severity)
        if score < threshold:
            return _stopped("embedding_low")
        signal = Signal(source=ac.SOURCE_EMBEDDING, categorie="",
                        score_confiance=score)

    # Session 6: the difficulty the router would assign (nano vs mini). Purely
    # informational here — the replay reuses recorded verdicts, so routing never
    # changes the offline outcome; it lets a --live run steer the smart model.
    niveau = routing.difficulte(case.contenu, signal, case.relation, severity=severity)

    # Step 5 — nano (recorded raw verdict) + deterministic grounding guards.
    raw = (fixture or {}).get("nano")
    if raw is None:
        # No recorded verdict for a case that reaches nano: cannot replay it.
        # Treat as not-sanctionnable but flag the missing fixture in stop_reason.
        return _mk_result(case, False, "", "basse", "missing_nano_fixture",
                          difficulte=niveau)

    verdict = nano.parse_verdict(raw)
    verdict = nano.validate_grounding(verdict, case.contenu)

    sanctionnable = bool(verdict["sanctionnable"])
    categorie = verdict["categorie"] or ""
    gravite = verdict["gravite"] or "basse"
    cran = _apply_bareme(categorie, gravite, verdict["confiance"]) if sanctionnable else None
    return _mk_result(case, sanctionnable, categorie, gravite, "nano",
                      rejet=verdict.get("rejet_grounding"), cran=cran,
                      difficulte=niveau)


def _mk_result(case: GoldenCase, sanctionnable: bool, categorie: str,
               gravite: str, stop_reason: str, *,
               rejet: Optional[str] = None, cran: Optional[int] = None,
               difficulte: Optional[str] = None) -> CaseResult:
    exp_s = case.attendu.get("sanctionnable")
    exp_c = case.attendu.get("categorie")
    r = CaseResult(
        id=case.id, tags=list(case.tags),
        predicted_sanctionnable=sanctionnable,
        predicted_categorie=categorie,
        predicted_gravite=gravite,
        expected_sanctionnable=exp_s,
        expected_categorie=exp_c,
        stop_reason=stop_reason,
        rejet_grounding=rejet,
        cran=cran,
        difficulte=difficulte,
    )
    if exp_s is not None:
        r.sanct_correct = (sanctionnable == bool(exp_s))
    if exp_c is not None and sanctionnable:
        r.categorie_ok = (categorie == exp_c)
    gmin = case.attendu.get("gravite_min")
    if gmin is not None and sanctionnable:
        r.gravite_ok = _GRAVITE_ORDER.get(gravite, 0) >= _GRAVITE_ORDER.get(gmin, 0)
    return r


# ---------------------------------------------------------------------------
# Live funnel (real gateway) — used manually, never in CI
# ---------------------------------------------------------------------------

async def live_case(engine, case: GoldenCase, *, severity: int = _NEUTRAL_SEVERITE
                    ) -> Tuple[CaseResult, Dict[str, Any]]:
    """Run one case through the real engine; also return a refreshed fixture.

    Everything external goes through ``engine`` (``bot.gateway``): this respects
    the pipeline/module separation and the gateway rule. Returns the evaluation
    result plus a fixture entry (recorded score/verdict) so ``--update-fixtures``
    can keep the replay corpus current.
    """
    from automod.schemas import TargetMessage, ContextMessage, AuthorHistory

    ctx = [ContextMessage(id=str(i), author_id="0", content=c)
           for i, c in enumerate(case.contexte)]

    async def fetch_context(n: int) -> List[ContextMessage]:
        return ctx[-n:] if n < len(ctx) else list(ctx)

    target = TargetMessage(id=case.id, author_id="0", content=case.contenu)

    relation_fn = None
    if case.relation:
        async def relation_fn(_observe, _rel=case.relation):
            return dict(_rel)

    # Session 7: precedents are provided pre-matched by the golden case (message,
    # verdict, similarity). We wrap them as PrecedentMatch objects so --live
    # exercises the injection / shortcut paths without a DB or real embeddings of
    # past messages. Inert under --replay (this function is --live only).
    precedents_fn = None
    if case.precedents:
        from automod import precedents as _ap

        async def precedents_fn(_get_vec, _pre=case.precedents):
            return [
                _ap.PrecedentMatch(
                    _ap.Precedent(
                        id=str(i), message=p.get("message", ""),
                        verdict_humain=p.get("verdict_moderateurs", ""),
                        vector=[]),
                    similarite=float(p.get("similarite", 0.0)),
                )
                for i, p in enumerate(_pre)
            ]

    decision = await engine.analyze(
        target,
        guild_id=0,
        guild_name="eval",
        rules="",
        author_history=AuthorHistory(),
        fetch_context=fetch_context,
        severity=severity,
        relation_fn=relation_fn,
        precedents_fn=precedents_fn,
    )

    fixture: Dict[str, Any] = {}
    if decision is None:
        result = _mk_result(case, False, "", "basse", "stopped")
    else:
        sanctionnable = bool(decision.sanctionnable)
        cran = _apply_bareme(decision.categorie, decision.gravite,
                             decision.confiance) if sanctionnable else None
        result = _mk_result(
            case, sanctionnable, decision.categorie or "", decision.gravite,
            "nano", rejet=decision.rejet_grounding, cran=cran,
        )
        # Best-effort fixture refresh (the grounded verdict as nano-shaped dict).
        fixture["nano"] = {
            "besoin_plus_contexte": False, "nb_messages_supplementaires": 0,
            "sanctionnable": sanctionnable, "categorie": decision.categorie or "",
            "gravite": decision.gravite, "cible": decision.cible,
            "citation": decision.citation, "raison": decision.raison,
            "explication": decision.explication, "confiance": decision.confiance,
            "autres_messages_a_verifier": [],
        }
        if decision.signal_source == ac.SOURCE_EMBEDDING:
            fixture["embedding"] = {"score": round(decision.score_detecteur, 4)}
    return result, fixture


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _compute_metrics(report: EvalReport) -> None:
    tp = fp = fn = tn = 0
    per_cat: Dict[str, Dict[str, int]] = {}
    confusion: Dict[str, Dict[str, int]] = {}

    for r in report.results:
        exp = r.expected_sanctionnable
        pred = r.predicted_sanctionnable
        if exp is None:
            continue
        if exp and pred:
            tp += 1
        elif not exp and pred:
            fp += 1
        elif exp and not pred:
            fn += 1
        else:
            tn += 1

        # Per-category recall bucket (only where an expected category exists).
        if r.expected_categorie:
            cat = r.expected_categorie
            bucket = per_cat.setdefault(cat, {"support": 0, "correct": 0})
            bucket["support"] += 1
            if pred and r.categorie_ok:
                bucket["correct"] += 1
        # Confusion matrix: expected category (or "aucune") → predicted.
        exp_c = r.expected_categorie or ("__sanct__" if exp else "aucune")
        pred_c = r.predicted_categorie or ("__sanct__" if pred else "aucune")
        confusion.setdefault(exp_c, {})
        confusion[exp_c][pred_c] = confusion[exp_c].get(pred_c, 0) + 1

    report.tp, report.fp, report.fn, report.tn = tp, fp, fn, tn
    report.precision = tp / (tp + fp) if (tp + fp) else 1.0
    report.recall = tp / (tp + fn) if (tp + fn) else 1.0
    report.f1 = (2 * report.precision * report.recall
                 / (report.precision + report.recall)
                 if (report.precision + report.recall) else 0.0)
    report.per_category = per_cat
    report.confusion = confusion

    # The CI gate: any known real false positive that became sanctionnable.
    report.fp_reels_regressed = [
        r.id for r in report.results
        if "faux_positif_reel" in r.tags and r.predicted_sanctionnable
    ]


def _diff_baseline(report: EvalReport, baseline: Optional[Dict[str, Any]]) -> None:
    if not baseline:
        return
    base_cases = baseline.get("cases", {})
    for r in report.results:
        before = base_cases.get(r.id)
        if before is None:
            report.changed_vs_baseline.append(
                {"id": r.id, "change": "new", "after": r.key()})
            continue
        if before != r.key():
            report.changed_vs_baseline.append(
                {"id": r.id, "change": "verdict", "before": before, "after": r.key()})


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_eval(
    *,
    golden: Optional[List[GoldenCase]] = None,
    fixtures: Optional[Dict[str, Any]] = None,
    baseline: Optional[Dict[str, Any]] = None,
    severity: int = _NEUTRAL_SEVERITE,
) -> EvalReport:
    """Replay the golden set and build the report (pure, offline)."""
    golden = golden if golden is not None else load_golden()
    fixtures = fixtures if fixtures is not None else load_fixtures()

    results = [replay_case(c, fixtures.get(c.id, {}), severity=severity)
               for c in golden]
    report = EvalReport(results=results, mode="replay")
    _compute_metrics(report)
    _diff_baseline(report, baseline)
    return report


async def run_eval_live(engine, *, golden: Optional[List[GoldenCase]] = None,
                        baseline: Optional[Dict[str, Any]] = None,
                        severity: int = _NEUTRAL_SEVERITE
                        ) -> Tuple[EvalReport, Dict[str, Any]]:
    golden = golden if golden is not None else load_golden()
    results: List[CaseResult] = []
    refreshed: Dict[str, Any] = {}
    for c in golden:
        result, fixture = await live_case(engine, c, severity=severity)
        results.append(result)
        refreshed[c.id] = fixture
    report = EvalReport(results=results, mode="live")
    _compute_metrics(report)
    _diff_baseline(report, baseline)
    return report, refreshed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_baseline() -> Optional[Dict[str, Any]]:
    if not os.path.exists(BASELINE_PATH):
        return None
    with open(BASELINE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_baseline(report: EvalReport) -> None:
    with open(BASELINE_PATH, "w", encoding="utf-8") as f:
        json.dump(report.as_dict(), f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")


def _print_report(report: EvalReport) -> None:
    print(f"# Automod eval — mode={report.mode} — {len(report.results)} cases")
    print(f"  precision={report.precision:.3f}  recall={report.recall:.3f}  "
          f"f1={report.f1:.3f}  (tp={report.tp} fp={report.fp} "
          f"fn={report.fn} tn={report.tn})")
    if report.per_category:
        print("  per-category recall:")
        for cat, b in sorted(report.per_category.items()):
            print(f"    - {cat:26s} {b['correct']}/{b['support']}")
    # Session 6: how the router split the cases that reach the decider.
    routed = [r for r in report.results if r.difficulte]
    if routed:
        ambigu = sum(1 for r in routed if r.difficulte == ac.DIFFICULTE_AMBIGU)
        print(f"  routing: {ambigu} ambigu (mini) / {len(routed) - ambigu} "
              f"evident (nano)  [{len(routed)} reached decider]")
    if report.changed_vs_baseline:
        print(f"  changed vs baseline: {len(report.changed_vs_baseline)}")
        for ch in report.changed_vs_baseline:
            print(f"    ~ {ch['id']}: {ch.get('before')} -> {ch['after']}")
    else:
        print("  changed vs baseline: none")
    # Any qualification/gravity mismatches (soft — reported, not gating).
    mism = [r.id for r in report.results if r.expected_sanctionnable is not None
            and not r.sanct_correct]
    if mism:
        print(f"  sanctionnable mismatches: {', '.join(mism)}")
    if report.fp_reels_regressed:
        print(f"  !! FALSE-POSITIVE REGRESSIONS: {', '.join(report.fp_reels_regressed)}")


def _build_live_engine():
    """Construct the real engine against ``bot.gateway`` for a --live run.

    Kept intentionally light: a --live run needs a started gateway. If the
    environment is not configured this raises with a clear message — the CI /
    pytest path never calls it (it uses --replay).
    """
    import types
    from gateway import Gateway
    from automod.engine import AutomodEngine

    bot = types.SimpleNamespace()
    bot.db = None
    bot.gateway = Gateway()
    return bot, AutomodEngine(bot)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Automod regression runner")
    parser.add_argument("--live", action="store_true",
                        help="run the real funnel through bot.gateway (costs a few cents)")
    parser.add_argument("--replay", action="store_true",
                        help="replay from fixtures.json (default, free, deterministic)")
    parser.add_argument("--update-baseline", action="store_true",
                        help="write the current results as the new baseline")
    parser.add_argument("--update-fixtures", action="store_true",
                        help="(live only) refresh fixtures.json from the live run")
    parser.add_argument("--json", action="store_true",
                        help="emit the full report as JSON on stdout")
    parser.add_argument("--severity", type=int, default=_NEUTRAL_SEVERITE)
    args = parser.parse_args(argv)

    baseline = _load_baseline()
    golden = load_golden()

    if args.live:
        try:
            bot, engine = _build_live_engine()
        except Exception as e:  # noqa: BLE001
            print(f"--live requires a configured gateway environment: {e}")
            return 2

        async def _go():
            if hasattr(bot.gateway, "start"):
                await bot.gateway.start(bot)
            return await run_eval_live(engine, golden=golden, baseline=baseline,
                                       severity=args.severity)

        report, refreshed = asyncio.run(_go())
        if args.update_fixtures:
            # Merge non-empty refreshed entries over the existing fixtures.
            existing = load_fixtures()
            for cid, entry in refreshed.items():
                if entry:
                    existing[cid] = entry
            with open(FIXTURES_PATH, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=1, sort_keys=True)
                f.write("\n")
    else:
        report = run_eval(golden=golden, baseline=baseline, severity=args.severity)

    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        _print_report(report)

    if args.update_baseline:
        _write_baseline(report)
        print(f"baseline updated -> {BASELINE_PATH}")

    # Exit non-zero iff a known real false positive regressed (the CI gate).
    return 1 if report.fp_reels_regressed else 0


if __name__ == "__main__":
    raise SystemExit(main())
