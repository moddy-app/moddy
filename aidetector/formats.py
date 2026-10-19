"""Image type sniffing and the cheap "could this file carry C2PA?" test.

Both run on raw bytes before anything expensive: the module sees every image
posted in every server, and the vast majority carry no Content Credentials, so
the c2pa reader is only woken up when a marker is actually present.
"""

from __future__ import annotations

from typing import Optional

MIME_PNG = "image/png"
MIME_JPEG = "image/jpeg"
MIME_WEBP = "image/webp"
SUPPORTED_MIMES = (MIME_PNG, MIME_JPEG, MIME_WEBP)

_EXTENSIONS = {
    ".png": MIME_PNG,
    ".jpg": MIME_JPEG,
    ".jpeg": MIME_JPEG,
    ".jfif": MIME_JPEG,
    ".webp": MIME_WEBP,
}

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def sniff_mime(data: bytes) -> Optional[str]:
    """MIME type from the magic bytes, or None when it is none of the three.

    The bytes decide, never the file name or Discord's content type: a renamed
    file must still be read with the right parser.
    """
    if data.startswith(PNG_SIGNATURE):
        return MIME_PNG
    if data[:3] == b"\xff\xd8\xff":
        return MIME_JPEG
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return MIME_WEBP
    return None


def declared_mime(filename: Optional[str], content_type: Optional[str]) -> Optional[str]:
    """What an attachment *claims* to be, used to skip non-images before download."""
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype in SUPPORTED_MIMES:
        return ctype
    if ctype == "image/jpg":
        return MIME_JPEG
    name = (filename or "").lower()
    for ext, mime in _EXTENSIONS.items():
        if name.endswith(ext):
            return mime
    return None


def may_contain_c2pa(data: bytes) -> bool:
    """True when the bytes hold an embedded C2PA manifest store marker.

    Every embedding puts the JUMBF box labelled ``c2pa`` in the file (JPEG
    APP11 segments, the PNG ``caBX`` chunk, the WebP ``C2PA`` chunk). A plain
    substring search is a few milliseconds even on a 20 MB file, and a false
    positive only costs one reader call that finds nothing.
    """
    return b"c2pa" in data or b"caBX" in data
