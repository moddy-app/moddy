"""Author-aggregation tests (session 4.2) — fragmented harassment.

"je vais" / "te" / "retrouver" each clears the funnel on its own (empty
fragments), but the concatenation must reach nano. These drive the real
``analyze`` funnel with controlled embeddings and a fake Redis buffer, stubbing
only ``_judge`` (nano) so no gateway is touched.
"""

import types

import pytest

from automod import constants
from automod.embeddings import EmbeddingEngine, _normalize_vec
from automod.engine import AutomodEngine
from automod.schemas import AuthorHistory, Decision, Signal, TargetMessage

from _redis_stub import FakeRedis


async def _no_context(_n):
    return []


def _build(monkeypatch, *, mapping):
    """Engine with controlled embeddings, a fake Redis, and a stubbed judge.

    ``mapping`` maps an exact embed input to a 2-d vector; the single reference
    is ``[1, 0]`` (category "menace"), so a vector of ``[1, 0]`` scores ~1 and an
    orthogonal ``[0, 1]`` scores ~0. Unknown inputs default to orthogonal.
    """
    bot = types.SimpleNamespace(redis=FakeRedis())
    engine = AutomodEngine(bot)

    embed_calls = []

    async def embed(texts):
        embed_calls.append(list(texts))
        return [list(mapping.get(t, [0.0, 1.0])) for t in texts]

    controlled = EmbeddingEngine(embed)
    controlled._ref_vectors = [_normalize_vec([1.0, 0.0])]
    controlled._ref_categories = ["menace"]
    controlled._ready = True
    engine.embeddings = controlled

    judged = []

    async def fake_judge(self, target, signal, **kw):
        agg = kw.get("agregat_de")
        judged.append({"content": target.content, "agregat_de": agg})
        return Decision(
            message_id=target.id, auteur_id=target.author_id, sanctionnable=True,
            actions=[], categorie="menace", gravite="haute", raison="menace",
            explication="e", confiance="high", signal_source=signal.source,
            score_detecteur=signal.score_confiance, citation="retrouver",
            cible="membre",
            agregat_de=[str(x) for x in agg] if agg else [],
            agregat_contenu=target.content if agg else "",
        )

    monkeypatch.setattr(AutomodEngine, "_judge", fake_judge)
    return engine, judged, embed_calls


_COMMON = dict(
    guild_id=1, guild_name="g", rules="", author_history=AuthorHistory(),
    fetch_context=_no_context, severity=3, response_language="French",
    channel_id=100,
)


def _target(mid, content, author="7"):
    return TargetMessage(id=str(mid), author_id=author, content=content)


class TestAggregation:
    async def test_fragmented_threat_is_reassembled_and_judged(self, monkeypatch):
        concat = "je vais\nte\nretrouver"
        engine, judged, _ = _build(monkeypatch, mapping={concat: [1.0, 0.0]})

        # Each fragment stops before nano (orthogonal → score 0).
        assert await engine.analyze(_target(1, "je vais"), **_COMMON) is None
        assert await engine.analyze(_target(2, "te"), **_COMMON) is None
        # The third fragment triggers the aggregate, which routes to nano.
        decision = await engine.analyze(_target(3, "retrouver"), **_COMMON)

        assert decision is not None and decision.sanctionnable
        assert len(judged) == 1
        assert judged[0]["content"] == concat
        # The decision carries every fragment id (caller deletes all N).
        assert decision.agregat_de == ["1", "2", "3"]
        assert decision.agregat_contenu == concat

    async def test_innocent_fragments_trigger_nothing(self, monkeypatch):
        # Nothing scores; the concat isn't in the mapping → orthogonal → no route.
        engine, judged, _ = _build(monkeypatch, mapping={})
        for mid, txt in [(1, "comment"), (2, "vas"), (3, "aujourdhui")]:
            assert await engine.analyze(_target(mid, txt), **_COMMON) is None
        assert judged == []                    # nano never called

    async def test_fragment_already_judged_skips_aggregate(self, monkeypatch):
        # First message routes to nano on its own (score ~1) → marked judged.
        concat = "toxic\nte"
        engine, judged, _ = _build(
            monkeypatch, mapping={"toxic": [1.0, 0.0], concat: [1.0, 0.0]})
        d1 = await engine.analyze(_target(1, "toxic"), **_COMMON)
        assert d1 is not None and not d1.agregat_de   # individual, not an aggregate
        # A follow-up fragment would form an aggregate, but a member fragment was
        # already judged individually → the aggregate is skipped (no double jeopardy).
        d2 = await engine.analyze(_target(2, "te"), **_COMMON)
        assert d2 is None
        assert len(judged) == 1 and judged[0]["agregat_de"] is None

    async def test_no_channel_id_disables_aggregation(self, monkeypatch):
        concat = "je vais\nte"
        engine, judged, _ = _build(monkeypatch, mapping={concat: [1.0, 0.0]})
        common = {k: v for k, v in _COMMON.items() if k != "channel_id"}
        assert await engine.analyze(_target(1, "je vais"), **common) is None
        assert await engine.analyze(_target(2, "te"), **common) is None
        assert judged == []                    # no buffer → no aggregate

    async def test_aggregate_verdict_is_cached(self, monkeypatch):
        # A second identical aggregate (different fragment ids, fresh judged set)
        # is served from the verdict cache — one nano call for a copypasta raid.
        concat = "je vais\nte\nretrouver"
        engine, judged, _ = _build(monkeypatch, mapping={concat: [1.0, 0.0]})
        for base in (0, 10):
            for mid, txt in [(base + 1, "je vais"), (base + 2, "te"),
                             (base + 3, "retrouver")]:
                # Fresh author per burst so the judged-set doesn't block the 2nd.
                await engine.analyze(
                    _target(mid, txt, author=str(base)), **_COMMON)
        assert len(judged) == 1                # second aggregate hit the cache
