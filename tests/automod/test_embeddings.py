"""Characterization tests for ``automod.embeddings`` (step 4).

Scoring is exercised with a fake embed function so no network/gateway is
involved. Assertions are value-based (not call-count-based) so they remain valid
regardless of the score cache added on top of ``score()``.
"""

import asyncio

import pytest

from automod.cache import LruTtlCache
from automod.embeddings import EmbeddingEngine, _normalize_vec


def make_engine(mapping):
    """Engine whose embed_fn returns a fixed vector per exact text.

    Returns ``(engine, calls)`` where ``calls`` is the list of text-batches the
    embed function was asked to embed.
    """
    calls = []

    async def embed(texts):
        calls.append(list(texts))
        return [list(mapping[t]) for t in texts]

    return EmbeddingEngine(embed), calls


def ready_engine(mapping, refs):
    """Return an engine with controlled reference vectors, marked ready."""
    engine, calls = make_engine(mapping)
    engine._ref_vectors = [_normalize_vec(v) for v, _ in refs]
    engine._ref_categories = [c for _, c in refs]
    engine._ready = True
    return engine, calls


class TestEnsureReady:
    async def test_embeds_references_once_and_is_idempotent(self):
        texts, _ = EmbeddingEngine.load_reference_texts()
        assert texts, "references.json should provide reference phrases"
        calls = {"n": 0}

        async def embed(ts):
            calls["n"] += 1
            return [[1.0, 0.0] for _ in ts]

        engine = EmbeddingEngine(embed)
        assert await engine.ensure_ready() is True
        assert engine.ready is True
        # Second call must not re-embed.
        assert await engine.ensure_ready() is True
        assert calls["n"] == 1

    async def test_size_mismatch_leaves_engine_not_ready(self):
        async def embed(ts):
            return [[1.0, 0.0]]  # wrong count

        engine = EmbeddingEngine(embed)
        assert await engine.ensure_ready() is False
        assert engine.ready is False


class TestScore:
    async def test_returns_best_category_and_high_cosine(self):
        engine, _ = ready_engine(
            mapping={"tu es nul": [0.98, 0.02]},
            refs=[([1.0, 0.0], "insultes"), ([0.0, 1.0], "menaces")],
        )
        score, category = await engine.score("tu es nul")
        assert category == "insultes"
        assert score == pytest.approx(0.9998, abs=1e-3)

    async def test_orthogonal_query_scores_low(self):
        engine, _ = ready_engine(
            mapping={"bonjour": [0.0, 1.0]},
            refs=[([1.0, 0.0], "insultes")],
        )
        score, _ = await engine.score("bonjour")
        assert score == pytest.approx(0.0, abs=1e-6)

    async def test_not_ready_returns_none(self):
        engine, _ = make_engine({})
        assert await engine.score("anything") is None


class TestPassesThreshold:
    def test_uses_default_when_no_threshold_given(self):
        from automod import constants
        assert EmbeddingEngine.passes_threshold(constants.SEUIL_EMBEDDING) is True
        assert EmbeddingEngine.passes_threshold(constants.SEUIL_EMBEDDING - 0.01) is False

    def test_explicit_threshold(self):
        assert EmbeddingEngine.passes_threshold(0.5, 0.4) is True
        assert EmbeddingEngine.passes_threshold(0.3, 0.4) is False

    def test_boundary_is_inclusive(self):
        assert EmbeddingEngine.passes_threshold(0.4, 0.4) is True


class TestScoreCache:
    async def test_identical_content_embeds_once(self):
        engine, calls = ready_engine(
            mapping={"tu es nul": [1.0, 0.0]},
            refs=[([1.0, 0.0], "insultes")],
        )
        first = await engine.score("tu es nul")
        second = await engine.score("tu es nul")
        assert first == second
        assert len(calls) == 1  # second call served from cache
        stats = engine.cache_stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["size"] == 1

    async def test_distinct_content_not_shared(self):
        engine, calls = ready_engine(
            mapping={"a": [1.0, 0.0], "b": [0.0, 1.0]},
            refs=[([1.0, 0.0], "insultes")],
        )
        await engine.score("a")
        await engine.score("b")
        assert len(calls) == 2

    async def test_transient_none_is_not_cached(self):
        # First embed response is empty (transient failure) → None, not cached;
        # a retry then succeeds and caches the real result.
        responses = [[], [[1.0, 0.0]]]

        async def embed(texts):
            return responses.pop(0)

        engine = EmbeddingEngine(embed)
        engine._ref_vectors = [_normalize_vec([1.0, 0.0])]
        engine._ref_categories = ["insultes"]
        engine._ready = True

        assert await engine.score("m") is None
        assert engine.cache_stats()["size"] == 0  # None was not stored
        score, category = await engine.score("m")
        assert category == "insultes"
        assert engine.cache_stats()["size"] == 1

    async def test_disabled_cache_embeds_every_time(self):
        engine, calls = ready_engine(
            mapping={"x": [1.0, 0.0]},
            refs=[([1.0, 0.0], "insultes")],
        )
        engine._cache = LruTtlCache(max_entries=0)  # disabled
        await engine.score("x")
        await engine.score("x")
        assert len(calls) == 2
        assert engine.cache_stats()["size"] == 0

    async def test_embed_error_propagates_and_clears_inflight(self):
        boom = RuntimeError("gateway down")

        async def embed(texts):
            raise boom

        engine = EmbeddingEngine(embed)
        engine._ref_vectors = [_normalize_vec([1.0, 0.0])]
        engine._ref_categories = ["insultes"]
        engine._ready = True

        with pytest.raises(RuntimeError):
            await engine.score("m")
        # The failed request must not wedge the engine: nothing cached, no
        # dangling in-flight entry, so a retry starts clean.
        assert engine.cache_stats()["inflight"] == 0
        assert engine.cache_stats()["size"] == 0

    async def test_single_flight_coalesces_concurrent_requests(self):
        started = asyncio.Event()
        release = asyncio.Event()
        calls = {"n": 0}

        async def embed(texts):
            calls["n"] += 1
            started.set()
            await release.wait()
            return [[1.0, 0.0] for _ in texts]

        engine = EmbeddingEngine(embed)
        engine._ref_vectors = [_normalize_vec([1.0, 0.0])]
        engine._ref_categories = ["insultes"]
        engine._ready = True

        t1 = asyncio.create_task(engine.score("flood"))
        await started.wait()               # first request is inside embed()
        t2 = asyncio.create_task(engine.score("flood"))
        await asyncio.sleep(0)             # let t2 attach to the in-flight future
        release.set()
        r1, r2 = await asyncio.gather(t1, t2)

        assert r1 == r2
        assert calls["n"] == 1             # both served by a single embed call
        assert engine.cache_stats()["size"] == 1
        assert engine.cache_stats()["inflight"] == 0
