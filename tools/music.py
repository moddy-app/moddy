"""Music links — pure logic (no Discord, no network, no database).

Moddy turns a Spotify link into links on other platforms. None of the
well-known routes is available any more (the Odesli / song.link public API
answers ``401 PUBLIC_API_ACCESS_DEPRECATED`` and the Spotify Web API is closed
to new apps), so the resolution is home-made and made of three pure steps that
live here:

1. **Detect** Spotify links in free text (``find_links``): tracks, albums and
   artists, ``intl-xx`` variants, short links (``spotify.link``, ``spoti.fi``).
   Playlists, episodes and shows have no equivalent elsewhere: they are
   recognised only to be refused politely.
2. **Read** the public Spotify page (``parse_spotify_page``): its Open Graph /
   ``music:*`` meta tags carry the title, the artist, the album, the year, the
   duration and the cover.
3. **Match** that against what Deezer and iTunes (Apple Music) return from a
   plain search (``best_match``). Matching is deliberately strict — a wrong
   link is worse than no link — and compares normalised titles, the artist and
   the duration, and refuses a *different version* of the song (a piano
   cover, a live take, a remix) when the original is not what Spotify shows.

The network side lives in ``services/music_links_service.py``. See
docs/features/MUSIC_LINKS.md.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

KIND_TRACK = "track"
KIND_ALBUM = "album"
KIND_ARTIST = "artist"
KINDS = (KIND_TRACK, KIND_ALBUM, KIND_ARTIST)

PLATFORM_SPOTIFY = "spotify"
PLATFORM_DEEZER = "deezer"
PLATFORM_APPLE = "apple_music"
PLATFORM_YOUTUBE = "youtube_music"
#: Platforms a server can show under a Spotify link, in display order.
PLATFORMS = (PLATFORM_DEEZER, PLATFORM_APPLE, PLATFORM_YOUTUBE)
#: Those whose link points at the very song (YouTube Music is a search).
DIRECT_PLATFORMS = (PLATFORM_DEEZER, PLATFORM_APPLE)

MAX_LINKS_PER_MESSAGE = 3

# --------------------------------------------------------------------------- #
# 1. Detecting links
# --------------------------------------------------------------------------- #

_LINK_RE = re.compile(
    r"https?://(?:"
    r"open\.spotify\.com/(?:embed/)?(?:intl-[a-z]{2}(?:-[a-z]{2})?/)?"
    r"(?:user/[^/\s<>]+/)?"
    r"(?P<kind>track|album|artist|playlist|episode|show)/(?P<id>[A-Za-z0-9]{22})"
    r"|(?P<short>(?:spotify\.link|spoti\.fi|spotify\.app\.link)/[A-Za-z0-9_\-]{2,40})"
    r")",
    re.IGNORECASE,
)

LINK_REF = "ref"
LINK_SHORT = "short"
LINK_UNSUPPORTED = "unsupported"

#: Hosts a short link may pass through before landing on open.spotify.com.
SHORT_LINK_HOSTS = frozenset({
    "spotify.link", "spoti.fi", "spotify.app.link", "link.tospotify.com", "open.spotify.com",
})


@dataclass(frozen=True)
class SpotifyRef:
    kind: str
    id: str

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.id}"

    @property
    def url(self) -> str:
        return f"https://open.spotify.com/{self.kind}/{self.id}"


@dataclass(frozen=True)
class ParsedLink:
    """One link found in a text: ``ref`` (resolved), ``short`` (to follow) or
    ``unsupported`` (a playlist, an episode…)."""

    type: str
    ref: Optional[SpotifyRef] = None
    url: str = ""


def has_spotify_hint(text: Optional[str]) -> bool:
    """Cheap pre-filter for every message: does it mention Spotify at all?"""
    if not text:
        return False
    low = text.lower()
    return "spotify" in low or "spoti.fi" in low


def find_links(text: Optional[str], *, limit: int = MAX_LINKS_PER_MESSAGE,
               include_unsupported: bool = False) -> List[ParsedLink]:
    """Spotify links of ``text`` in reading order, without duplicates.

    ``limit`` counts the usable links (refs and short links). Unsupported ones
    (playlist, episode, show) are returned only with ``include_unsupported`` —
    the command wants to explain itself, the module just ignores them.
    """
    found: List[ParsedLink] = []
    seen = set()
    usable = 0
    for match in _LINK_RE.finditer(text or ""):
        if match.group("short"):
            url = "https://" + match.group("short")
            marker = ("short", url.lower())
            parsed = ParsedLink(LINK_SHORT, url=url)
        else:
            kind = match.group("kind").lower()
            if kind not in KINDS:
                marker = ("unsupported", match.group("id"))
                parsed = ParsedLink(LINK_UNSUPPORTED, url=match.group(0))
            else:
                ref = SpotifyRef(kind, match.group("id"))
                marker = ("ref", ref.key)
                parsed = ParsedLink(LINK_REF, ref=ref, url=ref.url)
        if marker in seen:
            continue
        seen.add(marker)
        if parsed.type == LINK_UNSUPPORTED:
            if include_unsupported:
                found.append(parsed)
            continue
        if usable >= limit:
            break
        usable += 1
        found.append(parsed)
    return found


def ref_from_url(url: str) -> Optional[SpotifyRef]:
    """The track / album / artist an ``open.spotify.com`` URL points at."""
    for parsed in find_links(url, limit=1):
        if parsed.type == LINK_REF:
            return parsed.ref
    return None


def is_unsupported_url(url: str) -> bool:
    """True for an ``open.spotify.com`` playlist / episode / show URL."""
    return any(p.type == LINK_UNSUPPORTED for p in find_links(url, include_unsupported=True))


# --------------------------------------------------------------------------- #
# 2. Reading the public Spotify page
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SpotifyMeta:
    kind: str
    id: str
    title: str
    artist: str                      # as displayed ("A, B" for several artists)
    album: Optional[str] = None      # tracks only
    year: Optional[str] = None
    duration: Optional[int] = None   # seconds, tracks only
    cover: Optional[str] = None

    @property
    def ref(self) -> SpotifyRef:
        return SpotifyRef(self.kind, self.id)

    @property
    def artists(self) -> List[str]:
        return artist_names(self.artist)

    @property
    def primary_artist(self) -> str:
        return self.artists[0] if self.artists else self.artist


class _HeadMeta(HTMLParser):
    """Collects ``<meta>`` tags of the document head; stops at ``</head>``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: Dict[str, List[str]] = {}
        self.title = ""
        self._in_title = False
        self.done = False

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            attributes = {k.lower(): (v or "") for k, v in attrs}
            key = attributes.get("property") or attributes.get("name")
            if key and "content" in attributes:
                self.meta.setdefault(key.lower(), []).append(attributes["content"])

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "head":
            self.done = True

    def handle_data(self, data):
        if self._in_title and not self.done:
            self.title += data


