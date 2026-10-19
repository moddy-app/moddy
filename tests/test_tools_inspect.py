"""/exif, /sauce and the Find source menu (docs/features/TOOLS_INSPECT.md).

Cards are checked by walking their components, the cog with a fake
interaction; the gateway and the network are never touched.
"""

from __future__ import annotations

import io
import json
import types
from pathlib import Path

import discord
import pytest
from discord import ui
from PIL import Image

from tools import metadata as md
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


def geotagged_jpeg() -> bytes:
    exif = Image.Exif()
    exif[0x010F], exif[0x0110], exif[0x013B] = "Canon", "EOS", "Jane"
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2], gps[3], gps[4] = "N", (48.0, 51.0, 24.0), "E", (2.0, 17.0, 40.0)
    out = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(out, "JPEG", exif=exif)
    return out.getvalue()


def plain_png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(out, "PNG")
    return out.getvalue()


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


@pytest.mark.parametrize("tool", ["exif", "sauce"])
def test_tool_strings_have_the_same_keys_in_the_five_locales(tool):
    reference = set(_locale_keys("en-US", tool))
    for locale in LOCALES:
        assert set(_locale_keys(locale, tool)) == reference, locale


@pytest.mark.parametrize("locale", LOCALES)
def test_every_exif_vocabulary_word_is_translated(locale):
    keys = _locale_keys(locale, "exif")
    for field in md.FIELD_KEYS:
        assert keys.get(f"fields.{field}"), (locale, field)
    for group in md.GROUP_ORDER:
        assert keys.get(f"groups.{group}"), (locale, group)
    for kind in (md.KIND_IMAGE, md.KIND_PDF, md.KIND_OFFICE, md.KIND_AUDIO, md.KIND_VIDEO):
        assert keys.get(f"kinds.{kind}"), (locale, kind)


@pytest.mark.parametrize("locale", LOCALES)
def test_placeholders_survive_translation(locale):
    ref, keys = _locale_keys("en-US", "sauce"), _locale_keys(locale, "sauce")
    import re
    for key, value in ref.items():
        assert set(re.findall(r"\{\w+\}", value)) == set(re.findall(r"\{\w+\}", keys[key])), key


# --------------------------------------------------------------------------- #
# /exif cards
# --------------------------------------------------------------------------- #


def test_geotagged_photo_card_warns_and_offers_the_map():
    report = md.inspect_file(geotagged_jpeg(), "holiday.jpg")
    view = tv.build_exif_card(report, "holiday.jpg", "en-US", raw_token="abcDEF1234")
    body = texts(view)
    assert "GPS" in body and "`48.856667, 2.294444`" in body
    assert "`Canon`" in body and "`holiday.jpg`" in body
    assert any("openstreetmap.org" in u for u in link_urls(view))
    raw = [c for c in view.walk_children() if isinstance(c, tv.ExifRawButton)]
    assert raw and raw[0].item.custom_id == "moddy:tools:exif:raw:abcDEF1234"
    assert raw, "the All tags button is missing"
    assert view.children[0].accent_colour == discord.Colour(tv.ACCENT_WARNING)


def test_card_without_metadata_is_honest_about_discord():
    report = md.inspect_file(plain_png(), "x.png")
    view = tv.build_exif_card(report, "x.png", "en-US")
    body = texts(view)
    assert "Discord" in body and "Nothing found" in body
    assert not link_urls(view)
    assert not [c for c in view.walk_children() if isinstance(c, tv.ExifRawButton)]


def test_long_report_stays_inside_the_message_budget():
    report = md.MetadataReport(kind=md.KIND_PDF, format="PDF", size=10)
    for i in range(120):
        report.fields.append(md.Field(md.GROUP_DOCUMENT, "comment" if i % 2 else "title", "x" * 190 + str(i)))
    view = tv.build_exif_card(report, "big.pdf", "fr")
    assert len(texts(view)) < 4000


@pytest.mark.parametrize("locale", LOCALES)
def test_cards_render_in_every_locale(locale):
    report = md.inspect_file(geotagged_jpeg(), "a.jpg")
    assert "[" not in texts(tv.build_exif_card(report, "a.jpg", locale)).split("\n")[0]
    assert not any(t.startswith("[tools.") for t in texts(tv.build_exif_card(report, "a.jpg", locale)).split())


def test_raw_state_payload_is_none_without_tags():
    assert tv.raw_state_payload(md.MetadataReport(kind="image", format="PNG", size=1), "a.png") is None
    payload = tv.raw_state_payload(md.inspect_file(geotagged_jpeg(), "a.jpg"), "a.jpg")
    assert "GPS" in payload["text"] and payload["name"] == "a.jpg"


def test_raw_button_custom_id_round_trip():
    item = tv.ExifRawButton("abcDEF1234")
    cid = item.item.custom_id
    match = tv.ExifRawButton.__discord_ui_compiled_template__.fullmatch(cid)
    assert match and match["token"] == "abcDEF1234" and len(cid) <= 100


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


