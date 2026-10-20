"""Translate button: detection gating, known languages, bursts, button, config, i18n.

No network and no Discord: fake channels / messages / Redis / gateway
(SimpleNamespace, like tests/test_ocr.py). The detector tests use the real
``py3langid`` model — it is a local dependency, 4.6 MB.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from tools import langdetect as ld

LOCALES = ["fr", "en-US", "es-ES", "pt-BR", "de"]


# --------------------------------------------------------------------------- #
# Cleaning + eligibility
# --------------------------------------------------------------------------- #

class TestEligibility:
    @pytest.mark.parametrize("text", [
        "ok lol",                                   # too short
        "this is fine",                             # 3 words
        "hello hello hello hello hello hello",      # one word repeated
        "https://example.com/some/long/path?x=1 https://example.org/other/page",
        "<@123456789012345678> <#123456789012345678> <@&123456789012345678>",
        "```python\nfor index in range(10):\n    print(index, index * 2)\n```",
        "`some_inline_code(with, args)` `another_call(one, two)`",
        "<:blob:123456789012345678> <:blob:123456789012345679> <a:wave:123456789012345680>",
        "12345 67890 24680 13579 11111 22222",       # no letters
        "",
    ])
    def test_not_worth_a_verdict(self, text):
        assert ld.eligible_text(text) is None

    def test_four_words_is_enough_but_three_is_not(self):
        assert ld.eligible_text("this sentence has enough words") is not None
        assert ld.eligible_text("sentence has words") is None

    def test_links_mentions_and_code_do_not_count_as_words(self):
        # Four real words once the noise is gone...
        text = ("hello <@123456789012345678> everyone https://example.com check this "
                "`code()` out <:blob:123456789012345678>")
        cleaned = ld.clean_text(text)
        assert "http" not in cleaned and "<@" not in cleaned and "code()" not in cleaned
        assert ld.eligible_text(text) is not None
        # ...and with the words removed too, nothing is left.
        assert ld.eligible_text("https://example.com/a/b/c <@123456789012345678> `x = 1` hi") is None

    def test_spoiler_markers_and_everyone_are_stripped(self):
        assert "||" not in ld.clean_text("||this is hidden but readable text here||")
        assert "everyone" not in ld.clean_text("@everyone @here")

    def test_unspaced_scripts_count_by_characters(self):
        assert ld.eligible_text("今日はとても良い天気ですね、散歩に行きましょう") is not None
        assert ld.eligible_text("はい") is None

    def test_long_messages_are_cut(self):
        text = ld.eligible_text("this sentence has enough words " * 200)
        assert text is not None and len(text) <= ld.MAX_DETECT_CHARS

    def test_confidence_is_stricter_on_short_text(self):
        assert ld.required_confidence("tu es trop forte la") == ld.MIN_CONFIDENCE_SHORT
        long_text = "Bonjour tout le monde, comment allez-vous aujourd'hui, mes amis ?"
        assert ld.required_confidence(long_text) == ld.MIN_CONFIDENCE == 0.90


# --------------------------------------------------------------------------- #
# Detector (real model)
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def detector():
    pytest.importorskip("py3langid")
    d = ld.Detector()
    d.load()
    return d


class TestDetector:
    def test_lazy_until_loaded(self):
        d = ld.Detector()
        assert not d.loaded
        assert d.detect("This is clearly an English sentence with words.") is None

    @pytest.mark.parametrize("text, language", [
        ("Hello everyone, how are you doing today my friends?", "en"),
        ("Bonjour tout le monde, comment allez-vous aujourd'hui ?", "fr"),
        ("Das ist ein ganz normaler deutscher Satz zum Testen", "de"),
        ("Eu gosto muito de jogar este jogo com meus amigos", "pt"),
        ("Jag tycker om att spela det här spelet med mina vänner", "sv"),
    ])
    def test_detects_supported_languages(self, detector, text, language):
        found = detector.detect(ld.eligible_text(text))
        assert found is not None and found.language == language
        assert found.confidence >= ld.MIN_CONFIDENCE

    def test_ambiguous_short_text_is_refused_not_mislabelled(self, detector):
        assert detector.detect("tu es trop forte") is None

    def test_only_supported_languages_can_be_answered(self, detector):
        for sample in ("Bonjour tout le monde, comment allez-vous aujourd'hui ?",
                       "Ik hou erg van dit spel en speel het elke dag met vrienden"):
            found = detector.detect(sample)
            assert found is None or found.language in ld.SUPPORTED_LANGUAGES

    def test_low_confidence_returns_none(self):
        d = ld.Detector()
        long_text = "Bonjour tout le monde, comment allez-vous aujourd'hui, mes amis ?"
        d._identifier = SimpleNamespace(classify=lambda text: ("fr", 0.80))
        assert d.detect(long_text) is None
        d._identifier = SimpleNamespace(classify=lambda text: ("fr", 0.95))
        assert d.detect(long_text).language == "fr"
        # A short message needs 0.97, not 0.90.
        assert d.detect("tu es trop forte la") is None
        d._identifier = SimpleNamespace(classify=lambda text: ("fr", 0.98))
        assert d.detect("tu es trop forte la").language == "fr"
        # A language outside the catalogue is never answered.
        d._identifier = SimpleNamespace(classify=lambda text: ("gl", 0.99))
        assert d.detect(long_text) is None

    def test_a_crashing_model_never_breaks_a_message(self):
        def boom(text):
            raise RuntimeError("model exploded")
        d = ld.Detector()
        d._identifier = SimpleNamespace(classify=boom)
        assert d.detect("Bonjour tout le monde, comment allez-vous ?") is None

    def test_supported_languages_all_have_a_flag_and_a_known_code(self, detector):
        assert len(ld.SUPPORTED_LANGUAGES) <= 25   # one Discord select
        for code in ld.SUPPORTED_LANGUAGES:
            assert ld.language_flag(code)
            assert code in detector._identifier.nb_classes


class TestBaseLanguage:
    @pytest.mark.parametrize("value, expected", [
        ("en-US", "en"), ("EN-GB", "en"), ("pt_BR", "pt"), ("fr", "fr"),
        ("nb", "no"), ("nn-NO", "no"), ("", ""), (None, ""),
    ])
    def test_folding(self, value, expected):
        assert ld.base_language(value) == expected

    def test_unknown_language_has_no_flag(self):
        assert ld.language_flag("xx") == ""


# --------------------------------------------------------------------------- #
# Known languages of a server
# --------------------------------------------------------------------------- #

class TestKnownLanguages:
    def test_no_verdict_before_the_sample_is_big_enough(self):
        assert ld.usual_languages({"en": 150, "fr": 49}) == {}      # 199 < 200

    def test_ten_percent_share_threshold(self):
        counts = {"fr": 170, "en": 20, "de": 10}                    # 200 messages
        usual = ld.usual_languages(counts)
        assert set(usual) == {"fr", "en"}                            # en = 10 % exactly
        assert usual["fr"] == pytest.approx(0.85)
        assert "de" not in usual                                     # 5 %
        assert list(usual) == ["fr", "en"]                           # biggest first

    def test_just_under_ten_percent_is_not_usual(self):
        assert "en" not in ld.usual_languages({"fr": 181, "en": 19})

    def test_server_language_extra_and_learnt_are_all_known(self):
        known = ld.known_languages("pt-BR", ["en"], ["ja", "xx".upper()])
        assert {"pt", "en", "ja"} <= known
        assert "fr" not in known

    def test_server_language_alone_before_any_learning(self):
        assert ld.known_languages("fr", {}) == {"fr"}

    def test_window_is_fourteen_days_today_first(self):
        days = ld.window_days(date(2026, 3, 15))
        assert len(days) == 14 == ld.WINDOW_DAYS
        assert days[0] == date(2026, 3, 15) and days[-1] == date(2026, 3, 2)

    def test_merge_ignores_junk(self):
        merged = ld.merge_counts([{"en": "3", "fr": 2, "zz": 50, "de": "x", "es": -4},
                                  {"en": 1}, None])
        assert merged == {"en": 4, "fr": 2}

    def test_sanitize_languages(self):
        assert ld.sanitize_languages(["fr", "EN-GB", "klingon", "fr", None, "nb"]) == ["en", "fr", "no"]
        assert ld.sanitize_languages(None) == []


# --------------------------------------------------------------------------- #
# Burst tracker (reply merging)
# --------------------------------------------------------------------------- #

class TestBurstTracker:
    def _tracker(self, **kw):
        from services.translate_button_service import BurstTracker
        return BurstTracker(**kw)

    def test_consecutive_messages_extend_the_same_prompt(self):
        t = self._tracker()
        assert t.extendable(1, 2, "fr", now=0) is None
        state = t.start(1, 2, prompt_id=900, message_id=10, language="fr", now=0)
        again = t.extendable(1, 2, "fr", now=60)
        assert again is state
        t.extend(state, 11, now=60)
        assert (state.first_id, state.last_id, state.count) == (10, 11, 2)

    def test_window_expires_two_minutes_after_the_last_message(self):
        t = self._tracker()
        state = t.start(1, 2, 900, 10, "fr", now=0)
        t.extend(state, 11, now=100)
        assert t.extendable(1, 2, "fr", now=219) is state       # 119 s after the last one
        assert t.extendable(1, 2, "fr", now=221) is None

    def test_another_language_another_author_or_channel_starts_over(self):
        t = self._tracker()
        t.start(1, 2, 900, 10, "fr", now=0)
        assert t.extendable(1, 2, "de", now=10) is None
        assert t.extendable(1, 3, "fr", now=10) is None
        assert t.extendable(5, 2, "fr", now=10) is None

    def test_cap_on_messages_per_prompt(self):
        t = self._tracker(cap=3)
        state = t.start(1, 2, 900, 10, "fr", now=0)
        t.extend(state, 11, now=1)
        t.extend(state, 12, now=2)
        assert t.extendable(1, 2, "fr", now=3) is None

    def test_forget_prompt_and_bounded_memory(self):
        t = self._tracker(max_entries=8)
        t.start(1, 2, 900, 10, "fr", now=0)
        t.forget_prompt(900)
        assert len(t) == 0
        for i in range(40):
            t.start(1, i, 1000 + i, i, "fr", now=float(i))
        assert len(t) <= 8


# --------------------------------------------------------------------------- #
# Service (fakes)
# --------------------------------------------------------------------------- #

class FakePipeline:
    def __init__(self, redis):
        self.redis, self.ops = redis, []

    def hincrby(self, key, field, n):
        self.ops.append(("hincrby", key, field, n))

    def expire(self, key, ttl):
        self.ops.append(("expire", key, ttl))

    def hgetall(self, key):
        self.ops.append(("hgetall", key))

    async def execute(self):
        results = []
        for op in self.ops:
            if op[0] == "hincrby":
                h = self.redis.hashes.setdefault(op[1], {})
                h[op[2]] = h.get(op[2], 0) + op[3]
            elif op[0] == "expire":
                self.redis.ttls[op[1]] = op[2]
            elif op[0] == "hgetall":
                # decode_responses=True, like the bot's client: str in, str out
                results.append({k: str(v) for k, v in self.redis.hashes.get(op[1], {}).items()})
        return results


class FakeRedis:
    def __init__(self):
        self.hashes, self.ttls, self.kv = {}, {}, {}

    def pipeline(self, transaction=False):
        return FakePipeline(self)

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, value, ex=None):
        self.kv[key] = value


def make_service(redis=None, gateway=None):
    from services.translate_button_service import TranslateButtonService
    bot = SimpleNamespace(redis=redis, gateway=gateway, stats=None, get_cog=lambda name: None)
    service = TranslateButtonService(bot)
    service._today = lambda: date(2026, 3, 15)
    return service


def gateway_with_deepl(translate=None):
    return SimpleNamespace(
        deepl_available=lambda: True,
        translation=SimpleNamespace(translate=translate or AsyncMock(
            return_value={"text": "Hello", "detected_source_language": "FR"})),
    )


class TestLearning:
    async def test_counts_are_flushed_to_redis_with_a_ttl(self):
        redis = FakeRedis()
        service = make_service(redis)
        for _ in range(3):
            service.count(7, "fr")
        service.count(7, "en")
        await service.flush()
        key = "moddy:trbtn:langs:7:20260315"
        assert redis.hashes[key] == {"fr": 3, "en": 1}
        assert redis.ttls[key] == ld.COUNTER_TTL_DAYS * 86400
        assert not service._pending

    async def test_window_reads_fourteen_days_and_pending_counts(self):
        redis = FakeRedis()
        redis.hashes["moddy:trbtn:langs:7:20260315"] = {"fr": 100}
        redis.hashes["moddy:trbtn:langs:7:20260302"] = {"en": 60}      # 13 days ago: in
        redis.hashes["moddy:trbtn:langs:7:20260301"] = {"de": 999}     # 14 days ago: out
        service = make_service(redis)
        service.count(7, "en")
        counts = await service.learnt_counts(7, fresh=True)
        assert counts == {"fr": 100, "en": 61}

    async def test_a_usual_language_gets_no_button_a_rare_one_does(self):
        redis = FakeRedis()
        redis.hashes["moddy:trbtn:langs:7:20260315"] = {"fr": 160, "en": 30, "de": 10}
        service = make_service(redis)
        known = await service.known_languages(7, "fr", extra=["ja"])
        assert known == {"fr", "en", "ja"}
        assert "de" not in known

    async def test_without_redis_counts_stay_in_process(self):
        service = make_service(None)
        service.count(7, "fr")
        await service.flush()
        assert (await service.learnt_counts(7, fresh=True)) == {"fr": 1}

    async def test_learnt_summary_for_the_config_screen(self):
        redis = FakeRedis()
        redis.hashes["moddy:trbtn:langs:7:20260315"] = {"fr": 150, "en": 50}
        learnt = await make_service(redis).learnt(7, fresh=True)
        assert learnt.sample == 200 and set(learnt.usual) == {"fr", "en"}

    async def test_availability_follows_deepl(self):
        assert make_service(None, gateway_with_deepl()).available()
        assert not make_service(None, SimpleNamespace(deepl_available=lambda: False)).available()
        assert not make_service(None, None).available()


# --------------------------------------------------------------------------- #
# Prompts: posting and merging consecutive messages
# --------------------------------------------------------------------------- #

class FakeChannel:
    id = 10

    def __init__(self):
        self.edited = []
        self.replies = []
        channel = self

        class Partial:
            def __init__(self, id_):
                self.id = id_

            async def edit(self, **kwargs):
                channel.edited.append((self.id, kwargs))
                if channel.edit_error:
                    raise channel.edit_error

            async def delete(self):
                channel.deleted.append(self.id)

        self.Partial = Partial
        self.edit_error = None
        self.deleted = []

    def get_partial_message(self, id_):
        return self.Partial(id_)


def make_message(channel, message_id, author_id=2):
    msg = SimpleNamespace(id=message_id, author=SimpleNamespace(id=author_id), channel=channel)

    async def reply(**kwargs):
        channel.replies.append((message_id, kwargs))
        return SimpleNamespace(id=9000 + len(channel.replies))

    msg.reply = reply
    return msg


class TestPrompts:
    async def test_first_message_gets_a_silent_reply_with_the_button(self):
        service, channel = make_service(), FakeChannel()
        assert await service.offer(make_message(channel, 100), "fr", "en-US", 0)
        (replied_to, kwargs), = channel.replies
        assert replied_to == 100
        assert kwargs["mention_author"] is False and kwargs["silent"] is True
        assert kwargs["allowed_mentions"].users is False
        button = _find_button(kwargs["view"])
        assert button.item.custom_id == "moddy:autotr:tr:10:100:100"

    async def test_next_messages_update_the_same_reply(self):
        service, channel = make_service(), FakeChannel()
        await service.offer(make_message(channel, 100), "fr", "en-US", 0)
        await service.offer(make_message(channel, 101), "fr", "en-US", 0)
        await service.offer(make_message(channel, 105), "fr", "en-US", 0)
        assert len(channel.replies) == 1
        prompt_id = 9001
        assert [e[0] for e in channel.edited] == [prompt_id, prompt_id]
        last = _find_button(channel.edited[-1][1]["view"])
        assert last.item.custom_id == "moddy:autotr:tr:10:100:105"

    async def test_another_author_gets_their_own_prompt(self):
        service, channel = make_service(), FakeChannel()
        await service.offer(make_message(channel, 100, author_id=2), "fr", "en-US", 0)
        await service.offer(make_message(channel, 101, author_id=3), "fr", "en-US", 0)
        assert len(channel.replies) == 2 and not channel.edited

    async def test_a_deleted_prompt_is_replaced_by_a_fresh_one(self):
        service, channel = make_service(), FakeChannel()
        await service.offer(make_message(channel, 100), "fr", "en-US", 0)
        channel.edit_error = discord.NotFound(SimpleNamespace(status=404, reason="x"), "gone")
        await service.offer(make_message(channel, 101), "fr", "en-US", 0)
        channel.edit_error = None
        assert len(channel.replies) == 2
        assert _find_button(channel.replies[1][1]["view"]).item.custom_id == "moddy:autotr:tr:10:101:101"

    async def test_no_permission_means_silence(self):
        service, channel = make_service(), FakeChannel()
        msg = make_message(channel, 100)

        async def forbidden(**kwargs):
            raise discord.Forbidden(SimpleNamespace(status=403, reason="x"), "no")

        msg.reply = forbidden
        assert not await service.offer(msg, "fr", "en-US", 0)

    async def test_prompt_is_deleted_after_the_delay(self, monkeypatch):
        import asyncio
        service, channel = make_service(), FakeChannel()
        real_sleep = asyncio.sleep

        async def fast_sleep(seconds):
            await real_sleep(0)

        monkeypatch.setattr("services.translate_button_service.asyncio.sleep", fast_sleep)
        await service.offer(make_message(channel, 100), "fr", "en-US", 600)
        for _ in range(5):
            await real_sleep(0)
        assert channel.deleted == [9001]
        assert len(service.tracker) == 0

    async def test_zero_means_never_delete(self):
        service, channel = make_service(), FakeChannel()
        await service.offer(make_message(channel, 100), "fr", "en-US", 0)
        assert not service._deletions


def _find_button(view):
    for item in view.walk_children():
        if isinstance(item, discord.ui.DynamicItem) or getattr(item, "item", None) is not None:
            return item
        if isinstance(item, discord.ui.Button):
            return SimpleNamespace(item=item)
    raise AssertionError("no button")


# --------------------------------------------------------------------------- #
# Clicks: collecting the burst and translating
# --------------------------------------------------------------------------- #

def fake_message(id_, content, author_id=2, edited_at=None):
    return SimpleNamespace(id=id_, content=content, author=SimpleNamespace(id=author_id),
                           edited_at=edited_at, guild=None, jump_url=f"https://discord.com/channels/1/10/{id_}")


class HistoryChannel:
    id = 10

    def __init__(self, messages):
        self.messages = {m.id: m for m in messages}

    async def fetch_message(self, id_):
        if id_ not in self.messages:
            raise discord.NotFound(SimpleNamespace(status=404, reason="x"), "gone")
        return self.messages[id_]

    async def history(self, limit, oldest_first, after):
        for id_ in sorted(self.messages):
            if id_ > after.id:
                yield self.messages[id_]


GOOD = "Bonjour tout le monde, comment allez-vous aujourd'hui, mes amis ?"


class TestCollect:
    async def test_single_message(self):
        channel = HistoryChannel([fake_message(100, GOOD)])
        got = await make_service().collect_messages(channel, 100, 100)
        assert [m.id for m in got] == [100]

    async def test_burst_keeps_only_the_authors_eligible_messages_in_range(self):
        channel = HistoryChannel([
            fake_message(100, GOOD),
            fake_message(101, "ok"),                        # not eligible
            fake_message(102, GOOD, author_id=3),           # someone else
            fake_message(103, GOOD),
            fake_message(110, GOOD),                        # after the prompt's last message
        ])
        got = await make_service().collect_messages(channel, 100, 103)
        assert [m.id for m in got] == [100, 103]

    async def test_deleted_first_message(self):
        from services.translate_button_service import TranslateError
        with pytest.raises(TranslateError) as exc:
            await make_service().collect_messages(HistoryChannel([]), 100, 100)
        assert exc.value.code == "gone"


class TestTranslate:
    def _interaction(self, locale="en-US", user_id=50):
        return SimpleNamespace(user=SimpleNamespace(id=user_id), locale=locale)

    async def test_translates_for_the_clicker_and_caches(self):
        redis = FakeRedis()
        translate = AsyncMock(return_value={"text": "Hello everyone", "detected_source_language": "FR"})
        service = make_service(redis, gateway_with_deepl(translate))
        channel = HistoryChannel([fake_message(100, GOOD)])
        result = await service.translate_messages(self._interaction("en-US"), channel, 100, 100)
        assert result.target == "EN-US" and result.author_id == 2
        assert result.items[0].text == "Hello everyone" and result.items[0].source == "FR"
        assert not result.from_cache
        translate.assert_awaited_once()
        assert translate.await_args.args[1] == "EN-US"

        again = await service.translate_messages(self._interaction("en-US"), channel, 100, 100)
        assert again.from_cache and translate.await_count == 1
        # Another target language is another cache entry.
        await service.translate_messages(self._interaction("de"), channel, 100, 100)
        assert translate.await_count == 2

    async def test_an_edited_message_is_translated_again(self):
        redis = FakeRedis()
        translate = AsyncMock(return_value={"text": "Hi", "detected_source_language": "FR"})
        service = make_service(redis, gateway_with_deepl(translate))
        interaction = self._interaction()
        await service.translate_messages(interaction, HistoryChannel([fake_message(100, GOOD)]), 100, 100)
        edited = fake_message(100, GOOD + " !", edited_at=datetime(2026, 3, 15, tzinfo=timezone.utc))
        await service.translate_messages(interaction, HistoryChannel([edited]), 100, 100)
        assert translate.await_count == 2

    async def test_user_language_override_wins(self, monkeypatch):
        monkeypatch.setattr("utils.user_preferences.get_language_override", lambda uid: "de")
        service = make_service(None, gateway_with_deepl())
        assert service.deepl_target(self._interaction("en-US")) == "DE"

    @pytest.mark.parametrize("error, code", [
        ("QuotaExceededError", "quota"), ("RateLimitError", "rate_limited"),
        ("APIUnavailableError", "unavailable"), ("GatewayError", "failed"),
    ])
    async def test_gateway_errors_map_to_codes(self, error, code):
        from gateway import errors
        from services.translate_button_service import TranslateError
        from gateway.spec import QuotaTarget
        exc_class = getattr(errors, error)
        args = {"QuotaExceededError": (QuotaTarget.user(1, "translation"),),
                "RateLimitError": ("deepl",), "APIUnavailableError": ("deepl",)}.get(error, ("boom",))
        try:
            exc = exc_class(*args)
        except TypeError:
            exc = exc_class("deepl", "boom")
        service = make_service(None, gateway_with_deepl(AsyncMock(side_effect=exc)))
        with pytest.raises(TranslateError) as caught:
            await service.translate_messages(self._interaction(), HistoryChannel([fake_message(100, GOOD)]), 100, 100)
        assert caught.value.code == code

    async def test_unavailable_without_deepl(self):
        from services.translate_button_service import TranslateError
        service = make_service(None, SimpleNamespace(deepl_available=lambda: False))
        with pytest.raises(TranslateError) as caught:
            await service.translate_messages(self._interaction(), HistoryChannel([fake_message(100, GOOD)]), 100, 100)
        assert caught.value.code == "unavailable"

    async def test_nothing_to_translate(self):
        from services.translate_button_service import TranslateError
        service = make_service(None, gateway_with_deepl())
        with pytest.raises(TranslateError) as caught:
            await service.translate_messages(self._interaction(), HistoryChannel([fake_message(100, "   ")]), 100, 100)
        assert caught.value.code == "empty"

    def test_click_rate_limit(self):
        from services.translate_button_service import CLICKS_PER_MINUTE
        service = make_service()
        assert all(service.allow_click(1) for _ in range(CLICKS_PER_MINUTE))
        assert not service.allow_click(1)
        assert service.allow_click(2)

    def test_mentions_are_neutralised_before_deepl(self):
        service = make_service()
        out = service.neutralize_mentions("hi <@123456789012345678> and @everyone", None)
        assert "<@" not in out and "@everyone" not in out.replace("@​everyone", "")


# --------------------------------------------------------------------------- #
# Button + cards
# --------------------------------------------------------------------------- #

class TestButton:
    def test_custom_id_round_trip(self):
        from utils.translate_button_views import TranslateButton
        item = TranslateButton(111, 222, 333)
        cid = item.item.custom_id
        assert cid == "moddy:autotr:tr:111:222:333"
        match = TranslateButton.__discord_ui_compiled_template__.fullmatch(cid)
        assert match and (match["channel_id"], match["first_id"], match["last_id"]) == ("111", "222", "333")

    async def test_rebuilt_from_a_custom_id(self):
        from utils.translate_button_views import TranslateButton
        match = TranslateButton.__discord_ui_compiled_template__.fullmatch("moddy:autotr:tr:111:222:333")
        rebuilt = await TranslateButton.from_custom_id(MagicMock(), MagicMock(), match)
        assert (rebuilt.channel_id, rebuilt.first_id, rebuilt.last_id) == (111, 222, 333)
        assert rebuilt.item.custom_id == "moddy:autotr:tr:111:222:333"

    def test_prompt_is_one_dim_line_and_a_button(self):
        from utils.translate_button_views import build_prompt_view
        view = build_prompt_view(10, 1, 2, "fr", "en-US")
        text = json.dumps(view.to_components(), ensure_ascii=False)
        assert "French" in text and "detected" in text and "Translate" in text
        assert "\U0001F1EB\U0001F1F7" in text      # the flag

    def test_prompt_follows_the_server_language(self):
        from utils.translate_button_views import build_prompt_view
        text = json.dumps(build_prompt_view(10, 1, 2, "en", "fr").to_components(), ensure_ascii=False)
        assert "Anglais" in text and "détecté" in text and "Traduire" in text

    async def _click(self, monkeypatch, *, service, channel_id=10, guild_id=1):
        from utils.translate_button_views import TranslateButton
        item = TranslateButton(10, 100, 100)
        response = SimpleNamespace(send_message=AsyncMock())
        interaction = SimpleNamespace(
            client=SimpleNamespace(translate_button=service), guild_id=guild_id,
            channel_id=channel_id, channel=object(), user=SimpleNamespace(id=50), locale="en-US",
            response=response, edit_original_response=AsyncMock())
        await item.callback.__wrapped__(item, interaction) if hasattr(item.callback, "__wrapped__") \
            else await item.callback(interaction)
        return interaction

    async def test_click_in_another_channel_is_refused_privately(self, monkeypatch):
        service = make_service(None, gateway_with_deepl())
        interaction = await self._click(monkeypatch, service=service, channel_id=99)
        assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
        interaction.edit_original_response.assert_not_awaited()

    async def test_click_flow_answers_loading_then_the_translation(self, monkeypatch):
        service = make_service(None, gateway_with_deepl())
        result = SimpleNamespace(author_id=2, target="EN-US", from_cache=False, items=[
            SimpleNamespace(message_id=100, text="Hello", source="FR", jump_url="https://x/y")])
        service.translate_messages = AsyncMock(return_value=result)
        interaction = await self._click(monkeypatch, service=service)
        first = interaction.response.send_message.await_args
        assert first.kwargs["ephemeral"] is True
        edit = interaction.edit_original_response.await_args.kwargs
        assert edit["allowed_mentions"].users is False
        text = json.dumps(edit["view"].to_components(), ensure_ascii=False)
        assert "Hello" in text and "DeepL" in text and "<@2>" in text

    async def test_click_error_becomes_a_card(self, monkeypatch):
        from services.translate_button_service import TranslateError
        service = make_service(None, gateway_with_deepl())
        service.translate_messages = AsyncMock(side_effect=TranslateError("gone"))
        interaction = await self._click(monkeypatch, service=service)
        text = json.dumps(interaction.edit_original_response.await_args.kwargs["view"].to_components())
        assert "no longer exists" in text


class TestCards:
    def test_translation_card_merges_several_messages(self):
        from utils.translate_button_views import render_translation_card
        result = SimpleNamespace(author_id=2, target="EN-US", from_cache=False, items=[
            SimpleNamespace(message_id=1, text="First line\nsecond line", source="FR", jump_url="https://x/1"),
            SimpleNamespace(message_id=2, text="Another message", source="FR", jump_url="https://x/2")])
        text = json.dumps(render_translation_card(result, "en-US").to_components())
        assert "First line" in text and "Another message" in text
        assert "> second line" in text.replace("\\n", "\n")
        assert "French" in text and "English" in text

    def test_translation_card_stays_within_limits(self):
        from utils.translate_button_views import render_translation_card
        items = [SimpleNamespace(message_id=i, text="word " * 800, source="FR", jump_url="")
                 for i in range(10)]
        result = SimpleNamespace(author_id=2, target="EN-US", from_cache=False, items=items)
        view = render_translation_card(result, "fr")
        total = sum(len(getattr(c, "content", "") or "") for c in view.walk_children())
        assert total < 4000

    def test_every_error_code_has_text(self):
        from utils.translate_button_views import render_error_card
        for code in ("failed", "unavailable", "rate_limited", "gone", "empty", "quota", "unknown_code"):
            for locale in LOCALES:
                text = json.dumps(render_error_card(code, locale).to_components(), ensure_ascii=False)
                assert "[modules." not in text, (code, locale)


# --------------------------------------------------------------------------- #
# Module
# --------------------------------------------------------------------------- #

def foreign_message(content=GOOD, *, bot=False, webhook=None, channel_id=10, can_send=True,
                    message_type=discord.MessageType.default):
    me = SimpleNamespace(id=1)
    channel = MagicMock()
    channel.id = channel_id
    channel.parent_id = None
    channel.permissions_for.return_value = SimpleNamespace(send_messages=can_send)
    return SimpleNamespace(
        author=SimpleNamespace(bot=bot, id=2), webhook_id=webhook, content=content,
        guild=SimpleNamespace(id=5, me=me), channel=channel, id=77, type=message_type)


class TestModule:
    def test_defaults_and_normalisation(self):
        from modules.translate_button import TranslateButtonModule, normalize_config
        from services.translate_button_service import DEFAULT_DELETE_AFTER
        assert TranslateButtonModule.DEFAULT_ENABLED is False
        assert TranslateButtonModule.REQUIRED_BOT_PERMISSIONS == []
        assert normalize_config(None) == {"ignored_channel_ids": [], "extra_languages": [],
                                          "delete_after": DEFAULT_DELETE_AFTER}
        cfg = normalize_config({"ignored_channel_ids": ["12", 12, "x", 13], "extra_languages": ["fr", "xx", "en"],
                                "delete_after": 300, "state": "paused"})
        assert cfg == {"ignored_channel_ids": [12, 13], "extra_languages": ["en", "fr"],
                       "delete_after": 300, "state": "paused"}
        assert normalize_config({"delete_after": 7})["delete_after"] == DEFAULT_DELETE_AFTER
        assert normalize_config({"delete_after": True})["delete_after"] == DEFAULT_DELETE_AFTER
        assert normalize_config({"delete_after": 0})["delete_after"] == 0     # never delete

    async def test_validate_config(self):
        from modules.translate_button import TranslateButtonModule
        m = TranslateButtonModule(None, 1)
        ok = {"ignored_channel_ids": ["1"], "extra_languages": ["fr"], "delete_after": 600}
        assert (await m.validate_config(ok))[0]
        assert (await m.validate_config({}))[0]
        assert not (await m.validate_config({**ok, "ignored_channel_ids": ["a"]}))[0]
        assert not (await m.validate_config({**ok, "ignored_channel_ids": list(range(26))}))[0]
        assert not (await m.validate_config({**ok, "extra_languages": ["klingon"]}))[0]
        assert not (await m.validate_config({**ok, "delete_after": 42}))[0]
        assert not (await m.validate_config({**ok, "delete_after": True}))[0]

    async def test_channels_and_threads(self):
        from modules.translate_button import TranslateButtonModule
        m = TranslateButtonModule(None, 1)
        await m.load_config({"ignored_channel_ids": [50]})
        assert not m.handles_channel(SimpleNamespace(id=50))
        assert not m.handles_channel(SimpleNamespace(id=51, parent_id=50))
        assert m.handles_channel(SimpleNamespace(id=52, parent_id=None))

    async def _module(self, monkeypatch, *, detection, known=("fr",), config=None, available=True):
        from modules.translate_button import TranslateButtonModule
        service = MagicMock()
        service.available.return_value = available
        service.detect = AsyncMock(return_value=detection)
        service.known_languages = AsyncMock(return_value=set(known))
        service.offer = AsyncMock(return_value=True)
        service.count = MagicMock()
        m = TranslateButtonModule(SimpleNamespace(translate_button=service, stats=None), 5)
        await m.load_config(config or {})
        monkeypatch.setattr("utils.guild_language.guild_locale", AsyncMock(return_value="fr"))
        return m, service

    async def test_foreign_message_gets_a_prompt_and_is_counted(self, monkeypatch):
        m, service = await self._module(monkeypatch, detection=ld.Detection("en", 0.99))
        await m.on_message(foreign_message())
        service.offer.assert_awaited_once()
        assert service.offer.await_args.args[1:] == ("en", "fr", 600)
        service.count.assert_called_once_with(5, "en")

    async def test_known_language_is_counted_but_gets_no_button(self, monkeypatch):
        m, service = await self._module(monkeypatch, detection=ld.Detection("fr", 0.99))
        await m.on_message(foreign_message())
        service.count.assert_called_once_with(5, "fr")
        service.offer.assert_not_awaited()

    async def test_extra_languages_are_passed_to_the_known_rule(self, monkeypatch):
        m, service = await self._module(monkeypatch, detection=ld.Detection("en", 0.99),
                                        config={"extra_languages": ["en"]})
        await m.on_message(foreign_message())
        assert service.known_languages.await_args.args == (5, "fr", ["en"])

    @pytest.mark.parametrize("case", [
        "bot", "webhook", "ignored_channel", "ineligible", "no_detection", "no_deepl",
        "cannot_send", "join_notice", "empty",
    ])
    async def test_stays_silent(self, monkeypatch, case):
        detection = None if case == "no_detection" else ld.Detection("en", 0.99)
        config = {"ignored_channel_ids": [10]} if case == "ignored_channel" else {}
        m, service = await self._module(monkeypatch, detection=detection, config=config,
                                        available=case != "no_deepl")
        kwargs = {}
        if case == "bot":
            kwargs["bot"] = True
        if case == "webhook":
            kwargs["webhook"] = 123
        if case == "cannot_send":
            kwargs["can_send"] = False
        if case == "join_notice":
            kwargs["message_type"] = discord.MessageType.new_member
        content = {"ineligible": "ok lol", "empty": ""}.get(case, GOOD)
        await m.on_message(foreign_message(content, **kwargs))
        service.offer.assert_not_awaited()

    async def test_no_model_run_for_ineligible_text(self, monkeypatch):
        m, service = await self._module(monkeypatch, detection=ld.Detection("en", 0.99))
        await m.on_message(foreign_message("ok lol"))
        service.detect.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Default-enabled in the ModuleManager
# --------------------------------------------------------------------------- #

class FakeDB:
    def __init__(self, modules):
        self.modules = modules

    async def get_guild(self, gid):
        return {"data": {"modules": self.modules}}

    async def update_guild_data(self, gid, path, value):
        self.modules[path.split(".", 1)[1]] = value


class TestDefaultEnabled:
    def _manager(self, modules):
        from modules.module_manager import ModuleManager
        from modules.translate_button import TranslateButtonModule
        bot = SimpleNamespace(db=FakeDB(modules), module_slash_commands={})
        mgr = ModuleManager(bot)
        mgr.register_module(TranslateButtonModule)
        return mgr, bot

    async def test_runs_where_nothing_is_stored_and_can_be_paused(self):
        mgr, bot = self._manager({})
        module = await mgr.get_module_instance(1, "translate_button")
        assert module is not None and module.enabled
        await mgr.load_guild_modules(1)
        ok, err = await mgr.set_module_state(1, "translate_button", "paused")
        assert ok and err is None
        assert bot.db.modules["translate_button"]["state"] == "paused"
        mgr.active_modules.clear()
        assert not (await mgr.get_module_instance(1, "translate_button")).enabled

    def test_module_is_found_by_the_module_directory_scan(self):
        source = Path(__file__).parent.parent / "modules" / "translate_button.py"
        assert source.exists()
        assert "MODULE_ID = \"translate_button\"" in source.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Config view
# --------------------------------------------------------------------------- #

class TestConfigView:
    def _view(self, **kwargs):
        from modules.configs.translate_button_config import TranslateButtonConfigView
        return TranslateButtonConfigView(SimpleNamespace(get_channel=lambda i: None), 5, 9, "fr", **kwargs)

    def test_renders_and_selects_the_current_values(self):
        view = self._view(current_config={"extra_languages": ["en"], "delete_after": 300})
        selects = [c for c in view.walk_children() if isinstance(c, discord.ui.Select)]
        language = next(s for s in selects if s.custom_id.endswith(":languages"))
        assert len(language.options) == len(ld.SUPPORTED_LANGUAGES) <= 25
        assert [o.value for o in language.options if o.default] == ["en"]
        delay = next(s for s in selects if s.custom_id.endswith(":delay"))
        assert [o.value for o in delay.options if o.default] == ["300"]

    def test_learnt_languages_are_shown_read_only(self):
        from services.translate_button_service import Learnt
        view = self._view(learnt=Learnt(sample=400, usual={"fr": 0.8, "en": 0.2}))
        text = json.dumps(view.to_components(), ensure_ascii=False)
        assert "Français" in text and "80 %" in text and "Anglais" in text

    def test_learning_progress_before_the_sample_is_big_enough(self):
        from services.translate_button_service import Learnt
        text = json.dumps(self._view(learnt=Learnt(sample=42)).to_components(), ensure_ascii=False)
        assert "42" in text and "200" in text

    def test_shell_can_be_built_without_arguments(self):
        from modules.configs.translate_button_config import TranslateButtonConfigView
        shell = TranslateButtonConfigView()
        ids = {c.custom_id for c in shell.walk_children() if getattr(c, "custom_id", None)}
        assert {"moddy:translate_button:config:save", "moddy:translate_button:config:cancel",
                "moddy:translate_button:config:back"} <= ids


# --------------------------------------------------------------------------- #
# i18n
# --------------------------------------------------------------------------- #

def _keys(node, prefix=""):
    if isinstance(node, dict):
        out = set()
        for key, value in node.items():
            out |= _keys(value, f"{prefix}.{key}" if prefix else key)
        return out
    return {prefix}


def _leaf(node, dotted):
    for part in dotted.split("."):
        node = node[part]
    return node


def _placeholders(text):
    return set(re.findall(r"{(\w+)}", text))


def _locale(name):
    root = Path(__file__).parent.parent / "locales"
    return json.loads((root / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("locale", ["en-US", "es-ES", "pt-BR", "de"])
def test_i18n_complete_and_placeholders_consistent(locale):
    fr = _locale("fr")["modules"]["translate_button"]
    other = _locale(locale)["modules"]["translate_button"]
    assert _keys(fr) == _keys(other), f"{locale}: {sorted(_keys(fr) ^ _keys(other))}"
    for key in _keys(fr):
        assert _placeholders(_leaf(other, key)) == _placeholders(_leaf(fr, key)), (locale, key)
        assert str(_leaf(other, key)).strip(), (locale, key)


@pytest.mark.parametrize("locale", LOCALES)
def test_every_key_the_code_uses_exists(locale):
    from services.translate_button_service import DELETE_AFTER_OPTIONS
    node = _locale(locale)["modules"]["translate_button"]
    for code in ld.SUPPORTED_LANGUAGES:
        assert node["languages"][code].strip(), (locale, code)
    for seconds in DELETE_AFTER_OPTIONS:
        assert node["config"]["delay"]["options"][str(seconds)].strip(), (locale, seconds)
    for key in ("name", "description", "loading", "prompt.detected", "prompt.button", "card.title",
                "card.jump", "errors.title", "errors.failed", "errors.unavailable",
                "errors.rate_limited", "errors.gone", "errors.empty", "errors.quota",
                "config.title", "config.description", "config.learnt.learning",
                "config.learnt.none", "config.learnt.section_title",
                "config.learnt.section_description"):
        assert _leaf(node, key).strip(), (locale, key)
    assert "{language}" in node["prompt"]["detected"]
    assert "{days}" in node["config"]["learnt"]["section_description"]


def test_button_label_fits_discord_limit():
    for locale in LOCALES:
        assert len(_locale(locale)["modules"]["translate_button"]["prompt"]["button"]) <= 80


def test_source_files_have_no_stray_unicode_emojis():
    root = Path(__file__).parent.parent
    emoji = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
    for rel in ("modules/translate_button.py", "modules/configs/translate_button_config.py",
                "services/translate_button_service.py", "utils/translate_button_views.py"):
        source = (root / rel).read_text(encoding="utf-8")
        code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith(("#", '"""')))
        assert not emoji.search(code), rel
