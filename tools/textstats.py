"""Text statistics for ``/textstats`` (pure: no Discord, no database).

Counts, reading/speaking time, most frequent words, a small stop-word language
detector (fr / en / es / pt / de) and a readability score adapted to the
detected language:

========  ====================================  ============================
Language  Formula                               Source
========  ====================================  ============================
fr        ``207 - 1.015*ASL - 73.6*ASW``        Kandel & Moles (1958)
en        ``206.835 - 1.015*ASL - 84.6*ASW``    Flesch Reading Ease (1948)
es        ``206.84 - 0.60*P - 1.02*F``          Fernandez Huerta (1959)
de        ``180 - ASL - 58.5*ASW``              Amstad (1978)
pt        ``248.835 - 1.015*ASL - 84.6*ASW``    Martins et al. (1996)
========  ====================================  ============================

``ASL`` = words per sentence, ``ASW`` = syllables per word, ``P`` = syllables per
100 words, ``F`` = sentences per 100 words. Syllables come from ``pyphen``
(imported lazily) with a vowel-group fallback when a dictionary is missing.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, FrozenSet, List, Optional, Tuple

READING_WPM = 230
SPEAKING_WPM = 150
#: Below this many words the readability score is not reliable.
MIN_WORDS_READABILITY = 30
MAX_INPUT_CHARS = 1_000_000
TOP_WORDS = 5

LANGUAGES: Tuple[str, ...] = ("fr", "en", "es", "pt", "de")

_WORD_RE = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*|\d+(?:[.,]\d+)*", re.UNICODE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])[\"'»”’)\]]*\s+|\n+")
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")
_VOWEL_GROUP_RE = re.compile(r"[aeiouyàâäáãåæéèêëíìîïóòôöõœúùûüýÿ]+", re.IGNORECASE)

_STOPWORDS_RAW: Dict[str, str] = {
    "fr": """le la les un une des du de d l et ou mais donc or ni car que qui quoi dont où
        ce cet cette ces se sa son ses mon ma mes ton ta tes notre nos votre vos leur leurs
        je tu il elle on nous vous ils elles me te lui y en à au aux avec sans sous sur dans
        par pour entre vers chez est sont était étaient être avoir ai as a avons avez ont
        avait avaient été sera ne pas plus très aussi comme si quand lorsque puis alors
        encore déjà tout tous toute toutes même peu fait faire dit""",
    "en": """the a an and or but nor so yet that which who whom whose this these those it its
        i you he she we they me him her us them my your his our their of in on at to from by
        with without for about into over after before between through is are was were be been
        being am have has had do does did will would can could should may might must not no
        as if than then when while there here what where why how all any each other such only
        own same too very just also more most some""",
    "es": """el la los las un una unos unas y o pero sino que quien quienes cuyo cual cuales este
        esta estos estas ese esa esos esas aquel aquella se su sus mi mis tu tus nuestro nuestra
        vuestro yo tú él ella nosotros vosotros ellos ellas me te lo le les nos os de del en a al
        con sin sobre bajo entre hacia desde por para es son era eran ser estar está están
        estaba fue han ha he has hemos haber había no ni muy más menos también como si cuando
        mientras entonces ya todo todos toda todas mismo poco hace hacer dice""",
    "pt": """o a os as um uma uns umas e ou mas porém que quem cujo qual quais este esta estes
        estas esse essa esses essas aquele aquela se seu sua seus suas meu minha meus minhas teu
        tua nosso nossa vosso eu tu ele ela nós vós eles elas me te lhe lhes nos vos de do da dos
        das em no na nos nas ao aos à às com sem sobre sob entre para por pelo pela pelos pelas
        é são era eram ser estar está estão estava foi foram há tem têm tinha ter haver não nem
        muito mais menos também como se quando enquanto então já todo todos toda todas mesmo
        pouco faz fazer diz você vocês""",
    "de": """der die das den dem des ein eine einen einem einer eines und oder aber sondern
        denn doch dass daß welche welcher welches wer wen wem wessen dieser diese dieses
        diesen jener jene jenes sich sein seine seiner seinen mein meine dein deine unser unsere
        euer eure ihr ihre ich du er sie es wir ihr mich dich ihn uns euch ihnen mir dir ihm von
        vom zu zum zur in im an am auf aus bei mit nach über unter vor für gegen ohne um durch
        zwischen ist sind war waren sein bin bist seid wird werden wurde wurden hat haben hatte
        hatten nicht kein keine nur auch noch schon sehr mehr wie als wenn weil während dann
        so da hier dort alle alles jede jeder mal""",
}
_STOPWORDS: Dict[str, FrozenSet[str]] = {
    lang: frozenset(raw.split()) for lang, raw in _STOPWORDS_RAW.items()
}
ALL_STOPWORDS: FrozenSet[str] = frozenset().union(*_STOPWORDS.values())
#: Words found in a single list only — the detector trusts these.
_MARKERS: Dict[str, FrozenSet[str]] = {
    lang: frozenset(
        word for word in words
        if not any(word in other for code, other in _STOPWORDS.items() if code != lang)
    )
    for lang, words in _STOPWORDS.items()
}

#: Readability level keys, from easiest to hardest (``tools.textstats.levels.*``).
LEVELS: Tuple[str, ...] = ("very_easy", "easy", "average", "difficult", "very_difficult")

_PYPHEN_CODES = {"fr": "fr", "en": "en_US", "es": "es", "pt": "pt_PT", "de": "de_DE"}


@dataclass
class Readability:
    score: float
    level: str
    formula: str  # key under tools.textstats.formulas.*


@dataclass
class TextStats:
    words: int
    characters: int
    characters_no_spaces: int
    sentences: int
    paragraphs: int
    avg_words_per_sentence: float
    reading_seconds: int
    speaking_seconds: int
    language: Optional[str]
    top_words: List[Tuple[str, int]] = field(default_factory=list)
    readability: Optional[Readability] = None
    #: True when the text is long enough for a readability score.
    enough_words: bool = False


# --------------------------------------------------------------------------- #
# Tokenising
# --------------------------------------------------------------------------- #

def tokenize(text: str) -> List[str]:
    """Words as a word counter would see them (``l'homme`` is one word)."""
    return _WORD_RE.findall(text)


def _content_tokens(text: str) -> List[str]:
    """Lower-case alphabetic pieces for frequency and language detection."""
    out: List[str] = []
    for token in _WORD_RE.findall(text.lower()):
        if token[:1].isdigit():
            continue
        if token == "aujourd'hui":
            out.append(token)
            continue
        out.extend(part for part in re.split(r"['’\-]", token) if part)
    return out


def count_sentences(text: str) -> int:
    chunks = _SENTENCE_SPLIT_RE.split(text.strip())
    return sum(1 for chunk in chunks if _WORD_RE.search(chunk))


def count_paragraphs(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    blocks = [b for b in _PARAGRAPH_SPLIT_RE.split(stripped) if b.strip()]
    if len(blocks) == 1:
        # No blank line: a chat message where every line is its own paragraph.
        lines = [line for line in stripped.split("\n") if line.strip()]
        return max(1, len(lines))
    return len(blocks)


# --------------------------------------------------------------------------- #
# Language detection
# --------------------------------------------------------------------------- #

def detect_language(text: str) -> Optional[str]:
    """Best of fr / en / es / pt / de by stop-word hits, or ``None`` when unsure."""
    tokens = _content_tokens(text)
    if len(tokens) < 4:
        return None
    scores: Dict[str, int] = {}
    for lang in LANGUAGES:
        markers = _MARKERS[lang]
        scores[lang] = sum(1 for token in tokens if token in markers)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    (best, best_score), (_, second_score) = ranked[0], ranked[1]
    if best_score < 2 or best_score < second_score * 1.3 + 0.5:
        return None
    return best


# --------------------------------------------------------------------------- #
# Syllables
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=8)
def _hyphenator(lang: str):
    try:
        import pyphen
    except ImportError:
        return None
    code = _PYPHEN_CODES.get(lang)
    if code is None or code not in pyphen.LANGUAGES:
        return None
    try:
        return pyphen.Pyphen(lang=code, left=1, right=1)
    except Exception:  # noqa: BLE001 — a broken dictionary must not break /textstats
        return None


@lru_cache(maxsize=60000)
def count_syllables(word: str, lang: str) -> int:
    """Syllables of one word (at least 1)."""
    word = word.lower()
    hyphenator = _hyphenator(lang)
    if hyphenator is not None:
        try:
            return max(1, len(hyphenator.positions(word)) + 1)
        except Exception:  # noqa: BLE001
            pass
    return max(1, len(_VOWEL_GROUP_RE.findall(word)))


# --------------------------------------------------------------------------- #
# Readability
# --------------------------------------------------------------------------- #

def _level(score: float) -> str:
    if score >= 80:
        return "very_easy"
    if score >= 60:
        return "easy"
    if score >= 40:
        return "average"
    if score >= 20:
        return "difficult"
    return "very_difficult"


def readability_score(lang: str, words: int, sentences: int, syllables: int) -> Optional[Readability]:
    """Score on a 0-100 scale (higher is easier) with the language's own formula."""
    if lang not in LANGUAGES or words <= 0 or sentences <= 0:
        return None
    asl = words / sentences
    asw = syllables / words
    per_100_syllables = asw * 100
    per_100_sentences = sentences / words * 100
    if lang == "fr":
        raw = 207 - 1.015 * asl - 73.6 * asw
    elif lang == "en":
        raw = 206.835 - 1.015 * asl - 84.6 * asw
    elif lang == "es":
        raw = 206.84 - 0.60 * per_100_syllables - 1.02 * per_100_sentences
    elif lang == "de":
        raw = 180 - asl - 58.5 * asw
    else:  # pt
        raw = 248.835 - 1.015 * asl - 84.6 * asw
    score = max(0.0, min(100.0, raw))
    return Readability(score=round(score, 1), level=_level(score), formula=lang)


def score_bar(score: float, cells: int = 10) -> str:
    filled = max(0, min(cells, round(score / 100 * cells)))
    return "█" * filled + "░" * (cells - filled)


# --------------------------------------------------------------------------- #
# Time formatting
# --------------------------------------------------------------------------- #

def format_duration(seconds: int) -> str:
    """``45 s`` / ``3 min 20 s`` / ``1 h 05 min`` — units are the same in the 5 languages."""
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds} s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {sec:02d} s" if sec else f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min" if minutes else f"{hours} h"


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def analyze(text: str) -> TextStats:
    """All the statistics of ``text`` (CPU bound: run it in a thread for big inputs)."""
    text = (text or "")[:MAX_INPUT_CHARS]
    tokens = tokenize(text)
    words = len(tokens)
    sentences = count_sentences(text)
    language = detect_language(text) if words >= 4 else None

    stop = _STOPWORDS[language] if language else ALL_STOPWORDS
    counter = Counter(
        token for token in _content_tokens(text) if len(token) >= 3 and token not in stop
    )
    top = [(w, n) for w, n in counter.most_common(TOP_WORDS) if n >= 2]

    stats = TextStats(
        words=words,
        characters=len(text),
        characters_no_spaces=sum(1 for ch in text if not ch.isspace()),
        sentences=sentences,
        paragraphs=count_paragraphs(text),
        avg_words_per_sentence=round(words / sentences, 1) if sentences else 0.0,
        reading_seconds=round(words / READING_WPM * 60) if words else 0,
        speaking_seconds=round(words / SPEAKING_WPM * 60) if words else 0,
        language=language,
        top_words=top,
        enough_words=words >= MIN_WORDS_READABILITY,
    )
    if language and stats.enough_words and sentences:
        syllables = sum(count_syllables(token, language) for token in tokens if token[:1].isalpha())
        letter_words = sum(1 for token in tokens if token[:1].isalpha())
        if letter_words:
            stats.readability = readability_score(language, letter_words, sentences, syllables)
    return stats