def read_meta_tags(html: str) -> Dict[str, List[str]]:
    """``{property_or_name: [content, ...]}`` of the head's meta tags."""
    html = html or ""
    end = html.find("</head>")
    # The head comes first; never feed the parser a whole multi-hundred-KB app.
    html = html[: end + 7] if end != -1 else html[:300_000]
    parser = _HeadMeta()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 — malformed markup: keep what was read
        pass
    return parser.meta


def _first(meta: Dict[str, List[str]], key: str) -> str:
    values = meta.get(key) or []
    return (values[0] if values else "").strip()


_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
_COVER_HOSTS = ("scdn.co", "spotifycdn.com")


def _safe_cover(url: str) -> Optional[str]:
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not any(host == h or host.endswith("." + h) for h in _COVER_HOSTS):
        return None
    return url


def clean_album_title(raw: str, artist: str = "") -> str:
    """``Listen to X - Album by Y | Spotify`` -> ``X`` (the English page title)."""
    title = (raw or "").strip()
    title = re.sub(r"\s*\|\s*Spotify$", "", title)
    title = re.sub(r"^Listen to\s+", "", title)
    if artist:
        match = re.search(rf"\s+by\s+{re.escape(artist)}$", title)
        if match:
            title = title[: match.start()]
            head, sep, tail = title.rpartition(" - ")
            if sep and 0 < len(tail.split()) <= 2:
                title = head
    return title.strip()


