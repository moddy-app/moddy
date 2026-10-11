"""
Server-defined (custom) infraction types — pure schema, templates and keywords.

A server can declare its own infraction types ("Advertising", "Spoilers",
"Politics"…) next to the built-in ones. Each is detected by the funnel (keywords
and embedding similarity, see ``automod/engine.py``), qualified by nano under a
neutral ``P<n>`` id, and sanctioned with the policy stored in
``sanctions["perso:<id>"]`` (see ``automod/infractions.py``).

The module is **pure**: no I/O, no Discord import. ``utils.i18n`` is only
imported lazily by :func:`from_template` (and may be replaced by a ``translate``
callable), so the detection core stays dependency-free.

Config schema (``guilds.data.modules.automod_ai.infractions_perso``, max 10)::

    [{"id": "p1a2",                 # [a-z0-9]{1,12}, stable, never shown to nano
      "nom": "Publicité",            # <= 40 chars, shown on cards / DMs
      "description": "…",            # <= 300 chars, what nano looks for
      "exemples": ["…"],             # <= 5 x 200 chars, embedded as references
      "mots_cles": ["discord.gg/"],  # <= 20 x 60 chars, routed without embedding
      "modele": "pub_serveur" | null # template this entry was created from
    }]

The real id is ``perso:<id>`` everywhere downstream (Decision.categorie, cases,
``sanctions`` keys).
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
import secrets
import string
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .normalize import collapse_repeats, normalize_spaced

# --- Limits -----------------------------------------------------------------

MAX_INFRACTIONS = 10
MAX_NOM = 40
MAX_DESCRIPTION = 300
MAX_EXEMPLES = 5
MAX_EXEMPLE = 200
MAX_MOTS_CLES = 20
MAX_MOT_CLE = 60

ID_RE = re.compile(r"^[a-z0-9]{1,12}$")
PERSO_PREFIX = "perso:"

#: Locales the template texts exist in (kept in sync with the i18n files).
TEMPLATE_LOCALES: Tuple[str, ...] = ("fr", "en-US", "es-ES", "pt-BR", "de")

#: Keyword evasion tolerance: a keyword whose separator-free form is at least
#: this long is also matched as a substring of the separator-free message
#: ("r.e.j.o.i.n.s mon serveur"). Shorter ones are word-boundary only
#: (Scunthorpe guard — same idea as the blocklist's compact mode).
KEYWORD_COMPACT_MIN = 6

# A stray fence marker in server-authored text must never reach the prompt.
_FENCE_RE = re.compile(r"\[/?DATA:[0-9a-fA-F]*\]?")
_WS_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Normalisation / validation
# ---------------------------------------------------------------------------

def _clean(value: Any, limit: int, *, single_line: bool = True) -> str:
    """Trim, strip fence markers, collapse whitespace, cut to ``limit``."""
    if not isinstance(value, str):
        return ""
    text = _FENCE_RE.sub("", value)
    text = _WS_RE.sub(" ", text) if single_line else text
    return text.strip()[:limit]


def _clean_list(value: Any, max_items: int, max_len: int) -> List[str]:
    if not isinstance(value, (list, tuple)):
        return []
    out: List[str] = []
    seen = set()
    for item in value:
        text = _clean(item, max_len)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= max_items:
            break
    return out


def normalize(raw: Any) -> List[dict]:
    """Return a clean ``infractions_perso`` list (garbage entries are dropped).

    An entry needs a well-formed unique ``id``, a non-empty ``nom`` and a
    non-empty ``description``; text fields are trimmed and cut to their limits,
    list fields are de-duplicated, and the list is capped at
    :data:`MAX_INFRACTIONS`.
    """
    if not isinstance(raw, (list, tuple)):
        return []
    out: List[dict] = []
    seen_ids = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        pid = entry.get("id")
        if not isinstance(pid, str) or not ID_RE.match(pid) or pid in seen_ids:
            continue
        nom = _clean(entry.get("nom"), MAX_NOM)
        description = _clean(entry.get("description"), MAX_DESCRIPTION)
        if not nom or not description:
            continue
        modele = entry.get("modele")
        out.append({
            "id": pid,
            "nom": nom,
            "description": description,
            "exemples": _clean_list(entry.get("exemples"), MAX_EXEMPLES, MAX_EXEMPLE),
            "mots_cles": _clean_list(entry.get("mots_cles"), MAX_MOTS_CLES, MAX_MOT_CLE),
            "modele": modele if isinstance(modele, str) and modele in TEMPLATES else None,
        })
        seen_ids.add(pid)
        if len(out) >= MAX_INFRACTIONS:
            break
    return out


def _list_error(value: Any, max_items: int, max_len: int) -> bool:
    if not isinstance(value, list) or len(value) > max_items:
        return True
    return any(not isinstance(v, str) or not v.strip() or len(v) > max_len for v in value)


def validate(raw: Any) -> Optional[str]:
    """Strict check of an ``infractions_perso`` list; returns an error code.

    Codes: ``infractions_perso_invalid``, ``infractions_perso_too_many``,
    ``infraction_entry_invalid``, ``infraction_id_invalid``,
    ``infraction_id_duplicate``, ``infraction_nom_invalid``,
    ``infraction_description_invalid``, ``infraction_exemples_invalid``,
    ``infraction_mots_cles_invalid`` and ``infraction_modele_invalid``.
    ``None`` (or an empty list) is valid.
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        return "infractions_perso_invalid"
    if len(raw) > MAX_INFRACTIONS:
        return "infractions_perso_too_many"
    seen = set()
    for entry in raw:
        err = validate_entry(entry)
        if err:
            return err
        if entry["id"] in seen:
            return "infraction_id_duplicate"
        seen.add(entry["id"])
    return None


