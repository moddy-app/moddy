"""Password and UUID generators for /password and /uuid — pure.

Every random choice goes through :mod:`secrets` (the OS CSPRNG); ``random`` is
never used here. Nothing generated is stored or logged anywhere.
"""

from __future__ import annotations

import math
import secrets
import string
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #

KIND_STRONG = "strong"
KIND_CLEAR = "clear"  # no look-alikes
KIND_PASSPHRASE = "passphrase"
KINDS = (KIND_STRONG, KIND_CLEAR, KIND_PASSPHRASE)

MIN_LENGTH = 8
MAX_LENGTH = 128
DEFAULT_LENGTH = 20

MIN_WORDS = 4
MAX_WORDS = 10
#: /password length -> number of words: about one word per four characters.
CHARS_PER_WORD = 4

SYMBOLS = "!@#$%^&*-_=+?~.,:;/()[]{}<>|"
#: Characters that are easy to mistake for one another when read or typed.
LOOK_ALIKES = "Il1O0o"
#: Symbols that are ambiguous in print or awkward to type on some keyboards.
AMBIGUOUS_SYMBOLS = "~.,:;/()[]{}<>|"

_WORDLIST = Path(__file__).parent / "data" / "eff_large_wordlist.txt"


def _alphabets(kind: str) -> Tuple[str, ...]:
    """The character classes of ``kind`` (a password has at least one of each)."""
    upper, lower, digits, symbols = (string.ascii_uppercase, string.ascii_lowercase,
                                     string.digits, SYMBOLS)
    if kind == KIND_CLEAR:
        strip = LOOK_ALIKES + AMBIGUOUS_SYMBOLS
        classes = [c for c in (upper, lower, digits, symbols)]
        return tuple("".join(ch for ch in cls if ch not in strip) for cls in classes)
    return (upper, lower, digits, symbols)


def clamp_length(length: int) -> int:
    return max(MIN_LENGTH, min(MAX_LENGTH, int(length)))


def word_count_for(length: int) -> int:
    """Passphrase word count for a /password ``length`` (4 to 10 words)."""
    return max(MIN_WORDS, min(MAX_WORDS, int(length) // CHARS_PER_WORD))


@lru_cache(maxsize=1)
def wordlist() -> Tuple[str, ...]:
    """The EFF large wordlist (7776 words), vendored in ``tools/data``."""
    words = tuple(w for w in _WORDLIST.read_text(encoding="utf-8").split() if w)
    if len(words) != 7776:  # pragma: no cover - guards a corrupted vendored file
        raise RuntimeError("EFF wordlist is corrupted")
    return words


def _shuffle(items: List[str]) -> None:
    # Fisher-Yates with secrets.randbelow (random.shuffle is not a CSPRNG).
    for i in range(len(items) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        items[i], items[j] = items[j], items[i]


def generate_password(kind: str = KIND_STRONG, length: int = DEFAULT_LENGTH) -> str:
    """A random password (``strong`` / ``clear``) or passphrase (``passphrase``)."""
    if kind == KIND_PASSPHRASE:
        return generate_passphrase(word_count_for(length))
    classes = _alphabets(kind)
    length = clamp_length(length)
    pool = "".join(classes)
    chars = [secrets.choice(cls) for cls in classes]  # at least one of each class
    chars += [secrets.choice(pool) for _ in range(length - len(chars))]
    _shuffle(chars)
    return "".join(chars)


def generate_passphrase(words: int = MIN_WORDS) -> str:
    """``Word-word3-word-word``: EFF words, hyphens, one capital, one digit."""
    words = max(MIN_WORDS, min(MAX_WORDS, int(words)))
    chosen = [secrets.choice(wordlist()) for _ in range(words)]
    capital = secrets.randbelow(words)
    chosen[capital] = chosen[capital].capitalize()
    index = secrets.randbelow(words)
    chosen[index] = f"{chosen[index]}{secrets.randbelow(10)}"
    return "-".join(chosen)


def pool_size(kind: str) -> int:
    return len("".join(_alphabets(kind)))


def entropy_bits(kind: str, length: int) -> float:
    """Estimated entropy in bits of what :func:`generate_password` produces.

    Characters: ``length * log2(pool)`` — a slight over-estimate (one character
    per class is forced), well within the margin of a strength label.
    Passphrase: ``words * log2(7776)`` plus the capital and digit choices.
    """
    if kind == KIND_PASSPHRASE:
        words = word_count_for(length)
        return (words * math.log2(len(wordlist()))
                + math.log2(words)              # which word is capitalised
                + math.log2(words * 10))        # which word gets which digit
    return clamp_length(length) * math.log2(pool_size(kind))


# Strength labels by entropy: (minimum bits, key).
STRENGTH_LEVELS = ((120, "excellent"), (80, "strong"), (60, "good"), (40, "fair"), (0, "weak"))


def strength(bits: float) -> str:
    for minimum, key in STRENGTH_LEVELS:
        if bits >= minimum:
            return key
    return "weak"


def strength_cells(bits: float, cells: int = 10) -> int:
    """Filled cells of the strength bar (full at 120 bits, at least one)."""
    return max(1, min(cells, round(cells * bits / 120)))


# --------------------------------------------------------------------------- #
# UUIDs
# --------------------------------------------------------------------------- #

VERSIONS = ("v4", "v7")
MIN_COUNT = 1
MAX_COUNT = 10


def uuid7(*, now_ms: Optional[int] = None) -> uuid.UUID:
    """UUID version 7 per RFC 9562 §5.7.

    48 bits unix epoch milliseconds, 4 bits version (0b0111), 12 random bits,
    2 bits variant (0b10), 62 random bits. Uses the standard library's
    ``uuid.uuid7`` when the running Python has it (3.14+).
    """
    if now_ms is None and hasattr(uuid, "uuid7"):
        return uuid.uuid7()  # type: ignore[attr-defined]
    millis = int(time.time() * 1000) if now_ms is None else int(now_ms)
    rand = int.from_bytes(secrets.token_bytes(10), "big")  # 80 random bits
    rand_a = (rand >> 68) & 0xFFF                          # 12 bits
    rand_b = rand & ((1 << 62) - 1)                        # 62 bits
    value = (millis & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b
    return uuid.UUID(int=value)


def generate_uuids(version: str = "v4", count: int = 1) -> List[str]:
    count = max(MIN_COUNT, min(MAX_COUNT, int(count)))
    make = uuid7 if version == "v7" else uuid.uuid4
    return [str(make()) for _ in range(count)]
