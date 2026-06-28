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

from .normalize import normalize_spaced, normalize_compact
from .schemas import BlocklistEntry

# Each category: indicative gravity + term lists. Gravity is *indicative only*
# (passed to nano), never decisional.
# fmt: off
_CATEGORIES = {
    # Generic insults / contempt.
    "insultes": {
        "gravite": "moyenne",
        "compact": [
            # French
            "connard", "connasse", "conard", "salope", "salaud", "salopard",
            "enfoire", "enfoiree", "batard", "abruti", "abrutie", "cretin",
            "cretine", "imbecile", "debile", "tocard", "minable", "guignol",
            "pouffiasse", "poufiasse", "pute", "putain", "put1", "ptn",
            "encule", "enculee", "enflure", "ducon", "ordure", "fdp", "tg",
            "ntm", "nique ta mere", "niquetamere", "fils de pute", "filsdepute",
            "trouduc", "trou du cul", "trouducul", "branleur", "branleuse",
            "couillon", "couillonne", "bouffon", "bouffonne", "merdeux",
            "grosse merde", "sac a merde", "sacamerde",
            # English
            "asshole", "assholes", "bastard", "bitch", "biatch", "btch",
            "dumbass", "dipshit", "jackass", "scumbag", "douchebag", "douche",
            "moron", "idiot", "imbecile", "loser", "dickhead", "prick",
            "wanker", "twat", "knobhead", "shithead", "motherfucker", "mf",
            "fuckface", "shitbag", "piece of shit", "pieceofshit",
        ],
        "words": [
            # short / ambiguous → boundary match only
            "con", "conne", "naze", "nul a chier", "ta gueule", "ferme ta gueule",
            "stfu", "shut the fuck up", "you suck", "you re trash",
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
            "sale arabe", "salearabe", "sale juif", "salejuif", "sale noir",
            "salenoir", "sale blanc", "saleblanc", "sale pd", "salepd",
            "raton", "feuj",
            # English slurs
            "nigger", "nigga", "niggers", "chink", "spic", "kike", "wetback",
            "faggot", "faggots", "fag", "dyke", "tranny", "retard", "retarded",
            "gook", "paki", "coon", "sandnigger", "towelhead", "raghead",
        ],
        "words": [
            "pd", "go back to your country", "white trash", "gas the",
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
    # Unsolicited sexual content / sexual harassment. High gravity.
    "contenu_sexuel": {
        "gravite": "moyenne",
        "compact": [
            "salopdebite", "suce moi", "sucemoi", "branle moi", "branlemoi",
            "showbob", "send nudes", "sendnudes",
        ],
        "words": [
            # French
            "envoie ton nu", "envoie tes nudes", "montre tes seins",
            "montre ton cul", "je vais te violer",
            # English
            "show me your tits", "show bob", "send me nudes", "i will rape you",
            "im gonna rape you", "suck my",
        ],
    },
}
# fmt: on


def normalize_for_match(content: str) -> tuple[str, str]:
    """Return (spaced, compact) normalized forms of the content."""
    spaced = normalize_spaced(content)
    return spaced, spaced.replace(" ", "")


class Blocklist:
    """Compiled explicit-term blocklist."""

    def __init__(self):
        self._entries: List[BlocklistEntry] = []
        self._build()

    def _build(self):
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
        """Return the first matching entry, or None. Normalizes internally."""
        spaced, compact = normalize_for_match(content)
        if not compact:
            return None
        for entry in self._entries:
            haystack = compact if entry.compact else spaced
            if entry.pattern.search(haystack):
                return entry
        return None


# Module-level singleton (the term lists are global, identical for all guilds).
_blocklist: Optional[Blocklist] = None


def get_blocklist() -> Blocklist:
    global _blocklist
    if _blocklist is None:
        _blocklist = Blocklist()
    return _blocklist