def validate_entry(entry: Any) -> Optional[str]:
    """Strict check of ONE custom infraction (same codes as :func:`validate`)."""
    if not isinstance(entry, dict):
        return "infraction_entry_invalid"
    pid = entry.get("id")
    if not isinstance(pid, str) or not ID_RE.match(pid):
        return "infraction_id_invalid"
    nom = entry.get("nom")
    if not isinstance(nom, str) or not nom.strip() or len(nom) > MAX_NOM:
        return "infraction_nom_invalid"
    desc = entry.get("description")
    if not isinstance(desc, str) or not desc.strip() or len(desc) > MAX_DESCRIPTION:
        return "infraction_description_invalid"
    if "exemples" in entry and _list_error(entry["exemples"], MAX_EXEMPLES, MAX_EXEMPLE):
        return "infraction_exemples_invalid"
    if "mots_cles" in entry and _list_error(entry["mots_cles"], MAX_MOTS_CLES, MAX_MOT_CLE):
        return "infraction_mots_cles_invalid"
    modele = entry.get("modele")
    if modele is not None and (not isinstance(modele, str) or modele not in TEMPLATES):
        return "infraction_modele_invalid"
    return None


def new_id(existing_ids: Iterable[str] = ()) -> str:
    """A fresh 4-char ``[a-z0-9]`` id not in ``existing_ids``."""
    taken = {str(i) for i in (existing_ids or ())}
    alphabet = string.ascii_lowercase + string.digits
    for _ in range(1000):
        candidate = "".join(secrets.choice(alphabet) for _ in range(4))
        if candidate not in taken:
            return candidate
    # 36^4 ids and at most 10 entries: unreachable, but never loop forever.
    return "".join(secrets.choice(alphabet) for _ in range(12))


def ids(infractions: Sequence[dict]) -> List[str]:
    """The raw ids (``<id>`` part) of a list of custom infractions."""
    return [str(i.get("id")) for i in (infractions or ()) if isinstance(i, dict)]


def categorie_of(entry: dict) -> str:
    """The downstream category id (``perso:<id>``) of one custom infraction."""
    return f"{PERSO_PREFIX}{entry['id']}"


def find(infractions: Sequence[dict], categorie_or_id: str) -> Optional[dict]:
    """Look an infraction up by ``perso:<id>`` or bare ``<id>``."""
    if not isinstance(categorie_or_id, str):
        return None
    pid = categorie_or_id[len(PERSO_PREFIX):] \
        if categorie_or_id.startswith(PERSO_PREFIX) else categorie_or_id
    for entry in infractions or ():
        if isinstance(entry, dict) and entry.get("id") == pid:
            return entry
    return None


def fingerprint(infractions: Sequence[dict]) -> str:
    """Stable digest of everything that changes a verdict or its routing.

    ``""`` for no custom infractions, so a guild without any keeps exactly the
    cache keys it had before the feature existed. Part of the verdict-cache key:
    editing an infraction invalidates the verdicts qualified under the old one.
    """
    if not infractions:
        return ""
    payload = [
        [e.get("id"), e.get("nom"), e.get("description"),
         list(e.get("exemples") or ()), list(e.get("mots_cles") or ())]
        for e in infractions
    ]
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Opaque ids shown to nano
# ---------------------------------------------------------------------------

def pid_map(infractions: Sequence[dict]) -> Dict[str, str]:
    """``{"P1": "perso:<id>", …}`` in list order. The real ids never reach nano."""
    return {f"P{n}": categorie_of(e) for n, e in enumerate(infractions or (), 1)}


