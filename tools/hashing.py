"""Hashing helpers for /hash and /pwned — pure, no Discord, no network.

* :func:`hash_chunks` / :func:`hash_text` give the four digests of /hash;
* :func:`match_digest` tells which algorithm a pasted digest belongs to;
* :func:`sha1_parts` / :func:`parse_hibp_range` implement the k-anonymity
  protocol of Have I Been Pwned's range API
  (https://haveibeenpwned.com/API/v3#PwnedPasswords).
"""

from __future__ import annotations

import hashlib
import re
from typing import Dict, Iterable, Optional, Tuple

#: Algorithms shown by /hash, in display order: ``(key, label)``.
ALGORITHMS: Tuple[Tuple[str, str], ...] = (
    ("md5", "MD5"),
    ("sha1", "SHA-1"),
    ("sha256", "SHA-256"),
    ("sha512", "SHA-512"),
)

#: Hex length of each digest.
DIGEST_LENGTHS: Dict[str, int] = {"md5": 32, "sha1": 40, "sha256": 64, "sha512": 128}

#: Slice size when walking in-memory bytes.
CHUNK_SIZE = 1024 * 1024


def hash_chunks(chunks: Iterable[bytes]) -> Dict[str, str]:
    """Hex digests of every algorithm over ``chunks`` (one pass over the data)."""
    # usedforsecurity=False: MD5/SHA-1 are offered as checksums, which is what
    # people compare them to; it also keeps FIPS builds from refusing them.
    hashers = {key: hashlib.new(key, usedforsecurity=False) for key, _ in ALGORITHMS}
    for chunk in chunks:
        for hasher in hashers.values():
            hasher.update(chunk)
    return {key: hasher.hexdigest() for key, hasher in hashers.items()}


def hash_bytes(data: bytes) -> Dict[str, str]:
    """Digests of in-memory ``data``, walked in :data:`CHUNK_SIZE` slices."""
    view = memoryview(data)
    return hash_chunks(view[i:i + CHUNK_SIZE] for i in range(0, max(len(view), 1), CHUNK_SIZE))


def hash_text(text: str) -> Dict[str, str]:
    """Digests of ``text`` encoded as UTF-8, exactly as given (no stripping)."""
    return hash_bytes(text.encode("utf-8"))


_SEPARATORS = re.compile(r"[\s:\-]")
_HEX = re.compile(r"^[0-9a-f]+$")


def normalize_digest(value: str) -> str:
    """Lowercase, without spaces / colons / dashes (``AA:BB`` and ``aa bb`` compare equal)."""
    text = (value or "").strip()
    if text.lower().startswith("0x"):
        text = text[2:]
    return _SEPARATORS.sub("", text).lower()


def match_digest(digests: Dict[str, str], candidate: str) -> Optional[str]:
    """Key of the algorithm whose digest equals ``candidate``, or ``None``."""
    wanted = normalize_digest(candidate)
    if not wanted or not _HEX.match(wanted):
        return None
    for key, digest in digests.items():
        if digest == wanted:
            return key
    return None


def algorithm_label(key: str) -> str:
    return dict(ALGORITHMS).get(key, key)


# --------------------------------------------------------------------------- #
# Have I Been Pwned — Pwned Passwords range API (k-anonymity)
# --------------------------------------------------------------------------- #

HIBP_RANGE_URL = "https://api.pwnedpasswords.com/range/"
HIBP_PREFIX_LEN = 5


def sha1_parts(password: str) -> Tuple[str, str]:
    """``(prefix, suffix)`` of the upper-case SHA-1: 5 characters sent, the rest kept."""
    digest = hashlib.sha1(password.encode("utf-8"), usedforsecurity=False).hexdigest().upper()
    return digest[:HIBP_PREFIX_LEN], digest[HIBP_PREFIX_LEN:]


def parse_hibp_range(body: str, suffix: str) -> int:
    """Times ``suffix`` appears in a range response (0 when absent).

    Lines look like ``0018A45C4D1DEF81644B54AB7F969B88D65:3``. With
    ``Add-Padding: true`` the server adds fake entries whose count is 0: they
    never count as a hit.
    """
    wanted = suffix.upper()
    for line in (body or "").splitlines():
        head, sep, count = line.strip().partition(":")
        if not sep or head.upper() != wanted:
            continue
        try:
            return max(0, int(count))
        except ValueError:
            return 0
    return 0
