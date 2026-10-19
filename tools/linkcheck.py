"""Pure logic of /link-check.

* extracting URLs from a message and normalising what a person typed;
* following a redirect chain hop by hop through an injected ``fetch`` (so it is
  testable with a fake transport and the network layer decides how requests are
  made — public-only resolver, timeouts, size caps);
* cheap local signals (shortener, punycode / mixed-script homoglyphs, user-info
  tricks, raw IP, domain age);
* the aggregation of everything — including VirusTotal and urlscan.io answers —
  into one verdict.

Nothing here touches Discord, Redis or the network.
"""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

MAX_HOPS = 10
MAX_URLS_PER_MESSAGE = 5
MAX_URL_LENGTH = 2000

LEVEL_SAFE = "safe"
LEVEL_SUSPICIOUS = "suspicious"
LEVEL_DANGEROUS = "dangerous"

REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

# --------------------------------------------------------------------------- #
# URL extraction / normalisation
# --------------------------------------------------------------------------- #

_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"'`\]\[|^{}\\]+", re.IGNORECASE)
_TRAILING = ".,;:!?'\"*_~>"


def _strip_trailing(url: str) -> str:
    while url:
        last = url[-1]
        if last in _TRAILING:
            url = url[:-1]
        elif last == ")" and url.count(")") > url.count("("):
            url = url[:-1]  # "(see https://x.com)" but keep Wikipedia-style parens
        else:
            break
    return url


def extract_urls(*texts: Optional[str], limit: int = MAX_URLS_PER_MESSAGE) -> List[str]:
    """http(s) / ``www.`` URLs found in the texts, deduplicated, in order."""
    found: List[str] = []
    for text in texts:
        for match in _URL_RE.finditer(text or ""):
            url = _strip_trailing(match.group(0))
            if url.lower().startswith("www."):
                url = "https://" + url
            if len(url) > MAX_URL_LENGTH or not urlsplit(url).hostname:
                continue
            if url not in found:
                found.append(url)
            if len(found) >= limit:
                return found
    return found


@dataclass(frozen=True)
class PreparedUrl:
    url: str            # scheme://host[:port]/path?query — no credentials, no fragment
    had_userinfo: bool  # https://paypal.com@evil.example : the classic disguise


class BadURL(ValueError):
    """Not an http(s) URL we can check. ``code`` maps to ``tools.link_check.errors.<code>``."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def prepare_url(raw: str) -> PreparedUrl:
    """Normalise a typed URL: add ``https://`` when there is no scheme, drop the
    fragment, split off credentials (kept as a signal, never contacted)."""
    text = (raw or "").strip().strip("<>\"'`").strip()
    if not text:
        raise BadURL("empty")
    if any(ch.isspace() for ch in text) or len(text) > MAX_URL_LENGTH:
        raise BadURL("invalid_url")
    if "://" not in text:
        text = "https://" + text.lstrip("/")
    try:
        parts = urlsplit(text)
        host = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise BadURL("invalid_url") from exc
    if parts.scheme.lower() not in ("http", "https"):
        raise BadURL("unsupported_scheme")
    if not host:
        raise BadURL("invalid_url")
    userinfo = parts.username is not None or parts.password is not None
    netloc = f"[{host}]" if ":" in host else host
    if port:
        netloc += f":{port}"
    clean = urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, ""))
    return PreparedUrl(url=clean, had_userinfo=userinfo)


def display_url(url: str, limit: int = 64) -> str:
    """Compact human form: no scheme, no query/fragment (marked ``?…``), cut to ``limit``."""
    parts = urlsplit(url)
    text = (parts.netloc or "") + (parts.path if parts.path != "/" else "")
    if parts.query:
        text += "?…"
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


# --------------------------------------------------------------------------- #
# Redirect chain
# --------------------------------------------------------------------------- #


@dataclass
class HopResponse:
    status: int
    location: Optional[str] = None
    content_type: str = ""
    body: bytes = b""


#: ``fetch(method, url, want_body)``; raises :class:`FetchError` on a network failure.
Fetcher = Callable[[str, str, bool], Awaitable[HopResponse]]
#: Raises :class:`BadURL`/``ValueError``-like for URLs the bot must not contact.
Validator = Callable[[str], str]


class FetchError(Exception):
    """A hop could not be fetched. ``code``: timeout | unreachable | blocked | too_large."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class Hop:
    url: str
    status: int = 0                 # 0 = could not be fetched
    kind: str = "http"              # "http" | "meta" (HTML meta refresh led here)
    error: Optional[str] = None


