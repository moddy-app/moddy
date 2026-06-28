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
