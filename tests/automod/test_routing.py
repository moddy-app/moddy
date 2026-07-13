"""Difficulty-router tests (session 6.1/6.2) — ``automod.routing.difficulte``.

The router is a **pure**, free heuristic: given the message text, the routing
signal and the optional trusted relation block, it labels a case ``evident``
(cheap nano) or ``ambigu`` (smart mini). These table-driven cases pin every
branch of §6.1 so a later tweak to the thresholds is a visible, reviewed change.
"""

import pytest

from automod import constants as ac
from automod import routing
from automod.schemas import Signal


def _regex(score):
    return Signal(source=ac.SOURCE_REGEX, categorie="insulte", score_confiance=score)


def _embed(score):
    return Signal(source=ac.SOURCE_EMBEDDING, categorie="menace", score_confiance=score)


# threshold at severity 3 ≈ 0.47. evident-regex margin 0.15 → ≥ 0.62 evident.
# gray zone ±0.05 around the threshold → ambigu. short ≤ 3 words → ambigu.
_HAUTE = {"familiarite": ac.FAMILIARITE_HAUTE}
_MOYENNE = {"familiarite": ac.FAMILIARITE_MOYENNE}
_AUCUNE = {"familiarite": ac.FAMILIARITE_AUCUNE}


class TestDifficulte:
    @pytest.mark.parametrize("contenu,signal,relation,expected", [
        # 1. Flagrant regex hit (haute 0.85 ≥ 0.62) → evident, even short.
        ("sale connard", _regex(0.85), None, ac.DIFFICULTE_EVIDENT),
        # 2. A weak regex hit (basse 0.55 < 0.62) on a longer benign-ish line is
        #    not flagrant → falls through; no other ambiguity signal → evident.
        ("tu es un peu lourd la franchement mec", _regex(0.55), None, ac.DIFFICULTE_EVIDENT),
        # 3. Very short message → ambigu (little to judge cheaply).
        ("t'es con", _embed(0.90), None, ac.DIFFICULTE_AMBIGU),
        # 4. High familiarity → banter plausible → ambigu.
        ("t'es vraiment trop nul franchement", _embed(0.90), _HAUTE, ac.DIFFICULTE_AMBIGU),
        # 5. Medium familiarity → ambigu too.
        ("t'es vraiment trop nul franchement", _embed(0.90), _MOYENNE, ac.DIFFICULTE_AMBIGU),
        # 6. Laughter marker → probably banter → ambigu.
        ("t'es vraiment trop nul mdr franchement", _embed(0.90), _AUCUNE, ac.DIFFICULTE_AMBIGU),
        # 7. Emoji laughter marker → ambigu.
        ("retourne jouer tu es mauvais 😂 vraiment", _embed(0.90), None, ac.DIFFICULTE_AMBIGU),
        # 8. Grey-zone embedding score (|0.48 − 0.47| ≤ 0.05) → ambigu.
        ("message parfaitement anodin ou presque voila", _embed(0.48), None, ac.DIFFICULTE_AMBIGU),
        # 9. Clearly-above-threshold embedding, long, strangers, no laughter → evident.
        ("je vais te retrouver et te faire regretter tes paroles", _embed(0.90), _AUCUNE, ac.DIFFICULTE_EVIDENT),
        # 10. Regex just under the evident margin (0.60 < 0.62), longer text,
        #     no other signal → evident (not flagrant, but nothing ambiguous).
        ("tu racontes vraiment n'importe quoi tout le temps ici", _regex(0.60), None, ac.DIFFICULTE_EVIDENT),
        # 11. No relation dict at all behaves like "no banter presumption".
        ("tu es vraiment quelqu'un de tres desagreable ici", _embed(0.90), None, ac.DIFFICULTE_EVIDENT),
    ])
    def test_classification(self, contenu, signal, relation, expected):
        assert routing.difficulte(contenu, signal, relation, severity=3) == expected

    def test_flagrant_regex_beats_short_and_laughter(self):
        # A flagrant regex hit wins even if the message is short / has laughter.
        assert routing.difficulte("crève mdr", _regex(0.85), _HAUTE, severity=3) \
            == ac.DIFFICULTE_EVIDENT

    def test_severity_shifts_gray_zone(self):
        # At severity 5 the threshold drops (~0.35); a 0.48 score is no longer in
        # the grey zone, so a long stranger message routes evident.
        long_txt = "message parfaitement anodin ou presque voila vraiment"
        assert routing.difficulte(long_txt, _embed(0.48), None, severity=5) \
            == ac.DIFFICULTE_EVIDENT

    def test_helpers(self):
        assert routing.is_ambigu(ac.DIFFICULTE_AMBIGU) is True
        assert routing.is_ambigu(ac.DIFFICULTE_EVIDENT) is False
        # ambigu doubles the initial context (capped at CONTEXTE_MAX).
        assert routing.contexte_initial_for(ac.DIFFICULTE_EVIDENT) == ac.CONTEXTE_INITIAL
        assert routing.contexte_initial_for(ac.DIFFICULTE_AMBIGU) == min(
            ac.CONTEXTE_INITIAL * ac.AMBIGU_CONTEXT_MULTIPLIER, ac.CONTEXTE_MAX)