async def test_raw_button_sends_the_dump_then_expires():
    bot = types.SimpleNamespace(redis=None)
    token = await tv.save_state(bot, tv.RAW_STATE_KIND, {"name": "a b.jpg", "text": "[EXIF] Make: Canon\n"})
    interaction = FakeInteraction(bot)
    await tv.ExifRawButton(token).callback(interaction)
    sent = interaction.response.sent[0]
    assert sent["ephemeral"] and sent["file"].filename == "metadata-a_b.jpg.txt"

    gone = FakeInteraction(bot)
    await tv.ExifRawButton("zzzzzzzzzz").callback(gone)
    assert gone.response.sent[0]["ephemeral"] and gone.response.sent[0]["view"]


# --------------------------------------------------------------------------- #
# /exif command
# --------------------------------------------------------------------------- #


def make_cog(**bot_attrs):
    from cogs.tools_inspect import ToolsInspect
    tree = types.SimpleNamespace(add_command=lambda c: None, remove_command=lambda *a, **k: None)
    bot = types.SimpleNamespace(tree=tree, redis=None, stats=None, db=None, **bot_attrs)
    return ToolsInspect(bot), bot


async def test_exif_command_is_always_ephemeral_and_reads_the_file():
    cog, bot = make_cog()
    interaction = FakeInteraction(bot)
    await cog.exif_command.callback(cog, interaction, FakeAttachment(geotagged_jpeg(), filename="a.jpg"))
    assert interaction.response.sent[0]["ephemeral"] is True
    assert "48.856667" in texts(interaction.edits[0]["view"])


async def test_exif_command_refuses_unknown_and_oversized_files():
    cog, bot = make_cog()
    interaction = FakeInteraction(bot)
    await cog.exif_command.callback(cog, interaction, FakeAttachment(b"not a known format", filename="a.bin"))
    assert "support" in texts(interaction.edits[0]["view"]).lower()

    big = FakeInteraction(bot)
    await cog.exif_command.callback(cog, big, FakeAttachment(b"x", size=26 * 1024 * 1024))
    assert big.response.sent[0]["ephemeral"]
    assert "25 MB" in texts(big.response.sent[0]["view"])


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


async def test_find_source_menu_reads_the_message_image(available, monkeypatch):
    cog, bot = make_cog()
    search, calls = fake_search(sauce_result())
    monkeypatch.setattr(available, "search", search)
    message = types.SimpleNamespace(attachments=[FakeAttachment(b"x")], embeds=[])
    interaction = FakeInteraction(bot)
    await cog.find_source_context_menu(interaction, message)
    assert calls[0][1]["origin"] == "menu"
    assert interaction.response.sent[0]["ephemeral"] is True  # default preference

    empty = FakeInteraction(bot)
    await cog.find_source_context_menu(empty, types.SimpleNamespace(attachments=[], embeds=[]))
    assert "image" in texts(empty.response.sent[0]["view"]).lower()
    assert empty.response.sent[0]["ephemeral"] is True


# --------------------------------------------------------------------------- #
# Readers (tools/metadata.py) and parsers (tools/sauce.py)
# --------------------------------------------------------------------------- #

_CORE = ('<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/'
         'core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">{}</cp:coreProperties>')


def _docx(core: str) -> bytes:
    import zipfile
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("docProps/core.xml", core)
        zf.writestr("word/document.xml", "<x/>")
    return out.getvalue()


def test_pdf_metadata_is_read():
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    writer.add_metadata({"/Author": "Ann", "/Title": "Plan", "/Producer": "Acme"})
    out = io.BytesIO()
    writer.write(out)
    report = md.inspect_file(out.getvalue(), "plan.pdf")
    found = {(f.group, f.key): f.value for f in report.fields}
    assert report.kind == md.KIND_PDF and report.has_person
    assert found[("author", "author")] == "Ann" and found[("document", "title")] == "Plan"


def test_office_document_properties_are_read():
    report = md.inspect_file(_docx(_CORE.format("<dc:creator>Bob</dc:creator>")), "a.docx")
    assert report.kind == md.KIND_OFFICE
    assert ("author", "author", "Bob") in [(f.group, f.key, f.value) for f in report.fields]


def test_office_xml_with_a_dtd_is_refused_not_expanded():
    core = '<!DOCTYPE x [<!ENTITY a "boom">]>' + _CORE.format("<dc:creator>&a;</dc:creator>")
    report = md.inspect_file(_docx(core), "a.docx")
    assert all("boom" not in f.value for f in report.fields)


def test_unknown_and_empty_files_raise_clear_codes():
    with pytest.raises(md.MetadataError) as unknown:
        md.inspect_file(b"just some text", "a.txt")
    assert unknown.value.code == "unsupported"
    with pytest.raises(md.MetadataError) as empty:
        md.inspect_file(b"", "a.png")
    assert empty.value.code == "unreadable"


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
