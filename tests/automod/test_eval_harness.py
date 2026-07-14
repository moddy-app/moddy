"""
Tests for the automod evaluation harness (session 3).

These exercise the offline runner end to end on the committed golden set +
fixtures — no gateway, no DB, deterministic. They are the regression net the
harness exists to provide:

* the golden set is well-formed and large enough (≥ 60 cases);
* fixtures stay consistent with routing (a case that reaches nano has a verdict);
* a clean replay matches the committed baseline exactly (no silent drift);
* the CI gate fires: if the grounding guard is weakened, known real false
  positives become sanctionnable again and the runner exits non-zero.
"""

import json

import pytest

from automod.eval import run as R
from automod import nano


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def golden():
    return R.load_golden()


@pytest.fixture(scope="module")
def fixtures():
    return R.load_fixtures()


@pytest.fixture(scope="module")
def baseline():
    return R._load_baseline()


# --------------------------------------------------------------------------- #
# Golden set integrity
# --------------------------------------------------------------------------- #

class TestGoldenSet:
    def test_at_least_60_cases(self, golden):
        assert len(golden) >= 60

    def test_ids_unique_and_schema_valid(self, golden):
        ids = set()
        for c in golden:
            assert c.id and c.id not in ids, f"duplicate/empty id {c.id!r}"
            ids.add(c.id)
            assert isinstance(c.contenu, str)
            assert isinstance(c.tags, list)
            assert "sanctionnable" in c.attendu, f"{c.id}: attendu needs sanctionnable"
            assert isinstance(c.attendu["sanctionnable"], bool)

    def test_has_known_false_positives_and_true_positives(self, golden):
        fp_reels = [c for c in golden if "faux_positif_reel" in c.tags]
        positives = [c for c in golden if c.attendu.get("sanctionnable")]
        assert len(fp_reels) >= 10, "need a real corpus of known false positives"
        assert len(positives) >= 10, "need a real corpus of true positives"

    def test_seed_diversity(self, golden):
        all_tags = {t for c in golden for t in c.tags}
        # The plan seeds: few-shots, real FPs, injections, English, self-harm…
        for expected in ("few_shot", "injection", "english", "self_harm", "banter"):
            assert expected in all_tags, f"golden set missing '{expected}' cases"

    def test_every_pipeline_case_has_a_fixture(self, golden, fixtures):
        """A case that reaches nano must carry a recorded verdict for replay."""
        from automod.prefiltre import pre_filter
        from automod.triviaux import est_trivial
        from automod.blocklist import get_blocklist
        bl = get_blocklist()
        for c in golden:
            fx = fixtures.get(c.id, {})
            if not pre_filter(c.contenu, is_bot=False, is_system=False):
                continue
            if est_trivial(c.contenu):
                continue
            if bl.match(c.contenu) is not None:
                assert "nano" in fx, f"{c.id}: blocklist route needs a nano fixture"


# --------------------------------------------------------------------------- #
# Replay correctness
# --------------------------------------------------------------------------- #

class TestReplay:
    def test_clean_replay_matches_baseline(self, golden, fixtures, baseline):
        assert baseline is not None, "golden_baseline.json must be committed"
        report = R.run_eval(golden=golden, fixtures=fixtures, baseline=baseline)
        assert report.changed_vs_baseline == [], \
            f"replay drifted from baseline: {report.changed_vs_baseline}"

    def test_no_known_false_positive_regressions(self, golden, fixtures):
        report = R.run_eval(golden=golden, fixtures=fixtures)
        assert report.fp_reels_regressed == []

    def test_metrics_are_sane(self, golden, fixtures):
        report = R.run_eval(golden=golden, fixtures=fixtures)
        # The seeded corpus is internally consistent → precision/recall are high.
        assert report.tp >= 10
        assert report.precision >= 0.95
        assert report.recall >= 0.95
        assert report.fp == 0  # no benign case predicted sanctionnable

    def test_grounding_caught_cases_are_not_sanctionnable(self, golden, fixtures):
        by_id = {c.id: c for c in golden}
        report = R.run_eval(golden=golden, fixtures=fixtures)
        res = {r.id: r for r in report.results}
        # These fixtures record a nano verdict that WOULD sanction, but a
        # deterministic grounding guard must void it.
        for cid in ("gs-0040", "gs-0041", "gs-0042", "gs-0043"):
            assert cid in by_id
            assert res[cid].predicted_sanctionnable is False, \
                f"{cid} should be grounding-rejected"
            assert res[cid].rejet_grounding, f"{cid} should carry a rejection motif"

    def test_true_positive_roundtrip(self, golden, fixtures):
        report = R.run_eval(golden=golden, fixtures=fixtures)
        res = {r.id: r for r in report.results}
        # A genuine insult with a valid verbatim citation survives grounding and
        # is qualified as an insult.
        assert res["gs-0003"].predicted_sanctionnable is True
        assert res["gs-0003"].predicted_categorie == "insulte"
        assert res["gs-0003"].cran is not None


# --------------------------------------------------------------------------- #
# The CI gate
# --------------------------------------------------------------------------- #

class TestRegressionGate:
    def test_main_exit_zero_on_clean_replay(self):
        assert R.main(["--replay"]) == 0

    def test_weakened_grounding_trips_the_gate(self, golden, fixtures, monkeypatch):
        # Simulate a regression: disable the deterministic grounding guard.
        monkeypatch.setattr(nano, "validate_grounding", lambda v, c: v)
        report = R.run_eval(golden=golden, fixtures=fixtures)
        assert report.fp_reels_regressed, \
            "disabling grounding must resurrect known false positives"
        # And that is exactly what makes the CLI exit non-zero.
        assert R.main(["--replay"]) == 1


# --------------------------------------------------------------------------- #
# Difficulty routing (session 6)
# --------------------------------------------------------------------------- #

class TestRouting:
    def test_banter_cases_route_to_mini(self, golden, fixtures):
        """Banter / relation cases that reach the decider are judged by mini.

        This is the session-6 lever: the subtle "humour vs harm" cases are exactly
        the ones the smart model should see. It is wired into the runner so a
        --live run can show the gain; here we assert the routing itself.
        """
        report = R.run_eval(golden=golden, fixtures=fixtures)
        res = {r.id: r for r in report.results}
        for cid in ("gs-0061", "gs-0205"):  # banter (one with a relation block)
            assert cid in res
            assert res[cid].difficulte == "ambigu", \
                f"{cid} (banter) should route to mini"

    def test_routing_reports_only_for_decider_cases(self, golden, fixtures):
        report = R.run_eval(golden=golden, fixtures=fixtures)
        for r in report.results:
            # A case stopped before the decider carries no difficulty label.
            if r.stop_reason != "nano":
                assert r.difficulte is None
            else:
                assert r.difficulte in ("evident", "ambigu")

    def test_routing_does_not_touch_the_baseline(self, golden, fixtures, baseline):
        """The router is informational offline — it must not move any verdict."""
        report = R.run_eval(golden=golden, fixtures=fixtures, baseline=baseline)
        assert report.changed_vs_baseline == []


# --------------------------------------------------------------------------- #
# Baseline round-trip
# --------------------------------------------------------------------------- #

class TestBaseline:
    def test_report_serialises(self, golden, fixtures):
        report = R.run_eval(golden=golden, fixtures=fixtures)
        blob = json.dumps(report.as_dict())
        assert "cases" in json.loads(blob)