def parse_spotify_page(html: str, ref: SpotifyRef) -> Optional[SpotifyMeta]:
    """Title, artist, album, year, duration and cover from the public page.

    ``None`` when the page carries no usable metadata (an unknown id, a block
    page, a changed markup) — the caller treats that as "could not read".
    """
    meta = read_meta_tags(html)
    og_title = _first(meta, "og:title")
    if not og_title:
        return None
    og_type = _first(meta, "og:type").lower()
    parts = [p.strip() for p in _first(meta, "og:description").split("·") if p.strip()]
    release = _first(meta, "music:release_date")
    cover = _safe_cover(_first(meta, "og:image"))

    def year_of() -> Optional[str]:
        for part in reversed(parts):
            if _YEAR_RE.match(part):
                return part
        if _YEAR_RE.match(release[:4]):
            return release[:4]
        return None

    if ref.kind == KIND_TRACK:
        if og_type and og_type != "music.song":
            return None
        artist = parts[0] if parts else _first(meta, "music:musician_description")
        if not artist:
            return None
        album = parts[1] if len(parts) >= 3 else None
        duration_raw = _first(meta, "music:duration")
        duration = int(duration_raw) if duration_raw.isdigit() else None
        return SpotifyMeta(KIND_TRACK, ref.id, og_title, artist, album=album,
                           year=year_of(), duration=duration, cover=cover)

    if ref.kind == KIND_ALBUM:
        if og_type and og_type != "music.album":
            return None
        artist = parts[0] if parts else ""
        if not artist:
            return None
        return SpotifyMeta(KIND_ALBUM, ref.id, clean_album_title(og_title, artist), artist,
                           year=year_of(), cover=cover)

    if ref.kind == KIND_ARTIST:
        if og_type and og_type not in ("website", "profile", "music.musician"):
            return None
        return SpotifyMeta(KIND_ARTIST, ref.id, og_title, og_title, cover=cover)

    return None


# --------------------------------------------------------------------------- #
# 3. Normalising and matching
# --------------------------------------------------------------------------- #


def fold(text: str) -> str:
    """Lower-case, accent-free, punctuation-free form used to compare names."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.casefold().replace("&", " and ").replace("’", "'").replace("`", "'")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_NOISE_RES = [re.compile(p) for p in (
    r"^(?:feat|ft|featuring|with|avec|prod|produced by|from|de la bo)\b",
    r"\bremaster\w*",
    r"^(?:radio|single|album|original|explicit|clean|mono|stereo|bonus|deluxe|expanded|"
    r"anniversary|collector\w*|special|limited|standard)\b",
    r"^(?:single|ep|album)$",
    r"^(?:19|20)\d{2}$",
)]
_BRACKET_RE = re.compile(r"\(([^()]*)\)|\[([^\[\]]*)\]")
_DASH_SPLIT_RE = re.compile(r"\s+[-–—]\s+")


def _is_noise(folded_piece: str) -> bool:
    return any(rx.search(folded_piece) for rx in _NOISE_RES)


@dataclass(frozen=True)
class TitleParts:
    """A title split into its core and the qualifiers that matter.

    ``core`` is the folded main title. ``qualifiers`` are the *meaningful*
    extras — ``live``, ``pianoforte``, ``acoustic``, ``club remix`` — as folded
    strings; marketing noise (``Remastered 2009``, ``Radio Edit``, ``feat. X``,
    ``Deluxe Edition``) is dropped. ``raw_core`` / ``raw_qualifiers`` keep the
    original spelling to build search queries.
    """

    core: str
    qualifiers: frozenset
    raw_core: str
    raw_qualifiers: Tuple[str, ...]


def split_title(title: str) -> TitleParts:
    pieces: List[str] = []

    def take(match: "re.Match[str]") -> str:
        inner = (match.group(1) or match.group(2) or "").strip()
        if match.start() == 0:
            # "(I Can't Get No) Satisfaction": a leading bracket is the title.
            return f" {inner} "
        pieces.extend(_DASH_SPLIT_RE.split(inner))
        return " "

    rest = title or ""
    # Brackets can nest one level deep ("(Live (Remastered))"): peel twice.
    for _ in range(2):
        rest = _BRACKET_RE.sub(take, rest)
    head, *dashed = _DASH_SPLIT_RE.split(rest.strip())
    pieces.extend(dashed)

    raw_core = re.sub(r"\s+", " ", head).strip()
    qualifiers: List[Tuple[str, str]] = []
    for piece in pieces:
        piece = piece.strip()
        folded = fold(piece)
        if not folded or _is_noise(folded):
            continue
        qualifiers.append((folded, piece))
    return TitleParts(
        core=fold(raw_core) or fold(title),
        qualifiers=frozenset(f for f, _ in qualifiers),
        raw_core=raw_core or (title or "").strip(),
        raw_qualifiers=tuple(p for _, p in qualifiers),
    )


def artist_names(artist: str) -> List[str]:
    """``"A, B"`` -> ``["A, B", "A", "B"]``: the whole string and its parts."""
    artist = (artist or "").strip()
    names = [artist] if artist else []
    for part in re.split(r"\s*,\s*|\s+(?:feat\.?|ft\.?|x)\s+", artist, flags=re.IGNORECASE):
        part = part.strip()
        if part and part not in names:
            names.append(part)
    return names


def _name_similarity(a: str, b: str) -> float:
    fa, fb = fold(a), fold(b)
    if not fa or not fb:
        return 0.0
    if fa == fb:
        return 1.0
    if fa.removeprefix("the ") == fb.removeprefix("the "):
        return 1.0
    ta, tb = set(fa.split()), set(fb.split())
    # Spotify credits more names than the candidate ("Daft Punk & Pharrell" /
    # "Daft Punk"). The reverse ("Rick Astley Tribute Band") is NOT a match.
    if len(tb) >= 1 and tb < ta and len(tb) >= 2:
        return 0.9
    return SequenceMatcher(None, fa, fb).ratio()


def artist_similarity(spotify_artist: str, candidate_artist: str) -> float:
    """Best similarity between any of Spotify's artists and the candidate's."""
    names = artist_names(spotify_artist) or [spotify_artist]
    cands = artist_names(candidate_artist) or [candidate_artist]
    return max((_name_similarity(a, b) for a in names for b in cands), default=0.0)


def title_similarity(a: TitleParts, b: TitleParts) -> float:
    if not a.core or not b.core:
        return 0.0
    if a.core == b.core:
        return 1.0
    return SequenceMatcher(None, a.core, b.core).ratio()


@dataclass(frozen=True)
class Candidate:
    """One search result of a platform, reduced to what matching needs."""

    title: str
    artist: str
    url: str
    duration: Optional[int] = None
    album: Optional[str] = None
    fans: int = 0


# Weights and thresholds. A track must agree on title AND artist; the duration
# only separates two otherwise identical entries. The version penalty is what
# keeps a "(Pianoforte)" cover from passing for the original.
_W_TITLE, _W_ARTIST, _W_DURATION = 0.55, 0.30, 0.15
_VERSION_PENALTY = 0.35
MIN_TRACK_SCORE = 0.72
MIN_ALBUM_SCORE = 0.78
_MIN_TITLE = 0.82
_MIN_ARTIST = 0.6

# Words that make a result a *different* recording unless Spotify says so too.
_IMITATION_RE = re.compile(
    r"\b(?:karaoke|tribute|made famous|originally performed|backing track|cover|"
    r"instrumental|lullaby)\b")


def _is_imitation(meta: "SpotifyMeta", cand: "Candidate") -> bool:
    """A karaoke / tribute / cover entry when Spotify's item is not one."""
    wanted = set(_IMITATION_RE.findall(fold(f"{meta.title} {meta.artist}")))
    got = set(_IMITATION_RE.findall(fold(f"{cand.title} {cand.artist}")))
    return bool(got - wanted)


