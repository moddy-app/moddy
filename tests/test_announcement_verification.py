"""Announcement verification — who may authenticate, which copies get answered.

The feature's promises: only a real Discord relay of a watched support-server
channel counts as a copy; only Managers and Developers can authenticate; a copy
is answered once, whether it arrives before or after the authentication; an
edited announcement loses its authentication.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from cogs import announcement_verification as av
from cogs.announcement_verification import (
    AnnouncementVerification, TranslateAnnouncementButton, VerifyAnnouncementButton,
    content_hash, language_code, relayed_source_id,
)
from config import ANNOUNCEMENT_VERIFICATION_CHANNEL_IDS, MODDY_TEAM_GUILD_ID

SOURCE_CHANNEL = ANNOUNCEMENT_VERIFICATION_CHANNEL_IDS[0]
SOURCE_ID = 1553519871663407145
COPY_ID = 1553519938940174358
TEXT = "# Update to Our Terms of Use"


class FakeDB:
    def __init__(self):
        self.verifications = {}
        self.copies = {}
        self.translations = {}

    async def save_announcement_verification(self, mid, **kw):
        self.verifications[mid] = kw

    async def get_announcement_verification(self, mid):
        return self.verifications.get(mid)

    async def delete_announcement_verification(self, mid):
        return self.verifications.pop(mid, None) is not None

    async def record_announcement_crosspost(self, mid, **kw):
        if mid in self.copies:
            return False
        self.copies[mid] = {**kw, "answered": False}
        return True

    async def mark_announcement_crosspost_answered(self, mid):
        self.copies[mid]["answered"] = True

    async def get_unanswered_announcement_crossposts(self, source_id):
        return [{"message_id": mid, "guild_id": c["guild_id"], "channel_id": c["channel_id"]}
                for mid, c in self.copies.items()
                if c["source_message_id"] == source_id and not c["answered"]]

    async def save_announcement_translations(self, mid, **kw):
        self.translations[mid] = kw

    async def get_announcement_translations(self, mid):
        return self.translations.get(mid)


class FakeChannel:
    def __init__(self, cid=42, guild=None):
        self.id = cid
        self.guild = guild or SimpleNamespace(id=99)
        self.sent = []
        self.reactions = []

    async def send(self, **kw):
        self.sent.append(kw)

    def get_partial_message(self, mid):
        channel = self

        class Partial:
            async def add_reaction(self, emoji):
                channel.reactions.append((mid, emoji))
        return Partial()


def make_bot(db=None, channels=()):
    by_id = {c.id: c for c in channels}
    return SimpleNamespace(db=db or FakeDB(), user=SimpleNamespace(id=1),
                           get_channel=by_id.get, gateway=None)


def relayed_copy(*, flags=2, webhook=123, guild=MODDY_TEAM_GUILD_ID,
                 channel=SOURCE_CHANNEL, content=TEXT, target=None):
    target = target or FakeChannel()
    return SimpleNamespace(
        id=COPY_ID, content=content, webhook_id=webhook,
        flags=discord.MessageFlags._from_value(flags),
        reference=SimpleNamespace(message_id=SOURCE_ID, guild_id=guild, channel_id=channel),
        guild=target.guild, channel=target, author=SimpleNamespace(id=5, bot=True))


@pytest.fixture(autouse=True)
def server_locale(monkeypatch):
    async def fake(bot, guild):
        return "en-US"
    monkeypatch.setattr(av, "_server_locale", fake)


class TestRelayDetection:
    def test_real_relay_of_the_watched_channel(self):
        # flags 1026 is what the relayed Terms of Use announcement carried
        assert relayed_source_id(relayed_copy(flags=1026)) == SOURCE_ID

    @pytest.mark.parametrize("kwargs", [
        {"flags": 0},                    # a webhook cannot set IS_CROSSPOST itself
        {"webhook": None},
        {"guild": 1},                    # relayed from another server
        {"channel": 1},                  # another channel of the support server
    ])
    def test_look_alikes_are_ignored(self, kwargs):
        assert relayed_source_id(relayed_copy(**kwargs)) is None


class TestCopies:
    async def test_copy_of_an_authenticated_announcement_is_answered_once(self):
        db = FakeDB()
        db.verifications[SOURCE_ID] = {"content_hash": content_hash(TEXT)}
        db.translations[SOURCE_ID] = {"translations": {"fr": "Mise à jour"}}
        cog = AnnouncementVerification(make_bot(db))
        message = relayed_copy()

        await cog.on_message(message)
        await cog.on_message(message)

        assert message.channel.reactions == [(COPY_ID, av.STATUS_OK)]
        assert len(message.channel.sent) == 1
        ids = [c.item.custom_id for c in message.channel.sent[0]["view"].walk_children()
               if isinstance(c, discord.ui.DynamicItem)]
        assert ids == [f"moddy:annver:translate:{SOURCE_ID}"]
        assert db.copies[COPY_ID]["answered"]

    async def test_copy_before_authentication_waits_for_the_backlog(self):
        db = FakeDB()
        target = FakeChannel()
        cog = AnnouncementVerification(make_bot(db, [target]))
        await cog.on_message(relayed_copy(target=target))
        assert target.sent == [] and not db.copies[COPY_ID]["answered"]

        db.verifications[SOURCE_ID] = {"content_hash": content_hash(TEXT)}
        av._BACKLOG_PAUSE = 0
        assert await cog.answer_backlog(SOURCE_ID) == 1
        assert target.reactions == [(COPY_ID, av.STATUS_OK)]
        assert await cog.answer_backlog(SOURCE_ID) == 0

    async def test_copy_with_another_text_is_not_confirmed(self):
        db = FakeDB()
        db.verifications[SOURCE_ID] = {"content_hash": content_hash("something else")}
        cog = AnnouncementVerification(make_bot(db))
        message = relayed_copy()
        await cog.on_message(message)
        assert message.channel.sent == [] and message.channel.reactions == []

    async def test_no_translation_means_reaction_only(self):
        db = FakeDB()
        db.verifications[SOURCE_ID] = {"content_hash": content_hash(TEXT)}
        cog = AnnouncementVerification(make_bot(db))
        message = relayed_copy()
        await cog.on_message(message)
        assert len(message.channel.reactions) == 1 and message.channel.sent == []


class TestVerifyButton:
    async def test_non_manager_is_refused(self, monkeypatch):
        async def nope(bot, user_id):
            return False
        monkeypatch.setattr(av, "can_verify", nope)
        sent = []

        async def send_message(**kw):
            sent.append(kw)

        interaction = SimpleNamespace(
            client=make_bot(), user=SimpleNamespace(id=7), locale="en-US",
            response=SimpleNamespace(send_message=send_message))
        await VerifyAnnouncementButton(SOURCE_ID).callback(interaction)
        assert sent and sent[0]["ephemeral"] is True

    async def test_roles_that_may_authenticate(self, monkeypatch):
        from utils import staff_permissions as sp

        class Roles:
            def __init__(self, roles):
                self.roles = roles

            async def get_user_roles(self, user_id):
                return self.roles

        for roles, allowed in [([sp.StaffRole.MANAGER], True), ([sp.StaffRole.DEV], True),
                               ([sp.StaffRole.SUPERVISOR_COM, sp.StaffRole.COMMUNICATION], False),
                               ([], False)]:
            monkeypatch.setattr(sp, "staff_permissions", Roles(roles))
            assert await av.can_verify(None, 7) is allowed


class TestTranslateButton:
    async def _click(self, row, locale):
        sent = []

        async def defer(**kw):
            pass

        async def send(**kw):
            sent.append(kw)

        db = FakeDB()
        if row:
            db.translations[SOURCE_ID] = row
        interaction = SimpleNamespace(
            client=make_bot(db), user=SimpleNamespace(id=7), locale=locale,
            response=SimpleNamespace(defer=defer), followup=SimpleNamespace(send=send))
        await TranslateAnnouncementButton(SOURCE_ID).callback(interaction)
        return [c.content for c in sent[0]["view"].walk_children()
                if isinstance(c, discord.ui.TextDisplay)]

    async def test_shows_the_readers_language(self):
        texts = await self._click({"source_lang": "en", "translations": {"fr": "Bonjour"}}, "fr")
        assert texts == ["Bonjour"]

    async def test_reader_already_in_the_source_language(self):
        texts = await self._click({"source_lang": "en", "translations": {"fr": "Bonjour"}}, "en-US")
        assert texts and "Bonjour" not in texts


class TestEdit:
    async def test_edit_withdraws_the_authentication(self):
        db = FakeDB()
        db.verifications[SOURCE_ID] = {"content_hash": content_hash(TEXT)}
        replies = []

        async def reply(**kw):
            replies.append(kw)

        message = SimpleNamespace(id=SOURCE_ID, guild=SimpleNamespace(id=MODDY_TEAM_GUILD_ID),
                                  reply=reply)
        payload = SimpleNamespace(guild_id=MODDY_TEAM_GUILD_ID, channel_id=SOURCE_CHANNEL,
                                  message_id=SOURCE_ID, data={"content": TEXT + " (edited)"},
                                  message=message, cached_message=None)
        await AnnouncementVerification(make_bot(db)).on_raw_message_edit(payload)
        assert SOURCE_ID not in db.verifications
        assert len(replies) == 1

    async def test_unchanged_text_keeps_it(self):
        db = FakeDB()
        db.verifications[SOURCE_ID] = {"content_hash": content_hash(TEXT)}
        payload = SimpleNamespace(guild_id=MODDY_TEAM_GUILD_ID, channel_id=SOURCE_CHANNEL,
                                  message_id=SOURCE_ID, data={"content": TEXT},
                                  message=SimpleNamespace(content=TEXT), cached_message=None)
        await AnnouncementVerification(make_bot(db)).on_raw_message_edit(payload)
        assert SOURCE_ID in db.verifications


class TestAnnouncementText:
    def test_components_v2_text_is_read(self):
        from discord.components import _component_factory
        container = _component_factory({"type": 17, "id": 1, "components": [
            {"type": 10, "id": 2, "content": "# Update"},
            {"type": 9, "id": 3, "components": [{"type": 10, "id": 4, "content": "Details"}],
             "accessory": {"type": 11, "id": 5, "media": {"url": "https://x/y.png"}}},
        ]}, None)
        message = SimpleNamespace(content="", components=[container], embeds=[])
        assert av.announcement_text(message) == "# Update\n\nDetails"

    def test_embed_text_is_read(self):
        embed = discord.Embed(title="Title", description="Body")
        message = SimpleNamespace(content="", components=[], embeds=[embed])
        assert av.announcement_text(message) == "Title\n\nBody"

    def test_plain_content_wins(self):
        assert av.announcement_text(SimpleNamespace(content=" hi ", components=[], embeds=[])) == "hi"

    async def test_copy_without_readable_text_is_still_answered(self):
        db = FakeDB()
        db.verifications[SOURCE_ID] = {"content_hash": content_hash(TEXT)}
        cog = AnnouncementVerification(make_bot(db))
        message = relayed_copy(content="")
        await cog.on_message(message)
        assert len(message.channel.reactions) == 1


def test_language_code():
    assert [language_code(x) for x in ("en-US", "pt-BR", "fr", "de")] == ["en", "pt", "fr", "de"]


@pytest.mark.parametrize("locale", ["fr", "en-US", "es-ES", "pt-BR", "de"])
def test_every_locale_has_the_same_keys(locale):
    root = Path(__file__).resolve().parent.parent / "locales"

    def keys(d, prefix=""):
        for k, v in d.items():
            if isinstance(v, dict):
                yield from keys(v, f"{prefix}{k}.")
            else:
                yield prefix + k

    ref = set(keys(json.loads((root / "fr.json").read_text())["announcement_verification"]))
    got = set(keys(json.loads((root / f"{locale}.json").read_text())["announcement_verification"]))
    assert got == ref
