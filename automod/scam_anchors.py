"""
Scam anchors — a noise-tolerant scorer for crypto / giveaway scam text.

Pure module, no I/O. It plays the role of the regex blocklist for the
``arnaque_scam`` category: a score at or above ``SCAM_ANCHOR_THRESHOLD`` ROUTES
the text to nano (``source=ancres_scam``); it never decides on its own.

Why not the blocklist? The text it scores is mostly OCR output, and OCR on a
scam screenshot is noisy (``"BANK CARO"``, ``"Vyrowith"``, ``"55,800"``). The
interface strings of these scams, however, are remarkably stable —
``Withdrawal Success!``, ``promo code``, ``bonus``, ``USDT``, ``Bank card`` —
so the scorer matches weighted anchor phrases:

* per token, with a small edit distance (≤ 1 from 4 letters, ≤ 2 from 9), so a
  misread letter still counts;
* and on the separator-free form, so words glued together by the OCR
  (``"withdrawalsuccess"``) still count.

Each anchor counts once; the score is the sum of the matched weights. One strong
anchor (a fake withdrawal proof) routes alone; weaker ones must add up (``promo
code`` + ``bonus``), which keeps an ordinary "there's a bonus level" message out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Sequence, Tuple

from .normalize import fold_accents

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Money amounts typical of the bait ("$5,600", "5 600 USDT", "5600$").
_MONEY_RE = re.compile(
    r"(?:[$€£]\s?\d[\d\s.,]{2,}\d)"
    r"|(?:\d[\d\s.,]{2,}\d\s?(?:[$€£]|usdt|usd|usdc|btc|eth|trx|sol)\b)",
    re.IGNORECASE,
)
MONEY_WEIGHT = 0.5

# (phrase, weight). Phrases are matched token-by-token (fuzzy) and compact.
# fmt: off
ANCHORS: Tuple[Tuple[str, float], ...] = (
    # Strong — the fake proof / the call to action itself.
    ("withdrawal success", 1.0), ("withdrawal successful", 1.0),
    ("was successfully", 0.75), ("successfully withdrawn", 1.0),
    ("claim your reward", 1.0), ("claim your bonus", 1.0),
    ("claim your prize", 1.0), ("crypto casino", 1.0),
    ("special promo code", 1.0), ("enter the promo code", 1.0),
    ("enter promo code", 1.0), ("receive your bonus", 1.0),
    ("retrait reussi", 1.0), ("recuperez votre bonus", 1.0),
    ("entrez le code promo", 1.0), ("free nitro", 1.0),
    ("nitro gratuit", 1.0), ("steam gift", 0.75), ("double your", 0.75),
    # Medium — scam vocabulary that needs company.
    ("promo code", 0.5), ("code promo", 0.5), ("bonus", 0.5),
    ("usdt", 0.5), ("tether", 0.5), ("bank card", 0.5),
    ("wallet address", 0.5), ("withdraw", 0.5), ("withdrawal", 0.5),
    ("airdrop", 0.5), ("giveaway", 0.5), ("casino", 0.5),
    ("deposit", 0.25), ("select crypto", 0.5), ("receive usdt", 0.75),
    ("mrbeast", 0.5), ("elon musk", 0.5), ("retrait", 0.25),
    ("portefeuille", 0.25), ("cadeau", 0.25),
    # Weak — context only.
    ("crypto", 0.25), ("cryptocurrency", 0.25), ("reward", 0.25),
    ("vip club", 0.25), ("bitcoin", 0.25), ("btc", 0.25),
)
# fmt: on


def _max_edits(length: int) -> int:
    if length >= 9:
        return 2
    if length >= 4:
        return 1
    return 0


def _edit_distance_within(a: str, b: str, limit: int) -> bool:
    """Levenshtein(a, b) ≤ limit, with an early exit (strings are short)."""
    if a == b:
        return True
    if limit == 0 or abs(len(a) - len(b)) > limit:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        row_min = i
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            value = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            cur.append(value)
            row_min = min(row_min, value)
        if row_min > limit:
            return False
        prev = cur
    return prev[-1] <= limit


def tokenize(text: str) -> List[str]:
    """Lowercase, accent-folded alphanumeric tokens (no leetspeak folding: OCR
    misreads are not leetspeak, and digits matter for amounts)."""
    return _TOKEN_RE.findall(fold_accents((text or "").lower()))


def _phrase_in_tokens(phrase: Sequence[str], tokens: Sequence[str]) -> bool:
    n = len(phrase)
    for start in range(len(tokens) - n + 1):
        if all(
            _edit_distance_within(tokens[start + k], phrase[k], _max_edits(len(phrase[k])))
            for k in range(n)
        ):
            return True
    return False


@dataclass(frozen=True)
class AnchorScore:
    score: float
    anchors: Tuple[str, ...]


# Pre-split once.
_SPLIT_ANCHORS = tuple((phrase, tuple(phrase.split()), weight) for phrase, weight in ANCHORS)


def score(text: str) -> AnchorScore:
    """Sum of the weights of the distinct anchors found in ``text``."""
    if not text:
        return AnchorScore(0.0, ())
    tokens = tokenize(text)
    compact = "".join(tokens)
    found: List[str] = []
    total = 0.0
    for phrase, parts, weight in _SPLIT_ANCHORS:
        joined = "".join(parts)
        # Compact containment only for long anchors: short ones ("btc", "usdt")
        # would hit inside unrelated glued words.
        hit = (len(joined) >= 8 and joined in compact) or _phrase_in_tokens(parts, tokens)
        if hit:
            found.append(phrase)
            total += weight
    if _MONEY_RE.search(fold_accents(text)):
        found.append("montant")
        total += MONEY_WEIGHT
    return AnchorScore(round(total, 3), tuple(found))


def routes(text: str, threshold: float) -> bool:
    return score(text).score >= threshold
