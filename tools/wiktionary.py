"""Wiktionary entries for ``/define``.

Parsing is pure (wikitext in, :class:`Entry` out); :func:`fetch_entry` /
:func:`get_entry` take the bot's ``public_api`` client as an argument, so
nothing here imports Discord or opens a connection on its own.

Every Wiktionary edition has its own conventions, so each one gets a small
section reader:

====  ==================================================  ==========================
Wiki  Language section / part of speech                    Definitions / synonyms
====  ==================================================  ==========================
fr    ``== {{langue|fr}} ==`` / ``{{S|nom|fr}}``           ``# …`` / ``{{S|synonymes}}``
en    ``==English==`` / ``===Noun===``                     ``# …`` / ``====Synonyms====``
es    ``== {{lengua|es}} ==`` / ``{{sustantivo|es}}``      ``;1: …`` / ``Sinónimos:``
pt    ``={{-pt-}}=`` / ``==Substantivo==``                 ``# …`` / ``{{-sin-}}``
de    ``== Wort ({{Sprache|Deutsch}}) ==`` / ``{{Wortart``  ``{{Bedeutungen}}`` ``:[1] …``
====  ==================================================  ==========================

fr and en are handled carefully; es / pt / de are best effort. Whatever the
parser cannot read ends as "no definition" — never as an exception. When the
requested edition yields nothing, the English Wiktionary REST definitions
(HTML) are a last resort.
"""

from __future__ import annotations

import html
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

from tools.text_escape import escape_text

WIKI_LANGUAGES: Tuple[str, ...] = ("fr", "en", "es", "pt", "de")
MAX_WORD_LENGTH = 50
CACHE_TTL = 7 * 24 * 3600
NEGATIVE_CACHE_TTL = 24 * 3600
MAX_DEFINITIONS_STORED = 8
MAX_SYNONYMS = 8
DEFINITION_LIMIT = 260

API_URL = "https://{lang}.wiktionary.org/w/api.php"
REST_URL = "https://en.wiktionary.org/api/rest_v1/page/definition/{word}"
PAGE_URL = "https://{lang}.wiktionary.org/wiki/{title}"

_FORBIDDEN_TITLE_CHARS = set("#<>[]|{}:\n\r\t")


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

@dataclass
class Meaning:
    #: Canonical part-of-speech key (``noun``, ``verb``…) or ``other``.
    pos: str
    #: Heading as written on the wiki, used when ``pos`` is ``other``.
    label: str
    definitions: List[str] = field(default_factory=list)
    synonyms: List[str] = field(default_factory=list)
    #: Etymology number (en) so two "Noun" sections can be told apart; 0 if unknown.
    etymology: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"p": self.pos, "l": self.label, "d": self.definitions,
                "s": self.synonyms, "e": self.etymology}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Meaning":
        return cls(pos=data.get("p", "other"), label=data.get("l", ""),
                   definitions=list(data.get("d", [])), synonyms=list(data.get("s", [])),
                   etymology=int(data.get("e", 0)))


@dataclass
class Entry:
    word: str
    lang: str
    meanings: List[Meaning] = field(default_factory=list)
    #: ``"parse"`` (the requested edition) or ``"rest"`` (English Wiktionary fallback).
    source: str = "parse"

    @property
    def url(self) -> str:
        wiki = self.lang if self.source == "parse" else "en"
        return page_url(wiki, self.word)

    def to_dict(self) -> Dict[str, Any]:
        return {"w": self.word, "g": self.lang, "s": self.source,
                "m": [m.to_dict() for m in self.meanings]}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Entry":
        return cls(word=data["w"], lang=data["g"], source=data.get("s", "parse"),
                   meanings=[Meaning.from_dict(m) for m in data.get("m", [])])


def page_url(lang: str, title: str) -> str:
    return PAGE_URL.format(lang=lang, title=quote(title.replace(" ", "_"), safe="_(),'"))