def _duration_factor(wanted: Optional[int], got: Optional[int]) -> float:
    if not wanted or not got:
        return 0.5
    delta = abs(wanted - got)
    if delta <= 3:
        return 1.0
    if delta <= 10:
        return 0.5
    return 0.0


def score_track(meta: SpotifyMeta, cand: Candidate) -> float:
    """0..1; 0 when the title or the artist does not agree."""
    a, b = split_title(meta.title), split_title(cand.title)
    title = title_similarity(a, b)
    artist = artist_similarity(meta.artist, cand.artist)
    if title < _MIN_TITLE or artist < _MIN_ARTIST or _is_imitation(meta, cand):
        return 0.0
    score = (_W_TITLE * title + _W_ARTIST * artist
             + _W_DURATION * _duration_factor(meta.duration, cand.duration))
    if a.qualifiers != b.qualifiers:
        score -= _VERSION_PENALTY
    return max(0.0, score)


def score_album(meta: SpotifyMeta, cand: Candidate) -> float:
    a, b = split_title(meta.title), split_title(cand.title)
    title = title_similarity(a, b)
    artist = artist_similarity(meta.artist, cand.artist)
    if title < _MIN_TITLE or artist < _MIN_ARTIST or _is_imitation(meta, cand):
        return 0.0
    score = 0.65 * title + 0.35 * artist
    if a.qualifiers != b.qualifiers:
        score -= _VERSION_PENALTY
    # Between "X" and "X (Deluxe Edition)", prefer the plain release.
    if fold(cand.title) == fold(meta.title):
        score += 0.01
    return max(0.0, score)


