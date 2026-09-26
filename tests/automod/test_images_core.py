"""Automod images — the pure core (docs/AUTOMOD_AI.md §4.2/§4.3).

Perceptual hashing, the known-hash index, the OCR-tolerant scam anchors, the
SafeSearch policy, the monthly pacing, the scam pre-rules, and how the engine
routes / labels OCR text. No network, no Discord, no DB.
"""

from __future__ import annotations

import io
import json
import types
from array import array

import pytest
from PIL import Image, ImageDraw

from automod import constants, image_policy as ip, nano, scam_anchors
from automod.blocklist import Blocklist
from automod.embeddings import EmbeddingEngine, _normalize_vec
from automod.engine import AutomodEngine, detect_doute
from automod.image_hash import (
    HashEntry, HashIndex, ImageDecodeError, from_signed, hamming, prepare_image,
    to_signed,
)
from automod.schemas import (
    AuthorHistory, Decision, ORIGINE_IMAGE_OCR, Signal, TargetMessage,
)

# The raw OCR of a real "MrBeast crypto casino" scam screenshot (Tesseract-grade
# noise). The interface strings survive — that is what the anchors key on.
SCAM_OCR = """x NIMMAOM 0 yap CIO 13
Q Mrseast 19 • Fie,ast Games 5:Fong, SM.. is Om Nowl
fl01) °s,m ° Elta artno' u the launch of my Ile, MO' Mk. to our recent launch al
theVpoPmjec11...rele this ble,vent. I ✓ arn e.g. away 55,800 to the bonus IrnmedWaty.
O claim your reward
a Fella Wes,. promo code. BST w  Rea. yEsn 55.1300 bonus O pt,st tads..
Saiects Witham,* Mothoa CRYPID 11•11101.
Withdrawal Success!
BANK CARO • ma
• Twiner
r Gramm"""


