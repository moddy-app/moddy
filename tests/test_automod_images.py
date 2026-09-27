"""Automod images — module features, shared image service, labeling queue.

docs/AUTOMOD_AI.md §4.2 (image_scam), §4.3 (image_nsfw), §9 (labeling queue).
No network, no Discord, no DB: every collaborator is a small fake.
"""

from __future__ import annotations

import io
import types
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image, ImageDraw

from automod import constants as ac
from automod.image_hash import HashEntry, prepare_image
from automod.schemas import (
    AuthorHistory, Decision, ImageMeta, ORIGINE_IMAGE_HASH, ORIGINE_IMAGE_NSFW,
    ORIGINE_IMAGE_OCR,
)
from modules.automod_ai import (
    FEATURE_CLASSES, AutomodModule, ImageNsfwFeature, ImageScamFeature,
)
from services.automod_image_service import AutomodImageService, image_attachments
from services.automod_label_service import (
    AutomodLabelService, KIND_IMAGE_NSFW, KIND_IMAGE_SCAM, KIND_TEXTE, build_details,
    dedup_key, label_kind,
)
from utils.automod_label_views import render_label_card

NOW = datetime.now(timezone.utc)


def _png(w=1200, h=800, color=(30, 20, 60)) -> bytes:
    img = Image.new("RGB", (w, h), color)
    d = ImageDraw.Draw(img)
    d.rectangle((600, 300, 1050, 600), fill=(90, 60, 160))
    d.text((650, 350), "Withdrawal Success!", fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class FakeAttachment:
    def __init__(self, data: bytes, *, id=1, filename="shot.png", content_type="image/png",
                 width=1200, height=800):
        self.id, self.filename, self.content_type = id, filename, content_type
        self.size, self.width, self.height = len(data), width, height
        self._data = data
        self.reads = 0

    async def read(self):
        self.reads += 1
        return self._data


def _message(attachments, *, content="", account_days=900, member_days=400, nsfw=False):
    author = types.SimpleNamespace(
        id=5, bot=False,
        created_at=NOW - timedelta(days=account_days),
        joined_at=NOW - timedelta(days=member_days),
    )
    return types.SimpleNamespace(
        id=100, content=content, attachments=attachments, author=author,
        channel=types.SimpleNamespace(id=7, is_nsfw=lambda: nsfw),
        guild=types.SimpleNamespace(id=1, name="Guild"), jump_url="https://x",
        created_at=NOW,
    )


class FakeEngine:
    def __init__(self, decision=None):
        self.calls = []
        self.decision = decision

    async def analyze(self, target, **kwargs):
        self.calls.append((target, kwargs))
        if self.decision is None:
            return None
        self.decision.origine = target.origine
        self.decision.contenu_juge = target.content
        return self.decision


class FakeModule:
    def __init__(self, bot):
        self.bot = bot
        self.guild_id = 1
        self.rules = ""
        self.severity = 3

    def guild_locale(self, guild):
        return "fr"

    def response_language(self, guild):
        return "French"

    async def build_author_history(self, user_id):
        return AuthorHistory()

    def make_context_loader(self, message):
        async def loader(n):
            return []
        return loader

    def make_precedents_provider(self):
        return None


def _bot(engine=None):
    bot = types.SimpleNamespace(redis=None, db=None, stats=None, gateway=None)
    bot.automod_images = AutomodImageService(bot)
    bot._automod_engine = engine or FakeEngine()
    return bot


def _scam_decision(sanctionnable=True):
    return Decision(
        message_id="100", auteur_id="5", sanctionnable=sanctionnable, actions=[],
        categorie="arnaque_scam", gravite="haute", raison="r", explication="e",
        confiance="high", signal_source=ac.SOURCE_ANCRES_SCAM, score_detecteur=0.8,
        citation="Withdrawal Success!", cible="groupe",
    )


# --------------------------------------------------------------------------- #
# Attachments filter
# --------------------------------------------------------------------------- #

def test_image_attachments_filters_type_size_and_count():
    data = _png()
    msg = _message([
        FakeAttachment(data, id=1),
        FakeAttachment(b"x", id=2, filename="a.txt", content_type="text/plain"),
        FakeAttachment(data, id=3, width=64, height=64),           # emoji-sized
        *[FakeAttachment(data, id=10 + i) for i in range(6)],
    ])
    kept = image_attachments(msg)
    assert [a.id for a in kept][:1] == [1]
    assert 2 not in [a.id for a in kept] and 3 not in [a.id for a in kept]
    assert len(kept) == ac.IMAGE_MAX_PER_MESSAGE


# --------------------------------------------------------------------------- #
# Image service
# --------------------------------------------------------------------------- #

async def test_prepare_downloads_once_and_is_shared():
    svc = AutomodImageService(types.SimpleNamespace(redis=None, db=None, stats=None))
    att = FakeAttachment(_png())
    a = await svc.prepare(att)
    b = await svc.prepare(att)
    assert a is b and att.reads == 1
    assert svc.cached_by_phash(a.phash_hex) is a


async def test_prepare_returns_none_for_garbage():
    svc = AutomodImageService(types.SimpleNamespace(redis=None, db=None, stats=None))
    assert await svc.prepare(FakeAttachment(b"not an image")) is None


async def test_safesearch_respects_pacing(monkeypatch):
    calls = []

    class Vision:
        async def safe_search(self, *a, **k):
            calls.append(1)

    gw = types.SimpleNamespace(
        vision=Vision(), google_vision_available=lambda: True,
        config=types.SimpleNamespace(model_rate_limits={}),
    )

    async def usage(provider, model):
        return [{"rule": "rpmo", "used": 999, "limit": 1000}]
    gw.rate_limit_usage = usage
    svc = AutomodImageService(types.SimpleNamespace(redis=None, db=None, stats=None, gateway=gw))
    img = prepare_image(_png())
    assert await svc.safesearch(img, guild_id=1, tier="prioritaire") is None
    assert calls == []


# --------------------------------------------------------------------------- #
# image_scam feature
# --------------------------------------------------------------------------- #

async def test_known_scam_hash_sanctions_without_ocr(monkeypatch):
    bot = _bot()
    data = _png()
    img = prepare_image(data)
    bot.automod_images.index.add(HashEntry(9, img.phash, img.dhash, "scam", "block"))
    bot.automod_images._index_loaded_at = 1e18  # fresh
    ocr_calls = []

    async def fake_ocr(*a, **k):
        ocr_calls.append(1)
        return "x"
    monkeypatch.setattr(bot.automod_images, "ocr", fake_ocr)

    feature = ImageScamFeature(FakeModule(bot), {"enabled": True})
    (decision,) = await feature.process(_message([FakeAttachment(data)]))
    assert decision.sanctionnable and decision.categorie == "arnaque_scam"
    assert decision.origine == ORIGINE_IMAGE_HASH and decision.decideur == "equipe"
    assert decision.image.hash_match["id"] == 9
    assert ocr_calls == []


async def test_allowed_hash_is_ignored(monkeypatch):
    bot = _bot()
    data = _png()
    img = prepare_image(data)
    bot.automod_images.index.add(HashEntry(9, img.phash, img.dhash, "scam", "allow"))
    feature = ImageScamFeature(FakeModule(bot), {"enabled": True})
    assert await feature.process(_message([FakeAttachment(data)], content="hello")) == []


async def test_low_risk_image_is_not_ocrd(monkeypatch):
    bot = _bot()
    calls = []

    async def fake_ocr(*a, **k):
        calls.append(1)
        return "Withdrawal Success!"
    monkeypatch.setattr(bot.automod_images, "ocr", fake_ocr)
    # A photo-like image (noise) from a regular member, with text.
    noise = Image.effect_noise((800, 800), 90).convert("RGB")
    buf = io.BytesIO()
    noise.save(buf, "PNG")
    feature = ImageScamFeature(FakeModule(bot), {"enabled": True})
    out = await feature.process(_message([FakeAttachment(buf.getvalue(), width=800, height=800)],
                                         content="regardez mon chat"))
    assert out == [] and calls == []


async def test_scan_all_forces_ocr(monkeypatch):
    engine = FakeEngine(_scam_decision())
    bot = _bot(engine)

    async def fake_ocr(*a, **k):
        return "Withdrawal Success! BANK CARD promo code"
    monkeypatch.setattr(bot.automod_images, "ocr", fake_ocr)
    noise = Image.effect_noise((800, 800), 90).convert("RGB")
    buf = io.BytesIO()
    noise.save(buf, "PNG")
    feature = ImageScamFeature(FakeModule(bot), {"enabled": True, "scan_all": True})
    (decision,) = await feature.process(_message([FakeAttachment(buf.getvalue(), width=800, height=800)],
                                                 content="regardez"))
    assert engine.calls and engine.calls[0][0].origine == ORIGINE_IMAGE_OCR


async def test_risky_image_goes_through_ocr_and_the_text_funnel(monkeypatch):
    engine = FakeEngine(_scam_decision())
    bot = _bot(engine)
    ocr_text = "Withdrawal Success!\nBANK CARO\npromo code BET bonus"

    async def fake_ocr(img, *, guild_id):
        return ocr_text
    monkeypatch.setattr(bot.automod_images, "ocr", fake_ocr)
    feature = ImageScamFeature(FakeModule(bot), {"enabled": True})
    msg = _message([FakeAttachment(_png())], account_days=3)   # fresh account
    (decision,) = await feature.process(msg)
    target, kwargs = engine.calls[0]
    assert target.origine == ORIGINE_IMAGE_OCR and target.content == ocr_text
    assert decision.image is not None and "bank card" in decision.image.ancres
    assert decision.contenu_juge == ocr_text


async def test_ocr_unavailable_never_sanctions(monkeypatch):
    engine = FakeEngine(_scam_decision())
    bot = _bot(engine)

    async def fake_ocr(*a, **k):
        return None
    monkeypatch.setattr(bot.automod_images, "ocr", fake_ocr)
    feature = ImageScamFeature(FakeModule(bot), {"enabled": True})
    assert await feature.process(_message([FakeAttachment(_png())], account_days=3)) == []
    assert engine.calls == []


# --------------------------------------------------------------------------- #
# image_nsfw feature
# --------------------------------------------------------------------------- #

def _nsfw_bot(monkeypatch, likelihoods):
    bot = _bot()
    calls = []

    async def fake_safesearch(img, *, guild_id, tier):
        calls.append(tier)
        return likelihoods
    monkeypatch.setattr(bot.automod_images, "safesearch", fake_safesearch)
    return bot, calls


async def test_nsfw_very_likely_is_sanctioned(monkeypatch):
    bot, calls = _nsfw_bot(monkeypatch, {"adult": "VERY_LIKELY"})
    feature = ImageNsfwFeature(FakeModule(bot), {"enabled": True})
    (d,) = await feature.process(_message([FakeAttachment(_png())]))
    assert d.sanctionnable and d.categorie == "contenu_nsfw" and d.gravite == "haute"
    assert d.origine == ORIGINE_IMAGE_NSFW and d.decideur == "safesearch"
    assert d.image.safesearch["adult"] == "VERY_LIKELY"
    assert calls == ["normal"]    # a regular member → normal tier


async def test_nsfw_possible_is_a_doubt_only(monkeypatch):
    bot, _ = _nsfw_bot(monkeypatch, {"adult": "POSSIBLE"})
    feature = ImageNsfwFeature(FakeModule(bot), {"enabled": True})
    (d,) = await feature.process(_message([FakeAttachment(_png())], account_days=2))
    assert not d.sanctionnable and d.doute == "safesearch_adult_possible"


async def test_nsfw_channels_and_budget_refusals_are_skipped(monkeypatch):
    bot, calls = _nsfw_bot(monkeypatch, {"adult": "VERY_LIKELY"})
    feature = ImageNsfwFeature(FakeModule(bot), {"enabled": True})
    assert await feature.process(_message([FakeAttachment(_png())], nsfw=True)) == []
    assert calls == []
    bot2, _ = _nsfw_bot(monkeypatch, None)
    feature2 = ImageNsfwFeature(FakeModule(bot2), {"enabled": True})
    assert await feature2.process(_message([FakeAttachment(_png())])) == []


def test_features_are_registered_with_default_config():
    assert {"content", "image_nsfw", "image_scam"} <= set(FEATURE_CLASSES)
    module = AutomodModule(types.SimpleNamespace(), 1)
    feats = module.get_default_config()["features"]
    assert feats["image_scam"]["scan_all"] is False and "image_nsfw" in feats


# --------------------------------------------------------------------------- #
# Module glue
# --------------------------------------------------------------------------- #

async def test_doubtful_decision_is_copied_to_the_team_without_a_sanction():
    enq = []

    class Labels:
        async def enqueue(self, **kw):
            enq.append(kw)
    bot = types.SimpleNamespace(automod_labels=Labels(), stats=None)
    module = AutomodModule(bot, 1)
    d = _scam_decision(sanctionnable=False)
    d.doute = "ancres_sans_sanction"
    d.contenu_juge = "ocr text"
    await module.apply_decision(_message([]), d)
    assert enq and enq[0]["motif"] == "doute" and enq[0]["judged_text"] == "ocr text"


def test_judged_text_prefers_aggregate_then_ocr_then_message():
    module = AutomodModule(types.SimpleNamespace(), 1)
    msg = _message([], content="plain")
    d = _scam_decision()
    assert module._judged_text(msg, d) == "plain"
    d.contenu_juge = "ocr"
    assert module._judged_text(msg, d) == "ocr"
    d.agregat_contenu = "agg"
    assert module._judged_text(msg, d) == "agg"


# --------------------------------------------------------------------------- #
# Labeling queue
# --------------------------------------------------------------------------- #

class FakeDB:
    def __init__(self):
        self.items = {}
        self.hashes = []
        self.refs = []
        self.terms = []
        self.revoked = []
        self.events = []
        self.cases = {}
        self.eval = []

    async def find_recent_label_item(self, key):
        for it in self.items.values():
            if it["dedup_key"] == key and it["verdict"] is None:
                return it
        return None

    async def bump_label_item(self, item_id, sanction=None):
        it = self.items[item_id]
        it["occurrences"] += 1
        if sanction:
            it["details"]["sanctions"].append(sanction)
        return it

    async def create_label_item(self, **kw):
        item_id = str(uuid.uuid4())
        self.items[item_id] = {**kw, "id": item_id, "occurrences": 1, "verdict": None,
                               "revoked": False, "categorie_humaine": None,
                               "labeled_by": None, "card_channel_id": None,
                               "card_message_id": None}
        return item_id

    async def get_label_item(self, item_id):
        return self.items.get(item_id)

    async def set_label_item_card(self, item_id, ch, msg):
        self.items[item_id].update(card_channel_id=ch, card_message_id=msg)

    async def label_item(self, item_id, *, verdict, labeled_by, categorie=None):
        it = self.items.get(item_id)
        if it is None or it["verdict"] is not None:
            return None
        it.update(verdict=verdict, labeled_by=labeled_by)
        return it

    async def mark_label_item_revoked(self, item_id):
        self.items[item_id]["revoked"] = True

    async def add_image_hash(self, **kw):
        self.hashes.append(kw)
        return len(self.hashes)

    async def add_learned_reference(self, **kw):
        self.refs.append(kw)
        return len(self.refs)

    async def get_case_by_id(self, case_id):
        return self.cases.get(str(case_id))

    async def revoke_sanction(self, sanction_id, by_type, by_id):
        self.revoked.append((sanction_id, by_type, by_id))
        return True

    async def add_event(self, *a, **k):
        self.events.append((a, k))

    async def create_eval_candidate(self, **kw):
        self.eval.append(kw)
        return "cid"

    async def annotate_eval_candidate(self, cid, verdict, annotated_by=None):
        self.eval[-1]["annotated"] = verdict


class FakeChannel:
    def __init__(self):
        self.sent = []
        self.edits = []

    async def send(self, **kw):
        self.sent.append(kw)
        return types.SimpleNamespace(id=555, channel=types.SimpleNamespace(id=444))

    def get_partial_message(self, message_id):
        channel = self

        class _Partial:
            async def edit(self, **kw):
                channel.edits.append((message_id, kw))
        return _Partial()


def _label_bot(monkeypatch, db=None):
    import config
    monkeypatch.setattr(config, "MODDY_AUTOMOD_LABEL_CHANNEL_ID", 444)
    channel = FakeChannel()
    bot = types.SimpleNamespace(db=db or FakeDB(), redis=None, stats=None,
                                get_channel=lambda cid: channel, get_guild=lambda gid: None)
    bot.automod_images = AutomodImageService(bot)
    return bot, channel


def test_label_kind_and_dedup():
    d = _scam_decision()
    assert label_kind(d) == KIND_TEXTE
    d.origine = ORIGINE_IMAGE_OCR
    assert label_kind(d) == KIND_IMAGE_SCAM
    d.origine = ORIGINE_IMAGE_NSFW
    assert label_kind(d) == KIND_IMAGE_NSFW
    d.origine = ORIGINE_IMAGE_HASH
    d.categorie = "contenu_nsfw"
    assert label_kind(d) == KIND_IMAGE_NSFW
    assert dedup_key(KIND_TEXTE, text="Salut  ") != dedup_key(KIND_TEXTE, text="autre")
    assert dedup_key(KIND_IMAGE_SCAM, phash="ab", guild_id=1) == \
        dedup_key(KIND_IMAGE_SCAM, phash="ab", text="x", guild_id=2)
    assert dedup_key(KIND_TEXTE, text="fdp", guild_id=1) != dedup_key(KIND_TEXTE, text="fdp", guild_id=2)


async def test_enqueue_posts_a_card_then_counts_duplicates(monkeypatch):
    bot, channel = _label_bot(monkeypatch)
    svc = AutomodLabelService(bot)
    msg = _message([])
    d = _scam_decision()
    d.actions = ["ban", "supprimer"]
    first = await svc.enqueue(message=msg, decision=d, motif="sanction", case_id=uuid.uuid4(),
                              applied=["ban"], judged_text="free nitro here")
    assert len(channel.sent) == 1
    second = await svc.enqueue(message=msg, decision=d, motif="sanction", case_id=uuid.uuid4(),
                               applied=["ban"], judged_text="free nitro here")
    assert first == second and len(channel.sent) == 1
    assert channel.edits and channel.edits[0][0] == 555   # card refreshed in place
    item = bot.db.items[first]
    assert item["occurrences"] == 2 and len(item["details"]["sanctions"]) == 2


async def test_queue_is_off_without_a_channel(monkeypatch):
    import config
    monkeypatch.setattr(config, "MODDY_AUTOMOD_LABEL_CHANNEL_ID", 0)
    bot = types.SimpleNamespace(db=FakeDB(), redis=None, get_channel=lambda c: None)
    assert await AutomodLabelService(bot).enqueue(
        message=_message([]), decision=_scam_decision(), motif="doute") is None


async def test_not_sanctionable_on_a_bot_sanction_revokes_it(monkeypatch):
    bot, channel = _label_bot(monkeypatch)
    reversed_ = []

    async def fake_reverse(guild, subject_id, action, *, reason):
        reversed_.append((subject_id, action))
        return True
    monkeypatch.setattr("utils.sanction_reversal.reverse_discord_sanction", fake_reverse)
    svc = AutomodLabelService(bot)
    notified = []

    async def fake_notify(*a, **k):
        notified.append(a)
    monkeypatch.setattr(svc, "_notify_revocation", fake_notify)
    case_id = uuid.uuid4()
    bot.db.cases[str(case_id)] = {"case": {"reference": "A-1"}, "sanctions": [
        {"id": uuid.uuid4(), "status": "active", "action": "ban"},
        {"id": uuid.uuid4(), "status": "revoked", "action": "warn"},
    ]}
    d = _scam_decision()
    d.actions = ["ban", "supprimer"]
    item_id = await svc.enqueue(message=_message([]), decision=d, motif="sanction",
                                case_id=case_id, applied=["ban"], judged_text="hello there")
    row = await svc.label(item_id, verdict="non_sanctionnable", labeler_id=42)
    assert row["outcome"]["revoked"] == 1
    assert len(bot.db.revoked) == 1 and bot.db.revoked[0][1:] == ("moddy_staff", 42)
    assert reversed_ == [(5, "ban")]
    assert bot.db.items[item_id]["revoked"] and notified
    assert bot.db.eval[-1]["annotated"] == "faux_positif"


async def test_labels_never_apply_sanctions_and_teach_hashes(monkeypatch):
    bot, _ = _label_bot(monkeypatch)
    svc = AutomodLabelService(bot)
    learned = []

    async def fake_learn(text, categorie, **kw):
        learned.append((text, categorie))
        return "added"
    monkeypatch.setattr(svc, "learn_reference", fake_learn)
    img = prepare_image(_png())
    d = _scam_decision(sanctionnable=False)
    d.origine = ORIGINE_IMAGE_OCR
    d.doute = "ancres_sans_sanction"
    d.image = ImageMeta(phash=img.phash_hex, dhash=img.dhash_hex)
    item_id = await svc.enqueue(message=_message([]), decision=d, motif="doute",
                                judged_text="Withdrawal Success! promo code")
    row = await svc.label(item_id, verdict="sanctionnable", labeler_id=42)
    assert bot.db.hashes[0]["verdict"] == "block" and bot.db.hashes[0]["kind"] == "scam"
    assert learned == [("Withdrawal Success! promo code", "arnaque_scam")]
    assert "revoked" not in row["outcome"] and bot.db.revoked == []
    # The in-memory index learned it at once: the next identical image is known.
    assert bot.automod_images.index.match(img.phash, img.dhash).entry.verdict == "block"
    # Labeling twice is refused.
    assert await svc.label(item_id, verdict="non_sanctionnable", labeler_id=42) is None


# --------------------------------------------------------------------------- #
# Team card
# --------------------------------------------------------------------------- #

def _row(kind="texte", verdict=None, **details):
    base = {"guild_name": "G", "categorie": "insulte", "gravite": "moyenne",
            "confiance": "high", "decideur": "nano", "signal_source": "regex",
            "score": 0.7, "applied": ["mute"], "sanctions": []}
    base.update(details)
    return {"id": str(uuid.uuid4()), "kind": kind, "motif": "sanction", "guild_id": 1,
            "author_id": 5, "channel_id": 7, "contenu": "t'es nul", "occurrences": 1,
            "verdict": verdict, "labeled_by": 42 if verdict else None,
            "categorie_humaine": None, "revoked": False, "details": base}


def _custom_ids(view):
    out = []
    for item in view.walk_children():
        cid = getattr(item, "custom_id", None)
        if cid:
            out.append(cid)
    return out


def test_card_offers_labels_until_labeled():
    row = _row()
    ids = _custom_ids(render_label_card(row))
    assert any(":yes:" in c for c in ids) and any(":cat:" in c for c in ids)
    assert any(":terms:" in c for c in ids)
    row["verdict"], row["labeled_by"] = "ignore", 42
    assert _custom_ids(render_label_card(row)) == []


def test_nsfw_card_spoilers_the_image_and_has_no_category():
    row = _row(kind="image_nsfw", image={"phash": "ab" * 8, "safesearch": {"adult": "LIKELY"}})
    row["contenu"] = ""
    view = render_label_card(row, image_filename="image_x.jpg")
    ids = _custom_ids(view)
    assert not any(":cat:" in c or ":terms:" in c for c in ids)
    galleries = [i for i in view.walk_children() if i.__class__.__name__ == "MediaGallery"]
    assert galleries and all(it.spoiler for g in galleries for it in g.items)


def test_new_i18n_keys_exist_in_every_locale():
    from utils.i18n import i18n
    keys = [
        "modules.automod_ai.image.reason_nsfw", "modules.automod_ai.label_revoked.dm_body",
        "modules.automod_ai.config.image_scam_label", "automod_label.button.yes",
        "automod_label.terms.mode_compact", "ocr.errors.monthly_cap", "ocr.title",
        "staff.automod.title",
    ]
    for loc in ("fr", "en-US", "es-ES", "pt-BR", "de"):
        for key in keys:
            assert not i18n.get(key, locale=loc).startswith("["), (loc, key)
