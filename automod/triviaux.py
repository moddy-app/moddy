"""
Step 2 — trivial allowlist.

Zero-cost exit for ultra-frequent, *always* harmless messages (laughter,
acknowledgement, greetings). These represent a large share of Discord traffic.

SAFETY CONSTRAINT: only pure interjections that can **never** be toxic in
context belong here. No short, potentially contemptuous or ambiguous word.

The list is intentionally bilingual (FR + EN) and covers the common chat-speak
spellings. Repeated-letter variants ("mdrrrr", "okkk") are handled by
:func:`automod.normalize.normalize_trivial`, so only the canonical spelling
needs listing.
"""

from __future__ import annotations

from .normalize import normalize_trivial

# fmt: off
TRIVIAUX: frozenset[str] = frozenset({
    # --- French: laughter -------------------------------------------------
    "mdr", "ptdr", "lol", "lool", "jpp", "xd", "xpdr", "hihi", "haha",
    "ahah", "ahaha", "héhé", "hehe", "mddr", "mort de rire",
    # --- French: agreement / acknowledgement ------------------------------
    "ok", "okk", "oki", "okay", "dac", "dacc", "daccord", "d'accord", "ça marche",
    "ca marche", "cv", "rien", "ouais", "oui", "ouip", "yep", "yes", "yep",
    "carrément", "carrement", "exact", "exactement", "vrai", "grave", "trop vrai",
    "tout à fait", "tout a fait", "bien sûr", "bien sur", "evidemment", "évidemment",
    # --- French: negation -------------------------------------------------
    "non", "nan", "nope", "nn", "jamais",
    # --- French: greetings / politeness -----------------------------------
    "slt", "salut", "cc", "coucou", "bonjour", "bonsoir", "bjr", "bsr",
    "wsh", "wesh", "yo", "hey", "hello", "re", "rebonjour", "bye", "byebye",
    "ciao", "à plus", "a plus", "a+", "bonne nuit", "merci", "mrc", "mci",
    "de rien", "drien", "stp", "svp", "bienvenue",
    # --- French: reactions / fillers --------------------------------------
    "gg", "gege", "bravo", "félicitations", "felicitations", "nice", "cool",
    "top", "génial", "genial", "parfait", "super", "wow", "waw", "oh", "ah",
    "bah", "ben", "euh", "heu", "hmm", "mmh", "bof", "ouf", "enfin",
    "jsp", "jpp", "jsais pas", "je sais pas", "aucune idée", "aucune idee",
    "+1", "pareil", "idem", "moi aussi", "same",
    # --- English: laughter ------------------------------------------------
    "lmao", "lmfao", "rofl", "lel", "kek", "hahah", "hehe", "heh",
    # --- English: agreement / acknowledgement -----------------------------
    "yeah", "yea", "yup", "yup", "yas", "true", "facts", "fr", "frfr",
    "agreed", "indeed", "exactly", "right", "ofc", "of course", "sure",
    "alright", "aight", "kk", "k", "gotcha", "got it", "noted", "fine",
    # --- English: negation ------------------------------------------------
    "no", "nah", "naw", "never",
    # --- English: greetings / politeness ----------------------------------
    "hi", "hiya", "heya", "sup", "wassup", "morning", "evening", "gm", "gn",
    "good morning", "good night", "goodnight", "welcome", "wb", "thanks", "thx",
    "ty", "tysm", "thank you", "np", "no problem", "cya", "see ya", "later",
    "peace", "take care",
    # --- English: reactions / fillers -------------------------------------
    "gg", "wp", "nice", "cool", "awesome", "great", "perfect", "amazing",
    "wow", "woah", "omg", "oof", "huh", "hmm", "meh", "welp", "anyway",
    "idk", "dunno", "no idea", "me too", "same here", "this", "based",
})
# fmt: on


def est_trivial(content: str) -> bool:
    """True if the message is a trivial, always-harmless interjection."""
    return normalize_trivial(content) in TRIVIAUX
