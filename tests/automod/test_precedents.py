"""Tests for server precedents (session 7): the pure matcher, the deterministic
shortcut, the embedding-vector reuse, and the engine wiring (injection + stop).

Everything is offline: the embeddings are controlled (no gateway), nano's
``_judge`` is stubbed. See docs/AUTOMOD.md §2quinquies.
"""

import importlib.util
import os
import types

import pytest

from automod import constants as ac
from automod import precedents as ap
from automod.engine import AutomodEngine
from automod.embeddings import EmbeddingEngine, _normalize_vec
from automod.schemas import AuthorHistory

# Load the float32 pack/unpack helpers directly from the repository file so the
# pure test suite stays dependency-free (importing ``db`` would pull in asyncpg).
_repo_path = os.path.join(os.path.dirname(__file__), "..", "..",
                          "db", "repositories", "precedents.py")
_spec = importlib.util.spec_from_file_location("automod_precedents_repo", _repo_path)
_repo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_repo)
pack_vector = _repo.pack_vector
unpack_vector = _repo.unpack_vector


# --------------------------------------------------------------------------- #
# Pure matcher
# --------------------------------------------------------------------------- #

def _prec(pid, vec, verdict=ac.PRECEDENT_NON_SANCTIONNABLE, msg="m"):
    return ap.Precedent(id=str(pid), message=msg, verdict_humain=verdict,
                        vector=_normalize_vec(vec))


class TestPureMatcher:
    def test_cosine_length_mismatch_is_zero(self):
        assert ap.cosine([1.0, 0.0], [1.0]) == 0.0
        assert ap.cosine([], [1.0]) == 0.0

    def test_cosine_identical_normalised_is_one(self):
        v = _normalize_vec([3.0, 4.0])
        assert ap.cosine(v, v) == pytest.approx(1.0)

    def test_match_ranks_and_filters(self):
        q = _normalize_vec([1.0, 0.0])
        precs = [
            _prec("a", [1.0, 0.0]),      # sim 1.00
            _prec("b", [0.9, 0.1]),      # high
            _prec("c", [0.0, 1.0]),      # orthogonal → filtered out
        ]
        out = ap.match(q, precs, min_sim=0.80)
        assert [m.precedent.id for m in out] == ["a", "b"]
        assert out[0].similarite >= out[1].similarite

    def test_match_caps_top_k(self):
        q = _normalize_vec([1.0, 0.0])
        precs = [_prec(i, [1.0, 0.01 * i]) for i in range(6)]
        out = ap.match(q, precs, top_k=3, min_sim=0.0)
        assert len(out) == 3

    def test_match_empty_inputs(self):
        assert ap.match([], [_prec("a", [1.0, 0.0])]) == []
        assert ap.match([1.0, 0.0], []) == []

    def test_shortcut_only_for_strong_non_sanctionnable(self):
        q = _normalize_vec([1.0, 0.0])
        # 1.00 similarity, non_sanctionnable → shortcut fires.
        strong = ap.match(q, [_prec("a", [1.0, 0.0])], min_sim=0.0)
        assert ap.deterministic_shortcut(strong) is not None

    def test_shortcut_not_for_sanctionnable(self):
        q = _normalize_vec([1.0, 0.0])
        m = ap.match(q, [_prec("a", [1.0, 0.0], verdict=ac.PRECEDENT_SANCTIONNABLE)],
                     min_sim=0.0)
        assert ap.deterministic_shortcut(m) is None

    def test_shortcut_not_below_threshold(self):
        # A close-but-not-near-identical non_sanctionnable match: injected, not a stop.
        q = _normalize_vec([1.0, 0.0])
        m = ap.match(q, [_prec("a", [0.85, 0.5])], min_sim=0.0)  # cosine ≈ 0.86
        assert m and m[0].similarite < ac.PRECEDENT_SHORTCUT_SIMILARITE
        assert ap.deterministic_shortcut(m) is None

    def test_prompt_payload_shape(self):
        q = _normalize_vec([1.0, 0.0])
        m = ap.match(q, [_prec("a", [1.0, 0.0], msg="tg fdp mdrr")], min_sim=0.0)
        payload = ap.to_prompt_payload(m)
        assert payload == [{"message": "tg fdp mdrr",
                            "verdict_moderateurs": ac.PRECEDENT_NON_SANCTIONNABLE,
                            "similarite": 1.0}]


# --------------------------------------------------------------------------- #
# BYTEA pack / unpack round-trip
# --------------------------------------------------------------------------- #

class TestVectorPacking:
    def test_roundtrip(self):
        vec = [0.1, -0.2, 0.33, 1.0, -1.0]
        out = unpack_vector(pack_vector(vec))
        assert out == pytest.approx(vec, abs=1e-6)

    def test_unpack_none_is_empty(self):
        assert unpack_vector(None) == []
        assert unpack_vector(b"") == []