def _png(img: Image.Image, fmt="PNG", **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    return buf.getvalue()


def _scam_like_image(w=1200, h=800) -> Image.Image:
    img = Image.new("RGB", (w, h), (30, 20, 60))
    d = ImageDraw.Draw(img)
    d.rectangle((600, 300, 1050, 600), fill=(90, 60, 160))
    d.rectangle((650, 450, 1000, 500), fill=(40, 220, 140))
    d.text((650, 350), "Withdrawal Success!", fill=(255, 255, 255))
    return img


# --------------------------------------------------------------------------- #
# Hashing
# --------------------------------------------------------------------------- #

class TestImageHash:
    def test_reencoded_and_resized_copy_keeps_its_hash(self):
        img = _scam_like_image()
        a = prepare_image(_png(img))
        b = prepare_image(_png(img.resize((700, 466)), "JPEG", quality=55))
        assert hamming(a.phash, b.phash) <= constants.PHASH_MAX_DISTANCE
        assert hamming(a.dhash, b.dhash) <= constants.DHASH_MAX_DISTANCE

    def test_different_images_are_far_apart(self):
        a = prepare_image(_png(_scam_like_image()))
        noise = Image.effect_noise((800, 800), 90).convert("RGB")
        b = prepare_image(_png(noise))
        assert hamming(a.phash, b.phash) > constants.PHASH_MAX_DISTANCE

    def test_downscaled_copy_is_bounded(self):
        p = prepare_image(_png(Image.new("RGB", (4000, 3000), (10, 10, 10))))
        reduced = Image.open(io.BytesIO(p.data))
        assert max(reduced.size) <= constants.IMAGE_MAX_SIDE
        assert p.mime == "image/jpeg"
        assert (p.width, p.height) == (4000, 3000)

    def test_screenshot_heuristic(self):
        assert prepare_image(_png(_scam_like_image())).looks_like_screenshot
        noise = Image.effect_noise((900, 900), 90).convert("RGB")
        assert not prepare_image(_png(noise)).looks_like_screenshot

    def test_garbage_and_bombs_are_refused(self):
        with pytest.raises(ImageDecodeError):
            prepare_image(b"not an image")
        with pytest.raises(ImageDecodeError):
            prepare_image(_png(Image.new("L", (600, 600))), max_pixels=1000)

    def test_signed_roundtrip_for_bigint_columns(self):
        for value in (0, 1, (1 << 63) + 5, (1 << 64) - 1):
            assert from_signed(to_signed(value)) == value
            assert -(1 << 63) <= to_signed(value) < (1 << 63)


class TestHashIndex:
    def test_match_requires_both_hashes_close(self):
        idx = HashIndex([HashEntry(1, 0b1010, 0b1111, "scam", "block")])
        assert idx.match(0b1011, 0b1111).entry.id == 1
        assert idx.match(0b1010, (1 << 40) - 1) is None  # dHash far

    def test_closest_wins_and_allow_wins_a_tie(self):
        idx = HashIndex([
            HashEntry(1, 0b0000, 0, "scam", "block"),
            HashEntry(2, 0b0011, 0, "scam", "block"),
            HashEntry(3, 0b0000, 0, "scam", "allow"),
        ])
        m = idx.match(0b0001, 0)
        assert m.entry.id == 3 and m.distance == 1

    def test_add_replaces_and_remove(self):
        idx = HashIndex()
        idx.add(HashEntry(1, 0, 0, "nsfw", "block"))
        idx.add(HashEntry(1, 0, 0, "nsfw", "allow"))
        assert len(idx) == 1 and idx.entries[0].verdict == "allow"
        idx.remove(1)
        assert len(idx) == 0


# --------------------------------------------------------------------------- #
# Scam anchors
# --------------------------------------------------------------------------- #

class TestScamAnchors:
    def test_noisy_ocr_of_a_real_scam_routes(self):
        s = scam_anchors.score(SCAM_OCR)
        assert s.score >= constants.SCAM_ANCHOR_THRESHOLD
        for anchor in ("withdrawal success", "promo code", "bonus", "bank card"):
            assert anchor in s.anchors   # "BANK CARO" still counts (1 edit)

    def test_glued_words_still_count(self):
        assert "withdrawal success" in scam_anchors.score("WITHDRAWALSUCCESS 5600").anchors

    @pytest.mark.parametrize("text", [
        "il y a un niveau bonus caché dans ce jeu",
        "gg bien joué",
        "regarde mon casino minecraft",
        "j'ai retiré de l'argent au distributeur",
    ])
    def test_ordinary_text_stays_under_the_threshold(self, text):
        assert scam_anchors.score(text).score < constants.SCAM_ANCHOR_THRESHOLD

    def test_money_amounts_add_weight(self):
        assert "montant" in scam_anchors.score("+5 600 USDT received").anchors
        assert "montant" in scam_anchors.score("I give $5,600 away").anchors


# --------------------------------------------------------------------------- #
# Policies
# --------------------------------------------------------------------------- #

class TestSafeSearchPolicy:
    @pytest.mark.parametrize("lk,sanction,gravite,doute", [
        ({"adult": "VERY_LIKELY"}, True, "haute", None),
        ({"adult": "LIKELY"}, True, "moyenne", None),
        ({"adult": "POSSIBLE"}, False, "basse", "safesearch_adult_possible"),
        ({"adult": "UNLIKELY", "racy": "VERY_LIKELY"}, False, "basse", "safesearch_racy"),
        ({"violence": "VERY_LIKELY"}, False, "basse", "safesearch_violence"),
        ({"adult": "VERY_UNLIKELY", "racy": "LIKELY"}, False, "basse", None),
        ({}, False, "basse", None),
    ])
    def test_mapping(self, lk, sanction, gravite, doute):
        v = ip.safesearch_to_verdict(lk)
        assert (v.sanctionnable, v.gravite, v.doute) == (sanction, gravite, doute)
        assert v.actionable == (sanction or doute is not None)


class TestPacing:
    START, END = 0.0, 30 * 86400.0

    def test_early_month_only_the_burst_is_available(self):
        now = self.START + 3600
        assert ip.pacing_allows(10, 1000, self.START, self.END, now, ip.TIER_PRIORITAIRE)
        assert not ip.pacing_allows(32, 1000, self.START, self.END, now, ip.TIER_PRIORITAIRE)

    def test_mid_month_allows_about_half(self):
        mid = (self.START + self.END) / 2
        assert ip.pacing_allows(500, 1000, self.START, self.END, mid, ip.TIER_PRIORITAIRE)
        assert not ip.pacing_allows(540, 1000, self.START, self.END, mid, ip.TIER_PRIORITAIRE)

    def test_normal_tier_keeps_headroom_for_priority(self):
        mid = (self.START + self.END) / 2
        # allowed ≈ 530 → normal tier stops at 70 % of it (≈ 371)
        assert ip.pacing_allows(300, 1000, self.START, self.END, mid, ip.TIER_NORMAL)
        assert not ip.pacing_allows(400, 1000, self.START, self.END, mid, ip.TIER_NORMAL)
        assert ip.pacing_allows(400, 1000, self.START, self.END, mid, ip.TIER_PRIORITAIRE)

    def test_hard_cap(self):
        assert not ip.pacing_allows(1000, 1000, self.START, self.END, self.END, ip.TIER_PRIORITAIRE)
        assert not ip.pacing_allows(0, 0, self.START, self.END, self.END)

    def test_tiers(self):
        assert ip.nsfw_tier(5, 100, "haute") == ip.TIER_PRIORITAIRE
        assert ip.nsfw_tier(400, 2, "haute") == ip.TIER_PRIORITAIRE
        assert ip.nsfw_tier(400, 100, "aucune") == ip.TIER_PRIORITAIRE
        assert ip.nsfw_tier(400, 100, "moyenne") == ip.TIER_NORMAL


class TestScamRisk:
    def test_compromised_account_pattern_scores_high(self):
        score, reasons = ip.scam_risk_score(ip.ScamRiskInput(
            account_age_days=900, member_age_days=400, familiarite="haute",
            image_count=4, text_empty=True, cross_post=3, looks_like_screenshot=True))
        assert score >= constants.SCAM_RISK_THRESHOLD
        assert "cross_post" in reasons

    def test_regular_member_sharing_a_photo_is_skipped(self):
        score, _ = ip.scam_risk_score(ip.ScamRiskInput(
            account_age_days=900, member_age_days=400, familiarite="haute",
            image_count=1, text_empty=False))
        assert score < constants.SCAM_RISK_THRESHOLD

    def test_fresh_account_is_enough(self):
        score, _ = ip.scam_risk_score(ip.ScamRiskInput(account_age_days=3))
        assert score >= constants.SCAM_RISK_THRESHOLD


# --------------------------------------------------------------------------- #
# Learned blocklist terms + learned references
# --------------------------------------------------------------------------- #

class TestLearning:
    def test_blocklist_reload_adds_learned_terms(self):
        b = Blocklist()
        assert b.match("espece de zigoto patente") is None
        b.reload([{"terme": "zigoto patente", "categorie": "harcelement", "mode": "words"}])
        assert b.match("espèce de zigoto patenté").categorie == "harcelement"
        assert b.extra_count == 1
        b.reload([])
        assert b.extra_count == 0

    def test_short_compact_terms_fall_back_to_word_boundaries(self):
        b = Blocklist([{"terme": "abc", "categorie": "insulte", "mode": "compact"}])
        assert b.match("labcdef") is None or b.match("labcdef").categorie != "insulte"
        assert b.match("abc").categorie == "insulte"

    def test_scam_category_in_the_static_blocklist(self):
        assert Blocklist().match("Free Nitro!!").categorie == "arnaque_scam"

    async def test_learned_reference_changes_the_score(self):
        async def embed(texts):
            return [[1.0, 0.0] if "scam" in t else [0.0, 1.0] for t in texts]
        eng = EmbeddingEngine(embed)
        eng._ref_vectors = [_normalize_vec([0.0, 1.0])]
        eng._ref_categories = ["insultes"]
        eng._ready = True
        before = await eng.score("new scam text")
        eng.add_learned(array("f", [1.0, 0.0]), "arnaque_scam")
        after = await eng.score("new scam text")   # cache was cleared
        assert after[1] == "arnaque_scam" and after[0] > before[0]
        assert eng.learned_count == 1
        assert eng.max_similarity_to_learned(array("f", [1.0, 0.0])) == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Engine + nano wiring for OCR text
# --------------------------------------------------------------------------- #

async def _no_context(_n):
    return []


def _engine(monkeypatch):
    engine = AutomodEngine(types.SimpleNamespace())
    eng = EmbeddingEngine(lambda texts: None)
    eng._ready = True
    engine.embeddings = eng
    seen = {}

    async def fake_judge(self, target, signal, **kwargs):
        seen["signal"] = signal
        seen["target"] = target
        return Decision(
            message_id=target.id, auteur_id=target.author_id, sanctionnable=True,
            actions=[], categorie="arnaque_scam", gravite="haute", raison="r",
            explication="", confiance="high", signal_source=signal.source,
            score_detecteur=signal.score_confiance, citation="Withdrawal Success!",
            cible="groupe")

    monkeypatch.setattr(AutomodEngine, "_judge", fake_judge)
    return engine, seen


class TestEngineOcr:
    async def test_anchor_only_text_routes_with_the_anchor_source(self, monkeypatch):
        engine, seen = _engine(monkeypatch)
        t = TargetMessage(id="1", author_id="2", origine=ORIGINE_IMAGE_OCR,
                          content="BANK CARO bonus USDT deposit")
        await engine.analyze(t, guild_id=1, guild_name="G", rules="",
                             author_history=AuthorHistory(), fetch_context=_no_context)
        assert seen["signal"].source == constants.SOURCE_ANCRES_SCAM

    async def test_ocr_text_routes_by_anchors_and_is_stamped(self, monkeypatch):
        engine, seen = _engine(monkeypatch)
        t = TargetMessage(id="1", author_id="2", content=SCAM_OCR, origine=ORIGINE_IMAGE_OCR)
        d = await engine.analyze(t, guild_id=1, guild_name="G", rules="",
                                 author_history=AuthorHistory(),
                                 fetch_context=_no_context, channel_id=5)
        # The static blocklist ("withdrawalsuccess") or the anchors — either way
        # the scam category routes it to nano.
        assert seen["signal"].source in (constants.SOURCE_REGEX, constants.SOURCE_ANCRES_SCAM)
        assert seen["signal"].categorie == "arnaque_scam"
        assert d.origine == ORIGINE_IMAGE_OCR
        assert d.contenu_juge == SCAM_OCR

    def test_verdict_key_separates_ocr_from_typed_text(self):
        engine = AutomodEngine(types.SimpleNamespace())
        assert engine._verdict_key(1, "abc") != engine._verdict_key(1, "abc", ORIGINE_IMAGE_OCR)
        assert engine._verdict_key(1, "abc") == engine._verdict_key(1, "abc", "texte")

    def test_nano_payload_and_prompt_mention_the_origin(self):
        t = TargetMessage(id="1", author_id="2", content="x", origine=ORIGINE_IMAGE_OCR)
        payload = json.loads(nano.build_user_payload(t, AuthorHistory(), [], "abcd1234"))
        assert payload["message_cible"]["origine"] == ORIGINE_IMAGE_OCR
        assert "TEXT READ FROM AN IMAGE" in nano.origin_prompt_block(t)
        plain = TargetMessage(id="1", author_id="2", content="x")
        assert "origine" not in json.loads(
            nano.build_user_payload(plain, AuthorHistory(), [], "abcd1234"))["message_cible"]
        assert nano.origin_prompt_block(plain) == ""
        confirm = json.loads(nano.build_confirm_user_payload(t, {}, [], "abcd1234"))
        assert confirm["message_cible"]["origine"] == ORIGINE_IMAGE_OCR


def _decision(**kw):
    base = dict(message_id="1", auteur_id="2", sanctionnable=False, actions=[],
                categorie="", gravite="basse", raison="", explication="",
                confiance="high", signal_source="embedding", score_detecteur=0.5)
    base.update(kw)
    return Decision(**base)


class TestDoubt:
    def test_grounding_rejection_is_a_doubt(self):
        assert detect_doute(_decision(rejet_grounding="grounding_citation_absente")) \
            == "grounding_citation_absente"

    def test_low_confidence_with_a_category_is_a_doubt(self):
        assert detect_doute(_decision(categorie="insulte", confiance="low")) == "confiance_basse"

    def test_anchor_hit_that_nano_cleared_is_a_doubt(self):
        assert detect_doute(_decision(signal_source=constants.SOURCE_ANCRES_SCAM)) \
            == "ancres_sans_sanction"

    def test_clean_and_precedent_decisions_are_not(self):
        assert detect_doute(_decision()) is None
        assert detect_doute(_decision(rejet_grounding="x",
                                      precedent_applique={"similarite": 0.99})) is None

    def test_contenu_nsfw_has_bareme_floors(self):
        from automod import bareme
        assert ("contenu_nsfw", "haute") in bareme.PLANCHER
        assert "contenu_nsfw" in constants.CATEGORIES
