"""
Text normalization shared by the trivial allowlist and the regex blocklist.

We deliberately avoid an external ``unidecode`` dependency: a small, explicit
accent-folding table covers the French/English diacritics we care about. This
keeps the pipeline dependency-free (no numpy / unidecode) while still defeating
the most common obfuscations.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

# Leetspeak / homoglyph folding applied before matching, so "n1k3r", "sa10pe",
# "f@g" collapse onto their letter forms.
_LEET_MAP = str.maketrans({
    "0": "o",
    "1": "i",
    "3": "e",
    "4": "a",
    "5": "s",
    "7": "t",
    "@": "a",
    "$": "s",
    "€": "e",
    "!": "i",
})

_repeat_re = re.compile(r"(.)\1{2,}")        # 3+ repeats → 1 ("saalope" handled too)
_nonalnum_re = re.compile(r"[^a-z0-9]+")


def fold_accents(text: str) -> str:
    """Strip diacritics using Unicode decomposition (é → e, ç → c…)."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _base(text: str) -> str:
    text = fold_accents(text.lower())
    text = text.translate(_LEET_MAP)
    text = _repeat_re.sub(r"\1", text)
    return text


def normalize_spaced(text: str) -> str:
    """Word-preserving form: lowercase, de-accented, separators → single space.

    Suitable for word-boundary matching (``\\bterm\\b``).
    """
    return _nonalnum_re.sub(" ", _base(text)).strip()


def normalize_compact(text: str) -> str:
    """Anti-circumvention form: like :func:`normalize_spaced` but with no
    separators at all, so ``f.d.p`` / ``f_d_p`` / ``f-d-p`` all become ``fdp``.
    """
    return normalize_spaced(text).replace(" ", "")


def normalize_trivial(text: str) -> str:
    """Normalizer for the trivial allowlist ("mdrrrr" → "mdr", "okkk" → "ok")."""
    return _repeat_re.sub(r"\1", text.lower().strip())


# --- Repeat / concatenation de-spam --------------------------------------- #
# Defeats evasion by repetition: "je vais te tuer" repeated 40× (with or without
# separators) must collapse back to a single occurrence so the word-boundary
# blocklist hits and the embedding isn't diluted by the spam.

def _collapse_consecutive_words(text: str) -> str:
    """Drop immediately-repeated word runs: "tuer tuer tuer" → "tuer"."""
    out: list[str] = []
    for tok in text.split():
        if not out or out[-1] != tok:
            out.append(tok)
    return " ".join(out)


def _periodic_unit(s: str) -> Optional[str]:
    """If ``s`` is exactly a unit repeated ≥2×, return the smallest unit.

    Detection runs on the given string *with its spaces preserved*, so a phrase
    concatenated without separators ("je vais te tuer" ×40, where the joins read
    "tuerje…") is reduced back to "je vais te tuer" — spaces intact — which the
    word-boundary blocklist can then match.
    """
    n = len(s)
    if n < 4:
        return None
    for p in range(1, n // 2 + 1):
        if n % p != 0:
            continue
        unit = s[:p]
        if unit * (n // p) == s:
            return unit
    return None


def collapse_repeats(text: str) -> str:
    """Collapse repeated words and repeated whole-string units.

    Returns a de-spammed rendering used as an *additional* matching surface for
    the blocklist and the embedder. Operates on the spaced-normalized form.
    """
    spaced = normalize_spaced(text)
    if not spaced:
        return spaced

    # 1. Exact whole-string period on the spaced form (keeps the unit's spaces).
    unit = _periodic_unit(spaced)
    if unit is not None:
        return unit.strip()

    # 2. Same on the separator-free form ("tuertuertuer" → "tuer").
    compact = spaced.replace(" ", "")
    cunit = _periodic_unit(compact)
    if cunit is not None and len(cunit) <= 40:
        return cunit

    # 3. Token-level period for clean phrase repeats ("je vais te tuer " ×N).
    tokens = _collapse_consecutive_words(spaced).split()
    m = len(tokens)
    if m >= 2:
        for p in range(1, m // 2 + 1):
            if m % p == 0 and tokens[:p] * (m // p) == tokens:
                return " ".join(tokens[:p])
    return _collapse_consecutive_words(spaced)
