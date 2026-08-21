"""Tests for the deterministic sanction barème (``automod.bareme``).

The barème is pure (no I/O), so everything here is a dry table-driven check:
floor per (category, gravity), recidivism decay + weighting, confidence caps,
veteran clemency, fresh-account malus, the guild ``max_action`` ceiling, the
category kill-switch and the 0–7 bounds. Session 2 requires ≥15 cases.

See docs/AUTOMOD_AI.md §2bis and docs/archive/AUTOMOD_V2_PLAN.md (Session 2, §2.7).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from automod import bareme
from automod.bareme import (
    SanctionPassee, MembreInfo, ConfigBareme, calculer,
    points_actifs, crans_recidive, ladder_for,
)


# --- Harness ---------------------------------------------------------------- #

NOW = datetime(2026, 7, 12, tzinfo=timezone.utc)


@dataclass
class _Verdict:
    """Minimal stand-in for a Decision (what the barème actually reads)."""
    categorie: str
    gravite: str
    confiance: str = "high"


def _veteran(days: float = 400.0) -> MembreInfo:
    return MembreInfo(anciennete_jours=days)


def _member(days: float = 30.0) -> MembreInfo:
    # A plain member: old enough to escape the fresh-account malus (<7 d) but
    # not a clean veteran (≥90 d), so neither modulator fires by default.
    return MembreInfo(anciennete_jours=days)


def _past(cat: str, grav: str, days_ago: float, source: str = "automod") -> SanctionPassee:
    return SanctionPassee(
        categorie=cat, gravite=grav,
        date=NOW - timedelta(days=days_ago), source_fiabilite=source,
    )


def _cran(verdict, sanctions=None, membre=None, *, severity=3, config=None):
    return calculer(
        verdict, sanctions or [], membre or _member(),
        severite_guild=severity, config=config, now=NOW,
    ).cran


# --- Ladder ----------------------------------------------------------------- #

class TestLadder:
    def test_deletion_always_present(self):
        for c in range(0, 8):
            actions, _ = ladder_for(c)
            assert "supprimer" in actions

    def test_ladder_bounds_are_clamped(self):
        assert ladder_for(-3) == ladder_for(0)
        assert ladder_for(99) == ladder_for(7)

    def test_ban_is_top_rung(self):
        actions, duree = ladder_for(7)
        assert "ban" in actions and duree == 0

    def test_mute_durations(self):
        assert ladder_for(2)[1] == 2
        assert ladder_for(6)[1] == 672


# --- Floor (plancher) ------------------------------------------------------- #

class TestPlancher:
    @pytest.mark.parametrize("cat,grav,expected", [
        ("insulte", "moyenne", 1),
        ("insulte", "critique", 3),
        ("menace", "haute", 6),
        ("menace", "critique", 7),
        ("haine_discrimination", "moyenne", 4),
        ("doxxing", "basse", 4),
        ("violation_indications", "basse", 0),
    ])
    def test_floor_first_offence_no_history(self, cat, grav, expected):
        # A plain member, severity 3, no history → the cran is exactly the floor.
        assert _cran(_Verdict(cat, grav)) == expected

    def test_unknown_category_defaults_to_deletion(self):
        assert _cran(_Verdict("", "moyenne")) == 0
        assert _cran(_Verdict("banana", "haute")) == 0


# --- Recidivism ------------------------------------------------------------- #

class TestRecidive:
    def test_points_thresholds(self):
        assert crans_recidive(0) == 0
        assert crans_recidive(4.9) == 0
        assert crans_recidive(5) == 1
        assert crans_recidive(15) == 2
        assert crans_recidive(40) == 3

    def test_decay_halves_points_at_half_life(self):
        # A single "haute" sanction (7 pts) 45 days ago ≈ half its value.
        recent = points_actifs([_past("insulte", "haute", 0)], "menace", NOW)
        aged = points_actifs([_past("insulte", "haute", bareme.DEMI_VIE_JOURS)], "menace", NOW)
        assert recent == pytest.approx(7.0, abs=1e-6)
        assert aged == pytest.approx(3.5, rel=1e-3)

    def test_accepted_appeal_weighs_zero(self):
        pts = points_actifs(
            [_past("insulte", "critique", 1, source="automod_appel_accepte")],
            "insulte", NOW,
        )
        assert pts == 0.0

    def test_same_category_multiplier(self):
        same = points_actifs([_past("insulte", "moyenne", 0)], "insulte", NOW)
        other = points_actifs([_past("insulte", "moyenne", 0)], "menace", NOW)
        assert same == pytest.approx(other * bareme.MULT_MEME_CATEGORIE)

    def test_manual_source_weighs_more_than_automod(self):
        manual = points_actifs([_past("insulte", "moyenne", 0, source="manuel")], "x", NOW)
        auto = points_actifs([_past("insulte", "moyenne", 0, source="automod")], "x", NOW)
        assert manual > auto

    def test_recidive_escalates_the_cran(self):
        # Two recent same-category "moyenne" automod mutes: 3 * 1.5 * 2 = 9 pts → +1 cran.
        history = [_past("insulte", "moyenne", 2), _past("insulte", "moyenne", 5)]
        cran = _cran(_Verdict("insulte", "moyenne"), history)
        assert cran == 2  # floor 1 + recidive 1

    def test_old_isolated_warn_changes_nothing(self):
        # A single "basse" sanction ~60 days ago is well under 5 pts → +0.
        history = [_past("insulte", "basse", 60)]
        assert _cran(_Verdict("insulte", "moyenne"), history) == 1


# --- Modulators ------------------------------------------------------------- #

class TestModulateurs:
    def test_severity_1_softens_and_5_hardens(self):
        base = _cran(_Verdict("insulte", "haute"))               # floor 2
        assert _cran(_Verdict("insulte", "haute"), severity=1) == base - 1
        assert _cran(_Verdict("insulte", "haute"), severity=5) == base + 1

    def test_low_confidence_caps_at_warn(self):
        v = _Verdict("menace", "haute", confiance="low")         # floor 6
        assert _cran(v) == 1

    def test_medium_confidence_caps_at_mute48(self):
        v = _Verdict("menace", "critique", confiance="medium")   # floor 7
        assert _cran(v) == 4

    def test_veteran_bonus_on_low_gravity(self):
        # Clean veteran, low gravity → one cran of clemency.
        assert _cran(_Verdict("insulte", "moyenne"), membre=_veteran()) == 0

    def test_veteran_bonus_refused_on_high_gravity(self):
        # A veteran who makes a death threat is still banned — no clemency.
        assert _cran(_Verdict("menace", "haute"), membre=_veteran()) == 6

    def test_veteran_bonus_refused_with_history(self):
        history = [_past("insulte", "moyenne", 3)]
        assert _cran(_Verdict("insulte", "moyenne"), history, _veteran()) == 1

    def test_fresh_account_malus(self):
        assert _cran(_Verdict("insulte", "moyenne"), membre=_member(days=2)) == 2

    def test_veteran_bonus_never_on_sensitive_category(self):
        # harcelement_sexuel basse floor is 3; a veteran gets no discount here
        # only because it is a sensitive category (gravity is low).
        assert _cran(_Verdict("harcelement_sexuel", "basse"), membre=_veteran()) == 3


# --- Guild ceiling + kill-switch ------------------------------------------- #

class TestConfig:
    def test_max_action_warn_caps_everything_to_warn(self):
        cfg = ConfigBareme(max_action="warn")
        assert _cran(_Verdict("menace", "critique"), config=cfg) == 1

    def test_max_action_mute_forbids_ban(self):
        cfg = ConfigBareme(max_action="mute")
        assert _cran(_Verdict("menace", "critique"), config=cfg) == 6

    def test_disabled_category_is_deletion_only(self):
        cfg = ConfigBareme(categories_desactivees=("violation_indications",))
        assert _cran(_Verdict("violation_indications", "critique"), config=cfg) == 0

    def test_deletion_survives_the_ceiling(self):
        # Even bridled to "warn", the offending message is still deleted.
        cfg = ConfigBareme(max_action="warn")
        result = calculer(_Verdict("doxxing", "critique"), [], _member(),
                          config=cfg, now=NOW)
        assert "supprimer" in result.actions


# --- Bounds + breakdown ----------------------------------------------------- #

class TestResultat:
    def test_cran_never_below_zero(self):
        cfg = ConfigBareme(max_action="warn")
        result = calculer(_Verdict("violation_indications", "basse"), [], _veteran(),
                          severite_guild=1, config=cfg, now=NOW)
        assert result.cran == 0

    def test_breakdown_sum_holds_even_when_clamped(self):
        # floor 0 + severity(-1) would go to -1; the clamp brings it back to 0
        # and records a "borne" component so the breakdown still sums to the cran.
        result = calculer(_Verdict("violation_indications", "basse"), [], _member(),
                          severite_guild=1, now=NOW)
        assert result.cran == 0
        assert sum(c.delta for c in result.composantes) == result.cran

    def test_breakdown_sums_to_final_cran(self):
        # floor 1 + recidive 1 + fresh malus 1 = cran 3.
        history = [_past("insulte", "moyenne", 2), _past("insulte", "moyenne", 5)]
        result = calculer(_Verdict("insulte", "moyenne"), history, _member(days=2),
                          now=NOW)
        total = sum(c.delta for c in result.composantes)
        assert total == result.cran
        codes = [c.code for c in result.composantes]
        assert "plancher" in codes and "recidive" in codes and "compte_recent" in codes

    def test_needs_review_flag_on_heavy_automod_sanction(self):
        result = calculer(_Verdict("menace", "critique"), [], _member(), now=NOW)
        assert result.cran == 7
        assert result.needs_review is True

    def test_no_review_flag_on_light_sanction(self):
        result = calculer(_Verdict("insulte", "moyenne"), [], _member(), now=NOW)
        assert result.needs_review is False
