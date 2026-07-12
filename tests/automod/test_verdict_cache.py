"""Verdict-cache tests for ``automod.engine.AutomodEngine`` (session 4.1).

The nano *qualification* is memoised per (guild, collapsed-text) with a short TTL
and single-flight, so a copypasta raid that reaches nano costs one chat call, not
N. These tests drive ``_decide`` directly (bypassing routing) with a stubbed
``_judge`` so no gateway is touched, and assert on the number of real judge calls.
"""

import asyncio
import types

import pytest

from automod import constants
from automod.engine import AutomodEngine
from automod.schemas import AuthorHistory, Decision, Signal, TargetMessage


async def _no_context(_n):
    return []


def _make_decision(target: TargetMessage, signal: Signal, **over) -> Decision:
    base = dict(
        message_id=target.id, auteur_id=target.author_id, sanctionnable=True,
        actions=[], categorie="insulte", gravite="moyenne", raison="r",
        explication="e", confiance="high", signal_source=signal.source,
        score_detecteur=signal.score_confiance, citation="con", cible="membre",
    )
    base.update(over)
    return Decision(**base)


def _build(monkeypatch, *, slow: asyncio.Event = None):
    """Engine with a stubbed ``_judge`` recording each real call."""
    engine = AutomodEngine(types.SimpleNamespace())
    calls = []

    async def fake_judge(self, target, signal, **kw):
        calls.append({"content": target.content, "guild_id": kw.get("guild_id"),
                      "agregat_de": kw.get("agregat_de")})
        if slow is not None:
            await slow.wait()
        agg = kw.get("agregat_de")
        return _make_decision(
            target, signal,
            agregat_de=[str(x) for x in agg] if agg else [],
            agregat_contenu=target.content if agg else "",
        )

    monkeypatch.setattr(AutomodEngine, "_judge", fake_judge)
    return engine, calls


_COMMON = dict(
    guild_id=1, guild_name="g", rules="", author_history=AuthorHistory(),
    fetch_context=_no_context, correlation_id="c", severity=3,
    response_language="French",
)
_SIG = Signal(constants.SOURCE_REGEX, "insultes", 0.7)


class TestVerdictCache:
    async def test_identical_text_same_guild_calls_nano_once(self, monkeypatch):
        engine, calls = _build(monkeypatch)
        d1 = await engine._decide(TargetMessage("10", "2", "sale con"), _SIG, **_COMMON)
        d2 = await engine._decide(TargetMessage("11", "2", "sale con"), _SIG, **_COMMON)
        assert d1.sanctionnable and d2.sanctionnable
        assert len(calls) == 1                       # second served from cache
        assert engine.verdict_cache_stats()["hits"] == 1
        # The cached qualification is re-homed onto THIS message.
        assert d2.message_id == "11" and d2.auteur_id == "2"

    async def test_recased_variant_shares_entry(self, monkeypatch):
        engine, calls = _build(monkeypatch)
        await engine._decide(TargetMessage("1", "2", "SALE CON"), _SIG, **_COMMON)
        await engine._decide(TargetMessage("2", "2", "sale con"), _SIG, **_COMMON)
        assert len(calls) == 1                       # collapsed key is identical

    async def test_different_guild_is_a_miss(self, monkeypatch):
        engine, calls = _build(monkeypatch)
        await engine._decide(TargetMessage("1", "2", "sale con"), _SIG, **_COMMON)
        await engine._decide(TargetMessage("2", "2", "sale con"), _SIG,
                             **{**_COMMON, "guild_id": 999})
        assert len(calls) == 2                       # per-guild key differs

    async def test_signal_carries_over_on_hit(self, monkeypatch):
        """A cache hit rebuilds the Decision from THIS message's signal."""
        engine, calls = _build(monkeypatch)
        await engine._decide(TargetMessage("1", "2", "sale con"), _SIG, **_COMMON)
        emb = Signal(constants.SOURCE_EMBEDDING, "insultes", 0.83)
        d = await engine._decide(TargetMessage("2", "2", "sale con"), emb, **_COMMON)
        assert len(calls) == 1
        assert d.signal_source == constants.SOURCE_EMBEDDING
        assert d.score_detecteur == pytest.approx(0.83)
        # a_reverifier is context-specific and never restored from cache.
        assert d.a_reverifier == []

    async def test_single_flight_coalesces_concurrent(self, monkeypatch):
        gate = asyncio.Event()
        engine, calls = _build(monkeypatch, slow=gate)
        t1 = asyncio.create_task(
            engine._decide(TargetMessage("1", "2", "sale con"), _SIG, **_COMMON))
        await asyncio.sleep(0)          # let t1 register its in-flight future
        t2 = asyncio.create_task(
            engine._decide(TargetMessage("2", "2", "sale con"), _SIG, **_COMMON))
        await asyncio.sleep(0)
        gate.set()
        d1, d2 = await asyncio.gather(t1, t2)
        assert len(calls) == 1          # both waiters share one nano call
        assert d1.message_id == "1" and d2.message_id == "2"
        assert engine.verdict_cache_stats()["inflight"] == 0

    async def test_non_sanctionnable_qualification_is_cached_too(self, monkeypatch):
        engine = AutomodEngine(types.SimpleNamespace())
        calls = []

        async def fake_judge(self, target, signal, **kw):
            calls.append(1)
            return _make_decision(target, signal, sanctionnable=False,
                                  rejet_grounding="grounding_citation_absente")

        monkeypatch.setattr(AutomodEngine, "_judge", fake_judge)
        d1 = await engine._decide(TargetMessage("1", "2", "je suis con"), _SIG, **_COMMON)
        d2 = await engine._decide(TargetMessage("2", "2", "je suis con"), _SIG, **_COMMON)
        assert d1.sanctionnable is False and d2.sanctionnable is False
        assert d2.rejet_grounding == "grounding_citation_absente"
        assert len(calls) == 1          # a "no" is worth caching against a raid too
