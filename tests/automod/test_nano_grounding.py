"""Grounding-guard tests for ``automod.nano`` (v2 verdict contract).

These pin the deterministic post-LLM guards that make a hallucinated verdict
mechanically impossible: a citation that is not verbatim in the target, a
victimless insult/threat, or a speculative reason all void the sanction. The
chat call is stubbed (a fake ``chat_fn`` returning a crafted raw verdict), so no
gateway/network is touched — the full ``juger`` round-trip runs offline.

See docs/AUTOMOD_AI.md §2 and docs/archive/AUTOMOD_V2_PLAN.md (Session 1, §1.4).
"""

import pytest

from automod import nano
from automod.nano import parse_verdict, validate_grounding, normalize_categorie
from automod.schemas import AuthorHistory, Signal, TargetMessage


# --- Harness ---------------------------------------------------------------- #

async def _no_context(_n):
    return []


def _chat_returning(raw):
    async def chat_fn(_system, _user):
        return raw
    return chat_fn


def _raw(**overrides):
    """A minimally-valid *sanctionnable* raw verdict, overridable per test."""
    base = {
        "besoin_plus_contexte": False,
        "nb_messages_supplementaires": 0,
        "sanctionnable": True,
        "categorie": "insulte",
        "gravite": "moyenne",
        "citation": "",
        "cible": "membre",
        "raison": "Insulte visant un membre",
        "explication": "",
        "confiance": "high",
        "autres_messages_a_verifier": [],
    }
    base.update(overrides)
    return base


async def _judge(raw, content, *, categorie_signal="insulte"):
    target = TargetMessage(id="100", author_id="7", content=content)
    signal = Signal(source="embedding", categorie=categorie_signal, score_confiance=0.5)
    return await nano.juger(
        target,
        signal,
        guild_name="Guild",
        rules="",
        history=AuthorHistory(),
        chat_fn=_chat_returning(raw),
        fetch_context=_no_context,
    )


# --- The seven required scenarios (§1.4) ------------------------------------ #

class TestGroundingRoundTrip:
    async def test_1_citation_absent_is_rejected(self):
        raw = _raw(citation="quelque chose", categorie="insulte", cible="membre")
        decision = await _judge(raw, "bonjour ça va tout le monde")
        assert decision.sanctionnable is False
        assert decision.actions == []
        assert decision.rejet_grounding == "grounding_citation_absente"

    async def test_2_insult_targeting_self_is_rejected(self):
        raw = _raw(citation="je suis con", categorie="insulte", cible="auteur_lui_meme")
        decision = await _judge(raw, "ah mais non je suis con")
        assert decision.sanctionnable is False
        assert decision.rejet_grounding == "grounding_cible_incoherente"

    async def test_3_hallucinated_connard_reproduces_real_bug(self):
        # The real production false positive: message says "je suis con"
        # (self-deprecation) but nano hallucinates the insult "connard".
        raw = _raw(citation="connard", categorie="insulte", cible="membre",
                   raison="Insulte « connard » visant un membre")
        decision = await _judge(raw, "ah mais non je suis con")
        assert decision.sanctionnable is False
        assert decision.rejet_grounding == "grounding_citation_absente"

    async def test_4_citation_case_and_accent_insensitive(self):
        # Message has accents + mixed case; citation is lowercase + unaccented.
        raw = _raw(citation="espece de debile", categorie="insulte", cible="membre")
        decision = await _judge(raw, "Sérieux Espèce de Débile va-t'en")
        assert decision.sanctionnable is True
        assert decision.rejet_grounding is None
        assert decision.citation == "espece de debile"

    async def test_5_citation_with_data_markers_is_rejected(self):
        raw = _raw(citation="[DATA:ab12cd34]ferme ta gueule[/DATA:ab12cd34]",
                   categorie="insulte", cible="membre")
        decision = await _judge(raw, "ferme ta gueule connard")
        assert decision.sanctionnable is False
        assert decision.rejet_grounding == "grounding_citation_absente"

    async def test_6_speculative_reason_is_rejected(self):
        raw = _raw(citation="worthless idiot", categorie="insulte", cible="membre",
                   raison="This suggests hostility toward the member")
        decision = await _judge(raw, "you are a worthless idiot")
        assert decision.sanctionnable is False
        assert decision.rejet_grounding == "grounding_raison_speculative"

    async def test_7_genuine_sanctionnable_case_passes(self):
        raw = _raw(citation="ferme ta gueule connard", categorie="insulte",
                   cible="membre", gravite="moyenne")
        decision = await _judge(raw, "ferme ta gueule connard")
        assert decision.sanctionnable is True
        assert decision.rejet_grounding is None
        assert decision.categorie == "insulte"
        assert decision.cible == "membre"
        # v2: nano no longer decides actions — the barème owns them. The pipeline
        # leaves them empty; the module fills them from the computed cran.
        assert decision.actions == []