def score_artist(meta: SpotifyMeta, cand: Candidate) -> float:
    similarity = _name_similarity(meta.title, cand.title)
    return similarity if similarity >= 0.95 else 0.0


def best_match(meta: SpotifyMeta, candidates: Iterable[Candidate]) -> Optional[Candidate]:
    """The candidate that really is ``meta``, or ``None`` (never a weak guess)."""
    scorer, threshold = {
        KIND_TRACK: (score_track, MIN_TRACK_SCORE),
        KIND_ALBUM: (score_album, MIN_ALBUM_SCORE),
        KIND_ARTIST: (score_artist, 0.95),
    }[meta.kind]
    best: Optional[Candidate] = None
    best_key: Tuple[float, int] = (0.0, 0)
    for cand in candidates:
        if not cand.url:
            continue
        score = scorer(meta, cand)
        if score < threshold:
            continue
        key = (round(score, 4), cand.fans)  # equal scores: the most followed artist
        if best is None or key > best_key:
            best, best_key = cand, key
    return best


# --------------------------------------------------------------------------- #
# Search queries and URLs per platform
# --------------------------------------------------------------------------- #


def search_title(meta: SpotifyMeta) -> str:
    """Title as typed in a search box: noise removed, real qualifiers kept."""
    parts = split_title(meta.title)
    words = [parts.raw_core, *parts.raw_qualifiers]
    return " ".join(w for w in words if w).strip()


def plain_query(meta: SpotifyMeta) -> str:
    """``Artist Title`` — Deezer's combined ``artist:"" track:""`` syntax
    returns nothing for tracks, a plain query always works."""
    if meta.kind == KIND_ARTIST:
        return meta.title
    return f"{meta.primary_artist} {search_title(meta)}".strip()


def deezer_fallback_query(meta: SpotifyMeta) -> Optional[str]:
    """Single-field query (``track:"…"`` / ``album:"…"``) — works where the
    two-field one does not; the artist is checked on the results instead."""
    field_name = {KIND_TRACK: "track", KIND_ALBUM: "album"}.get(meta.kind)
    if field_name is None:
        return None
    title = search_title(meta).replace('"', " ")
    return f'{field_name}:"{title}"'


def youtube_music_search_url(meta: SpotifyMeta) -> str:
    """YouTube Music has no free API: a search link is the honest answer."""
    return "https://music.youtube.com/search?q=" + quote_plus(plain_query(meta))


_DEEZER_HOSTS = ("deezer.com", "www.deezer.com")
_APPLE_HOSTS = ("music.apple.com", "itunes.apple.com", "geo.music.apple.com")


def clean_deezer_url(url: str) -> Optional[str]:
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https") or (parts.hostname or "").lower() not in _DEEZER_HOSTS:
        return None
    return urlunsplit(("https", "www.deezer.com", parts.path, "", ""))


def clean_apple_url(url: str) -> Optional[str]:
    """Any iTunes / Apple Music URL -> ``https://music.apple.com/...`` without
    tracking parameters (``uo``, ``app``, ``at``…); the ``i`` track id stays."""
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https") or (parts.hostname or "").lower() not in _APPLE_HOSTS:
        return None
    query = [(k, v) for k, v in parse_qsl(parts.query) if k == "i"]
    return urlunsplit(("https", "music.apple.com", parts.path, urlencode(query), ""))


def deezer_candidates(payload: Any, kind: str) -> List[Candidate]:
    """Candidates of a ``api.deezer.com/search[/album|/artist]`` payload."""
    out: List[Candidate] = []
    data = payload.get("data") if isinstance(payload, dict) else None
    for item in data or []:
        if not isinstance(item, dict):
            continue
        url = clean_deezer_url(str(item.get("link") or ""))
        if url is None:
            continue
        if kind == KIND_ARTIST:
            name = str(item.get("name") or "")
            out.append(Candidate(title=name, artist=name, url=url,
                                 fans=int(item.get("nb_fan") or 0)))
            continue
        artist = item.get("artist") if isinstance(item.get("artist"), dict) else {}
        album = item.get("album") if isinstance(item.get("album"), dict) else {}
        duration = item.get("duration")
        out.append(Candidate(
            title=str(item.get("title") or ""), artist=str(artist.get("name") or ""), url=url,
            duration=int(duration) if isinstance(duration, int) and duration > 0 else None,
            album=str(album.get("title") or "") or None))
    return out