# --------------------------------------------------------------------------- #
# Engine wiring
# --------------------------------------------------------------------------- #

def build_engine(monkeypatch, *, mapping=None, refs=None):
    """Engine with controlled embeddings and a stubbed ``_judge`` that records
    the ``precedents`` payload it was handed."""
    bot = types.SimpleNamespace()
    engine = AutomodEngine(bot)

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
        recorded["precedents"] = kwargs.get("precedents")
        return "DECISION"

    monkeypatch.setattr(AutomodEngine, "_judge", fake_judge)
    return engine, recorded, embed_calls


def _target(content):
    from automod.schemas import TargetMessage
    return TargetMessage(id="1", author_id="2", content=content)


COMMON = dict(guild_id=10, guild_name="G", rules="",
              author_history=AuthorHistory())


async def _no_context(_n):
    return []


class TestEngineWiring:
    async def test_no_precedents_leaves_payload_empty(self, monkeypatch):
        engine, recorded, calls = build_engine(
            monkeypatch, mapping={"toxic here": [1.0, 0.0]},
            refs=[([1.0, 0.0], "insultes")])

        async def precedents_fn(_get_vec):
            return []

        result = await engine.analyze(
            _target("toxic here"), severity=3, fetch_context=_no_context,
            precedents_fn=precedents_fn, **COMMON)
        assert result == "DECISION"
        assert recorded["precedents"] == []

    async def test_injects_top_matches(self, monkeypatch):
        engine, recorded, calls = build_engine(
            monkeypatch, mapping={"borderline": [1.0, 0.0]},
            refs=[([1.0, 0.0], "insultes")])

        match = ap.PrecedentMatch(
            ap.Precedent(id="p1", message="past ruled msg",
                         verdict_humain=ac.PRECEDENT_NON_SANCTIONNABLE,
                         vector=_normalize_vec([1.0, 0.0])),
            similarite=0.88)

        async def precedents_fn(_get_vec):
            return [match]

        result = await engine.analyze(
            _target("borderline"), severity=3, fetch_context=_no_context,
            precedents_fn=precedents_fn, **COMMON)
        assert result == "DECISION"
        assert recorded["precedents"] == [
            {"message": "past ruled msg",
             "verdict_moderateurs": ac.PRECEDENT_NON_SANCTIONNABLE,
             "similarite": 0.88}]

    async def test_shortcut_stops_before_nano(self, monkeypatch):
        engine, recorded, calls = build_engine(
            monkeypatch, mapping={"tg fdp mdrr": [1.0, 0.0]},
            refs=[([1.0, 0.0], "insultes")])

        match = ap.PrecedentMatch(
            ap.Precedent(id="p1", message="fdp va mdr",
                         verdict_humain=ac.PRECEDENT_NON_SANCTIONNABLE,
                         vector=_normalize_vec([1.0, 0.0])),
            similarite=0.99)

        async def precedents_fn(_get_vec):
            return [match]

        result = await engine.analyze(
            _target("tg fdp mdrr"), severity=3, fetch_context=_no_context,
            precedents_fn=precedents_fn, **COMMON)
        # A non-sanctionnable Decision produced by the shortcut, no nano call.
        assert result is not None
        assert result.sanctionnable is False
        assert result.precedent_applique is not None
        assert result.precedent_applique["similarite"] == pytest.approx(0.99)
        assert "signal" not in recorded  # _judge never ran

    async def test_query_vector_reuses_scoring_embedding(self, monkeypatch):
        """A real provider that embeds via get_vector reuses the funnel's vector:
        the message is embedded once (scoring), not twice."""
        engine, recorded, calls = build_engine(
            monkeypatch, mapping={"toxic here": [1.0, 0.0]},
            refs=[([1.0, 0.0], "insultes")])

        seen = {}

        async def precedents_fn(get_vec):
            qvec = await get_vec()
            seen["qvec"] = qvec
            precs = [ap.Precedent(id="p1", message="toxic here",
                                  verdict_humain=ac.PRECEDENT_NON_SANCTIONNABLE,
                                  vector=_normalize_vec([1.0, 0.0]))]
            return ap.match(qvec, precs, min_sim=0.0)

        result = await engine.analyze(
            _target("toxic here"), severity=3, fetch_context=_no_context,
            precedents_fn=precedents_fn, **COMMON)
        # Reused the captured vector → the message was embedded exactly once.
        assert len(calls) == 1
        assert seen["qvec"] == pytest.approx([1.0, 0.0])
        # Similarity 1.0 non_sanctionnable → shortcut stop.
        assert result.sanctionnable is False
        assert result.precedent_applique is not None