# --- Unit-level guards ------------------------------------------------------ #

class TestValidateGrounding:
    def test_non_sanctionnable_passes_through_untouched(self):
        v = parse_verdict(_raw(sanctionnable=False, citation="", cible="aucune"))
        out = validate_grounding(v, "n'importe quoi")
        assert out["sanctionnable"] is False
        assert out["rejet_grounding"] is None

    def test_threat_needs_a_victim(self):
        v = parse_verdict(_raw(categorie="menace", cible="aucune",
                               citation="je vais te tuer"))
        out = validate_grounding(v, "je vais te tuer")
        assert out["sanctionnable"] is False
        assert out["rejet_grounding"] == "grounding_cible_incoherente"

    def test_hate_speech_without_victim_still_sanctionnable(self):
        # haine_discrimination is NOT in CATEGORIES_AVEC_VICTIME: a slur can be
        # victimless (aimed at a group) and still qualify.
        v = parse_verdict(_raw(categorie="haine_discrimination", cible="groupe",
                               citation="sale race"))
        out = validate_grounding(v, "sale race dehors")
        assert out["sanctionnable"] is True
        assert out["rejet_grounding"] is None

    def test_empty_citation_is_rejected(self):
        v = parse_verdict(_raw(citation=""))
        out = validate_grounding(v, "un message quelconque")
        assert out["rejet_grounding"] == "grounding_citation_absente"


# --- Parsing / category folding -------------------------------------------- #

class TestParseVerdict:
    def test_citation_and_cible_coerced(self):
        v = parse_verdict(_raw(citation="  hello  ", cible="not_a_target"))
        assert v["citation"] == "hello"
        assert v["cible"] == "aucune"  # invalid → default

    def test_legacy_category_is_folded(self):
        v = parse_verdict(_raw(categorie="insultes"))  # legacy plural
        assert v["categorie"] == "insulte"

    def test_unknown_category_becomes_empty(self):
        v = parse_verdict(_raw(categorie="banana"))
        assert v["categorie"] == ""

    def test_actions_and_duree_dropped_from_nano_contract(self):
        # v2 (session 2): nano no longer decides the sanction. Even if a stray
        # `actions` / `duree_heures` shows up in the raw output, parse_verdict
        # ignores it — the barème is the only source of the sanction.
        v = parse_verdict(_raw(actions=["ban", "supprimer"], duree_heures=48))
        assert "actions" not in v
        assert "duree_heures" not in v


class TestNormalizeCategorie:
    @pytest.mark.parametrize("raw,expected", [
        ("insultes", "insulte"),
        ("menaces", "menace"),
        ("contenu_sexuel", "harcelement_sexuel"),
        ("hate", "haine_discrimination"),
        ("scam", "arnaque_scam"),
        ("insulte", "insulte"),      # already canonical
        ("MENACE", "menace"),         # case-insensitive
        ("unknown", ""),
        (None, ""),
    ])
    def test_folding(self, raw, expected):
        assert normalize_categorie(raw) == expected
