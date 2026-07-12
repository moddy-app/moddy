"""Tests for ``automod.nano`` — the decision contract.

Focus on the pure, safety-critical seams: output coercion (`parse_verdict`), the
user payload's *absence* of detector metadata (nano must judge cold), the
fence-marker stripping that keeps injection markers out of user-facing text, and
the bounded decision loop in `juger`.
"""

import json

import pytest

from automod import constants, nano
from automod.nano import (
    _MAX_DUREE_HEURES,
    build_system_prompt,
    build_user_payload,
    parse_verdict,
    response_language_name,
)
from automod.schemas import AuthorHistory, ContextMessage, Signal, TargetMessage


class TestResponseLanguageName:
    @pytest.mark.parametrize(
        "locale,expected",
        [
            ("fr", "French"),
            ("en-US", "English"),
            ("en-GB", "English"),
            ("fr-CA", "French"),   # falls back to the base "fr"
            ("de", "English"),     # unknown → English
            ("", "English"),
            (None, "English"),
        ],
    )
    def test_language_name(self, locale, expected):
        assert response_language_name(locale) == expected


class TestParseVerdict:
    def test_non_dict_returns_default(self):
        v = parse_verdict("not a dict")
        assert v == dict(nano._DEFAULT_VERDICT)

    def test_empty_dict_yields_safe_default(self):
        v = parse_verdict({})
        assert v["sanctionnable"] is False
        assert v["actions"] == []
        assert v["gravite"] == "basse"
        assert v["confiance"] == "low"

    def test_invalid_gravite_falls_back(self):
        assert parse_verdict({"gravite": "apocalyptic"})["gravite"] == "basse"

    def test_valid_gravite_preserved(self):
        assert parse_verdict({"gravite": "critique"})["gravite"] == "critique"

    def test_actions_filtered_to_allowed(self):
        v = parse_verdict({"actions": ["ban", "nuke", "warn", 123]})
        assert v["actions"] == ["ban", "warn"]

    def test_actions_non_list_becomes_empty(self):
        assert parse_verdict({"actions": "ban"})["actions"] == []

    def test_confiance_invalid_falls_back(self):
        assert parse_verdict({"confiance": "certain"})["confiance"] == "low"

    @pytest.mark.parametrize(
        "raw,expected",
        [
            (5, 5),
            (0, 0),
            (-3, 0),                       # clamped up to 0
            (_MAX_DUREE_HEURES + 100, _MAX_DUREE_HEURES),  # clamped down
            ("12", 12),                    # numeric string coerced
            ("abc", 0),                    # non-numeric → 0
            (None, 0),
        ],
    )
    def test_duree_is_clamped(self, raw, expected):
        assert parse_verdict({"duree_heures": raw})["duree_heures"] == expected

    def test_nb_messages_invalid_becomes_zero(self):
        assert parse_verdict({"nb_messages_supplementaires": "lots"})[
            "nb_messages_supplementaires"] == 0

    def test_besoin_contexte_clears_sanction(self):
        v = parse_verdict({
            "besoin_plus_contexte": True,
            "sanctionnable": True,
            "actions": ["ban"],
            "duree_heures": 48,
        })
        assert v["besoin_plus_contexte"] is True
        assert v["sanctionnable"] is False
        assert v["actions"] == []
        assert v["duree_heures"] == 0

    def test_others_coerced_to_strings(self):
        v = parse_verdict({"autres_messages_a_verifier": ["1", 2, None, {"x": 1}]})
        assert v["autres_messages_a_verifier"] == ["1", "2"]

    def test_others_non_list_becomes_empty(self):
        assert parse_verdict({"autres_messages_a_verifier": "123"})[
            "autres_messages_a_verifier"] == []

    def test_text_fields_trimmed_to_limits(self):
        v = parse_verdict({
            "categorie": "c" * 200,
            "raison": "r" * 5000,
            "explication": "e" * 5000,
        })
        assert len(v["categorie"]) == 60
        assert len(v["raison"]) == 1000
        assert len(v["explication"]) == 400

    def test_fence_markers_stripped_from_user_facing_fields(self):
        # A critical property: nano must never leak the injection fence markers
        # into text shown to a sanctioned member.
        v = parse_verdict({
            "raison": "[DATA:ab12cd34]insult targeting a member[/DATA:ab12cd34]",
            "explication": "he wrote [DATA:deadbeef]a slur[/DATA:deadbeef] at them",
            "categorie": "[DATA:ff00ff00]insultes[/DATA:ff00ff00]",
        })
        assert "DATA:" not in v["raison"]
        assert "DATA:" not in v["explication"]
        assert "DATA:" not in v["categorie"]
        assert v["raison"] == "insult targeting a member"
        assert v["categorie"] == "insultes"

    def test_non_string_text_fields_become_empty(self):
        v = parse_verdict({"raison": 123, "explication": ["x"], "categorie": None})
        assert v["raison"] == ""
        assert v["explication"] == ""
        assert v["categorie"] == ""


