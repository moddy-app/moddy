"""/sauce (docs/features/TOOLS_INSPECT.md).

Cards are checked by walking their components, the cog with a fake
interaction; the gateway and the network are never touched.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import discord
import pytest
from discord import ui

from tools import sauce as sc
from utils import tools_inspect_views as tv
from utils.tool_card import ToolError

LOCALES = ("fr", "en-US", "es-ES", "pt-BR", "de")
ROOT = Path(__file__).parent.parent


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def texts(view) -> str:
    return "\n".join(c.content for c in view.walk_children() if isinstance(c, ui.TextDisplay))


def buttons(view):
    return [c for c in view.walk_children() if isinstance(c, ui.Button)]


def link_urls(view):
    return [b.url for b in buttons(view) if getattr(b, "url", None)]


class FakeAttachment:
    def __init__(self, data=b"", *, filename="a.png", content_type="image/png", size=None,
                 url="https://cdn.discordapp.com/attachments/1/2/a.png"):
        self._data, self.filename, self.content_type = data, filename, content_type
        self.size = len(data) if size is None else size
        self.url = url

    async def read(self):
        return self._data


# --------------------------------------------------------------------------- #
# i18n
# --------------------------------------------------------------------------- #


def _flat(node, prefix=""):
    out = {}
    for k, v in node.items():
        key = f"{prefix}.{k}" if prefix else k
        out.update(_flat(v, key) if isinstance(v, dict) else {key: v})
    return out


def _locale_keys(locale, tool):
    data = json.loads((ROOT / "locales" / f"{locale}.json").read_text(encoding="utf-8"))
    return _flat(data["tools"][tool])


@pytest.mark.parametrize("tool", ["sauce"])
def test_tool_strings_have_the_same_keys_in_the_five_locales(tool):
    reference = set(_locale_keys("en-US", tool))
    for locale in LOCALES:
        assert set(_locale_keys(locale, tool)) == reference, locale


@pytest.mark.parametrize("locale", LOCALES)
def test_placeholders_survive_translation(locale):
    ref, keys = _locale_keys("en-US", "sauce"), _locale_keys(locale, "sauce")
    import re
    for key, value in ref.items():
        assert set(re.findall(r"\{\w+\}", value)) == set(re.findall(r"\{\w+\}", keys[key])), key


class FakeResponse:
    def __init__(self):
        self.sent, self._done = [], False

    def is_done(self):
        return self._done

    async def send_message(self, **kwargs):
        self._done = True
        self.sent.append(kwargs)

    async def defer(self, **kwargs):
        self._done = True


class FakeInteraction:
    def __init__(self, bot, *, guild=None, channel=None, user_id=7):
        self.client = bot
        self.response = FakeResponse()
        self.guild, self.guild_id = guild, getattr(guild, "id", None)
        self.channel = channel
        self.user = types.SimpleNamespace(id=user_id)
        self.locale = discord.Locale.american_english
        self.extras = {"incognito": True}
        self.edits = []

    async def edit_original_response(self, **kwargs):
        self.edits.append(kwargs)


def make_cog(**bot_attrs):
    from cogs.tools_inspect import ToolsInspect
    tree = types.SimpleNamespace(add_command=lambda c: None, remove_command=lambda *a, **k: None)
    bot = types.SimpleNamespace(tree=tree, redis=None, stats=None, db=None, **bot_attrs)
    return ToolsInspect(bot), bot


# --------------------------------------------------------------------------- #
# /sauce cards
# --------------------------------------------------------------------------- #

IMG = "https://cdn.discordapp.com/attachments/1/2/a.png"


def sauce_result(**kw):
    base = dict(image_url=IMG, used_saucenao=True, used_tracemoe=True)
    base.update(kw)
    return sc.SauceResult(**base)


def test_sauce_card_best_match_with_other_engines():
    match = sc.SourceMatch(92.0, "Pixiv", title="Moon *girl*", author="Foo",
                           urls=["https://www.pixiv.net/artworks/1"])
    other = sc.SourceMatch(70.0, "Danbooru", title="Alt", urls=["https://danbooru.donmai.us/posts/9"])
    view = tv.build_sauce_card(sauce_result(matches=[match, other]), "en-US", requester_id=5)
    body = texts(view)
    assert "Moon \\*girl\\*" in body and "by Foo" in body and "92%" in body
    assert "<@5>" in body and "Alt" in body
    urls = link_urls(view)
    assert "https://www.pixiv.net/artworks/1" in urls
    for engine in ("lens.google.com", "yandex.com", "bing.com", "tineye.com"):
        assert any(engine in u for u in urls), engine
    assert all(getattr(b, 'url', None) for b in buttons(view))


def test_sauce_card_anime_shows_episode_and_timestamp():
    anime = sc.AnimeMatch(95.0, 154587, "Sousou no Frieren", english="Frieren", episode="5",
                          timestamp="4:32")
    view = tv.build_sauce_card(sauce_result(anime=anime), "en-US")
    body = texts(view)
    assert "Episode `5`" in body and "`4:32`" in body and "95%" in body
    assert "<@" not in body
    assert "https://anilist.co/anime/154587" in link_urls(view)


def test_sauce_card_nothing_found_still_offers_manual_search():
    view = tv.build_sauce_card(sauce_result(failed=["tracemoe"]), "fr")
    body = texts(view)
    assert "Aucune source fiable" in body and "trace.moe" in body
    assert len(link_urls(view)) == 4


def test_sauce_card_says_when_only_anime_could_be_searched():
    only_trace = sauce_result(used_saucenao=False)
    body = texts(tv.build_sauce_card(only_trace, "en-US"))
    assert "Only anime screenshots" in body
    # SauceNAO was asked (or failed): the generic wording stays
    assert "Only anime screenshots" not in texts(tv.build_sauce_card(sauce_result(), "en-US"))
    failed = sauce_result(used_saucenao=False, failed=["saucenao"])
    assert "Only anime screenshots" not in texts(tv.build_sauce_card(failed, "en-US"))


def test_unusable_image_url_yields_no_engine_buttons():
    view = tv.build_sauce_card(sc.SauceResult(image_url="not a url"), "en-US")
    assert link_urls(view) == []


# --------------------------------------------------------------------------- #
# /sauce command
# --------------------------------------------------------------------------- #


def fake_search(result=None, error=None):
    calls = []

    async def search(bot, source, **kw):
        calls.append((source, kw))
        if error:
            raise error
        return result
    return search, calls


@pytest.fixture
def available(monkeypatch):
    from services import sauce_service as ss
    monkeypatch.setattr(ss, "available", lambda bot: (True, True))
    return ss


async def test_sauce_command_runs_the_search_and_hides_nsfw_by_default(available, monkeypatch):
    cog, bot = make_cog()
    search, calls = fake_search(sauce_result())
    monkeypatch.setattr(available, "search", search)
    interaction = FakeInteraction(bot, channel=types.SimpleNamespace())
    await cog.sauce_command.callback(cog, interaction, image=FakeAttachment(b"x"), message=None)
    assert interaction.response.sent[0]["ephemeral"] is True
    assert calls[0][1]["allow_explicit"] is False and calls[0][1]["origin"] == "slash"
    assert "Image source" in texts(interaction.edits[-1]["view"])


async def test_sauce_command_needs_an_image_or_a_link(available):
    cog, bot = make_cog()
    interaction = FakeInteraction(bot)
    await cog.sauce_command.callback(cog, interaction, image=None, message=None)
    assert "image" in texts(interaction.response.sent[0]["view"]).lower()


async def test_sauce_command_rejects_non_images(available):
    cog, bot = make_cog()
    interaction = FakeInteraction(bot)
    await cog.sauce_command.callback(
        cog, interaction, image=FakeAttachment(b"x", filename="a.pdf", content_type="application/pdf"),
        message=None)
    assert "PNG" in texts(interaction.response.sent[0]["view"])


async def test_sauce_unavailable_when_no_provider_is_configured(monkeypatch):
    from services import sauce_service as ss
    monkeypatch.setattr(ss, "available", lambda bot: (False, False))
    cog, bot = make_cog()
    interaction = FakeInteraction(bot)
    await cog.sauce_command.callback(cog, interaction, image=FakeAttachment(b"x"), message=None)
    assert "not available" in texts(interaction.response.sent[0]["view"])


async def test_sauce_quota_error_is_private_and_clear(available, monkeypatch):
    cog, bot = make_cog()
    search, _ = fake_search(error=ToolError("quota", tool="sauce"))
    monkeypatch.setattr(available, "search", search)
    interaction = FakeInteraction(bot)
    await cog.sauce_command.callback(cog, interaction, image=FakeAttachment(b"x"), message=None)
    assert "15 image searches" in texts(interaction.edits[-1]["view"])


async def test_sauce_link_must_point_to_this_server(available):
    cog, bot = make_cog()
    guild = types.SimpleNamespace(id=10)
    interaction = FakeInteraction(bot, guild=guild)
    await cog.sauce_command.callback(cog, interaction, image=None,
                                     message="https://discord.com/channels/99/1/2")
    assert "not in this server" in texts(interaction.edits[-1]["view"])

    bad = FakeInteraction(bot, guild=guild)
    await cog.sauce_command.callback(cog, bad, image=None, message="hello")
    assert "valid message link" in texts(bad.edits[-1]["view"])


async def test_sauce_link_respects_channel_permissions(available):
    cog, bot = make_cog()
    perms = types.SimpleNamespace(view_channel=False, read_message_history=False)
    channel = types.SimpleNamespace(permissions_for=lambda m: perms)
    guild = types.SimpleNamespace(id=10, get_channel_or_thread=lambda i: channel)
    interaction = FakeInteraction(bot, guild=guild)
    interaction.user = discord.Member.__new__(discord.Member)
    with pytest.raises(ToolError) as caught:
        await cog._source_from_link(interaction, "https://discord.com/channels/10/1/2")
    assert caught.value.code == "gone"


# --------------------------------------------------------------------------- #
# Parsers (tools/sauce.py)
# --------------------------------------------------------------------------- #


def test_saucenao_threshold_and_explicit_filter():
    payload = {"results": [
        {"header": {"similarity": "91.2", "index_id": 5, "index_name": "Index #5: Pixiv"},
         "data": {"title": "Moon", "member_name": "Foo", "ext_urls": ["https://www.pixiv.net/artworks/1"]}},
        {"header": {"similarity": "40.0", "index_id": 5}, "data": {"title": "Low"}},
    ]}
    matches = sc.parse_saucenao(payload)
    assert [m.title for m in matches] == ["Moon"] and matches[0].author == "Foo"
    assert sc.parse_saucenao({"results": "nope"}) == []


def test_tracemoe_threshold_episode_and_timestamp():
    payload = {"result": [{"similarity": 0.95, "episode": 5, "from": 272.18,
                           "anilist": {"id": 1, "isAdult": False,
                                       "title": {"romaji": "Frieren", "english": "Frieren: Beyond"}}}]}
    anime = sc.parse_tracemoe(payload)
    assert anime.episode == "5" and anime.timestamp == "4:32" and anime.similarity == 95.0
    payload["result"][0]["similarity"] = 0.80
    assert sc.parse_tracemoe(payload) is None
