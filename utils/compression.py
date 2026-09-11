"""
Payload compression — one codec decision, made in one place.

Anything Moddy stores as a large opaque blob (today: ticket transcripts) goes
through here. The codec actually used is returned alongside the bytes and must
be stored with them: that is what lets a row written by a deployment with
``zstandard`` installed be read back by one without it, and vice versa, with no
migration and no guessing.

zstd at level 19 is the default because transcripts are written once and read
rarely, so spending compression time is the right trade — on conversational
JSON it lands around 90-94% smaller. ``zlib`` is the fallback, from the stdlib,
so the bot still starts and still archives if the wheel is missing.
"""

from __future__ import annotations

import zlib
from typing import Tuple

CODEC_ZSTD = "zstd"
CODEC_ZLIB = "zlib"
CODECS = (CODEC_ZSTD, CODEC_ZLIB)

# Written once, read rarely: spend the CPU on the write.
ZSTD_LEVEL = 19
ZLIB_LEVEL = 9


class UnknownCodecError(ValueError):
    """A stored row names a codec this build cannot read."""


def compress(data: bytes) -> Tuple[bytes, str]:
    """Compress ``data``, returning ``(payload, codec)``.

    The codec is part of the return value on purpose: callers must persist it
    next to the bytes rather than assume what this build happened to use.
    """
    try:
        import zstandard  # noqa: PLC0415 - optional, resolved per call
    except ImportError:
        return zlib.compress(data, ZLIB_LEVEL), CODEC_ZLIB
    return zstandard.ZstdCompressor(level=ZSTD_LEVEL).compress(data), CODEC_ZSTD


def decompress(payload: bytes, codec: str) -> bytes:
    """Restore bytes compressed by :func:`compress`."""
    if codec == CODEC_ZLIB:
        return zlib.decompress(payload)
    if codec == CODEC_ZSTD:
        try:
            import zstandard  # noqa: PLC0415
        except ImportError as exc:
            raise UnknownCodecError(
                "this row was compressed with zstd but `zstandard` is not "
                "installed on this deployment") from exc
        return zstandard.ZstdDecompressor().decompress(payload)
    raise UnknownCodecError(f"unknown codec {codec!r}")