def parse_pid(value: Any) -> str:
    """Normalise a model-returned P-id ("p2", " P2 ") onto ``P<n>`` (else "")."""
    if not isinstance(value, str):
        return ""
    v = value.strip().upper()
    return v if re.fullmatch(r"P\d{1,2}", v) else ""


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Template:
    """A ready-made custom infraction, one click to add from the config UI."""
    id: str
    exemples: Tuple[str, ...]
    mots_cles: Tuple[str, ...]

    @property
    def name_key(self) -> str:
        return f"modules.automod_ai.custom.templates.{self.id}.name"

    @property
    def desc_key(self) -> str:
        return f"modules.automod_ai.custom.templates.{self.id}.desc"


# Examples and keywords are deliberately bilingual (FR + EN): the embedding is
# multilingual and keywords are plain substrings, so one list serves every
# server. ``langue`` carries none — what is "foreign" depends on the server, so
# the admin adds their own examples (the description alone does not route).
_TEMPLATES: Tuple[Template, ...] = (
    Template(
        "pub_serveur",
        exemples=(
            "Rejoignez mon serveur, on est déjà 3000 : discord.gg/abc123",
            "Join my server, we have giveaways and a great community",
            "Venez sur mon serveur Discord, je vous attends en MP pour le lien",
        ),
        mots_cles=(
            "discord.gg/", "discord.com/invite", "discordapp.com/invite",
            "rejoignez mon serveur", "rejoins mon serveur", "join my server",
        ),
    ),
    Template(
        "spoilers",
        exemples=(
            "Le héros meurt à la fin de la saison 3, c'est trop triste",
            "Spoiler: the killer is the butler in the last episode",
        ),
        mots_cles=("spoiler", "spoilers", "divulgâche"),
    ),
    Template(
        "politique_religion",
        exemples=(
            "Pour qui vous allez voter à la présidentielle ? Ce parti est une honte",
            "Your religion is the only true one, all the others are wrong",
            "Let's debate whether the government should ban this political party",
        ),
        mots_cles=("présidentielle", "parti politique", "political party", "elections"),
    ),
    Template(
        "demande_dm",
        exemples=(
            "Envoie-moi un mp, j'ai des photos à te montrer",
            "DM me if you want to see something private",
            "Qui veut m'envoyer des nudes ?",
        ),
        mots_cles=("dm me", "send nudes", "envoie des nudes", "mp moi"),
    ),
    Template("langue", exemples=(), mots_cles=()),
    Template(
        "liens",
        exemples=(
            "Regardez ça, c'est incroyable https://example.com/promo",
            "check this out http://some-site.example/offer",
        ),
        mots_cles=("http://", "https://", "www."),
    ),
)

TEMPLATES: Dict[str, Template] = {t.id: t for t in _TEMPLATES}


def template_ids() -> List[str]:
    """Template ids in display order."""
    return [t.id for t in _TEMPLATES]


def _default_translate() -> Callable[..., str]:
    # Lazy: keeps this module importable without the i18n stack.
    from utils.i18n import t
    return t


def from_template(template_id: str, locale: str = "fr", *,
                  existing_ids: Iterable[str] = (),
                  translate: Optional[Callable[..., str]] = None) -> dict:
    """Build a ready-to-store infraction from a Moddy template.

    ``nom`` / ``description`` are filled in ``locale`` (the SERVER language) so
    the stored text reads naturally for the admins; the keys are the
    ``modules.automod_ai.custom.templates.<id>.*`` i18n entries. A fresh id is
    generated unless ``existing_ids`` makes the caller choose one itself.
    Raises ``KeyError`` for an unknown template.
    """
    tpl = TEMPLATES[template_id]
    tr = translate or _default_translate()
    return {
        "id": new_id(existing_ids),
        "nom": _clean(tr(tpl.name_key, locale=locale), MAX_NOM),
        "description": _clean(tr(tpl.desc_key, locale=locale), MAX_DESCRIPTION),
        "exemples": list(tpl.exemples),
        "mots_cles": list(tpl.mots_cles),
        "modele": tpl.id,
    }


def is_unmodified_template(entry: dict, *,
                           translate: Optional[Callable[..., str]] = None) -> bool:
    """Whether ``entry`` still carries the exact Moddy template text.

    True only when ``modele`` names a template and ``nom``, ``description`` and
    ``exemples`` all equal the template's (in any supported locale). Keywords are
    ignored: they are matched by code and never reach a prompt. Used to skip the
    prompt-injection check for text Moddy wrote itself.
    """
    if not isinstance(entry, dict):
        return False
    tpl = TEMPLATES.get(entry.get("modele") or "")
    if tpl is None:
        return False
    if list(entry.get("exemples") or ()) != list(tpl.exemples):
        return False
    tr = translate or _default_translate()
    nom, desc = entry.get("nom"), entry.get("description")
    for loc in TEMPLATE_LOCALES:
        if (nom == _clean(tr(tpl.name_key, locale=loc), MAX_NOM)
                and desc == _clean(tr(tpl.desc_key, locale=loc), MAX_DESCRIPTION)):
            return True
    return False