class TestBuildUserPayload:
    def _payload(self, **kw):
        target = TargetMessage(id="100", author_id="200", content="you are trash")
        history = AuthorHistory(cases_total=2)
        context = [ContextMessage(id="99", author_id="201", content="prior msg")]
        raw = build_user_payload(target, history, context, nonce="ab12cd34",
                                 severite=4, **kw)
        return json.loads(raw)

    def test_content_is_fenced(self):
        p = self._payload()
        assert p["message_cible"]["contenu"] == "[DATA:ab12cd34]you are trash[/DATA:ab12cd34]"
        assert p["contexte"][0]["contenu"] == "[DATA:ab12cd34]prior msg[/DATA:ab12cd34]"

    def test_carries_no_detector_signal(self):
        # Documented invariant: nano judges cold — the payload must NOT leak the
        # detection source, suspected category or score.
        p = self._payload()
        blob = json.dumps(p)
        for leaked in ("source", "categorie", "score", "signal", "embedding", "regex"):
            assert leaked not in blob, leaked

    def test_includes_severity_and_history(self):
        p = self._payload()
        assert p["severite"] == 4
        assert p["historique_auteur"]["cases_total"] == 2


class TestBuildSystemPrompt:
    def test_contains_nonce_and_language_and_guild(self):
        prompt = build_system_prompt(
            "My Server", "Be nice", restant=10, nonce="ab12cd34",
            severite=5, response_language="French")
        assert "ab12cd34" in prompt
        assert "My Server" in prompt
        assert "French" in prompt
        assert "Be nice" in prompt          # server rules embedded
        assert str(_MAX_DUREE_HEURES) in prompt

    def test_empty_rules_get_a_default(self):
        prompt = build_system_prompt("S", "   ", restant=5, nonce="deadbeef")
        assert "No specific guidance provided" in prompt

    def test_severity_guidance_varies(self):
        p1 = build_system_prompt("S", "r", 5, "n", severite=1)
        p5 = build_system_prompt("S", "r", 5, "n", severite=5)
        assert "very lenient" in p1
        assert "very strict" in p5


class TestJugerLoop:
    def _target(self):
        return TargetMessage(id="1", author_id="2", content="msg")

    async def test_returns_decision_with_signal_fields(self):
        signal = Signal(source="embedding", categorie="insultes", score_confiance=0.8)

        async def chat_fn(system, user):
            return {"sanctionnable": True, "actions": ["warn", "supprimer"],
                    "gravite": "moyenne", "confiance": "high", "raison": "insult"}

        async def fetch_context(n):
            return []

        decision = await nano.juger(
            self._target(), signal, guild_name="G", rules="", history=AuthorHistory(),
            chat_fn=chat_fn, fetch_context=fetch_context)
        assert decision.sanctionnable is True
        assert decision.actions == ["warn", "supprimer"]
        assert decision.signal_source == "embedding"
        assert decision.score_detecteur == 0.8
        # categorie comes from the model here
        assert decision.categorie == "insultes"

    async def test_categorie_falls_back_to_signal(self):
        signal = Signal(source="regex", categorie="menaces", score_confiance=0.7)

        async def chat_fn(system, user):
            return {"sanctionnable": False}      # no categorie from the model

        async def fetch_context(n):
            return []

        decision = await nano.juger(
            self._target(), signal, guild_name="G", rules="", history=AuthorHistory(),
            chat_fn=chat_fn, fetch_context=fetch_context)
        assert decision.categorie == "menaces"

    async def test_chat_error_yields_safe_nonsanction_decision(self):
        signal = Signal(source="regex", categorie="x", score_confiance=0.5)
        calls = {"n": 0}

        async def chat_fn(system, user):
            calls["n"] += 1
            raise RuntimeError("gateway down")

        async def fetch_context(n):
            return []

        decision = await nano.juger(
            self._target(), signal, guild_name="G", rules="", history=AuthorHistory(),
            chat_fn=chat_fn, fetch_context=fetch_context)
        assert decision.sanctionnable is False
        assert decision.actions == []
        assert calls["n"] == 1          # broke out immediately, no retry storm

    async def test_more_context_loop_is_bounded_by_rounds_max(self):
        calls = {"n": 0}

        async def chat_fn(system, user):
            calls["n"] += 1
            # Always ask for more context.
            return {"besoin_plus_contexte": True, "nb_messages_supplementaires": 1}

        async def fetch_context(n):
            return []

        signal = Signal(source="embedding", categorie="", score_confiance=0.5)
        await nano.juger(
            self._target(), signal, guild_name="G", rules="", history=AuthorHistory(),
            chat_fn=chat_fn, fetch_context=fetch_context)
        assert calls["n"] == constants.ROUNDS_MAX

    async def test_loop_stops_when_no_context_budget_remains(self):
        calls = {"n": 0}

        async def chat_fn(system, user):
            calls["n"] += 1
            # Ask for far more than the remaining budget → jumps straight to the cap.
            return {"besoin_plus_contexte": True,
                    "nb_messages_supplementaires": 10_000}

        async def fetch_context(n):
            return []

        signal = Signal(source="embedding", categorie="", score_confiance=0.5)
        await nano.juger(
            self._target(), signal, guild_name="G", rules="", history=AuthorHistory(),
            chat_fn=chat_fn, fetch_context=fetch_context)
        # Round 1: n=12 → jumps to CONTEXTE_MAX; round 2: restant=0 → stop.
        assert calls["n"] == 2