@dataclass
class Chain:
    hops: List[Hop] = field(default_factory=list)
    error: Optional[str] = None     # too_many_redirects | loop | blocked | timeout | unreachable | bad_redirect

    @property
    def final(self) -> Hop:
        return self.hops[-1]

    @property
    def final_url(self) -> str:
        return self.hops[-1].url

    @property
    def redirects(self) -> int:
        return max(0, len(self.hops) - 1)


_META_REFRESH_RE = re.compile(
    r"<meta[^>]+http-equiv\s*=\s*[\"']?refresh[\"']?[^>]*>", re.IGNORECASE)
_META_CONTENT_RE = re.compile(
    r"content\s*=\s*[\"']\s*(\d+(?:\.\d+)?)\s*[;,]\s*(?:url\s*=\s*)?['\"]?([^\"'>]+)", re.IGNORECASE)


def parse_meta_refresh(html: bytes, base_url: str, *, max_delay: float = 10.0) -> Optional[str]:
    """Target of ``<meta http-equiv="refresh" content="0; url=...">``, or ``None``."""
    text = html[:32768].decode("utf-8", errors="ignore")
    for tag in _META_REFRESH_RE.findall(text):
        match = _META_CONTENT_RE.search(tag)
        if match and float(match.group(1)) <= max_delay:
            target = match.group(2).strip().rstrip("'\"")
            if target:
                return urljoin(base_url, target)
    return None


def _wants_get_after_head(resp: HopResponse) -> bool:
    """HEAD is untrustworthy on error codes and gives no body for meta refresh."""
    if resp.status in REDIRECT_STATUSES and resp.location:
        return False
    if resp.status >= 400:
        return True
    ctype = (resp.content_type or "").lower()
    return resp.status < 400 and (not ctype or "html" in ctype)


async def follow(url: str, fetch: Fetcher, validate: Validator, *,
                 max_hops: int = MAX_HOPS) -> Chain:
    """Walk the redirects of ``url`` (HEAD first, GET when HEAD is not enough).

    ``validate`` is called on every URL before it is contacted and must raise
    to refuse it (private address, bad port...). The chain always has at least
    one hop; ``chain.error`` says why it stopped early, if it did.
    """
    chain = Chain()
    seen = set()
    current = url
    next_kind = "http"
    for _ in range(max_hops + 1):
        if current in seen:
            chain.error = "loop"
            return chain
        seen.add(current)
        try:
            validate(current)
        except Exception:  # noqa: BLE001 — any refusal means "do not contact"
            chain.hops.append(Hop(current, 0, error="blocked"))
            chain.error = "blocked"
            return chain

        kind, next_kind = next_kind, "http"
        try:
            resp = await fetch("HEAD", current, False)
            if _wants_get_after_head(resp):
                try:
                    resp = await fetch("GET", current, True)
                except FetchError:
                    if resp.status >= 400:
                        raise
        except FetchError as exc:
            chain.hops.append(Hop(current, 0, kind=kind, error=exc.code))
            chain.error = exc.code
            return chain

        hop = Hop(current, resp.status, kind=kind)
        chain.hops.append(hop)

        if resp.status in REDIRECT_STATUSES:
            if not resp.location:
                chain.error = "bad_redirect"
                return chain
            current = urljoin(current, resp.location.strip())
            if urlsplit(current).scheme not in ("http", "https"):
                chain.error = "bad_redirect"
                return chain
            continue

        target = parse_meta_refresh(resp.body, current) if resp.body and resp.status < 400 else None
        if target and target != current and urlsplit(target).scheme in ("http", "https"):
            next_kind = "meta"  # the NEXT hop is reached by a meta refresh
            current = target
            continue
        return chain

    chain.error = "too_many_redirects"
    return chain


