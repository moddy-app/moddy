"""
Relation injection + reaction-window contract tests (session 5.2 / 5.3).

These drive the engine's ``_decide`` / ``analyze`` directly with a stubbed
``_judge`` so no gateway is touched. They assert the engine:

* calls the ``relation_fn`` lazily and passes its result into nano;
* tells the provider whether to observe the target (skipped for a flagrant
  regex hit — the 20 s wait exception);
* NEVER verdict-caches a relation-carrying message (the reaction is
  message-specific);
* awaits the provider (a real 20 s wait would be paid here) before judging.
"""

import asyncio
import json
import types

import pytest

from automod import constants
from automod import nano
from automod.engine import AutomodEngine
from automod.schemas import AuthorHistory, Decision, Signal, TargetMessage


async def _no_context(_n):
    return []


def _decision(target, signal, **over):
    base = dict(
        message_id=target.id, auteur_id=target.author_id, sanctionnable=True,
        actions=[], categorie="insulte", gravite="moyenne", raison="r",
        explication="e", confiance="high", signal_source=signal.source,
        score_detecteur=signal.score_confiance, citation="con", cible="membre",
    )
    base.update(over)
    return Decision(**base)


def _build(monkeypatch):
    """Engine with a stubbed ``_judge`` recording the ``relation`` it received."""
    engine = AutomodEngine(types.SimpleNamespace())
    seen = []

    async def fake_judge(self, target, signal, **kw):
        seen.append({"content": target.content, "relation": kw.get("relation")})
        return _decision(target, signal)

    monkeypatch.setattr(AutomodEngine, "_judge", fake_judge)
    return engine, seen


_COMMON = dict(
    guild_id=1, guild_name="g", rules="", author_history=AuthorHistory(),
    fetch_context=_no_context, correlation_id="c", severity=3,
    response_language="French",
)
# An embedding signal (below the skip score) → the reaction window is observed.
_EMBED_SIG = Signal(constants.SOURCE_EMBEDDING, "insulte", 0.6)
# A flagrant regex hit (>= skip score) → no wait, immediate verdict.
_REGEX_HIGH = Signal(constants.SOURCE_REGEX, "menace", 0.9)


class TestRelationInjection:
    async def test_relation_is_passed_to_nano(self, monkeypatch):
        engine, seen = _build(monkeypatch)

        async def relation_fn(observe):
            return {"familiarite": "haute", "reaction_cible": "banter_reciproque",
                    "observed": observe}

        await engine._decide(TargetMessage("1", "2", "t'es nul"), _EMBED_SIG,
                             relation_fn=relation_fn, **_COMMON)
        assert seen[0]["relation"]["familiarite"] == "haute"
        # An embedding signal is below the skip score → target IS observed.
        assert seen[0]["relation"]["observed"] is True

    async def test_flagrant_regex_skips_observation(self, monkeypatch):
        engine, seen = _build(monkeypatch)
        got = {}

        async def relation_fn(observe):
            got["observe"] = observe
            return {"familiarite": "aucune", "reaction_cible": "aucune"}

        await engine._decide(TargetMessage("1", "2", "je vais te tuer"), _REGEX_HIGH,
                             relation_fn=relation_fn, **_COMMON)
        assert got["observe"] is False           # death threat → no 20 s wait

    async def test_should_observe_reaction_rule(self):
        assert AutomodEngine._should_observe_reaction(_EMBED_SIG) is True
        assert AutomodEngine._should_observe_reaction(_REGEX_HIGH) is False
        # A low-gravity regex hit is still observed.
        low_regex = Signal(constants.SOURCE_REGEX, "insulte", 0.55)
        assert AutomodEngine._should_observe_reaction(low_regex) is True

    async def test_relation_message_is_never_cached(self, monkeypatch):
        engine, seen = _build(monkeypatch)

        async def relation_fn(observe):
            return {"familiarite": "aucune", "reaction_cible": "aucune"}

        # Same text, same guild, twice — but each carries a message-specific
        # relation, so both must reach nano (no cache hit).
        await engine._decide(TargetMessage("1", "2", "sale con"), _EMBED_SIG,
                             relation_fn=relation_fn, **_COMMON)
        await engine._decide(TargetMessage("2", "2", "sale con"), _EMBED_SIG,
                             relation_fn=relation_fn, **_COMMON)
        assert len(seen) == 2
        assert engine.verdict_cache_stats()["hits"] == 0

    async def test_no_relation_fn_still_caches(self, monkeypatch):
        engine, seen = _build(monkeypatch)
        await engine._decide(TargetMessage("1", "2", "sale con"), _EMBED_SIG, **_COMMON)
        await engine._decide(TargetMessage("2", "2", "sale con"), _EMBED_SIG, **_COMMON)
        assert len(seen) == 1                    # second served from cache
        assert engine.verdict_cache_stats()["hits"] == 1

    async def test_provider_is_awaited_before_judging(self, monkeypatch):
        """The engine awaits the provider (a real reaction wait) before nano."""
        engine, seen = _build(monkeypatch)
        order = []

        async def relation_fn(observe):
            await asyncio.sleep(0)               # stand-in for the 20 s window
            order.append("relation")
            return {"familiarite": "moyenne", "reaction_cible": "conflit_reciproque"}

        async def fake_judge(self, target, signal, **kw):
            order.append("judge")
            return _decision(target, signal)

        monkeypatch.setattr(AutomodEngine, "_judge", fake_judge)
        await engine._decide(TargetMessage("1", "2", "t'es lourd"), _EMBED_SIG,
                             relation_fn=relation_fn, **_COMMON)
        assert order == ["relation", "judge"]

    async def test_provider_failure_degrades_to_no_relation(self, monkeypatch):
        engine, seen = _build(monkeypatch)

        async def relation_fn(observe):
            raise RuntimeError("redis down")

        d = await engine._decide(TargetMessage("1", "2", "sale con"), _EMBED_SIG,
                                 relation_fn=relation_fn, **_COMMON)
        # A broken provider must not break moderation — nano runs with no block.
        assert d is not None
        assert seen[0]["relation"] is None


class TestPromptInjection:
    """§5.3: the relation object reaches the user payload and the system prompt
    gains the trusted RELATION block only when a relation is present."""

    def test_relation_in_user_payload(self):
        target = TargetMessage("1", "2", "t'es nul")
        relation = {"familiarite": "haute", "reaction_cible": "banter_reciproque",
                    "interactions_30j": 200, "reciprocite": True}
        payload = json.loads(nano.build_user_payload(
            target, AuthorHistory(), [], "abcd1234", relation=relation))
        assert payload["message_cible"]["relation"] == relation

    def test_no_relation_key_when_absent(self):
        target = TargetMessage("1", "2", "t'es nul")
        payload = json.loads(nano.build_user_payload(
            target, AuthorHistory(), [], "abcd1234"))
        assert "relation" not in payload["message_cible"]

    def test_system_prompt_block_only_when_relation_present(self):
        with_block = nano.build_system_prompt(
            "g", "", 0, "abcd1234", bloc_relation=nano.RELATION_PROMPT_BLOCK)
        without = nano.build_system_prompt("g", "", 0, "abcd1234")
        assert "RELATION (objective server data" in with_block
        assert "RELATION (objective server data" not in without
