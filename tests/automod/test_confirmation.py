"""Difficulty routing + heavy-sanction confirmation tests (session 6).

Covers the three moving parts that must hold for §6 to be safe:

* the engine routes an ``ambigu`` message to **mini** (and an ``evident`` one to
  nano), stamping ``decideur`` and weighing the budget ×4 for mini;
* ``engine.confirm_heavy`` runs a **binary mini senior review** (gateway-backed,
  fail-safe) and counts ×4 in the budget;
* ``bareme.appliquer_non_confirme`` makes a heavy sanction that is **not**
  confirmed *impossible* — it is capped to a bounded mute, never a ban.

A tiny fake gateway records each chat call; a fake Redis is the budget authority.
No network, no Discord.
"""

import types

import pytest

from automod import bareme as ab
from automod import constants as ac
from automod import nano
from automod.engine import AutomodEngine
from automod.schemas import AuthorHistory, Decision, Signal, TargetMessage

from _redis_stub import FakeRedis


async def _no_context(_n):
    return []


# --------------------------------------------------------------------------- #
# Fake gateway
# --------------------------------------------------------------------------- #

class _FakeAI:
    """Records chat calls and returns a canned response keyed by call_type."""

    def __init__(self, responses):
        self.responses = responses          # call_type -> dict
        self.calls = []

    async def chat(self, *, system, user, model, json_mode, temperature,
                   max_tokens, quota, call_type, correlation_id, metadata):
        self.calls.append({"model": model, "call_type": call_type,
                           "system": system, "user": user, "quota": quota})
        return dict(self.responses.get(call_type, {}))


def _engine(responses, *, redis=None):
    ai = _FakeAI(responses)
    bot = types.SimpleNamespace(gateway=types.SimpleNamespace(ai=ai), redis=redis)
    return AutomodEngine(bot), ai


def _nano_verdict(citation, categorie="insulte", gravite="haute"):
    return {
        "besoin_plus_contexte": False, "nb_messages_supplementaires": 0,
        "sanctionnable": True, "categorie": categorie, "gravite": gravite,
        "citation": citation, "cible": "membre", "raison": "insulte directe",
        "explication": "x", "confiance": "high", "autres_messages_a_verifier": [],
    }


_COMMON = dict(
    guild_id=1, guild_name="g", rules="", author_history=AuthorHistory(),
    fetch_context=_no_context, correlation_id="c", severity=3,
    response_language="French",
)


# --------------------------------------------------------------------------- #
# Routing: which model decides
# --------------------------------------------------------------------------- #

class TestRoutingModelSelection:
    async def test_evident_uses_nano(self):
        engine, ai = _engine({ac.CALL_TYPE_DECISION: _nano_verdict("nul et desagreable")})
        sig = Signal(ac.SOURCE_EMBEDDING, "insulte", 0.90)  # comfortably above thr
        target = TargetMessage("1", "2", "tu es vraiment nul et desagreable ici")
        d = await engine._decide(target, sig, **_COMMON)
        assert d.sanctionnable and d.decideur == "nano"
        assert ai.calls[-1]["call_type"] == ac.CALL_TYPE_DECISION
        assert ai.calls[-1]["model"] == ac.NANO_MODEL

    async def test_ambigu_short_uses_mini(self):
        engine, ai = _engine({ac.CALL_TYPE_DECISION_MINI: _nano_verdict("con")})
        sig = Signal(ac.SOURCE_EMBEDDING, "insulte", 0.90)
        d = await engine._decide(TargetMessage("1", "2", "t'es con"), sig, **_COMMON)
        assert d.sanctionnable and d.decideur == "mini"
        assert ai.calls[-1]["call_type"] == ac.CALL_TYPE_DECISION_MINI
        assert ai.calls[-1]["model"] == ac.MINI_MODEL

    async def test_mini_call_weighs_four_in_budget(self):
        redis = FakeRedis()
        engine, ai = _engine(
            {ac.CALL_TYPE_DECISION_MINI: _nano_verdict("con")}, redis=redis)
        await engine._decide(TargetMessage("1", "2", "t'es con"),
                             Signal(ac.SOURCE_EMBEDDING, "insulte", 0.90), **_COMMON)
        assert await engine._budget_used(1) == ac.MINI_BUDGET_WEIGHT

    async def test_evident_call_weighs_one_in_budget(self):
        redis = FakeRedis()
        engine, ai = _engine(
            {ac.CALL_TYPE_DECISION: _nano_verdict("nul et desagreable")}, redis=redis)
        await engine._decide(
            TargetMessage("1", "2", "tu es vraiment nul et desagreable ici"),
            Signal(ac.SOURCE_EMBEDDING, "insulte", 0.90), **_COMMON)
        assert await engine._budget_used(1) == 1


