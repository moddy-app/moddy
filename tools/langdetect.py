"""Local, free language detection for the translate button.

Pure logic, no Discord and no database. Three things live here:

* **Text cleaning and eligibility** — what is left of a chat message once links,
  mentions, emojis and code are removed, and whether it is worth running a
  detector on at all (:func:`clean_text`, :func:`eligible_text`).
* **The detector** — a thin, lazily loaded wrapper around ``py3langid``
  (:class:`Detector`), restricted to the ~25 languages people actually speak on
  Discord and returning normalised probabilities.
* **The "known languages" rules** — which languages a server usually speaks,
  learnt from daily counters (:func:`usual_languages`, :func:`known_languages`).

Honest limits (see docs/features/TRANSLATE_BUTTON.md): detection on short or
mixed-language messages is imperfect, hence the strict thresholds below. A
message that fails them is simply ignored, never mislabelled.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

logger = logging.getLogger("moddy.langdetect")

# --------------------------------------------------------------------------- #
# Languages
# --------------------------------------------------------------------------- #

#: The languages the detector chooses between (ISO 639-1 as ``py3langid`` knows
#: them) and the order they are listed in the /config select. Restricting the
#: model to a short list makes it more accurate (no false "Galician" on a
#: Spanish sentence) and keeps the select within Discord's 25 options.
SUPPORTED_LANGUAGES: Tuple[str, ...] = (
    "en", "fr", "de", "es", "pt", "it", "nl", "pl", "ru", "uk", "tr", "ar", "ja",
    "ko", "zh", "sv", "da", "no", "fi", "cs", "ro", "hu", "el", "id", "bg",
)

#: Country flag shown next to a detected language. A flag stands for a
#: language, not a nation: the most widespread country is used (flags are the
#: one kind of Unicode emoji CLAUDE.md allows).
LANGUAGE_FLAGS: Dict[str, str] = {
    "en": "\U0001F1EC\U0001F1E7", "fr": "\U0001F1EB\U0001F1F7", "de": "\U0001F1E9\U0001F1EA",
    "es": "\U0001F1EA\U0001F1F8", "pt": "\U0001F1F5\U0001F1F9", "it": "\U0001F1EE\U0001F1F9",
    "nl": "\U0001F1F3\U0001F1F1", "pl": "\U0001F1F5\U0001F1F1", "ru": "\U0001F1F7\U0001F1FA",
    "uk": "\U0001F1FA\U0001F1E6", "tr": "\U0001F1F9\U0001F1F7", "ar": "\U0001F1F8\U0001F1E6",
    "ja": "\U0001F1EF\U0001F1F5", "ko": "\U0001F1F0\U0001F1F7", "zh": "\U0001F1E8\U0001F1F3",
    "sv": "\U0001F1F8\U0001F1EA", "da": "\U0001F1E9\U0001F1F0", "no": "\U0001F1F3\U0001F1F4",
    "fi": "\U0001F1EB\U0001F1EE", "cs": "\U0001F1E8\U0001F1FF", "ro": "\U0001F1F7\U0001F1F4",
    "hu": "\U0001F1ED\U0001F1FA", "el": "\U0001F1EC\U0001F1F7", "id": "\U0001F1EE\U0001F1E9",
    "bg": "\U0001F1E7\U0001F1EC",
}


def language_flag(code: str) -> str:
    """Flag for a language code, ``""`` for an unknown one (the line just drops it)."""
    return LANGUAGE_FLAGS.get(base_language(code), "")


def base_language(value: Optional[str]) -> str:
    """``"en-US"`` / ``"EN-GB"`` / ``"pt_BR"`` / ``"fr"`` -> ``"en"`` / ``"en"`` / ``"pt"`` / ``"fr"``.

    ``nb`` and ``nn`` (the two written Norwegians) fold into ``no`` — the code
    the detector uses.
    """
    base = re.split(r"[-_]", (value or "").strip().lower(), maxsplit=1)[0]
    return "no" if base in ("nb", "nn") else base



# --------------------------------------------------------------------------- #
# Cleaning and eligibility
# --------------------------------------------------------------------------- #

#: A message needs at least this much to be worth detecting.
MIN_WORDS = 4
MIN_LETTERS = 20
#: Share of the visible characters that must be letters.
MIN_LETTER_RATIO = 0.6
#: A message of one word repeated is spam, not a language sample.
MIN_DISTINCT_WORDS = 3
#: Messages longer than this are cut before detection (the first lines are
#: plenty, and the detector's cost grows with the text).
MAX_DETECT_CHARS = 1000

_CODE_BLOCK = re.compile(r"```.*?(?:```|$)", re.DOTALL)
_INLINE_CODE = re.compile(r"`+[^`\n]*`+")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_DISCORD_TOKEN = re.compile(
    r"<a?:\w+:\d+>"                  # custom emoji
    r"|<@[!&]?\d+>|<#\d+>"           # user / role / channel mention
    r"|<t:-?\d+(?::[a-zA-Z])?>"      # timestamp
    r"|<id:\w+>|</[\w -]+:\d+>"      # guild navigation, slash command
    r"|@everyone|@here"
)
_WORD = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*")


def _is_unspaced(char: str) -> bool:
    """Characters of scripts written without spaces (each carries more meaning)."""
    code = ord(char)
    return (0x3040 <= code <= 0x30FF       # hiragana + katakana
            or 0x3400 <= code <= 0x4DBF    # CJK extension A
            or 0x4E00 <= code <= 0x9FFF    # CJK unified ideographs
            or 0x0E00 <= code <= 0x0E7F)   # Thai


def clean_text(raw: Optional[str]) -> str:
    """The prose of a message: code, links, mentions and custom emojis removed."""
    text = raw or ""
    text = _CODE_BLOCK.sub(" ", text)
    text = _INLINE_CODE.sub(" ", text)
    text = _URL.sub(" ", text)
    text = _DISCORD_TOKEN.sub(" ", text)
    text = text.replace("||", " ")
    return re.sub(r"[ \t]+", " ", text).strip()


@dataclass(frozen=True)
class TextStats:
    """What :func:`measure` found in a cleaned text."""

    letters: int = 0       # a character of an unspaced script counts double
    words: int = 0         # likewise: two such characters make one word
    distinct_words: int = 0
    letter_ratio: float = 0.0


def measure(text: str) -> TextStats:
    """Count letters and words the way eligibility reasons about them."""
    visible = [c for c in text if not c.isspace()]
    if not visible:
        return TextStats()
    letters = 0
    for char in visible:
        if char.isalpha():
            letters += 2 if _is_unspaced(char) else 1
    words: List[str] = []
    count = 0
    for token in _WORD.findall(text):
        unspaced = sum(1 for c in token if _is_unspaced(c))
        count += max(1, unspaced // 2) if unspaced else 1
        # A run of an unspaced script is many words: judge its variety by characters.
        words.extend(token) if unspaced else words.append(token.lower())
    alpha = sum(1 for c in visible if c.isalpha())
    return TextStats(letters=letters, words=count, distinct_words=len(set(words)),
                     letter_ratio=alpha / len(visible))


def eligible_text(raw: Optional[str]) -> Optional[str]:
    """The text to detect, or ``None`` when the message is not worth a verdict.

    At least :data:`MIN_WORDS` words and :data:`MIN_LETTERS` letters once links,
    mentions, custom emojis and code are gone, mostly letters, and not one word
    repeated. A short "ok lol" never gets a button — and never costs a model run.
    """
    text = clean_text(raw)
    if not text:
        return None
    stats = measure(text)
    if (stats.words < MIN_WORDS or stats.letters < MIN_LETTERS
            or stats.letter_ratio < MIN_LETTER_RATIO
            or stats.distinct_words < MIN_DISTINCT_WORDS):
        return None
    return text[:MAX_DETECT_CHARS]


# --------------------------------------------------------------------------- #
# Detector
# --------------------------------------------------------------------------- #

#: Minimum normalised probability of the best language.
MIN_CONFIDENCE = 0.90
#: Short texts are ambiguous ("tu es trop forte" is French, Spanish, Italian...):
#: below this many letters the detector must be nearly certain.
SHORT_LETTERS = 40
MIN_CONFIDENCE_SHORT = 0.97


def required_confidence(text: str) -> float:
    """Confidence a verdict on ``text`` needs: stricter on short messages."""
    return MIN_CONFIDENCE_SHORT if measure(text).letters < SHORT_LETTERS else MIN_CONFIDENCE


@dataclass(frozen=True)
class Detection:
    language: str      # one of SUPPORTED_LANGUAGES
    confidence: float  # normalised probability, 0..1


class Detector:
    """``py3langid`` restricted to :data:`SUPPORTED_LANGUAGES`, loaded on first use.

    The model is the one heavy thing in the translate button (~55-65 MB resident,
    numpy included), so nothing is imported or built until a server actually
    needs a verdict, and the loading itself is meant to run in a worker thread
    (:meth:`load`). Classification afterwards is ~0.05 ms: safe on the event loop.
    """

    def __init__(self) -> None:
        self._identifier = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._identifier is not None

    def load(self) -> None:
        """Build the identifier (blocking, ~1 s). Idempotent and thread-safe."""
        if self._identifier is not None:
            return
        with self._lock:
            if self._identifier is not None:
                return
            from py3langid.langid import MODEL_FILE, LanguageIdentifier

            identifier = LanguageIdentifier.from_model_file(MODEL_FILE, norm_probs=True)
            identifier.set_languages(list(SUPPORTED_LANGUAGES))
            self._trim(identifier)
            self._identifier = identifier

    @staticmethod
    def _trim(identifier) -> None:
        """Free the full-model matrix ``set_languages`` keeps for a later reset.

        We never reset, and the original is ~28 MB. Private attribute, so
        best-effort: a different library version just keeps the memory.
        """
        try:
            identifier._full_model = (identifier.nb_ptc, identifier.nb_pc, identifier.nb_classes)
        except Exception:  # noqa: BLE001
            logger.debug("Could not trim the language model", exc_info=True)

    def detect(self, text: str) -> Optional[Detection]:
        """Best language of ``text`` if the detector is sure enough, else ``None``.

        Call :meth:`load` first (a model not loaded yet answers ``None``).
        """
        identifier = self._identifier
        if identifier is None or not text:
            return None
        try:
            language, confidence = identifier.classify(text)
        except Exception:  # noqa: BLE001 — a detector failure must never break a message
            logger.debug("Language detection failed", exc_info=True)
            return None
        language = base_language(language)
        if language not in SUPPORTED_LANGUAGES or confidence < required_confidence(text):
            return None
        return Detection(language, float(confidence))


# --------------------------------------------------------------------------- #
# Known languages of a server
# --------------------------------------------------------------------------- #

#: Days of daily counters kept and read.
WINDOW_DAYS = 14
#: Counters outlive the window by a day so a read never races an expiry.
COUNTER_TTL_DAYS = WINDOW_DAYS + 1
#: A language is "usually spoken" from this share of the detected messages…
USUAL_SHARE = 0.10
#: …but only once this many messages were sampled; before, the rule is silent.
MIN_SAMPLE = 200


def window_days(today: date, days: int = WINDOW_DAYS) -> List[date]:
    """The ``days`` most recent days, today first."""
    return [today - timedelta(days=i) for i in range(days)]


def merge_counts(daily: Iterable[Mapping[str, int]]) -> Dict[str, int]:
    """Sum several days of ``{language: count}`` (junk entries are ignored)."""
    total: Dict[str, int] = {}
    for counts in daily:
        for language, value in (counts or {}).items():
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if number > 0 and language in SUPPORTED_LANGUAGES:
                total[language] = total.get(language, 0) + number
    return total


def usual_languages(counts: Mapping[str, int], *, min_sample: int = MIN_SAMPLE,
                    min_share: float = USUAL_SHARE) -> Dict[str, float]:
    """``{language: share}`` of the languages a server usually speaks.

    Empty until ``min_sample`` messages were detected over the window: a quiet
    server must not conclude that its first three foreign messages are "usual".
    """
    total = sum(max(0, int(v)) for v in counts.values())
    if total < min_sample:
        return {}
    shares = {lang: counts[lang] / total for lang in counts if counts[lang] > 0}
    return {lang: share for lang, share in sorted(shares.items(), key=lambda kv: -kv[1])
            if share >= min_share}


def known_languages(server_locale: str, usual: Iterable[str],
                    extra: Iterable[str] = ()) -> Set[str]:
    """Languages that never get a button on a server.

    1. the server language, 2. the learnt usual languages, 3. the extra
    languages an admin added by hand.
    """
    known = {base_language(server_locale)}
    known.update(base_language(lang) for lang in usual)
    known.update(base_language(lang) for lang in extra)
    known.discard("")
    return known


def sanitize_languages(values: Optional[Sequence]) -> List[str]:
    """Stored extra languages -> supported codes, de-duplicated, in catalogue order."""
    wanted = {base_language(str(v)) for v in (values or [])}
    return [lang for lang in SUPPORTED_LANGUAGES if lang in wanted]