def valid_word(word: str) -> bool:
    """Reject empty, over-long or title-illegal input before any request."""
    word = (word or "").strip()
    if not word or len(word) > MAX_WORD_LENGTH:
        return False
    return not any(ch in _FORBIDDEN_TITLE_CHARS for ch in word)


# --------------------------------------------------------------------------- #
# Part of speech
# --------------------------------------------------------------------------- #

#: (keyword prefixes, canonical key) — first match wins, so specific entries come first.
_POS_TABLE: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("nom propre", "proper noun", "nombre propio", "nome próprio", "nome proprio",
      "eigenname", "toponym"), "proper_noun"),
    (("prénom", "nom de famille", "given name", "surname", "vorname", "nachname",
      "nombre de pila", "apellido", "primeiro nome", "sobrenome"), "name"),
    (("proverbe", "proverb", "proverbio", "provérbio", "sprichwort"), "proverb"),
    (("numéral", "numeral", "numerale", "adjectif numéral", "numeral"), "numeral"),
    (("nom", "noun", "sustantivo", "substantivo", "substantiv"), "noun"),
    (("verbe", "verb", "verbo"), "verb"),
    (("adjectif", "adjective", "adjetivo", "adjektiv"), "adjective"),
    (("adverbe", "adverb", "adverbio", "advérbio"), "adverb"),
    (("pronom", "pronoun", "pronombre", "pronome"), "pronoun"),
    (("préposition", "preposition", "preposición", "preposição", "präposition", "postposition"),
     "preposition"),
    (("conjonction", "conjunction", "conjunción", "conjunção", "konjunktion"), "conjunction"),
    (("interjection", "interjección", "interjeição", "interjektion"), "interjection"),
    (("article", "artículo", "artigo", "artikel"), "article"),
    (("déterminant", "determiner", "determinante", "determinativo"), "determiner"),
    (("locution", "phrase", "locución", "locução", "redewendung", "wortverbindung", "idiom",
      "expresión", "expressão", "expressao"), "phrase"),
    (("particule", "particle", "partícula", "partikel"), "particle"),
    (("préfixe", "prefix", "prefijo", "prefixo", "präfix"), "prefix"),
    (("suffixe", "suffix", "sufijo", "sufixo"), "suffix"),
    (("abréviation", "sigle", "abbreviation", "acronym", "initialism", "abreviatura", "sigla",
      "abkürzung", "contraction"), "abbreviation"),
    (("symbole", "lettre", "symbol", "letter", "símbolo", "letra", "buchstabe"), "symbol"),
    (("onomatopée", "onomatopoeia", "onomatopeya", "onomatopeia", "onomatopoetikum"),
     "onomatopoeia"),
    (("infixe", "infix", "interfix", "circumfix", "affix", "affixe", "interfixe", "enclitic",
      "proverb"), "other"),
)

_FR_NON_POS = {"étymologie", "synonymes", "antonymes", "dérivés", "traductions", "prononciation"}


def canon_pos(term: str) -> Optional[str]:
    """Canonical key for a heading term, or ``None`` when it is not a part of speech."""
    low = re.sub(r"\s+", " ", (term or "").strip().lower().replace("_", " ").replace("-", " "))
    if not low or low in _FR_NON_POS:
        return None
    for prefixes, key in _POS_TABLE:
        for prefix in prefixes:
            if low == prefix or low.startswith(prefix + " ") or low.startswith(prefix + "s ") \
                    or low == prefix + "s":
                return key
    return None


# --------------------------------------------------------------------------- #
# Wikitext cleaning
# --------------------------------------------------------------------------- #