# --------------------------------------------------------------------------- #
# Confirmation parsing / orchestration (pure nano helpers)
# --------------------------------------------------------------------------- #

class TestParseConfirmation:
    def test_valid_true(self):
        out = nano.parse_confirmation({"confirme": True, "motif": "clear threat"})
        assert out == {"confirme": True, "motif": "clear threat"}

    def test_valid_false(self):
        assert nano.parse_confirmation({"confirme": False, "motif": "ambiguous"})["confirme"] is False

    @pytest.mark.parametrize("raw", [None, {}, "nope", {"confirme": "maybe"}, 42])
    def test_malformed_is_a_refusal(self, raw):
        assert nano.parse_confirmation(raw)["confirme"] is False

    def test_stringy_true_coerced(self):
        assert nano.parse_confirmation({"confirme": "true"})["confirme"] is True

    async def test_confirmer_failsafe_on_error(self):
        async def boom(_s, _u):
            raise RuntimeError("network")
        out = await nano.confirmer(
            TargetMessage("1", "2", "je vais te tuer"),
            {"categorie": "menace", "gravite": "haute", "citation": "je vais te tuer"},
            chat_fn=boom, fetch_context=_no_context)
        assert out["confirme"] is False


# --------------------------------------------------------------------------- #
# engine.confirm_heavy
# --------------------------------------------------------------------------- #

class TestConfirmHeavy:
    def _decision(self):
        return Decision(
            message_id="10", auteur_id="2", sanctionnable=True, actions=[],
            categorie="menace", gravite="haute", raison="r", explication="e",
            confiance="high", signal_source=ac.SOURCE_REGEX, score_detecteur=0.85,
            citation="je vais te tuer", cible="membre", decideur="nano",
        )

    async def test_confirmed(self):
        engine, ai = _engine({ac.CALL_TYPE_CONFIRM: {"confirme": True, "motif": "ok"}})
        confirme, motif = await engine.confirm_heavy(
            TargetMessage("10", "2", "je vais te tuer"), self._decision(),
            guild_id=1, fetch_context=_no_context)
        assert confirme is True and motif == "ok"
        assert ai.calls[-1]["call_type"] == ac.CALL_TYPE_CONFIRM
        assert ai.calls[-1]["model"] == ac.MINI_MODEL

    async def test_refused(self):
        engine, ai = _engine({ac.CALL_TYPE_CONFIRM: {"confirme": False, "motif": "doubt"}})
        confirme, _ = await engine.confirm_heavy(
            TargetMessage("10", "2", "je vais te tuer"), self._decision(),
            guild_id=1, fetch_context=_no_context)
        assert confirme is False

    async def test_confirmation_weighs_four_in_budget(self):
        redis = FakeRedis()
        engine, ai = _engine(
            {ac.CALL_TYPE_CONFIRM: {"confirme": True, "motif": "ok"}}, redis=redis)
        await engine.confirm_heavy(
            TargetMessage("10", "2", "je vais te tuer"), self._decision(),
            guild_id=1, fetch_context=_no_context)
        assert await engine._budget_used(1) == ac.MINI_BUDGET_WEIGHT


# --------------------------------------------------------------------------- #
# bareme downgrade — the "heavy without confirmation is impossible" guarantee
# --------------------------------------------------------------------------- #

def _heavy_bareme(cran):
    actions, duree = ab.ladder_for(cran)
    return ab.ResultatBareme(
        cran=cran, actions=actions, duree_heures=duree, categorie="menace",
        gravite="haute", points_recidive=0.0,
        composantes=[ab.Composante("plancher", cran, "menace/haute")],
        needs_review=cran >= 6,
    )


class TestUnconfirmedDowngrade:
    @pytest.mark.parametrize("cran", [6, 7])
    def test_heavy_capped_to_mute_48h(self, cran):
        res = ab.appliquer_non_confirme(_heavy_bareme(cran))
        assert res.cran == ac.CONFIRM_UNCONFIRMED_CRAN == 4
        # An unconfirmed heavy sanction can NEVER be a ban / max mute.
        assert "ban" not in res.actions
        assert res.duree_heures == 48
        assert res.needs_review is True
        assert any(c.code == "confirmation_refusee" for c in res.composantes)

    def test_below_cap_unchanged(self):
        base = _heavy_bareme(3)
        res = ab.appliquer_non_confirme(base)
        assert res is base            # nothing to downgrade

    def test_breakdown_still_sums_to_cran(self):
        res = ab.appliquer_non_confirme(_heavy_bareme(7))
        assert sum(c.delta for c in res.composantes) == res.cran
