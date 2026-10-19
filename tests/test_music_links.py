"""Music links: the module (default-enabled), ``/music link`` and the pure core.

Spotify pages, Deezer and iTunes payloads are real captures kept in
``tests/data/music`` (trimmed); the network is faked, nothing leaves the process.
docs/features/MUSIC_LINKS.md
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from modules.music_links import MAX_IGNORED_CHANNELS, MusicLinksModule, normalize_config
from services.music_links_service import MusicLinksError, MusicLinksService
from services.public_api import NETWORK, NOT_FOUND, TIMEOUT, PublicAPIError
from tools import music as mu
from utils.i18n import t
from utils.music_links_views import build_link_card, build_reply_view, reply_line

DATA = Path(__file__).parent / "data" / "music"
ROOT = Path(__file__).parent.parent
LOCALES = ("fr", "en-US", "es-ES", "pt-BR", "de")

TRACK_ID = "4PTG3Z6ehGkBFwjybzWkR8"
ALBUM_ID = "6eUW0wxWtzkFdaEFsTJto6"
ARTIST_ID = "0gxyHStUsqpMadRV0Di1Qt"
PLAYLIST_ID = "37i9dQZF1DXcBWIGoYBM5M"


def page(name: str) -> str:
    return (DATA / name).read_text(encoding="utf-8")


def track_meta(**kw) -> mu.SpotifyMeta:
    base = dict(kind=mu.KIND_TRACK, id=TRACK_ID, title="Never Gonna Give You Up",
                artist="Rick Astley", album="Whenever You Need Somebody", year="1987", duration=214)
    base.update(kw)
    return mu.SpotifyMeta(**base)


def cand(title, artist="Rick Astley", duration=213, url="https://www.deezer.com/track/1", **kw):
    return mu.Candidate(title=title, artist=artist, url=url, duration=duration, **kw)


def texts(view):
    return [c.content for c in view.walk_children() if isinstance(c, discord.ui.TextDisplay)]


def link_buttons(view):
    return [c for c in view.walk_children() if isinstance(c, discord.ui.Button)]


# --------------------------------------------------------------------------- #
# Detecting links
# --------------------------------------------------------------------------- #


class TestFindLinks:
    def test_track_album_artist(self):
        text = (f"https://open.spotify.com/track/{TRACK_ID} "
                f"https://open.spotify.com/album/{ALBUM_ID} "
                f"https://open.spotify.com/artist/{ARTIST_ID}")
        assert [p.ref.kind for p in mu.find_links(text)] == ["track", "album", "artist"]

    @pytest.mark.parametrize("url", [
        f"https://open.spotify.com/intl-fr/track/{TRACK_ID}?si=abc123",
        f"https://open.spotify.com/intl-pt-br/track/{TRACK_ID}",
        f"https://open.spotify.com/embed/track/{TRACK_ID}",
        f"<https://open.spotify.com/track/{TRACK_ID}>",
        f"HTTPS://OPEN.SPOTIFY.COM/track/{TRACK_ID}",
    ])
    def test_variants(self, url):
        (link,) = mu.find_links(f"écoute ça {url} !")
        assert link.type == mu.LINK_REF and link.ref == mu.SpotifyRef("track", TRACK_ID)
        assert link.ref.url == f"https://open.spotify.com/track/{TRACK_ID}"

    def test_short_links(self):
        links = mu.find_links("https://spotify.link/AbC123xyz et https://spoti.fi/Qw3rty")
        assert [(p.type, p.url) for p in links] == [
            (mu.LINK_SHORT, "https://spotify.link/AbC123xyz"),
            (mu.LINK_SHORT, "https://spoti.fi/Qw3rty")]

    def test_unsupported_are_hidden_unless_asked(self):
        text = f"https://open.spotify.com/playlist/{PLAYLIST_ID} https://open.spotify.com/episode/{PLAYLIST_ID[::-1]}"
        assert mu.find_links(text) == []
        assert [p.type for p in mu.find_links(text, include_unsupported=True)] == [mu.LINK_UNSUPPORTED] * 2
        assert mu.is_unsupported_url(f"https://open.spotify.com/show/{PLAYLIST_ID}")
        assert not mu.is_unsupported_url(f"https://open.spotify.com/track/{TRACK_ID}")

    def test_duplicates_and_limit(self):
        url = f"https://open.spotify.com/track/{TRACK_ID}"
        assert len(mu.find_links(f"{url} {url}?si=1 {url}")) == 1
        many = " ".join(f"https://open.spotify.com/track/{'a' * 21}{i}" for i in range(6))
        assert len(mu.find_links(many)) == mu.MAX_LINKS_PER_MESSAGE
        assert len(mu.find_links(many, limit=1)) == 1

    @pytest.mark.parametrize("text", [
        "", None, "no link here", "https://open.spotify.com/track/short",
        "https://evil.example/open.spotify.com/track/" + TRACK_ID,
        "https://open.spotify.com.evil.example/track/" + TRACK_ID,
        "https://deezer.com/track/123",
    ])
    def test_nothing(self, text):
        assert mu.find_links(text) == []

    def test_hint_prefilter(self):
        assert mu.has_spotify_hint("look: https://open.spotify.com/x")
        assert mu.has_spotify_hint("https://spoti.fi/abc")
        assert not mu.has_spotify_hint("hello") and not mu.has_spotify_hint(None)

    def test_ref_from_url(self):
        assert mu.ref_from_url(f"https://open.spotify.com/intl-de/album/{ALBUM_ID}?si=1") == mu.SpotifyRef("album", ALBUM_ID)
        assert mu.ref_from_url("https://example.com") is None


# --------------------------------------------------------------------------- #
# Reading the Spotify page
# --------------------------------------------------------------------------- #


class TestSpotifyPage:
    def test_track(self):
        meta = mu.parse_spotify_page(page("spotify_track.html"), mu.SpotifyRef("track", TRACK_ID))
        assert (meta.title, meta.artist, meta.album, meta.year, meta.duration) == (
            "Never Gonna Give You Up", "Rick Astley", "Whenever You Need Somebody", "1987", 214)
        assert meta.cover.startswith("https://i.scdn.co/")

    def test_album_title_is_cleaned(self):
        meta = mu.parse_spotify_page(page("spotify_album.html"), mu.SpotifyRef("album", ALBUM_ID))
        assert (meta.title, meta.artist, meta.year) == ("Whenever You Need Somebody", "Rick Astley", "1987")

    def test_artist(self):
        meta = mu.parse_spotify_page(page("spotify_artist.html"), mu.SpotifyRef("artist", ARTIST_ID))
        assert meta.title == meta.artist == "Rick Astley"

    def test_wrong_kind_garbage_and_block_pages(self):
        track_html = page("spotify_track.html")
        assert mu.parse_spotify_page(track_html, mu.SpotifyRef("album", ALBUM_ID)) is None
        assert mu.parse_spotify_page("", mu.SpotifyRef("track", TRACK_ID)) is None
        assert mu.parse_spotify_page("<html><head><title>x</title></head></html>",
                                     mu.SpotifyRef("track", TRACK_ID)) is None
        assert mu.parse_spotify_page("\x00<<<>>>", mu.SpotifyRef("track", TRACK_ID)) is None

    def test_cover_must_be_a_spotify_cdn(self):
        html = page("spotify_track.html").replace("https://i.scdn.co/", "https://evil.example/")
        assert mu.parse_spotify_page(html, mu.SpotifyRef("track", TRACK_ID)).cover is None

    def test_clean_album_title(self):
        assert mu.clean_album_title("Listen to Abbey Road - Album by The Beatles | Spotify", "The Beatles") == "Abbey Road"
        assert mu.clean_album_title("Listen to X - EP by Y | Spotify", "Y") == "X"


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #


class TestMatching:
    def test_real_deezer_results_without_the_original_give_no_match(self):
        """Pianoforte, remixes, covers and karaoke must never pass for the song."""
        payload = json.loads((DATA / "deezer_track.json").read_text(encoding="utf-8"))
        candidates = mu.deezer_candidates(payload, mu.KIND_TRACK)
        assert len(candidates) == 25
        assert mu.best_match(track_meta(), candidates) is None

    def test_original_wins_among_the_real_results(self):
        payload = json.loads((DATA / "deezer_track.json").read_text(encoding="utf-8"))
        candidates = mu.deezer_candidates(payload, mu.KIND_TRACK)
        original = cand("Never Gonna Give You Up", url="https://www.deezer.com/track/42")
        assert mu.best_match(track_meta(), [*candidates, original]) is original

    @pytest.mark.parametrize("title", [
        "Never Gonna Give You Up - 2022 Remaster",
        "Never Gonna Give You Up (Remastered 2022)",
        "Never Gonna Give You Up (feat. Someone)",
        "Never Gonna Give You Up (Radio Edit)",
        "never gonna give you up",
    ])
    def test_marketing_noise_is_ignored(self, title):
        assert mu.best_match(track_meta(), [cand(title)]) is not None

    @pytest.mark.parametrize("title", [
        "Never Gonna Give You Up (Live)",
        "Never Gonna Give You Up (Pianoforte)",
        "Never Gonna Give You Up (Acoustic)",
        "Never Gonna Give You Up - Club Remix",
    ])
    def test_a_different_version_is_refused(self, title):
        assert mu.best_match(track_meta(), [cand(title)]) is None

    def test_spotify_says_live_so_live_matches(self):
        meta = track_meta(title="Never Gonna Give You Up (Live)")
        assert mu.best_match(meta, [cand("Never Gonna Give You Up (Live)")]) is not None
        assert mu.best_match(meta, [cand("Never Gonna Give You Up")]) is None

    def test_artist_must_agree(self):
        assert mu.best_match(track_meta(), [cand("Never Gonna Give You Up", artist="Oxyy")]) is None
        assert mu.best_match(track_meta(), [cand("Never Gonna Give You Up", artist="Rick Astley Tribute Band")]) is None

    def test_featuring_artists_are_tolerated(self):
        meta = track_meta(title="Get Lucky", artist="Daft Punk, Pharrell Williams, Nile Rodgers")
        assert mu.best_match(meta, [cand("Get Lucky", artist="Daft Punk")]) is not None
        assert mu.best_match(meta, [cand("Get Lucky", artist="Pharrell Williams")]) is not None

    def test_imitations_are_refused(self):
        for title, artist in (("Never Gonna Give You Up (Karaoke Version)", "Rick Astley"),
                              ("Never Gonna Give You Up", "Rick Astley Tribute"),
                              ("Never Gonna Give You Up - Instrumental", "Rick Astley")):
            assert mu.best_match(track_meta(), [cand(title, artist=artist)]) is None

    def test_accents_and_punctuation(self):
        meta = track_meta(title="Ça plane pour moi", artist="Plastic Bertrand")
        assert mu.best_match(meta, [cand("Ca plane pour moi!", artist="Plastic Bertrand")]) is not None

    def test_duration_separates_equal_titles(self):
        near = cand("Never Gonna Give You Up", duration=214, url="https://www.deezer.com/track/near")
        far = cand("Never Gonna Give You Up", duration=400, url="https://www.deezer.com/track/far")
        assert mu.best_match(track_meta(), [far, near]).url.endswith("near")

    def test_candidates_without_url_are_skipped(self):
        assert mu.best_match(track_meta(), [mu.Candidate("Never Gonna Give You Up", "Rick Astley", "")]) is None

    def test_albums_prefer_the_plain_release(self):
        meta = mu.SpotifyMeta(mu.KIND_ALBUM, ALBUM_ID, "Whenever You Need Somebody", "Rick Astley")
        deluxe = cand("Whenever You Need Somebody (Deluxe Edition)", duration=None, url="https://www.deezer.com/album/2")
        plain = cand("Whenever You Need Somebody", duration=None, url="https://www.deezer.com/album/1")
        assert mu.best_match(meta, [deluxe, plain]) is plain
        assert mu.best_match(meta, [cand("Another Album", duration=None)]) is None
        assert mu.best_match(meta, [cand("Whenever You Need Somebody (Live)", duration=None)]) is None

    def test_artists_need_an_exact_name_and_the_most_fans_win(self):
        meta = mu.SpotifyMeta(mu.KIND_ARTIST, ARTIST_ID, "Rick Astley", "Rick Astley")
        small = cand("Rick Astley", duration=None, url="https://www.deezer.com/artist/2", fans=10)
        big = cand("Rick Astley", duration=None, url="https://www.deezer.com/artist/1", fans=5000)
        assert mu.best_match(meta, [small, big]) is big
        assert mu.best_match(meta, [cand("Rick Astley Band", duration=None)]) is None

    def test_split_title(self):
        parts = mu.split_title("(I Can't Get No) Satisfaction")
        assert parts.core == "i can t get no satisfaction" and not parts.qualifiers
        parts = mu.split_title("Song (Live at Wembley) [Remastered 2011] - Radio Edit")
        assert parts.core == "song" and parts.qualifiers == frozenset({"live at wembley"})

    def test_artist_names(self):
        assert mu.artist_names("A, B") == ["A, B", "A", "B"]
        assert mu.artist_names("") == []


# --------------------------------------------------------------------------- #
# Queries, URLs, payload shapes
# --------------------------------------------------------------------------- #


class TestPlatformHelpers:
    def test_queries(self):
        meta = track_meta(title="Song (Remastered 2009) (Live)")
        assert mu.search_title(meta) == "Song Live"
        assert mu.plain_query(meta) == "Rick Astley Song Live"
        assert mu.deezer_fallback_query(meta) == 'track:"Song Live"'
        assert mu.plain_query(mu.SpotifyMeta(mu.KIND_ARTIST, "x" * 22, "Rick Astley", "Rick Astley")) == "Rick Astley"
        assert mu.deezer_fallback_query(mu.SpotifyMeta(mu.KIND_ARTIST, "x" * 22, "A", "A")) is None

    def test_youtube_music_is_a_search(self):
        url = mu.youtube_music_search_url(track_meta(artist="AC/DC", title="Back & Black"))
        assert url.startswith("https://music.youtube.com/search?q=")
        assert "AC%2FDC" in url and "%26" in url and " " not in url

    def test_url_cleaning(self):
        assert mu.clean_deezer_url("http://deezer.com/track/1?utm=x#a") == "https://www.deezer.com/track/1"
        assert mu.clean_deezer_url("https://evil.example/track/1") is None
        assert mu.clean_deezer_url("javascript:alert(1)") is None
        assert (mu.clean_apple_url("https://music.apple.com/us/album/x/1?i=2&uo=4&app=music")
                == "https://music.apple.com/us/album/x/1?i=2")
        assert (mu.clean_apple_url("https://itunes.apple.com/fr/album/x/1?uo=4")
                == "https://music.apple.com/fr/album/x/1")
        assert mu.clean_apple_url("https://apple.evil.example/a") is None

    def test_deezer_payload_shapes(self):
        payload = {"data": [
            {"title": "T", "link": "https://www.deezer.com/track/9?x=1", "duration": 200,
             "artist": {"name": "A"}, "album": {"title": "Al"}},
            {"title": "Bad host", "link": "https://evil.example/x", "artist": {"name": "A"}},
            "garbage", {"link": None}]}
        (one,) = mu.deezer_candidates(payload, mu.KIND_TRACK)
        assert (one.title, one.artist, one.duration, one.album, one.url) == (
            "T", "A", 200, "Al", "https://www.deezer.com/track/9")
        artists = mu.deezer_candidates(
            {"data": [{"name": "Rick Astley", "link": "https://www.deezer.com/artist/4", "nb_fan": 7}]},
            mu.KIND_ARTIST)
        assert artists[0].title == "Rick Astley" and artists[0].fans == 7
        assert mu.deezer_candidates(None, mu.KIND_TRACK) == [] and mu.deezer_candidates({"data": None}, "track") == []

    def test_itunes_payload_shapes(self):
        payload = {"results": [
            {"trackName": "Never Gonna Give You Up", "artistName": "Rick Astley",
             "collectionName": "Whenever You Need Somebody", "trackTimeMillis": 213573,
             "trackViewUrl": "https://music.apple.com/us/album/never/1?i=2&uo=4"},
            {"trackName": "x", "trackViewUrl": "https://evil.example/"}, 3]}
        (one,) = mu.itunes_candidates(payload, mu.KIND_TRACK)
        assert one.duration == 214 and one.url == "https://music.apple.com/us/album/never/1?i=2"
        assert mu.best_match(track_meta(), [one]) is one
        albums = mu.itunes_candidates({"results": [
            {"collectionName": "Al", "artistName": "A", "collectionViewUrl": "https://music.apple.com/us/album/al/1"}]},
            mu.KIND_ALBUM)
        assert albums[0].title == "Al"
        artists = mu.itunes_candidates({"results": [
            {"artistName": "A", "artistLinkUrl": "https://music.apple.com/us/artist/a/1?uo=4"}]}, mu.KIND_ARTIST)
        assert artists[0].url == "https://music.apple.com/us/artist/a/1"

    def test_format_duration(self):
        assert mu.format_duration(214) == "3:34" and mu.format_duration(3725) == "1:02:05"
        assert mu.format_duration(None) == "" and mu.format_duration(0) == ""


class TestMusicInfo:
    def info(self, **links) -> mu.MusicInfo:
        info = mu.MusicInfo.from_meta(track_meta())
        info.links.update(links)
        return info

    def test_round_trip_and_hostile_cache_entries(self):
        info = self.info(deezer="https://www.deezer.com/track/1", youtube_music="https://music.youtube.com/search?q=x")
        again = mu.MusicInfo.from_dict(json.loads(json.dumps(info.to_dict())))
        assert again == info
        assert mu.MusicInfo.from_dict(None) is None
        assert mu.MusicInfo.from_dict({"v": 2}) is None
        assert mu.MusicInfo.from_dict({"v": 1, "kind": "playlist"}) is None
        assert mu.MusicInfo.from_dict({"v": 1, "kind": "track"}) is None  # missing fields
        poisoned = info.to_dict()
        poisoned["links"] = {"deezer": "javascript:x", "evil": "https://x", "apple_music": "https://music.apple.com/a"}
        assert mu.MusicInfo.from_dict(poisoned).links == {"apple_music": "https://music.apple.com/a"}

    def test_visible_and_worth_posting(self):
        yt_only = self.info(youtube_music="https://music.youtube.com/search?q=x")
        both = self.info(deezer="https://www.deezer.com/track/1", youtube_music="https://music.youtube.com/search?q=x")
        assert mu.visible_platforms(both, mu.PLATFORMS) == ["deezer", "youtube_music"]
        assert mu.visible_platforms(both, ["apple_music"]) == []
        # A card whose only button is a YouTube Music *search* is noise...
        assert not mu.worth_posting(yt_only, mu.PLATFORMS)
        # ...unless that is the one platform the server asked for.
        assert mu.worth_posting(yt_only, ["youtube_music"])
        assert mu.worth_posting(both, mu.PLATFORMS)
        assert not mu.worth_posting(both, ["apple_music"])
        assert [*both.direct_links] == ["deezer"]


# --------------------------------------------------------------------------- #
# Cards
# --------------------------------------------------------------------------- #


def full_info(kind=mu.KIND_TRACK) -> mu.MusicInfo:
    info = mu.MusicInfo(kind=kind, id=TRACK_ID, title="Never Gonna Give You Up", artist="Rick Astley",
                        album="Whenever You Need Somebody", year="1987", duration=214,
                        cover="https://i.scdn.co/image/abc")
    info.links.update({"deezer": "https://www.deezer.com/track/1",
                       "apple_music": "https://music.apple.com/us/album/x/1?i=2",
                       "youtube_music": "https://music.youtube.com/search?q=rick"})
    return info


class TestCards:
    def test_reply_is_one_dim_line_and_link_buttons(self):
        view = build_reply_view([full_info()], mu.PLATFORMS, "en-US")
        (line,) = texts(view)
        assert line.startswith("-# ") and "**Never Gonna Give You Up** — Rick Astley" in line
        buttons = link_buttons(view)
        assert [b.label for b in buttons] == ["Deezer", "Apple Music", "YouTube Music"]
        assert all(b.url and b.style == discord.ButtonStyle.link for b in buttons)
        # discord.Embed is forbidden: it must be a Components V2 layout view.
        assert isinstance(view, discord.ui.LayoutView)

    def test_reply_respects_the_enabled_platforms(self):
        view = build_reply_view([full_info()], ["apple_music"], "en-US")
        assert [b.label for b in link_buttons(view)] == ["Apple Music"]

    def test_reply_is_none_when_nothing_is_worth_it(self):
        info = full_info()
        del info.links["deezer"], info.links["apple_music"]
        assert build_reply_view([info], mu.PLATFORMS, "en-US") is None
        assert build_reply_view([], mu.PLATFORMS, "en-US") is None

    def test_reply_one_container_per_item(self):
        view = build_reply_view([full_info(), full_info(mu.KIND_ALBUM)], mu.PLATFORMS, "fr")
        assert len([c for c in view.children if isinstance(c, discord.ui.Container)]) == 2

    def test_reply_line_tags_and_escaping(self):
        album = full_info(mu.KIND_ALBUM)
        assert "album" in reply_line(album, "en-US")
        artist = mu.MusicInfo(kind=mu.KIND_ARTIST, id=ARTIST_ID, title="Rick Astley", artist="Rick Astley")
        assert "artist" in reply_line(artist, "en-US") and "Rick Astley" in reply_line(artist, "en-US")
        nasty = full_info()
        nasty.title = "**bold** @everyone [x](https://evil.example)"
        line = reply_line(nasty, "en-US")
        assert "\\*\\*bold\\*\\*" in line

    def test_hostile_text_cannot_ping(self):
        nasty = full_info()
        nasty.title = "@everyone"
        view = build_reply_view([nasty], mu.PLATFORMS, "en-US")
        # Defence in depth next to allowed_mentions none: the mention is broken.
        assert "@everyone" not in texts(view)[0]

    @pytest.mark.parametrize("locale", LOCALES)
    def test_link_card(self, locale):
        view = build_link_card(full_info(), locale, requested_by_id=42)
        body = "\n".join(texts(view))
        assert body.startswith("###") and "Never Gonna Give You Up" in body
        assert "`1987`" in body and "`3:34`" in body and "`Whenever You Need Somebody`" in body
        assert "<@42>" in body  # requested by
        assert "[" not in body.split("###")[1].split("\n")[0].replace("](", "")  # no missing i18n key
        buttons = link_buttons(view)
        assert [b.label for b in buttons] == ["Spotify", "Deezer", "Apple Music", "YouTube Music"]
        assert any(isinstance(c, discord.ui.Thumbnail) for c in view.walk_children())

    def test_link_card_without_a_direct_match_explains_the_search_link(self):
        info = full_info()
        del info.links["deezer"], info.links["apple_music"]
        view = build_link_card(info, "en-US")
        assert t("tools.music.no_match", locale="en-US") in "\n".join(texts(view))
        assert [b.label for b in link_buttons(view)] == ["Spotify", "YouTube Music"]

    def test_link_card_artist_has_no_second_line_nor_missing_fields(self):
        info = mu.MusicInfo(kind=mu.KIND_ARTIST, id=ARTIST_ID, title="Rick Astley", artist="Rick Astley")
        info.links["youtube_music"] = "https://music.youtube.com/search?q=rick"
        view = build_link_card(info, "en-US")
        body = "\n".join(texts(view))
        assert "Duration" not in body and "Year" not in body and "\n\nRick Astley\n" not in body

    def test_link_card_public_vs_private_footer(self):
        public = "\n".join(texts(build_link_card(full_info(), "en-US", requested_by_id=7)))
        private = "\n".join(texts(build_link_card(full_info(), "en-US")))
        assert "<@7>" in public and "<@7>" not in private
        assert "Found by" in private  # phrase-style source line, never "Source:"
        assert "Source:" not in private


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


def _get(tree, dotted):
    for part in dotted.split("."):
        tree = tree[part]
    return tree


def _locale(name):
    return json.loads((ROOT / "locales" / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("locale", [l for l in LOCALES if l != "fr"])
@pytest.mark.parametrize("root", ["tools.music", "modules.music_links"])
def test_i18n_keys_and_placeholders_match_french(locale, root):
    fr, other = _get(_locale("fr"), root), _get(_locale(locale), root)
    assert _keys(fr) == _keys(other), sorted(_keys(fr) ^ _keys(other))
    for key in _keys(fr):
        a, b = _get(fr, key), _get(other, key)
        assert isinstance(b, str) and b.strip(), (locale, key)
        assert set(re.findall(r"{(\w+)}", b)) == set(re.findall(r"{(\w+)}", a)), (locale, key)


@pytest.mark.parametrize("locale", LOCALES)
def test_every_key_the_code_uses_resolves(locale):
    keys = [f"tools.music.title.{k}" for k in mu.KINDS]
    keys += [f"tools.music.fields.{k}" for k in ("album", "year", "duration")]
    keys += ["tools.music.no_match"]
    keys += [f"tools.music.errors.{c}" for c in ("not_spotify", "unsupported", "not_found", "unavailable")]
    keys += ["modules.music_links.name", "modules.music_links.description",
             "modules.music_links.reply.artist_tag", "modules.music_links.reply.album_tag"]
    keys += [f"modules.music_links.platforms.{p}" for p in ("spotify", *mu.PLATFORMS)]
    keys += [f"modules.music_links.config.{k}" for k in (
        "title", "description", "platforms.section_title", "platforms.section_description",
        "platforms.placeholder", "channels.section_title", "channels.section_description",
        "channels.placeholder")]
    keys += [f"modules.music_links.config.platforms.{p}_description" for p in mu.PLATFORMS]
    for key in keys:
        assert not t(key, locale=locale).startswith("["), (locale, key)
    for p in mu.PLATFORMS:
        assert len(t(f"modules.music_links.config.platforms.{p}_description", locale=locale)) <= 100


def test_select_labels_fit_discord_limits():
    for locale in LOCALES:
        for p in mu.PLATFORMS:
            assert len(t(f"modules.music_links.platforms.{p}", locale=locale)) <= 100


def test_music_command_is_localized_in_every_locale():
    for path in sorted((ROOT / "locales" / "commands").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))["commands"]
        assert data["music"]["name"] and data["music"]["description"], path.name
        link = data["music link"]
        assert link["name"] and link["description"], path.name
        assert link["parameters"]["url"]["name"], path.name
    fr = json.loads((ROOT / "locales" / "commands" / "fr.json").read_text(encoding="utf-8"))["commands"]
    assert fr["music"]["name"] == "musique"


# --------------------------------------------------------------------------- #
# Module
# --------------------------------------------------------------------------- #


class TestModuleConfig:
    def test_defaults_and_normalisation(self):
        assert MusicLinksModule.DEFAULT_ENABLED is True
        assert MusicLinksModule.REQUIRED_BOT_PERMISSIONS == []
        assert normalize_config(None) == {"platforms": list(mu.PLATFORMS), "ignored_channel_ids": []}
        cfg = normalize_config({"platforms": ["apple_music", "bogus", "deezer"],
                                "ignored_channel_ids": ["12", 12, "x", 13], "state": "paused"})
        assert cfg == {"platforms": ["deezer", "apple_music"], "ignored_channel_ids": [12, 13],
                       "state": "paused"}
        # An empty list can only be stale: a module with no button answers nothing.
        assert normalize_config({"platforms": []})["platforms"] == list(mu.PLATFORMS)
        assert len(normalize_config({"ignored_channel_ids": list(range(40))})["ignored_channel_ids"]) == MAX_IGNORED_CHANNELS

    async def test_validate_config(self):
        m = MusicLinksModule(None, 1)
        assert (await m.validate_config({"platforms": ["deezer"], "ignored_channel_ids": []}))[0]
        assert not (await m.validate_config({"platforms": []}))[0]
        assert not (await m.validate_config({"platforms": ["spotify"]}))[0]
        assert not (await m.validate_config({"platforms": ["deezer"], "ignored_channel_ids": ["a"]}))[0]
        assert not (await m.validate_config({"platforms": ["deezer"],
                                             "ignored_channel_ids": list(range(26))}))[0]

    async def test_load_config_and_channels(self):
        m = MusicLinksModule(None, 1)
        assert await m.load_config({"platforms": ["apple_music"], "ignored_channel_ids": [50]})
        assert m.platforms == ["apple_music"] and m.enabled
        assert not m.handles_channel(SimpleNamespace(id=50))
        assert not m.handles_channel(SimpleNamespace(id=51, parent_id=50))  # a thread follows its parent
        assert m.handles_channel(SimpleNamespace(id=52, parent_id=None))


def make_message(content, *, bot=False, webhook=None, channel_id=10, can_send=True, guild=True):
    me = SimpleNamespace(id=1)
    channel = MagicMock()
    channel.id = channel_id
    channel.parent_id = None
    channel.permissions_for.return_value = SimpleNamespace(send_messages=can_send)
    return SimpleNamespace(
        content=content, author=SimpleNamespace(bot=bot), webhook_id=webhook,
        guild=SimpleNamespace(id=5, me=me) if guild else None, channel=channel,
        reply=AsyncMock())


class FakeService:
    """Stands in for ``bot.music_links``: runs spawned work inline."""

    def __init__(self, infos=None):
        self.infos = infos if infos is not None else {}
        self.spawned = 0
        self.recorded = []

    def spawn(self, coro, **_):
        self.spawned += 1
        return asyncio.ensure_future(coro)

    async def refs_from_text(self, text, **_):
        return [p.ref for p in mu.find_links(text) if p.ref]

    async def resolve_quiet(self, ref, **_):
        return self.infos.get(ref.id)

    def record(self, metric, **dims):
        self.recorded.append((metric, dims))


class TestModuleRuntime:
    async def _module(self, monkeypatch, infos, config=None):
        service = FakeService(infos)
        bot = SimpleNamespace(music_links=service, stats=None)
        m = MusicLinksModule(bot, 5)
        await m.load_config(config or {})
        monkeypatch.setattr("utils.guild_language.guild_locale", AsyncMock(return_value="fr"))
        return m, service

    async def _run(self, m, message, service):
        await m.on_message(message)
        for task in list(asyncio.all_tasks()):
            if task is not asyncio.current_task() and not task.done():
                await task

    async def test_replies_silently_under_the_message(self, monkeypatch):
        m, service = await self._module(monkeypatch, {TRACK_ID: full_info()})
        message = make_message(f"écoute https://open.spotify.com/track/{TRACK_ID}")
        await self._run(m, message, service)
        message.reply.assert_awaited_once()
        kwargs = message.reply.await_args.kwargs
        assert kwargs["mention_author"] is False and kwargs["silent"] is True
        assert kwargs["allowed_mentions"].users is False and kwargs["allowed_mentions"].everyone is False
        assert [b.label for b in link_buttons(kwargs["view"])] == ["Deezer", "Apple Music", "YouTube Music"]
        assert ("posted", {"kind": "track"}) in service.recorded

    async def test_platforms_setting_is_applied(self, monkeypatch):
        m, service = await self._module(monkeypatch, {TRACK_ID: full_info()}, {"platforms": ["deezer"]})
        message = make_message(f"https://open.spotify.com/track/{TRACK_ID}")
        await self._run(m, message, service)
        assert [b.label for b in link_buttons(message.reply.await_args.kwargs["view"])] == ["Deezer"]

    @pytest.mark.parametrize("case", [
        "bot_author", "webhook", "ignored_channel", "no_link", "dm", "cannot_send", "unresolved", "disabled",
        "youtube_only_result",
    ])
    async def test_stays_silent(self, monkeypatch, case):
        infos = {TRACK_ID: full_info()}
        config = {}
        content = f"https://open.spotify.com/track/{TRACK_ID}"
        kwargs = {}
        if case == "bot_author":
            kwargs["bot"] = True
        if case == "webhook":
            kwargs["webhook"] = 123
        if case == "ignored_channel":
            config = {"ignored_channel_ids": [10]}
        if case == "no_link":
            content = "hello world"
        if case == "dm":
            kwargs["guild"] = False
        if case == "cannot_send":
            kwargs["can_send"] = False
        if case == "unresolved":
            infos = {}
        if case == "youtube_only_result":
            info = full_info()
            del info.links["deezer"], info.links["apple_music"]
            infos = {TRACK_ID: info}
        m, service = await self._module(monkeypatch, infos, config)
        if case == "disabled":
            m.enabled = False
        message = make_message(content, **kwargs)
        await self._run(m, message, service)
        message.reply.assert_not_awaited()

    async def test_playlist_is_ignored(self, monkeypatch):
        m, service = await self._module(monkeypatch, {})
        message = make_message(f"https://open.spotify.com/playlist/{PLAYLIST_ID}")
        await self._run(m, message, service)
        message.reply.assert_not_awaited()

    async def test_deleted_message_or_missing_permission_is_swallowed(self, monkeypatch):
        m, service = await self._module(monkeypatch, {TRACK_ID: full_info()})
        for exc in (discord.Forbidden(MagicMock(status=403), "no"),
                    discord.NotFound(MagicMock(status=404), "gone")):
            message = make_message(f"https://open.spotify.com/track/{TRACK_ID}")
            message.reply.side_effect = exc
            await self._run(m, message, service)  # must not raise

    async def test_on_message_never_waits_for_the_network(self, monkeypatch):
        m, service = await self._module(monkeypatch, {TRACK_ID: full_info()})
        gate = asyncio.Event()

        async def slow(text, **_):
            await gate.wait()
            return []

        service.refs_from_text = slow
        message = make_message(f"https://open.spotify.com/track/{TRACK_ID}")
        await asyncio.wait_for(m.on_message(message), 1)  # returns at once
        assert service.spawned == 1
        gate.set()
        await asyncio.sleep(0)


# --------------------------------------------------------------------------- #
# Default-enabled behaviour in the ModuleManager
# --------------------------------------------------------------------------- #


class FakeDB:
    def __init__(self, modules):
        self.modules = modules

    async def get_guild(self, gid):
        return {"data": {"modules": self.modules}}

    async def update_guild_data(self, gid, path, value):
        self.modules[path.split(".", 1)[1]] = value


def make_manager(modules):
    from modules.module_manager import ModuleManager
    bot = SimpleNamespace(db=FakeDB(modules), module_slash_commands={})
    mgr = ModuleManager(bot)
    mgr.register_module(MusicLinksModule)
    return mgr, bot


class TestDefaultEnabled:
    def test_is_discovered_by_the_manager(self):
        from modules.module_manager import ModuleManager
        mgr = ModuleManager(SimpleNamespace(db=FakeDB({}), module_slash_commands={}))
        mgr.discover_modules()
        assert mgr.registered_modules["music_links"] is MusicLinksModule

    async def test_runs_where_nothing_is_stored(self):
        mgr, _ = make_manager({})
        module = await mgr.get_module_instance(1, "music_links")
        assert module is not None and module.enabled and module.state == "active"
        assert module.platforms == list(mu.PLATFORMS)
        assert await mgr.get_module_state(1, "music_links") == "active"

    async def test_pause_survives_a_restart(self):
        mgr, bot = make_manager({})
        await mgr.load_guild_modules(1)
        ok, err = await mgr.set_module_state(1, "music_links", "paused")
        assert ok and err is None
        assert bot.db.modules["music_links"]["state"] == "paused"
        assert not mgr.active_modules[1]["music_links"].enabled
        mgr.active_modules.clear()
        assert not (await mgr.get_module_instance(1, "music_links")).enabled

    async def test_deleted_config_goes_back_to_defaults(self):
        mgr, _ = make_manager({"music_links": {"platforms": ["deezer"], "state": "paused"}})
        await mgr.load_guild_modules(1)
        assert not mgr.active_modules[1]["music_links"].enabled
        assert await mgr.delete_module_config(1, "music_links")
        module = mgr.active_modules[1]["music_links"]
        assert module.enabled and module.platforms == list(mu.PLATFORMS)

    async def test_manage_panel_available_before_any_save(self):
        from modules.configs._common import build_manage_view
        mgr, bot = make_manager({})
        bot.module_manager = mgr
        assert await build_manage_view(bot, 1, "fr", "music_links") is not None


# --------------------------------------------------------------------------- #
# /config screen
# --------------------------------------------------------------------------- #


class TestConfigView:
    def test_shell_renders_every_custom_id_once(self):
        from modules.configs.music_links_config import MusicLinksConfigView
        view = MusicLinksConfigView()
        ids = [c.custom_id for c in view.walk_children() if getattr(c, "custom_id", None)]
        assert len(ids) == len(set(ids))
        assert {"moddy:music_links:config:platforms", "moddy:music_links:config:channels",
                "moddy:music_links:config:save", "moddy:music_links:config:cancel",
                "moddy:music_links:config:back"} <= set(ids)
        assert all(i.startswith("moddy:") for i in ids)
        assert view.timeout is None and MusicLinksConfigView.__persistent__

    def test_platform_select_shows_the_current_setting(self):
        from modules.configs.music_links_config import MusicLinksConfigView
        view = MusicLinksConfigView(MagicMock(get_channel=lambda _i: None), 1, 2, "fr",
                                    current_config={"platforms": ["apple_music"]})
        select = next(c for c in view.walk_children()
                      if getattr(c, "custom_id", "") == "moddy:music_links:config:platforms")
        assert [o.value for o in select.options if o.default] == ["apple_music"]
        assert select.min_values == 1

    def test_registered_for_persistence(self):
        import utils.music_links_views as views
        from modules.configs.music_links_config import MusicLinksConfigView
        assert views.PERSISTENT_VIEWS == [MusicLinksConfigView]
        assert views.DYNAMIC_ITEM_CASES == []


# --------------------------------------------------------------------------- #
# Service (network faked)
# --------------------------------------------------------------------------- #


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value


class FakeResponse:
    def __init__(self, status=302, headers=None):
        self.status = status
        self.headers = headers or {}


class FakePublicAPI:
    def __init__(self):
        self.spotify_html = page("spotify_track.html")
        self.spotify_error = None
        self.deezer_payload = {"data": [{
            "title": "Never Gonna Give You Up", "link": "https://www.deezer.com/track/42", "duration": 213,
            "artist": {"name": "Rick Astley"}, "album": {"title": "x"}}]}
        self.itunes_payload = {"results": [{
            "trackName": "Never Gonna Give You Up", "artistName": "Rick Astley", "trackTimeMillis": 213000,
            "collectionName": "x", "trackViewUrl": "https://music.apple.com/us/album/n/1?i=2&uo=4"}]}
        self.deezer_error = None
        self.itunes_error = None
        self.redirects = {}
        self.calls = []

    async def get_text(self, url, **kw):
        self.calls.append(url)
        if self.spotify_error:
            raise self.spotify_error
        return self.spotify_html

    async def get_json(self, url, **kw):
        self.calls.append(url)
        if "deezer" in url:
            if self.deezer_error:
                raise self.deezer_error
            return self.deezer_payload
        if self.itunes_error:
            raise self.itunes_error
        return self.itunes_payload

    async def request(self, method, url, **kw):
        self.calls.append(url)
        if url not in self.redirects:
            raise PublicAPIError(NOT_FOUND, status=404, url=url)
        return FakeResponse(302, {"Location": self.redirects[url]})


def make_service(api=None, redis=None):
    api = api or FakePublicAPI()
    stats = MagicMock()
    bot = SimpleNamespace(public_api=api, redis=redis, stats=stats)
    return MusicLinksService(bot), api, stats


class TestService:
    REF = mu.SpotifyRef("track", TRACK_ID)

    async def test_full_resolution(self):
        service, api, stats = make_service()
        info = await service.resolve(self.REF, source="command")
        assert info.title == "Never Gonna Give You Up" and info.duration == 214
        assert info.links == {
            "youtube_music": mu.youtube_music_search_url(track_meta()),
            "deezer": "https://www.deezer.com/track/42",
            "apple_music": "https://music.apple.com/us/album/n/1?i=2"}
        assert not info.partial
        stats.incr.assert_called_with("music_links.resolved", dims={"kind": "track", "source": "command"})

    async def test_result_is_cached(self):
        service, api, _ = make_service(redis=FakeRedis())
        await service.resolve(self.REF)
        calls = len(api.calls)
        again = await service.resolve(self.REF)
        assert len(api.calls) == calls and again.links["deezer"].endswith("/42")

    async def test_local_cache_without_redis(self):
        service, api, _ = make_service()
        await service.resolve(self.REF)
        calls = len(api.calls)
        await service.resolve(self.REF)
        assert len(api.calls) == calls

    async def test_concurrent_requests_share_one_resolution(self):
        service, api, _ = make_service()
        await asyncio.gather(*(service.resolve(self.REF) for _ in range(5)))
        assert api.calls.count(self.REF.url) == 1

    async def test_no_match_still_gives_youtube_search(self):
        service, api, _ = make_service()
        api.deezer_payload = {"data": []}
        api.itunes_payload = {"results": []}
        info = await service.resolve(self.REF)
        assert list(info.links) == ["youtube_music"] and not info.partial

    async def test_a_failing_platform_is_left_out_and_cached_briefly(self):
        redis = FakeRedis()
        service, api, _ = make_service(redis=redis)
        api.deezer_error = PublicAPIError(TIMEOUT)
        info = await service.resolve(self.REF)
        assert "deezer" not in info.links and "apple_music" in info.links and info.partial

    async def test_deezer_error_payload_is_a_failure_not_a_match(self):
        service, api, _ = make_service()
        api.deezer_payload = {"error": {"type": "Exception", "message": "Quota limit exceeded", "code": 4}}
        info = await service.resolve(self.REF)
        assert "deezer" not in info.links and info.partial

    async def test_deezer_falls_back_to_the_single_field_query(self):
        service, api, _ = make_service()
        answers = [{"data": []}, {"data": api.deezer_payload["data"]}]

        async def get_json(url, **kw):
            api.calls.append((url, kw.get("params")))
            if "deezer" in url:
                return answers.pop(0)
            return {"results": []}

        api.get_json = get_json
        info = await service.resolve(self.REF)
        queries = [c[1]["q"] for c in api.calls[1:] if isinstance(c, tuple) and "deezer" in c[0]]
        assert queries == ["Rick Astley Never Gonna Give You Up", 'track:"Never Gonna Give You Up"']
        assert info.links["deezer"].endswith("/42")

    async def test_itunes_budget_leaves_apple_out_instead_of_hammering(self):
        from services import music_links_service as svc
        service, api, _ = make_service()
        service._itunes_calls.extend([time.monotonic()] * svc.ITUNES_BUDGET)
        info = await service.resolve(self.REF)
        assert "apple_music" not in info.links and "deezer" in info.links and info.partial

    @pytest.mark.parametrize("error,code", [
        (PublicAPIError(NOT_FOUND, status=404), "not_found"),
        (PublicAPIError(TIMEOUT), "unavailable"),
        (PublicAPIError(NETWORK), "unavailable"),
        (PublicAPIError("http_error", status=403), "unavailable"),
    ])
    async def test_unreadable_spotify_page(self, error, code):
        service, api, stats = make_service()
        api.spotify_error = error
        with pytest.raises(MusicLinksError) as caught:
            await service.resolve(self.REF, source="auto")
        assert caught.value.code == code
        stats.incr.assert_called_with("music_links.failed", dims={"reason": code, "source": "auto"})
        assert await service.resolve_quiet(self.REF) is None

    async def test_page_without_metadata_is_unavailable(self):
        service, api, _ = make_service()
        api.spotify_html = "<html><head><title>x</title></head></html>"
        with pytest.raises(MusicLinksError) as caught:
            await service.resolve(self.REF)
        assert caught.value.code == "unavailable"

    async def test_failures_are_remembered_so_a_blocked_ip_is_not_hammered(self):
        service, api, _ = make_service(redis=FakeRedis())
        api.spotify_error = PublicAPIError("http_error", status=403)
        for _ in range(3):
            with pytest.raises(MusicLinksError):
                await service.resolve(self.REF)
        assert api.calls.count(self.REF.url) == 1

    async def test_short_link_is_followed_through_spotify_hosts_only(self):
        service, api, _ = make_service(redis=FakeRedis())
        api.redirects = {
            "https://spotify.link/abc": "https://spotify.app.link/xyz",
            "https://spotify.app.link/xyz": f"https://open.spotify.com/intl-fr/track/{TRACK_ID}?si=1"}
        assert await service.resolve_short("https://spotify.link/abc") == self.REF
        calls = len(api.calls)
        assert await service.resolve_short("https://spotify.link/abc") == self.REF  # cached
        assert len(api.calls) == calls

    async def test_short_link_leaving_spotify_or_to_a_playlist_is_refused(self):
        service, api, _ = make_service()
        api.redirects = {"https://spotify.link/evil": "https://evil.example/x",
                         "https://spotify.link/pl": f"https://open.spotify.com/playlist/{PLAYLIST_ID}",
                         "https://spotify.link/loop": "https://spotify.link/loop"}
        with pytest.raises(MusicLinksError) as caught:
            await service.resolve_short("https://spotify.link/evil")
        assert caught.value.code == "not_found"
        with pytest.raises(MusicLinksError) as caught:
            await service.resolve_short("https://spotify.link/pl")
        assert caught.value.code == "unsupported"
        with pytest.raises(MusicLinksError):
            await service.resolve_short("https://spotify.link/loop")  # bounded hops
        with pytest.raises(MusicLinksError):
            await service.resolve_short("http://spotify.link/insecure")

    async def test_refs_from_text_skips_dead_short_links_and_dedupes(self):
        service, api, _ = make_service()
        api.redirects = {"https://spotify.link/ok": f"https://open.spotify.com/track/{TRACK_ID}"}
        text = (f"https://spotify.link/dead https://spotify.link/ok "
                f"https://open.spotify.com/track/{TRACK_ID} https://open.spotify.com/album/{ALBUM_ID}")
        refs = await service.refs_from_text(text, limit=4)
        assert refs == [mu.SpotifyRef("track", TRACK_ID), mu.SpotifyRef("album", ALBUM_ID)]

    async def test_spawn_is_bounded_and_never_raises(self):
        from services import music_links_service as svc
        service, _, _ = make_service()

        async def boom():
            raise RuntimeError("x")

        assert service.spawn(boom())
        await asyncio.sleep(0.01)  # the error is logged, not propagated
        gate = asyncio.Event()

        async def hold():
            await gate.wait()

        for _ in range(svc.MAX_PENDING_BACKGROUND):
            assert service.spawn(hold())
        refused = hold()
        assert service.spawn(refused) is False
        await service.stop()
        assert not service._background

    async def test_stats_failures_never_break_a_resolution(self):
        service, _, stats = make_service()
        stats.incr.side_effect = RuntimeError("down")
        assert (await service.resolve(self.REF)).title


# --------------------------------------------------------------------------- #
# /music link
# --------------------------------------------------------------------------- #


class FakeInteraction:
    def __init__(self):
        self.locale = discord.Locale.american_english
        self.user = SimpleNamespace(id=9, locale=self.locale)
        self.namespace = SimpleNamespace()
        self.response = SimpleNamespace(
            is_done=lambda: self._done, send_message=AsyncMock(side_effect=self._sent))
        self._done = False
        self.followup = SimpleNamespace(send=AsyncMock())
        self.edit_original_response = AsyncMock()

    async def _sent(self, *a, **k):
        self._done = True


class TestCommand:
    def make(self, service):
        from cogs.music_links import MusicLinks
        return MusicLinks(SimpleNamespace(music_links=service, db=None))

    async def call(self, cog, url, interaction=None):
        interaction = interaction or FakeInteraction()
        interaction.namespace.incognito = None
        await cog.link.callback(cog, interaction, url, None)
        return interaction

    def first_view_text(self, interaction):
        view = interaction.response.send_message.await_args.kwargs["view"]
        return "\n".join(texts(view))

    async def test_not_a_spotify_link(self):
        cog = self.make(FakeService())
        interaction = await self.call(cog, "https://example.com")
        assert t("tools.music.errors.not_spotify", locale="en-US") in self.first_view_text(interaction)
        assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
        interaction.edit_original_response.assert_not_awaited()

    async def test_playlist_is_explained(self):
        cog = self.make(FakeService())
        interaction = await self.call(cog, f"https://open.spotify.com/playlist/{PLAYLIST_ID}")
        assert t("tools.music.errors.unsupported", locale="en-US") in self.first_view_text(interaction)

    async def test_success_shows_loading_then_the_card(self, monkeypatch):
        info = full_info()
        service = SimpleNamespace(resolve=AsyncMock(return_value=info), resolve_short=AsyncMock())
        cog = self.make(service)
        interaction = await self.call(cog, f"https://open.spotify.com/track/{TRACK_ID}")
        interaction.response.send_message.assert_awaited_once()  # the loading card
        service.resolve.assert_awaited_once_with(mu.SpotifyRef("track", TRACK_ID), source="command")
        kwargs = interaction.edit_original_response.await_args.kwargs
        assert kwargs["allowed_mentions"].users is False
        assert "Never Gonna Give You Up" in "\n".join(texts(kwargs["view"]))

    async def test_short_link_is_resolved_first(self):
        service = SimpleNamespace(resolve=AsyncMock(return_value=full_info()),
                                  resolve_short=AsyncMock(return_value=mu.SpotifyRef("track", TRACK_ID)))
        cog = self.make(service)
        await self.call(cog, "https://spotify.link/abc123")
        service.resolve_short.assert_awaited_once_with("https://spotify.link/abc123")

    async def test_service_errors_become_a_card(self):
        service = SimpleNamespace(resolve=AsyncMock(side_effect=MusicLinksError("unavailable")),
                                  resolve_short=AsyncMock())
        cog = self.make(service)
        interaction = await self.call(cog, f"https://open.spotify.com/track/{TRACK_ID}")
        view = interaction.edit_original_response.await_args.kwargs["view"]
        assert t("tools.music.errors.unavailable", locale="en-US") in "\n".join(texts(view))

    async def test_unexpected_bug_clears_the_spinner_then_propagates(self):
        service = SimpleNamespace(resolve=AsyncMock(side_effect=ValueError("bug")), resolve_short=AsyncMock())
        cog = self.make(service)
        interaction = FakeInteraction()
        interaction.namespace.incognito = None
        with pytest.raises(ValueError):
            await cog.link.callback(cog, interaction, f"https://open.spotify.com/track/{TRACK_ID}", None)
        interaction.edit_original_response.assert_awaited_once()

    async def test_without_a_service_says_unavailable(self):
        from cogs.music_links import MusicLinks
        cog = MusicLinks(SimpleNamespace(db=None))
        interaction = await self.call(cog, f"https://open.spotify.com/track/{TRACK_ID}")
        assert interaction.response.send_message.await_count == 1

    def test_command_shape(self):
        from cogs.music_links import MusicLinks
        group = MusicLinks.music_group
        assert group.name == "music" and [c.name for c in group.commands] == ["link"]
        assert group.allowed_installs.guild and group.allowed_installs.user
        assert group.allowed_contexts.guild and group.allowed_contexts.dm_channel


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #


def test_stats_metrics_are_declared():
    from stats import registry
    for key in ("music_links.resolved", "music_links.posted", "music_links.failed"):
        assert registry.get(key).key == key


def test_module_events_and_config_are_wired():
    events = (ROOT / "cogs" / "module_events.py").read_text(encoding="utf-8")
    config = (ROOT / "cogs" / "config.py").read_text(encoding="utf-8")
    assert "'music_links'" in events and "MusicLinksConfigView" in config
