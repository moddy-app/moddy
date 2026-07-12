"""Funnel-routing tests for ``automod.engine.AutomodEngine``.

These verify the *order* of the funnel and, crucially, that the expensive
embedding step is skipped whenever a cheaper earlier step already decided the
routing (trivial allowlist, regex blocklist) or when ``force_nano`` short-cuts
straight to nano. ``_judge`` (nano) is stubbed so no gateway/network is touched.
"""

import types

import pytest

from automod import constants
from automod.engine import AutomodEngine
from automod.embeddings import EmbeddingEngine, _normalize_vec
from automod.schemas import AuthorHistory, Signal


async def _no_context(_n):
    return []


def build_engine(monkeypatch, *, mapping=None, refs=None):
    """An engine with a stubbed ``_judge`` and controlled embeddings.

    Returns ``(engine, recorded, embed_calls)`` where ``recorded['signal']`` is
    the :class:`Signal` handed to nano (or absent if the funnel stopped early)
    and ``embed_calls`` is the list of embed batches issued during scoring.
    """
    bot = types.SimpleNamespace()
    engine = AutomodEngine(bot)

    # Controlled, already-ready embeddings so ensure_ready() is a no-op and
    # score() uses our mapping instead of the gateway.
    embed_calls = []

    async def embed(texts):
        embed_calls.append(list(texts))
        return [list((mapping or {})[t]) for t in texts]

    controlled = EmbeddingEngine(embed)
    controlled._ref_vectors = [_normalize_vec(v) for v, _ in (refs or [])]
    controlled._ref_categories = [c for _, c in (refs or [])]
    controlled._ready = True
    engine.embeddings = controlled

    recorded = {}

    async def fake_judge(self, target, signal, **kwargs):
        recorded["signal"] = signal
        return "DECISION"

    monkeypatch.setattr(AutomodEngine, "_judge", fake_judge)
    return engine, recorded, embed_calls


def target(content):
    from automod.schemas import TargetMessage
    return TargetMessage(id="1", author_id="2", content=content)


COMMON = dict(
    guild_id=10,
    guild_name="Guild",
    rules="",
    author_history=AuthorHistory(),
    fetch_context=_no_context,
)


class TestFunnelRouting:
    async def test_prefilter_drops_empty(self, monkeypatch):
        engine, recorded, embed_calls = build_engine(monkeypatch)
        result = await engine.analyze(target("   "), **COMMON)
        assert result is None
        assert "signal" not in recorded
        assert embed_calls == []

    async def test_trivial_message_short_circuits_before_embedding(self, monkeypatch):
        engine, recorded, embed_calls = build_engine(monkeypatch)
        result = await engine.analyze(target("mdr"), **COMMON)
        assert result is None
        assert "signal" not in recorded
        assert embed_calls == []  # no embedding call for a trivial message

    async def test_blocklist_routes_to_nano_without_embedding(self, monkeypatch):
        engine, recorded, embed_calls = build_engine(monkeypatch)
        result = await engine.analyze(target("espèce de connard"), **COMMON)
        assert result == "DECISION"
        assert recorded["signal"].source == constants.SOURCE_REGEX
        assert recorded["signal"].categorie == "insultes"
        assert embed_calls == []  # blocklist hit → embedding skipped

    async def test_embedding_above_threshold_routes_to_nano(self, monkeypatch):
        engine, recorded, embed_calls = build_engine(
            monkeypatch,
            mapping={"you are worthless": [1.0, 0.0]},
            refs=[([1.0, 0.0], "insultes")],
        )
        result = await engine.analyze(
            target("you are worthless"), severity=3, **COMMON
        )
        assert result == "DECISION"
        assert recorded["signal"].source == constants.SOURCE_EMBEDDING
        assert recorded["signal"].categorie == "insultes"
        assert len(embed_calls) == 1

    async def test_embedding_below_threshold_stops(self, monkeypatch):
        engine, recorded, embed_calls = build_engine(
            monkeypatch,
            mapping={"lovely weather today": [0.0, 1.0]},  # orthogonal → score 0
            refs=[([1.0, 0.0], "insultes")],
        )
        result = await engine.analyze(
            target("lovely weather today"), severity=3, **COMMON
        )
        assert result is None
        assert "signal" not in recorded
        assert len(embed_calls) == 1  # scored once, then dropped

    async def test_force_nano_skips_all_detectors(self, monkeypatch):
        engine, recorded, embed_calls = build_engine(monkeypatch)
        # A trivial message would normally be dropped; force_nano overrides that.
        result = await engine.analyze(
            target("mdr"), force_nano=True, **COMMON
        )
        assert result == "DECISION"
        assert recorded["signal"].source == constants.SOURCE_NANO_FLAG
        assert embed_calls == []


class TestFunnelCacheInteraction:
    async def test_repeated_flood_message_embeds_once(self, monkeypatch):
        engine, recorded, embed_calls = build_engine(
            monkeypatch,
            mapping={"spam toxic line": [1.0, 0.0]},
            refs=[([1.0, 0.0], "insultes")],
        )
        for _ in range(5):
            assert await engine.analyze(
                target("spam toxic line"), severity=3, **COMMON
            ) == "DECISION"
        # 5 identical flood messages cost exactly one embedding call.
        assert len(embed_calls) == 1
        assert engine.cache_stats()["hits"] == 4
