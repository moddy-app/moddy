"""
Step 3 — regex blocklist (explicit terms).

A match never sanctions: it routes the message to nano with ``source=regex``.
That tolerance is intentional — the blocklist is deliberately aggressive
(anti-circumvention substring matching can over-trigger, e.g. the "Scunthorpe"
problem), and nano is the safety net that makes the actual call.

Two matching modes:

* ``words``  — matched with word boundaries against the *spaced* normalized form
  (good for short/ambiguous terms and multi-word phrases).
* ``compact`` — matched as a substring against the *separator-stripped* form
  (defeats ``f.d.p`` / ``f_d_p`` / ``f-d-p`` / ``f d p`` style obfuscation;
  reserve for unambiguous, sufficiently long terms).

Lists are bilingual (FR + EN). Adding a term is just editing the dicts below.
"""

from __future__ import annotations

import re
from typing import List, Optional

from .normalize import normalize_spaced, normalize_compact, collapse_repeats
from .schemas import BlocklistEntry

# Each category: indicative gravity + term lists. Gravity is *indicative only*
# (passed to nano), never decisional — a match merely routes to nano.
#
# Normalization already neutralizes most obfuscation BEFORE matching, so the
# lists below stay readable instead of enumerating every spelling:
#   * accents folded   (négro → negro)
#   * leetspeak folded (n1k3r → niker, sa10pe → salope, f@g → fag)
#   * separators stripped for ``compact`` (f.d.p / f-d-p / f_d_p → fdp)
#   * 3+ char repeats collapsed (saalope → salope) + repeat/concat de-spam.
# Rule of thumb: short or dictionary-ambiguous terms (con, cul, ass, pd, viol…)
# go in ``words`` (boundary match) to avoid the Scunthorpe problem; longer
# unambiguous terms go in ``compact`` to defeat separator obfuscation.
# fmt: off
_CATEGORIES = {
    # Mild profanity (no target / no intent on its own) — low gravity, nano
    # decides whether it actually warrants anything.
    "vulgarite": {
        "gravite": "basse",
        "compact": ["putain", "foutre"],
        "words": [
            "merde", "ptn", "con", "conne", "cul", "chier", "chiant", "chiante",
            "bordel", "zut", "putain", "wtf", "crap", "damn",
        ],
    },
    # Generic insults / contempt.
    "insultes": {
        "gravite": "moyenne",
        "compact": [
            # French
            "connard", "connasse", "conard", "conasse", "salope", "salopes",
            "salaud", "salopard", "salopards", "enfoire", "enfoiree", "batard",
            "batards", "abruti", "abrutie", "cretin", "cretine", "imbecile",
            "debile", "tocard", "minable", "guignol", "pouffiasse", "poufiasse",
            "pute", "putes", "encule", "enculee", "enculer", "enculé", "enflure",
            "ducon", "ordure", "fdp", "ftg", "ntm", "niquetamere", "filsdepute",
            "trouduc", "trouducul", "branleur", "branleuse", "branlette",
            "branler", "couillon", "couillonne", "bouffon", "bouffonne",
            "bouffonne", "merdeux", "sacamerde", "kehba", "chibre", "bolosse",
            "boloss", "bouffonnerie", "gueulard",
            # English
            "asshole", "assholes", "bastard", "bitch", "biatch", "btch",
            "dumbass", "dipshit", "jackass", "scumbag", "douchebag", "douche",
            "moron", "loser", "dickhead", "prick", "wanker", "twat", "knobhead",
            "shithead", "motherfucker", "fuckface", "shitbag", "pieceofshit",
            "fucker", "fuckers", "fuckin", "fucking", "fuck",
        ],
        "words": [
            # short / ambiguous → boundary match only
            "tg", "tgl", "blc", "bdp", "bdc", "zebi", "naze", "guele", "gueule",
            "ta gueule", "ferme ta gueule", "ta geule",
            "stfu", "shut the fuck up", "you suck", "you re trash", "ass",
        ],
    },
    # Hate speech / discrimination (race, religion, sexual orientation, gender,
    # disability…). High gravity.
    "haine_discrimination": {
        "gravite": "haute",
        "compact": [
            # French slurs
            "negre", "negro", "bougnoul", "bougnoule", "bicot", "youpin",
            "youpine", "pede", "pedale", "tarlouze", "tapette", "tantouze",
            "gouine", "tranny", "mongol", "mongolien", "trisomique",
            "salearabe", "salejuif", "salenoir", "saleblanc", "salepd",
            "raton", "feuj",
            # English slurs
            "nigger", "nigga", "niggers", "chink", "spic", "kike", "wetback",
            "faggot", "faggots", "dyke", "retard", "retarded",
            "gook", "paki", "coon", "sandnigger", "towelhead", "raghead",
        ],
        "words": [
            "pd", "fag", "nazi", "hitler", "staline", "goulag",
            "go back to your country", "white trash", "gas the",
            "sieg heil", "heil hitler",
        ],
    },
    # Threats / incitement to violence. High gravity.
    "menaces": {
        "gravite": "haute",
        "compact": [],
        "words": [
            # French
            "je vais te tuer", "je vais te buter", "je vais te defoncer",
            "je vais te crever", "je vais te frapper", "je te retrouve",
            "je sais ou tu habites", "je sais ou tu vis", "tu vas mourir",
            "je vais te casser la gueule", "je vais te demonter",
            "fais gaffe a toi", "tu vas le regretter", "je vais te faire la peau",
            # English
            "i will kill you", "i'll kill you", "im gonna kill you",
            "i will find you", "i know where you live", "you re dead",
            "you are dead", "im gonna beat you", "i will hurt you",
            "watch your back", "you'll regret this", "im going to hurt you",
        ],
    },
    # Incitement to self-harm / suicide directed at someone. Critical.
    "incitation_automutilation": {
        "gravite": "haute",
        "compact": [],
        "words": [
            # French
            "tue toi", "va te tuer", "suicide toi", "va te pendre", "pends toi",
            "finis en avec ta vie", "tu devrais mourir", "tu devrais te tuer",
            "personne ne t aime tue toi", "creve",
            # English
            "kill yourself", "kys", "go kill yourself", "you should die",
            "you should kill yourself", "go die", "hang yourself", "end your life",
            "nobody loves you kill yourself", "neck yourself",
        ],
    },
    # Unsolicited sexual content / sexual harassment.
    "contenu_sexuel": {
        "gravite": "moyenne",
        "compact": [
            "salopdebite", "sucemoi", "branlemoi", "showbob", "sendnudes",
            "hentai", "fellation", "cunnilingus", "penetration", "penetrer",
            "pornhub", "xvideos",
        ],
        "words": [
            # French
            "bite", "zizi", "couille", "couilles", "anal", "penis", "chatte",
            "chienne", "suce", "sucer", "baise", "baiser", "niquer", "nique",
            "viol", "viole", "violer", "violeur", "nude", "nudes", "boobs",
            "dick", "envoie ton nu", "envoie tes nudes", "montre tes seins",
            "montre ton cul", "je vais te violer", "pedo", "pedophile",
            # English
            "show me your tits", "show bob", "send me nudes", "i will rape you",
            "im gonna rape you", "suck my",
        ],
    },
    # Doxxing / threat to leak personal data. High gravity.
    "doxxing": {
        "gravite": "haute",
        "compact": [
            "doxx", "doxxer", "doxxing", "doxer", "deanon", "deanonymiser",
        ],
        "words": [
            # French
            "dox", "je vais te dox", "je vais te doxx", "balance son ip",
            "balance ton ip", "balance son adresse", "balance ton adresse",
            "je connais ton adresse", "je vais leak ton adresse",
            "je vais leak tes infos", "je divulgue ton adresse",
            "ton ip est", "j'ai ton ip", "jai ton ip", "ip grab",
            # English
            "i will dox you", "im gonna dox you", "leak your address",
            "leak your ip", "i have your ip", "i know your address",
            "post your address", "expose your address", "ip logger",
        ],
    },
}
# fmt: on