def itunes_candidates(payload: Any, kind: str) -> List[Candidate]:
    """Candidates of an iTunes Search API payload (``entity`` song / album /
    musicArtist)."""
    out: List[Candidate] = []
    results = payload.get("results") if isinstance(payload, dict) else None
    for item in results or []:
        if not isinstance(item, dict):
            continue
        if kind == KIND_TRACK:
            url = clean_apple_url(str(item.get("trackViewUrl") or ""))
            title = str(item.get("trackName") or "")
            millis = item.get("trackTimeMillis")
            duration = round(millis / 1000) if isinstance(millis, (int, float)) and millis > 0 else None
        elif kind == KIND_ALBUM:
            url = clean_apple_url(str(item.get("collectionViewUrl") or ""))
            title, duration = str(item.get("collectionName") or ""), None
        else:
            url = clean_apple_url(str(item.get("artistLinkUrl") or ""))
            title, duration = str(item.get("artistName") or ""), None
        if url is None:
            continue
        out.append(Candidate(title=title, artist=str(item.get("artistName") or ""), url=url,
                             duration=duration, album=str(item.get("collectionName") or "") or None))
    return out


# --------------------------------------------------------------------------- #
# The resolved result (what gets cached)
# --------------------------------------------------------------------------- #


def format_duration(seconds: Optional[int]) -> str:
    if not seconds or seconds < 0:
        return ""
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


@dataclass
class MusicInfo:
    """A Spotify item and where else it was found."""

    kind: str
    id: str
    title: str
    artist: str
    album: Optional[str] = None
    year: Optional[str] = None
    duration: Optional[int] = None
    cover: Optional[str] = None
    #: platform -> URL, for the platforms that answered (never Spotify itself).
    links: Dict[str, str] = field(default_factory=dict)
    #: True when a platform could not be asked (timeout, budget): cached shortly.
    partial: bool = False

    @property
    def spotify_url(self) -> str:
        return SpotifyRef(self.kind, self.id).url

    @property
    def direct_links(self) -> Dict[str, str]:
        return {p: u for p, u in self.links.items() if p in DIRECT_PLATFORMS}

    def to_dict(self) -> Dict[str, Any]:
        return {"v": 1, "kind": self.kind, "id": self.id, "title": self.title,
                "artist": self.artist, "album": self.album, "year": self.year,
                "duration": self.duration, "cover": self.cover, "links": dict(self.links),
                "partial": self.partial}

    @classmethod
    def from_dict(cls, data: Any) -> Optional["MusicInfo"]:
        if not isinstance(data, dict) or data.get("v") != 1 or data.get("kind") not in KINDS:
            return None
        try:
            links = {p: str(u) for p, u in (data.get("links") or {}).items()
                     if p in PLATFORMS and isinstance(u, str) and u.startswith("https://")}
            duration = data.get("duration")
            return cls(kind=data["kind"], id=str(data["id"]), title=str(data["title"]),
                       artist=str(data["artist"]), album=data.get("album") or None,
                       year=data.get("year") or None,
                       duration=int(duration) if isinstance(duration, int) else None,
                       cover=data.get("cover") or None, links=links,
                       partial=bool(data.get("partial")))
        except (KeyError, TypeError, ValueError):
            return None

    @classmethod
    def from_meta(cls, meta: SpotifyMeta) -> "MusicInfo":
        return cls(kind=meta.kind, id=meta.id, title=meta.title, artist=meta.artist,
                   album=meta.album, year=meta.year, duration=meta.duration, cover=meta.cover)


def visible_platforms(info: MusicInfo, enabled: Sequence[str]) -> List[str]:
    """Platforms to show under a Spotify link, in display order.

    Only what was actually found; YouTube Music is a search link and so always
    available once Spotify's page was read.
    """
    return [p for p in PLATFORMS if p in enabled and p in info.links]


def worth_posting(info: MusicInfo, enabled: Sequence[str]) -> bool:
    """Should the module reply for ``info`` with these platforms enabled?

    A card whose only button is a YouTube Music *search* adds noise to every
    Spotify link of a server, so a real match (Deezer / Apple Music) is
    required — unless the server picked YouTube Music alone, which is then
    exactly what it asked for.
    """
    shown = visible_platforms(info, enabled)
    if not shown:
        return False
    if any(p in DIRECT_PLATFORMS for p in shown):
        return True
    return all(p == PLATFORM_YOUTUBE for p in enabled)