# --------------------------------------------------------------------------- #
# Local signals
# --------------------------------------------------------------------------- #

SHORTENERS = frozenset("""
bit.ly bitly.com t.co tinyurl.com goo.gl ow.ly is.gd buff.ly cutt.ly rb.gy shorturl.at
tiny.cc t.ly rebrand.ly s.id v.gd lnkd.in fb.me amzn.to bit.do soo.gd shor.by urlz.fr
tr.ee clck.ru vk.cc adf.ly ouo.io bc.vc linktr.ee qr.ae x.co 1url.com short.io
""".split())


def is_shortener(host: str) -> bool:
    host = host.lower()
    return host in SHORTENERS or host.removeprefix("www.") in SHORTENERS


# Non-Latin letters that look like a Latin one. A label made only of these
# reads as a Latin word (``аррӏе`` -> apple).
_LOOKALIKES = {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i",
    "ј": "j", "ѕ": "s", "ԁ": "d", "ӏ": "l", "һ": "h", "ԛ": "q", "ԝ": "w", "ɡ": "g",
    "ο": "o", "ν": "v", "α": "a", "ρ": "p", "ι": "i", "κ": "k", "τ": "t", "υ": "u",
    "ա": "u", "օ": "o", "ս": "u", "հ": "h", "ո": "n", "ց": "g",
}
_SCRIPT_GROUPS = {"HIRAGANA": "JAPANESE", "KATAKANA": "JAPANESE", "CJK": "JAPANESE",
                  "HANGUL": "KOREAN", "BOPOMOFO": "CHINESE"}


def _script(char: str) -> Optional[str]:
    if not char.isalpha():
        return None
    try:
        name = unicodedata.name(char)
    except ValueError:
        return None
    script = name.split()[0]
    if script == "LATIN":
        return "LATIN"
    return _SCRIPT_GROUPS.get(script, script)


def idn_signal(unicode_host: str) -> Optional[str]:
    """``"mixed_script"`` (Latin mixed with another alphabet in one label),
    ``"lookalike"`` (a non-Latin label that reads as a Latin word) or ``None``."""
    if unicode_host.isascii():
        return None
    result = None
    for label in unicode_host.split("."):
        if label.isascii():
            continue
        scripts = {s for s in (_script(c) for c in label) if s}
        # Japanese mixes Han + kana legitimately.
        if len(scripts) > 1 and not scripts <= {"JAPANESE"}:
            return "mixed_script"
        letters = [c for c in label if c.isalpha()]
        if letters and all(c in _LOOKALIKES for c in letters):
            result = "lookalike"
    return result


def host_is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


@dataclass
class Signals:
    """Everything known about the URL without asking a reputation service."""

    host: str = ""
    final_host: str = ""
    https: bool = True              # the FINAL url is https
    shortener: bool = False
    redirects: int = 0
    idn: Optional[str] = None       # mixed_script | lookalike | None
    is_idn: bool = False
    userinfo: bool = False
    ip_host: bool = False
    age_days: Optional[int] = None  # age of the final domain, when RDAP knew it
    age_level: Optional[str] = None  # recent | very_recent | None
    shared_hosting: bool = False    # age says nothing about the page
    chain_error: Optional[str] = None
    reached: bool = True


@dataclass
class VtResult:
    malicious: int = 0
    suspicious: int = 0
    harmless: int = 0
    undetected: int = 0

    @property
    def engines(self) -> int:
        return self.malicious + self.suspicious + self.harmless + self.undetected


@dataclass
class UrlscanResult:
    malicious: bool = False
    score: int = 0
    categories: List[str] = field(default_factory=list)
    brands: List[str] = field(default_factory=list)


@dataclass
class Reason:
    code: str
    severity: str                   # "hard" | "soft" | "info"
    params: dict = field(default_factory=dict)


