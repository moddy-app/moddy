"""Announcement translation — which languages get a button, and what it costs.

The whole feature is two promises: DeepL is called once per announcement (never
once per click), and the announcement's own language never gets a button. Both
are properties of ``_translate_all``, so that is what these tests drive, with a
fake gateway recording every call.
"""

from types import SimpleNamespace

import pytest

from cogs.announcement_translation import (
    LANGUAGES,
    AnnouncementLanguageButton,
    AnnouncementTranslation,
    AnnouncementTranslationView,
    sanitize_mentions,
)


class FakeTranslationClient:
    """Records calls and answers with a fixed detected source language."""

    def __init__(self, detected: str, fail_targets=()):
        self.detected = detected
        self.fail_targets = set(fail_targets)
        self.calls = []

    async def translate(self, text, target_lang, **kwargs):
        self.calls.append(target_lang)
        if target_lang in self.fail_targets:
            raise RuntimeError("provider down")
        return {"text": f"[{target_lang}] {text}",
                "detected_source_language": self.detected}


def make_cog(detected: str, fail_targets=()) -> tuple:
    client = FakeTranslationClient(detected, fail_targets)
    bot = SimpleNamespace(gateway=SimpleNamespace(translation=client))
    return AnnouncementTranslation(bot), client


class TestSourceLanguageHasNoButton:
    """A message is never offered a translation into the language it is in."""

    @pytest.mark.parametrize("detected,expected_missing", [
        ("FR", "fr"),
        ("EN", "en"),
        ("ES", "es"),
        ("PT", "pt"),
        ("DE", "de"),
    ])
    async def test_source_language_is_dropped(self, detected, expected_missing):
        cog, _client = make_cog(detected)
        translations, source = await cog._translate_all("hello", user_id=1)

        assert source == expected_missing
        assert expected_missing not in translations
        assert set(translations) == set(LANGUAGES) - {expected_missing}

    async def test_english_source_keeps_the_other_four(self):
        """The regression: EN is detected on the *first* (French) call.

        The language translated before the source was known has to be dropped
        afterwards, which is what "hello" showing an English button was missing.
        """
        cog, client = make_cog("EN")
        translations, _ = await cog._translate_all("hello", user_id=1)

        assert "en" not in translations
        assert sorted(translations) == ["de", "es", "fr", "pt"]
        # EN-US is never even requested: the skip happens before the call.
        assert "EN-US" not in client.calls

    async def test_identical_result_gets_no_button(self):
        """Safety net for a detection DeepL gets wrong on a short text.

        Whatever DeepL claims the source is, a "translation" that comes back
        character-for-character identical to the announcement would show the
        reader the message they are already looking at.
        """
        class Echo(FakeTranslationClient):
            async def translate(self, text, target_lang, **kwargs):
                self.calls.append(target_lang)
                if target_lang == "EN-US":
                    return {"text": text, "detected_source_language": "IT"}
                return {"text": f"[{target_lang}] {text}",
                        "detected_source_language": "IT"}

        client = Echo("IT")
        cog = AnnouncementTranslation(
            SimpleNamespace(gateway=SimpleNamespace(translation=client)))
        translations, _ = await cog._translate_all("hello", user_id=1)
        assert "en" not in translations

    async def test_unknown_source_keeps_every_language(self):
        """An announcement in a language Moddy does not speak loses nothing."""
        cog, _client = make_cog("IT")
        translations, source = await cog._translate_all("ciao", user_id=1)

        assert source is None
        assert set(translations) == set(LANGUAGES)


class TestCallCount:
    """One call per language, at most — and never one per click."""

    async def test_one_call_per_language_at_most(self):
        cog, client = make_cog("EN")
        await cog._translate_all("hello", user_id=1)
        assert len(client.calls) == len(LANGUAGES) - 1
        assert len(client.calls) == len(set(client.calls))

    async def test_failed_language_gets_no_entry(self):
        cog, _client = make_cog("FR", fail_targets={"DE"})
        translations, _ = await cog._translate_all("bonjour", user_id=1)
        assert "de" not in translations
        assert sorted(translations) == ["en", "es", "pt"]

    async def test_every_language_failing_yields_nothing(self):
        cog, _client = make_cog("FR", fail_targets={
            target for target, _f, _l in LANGUAGES.values()})
        translations, _ = await cog._translate_all("bonjour", user_id=1)
        assert translations == {}


class TestCard:
    """The reply is buttons and nothing else."""

    def test_one_button_per_translation(self):
        view = AnnouncementTranslationView(1234567890123456789, ["en", "es"])
        buttons = [i for i in view.walk_children()
                   if isinstance(i, AnnouncementLanguageButton)]
        assert [b.code for b in buttons] == ["en", "es"]
        assert view.is_persistent()

    def test_no_text_in_the_card(self):
        view = AnnouncementTranslationView(1234567890123456789, list(LANGUAGES))
        payload = view.to_components()
        container = payload[0]
        # A single ActionRow, no TextDisplay: the card is buttons only.
        assert [c["type"] for c in container["components"]] == [1]

    def test_button_label_is_written_in_its_own_language(self):
        button = AnnouncementLanguageButton("de", 1234567890123456789)
        assert button.item.label == "Deutsch"
        assert button.item.custom_id.endswith(":de:1234567890123456789")


class TestSanitizeMentions:
    def test_everyone_cannot_ping_from_a_translation(self):
        assert "@everyone" not in sanitize_mentions("@everyone hi", None)

    def test_ids_become_words(self):
        assert sanitize_mentions("<@123> <@&456>", None) == "@user @role"