_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_REF_RE = re.compile(r"<ref[^>]*?/>|<ref[^>]*>.*?</ref>", re.S | re.I)
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
_TEMPLATE_RE = re.compile(r"\{\{([^{}]*)\}\}")
_LINK_RE = re.compile(r"\[\[([^\[\]]*)\]\]")
_EXTLINK_RE = re.compile(r"\[(?:https?:)?//[^\s\]]+(?:\s+([^\]]*))?\]")
_NAMED_ARG_RE = re.compile(r"^\s*[\w\- ]{1,20}=")
_LANG_CODE_RE = re.compile(r"^[a-z]{2,3}(?:-[a-z]{2,8})?$")
_EMPTY_PARENS_RE = re.compile(r"\(\s*[,;:]?\s*\)")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;])")
_NAMESPACE_PREFIXES = (
    "file:", "image:", "category:", "catégorie:", "fichier:", "image:", "categoría:", "categoria:",
    "archivo:", "imagen:", "arquivo:", "imagem:", "kategorie:", "datei:", "bild:",
)

_LINK_TEMPLATES = {"lien", "l", "link", "m", "mention", "term", "l-self", "m-self", "lien-ext",
                   "lien-lang", "l-lien", "zh-l", "ja-l", "ko-l", "vern"}
_LABEL_TEMPLATES = {"lb", "lbl", "label", "context", "cx", "lexique", "contexte", "k", "csem",
                    "uso", "escopo", "especialidad", "registro", "domaine", "déf-ctx"}
_QUALIFIER_TEMPLATES = {"q", "qual", "qualifier", "i", "qf", "qualifier-lite", "sense", "gloss",
                        "gl", "s-gloss"}
_NON_GLOSS_TEMPLATES = {"n-g", "ng", "non-gloss", "non-gloss definition", "nongloss"}
_SYN_TEMPLATES = {"syn", "synonyms", "synonym", "sinónimo", "sinonimo", "sinônimo", "synonyme"}
_ALT_FORM_NAMES = {"alt form", "alt sp", "alt spell", "altspell", "altform", "alt"}
_DROP_TEMPLATES = {
    "pron", "ipa", "ipa-lite", "audio", "ref", "réf", "source", "sources", "conj", "conjugaison",
    "grammaire", "wikipédia", "wikipedia", "voir", "voir aussi", "trad", "exemple", "ex", "t", "t+",
    "senseid", "sid", "ux", "usex", "uxi", "quote", "cite", "rfdef", "rfv", "rfd", "rfc",
    "attention", "nocat", "defdate", "taxlink", "taxon", "pron-graf", "pronunciación",
    "ampliable", "clé de tri", "refnec", "réf?", "pron-lite",
}
_FR_ABBREVIATIONS = {
    "par ext": "par extension", "p ext": "par extension", "par extension": "par extension",
    "fig": "figuré", "fam": "familier", "pop": "populaire", "vx": "vieux", "péj": "péjoratif",
    "pej": "péjoratif", "vulg": "vulgaire", "litt": "littéraire", "arg": "argot",
    "didact": "didactique", "plur": "au pluriel", "sing": "au singulier",
}
_HTML_TEXT_TEMPLATES = {"!": "|", "=": "=", "...": "…", "ellipsis": "…", "nbsp": " ", "-": "–"}


def _split_args(content: str) -> Tuple[str, List[str], Dict[str, str]]:
    parts = content.split("|")
    name = parts[0].strip()
    positional: List[str] = []
    named: Dict[str, str] = {}
    for part in parts[1:]:
        if _NAMED_ARG_RE.match(part):
            key, _, value = part.partition("=")
            named[key.strip().lower()] = value.strip()
        else:
            positional.append(part.strip())
    return name, positional, named


def _drop_lang(args: List[str], wiki: str) -> List[str]:
    """Remove a leading language code argument (en-style templates)."""
    if len(args) >= 1 and (args[0].lower() == wiki or (_LANG_CODE_RE.match(args[0]) and len(args) > 1)):
        return args[1:]
    return args


def _label_text(items: Iterable[str]) -> str:
    out: List[str] = []
    connectors = {"and", "or", "also", "with", "_", "nor", "chiefly", "usually", "especially"}
    for item in items:
        item = item.strip()
        if not item:
            continue
        if item in connectors and out:
            out[-1] = out[-1] + (" " if item == "_" else f" {item}")
            continue
        if out and out[-1].endswith(("and", "or", "also", "with", "nor")) and not out[-1].endswith(","):
            out[-1] = out[-1] + " " + item
            continue
        out.append(item)
    return ", ".join(part.strip() for part in out if part.strip())


