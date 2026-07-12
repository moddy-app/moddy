"""Per-guild budget guard tests (session 4.3).

Past the daily nano soft cap the funnel degrades — nano is reserved for flagrant
cases (a regex hit or an embedding score comfortably above threshold) — but it
never hard-cuts. Redis is faked; the counter is the authority.
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


def _engine(redis=None):
    return AutomodEngine(types.SimpleNamespace(redis=redis))


async def _fill_to_cap(engine, redis, guild_id, cap=constants.NANO_DAILY_SOFT_CAP):
    await redis.set(f"automod:budget:{guild_id}:{engine._utc_day()}", cap)


_REGEX = Signal(constants.SOURCE_REGEX, "insulte", 0.7)
# threshold(sev 3) ≈ 0.47; degraded needs ≥ threshold + 0.10 ≈ 0.57.
_EMB_HIGH = Signal(constants.SOURCE_EMBEDDING, "menace", 0.90)
_EMB_LOW = Signal(constants.SOURCE_EMBEDDING, "menace", 0.50)
_NANO_FLAG = Signal(constants.SOURCE_NANO_FLAG, "", 0.0)


class TestBudgetAllows:
    async def test_no_redis_is_inert(self):
        engine = _engine(redis=None)
        for sig in (_REGEX, _EMB_LOW, _NANO_FLAG):
            assert await engine._budget_allows(1, sig, 3) is True

    async def test_under_cap_allows_everything(self):
        redis = FakeRedis()
        engine = _engine(redis)
        await redis.set(f"automod:budget:1:{engine._utc_day()}", 10)
        for sig in (_REGEX, _EMB_LOW, _NANO_FLAG):
            assert await engine._budget_allows(1, sig, 3) is True

    async def test_over_cap_keeps_flagrant_drops_borderline(self):
        redis = FakeRedis()
        engine = _engine(redis)
        await _fill_to_cap(engine, redis, 1)
        # Flagrant → still judged.
        assert await engine._budget_allows(1, _REGEX, 3) is True
        assert await engine._budget_allows(1, _EMB_HIGH, 3) is True
        # Borderline → dropped (degraded sensitivity, not a hard cut).
        assert await engine._budget_allows(1, _EMB_LOW, 3) is False
        assert await engine._budget_allows(1, _NANO_FLAG, 3) is False

    async def test_per_guild_override_cap(self):
        redis = FakeRedis()
        engine = _engine(redis)
        await redis.set("automod:budget:cap:1", 5)
        await redis.set(f"automod:budget:1:{engine._utc_day()}", 5)
        assert await engine._budget_allows(1, _EMB_LOW, 3) is False   # over the low cap
        # Another guild without an override still uses the default cap.
        await redis.set(f"automod:budget:2:{engine._utc_day()}", 5)
        assert await engine._budget_allows(2, _EMB_LOW, 3) is True


class TestBudgetNotice:
    async def test_notice_fires_once_per_guild(self):
        redis = FakeRedis()
        engine = _engine(redis)
        await _fill_to_cap(engine, redis, 1)
        # Crossing the cap arms the one-off notice.
        await engine._budget_allows(1, _EMB_LOW, 3)
        assert engine.pop_budget_notice(1) is True
        assert engine.pop_budget_notice(1) is False   # only once
        # A different guild has its own notice.
        await _fill_to_cap(engine, redis, 2)
        await engine._budget_allows(2, _EMB_LOW, 3)
        assert engine.pop_budget_notice(2) is True

    async def test_stats_report_degraded_guild(self):
        redis = FakeRedis()
        engine = _engine(redis)
        await _fill_to_cap(engine, redis, 42)
        await engine._budget_allows(42, _EMB_LOW, 3)
        stats = engine.budget_stats()
        assert stats["soft_cap"] == constants.NANO_DAILY_SOFT_CAP
        assert 42 in stats["degraded_guilds"]


class TestBudgetIncrement:
    async def test_increment_counts_and_stats(self):
        redis = FakeRedis()
        engine = _engine(redis)
        await engine._budget_increment(7)
        await engine._budget_increment(7)
        assert await engine._budget_used(7) == 2
        # Process-local call counter tracked for diagnostics.
        engine._budget_calls += 0
        assert "nano_calls" in engine.budget_stats()


class TestBudgetIntegration:
    """Over-cap behaviour through the real ``analyze`` funnel."""

    def _build(self, monkeypatch, redis, *, mapping):
        bot = types.SimpleNamespace(redis=redis)
        engine = AutomodEngine(bot)

        async def embed(texts):
            return [list(mapping.get(t, [0.0, 1.0])) for t in texts]

        controlled = EmbeddingEngine(embed)
        controlled._ref_vectors = [_normalize_vec([1.0, 0.0])]
        controlled._ref_categories = ["menace"]
        controlled._ready = True
        engine.embeddings = controlled

        calls = []

        async def fake_judge(self, target, signal, **kw):
            calls.append(target.content)
            return Decision(
                message_id=target.id, auteur_id=target.author_id,
                sanctionnable=True, actions=[], categorie="menace",
                gravite="haute", raison="r", explication="e", confiance="high",
                signal_source=signal.source, score_detecteur=signal.score_confiance,
                citation="x", cible="membre",
            )

        monkeypatch.setattr(AutomodEngine, "_judge", fake_judge)
        return engine, calls

    async def test_over_cap_drops_borderline_message(self, monkeypatch):
        redis = FakeRedis()
        # A cosine of 0.5 → routes (>0.47) but is borderline (<0.57).
        engine, calls = self._build(
            monkeypatch, redis, mapping={"veiled threat": [0.5, 0.8660254]})
        await _fill_to_cap(engine, redis, 1)
        out = await engine.analyze(
            TargetMessage("1", "9", "veiled threat"), guild_id=1, guild_name="g",
            rules="", author_history=AuthorHistory(), fetch_context=_no_context,
            severity=3, response_language="French", channel_id=100)
        assert out is None            # dropped by the budget guard
        assert calls == []            # nano never called
        assert engine.budget_stats()["dropped"] >= 1

    async def test_over_cap_still_judges_regex_hit(self, monkeypatch):
        redis = FakeRedis()
        engine, calls = self._build(monkeypatch, redis, mapping={})
        await _fill_to_cap(engine, redis, 1)
        # An explicit insult hits the regex blocklist → flagrant → still judged.
        out = await engine.analyze(
            TargetMessage("1", "9", "espèce de connard"), guild_id=1,
            guild_name="g", rules="", author_history=AuthorHistory(),
            fetch_context=_no_context, severity=3, response_language="French",
            channel_id=100)
        assert out is not None and out.sanctionnable
        assert calls == ["espèce de connard"]