@dataclass
class Verdict:
    level: str
    reasons: List[Reason] = field(default_factory=list)

    @property
    def dangerous(self) -> bool:
        return self.level == LEVEL_DANGEROUS


def collect_reasons(signals: Signals, vt: Optional[VtResult] = None,
                    urlscan: Optional[UrlscanResult] = None) -> List[Reason]:
    reasons: List[Reason] = []
    if vt is not None:
        if vt.malicious >= 3:
            reasons.append(Reason("vt_malicious", "hard", {"count": vt.malicious}))
        elif vt.malicious >= 1:
            reasons.append(Reason("vt_malicious", "soft", {"count": vt.malicious}))
        elif vt.suspicious >= 2:
            reasons.append(Reason("vt_suspicious", "soft", {"count": vt.suspicious}))
    if urlscan is not None:
        if urlscan.malicious:
            reasons.append(Reason("urlscan_malicious", "hard"))
        elif urlscan.score > 0:
            reasons.append(Reason("urlscan_suspicious", "soft"))
    if signals.age_level == "very_recent":
        reasons.append(Reason("age_very_recent", "soft", {"days": signals.age_days}))
    elif signals.age_level == "recent":
        reasons.append(Reason("age_recent", "soft", {"days": signals.age_days}))
    if signals.idn == "mixed_script":
        reasons.append(Reason("idn_mixed_script", "soft"))
    elif signals.idn == "lookalike":
        reasons.append(Reason("idn_lookalike", "soft"))
    if signals.userinfo:
        reasons.append(Reason("userinfo", "soft"))
    if signals.ip_host:
        reasons.append(Reason("ip_host", "soft"))
    if signals.chain_error in ("loop", "too_many_redirects"):
        reasons.append(Reason(signals.chain_error, "soft"))
    # Weak signals: shown to the reader, never enough on their own.
    if not signals.https and signals.reached:
        reasons.append(Reason("http_only", "info"))
    if signals.shortener:
        reasons.append(Reason("shortener", "info"))
    if signals.redirects >= 4:
        reasons.append(Reason("many_redirects", "info", {"count": signals.redirects}))
    return reasons


def assess(signals: Signals, vt: Optional[VtResult] = None,
           urlscan: Optional[UrlscanResult] = None) -> Verdict:
    """One verdict from every signal gathered so far (usable while checks are pending)."""
    reasons = collect_reasons(signals, vt, urlscan)
    hard = [r for r in reasons if r.severity == "hard"]
    soft = [r for r in reasons if r.severity == "soft"]
    very_recent = any(r.code == "age_very_recent" for r in soft)
    if hard or len(soft) >= 3 or (very_recent and len(soft) >= 2):
        level = LEVEL_DANGEROUS
    elif soft:
        level = LEVEL_SUSPICIOUS
    else:
        level = LEVEL_SAFE
    order = {"hard": 0, "soft": 1, "info": 2}
    reasons.sort(key=lambda r: order[r.severity])
    return Verdict(level=level, reasons=reasons)


def build_signals(original_host: str, chain: Chain, *, final_unicode_host: str = "",
                  userinfo: bool = False, age_days: Optional[int] = None,
                  age_level: Optional[str] = None, shared_hosting: bool = False) -> Signals:
    """Signals from the original host and the walked chain (final host first-class)."""
    final = urlsplit(chain.final_url)
    final_host = (final.hostname or "").lower()
    chain_hosts = {urlsplit(h.url).hostname or "" for h in chain.hops}
    return Signals(
        host=original_host,
        final_host=final_host,
        https=final.scheme == "https",
        shortener=any(is_shortener(h) for h in chain_hosts),
        redirects=chain.redirects,
        idn=idn_signal(final_unicode_host or final_host),
        is_idn=any(h.startswith("xn--") or ".xn--" in h for h in chain_hosts),
        userinfo=userinfo,
        ip_host=host_is_ip(final_host),
        age_days=age_days,
        age_level=age_level,
        shared_hosting=shared_hosting,
        chain_error=chain.error,
        reached=chain.final.status > 0,
    )