def expand_template(content: str, wiki: str) -> str:
    """Readable text for one (innermost) ``{{template}}``; ``""`` when it carries none."""
    name, args, named = _split_args(content)
    low = name.lower().replace("_", " ").strip()
    if not low:
        return ""
    if low in _HTML_TEXT_TEMPLATES:
        return _HTML_TEXT_TEMPLATES[low]
    if low in _DROP_TEMPLATES or low.startswith("ébauche") or low.startswith("rf"):
        return ""

    # Links: {{lien|mot|fr}} (word first) or {{l|en|word|alt}} (language first).
    if low in _LINK_TEMPLATES or low.startswith("l."):
        if "dif" in named or "alt" in named:
            return named.get("dif") or named.get("alt", "")
        if wiki == "fr" and low != "m":
            words = args
        elif low.startswith("l."):
            words = args
        else:
            words = _drop_lang(args, wiki) if len(args) > 1 else args
        if wiki == "fr" and low != "m" and len(args) > 1 and not _LANG_CODE_RE.match(args[0]):
            words = args
        if not words:
            return ""
        return words[1] if len(words) > 1 and words[1] and wiki != "fr" else words[0]
    if low in ("w", "pedia", "wp"):
        shown = [a for a in args if a]
        return shown[1] if len(shown) > 1 else (shown[0] if shown else "")

    # Labels / qualifiers: rendered as "(a, b)".
    if low in _LABEL_TEMPLATES:
        items = _drop_lang(args, wiki)
        if wiki == "de":
            items = items[:1]
        if low in ("lb", "lbl", "label", "context", "cx"):
            items = _drop_lang(args, wiki) if args and _LANG_CODE_RE.match(args[0]) else args
        items = [i for i in items if i and i.lower() != wiki or low in ("lb", "lbl", "label")]
        text = _label_text(items)
        return f"({text})" if text else ""
    if low in _QUALIFIER_TEMPLATES:
        text = _label_text(args)
        return f"({text})" if text else ""
    if low in _NON_GLOSS_TEMPLATES:
        return args[0] if args else ""
    if low in _SYN_TEMPLATES:
        items = _drop_lang(args, wiki)
        return ", ".join(i for i in items if i)

    # Form-of templates: "plural of cat", "alternative form of colour".
    if low.endswith(" of") or low.endswith(" form of") or low in _ALT_FORM_NAMES:
        targets = [a for a in args if a]
        if targets and _LANG_CODE_RE.match(targets[0]) and len(targets) > 1:
            targets = targets[1:]
        label = "alternative form of" if low in _ALT_FORM_NAMES else low
        return f"{label} {targets[0]}" if targets else label
    if low == "plm":
        text = args[0] if args else ""
        return text[:1].upper() + text[1:]

    # fr / pt / es context labels: {{familier|fr}}, {{zoo|pt}}, {{par ext}}.
    if wiki != "en" and len(args) <= 1 and (not args or args[0].lower() == wiki):
        if not args and low not in _FR_ABBREVIATIONS:
            return ""
        shown = _FR_ABBREVIATIONS.get(low, name)
        if len(shown) <= 30:
            return f"({shown})"
    return ""