# Raw emoji / emoji-sequence flags. Checked against the *raw* (lowercased)
# content BEFORE normalization strips non-alphanumerics, so vulgar gestures are
# still caught. Each: (substring, categorie, gravite_indicative).
_EMOJI_TERMS = [
    ("\U0001F595", "insultes", "moyenne"),            # 🖕 middle finger
    (":middle_finger:", "insultes", "moyenne"),       # literal shortcode
    ("\U0001F44C\U0001F448", "contenu_sexuel", "moyenne"),  # 👌👈
    ("\U0001F449\U0001F44C", "contenu_sexuel", "moyenne"),  # 👉👌
    (":ok_hand::point_left:", "contenu_sexuel", "moyenne"),
    (":point_right::ok_hand:", "contenu_sexuel", "moyenne"),
]


def normalize_for_match(content: str) -> tuple[str, str]:
    """Return (spaced, compact) normalized forms of the content."""
    spaced = normalize_spaced(content)
    return spaced, spaced.replace(" ", "")


class Blocklist:
    """Compiled explicit-term blocklist."""

    def __init__(self):
        self._entries: List[BlocklistEntry] = []
        # Raw (pre-normalization) substring flags: list of (substr, entry).
        self._emoji_entries: List[tuple] = []
        self._build()

    def _build(self):
        for substr, categorie, gravite in _EMOJI_TERMS:
            self._emoji_entries.append((substr.lower(), BlocklistEntry(
                pattern=re.compile(re.escape(substr)),
                categorie=categorie,
                gravite_indicative=gravite,
                compact=False,
            )))
        for categorie, data in _CATEGORIES.items():
            gravite = data["gravite"]
            for term in data.get("compact", []):
                if not term:
                    continue
                self._entries.append(BlocklistEntry(
                    pattern=re.compile(re.escape(term.replace(" ", ""))),
                    categorie=categorie,
                    gravite_indicative=gravite,
                    compact=True,
                ))
            for term in data.get("words", []):
                if not term:
                    continue
                self._entries.append(BlocklistEntry(
                    pattern=re.compile(rf"\b{re.escape(term)}\b"),
                    categorie=categorie,
                    gravite_indicative=gravite,
                    compact=False,
                ))

    def match(self, content: str) -> Optional[BlocklistEntry]:
        """Return the first matching entry, or None. Normalizes internally.

        Tests both the plain normalized form and a *repeat-collapsed* form so
        spammed/concatenated evasions ("je vais te tuer" ×40, "tuertuertuer")
        still hit the word-boundary patterns.
        """
        # Raw emoji / gesture flags first (normalization would strip them).
        raw = (content or "").lower()
        if raw:
            for substr, entry in self._emoji_entries:
                if substr in raw:
                    return entry

        spaced, compact = normalize_for_match(content)
        if not compact:
            return None
        # Additional de-spammed surface (cheap; only differs when repeated).
        collapsed_spaced = collapse_repeats(content)
        collapsed_compact = collapsed_spaced.replace(" ", "")
        spaced_forms = {spaced, collapsed_spaced}
        compact_forms = {compact, collapsed_compact}
        for entry in self._entries:
            haystacks = compact_forms if entry.compact else spaced_forms
            if any(h and entry.pattern.search(h) for h in haystacks):
                return entry
        return None


# Module-level singleton (the term lists are global, identical for all guilds).
_blocklist: Optional[Blocklist] = None


def get_blocklist() -> Blocklist:
    global _blocklist
    if _blocklist is None:
        _blocklist = Blocklist()
    return _blocklist
