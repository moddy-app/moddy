"""
Reverse image search helpers for ``/sauce`` — pure, no Discord, no network.

* parsers for the two providers' JSON (SauceNAO ``output_type=2``, trace.moe
  ``anilistInfo``) into small dataclasses, with the similarity thresholds and
  the NSFW filtering applied here so the tests cover them;
* the reverse-search engine links (Google Lens, Yandex, Bing, TinEye) that need
  no API;
* tiny formatters (similarity bar, ``mm:ss``, episode).

The calls themselves live in ``gateway/adapters/{saucenao,tracemoe}.py`` and the
orchestration in ``services/sauce_service.py``. See docs/features/TOOLS_INSPECT.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote, urlparse

#: SauceNAO similarity (percent) under which a result is noise.
MIN_SIMILARITY = 60.0
#: trace.moe's own documentation: below 0.87 a match is usually wrong.
MIN_ANIME_SIMILARITY = 0.87
#: Matches shown besides the best one.
MAX_OTHERS = 3
#: A Discord link button URL holds 512 characters at most.
MAX_BUTTON_URL = 512


# --------------------------------------------------------------------------- #
# SauceNAO indexes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Site:
    name: str
    #: Hosts adult material almost exclusively: hidden outside NSFW channels.
    explicit: bool = False


#: SauceNAO ``index_id`` -> site (https://saucenao.com/tools/examples/api/index_details.txt).
#: Boorus and Pixiv mix safe and adult art; the API's own ``hide`` level and the
#: ``hidden`` flag of each result cover them. Only the adult-only indexes are
#: dropped by id.
SAUCENAO_SITES: Dict[int, Site] = {
    0: Site("H-Magazines", True), 2: Site("H-Game CG", True), 3: Site("DoujinshiDB", True),
    5: Site("Pixiv"), 6: Site("Pixiv"), 8: Site("Nico Nico Seiga"), 9: Site("Danbooru"),
    10: Site("drawr"), 11: Site("Nijie", True), 12: Site("Yande.re"), 15: Site("Shutterstock"),
    16: Site("FAKKU", True), 18: Site("H-Misc", True), 19: Site("2D-Market", True),
    20: Site("MediBang"), 21: Site("Anime"), 22: Site("H-Anime", True), 23: Site("Movies"),
    24: Site("Shows"), 25: Site("Gelbooru"), 26: Site("Konachan"), 27: Site("Sankaku Channel", True),
    28: Site("Anime-Pictures"), 29: Site("e621", True), 30: Site("Idol Complex"),
    31: Site("bcy.net"), 32: Site("bcy.net"), 33: Site("PortalGraphics"), 34: Site("DeviantArt"),
    35: Site("Pawoo"), 36: Site("Madokami"), 37: Site("MangaDex"), 38: Site("E-Hentai", True),
    39: Site("ArtStation"), 40: Site("FurAffinity"), 41: Site("X (Twitter)"),
    42: Site("Furry Network"), 43: Site("Kemono", True), 44: Site("Skeb"),
}

#: The 999 "all indexes" search; the API hides results at this level when the
#: channel is not NSFW (3 = only what SauceNAO expects to be safe).
HIDE_SAFE = 3
HIDE_NONE = 0


@dataclass
class SourceMatch:
    similarity: float          # 0-100
    site: str
    title: str = ""
    author: str = ""
    urls: List[str] = field(default_factory=list)
    thumbnail: Optional[str] = None
    index_id: int = -1

    @property
    def url(self) -> Optional[str]:
        return self.urls[0] if self.urls else None


@dataclass
class AnimeMatch:
    similarity: float          # 0-100
    anilist_id: Optional[int]
    title: str                 # display title: romaji, else English, else native
    english: str = ""          # shown under the title when it differs
    native: str = ""
    episode: Optional[str] = None
    timestamp: str = ""
    preview: Optional[str] = None

    @property
    def anilist_url(self) -> Optional[str]:
        return f"https://anilist.co/anime/{self.anilist_id}" if self.anilist_id else None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_CONTROL = re.compile(r"[\x00-\x1f\x7f\s]")
_WS = re.compile(r"\s+")


def safe_url(url: Any) -> Optional[str]:
    """``url`` when it is a plain http(s) link, else None.

    Provider data ends up in link buttons and markdown links: anything that
    is not a clean ``http(s)://host/...`` is dropped.
    """
    if not isinstance(url, str) or len(url) > 500:
        return None
    url = url.strip()
    if _CONTROL.search(url):
        return None
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    return url


def clean_text(value: Any, limit: int = 120) -> str:
    """One-line, bounded text out of a provider field (str or list of str)."""
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value if v)
    if value is None:
        return ""
    text = _WS.sub(" ", str(value)).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_nsfw_context(channel: Any) -> bool:
    """True only in a server channel flagged age-restricted.

    DMs, group DMs and channels the bot cannot see (user installs) are never
    NSFW: explicit sources stay hidden there.
    """
    check = getattr(channel, "is_nsfw", None)
    if not callable(check):
        return False
    try:
        return bool(check())
    except Exception:  # noqa: BLE001 — partial channels
        return False


def similarity_bar(percent: float, cells: int = 10) -> str:
    filled = max(0, min(cells, int(round(cells * percent / 100.0))))
    return "▰" * filled + "▱" * (cells - filled)


def format_timestamp(seconds: Any) -> str:
    """``272.18`` -> ``4:32``; an hour or more -> ``1:02:03``."""
    value = _float(seconds)
    if value is None or value < 0:
        return ""
    total = int(value)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def format_episode(episode: Any) -> Optional[str]:
    """trace.moe sends a number, ``null``, a list (``[1, 2]``) or a string."""
    if episode is None or episode == "":
        return None
    if isinstance(episode, (list, tuple)):
        nums = [str(e) for e in episode if e not in (None, "")]
        if not nums:
            return None
        return nums[0] if len(nums) == 1 else f"{nums[0]}–{nums[-1]}"
    if isinstance(episode, float) and episode.is_integer():
        episode = int(episode)
    return clean_text(episode, 20) or None


# --------------------------------------------------------------------------- #
# SauceNAO
# --------------------------------------------------------------------------- #

_AUTHOR_KEYS = ("member_name", "creator", "author_name", "user_name", "author",
                "twitter_user_handle", "artist", "company")
_TITLE_KEYS = ("title", "eng_name", "jp_name", "source", "material", "characters")


def _pixiv_url(data: Dict[str, Any]) -> Optional[str]:
    pid = data.get("pixiv_id")
    return f"https://www.pixiv.net/artworks/{pid}" if isinstance(pid, int) else None


def parse_saucenao(payload: Any, *, allow_explicit: bool = False,
                   min_similarity: float = MIN_SIMILARITY) -> List[SourceMatch]:
    """Matches of a SauceNAO ``output_type=2`` response, best first.

    ``allow_explicit`` False drops the adult-only indexes and every result the
    API flags ``hidden``. Duplicates (same first link) keep their best entry.
    """
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return []
    found: List[SourceMatch] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        header = item.get("header") if isinstance(item.get("header"), dict) else {}
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        similarity = _float(header.get("similarity"))
        if similarity is None or similarity < min_similarity:
            continue
        index_id = header.get("index_id")
        index_id = index_id if isinstance(index_id, int) else -1
        site = SAUCENAO_SITES.get(index_id)
        if not allow_explicit:
            if site is not None and site.explicit:
                continue
            hidden = header.get("hidden")
            if isinstance(hidden, (int, float)) and hidden > 0:
                continue
        name = site.name if site else (clean_text(header.get("index_name"), 40) or "SauceNAO")
        if site is None and ":" in name:  # "Index #5: Pixiv Images - 123_p0.jpg"
            name = name.split(":", 1)[1].strip().split(" - ")[0]

        urls = [u for u in (safe_url(x) for x in (data.get("ext_urls") or []) if x) if u]
        if not urls:
            guessed = _pixiv_url(data)
            if guessed:
                urls = [guessed]
        title = next((clean_text(data[k]) for k in _TITLE_KEYS if data.get(k)), "")
        author = next((clean_text(data[k], 80) for k in _AUTHOR_KEYS if data.get(k)), "")
        thumb = safe_url(header.get("thumbnail")) if (allow_explicit or not (site and site.explicit)) else None
        found.append(SourceMatch(similarity=similarity, site=name, title=title, author=author,
                                 urls=urls[:3], thumbnail=thumb, index_id=index_id))

    found.sort(key=lambda m: -m.similarity)
    seen: set = set()
    unique: List[SourceMatch] = []
    for match in found:
        key = match.url or f"{match.site}:{match.title}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(match)
    return unique[: MAX_OTHERS + 1]


# --------------------------------------------------------------------------- #
# trace.moe
# --------------------------------------------------------------------------- #


def parse_tracemoe(payload: Any, *, allow_adult: bool = False,
                   min_similarity: float = MIN_ANIME_SIMILARITY) -> Optional[AnimeMatch]:
    """The best trace.moe match, or None under the threshold / adult-filtered."""
    results = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return None
    for item in results:  # trace.moe sorts by similarity
        if not isinstance(item, dict):
            continue
        similarity = _float(item.get("similarity"))
        if similarity is None or similarity < min_similarity:
            return None
        anilist = item.get("anilist")
        info: Dict[str, Any] = anilist if isinstance(anilist, dict) else {}
        if info.get("isAdult") and not allow_adult:
            continue
        titles = info.get("title") if isinstance(info.get("title"), dict) else {}
        title = (clean_text(titles.get("romaji")) or clean_text(titles.get("english"))
                 or clean_text(titles.get("native")) or clean_text(item.get("filename"), 80))
        if not title:
            continue
        anilist_id = info.get("id") if isinstance(info.get("id"), int) else (
            anilist if isinstance(anilist, int) else None)
        english = clean_text(titles.get("english"))
        native = clean_text(titles.get("native"))
        return AnimeMatch(
            similarity=round(similarity * 100, 1),
            anilist_id=anilist_id,
            title=title,
            english=english if english != title else "",
            native=native if native != title else "",
            episode=format_episode(item.get("episode")),
            timestamp=format_timestamp(item.get("from")),
            preview=safe_url(item.get("image")),
        )
    return None


# --------------------------------------------------------------------------- #
# Reverse-search engines (no API)
# --------------------------------------------------------------------------- #

#: (key, URL template) — ``{u}`` is the percent-encoded image URL.
ENGINES: Tuple[Tuple[str, str], ...] = (
    ("google_lens", "https://lens.google.com/uploadbyurl?url={u}"),
    ("yandex", "https://yandex.com/images/search?rpt=imageview&url={u}"),
    ("bing", "https://www.bing.com/images/search?view=detailv2&iss=sbi&q=imgurl:{u}"),
    ("tineye", "https://tineye.com/search?url={u}"),
)


def search_engine_urls(image_url: str) -> List[Tuple[str, str]]:
    """``[(engine_key, url)]`` for the image, dropping any link a button cannot hold."""
    if not safe_url(image_url):
        return []
    encoded = quote(image_url, safe="")
    out = []
    for key, template in ENGINES:
        url = template.format(u=encoded)
        if len(url) <= MAX_BUTTON_URL:
            out.append((key, url))
    return out


# --------------------------------------------------------------------------- #
# Result container
# --------------------------------------------------------------------------- #


@dataclass
class SauceResult:
    image_url: str
    matches: List[SourceMatch] = field(default_factory=list)
    anime: Optional[AnimeMatch] = None
    used_saucenao: bool = False
    used_tracemoe: bool = False
    #: Providers that were configured but failed (shown as a quiet note).
    failed: List[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.matches) or self.anime is not None

    @property
    def best_is_anime(self) -> bool:
        """The anime frame outranks the best SauceNAO hit (or is the only one)."""
        if self.anime is None:
            return False
        return not self.matches or self.anime.similarity >= self.matches[0].similarity