def clean_wikitext(text: str, wiki: str) -> str:
    """Wikitext fragment to plain text (templates and links expanded, markup removed)."""
    text = _COMMENT_RE.sub("", text or "")
    text = _REF_RE.sub("", text)
    text = _BR_RE.sub(" ", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    for _ in range(10):
        new = _TEMPLATE_RE.sub(lambda m: expand_template(m.group(1), wiki), text)
        if new == text:
            break
        text = new
    text = text.replace("{{", "").replace("}}", "")

    def link(match: "re.Match[str]") -> str:
        inner = match.group(1)
        target, sep, shown = inner.partition("|")
        if target.strip().lower().startswith(_NAMESPACE_PREFIXES):
            return ""
        if target.startswith(":"):
            target = target[1:]
        if sep:
            return shown if shown else target.split("#")[0]
        return target.split("#")[0] if target.startswith("#") is False else target.lstrip("#")

    for _ in range(3):
        new = _LINK_RE.sub(link, text)
        if new == text:
            break
        text = new
    text = text.replace("[[", "").replace("]]", "")
    text = _EXTLINK_RE.sub(lambda m: m.group(1) or "", text)
    text = text.replace("'''", "").replace("''", "")
    text = _EMPTY_PARENS_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    return text.strip(" :*;")


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #

_HEADING_RE = re.compile(r"^(={1,6})\s*(.*?)\s*(={1,6})\s*$")


@dataclass
class Section:
    level: int
    title: str
    lines: List[str] = field(default_factory=list)


def split_sections(wikitext: str) -> List[Section]:
    """Split a page into sections; the first one (level 0) is the preamble."""
    wikitext = _COMMENT_RE.sub("", wikitext or "").replace("\r\n", "\n")
    sections = [Section(0, "")]
    for line in wikitext.split("\n"):
        match = _HEADING_RE.match(line)
        if match and match.group(2):
            level = min(len(match.group(1)), len(match.group(3)))
            title = match.group(2).strip("= ")
            sections.append(Section(level, title))
        else:
            sections[-1].lines.append(line)
    return sections


def _is_language_heading(title: str, wiki: str) -> bool:
    low = title.lower()
    if wiki == "fr":
        return bool(re.search(r"\{\{\s*langue\s*\|\s*fr\s*[|}]", low))
    if wiki == "en":
        return low.strip() == "english"
    if wiki == "es":
        return bool(re.search(r"\{\{\s*lengua\s*\|\s*es\s*[|}]", low)) or low.strip() == "español"
    if wiki == "pt":
        return "-pt-" in low or low.strip() in ("português", "portugues")
    if wiki == "de":
        return bool(re.search(r"\{\{\s*sprache\s*\|\s*deutsch\s*\}\}", low))
    return False


def language_sections(sections: List[Section], wiki: str) -> List[Section]:
    """The language heading's section and every sub-section under it."""
    for index, section in enumerate(sections):
        if section.level and _is_language_heading(section.title, wiki):
            end = len(sections)
            for later in range(index + 1, len(sections)):
                if sections[later].level and sections[later].level <= section.level:
                    end = later
                    break
            return sections[index:end]
    return []


def _fr_pos_term(title: str) -> Optional[str]:
    match = re.match(r"\{\{\s*S\s*\|\s*([^|}]+)", title, re.I)
    return match.group(1).strip() if match else None


def heading_pos(title: str, wiki: str) -> Optional[Tuple[str, str]]:
    """``(canonical key, label)`` when a heading opens a part-of-speech section."""
    if wiki == "fr":
        term = _fr_pos_term(title)
        if not term:
            return None
        key = canon_pos(term)
        return (key, term.capitalize()) if key else None
    if wiki == "de":
        match = re.search(r"\{\{\s*Wortart\s*\|\s*([^|}]+)", title, re.I)
        term = match.group(1).strip() if match else clean_wikitext(title, wiki)
        key = canon_pos(term)
        return (key, term) if key else None
    term = title
    template = re.match(r"\{\{\s*([^|}]+)", title)
    if template:
        term = template.group(1).strip().strip("-")
    else:
        term = clean_wikitext(title, wiki)
    key = canon_pos(term)
    return (key, term.capitalize()) if key else None


def _is_synonym_heading(title: str, wiki: str) -> bool:
    if wiki == "fr":
        term = _fr_pos_term(title)
        return bool(term) and term.lower() == "synonymes"
    low = clean_wikitext(title, wiki).lower() or title.lower()
    low = low.strip("-{} ")
    return low in ("synonyms", "sinónimos", "sinonimos", "sinônimos", "sinónimos", "synonyme",
                   "sin", "synonymes")


_ETYMOLOGY_HEADING_RE = re.compile(r"^etymology\s*(\d+)?$", re.I)


# --------------------------------------------------------------------------- #
# Definitions and synonyms
# --------------------------------------------------------------------------- #

_HASH_DEF_RE = re.compile(r"^#(?![#:*;])\s*(.*)$")
_ES_DEF_RE = re.compile(r"^;\s*\d+[^:]*:\s*(.*)$")
_DE_DEF_RE = re.compile(r"^:\s*\[\s*[\d,\-–\s.f]+\]\s*(.*)$")
_DE_MARKER_RE = re.compile(r"^\{\{\s*([^|}]+?)\s*\}\}\s*$")
_ES_SYN_LINE_RE = re.compile(r"^\*+\s*'*\s*sin[oó]nimos?\s*'*\s*:?\s*'*\s*:?\s*(.*)$", re.I)
_PT_SYN_MARKER_RE = re.compile(r"^\{\{\s*-sin-\s*\}\}\s*$|^=+\s*sin[oô]nimos?\s*=+$", re.I)
_PT_MARKER_RE = re.compile(r"^\{\{\s*-[\w]+-\s*\}\}\s*$")
_PARENTHESES_RE = re.compile(r"\([^)]*\)")


def _trim(text: str, limit: int = DEFINITION_LIMIT) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "…"


def _definition_ok(text: str) -> bool:
    letters = sum(1 for ch in text if ch.isalpha())
    return letters >= 2


def extract_definitions(lines: List[str], wiki: str) -> List[str]:
    """Definition lines of one part-of-speech section, cleaned."""
    raw: List[str] = []
    if wiki in ("fr", "en", "pt"):
        for line in lines:
            match = _HASH_DEF_RE.match(line.strip())
            if match:
                raw.append(match.group(1))
    elif wiki == "es":
        for line in lines:
            match = _ES_DEF_RE.match(line.strip())
            if match:
                raw.append(match.group(1))
    elif wiki == "de":
        active = False
        for line in lines:
            stripped = line.strip()
            marker = _DE_MARKER_RE.match(stripped)
            if marker:
                active = marker.group(1).strip().lower() == "bedeutungen"
                continue
            if active:
                match = _DE_DEF_RE.match(stripped)
                if match:
                    raw.append(match.group(1))
    out: List[str] = []
    for item in raw:
        cleaned = clean_wikitext(item, wiki)
        if _definition_ok(cleaned):
            out.append(_trim(cleaned))
        if len(out) >= MAX_DEFINITIONS_STORED:
            break
    return out


def _synonym_items(raw_lines: Iterable[str], wiki: str) -> List[str]:
    found: List[str] = []
    for raw in raw_lines:
        text = clean_wikitext(raw, wiki)
        text = _PARENTHESES_RE.sub("", text)
        for piece in re.split(r"[,;/]", text):
            piece = piece.strip(" .:*·-–")
            piece = re.sub(r"^\[\s*[\d,\-–\s.f]+\]\s*", "", piece).strip()
            if not piece or len(piece) > 40 or len(piece.split()) > 4:
                continue
            if not any(ch.isalpha() for ch in piece) or piece in ("…", "..."):
                continue
            if piece.lower() not in (s.lower() for s in found):
                found.append(piece)
    return found[:MAX_SYNONYMS]


def synonyms_from_section(lines: List[str], wiki: str) -> List[str]:
    """Synonyms of a dedicated "Synonyms" heading (fr, en, pt)."""
    items = [line.strip()[1:] if line.strip().startswith(("*", ":")) else line.strip()
             for line in lines
             if line.strip().startswith(("*", ":")) or "{{syn" in line.lower()]
    return _synonym_items(items, wiki)


def synonyms_inline(lines: List[str], wiki: str) -> List[str]:
    """Synonyms written inside the part-of-speech section itself (es, pt, de)."""
    raw: List[str] = []
    if wiki == "es":
        for line in lines:
            match = _ES_SYN_LINE_RE.match(line.strip())
            if match:
                raw.append(match.group(1))
    elif wiki == "de":
        active = False
        for line in lines:
            stripped = line.strip()
            marker = _DE_MARKER_RE.match(stripped)
            if marker:
                active = marker.group(1).strip().lower() == "synonyme"
                continue
            if active and stripped.startswith(":"):
                raw.append(stripped[1:])
    elif wiki == "pt":
        active = False
        for line in lines:
            stripped = line.strip()
            if _PT_SYN_MARKER_RE.match(stripped):
                active = True
                continue
            if _PT_MARKER_RE.match(stripped) or stripped.startswith("="):
                active = False
                continue
            if active and stripped.startswith("*"):
                raw.append(stripped[1:])
    return _synonym_items(raw, wiki)


# --------------------------------------------------------------------------- #
# Entry parsing
# --------------------------------------------------------------------------- #

def parse_wikitext(wikitext: str, title: str, wiki: str) -> Entry:
    """Entry for the ``wiki`` language section of a page (empty meanings if none)."""
    entry = Entry(word=title, lang=wiki)
    sections = language_sections(split_sections(wikitext), wiki)
    if not sections:
        return entry
    current: Optional[Meaning] = None
    etymology = 0
    pending_synonyms: List[str] = []
    for section in sections[1:]:
        etym = _ETYMOLOGY_HEADING_RE.match(section.title) if wiki == "en" else None
        if etym:
            etymology = int(etym.group(1) or 1)
            continue
        pos = heading_pos(section.title, wiki)
        if pos is not None:
            key, label = pos
            current = Meaning(
                pos=key, label=label,
                definitions=extract_definitions(section.lines, wiki),
                synonyms=synonyms_inline(section.lines, wiki),
                etymology=etymology,
            )
            if pending_synonyms and not current.synonyms:
                current.synonyms = pending_synonyms[:MAX_SYNONYMS]
                pending_synonyms = []
            if current.definitions:
                entry.meanings.append(current)
            continue
        if _is_synonym_heading(section.title, wiki):
            items = synonyms_from_section(section.lines, wiki)
            if current is not None:
                for item in items:
                    if len(current.synonyms) < MAX_SYNONYMS and item.lower() not in (
                            s.lower() for s in current.synonyms):
                        current.synonyms.append(item)
            else:
                pending_synonyms = items
    return entry


def _html_to_text(value: str) -> str:
    text = _TAG_RE.sub("", value or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_rest(data: Any, word: str, lang: str) -> Entry:
    """Entry from the English Wiktionary REST ``definition`` payload (HTML definitions)."""
    entry = Entry(word=word, lang=lang, source="rest")
    if not isinstance(data, dict):
        return entry
    for block in data.get(lang) or []:
        if not isinstance(block, dict):
            continue
        definitions = []
        for item in block.get("definitions") or []:
            text = _html_to_text(item.get("definition", "")) if isinstance(item, dict) else ""
            if _definition_ok(text):
                definitions.append(_trim(text))
            if len(definitions) >= MAX_DEFINITIONS_STORED:
                break
        if definitions:
            label = str(block.get("partOfSpeech") or "")
            entry.meanings.append(Meaning(pos=canon_pos(label) or "other",
                                          label=label, definitions=definitions))
    return entry


# --------------------------------------------------------------------------- #
# Display helpers
# --------------------------------------------------------------------------- #

_LEADING_LABELS_RE = re.compile(r"^((?:\([^()]{1,60}\)\s*)+)(.*)$", re.S)


def split_labels(definition: str) -> Tuple[str, str]:
    """``"(slang) A person."`` -> ``("(slang)", "A person.")``."""
    match = _LEADING_LABELS_RE.match(definition)
    if match and match.group(2).strip():
        return match.group(1).strip(), match.group(2).strip()
    return "", definition


def render_definition(definition: str) -> str:
    """Escaped markdown for one definition, its leading ``(labels)`` in italics."""
    labels, rest = split_labels(definition)
    body = escape_text(rest, at_line_start=False)
    if labels:
        return f"*{escape_text(labels, at_line_start=False)}* {body}"
    return body


def dedupe_labels(meanings: List[Meaning], labels: List[str]) -> List[str]:
    """Add ``(2)``, ``(3)``… to labels that appear more than once."""
    totals: Dict[str, int] = {}
    for label in labels:
        totals[label] = totals.get(label, 0) + 1
    seen: Dict[str, int] = {}
    out = []
    for label in labels:
        if totals[label] > 1:
            seen[label] = seen.get(label, 0) + 1
            out.append(f"{label} ({seen[label]})")
        else:
            out.append(label)
    return out


# --------------------------------------------------------------------------- #
# Fetching (the caller passes ``bot.public_api``)
# --------------------------------------------------------------------------- #

_MEMORY: "OrderedDict[str, Optional[Dict[str, Any]]]" = OrderedDict()
_MEMORY_MAX = 64


def _memory_get(key: str):
    if key in _MEMORY:
        _MEMORY.move_to_end(key)
        return True, _MEMORY[key]
    return False, None


def _memory_put(key: str, value: Optional[Dict[str, Any]]) -> None:
    _MEMORY[key] = value
    _MEMORY.move_to_end(key)
    while len(_MEMORY) > _MEMORY_MAX:
        _MEMORY.popitem(last=False)


async def fetch_entry(api, word: str, lang: str) -> Optional[Entry]:
    """Look ``word`` up on the ``lang`` Wiktionary (then the English REST fallback).

    Returns ``None`` when nothing was found; lets ``PublicAPIError`` (timeout,
    network, rate limit) propagate when even the fallback could not answer.
    """
    from services.public_api import NOT_FOUND, PublicAPIError

    candidates = [word]
    if word.lower() != word:
        candidates.append(word.lower())
    last_error: Optional[PublicAPIError] = None
    for candidate in candidates:
        try:
            data = await api.get_json(
                API_URL.format(lang=lang),
                params={"action": "parse", "page": candidate, "prop": "wikitext",
                        "format": "json", "formatversion": "2", "redirects": "1"},
                cache_ttl=0,
            )
        except PublicAPIError as exc:
            if exc.code == NOT_FOUND:
                continue
            last_error = exc
            break
        parsed = data.get("parse") if isinstance(data, dict) else None
        if not isinstance(parsed, dict):
            continue  # {"error": {"code": "missingtitle"}}
        wikitext = parsed.get("wikitext")
        if isinstance(wikitext, dict):  # formatversion=1 shape, defensive
            wikitext = wikitext.get("*")
        entry = parse_wikitext(wikitext or "", str(parsed.get("title") or candidate), lang)
        if entry.meanings:
            return entry
    for candidate in candidates:
        try:
            data = await api.get_json(
                REST_URL.format(word=quote(candidate, safe="")), cache_ttl=0)
        except PublicAPIError as exc:
            if exc.code != NOT_FOUND:
                last_error = last_error or exc
            continue
        entry = parse_rest(data, candidate, lang)
        if entry.meanings:
            return entry
    if last_error is not None:
        raise last_error
    return None


async def get_entry(api, word: str, lang: str) -> Optional[Entry]:
    """:func:`fetch_entry` with a Redis cache (7 days) and a tiny in-process LRU."""
    word = word.strip()
    key = api.cache_key("define", lang, word)
    hit, value = _memory_get(key)
    if not hit:
        cached = await api.cache_get(key)
        if cached is not None:
            value = None if cached.get("missing") else cached
            hit = True
            _memory_put(key, value)
    if hit:
        return Entry.from_dict(value) if value else None
    entry = await fetch_entry(api, word, lang)
    payload = entry.to_dict() if entry else None
    _memory_put(key, payload)
    await api.cache_set(key, payload if payload else {"missing": True},
                        CACHE_TTL if payload else NEGATIVE_CACHE_TTL)
    return entry
