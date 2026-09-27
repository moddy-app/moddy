"""
Perceptual image hashing + the in-memory index of team-validated hashes.

Pure module (Pillow only — no numpy/scipy/imagehash, which would cost tens of MB
of resident memory for a few dozen multiplications). No Discord, no DB.

* :func:`prepare_image` decodes an attachment once, safely (pixel-count guard
  against decompression bombs, JPEG draft mode, first frame of an animation),
  and returns everything the image features need: the two hashes, a downscaled
  re-encoding for OCR / SafeSearch, and a few cheap layout facts.
* pHash — 32×32 greyscale → 2-D DCT → the 8×8 lowest frequencies vs their
  median = 64 bits. Robust to re-encoding, resizing and light recolouring: the
  same scam screenshot re-uploaded on another server lands within a few bits.
* dHash — 9×8 greyscale, each pixel vs its right neighbour = 64 bits. Cheap and
  independent of pHash; requiring BOTH to be close keeps collisions negligible.
* :class:`HashIndex` — linear scan with ``int.bit_count()``. The hash DB holds a
  few thousand rows at most; a scan is microseconds and needs no vector index.

CPU-bound work here is synchronous by design: callers run it through
``asyncio.to_thread``.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

from . import constants

try:  # Pillow is a runtime dependency; keep the import error explicit.
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover - exercised only without Pillow
    Image = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]


class ImageDecodeError(ValueError):
    """The attachment is not a decodable image (or is too large to decode)."""


# DCT-II basis for the 8 lowest frequencies of a 32-sample signal, computed once.
_N = 32
_K = 8
_DCT_ROWS: List[List[float]] = [
    [math.cos(math.pi * (2 * x + 1) * u / (2 * _N)) for x in range(_N)]
    for u in range(_K)
]


def _phash_from_grey(pixels: List[int]) -> int:
    """pHash of a 32×32 greyscale image given as a flat row-major list."""
    rows = [pixels[r * _N:(r + 1) * _N] for r in range(_N)]
    # tmp[u][x] = Σ_y C[u][y] · X[y][x]  (8×32), then low[u][v] = Σ_x tmp[u][x] · C[v][x]
    tmp = [
        [sum(_DCT_ROWS[u][y] * rows[y][x] for y in range(_N)) for x in range(_N)]
        for u in range(_K)
    ]
    low = [
        sum(tmp[u][x] * _DCT_ROWS[v][x] for x in range(_N))
        for u in range(_K) for v in range(_K)
    ]
    median = sorted(low)[len(low) // 2]
    bits = 0
    for value in low:
        bits = (bits << 1) | (1 if value > median else 0)
    return bits


def _dhash_from_grey(pixels: List[int]) -> int:
    """dHash of a 9×8 greyscale image given as a flat row-major list."""
    bits = 0
    for r in range(8):
        row = pixels[r * 9:(r + 1) * 9]
        for c in range(8):
            bits = (bits << 1) | (1 if row[c] > row[c + 1] else 0)
    return bits


def hamming(a: int, b: int) -> int:
    """Bit distance between two 64-bit hashes."""
    return (int(a) ^ int(b)).bit_count()


def to_hex(value: int) -> str:
    return f"{int(value) & 0xFFFFFFFFFFFFFFFF:016x}"


def from_hex(value: str) -> int:
    return int(value, 16)


def to_signed(value: int) -> int:
    """64-bit unsigned → signed, for a PostgreSQL BIGINT column."""
    value = int(value) & 0xFFFFFFFFFFFFFFFF
    return value - (1 << 64) if value >= (1 << 63) else value


def from_signed(value: int) -> int:
    return int(value) & 0xFFFFFFFFFFFFFFFF


@dataclass
class PreparedImage:
    """One decoded attachment, reduced to what the automod needs."""
    width: int
    height: int
    phash: int
    dhash: int
    data: bytes            # downscaled re-encoding (≤ IMAGE_MAX_SIDE), for the APIs
    mime: str
    flat_ratio: float = 0.0  # share of flat neighbouring pixels (UI/screenshot-like)

    @property
    def phash_hex(self) -> str:
        return to_hex(self.phash)

    @property
    def dhash_hex(self) -> str:
        return to_hex(self.dhash)

    @property
    def looks_like_screenshot(self) -> bool:
        """Big flat areas + a reasonable size — a UI capture rather than a photo."""
        return min(self.width, self.height) >= 400 and self.flat_ratio >= 0.55


def _flat_ratio(grey64: List[int]) -> float:
    """Share of pixels equal (±2) to their right neighbour on a 64×64 thumbnail."""
    flat = total = 0
    for r in range(64):
        row = grey64[r * 64:(r + 1) * 64]
        for c in range(63):
            total += 1
            if abs(row[c] - row[c + 1]) <= 2:
                flat += 1
    return flat / total if total else 0.0


def prepare_image(raw: bytes, *, max_side: int = constants.IMAGE_MAX_SIDE,
                  max_pixels: int = constants.IMAGE_MAX_PIXELS) -> PreparedImage:
    """Decode ``raw`` once and compute hashes + a downscaled copy.

    Raises :class:`ImageDecodeError` for anything that is not a sane image.
    """
    if Image is None:  # pragma: no cover
        raise ImageDecodeError("Pillow is not installed")
    try:
        img = Image.open(io.BytesIO(raw))
        width, height = img.size
        if width <= 0 or height <= 0 or width * height > max_pixels:
            raise ImageDecodeError(f"image too large ({width}x{height})")
        # JPEG draft mode decodes straight at a reduced scale (much less memory).
        if img.format == "JPEG":
            img.draft("RGB", (max_side, max_side))
        # Animated GIF/WebP: the first frame is what the preview shows.
        try:
            img.seek(0)
        except EOFError:
            pass
        img = ImageOps.exif_transpose(img) if img.format == "JPEG" else img
        img = img.convert("RGB")
    except ImageDecodeError:
        raise
    except Exception as exc:  # Pillow raises a zoo of exception types
        raise ImageDecodeError(str(exc)) from exc

    grey = img.convert("L")
    phash = _phash_from_grey(list(grey.resize((32, 32), Image.LANCZOS).tobytes()))
    dhash = _dhash_from_grey(list(grey.resize((9, 8), Image.LANCZOS).tobytes()))
    flat = _flat_ratio(list(grey.resize((64, 64), Image.BILINEAR).tobytes()))

    reduced = img.copy()
    reduced.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    reduced.save(buf, format="JPEG", quality=88, optimize=True)
    return PreparedImage(
        width=width, height=height, phash=phash, dhash=dhash,
        data=buf.getvalue(), mime="image/jpeg", flat_ratio=round(flat, 4),
    )


# --------------------------------------------------------------------------- #
# Team-validated hash index
# --------------------------------------------------------------------------- #

VERDICT_BLOCK = "block"
VERDICT_ALLOW = "allow"
KIND_SCAM = "scam"
KIND_NSFW = "nsfw"


@dataclass
class HashEntry:
    id: int
    phash: int
    dhash: int
    kind: str          # "scam" | "nsfw"
    verdict: str       # "block" | "allow"


@dataclass
class HashMatch:
    entry: HashEntry
    distance: int      # pHash distance

    def as_dict(self) -> dict:
        return {"id": self.entry.id, "distance": self.distance,
                "kind": self.entry.kind, "verdict": self.entry.verdict}


@dataclass
class HashIndex:
    """In-memory list of known image hashes (a few thousand at most)."""
    entries: List[HashEntry] = field(default_factory=list)

    def replace(self, entries: Iterable[HashEntry]) -> None:
        self.entries = list(entries)

    def add(self, entry: HashEntry) -> None:
        self.entries = [e for e in self.entries if e.id != entry.id] + [entry]

    def remove(self, entry_id: int) -> None:
        self.entries = [e for e in self.entries if e.id != entry_id]

    def __len__(self) -> int:
        return len(self.entries)

    def match(self, phash: int, dhash: int, *,
              max_phash: int = constants.PHASH_MAX_DISTANCE,
              max_dhash: int = constants.DHASH_MAX_DISTANCE) -> Optional[HashMatch]:
        """Closest entry within both thresholds. An ``allow`` wins a tie."""
        best: Optional[HashMatch] = None
        for entry in self.entries:
            dp = hamming(phash, entry.phash)
            if dp > max_phash or hamming(dhash, entry.dhash) > max_dhash:
                continue
            if (best is None or dp < best.distance
                    or (dp == best.distance and entry.verdict == VERDICT_ALLOW)):
                best = HashMatch(entry, dp)
        return best
