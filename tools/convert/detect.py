"""Detect what a file really is: magic bytes first, then structure, then the name.

The extension and the ``content_type`` Discord reports are hints; what the
bytes say wins (a ``.png`` that is a JPEG is a JPEG). Text formats have no
magic number, so they fall back to the extension / MIME type and a light look
at the first characters.
"""

from __future__ import annotations

import io
import zipfile
from typing import Optional

from .formats import EXTENSION_TO_KEY, MIME_TO_KEY

_HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}
_AVIF_BRANDS = {b"avif", b"avis"}
_M4A_BRANDS = {b"M4A ", b"M4B ", b"M4P "}
_MOV_BRANDS = {b"qt  "}


def extension_of(filename: str) -> str:
    """Lower-case extension of the file name (``tar.gz`` as one), or ``""``."""
    name = (filename or "").rsplit("/", 1)[-1].lower()
    if name.endswith(".tar.gz"):
        return "tar.gz"
    return name.rsplit(".", 1)[1] if "." in name else ""


def key_from_name(filename: str) -> Optional[str]:
    ext = extension_of(filename)
    if ext == "tar.gz":
        return "tgz"
    return EXTENSION_TO_KEY.get(ext)


def key_from_mime(content_type: Optional[str]) -> Optional[str]:
    mime = (content_type or "").split(";")[0].strip().lower()
    return MIME_TO_KEY.get(mime)


def _sniff_zip(raw: bytes, name_key: Optional[str]) -> str:
    """A ZIP container: OOXML / ODF / EPUB documents are zips with known members."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = zf.namelist()
            if "mimetype" in names:
                try:
                    mimetype = zf.read("mimetype")[:100].decode("ascii", "ignore").strip()
                except Exception:  # noqa: BLE001
                    mimetype = ""
                if mimetype == "application/epub+zip":
                    return "epub"
                if mimetype == "application/vnd.oasis.opendocument.text":
                    return "odt"
                if mimetype == "application/vnd.oasis.opendocument.spreadsheet":
                    return "ods"
            if "[Content_Types].xml" in names:
                if any(n.startswith("word/") for n in names):
                    return "docx"
                if any(n.startswith("xl/") for n in names):
                    return "xlsx"
                if any(n.startswith("ppt/") for n in names):
                    return "pptx"
    except Exception:  # noqa: BLE001 — a damaged zip is still "a zip" for the name check
        pass
    return "zip"


def _sniff_ftyp(raw: bytes, name_key: Optional[str]) -> Optional[str]:
    brand = raw[8:12]
    if brand in _AVIF_BRANDS:
        return "avif"
    if brand in _HEIC_BRANDS:
        return "heic"
    if brand in _M4A_BRANDS:
        return "m4a"
    if brand in _MOV_BRANDS:
        return "mov"
    # isom, mp41, mp42, avc1, dash, M4V ... : MPEG-4. An audio-only .m4a often
    # carries one of these brands, so the name breaks the tie.
    return "m4a" if name_key == "m4a" else "mp4"


def sniff_bytes(raw: bytes, name_key: Optional[str] = None) -> Optional[str]:
    """Format key from the leading bytes, or ``None`` for text / unknown."""
    head = raw[:64]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:2] == b"BM" and len(raw) > 26 and int.from_bytes(raw[14:18], "little") in (
            12, 40, 52, 56, 64, 108, 124):
        return "bmp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[:4] == b"\x00\x00\x01\x00":
        return "ico"
    if head[:4] == b"%PDF":
        return "pdf"
    if head[:4] == b"PK\x03\x04" or head[:4] == b"PK\x05\x06":
        return _sniff_zip(raw, name_key)
    if head[:2] == b"\x1f\x8b":
        return "tgz"
    if head[:6] == b"7z\xbc\xaf\x27\x1c":
        return "7z"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"OggS":
        return "opus" if b"OpusHead" in raw[:128] else "ogg"
    if head[4:8] == b"ftyp":
        return _sniff_ftyp(raw, name_key)
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm" if b"webm" in raw[:64] else "mkv"
    if head[:3] == b"ID3":
        return "mp3"
    # Headerless audio streams: a frame sync is only believable when the name
    # does not already say "text" (a UTF-16 BOM is 0xFF 0xFE too).
    if name_key in (None, "mp3", "aac", "m4a", "ogg", "opus", "wav", "flac"):
        if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xF6) == 0xF0:
            return "aac"          # ADTS frame sync with layer == 0
        if len(head) >= 3 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0 \
                and (head[1] >> 1) & 3 != 0 and head[2] >> 4 != 15:
            return "mp3"          # MPEG audio frame sync
    if raw[60:68] == b"BOOKMOBI":
        return "azw3" if name_key == "azw3" else "mobi"
    return None


def _sniff_text(raw: bytes, name_key: Optional[str]) -> Optional[str]:
    """Best guess for a text file with no useful name."""
    if b"\x00" in raw[:512]:
        return None
    start = raw[:2048].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if start.startswith(b"<?xml"):
        return "xml"
    if start.startswith((b"<!doctype html", b"<html")):
        return "html"
    if start[:1] in (b"{", b"["):
        return "json"
    return None


def detect(filename: str, content_type: Optional[str], raw: bytes) -> Optional[str]:
    """Format key of a file, or ``None`` when it is not something /convert knows.

    Order: magic bytes (binary formats), then the extension, then the declared
    MIME type, then a look at the first characters of a text file.
    """
    name_key = key_from_name(filename)
    sniffed = sniff_bytes(raw, name_key)
    if sniffed is not None:
        # A tar archive compressed with gzip and a lone .gz share the magic bytes:
        # only ".tar.gz" / ".tgz" names are archives we understand.
        if sniffed == "tgz" and name_key != "tgz":
            return None
        # ogg container: keep the name's opinion between ogg / opus.
        if sniffed in ("ogg", "opus") and name_key in ("ogg", "opus"):
            return name_key
        return sniffed
    if name_key in ("mp3", "m4a", "aac"):
        return name_key  # headerless / odd streams: trust the name
    if name_key is not None:
        return name_key
    mime_key = key_from_mime(content_type)
    if mime_key is not None:
        return mime_key
    return _sniff_text(raw, name_key)