# ---------------------------------------------------------------------------
# Keyword matching
# ---------------------------------------------------------------------------

#: Characters that make a keyword a URL / literal fragment ("discord.gg/").
_LITERAL_CHARS = frozenset("./:\\@#")


@dataclass(frozen=True)
class _Matcher:
    perso_id: str
    literals: Tuple[str, ...]         # lowercase raw substrings
    literals_compact: Tuple[str, ...]  # whitespace-free lowercase (URL evasion)
    words: Tuple[re.Pattern, ...]     # word-boundary on the spaced form
    compacts: Tuple[str, ...]         # substring of the separator-free form


@functools.lru_cache(maxsize=256)
def _compile(spec: Tuple[Tuple[str, Tuple[str, ...]], ...]) -> Tuple[_Matcher, ...]:
    matchers: List[_Matcher] = []
    for perso_id, keywords in spec:
        literals: List[str] = []
        literals_compact: List[str] = []
        words: List[re.Pattern] = []
        compacts: List[str] = []
        for kw in keywords:
            low = kw.strip().lower()
            if not low:
                continue
            if any(ch in _LITERAL_CHARS for ch in low):
                literals.append(low)
                literals_compact.append("".join(low.split()))
                continue
            spaced = normalize_spaced(kw)
            if not spaced:
                continue
            words.append(re.compile(rf"\b{re.escape(spaced)}\b"))
            compact = spaced.replace(" ", "")
            if len(compact) >= KEYWORD_COMPACT_MIN:
                compacts.append(compact)
        if literals or words:
            matchers.append(_Matcher(
                perso_id, tuple(literals), tuple(literals_compact),
                tuple(words), tuple(compacts)))
    return tuple(matchers)


def has_keywords(infractions: Sequence[dict]) -> bool:
    """Whether any custom infraction declares a keyword (cheap pre-check)."""
    return any(e.get("mots_cles") for e in (infractions or ()) if isinstance(e, dict))


def match_keywords(text: str, infractions: Sequence[dict]) -> Optional[str]:
    """Return the id of the first custom infraction whose keyword hits ``text``.

    Three surfaces, mirroring ``automod/blocklist.py``:

    * URL / literal fragments (a keyword with ``. / : @``) match as a
      case-insensitive substring of the raw text, and again with all whitespace
      removed ("discord .gg/ abc");
    * ordinary keywords match on word boundaries of the accent/leetspeak-folded
      text ("Spo1ler", "présidentielle" vs "presidentielle");
    * keywords of ``KEYWORD_COMPACT_MIN``+ characters also match inside the
      separator-free text ("r.e.j.o.i.n.s mon serveur"), on both the plain and
      the repeat-collapsed forms.

    Returns the bare ``<id>`` (not ``perso:<id>``), or ``None``.
    """
    if not text or not infractions:
        return None
    spec = tuple(
        (str(e["id"]), tuple(e.get("mots_cles") or ()))
        for e in infractions if isinstance(e, dict) and e.get("id") and e.get("mots_cles")
    )
    if not spec:
        return None
    matchers = _compile(spec)
    if not matchers:
        return None

    raw = text.lower()
    raw_compact = "".join(raw.split())
    spaced = normalize_spaced(text)
    collapsed = collapse_repeats(text)
    spaced_forms = tuple(f for f in {spaced, collapsed} if f)
    compact_forms = tuple(f.replace(" ", "") for f in spaced_forms)

    for m in matchers:
        if any(lit in raw for lit in m.literals):
            return m.perso_id
        if any(lit and lit in raw_compact for lit in m.literals_compact):
            return m.perso_id
        if any(p.search(h) for p in m.words for h in spaced_forms):
            return m.perso_id
        if any(c in h for c in m.compacts for h in compact_forms):
            return m.perso_id
    return None


# ---------------------------------------------------------------------------
# Embedding references
# ---------------------------------------------------------------------------

def reference_texts(infractions: Sequence[dict]) -> List[Tuple[str, str]]:
    """``[(perso_id, text)]`` to embed: each description and each example."""
    out: List[Tuple[str, str]] = []
    for e in infractions or ():
        if not isinstance(e, dict) or not e.get("id"):
            continue
        pid = str(e["id"])
        if e.get("description"):
            out.append((pid, str(e["description"])))
        for ex in e.get("exemples") or ():
            out.append((pid, str(ex)))
    return out


def references_digest(refs: Sequence[Tuple[str, str]]) -> str:
    """Content hash of the reference TEXTS (the embedding-cache key).

    The infraction ids are deliberately left out: vectors depend on the text only,
    so servers holding the same template in the same language share one entry.
    """
    raw = json.dumps([text for _pid, text in refs], ensure_ascii=False,
                     separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
