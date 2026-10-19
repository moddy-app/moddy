"""
File metadata extraction for ``/exif`` — pure, synchronous, no Discord.

``inspect_file(data, filename)`` sniffs the bytes (never trusts the name alone)
and returns a :class:`MetadataReport`: a flat list of :class:`Field` grouped by
meaning (camera, location, dates...), a raw dump of every tag found, the
decimal GPS position when there is one and a few flags (``location``,
``person``, ``c2pa``, ``ai``) that drive the privacy verdict of the card.

Supported:

* images (Pillow): EXIF, GPS, XMP, PNG text chunks (Stable Diffusion /
  ComfyUI parameters), JPEG comment, ICC profile, DPI; HEIC through the
  optional ``pillow-heif`` (imported lazily);
* PDF (``pypdf``);
* Office / OpenDocument (``zipfile`` + ``xml.etree``, no dependency);
* audio / video tags (``mutagen``), including the QuickTime ``©xyz`` GPS atom
  of phone videos.

Nothing here decodes pixels, so a decompression bomb costs nothing; every XML
document is refused when it declares a DTD / entities. All of it is blocking:
call it from ``asyncio.to_thread``. See docs/features/TOOLS_INSPECT.md.
"""

from __future__ import annotations

import io
import math
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

# --------------------------------------------------------------------------- #
# Vocabulary (the i18n keys of the card: tools.exif.groups.* / tools.exif.fields.*)
# --------------------------------------------------------------------------- #

GROUP_AUTHOR = "author"
GROUP_LOCATION = "location"
GROUP_CAMERA = "camera"
GROUP_CAPTURE = "capture"
GROUP_DATES = "dates"
GROUP_SOFTWARE = "software"
GROUP_DOCUMENT = "document"
GROUP_MEDIA = "media"
GROUP_IMAGE = "image"

#: Display order of the sections on the card.
GROUP_ORDER: Tuple[str, ...] = (
    GROUP_LOCATION, GROUP_AUTHOR, GROUP_CAMERA, GROUP_CAPTURE, GROUP_DATES,
    GROUP_SOFTWARE, GROUP_DOCUMENT, GROUP_MEDIA, GROUP_IMAGE,
)

FIELD_KEYS: Tuple[str, ...] = (
    # location
    "coordinates", "altitude", "place",
    # author
    "author", "last_modified_by", "company", "owner", "artist", "copyright",
    # camera + capture
    "make", "model", "lens", "serial", "exposure", "aperture", "iso",
    "focal_length", "focal_length_35",
    # dates
    "taken", "digitized", "modified", "created", "printed",
    # software
    "software", "creator_tool", "producer", "generator", "edit_history",
    "digital_source", "application", "revision", "edit_time",
    # document
    "title", "subject", "keywords", "description", "comment", "pages",
    "words", "encrypted", "version",
    # audio / video
    "album", "track", "year", "genre", "duration", "bitrate", "sample_rate",
    "channels", "encoder", "cover", "codec",
    # image
    "dimensions", "dpi", "orientation", "color_profile", "frames", "format",
)

#: Boolean values are stored as these tokens and translated by the card.
YES = "@yes"
NO = "@no"

FLAG_LOCATION = "location"
FLAG_PERSON = "person"
FLAG_C2PA = "c2pa"
FLAG_AI = "ai"

KIND_IMAGE = "image"
KIND_PDF = "pdf"
KIND_OFFICE = "office"
KIND_AUDIO = "audio"
KIND_VIDEO = "video"

MAX_VALUE_CHARS = 200
MAX_RAW_VALUE_CHARS = 600
MAX_RAW_LINES = 600
MAX_XML_BYTES = 1_000_000
XMP_SCAN_BYTES = 2_000_000


class MetadataError(Exception):
    """``code`` is ``unsupported`` (no reader for this file) or ``unreadable``."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class Field:
    group: str
    key: str
    value: str


@dataclass
class MetadataReport:
    kind: str
    format: str
    size: int
    fields: List[Field] = field(default_factory=list)
    raw: List[str] = field(default_factory=list)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[float] = None
    flags: set = field(default_factory=set)

    # -- building --------------------------------------------------------- #
    def add(self, group: str, key: str, value: Any, *, person: bool = False,
            location: bool = False) -> None:
        text = clean(value)
        if not text:
            return
        # The same fact often sits in EXIF and XMP: keep the first.
        if any(f.group == group and f.key == key for f in self.fields):
            return
        self.fields.append(Field(group, key, text))
        if person:
            self.flags.add(FLAG_PERSON)
        if location:
            self.flags.add(FLAG_LOCATION)

    def add_raw(self, section: str, name: str, value: Any) -> None:
        if len(self.raw) >= MAX_RAW_LINES:
            return
        text = clean(value, MAX_RAW_VALUE_CHARS)
        self.raw.append(f"[{section}] {name}: {text}" if name else f"[{section}] {text}")

    # -- reading ---------------------------------------------------------- #
    @property
    def has_location(self) -> bool:
        return FLAG_LOCATION in self.flags

    @property
    def has_person(self) -> bool:
        return FLAG_PERSON in self.flags

    @property
    def meaningful(self) -> bool:
        """True when anything beyond the bare image properties was found."""
        return any(f.group != GROUP_IMAGE for f in self.fields)

    def groups(self) -> List[Tuple[str, List[Field]]]:
        by_group: Dict[str, List[Field]] = {}
        for f in self.fields:
            by_group.setdefault(f.group, []).append(f)
        ordered = [(g, by_group[g]) for g in GROUP_ORDER if g in by_group]
        ordered += [(g, v) for g, v in by_group.items() if g not in GROUP_ORDER]
        return ordered

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

_WS = re.compile(r"\s+")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean(value: Any, limit: int = MAX_VALUE_CHARS) -> str:
    """One display-safe line out of whatever a tag holds."""
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        for enc in ("utf-8", "utf-16-le"):
            try:
                text = raw.decode(enc)
                if enc == "utf-16-le" and not _printable(text):
                    raise ValueError
                break
            except (UnicodeDecodeError, ValueError):
                continue
        else:
            text = raw.decode("latin-1", errors="replace")
    elif isinstance(value, (list, tuple)):
        text = ", ".join(clean(v, limit) for v in value if v is not None)
    else:
        text = str(value)
    text = _CONTROL.sub("", text.replace("\x00", ""))
    text = _WS.sub(" ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _printable(text: str) -> bool:
    return bool(text) and all(ch.isprintable() or ch.isspace() for ch in text)


def _num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return number if math.isfinite(number) else None


def _fmt_float(value: float, digits: int = 1) -> str:
    text = f"{value:.{digits}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


# --------------------------------------------------------------------------- #
# GPS
# --------------------------------------------------------------------------- #


def dms_to_decimal(dms: Sequence[Any], ref: Any) -> Optional[float]:
    """Degrees / minutes / seconds + hemisphere (``N S E W``) -> signed decimal."""
    try:
        parts = [_num(x) for x in list(dms)[:3]]
    except TypeError:
        return None
    if not parts or any(p is None for p in parts):
        return None
    while len(parts) < 3:
        parts.append(0.0)
    degrees, minutes, seconds = parts
    value = degrees + minutes / 60.0 + seconds / 3600.0
    if isinstance(ref, (bytes, bytearray)):
        ref = bytes(ref).decode("ascii", errors="ignore")
    if str(ref or "").strip().upper()[:1] in ("S", "W"):
        value = -value
    return round(value, 6)


def valid_position(lat: Optional[float], lon: Optional[float]) -> bool:
    """In range, and not the ``0, 0`` cameras write when they have no fix."""
    if lat is None or lon is None:
        return False
    if abs(lat) > 90 or abs(lon) > 180:
        return False
    return not (lat == 0 and lon == 0)


def format_coordinates(lat: float, lon: float) -> str:
    return f"{lat:.6f}, {lon:.6f}"


def osm_url(lat: float, lon: float) -> str:
    """OpenStreetMap centred on the point, with a marker."""
    return (f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}"
            f"#map=15/{lat:.6f}/{lon:.6f}")


_ISO6709 = re.compile(r"^([+-]\d{1,2}(?:\.\d+)?)([+-]\d{1,3}(?:\.\d+)?)([+-]\d+(?:\.\d+)?)?")


def parse_iso6709(text: str) -> Optional[Tuple[float, float, Optional[float]]]:
    """``+48.8584+002.2945+035.000/`` (QuickTime ``©xyz``) -> (lat, lon, alt)."""
    match = _ISO6709.match((text or "").strip())
    if not match:
        return None
    lat, lon = float(match.group(1)), float(match.group(2))
    alt = float(match.group(3)) if match.group(3) else None
    return lat, lon, alt


def _set_position(report: MetadataReport, lat: Optional[float], lon: Optional[float],
                  alt: Optional[float] = None) -> None:
    if not valid_position(lat, lon):
        return
    report.latitude, report.longitude = lat, lon
    report.add(GROUP_LOCATION, "coordinates", format_coordinates(lat, lon), location=True)
    if alt is not None and math.isfinite(alt):
        report.altitude = alt
        report.add(GROUP_LOCATION, "altitude", f"{_fmt_float(alt)} m")
        # altitude alone never matters, but keep it next to the position
    report.flags.add(FLAG_LOCATION)


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #


def format_exif_date(value: Any, offset: Any = None) -> str:
    """``2024:05:12 14:33:10`` -> ``2024-05-12 14:33:10`` (+ UTC offset when known).

    EXIF dates carry no time zone: they are shown as written, never converted
    (a Discord timestamp would lie by the reader's offset).
    """
    text = clean(value)
    match = re.match(r"^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})", text)
    if not match:
        return "" if not text or text.startswith("0000") else text
    year, month, day, hh, mm, ss = match.groups()
    if year == "0000" or month == "00" or day == "00":
        return ""
    out = f"{year}-{month}-{day} {hh}:{mm}:{ss}"
    off = clean(offset)
    if re.match(r"^[+-]\d{2}:\d{2}$", off):
        out += f" {off}"
    return out


def format_iso_date(value: Any) -> str:
    """``2024-05-12T14:33:10Z`` -> ``2024-05-12 14:33:10 UTC`` (other forms as is)."""
    text = clean(value)
    match = re.match(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?", text)
    if not match:
        return text
    day, time_, tz = match.groups()
    out = f"{day} {time_}"
    if tz == "Z":
        out += " UTC"
    elif tz:
        out += f" {tz}"
    return out


def _format_datetime(value: Optional[datetime]) -> str:
    if value is None:
        return ""
    out = value.strftime("%Y-%m-%d %H:%M:%S")
    if value.tzinfo is not None and value.utcoffset() is not None:
        offset = value.utcoffset()
        total = int(offset.total_seconds() // 60)
        sign = "+" if total >= 0 else "-"
        out += f" {sign}{abs(total) // 60:02d}:{abs(total) % 60:02d}"
    return out


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

_HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}


def sniff_image_format(data: bytes) -> Optional[str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if data[:3] == b"\xff\xd8\xff":
        return "JPEG"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "GIF"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "TIFF"
    if data[:2] == b"BM" and len(data) > 26:
        return "BMP"
    if data[4:8] == b"ftyp" and data[8:12] in _HEIC_BRANDS:
        return "HEIC"
    return None


def inspect_file(data: bytes, filename: str = "") -> MetadataReport:
    """Read every metadata the file carries. Raises :class:`MetadataError`."""
    if not data:
        raise MetadataError("unreadable")
    name = (filename or "").lower()

    if data[:1024].find(b"%PDF-") != -1:
        return _inspect_pdf(data)
    if data[:4] == b"PK\x03\x04":
        return _inspect_zip(data, name)
    image_format = sniff_image_format(data)
    if image_format:
        return _inspect_image(data, image_format)
    return _inspect_media(data, name)


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #

# Tag ids (Exif 2.32) — numeric so this module does not depend on the names
# Pillow happens to expose.
_T_MAKE, _T_MODEL, _T_SOFTWARE, _T_ARTIST = 0x010F, 0x0110, 0x0131, 0x013B
_T_DESCRIPTION, _T_COPYRIGHT, _T_MODIFIED, _T_HOST = 0x010E, 0x8298, 0x0132, 0x013C
_T_ORIENTATION = 0x0112
_T_EXPOSURE, _T_FNUMBER, _T_ISO, _T_ISO2 = 0x829A, 0x829D, 0x8827, 0x8833
_T_TAKEN, _T_DIGITIZED = 0x9003, 0x9004
_T_OFFSET_TAKEN, _T_OFFSET_DIGITIZED = 0x9011, 0x9012
_T_FOCAL, _T_FOCAL35, _T_USER_COMMENT = 0x920A, 0xA405, 0x9286
_T_OWNER, _T_BODY_SERIAL, _T_LENS = 0xA430, 0xA431, 0xA434
_T_XP = {0x9C9B: "XPTitle", 0x9C9C: "XPComment", 0x9C9D: "XPAuthor",
         0x9C9E: "XPKeywords", 0x9C9F: "XPSubject"}
_IFD_EXIF, _IFD_GPS = 0x8769, 0x8825

_ORIENTATION = {1: "0°", 2: "0° ↔", 3: "180°", 4: "180° ↕", 5: "90° ↔", 6: "90°",
                7: "270° ↔", 8: "270°"}

_heif_registered = False


def _register_heif() -> bool:
    """Teach Pillow HEIC, when ``pillow-heif`` is installed (optional, lazy)."""
    global _heif_registered
    if _heif_registered:
        return True
    try:
        import pillow_heif  # type: ignore
        pillow_heif.register_heif_opener()
    except Exception:  # noqa: BLE001 — optional dependency
        return False
    _heif_registered = True
    return True


def format_exposure(value: Any) -> str:
    number = _num(value)
    if number is None or number <= 0:
        return ""
    if number < 1:
        inverse = 1 / number
        if abs(inverse - round(inverse)) / inverse < 0.05:
            return f"1/{int(round(inverse))} s"
    return f"{_fmt_float(number, 2)} s"


def _exif_text(ifd: Dict[int, Any], tag: int) -> str:
    return clean(ifd.get(tag))


def _user_comment(value: Any) -> str:
    """``UserComment`` starts with an 8-byte charset marker."""
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        prefix, body = raw[:8], raw[8:]
        if prefix.startswith(b"UNICODE"):
            for enc in ("utf-16-be", "utf-16-le"):
                try:
                    return clean(body.decode(enc))
                except UnicodeDecodeError:
                    continue
        return clean(body if prefix[:5] in (b"ASCII", b"JIS\x00\x00") or prefix == b"\x00" * 8 else raw)
    return clean(value)


def _inspect_image(data: bytes, image_format: str) -> MetadataReport:
    from PIL import Image
    from PIL.ExifTags import GPSTAGS, TAGS

    if image_format == "HEIC" and not _register_heif():
        raise MetadataError("unsupported")

    try:
        img = Image.open(io.BytesIO(data))
        width, height = img.size
    except Exception as exc:  # noqa: BLE001 — Pillow raises many types
        raise MetadataError("unreadable") from exc

    report = MetadataReport(kind=KIND_IMAGE, format=img.format or image_format, size=len(data))

    # -- file properties (always there, never "personal") ------------------ #
    report.add(GROUP_IMAGE, "dimensions", f"{width} × {height}")
    dpi = img.info.get("dpi")
    if isinstance(dpi, (tuple, list)) and dpi and _num(dpi[0]):
        report.add(GROUP_IMAGE, "dpi", _fmt_float(_num(dpi[0]), 0))
    frames = getattr(img, "n_frames", 1)
    if isinstance(frames, int) and frames > 1:
        report.add(GROUP_IMAGE, "frames", frames)
    icc = img.info.get("icc_profile")
    if icc:
        report.add(GROUP_IMAGE, "color_profile", _icc_name(icc))
        report.add_raw("Image", "icc_profile", f"{len(icc)} bytes")

    # -- EXIF -------------------------------------------------------------- #
    try:
        exif = img.getexif()
    except Exception:  # noqa: BLE001 — a corrupt block must not hide the rest
        exif = None
    if exif:
        _read_exif(report, exif, TAGS, GPSTAGS)

    # -- JPEG comment ------------------------------------------------------ #
    comment = img.info.get("comment")
    if comment:
        report.add(GROUP_DOCUMENT, "comment", comment)
        report.add_raw("JPEG", "comment", comment)

    # -- PNG text chunks + AI generation parameters ------------------------- #
    if image_format == "PNG":
        _read_png_text(report, data)

    # -- XMP ---------------------------------------------------------------- #
    _read_xmp(report, data)

    # -- Content Credentials ------------------------------------------------ #
    head = data[:XMP_SCAN_BYTES]
    if b"c2pa" in head and b"jumb" in head:
        report.flags.add(FLAG_C2PA)
    return report


def _icc_name(icc: bytes) -> str:
    try:
        from PIL import ImageCms
        profile = ImageCms.getOpenProfile(io.BytesIO(icc))
        return clean(ImageCms.getProfileDescription(profile))
    except Exception:  # noqa: BLE001 — name is a nicety
        return ""


def _read_exif(report: MetadataReport, exif, TAGS, GPSTAGS) -> None:
    def ifd(tag: int) -> Dict[int, Any]:
        try:
            return dict(exif.get_ifd(tag))
        except Exception:  # noqa: BLE001
            return {}

    base = dict(exif)
    exif_ifd = ifd(_IFD_EXIF)
    gps = ifd(_IFD_GPS)
    merged = {**base, **exif_ifd}

    for section, entries, names in (("EXIF", merged, TAGS), ("GPS", gps, GPSTAGS)):
        for tag, value in sorted(entries.items(), key=lambda kv: str(kv[0])):
            if tag in (_IFD_EXIF, _IFD_GPS, 0xA005):  # pointers to sub-IFDs
                continue
            name = names.get(tag, f"0x{tag:04X}" if isinstance(tag, int) else str(tag))
            if isinstance(value, (bytes, bytearray)) and len(value) > 64 and tag not in _XP_AND_COMMENT:
                report.add_raw(section, name, f"{len(value)} bytes")
            else:
                report.add_raw(section, name, _raw_exif_value(tag, value))

    # Camera
    make = _exif_text(merged, _T_MAKE)
    model = _exif_text(merged, _T_MODEL)
    report.add(GROUP_CAMERA, "make", make)
    report.add(GROUP_CAMERA, "model", model)
    report.add(GROUP_CAMERA, "lens", _exif_text(merged, _T_LENS))
    report.add(GROUP_CAMERA, "serial", _exif_text(merged, _T_BODY_SERIAL))

    # Capture settings
    report.add(GROUP_CAPTURE, "exposure", format_exposure(merged.get(_T_EXPOSURE)))
    fnumber = _num(merged.get(_T_FNUMBER))
    if fnumber:
        report.add(GROUP_CAPTURE, "aperture", f"f/{_fmt_float(fnumber)}")
    iso = merged.get(_T_ISO, merged.get(_T_ISO2))
    if isinstance(iso, (tuple, list)):
        iso = iso[0] if iso else None
    if _num(iso):
        report.add(GROUP_CAPTURE, "iso", int(_num(iso)))
    focal = _num(merged.get(_T_FOCAL))
    if focal:
        report.add(GROUP_CAPTURE, "focal_length", f"{_fmt_float(focal)} mm")
    focal35 = _num(merged.get(_T_FOCAL35))
    if focal35:
        report.add(GROUP_CAPTURE, "focal_length_35", f"{_fmt_float(focal35, 0)} mm")
    orientation = merged.get(_T_ORIENTATION)
    if isinstance(orientation, int) and orientation in _ORIENTATION and orientation != 1:
        report.add(GROUP_IMAGE, "orientation", _ORIENTATION[orientation])

    # Dates
    report.add(GROUP_DATES, "taken",
               format_exif_date(merged.get(_T_TAKEN), merged.get(_T_OFFSET_TAKEN)))
    report.add(GROUP_DATES, "digitized",
               format_exif_date(merged.get(_T_DIGITIZED), merged.get(_T_OFFSET_DIGITIZED)))
    report.add(GROUP_DATES, "modified", format_exif_date(merged.get(_T_MODIFIED)))

    # Software
    report.add(GROUP_SOFTWARE, "software", _exif_text(merged, _T_SOFTWARE))
    report.add(GROUP_SOFTWARE, "producer", _exif_text(merged, _T_HOST))

    # People
    report.add(GROUP_AUTHOR, "artist", _exif_text(merged, _T_ARTIST), person=True)
    report.add(GROUP_AUTHOR, "owner", _exif_text(merged, _T_OWNER), person=True)
    report.add(GROUP_AUTHOR, "copyright", _exif_text(merged, _T_COPYRIGHT))
    for tag, label in _XP_TARGET.items():
        value = merged.get(tag)
        if value:
            text = _xp_text(value)
            if label == "author":
                report.add(GROUP_AUTHOR, "author", text, person=True)
            elif label == "title":
                report.add(GROUP_DOCUMENT, "title", text)
            elif label == "comment":
                report.add(GROUP_DOCUMENT, "comment", text)
            elif label == "keywords":
                report.add(GROUP_DOCUMENT, "keywords", text)
            elif label == "subject":
                report.add(GROUP_DOCUMENT, "subject", text)

    # Text
    report.add(GROUP_DOCUMENT, "description", _exif_text(merged, _T_DESCRIPTION))
    comment = _user_comment(merged.get(_T_USER_COMMENT))
    if comment:
        report.add(GROUP_DOCUMENT, "comment", comment)

    # GPS
    lat = dms_to_decimal(gps.get(2), gps.get(1)) if gps.get(2) is not None else None
    lon = dms_to_decimal(gps.get(4), gps.get(3)) if gps.get(4) is not None else None
    altitude = _num(gps.get(6))
    if altitude is not None:
        ref = gps.get(5)
        if ref in (1, b"\x01"):
            altitude = -altitude
    _set_position(report, lat, lon, altitude)


_XP_TARGET = {0x9C9B: "title", 0x9C9C: "comment", 0x9C9D: "author",
              0x9C9E: "keywords", 0x9C9F: "subject"}
_XP_AND_COMMENT = set(_XP_TARGET) | {_T_USER_COMMENT}


def _xp_text(value: Any) -> str:
    """Windows ``XP*`` tags are UTF-16LE, as bytes or as a tuple of ints."""
    if isinstance(value, (tuple, list)):
        try:
            value = bytes(int(v) & 0xFF for v in value)
        except (TypeError, ValueError):
            return clean(value)
    if isinstance(value, (bytes, bytearray)):
        try:
            return clean(bytes(value).decode("utf-16-le").rstrip("\x00"))
        except UnicodeDecodeError:
            return clean(value)
    return clean(value)


def _raw_exif_value(tag: Any, value: Any) -> str:
    if tag in _XP_TARGET:
        return _xp_text(value)
    if tag == _T_USER_COMMENT:
        return _user_comment(value)
    if isinstance(value, (tuple, list)):
        return ", ".join(clean(_rational(v), 60) for v in value[:12])
    return clean(_rational(value), MAX_RAW_VALUE_CHARS)


def _rational(value: Any) -> Any:
    if type(value).__name__ == "IFDRational":
        number = _num(value)
        return _fmt_float(number, 4) if number is not None else "n/a"
    return value


# -- PNG text / AI generation -------------------------------------------------


def _read_png_text(report: MetadataReport, data: bytes) -> None:
    from aidetector.png_text import detect_generation_metadata, iter_text_chunks

    mapping = {
        "author": (GROUP_AUTHOR, "author", True),
        "software": (GROUP_SOFTWARE, "software", False),
        "creation time": (GROUP_DATES, "created", False),
        "title": (GROUP_DOCUMENT, "title", False),
        "description": (GROUP_DOCUMENT, "description", False),
        "comment": (GROUP_DOCUMENT, "comment", False),
        "copyright": (GROUP_AUTHOR, "copyright", False),
    }
    for keyword, text in iter_text_chunks(data):
        report.add_raw("PNG", keyword, text)
        if keyword.lower() == "xml:com.adobe.xmp":
            continue  # read below with the other XMP packets
        target = mapping.get(keyword.strip().lower())
        if target:
            group, key, person = target
            value = format_iso_date(text) if key == "created" else text
            report.add(group, key, value, person=person)
    generation = detect_generation_metadata(data)
    if generation is not None:
        report.flags.add(FLAG_AI)
        report.add(GROUP_SOFTWARE, "generator", generation.tool)


# -- XMP --------------------------------------------------------------------


def _safe_xml(raw: bytes) -> Optional[ET.Element]:
    """Parse a small XML document, refusing DTDs / entities (expansion bombs)."""
    if len(raw) > MAX_XML_BYTES:
        return None
    lowered = raw[:4096].lower()
    if b"<!doctype" in lowered or b"<!entity" in raw.lower():
        return None
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _read_xmp(report: MetadataReport, data: bytes) -> None:
    head = data[:XMP_SCAN_BYTES]
    start = head.find(b"<x:xmpmeta")
    if start < 0:
        start = head.find(b"<?xpacket begin")
    if start < 0:
        return
    end = head.find(b"</x:xmpmeta>", start)
    if end < 0:
        return
    packet = head[head.find(b"<x:xmpmeta", start):end + len(b"</x:xmpmeta>")]
    root = _safe_xml(packet)
    if root is None:
        return

    creators: List[str] = []
    agents: List[str] = []
    places: List[str] = []
    digital_source = ""
    tool = ""
    lines = 0

    def note(name: str, value: str) -> None:
        nonlocal lines
        if value and lines < 200:
            lines += 1
            report.add_raw("XMP", name, value)

    for element in root.iter():
        name = _local(element.tag)
        # Attributes of rdf:Description carry most simple properties.
        for attr, value in element.attrib.items():
            local = _local(attr)
            value = clean(value)
            if not value:
                continue
            if local in ("about", "parseType", "xmptk"):
                continue
            note(local, value)
            if local == "CreatorTool":
                tool = tool or value
            elif local == "softwareAgent":
                agents.append(value)
            elif local in ("City", "State", "Country", "Location", "Sublocation"):
                places.append(value)
            elif local == "DigitalSourceType":
                digital_source = digital_source or value
        text = clean(element.text)
        if not text or name in ("li", "Seq", "Bag", "Alt", "Description"):
            continue
        if name == "CreatorTool":
            tool = tool or text
        elif name == "DigitalSourceType":
            digital_source = digital_source or text
        elif name in ("City", "State", "Country", "Location", "Sublocation"):
            places.append(text)
        elif name == "softwareAgent":
            agents.append(text)
        note(name, text)

    # dc:creator is a Seq of li.
    for element in root.iter():
        if _local(element.tag) == "creator":
            for li in element.iter():
                if _local(li.tag) == "li" and clean(li.text):
                    creators.append(clean(li.text))
    for creator in creators[:3]:
        note("creator", creator)

    report.add(GROUP_SOFTWARE, "creator_tool", tool)
    unique_agents = list(dict.fromkeys(agents))[:4]
    if unique_agents:
        report.add(GROUP_SOFTWARE, "edit_history", ", ".join(unique_agents))
    for creator in creators[:2]:
        report.add(GROUP_AUTHOR, "author", creator, person=True)
    if places:
        report.add(GROUP_LOCATION, "place", ", ".join(dict.fromkeys(places)), location=True)
    if digital_source:
        label = digital_source.rsplit("/", 1)[-1]
        report.add(GROUP_SOFTWARE, "digital_source", label)
        if "algorithmicmedia" in label.lower():
            report.flags.add(FLAG_AI)


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #


def _inspect_pdf(data: bytes) -> MetadataReport:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover — declared in requirements
        raise MetadataError("unsupported") from exc

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
    except Exception as exc:  # noqa: BLE001
        raise MetadataError("unreadable") from exc

    report = MetadataReport(kind=KIND_PDF, format="PDF", size=len(data))
    encrypted = bool(getattr(reader, "is_encrypted", False))
    if encrypted:
        report.add(GROUP_DOCUMENT, "encrypted", YES)
        try:
            reader.decrypt("")  # many PDFs only lock editing, not opening
        except Exception:  # noqa: BLE001 — wrong password / unsupported cipher
            pass
    try:
        report.add(GROUP_DOCUMENT, "pages", len(reader.pages))
    except Exception:  # noqa: BLE001
        pass
    header = clean(getattr(reader, "pdf_header", ""))
    if header:
        report.add(GROUP_DOCUMENT, "version", header.replace("%PDF-", ""))

    try:
        meta = reader.metadata
    except Exception:  # noqa: BLE001
        meta = None
    if meta:
        report.add(GROUP_DOCUMENT, "title", meta.get("/Title"))
        report.add(GROUP_DOCUMENT, "subject", meta.get("/Subject"))
        report.add(GROUP_DOCUMENT, "keywords", meta.get("/Keywords"))
        report.add(GROUP_AUTHOR, "author", meta.get("/Author"), person=True)
        report.add(GROUP_SOFTWARE, "creator_tool", meta.get("/Creator"))
        report.add(GROUP_SOFTWARE, "producer", meta.get("/Producer"))
        for key, attr, label in (("/CreationDate", "creation_date", "created"),
                                 ("/ModDate", "modification_date", "modified")):
            try:
                parsed = getattr(meta, attr)
            except Exception:  # noqa: BLE001 — pypdf raises on odd date forms
                parsed = None
            shown = _format_datetime(parsed) or clean(meta.get(key))
            report.add(GROUP_DATES, label, shown)
        for key in list(meta.keys())[:60]:
            report.add_raw("PDF", str(key).lstrip("/"), meta.get(key))
    return report


# --------------------------------------------------------------------------- #
# Office / OpenDocument
# --------------------------------------------------------------------------- #

_OFFICE_EXT = {".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm", ".dotx", ".xltx", ".potx",
               ".odt", ".ods", ".odp", ".odg", ".odf"}


def _zip_member(zf: zipfile.ZipFile, name: str) -> Optional[bytes]:
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_XML_BYTES:
        return None
    with zf.open(info) as handle:
        raw = handle.read(MAX_XML_BYTES + 1)
    return raw if len(raw) <= MAX_XML_BYTES else None


def _xml_values(raw: Optional[bytes]) -> Dict[str, str]:
    """``{local tag name: text}`` of a flat properties document."""
    if raw is None:
        return {}
    root = _safe_xml(raw)
    if root is None:
        return {}
    values: Dict[str, str] = {}
    for element in root.iter():
        name = _local(element.tag)
        text = clean(element.text)
        if text and name not in values:
            values[name] = text
        for attr, value in element.attrib.items():
            local = _local(attr)
            if clean(value) and local not in values:
                values[f"{name}.{local}"] = clean(value)
    return values


def _iso_duration_minutes(text: str) -> str:
    match = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$", text or "")
    if not match:
        return clean(text)
    hours, minutes, seconds = (float(x) if x else 0.0 for x in match.groups())
    total = int(hours * 60 + minutes + (1 if seconds >= 30 else 0))
    return f"{total} min"


def _inspect_zip(data: bytes, name: str) -> MetadataReport:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        names = set(zf.namelist())
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        raise MetadataError("unreadable") from exc
    with zf:
        if "meta.xml" in names and "mimetype" in names:
            return _inspect_odf(zf, name)
        if "docProps/core.xml" in names or "docProps/app.xml" in names \
                or "[Content_Types].xml" in names:
            return _inspect_ooxml(zf, name)
    raise MetadataError("unsupported")


def _extension_label(name: str, default: str) -> str:
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    return ext[1:].upper() if ext in _OFFICE_EXT else default


def _inspect_ooxml(zf: zipfile.ZipFile, name: str) -> MetadataReport:
    report = MetadataReport(kind=KIND_OFFICE, format=_extension_label(name, "OOXML"),
                            size=sum(i.compress_size for i in zf.infolist()))
    core = _xml_values(_zip_member(zf, "docProps/core.xml"))
    app = _xml_values(_zip_member(zf, "docProps/app.xml"))

    report.add(GROUP_AUTHOR, "author", core.get("creator"), person=True)
    report.add(GROUP_AUTHOR, "last_modified_by", core.get("lastModifiedBy"), person=True)
    report.add(GROUP_AUTHOR, "company", app.get("Company"), person=True)
    report.add(GROUP_AUTHOR, "owner", app.get("Manager"), person=True)
    report.add(GROUP_DOCUMENT, "title", core.get("title"))
    report.add(GROUP_DOCUMENT, "subject", core.get("subject"))
    report.add(GROUP_DOCUMENT, "keywords", core.get("keywords"))
    report.add(GROUP_DOCUMENT, "description", core.get("description"))
    report.add(GROUP_DOCUMENT, "pages", app.get("Pages") or app.get("Slides"))
    report.add(GROUP_DOCUMENT, "words", app.get("Words"))
    report.add(GROUP_DATES, "created", format_iso_date(core.get("created")))
    report.add(GROUP_DATES, "modified", format_iso_date(core.get("modified")))
    report.add(GROUP_DATES, "printed", format_iso_date(core.get("lastPrinted")))
    application = app.get("Application")
    if application and app.get("AppVersion"):
        application = f"{application} {app['AppVersion']}"
    report.add(GROUP_SOFTWARE, "application", application)
    report.add(GROUP_SOFTWARE, "revision", core.get("revision"))
    total = app.get("TotalTime")
    if total and total.isdigit():
        report.add(GROUP_SOFTWARE, "edit_time", f"{int(total)} min")
    for source, values in (("core", core), ("app", app)):
        for key, value in list(values.items())[:60]:
            report.add_raw(source, key, value)
    return report


def _inspect_odf(zf: zipfile.ZipFile, name: str) -> MetadataReport:
    report = MetadataReport(kind=KIND_OFFICE, format=_extension_label(name, "ODF"),
                            size=sum(i.compress_size for i in zf.infolist()))
    meta = _xml_values(_zip_member(zf, "meta.xml"))
    report.add(GROUP_AUTHOR, "author", meta.get("initial-creator"), person=True)
    report.add(GROUP_AUTHOR, "last_modified_by", meta.get("creator"), person=True)
    report.add(GROUP_DOCUMENT, "title", meta.get("title"))
    report.add(GROUP_DOCUMENT, "subject", meta.get("subject"))
    report.add(GROUP_DOCUMENT, "keywords", meta.get("keyword"))
    report.add(GROUP_DOCUMENT, "description", meta.get("description"))
    report.add(GROUP_DOCUMENT, "pages", meta.get("document-statistic.page-count"))
    report.add(GROUP_DOCUMENT, "words", meta.get("document-statistic.word-count"))
    report.add(GROUP_DATES, "created", format_iso_date(meta.get("creation-date")))
    report.add(GROUP_DATES, "modified", format_iso_date(meta.get("date")))
    report.add(GROUP_DATES, "printed", format_iso_date(meta.get("print-date")))
    report.add(GROUP_SOFTWARE, "application", meta.get("generator"))
    report.add(GROUP_SOFTWARE, "revision", meta.get("editing-cycles"))
    if meta.get("editing-duration"):
        report.add(GROUP_SOFTWARE, "edit_time", _iso_duration_minutes(meta["editing-duration"]))
    for key, value in list(meta.items())[:60]:
        report.add_raw("meta", key, value)
    return report


# --------------------------------------------------------------------------- #
# Audio / video (mutagen)
# --------------------------------------------------------------------------- #

_VIDEO_EXT = {".mp4", ".m4v", ".mov", ".3gp", ".mkv", ".webm", ".avi"}


def _first(tags: Any, *keys: str) -> Any:
    for key in keys:
        try:
            value = tags.get(key)
        except Exception:  # noqa: BLE001
            value = None
        if value is None:
            continue
        if isinstance(value, (list, tuple)) and value:
            value = value[0]
        if hasattr(value, "text") and getattr(value, "text", None):
            value = value.text[0] if isinstance(value.text, list) else value.text
        if value not in (None, "", b""):
            return value
    return None


def _inspect_media(data: bytes, name: str) -> MetadataReport:
    try:
        import mutagen
    except ImportError as exc:  # pragma: no cover — declared in requirements
        raise MetadataError("unsupported") from exc
    try:
        audio = mutagen.File(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 — mutagen raises its own tree
        raise MetadataError("unreadable") from exc
    if audio is None:
        raise MetadataError("unsupported")

    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    mime = (getattr(audio, "mime", None) or [""])[0]
    is_video = ext in _VIDEO_EXT or mime.startswith("video/")
    cls = type(audio).__name__
    report = MetadataReport(kind=KIND_VIDEO if is_video else KIND_AUDIO,
                            format=(ext[1:].upper() if ext else cls), size=len(data))
    tags = audio.tags

    info = audio.info
    length = _num(getattr(info, "length", None))
    if length:
        report.add(GROUP_MEDIA, "duration", format_duration(length))
    bitrate = _num(getattr(info, "bitrate", None))
    if bitrate:
        report.add(GROUP_MEDIA, "bitrate", f"{int(round(bitrate / 1000))} kbps")
    rate = _num(getattr(info, "sample_rate", None))
    if rate:
        report.add(GROUP_MEDIA, "sample_rate", f"{_fmt_float(rate / 1000)} kHz")
    channels = getattr(info, "channels", None)
    if isinstance(channels, int) and channels:
        report.add(GROUP_MEDIA, "channels", channels)
    codec = getattr(info, "codec", None) or getattr(info, "codec_description", None)
    report.add(GROUP_MEDIA, "codec", codec)

    if tags is None:
        return report

    for line in _pprint_lines(tags):
        report.add_raw("tags", "", line)

    if cls.startswith("MP4"):
        _read_mp4_tags(report, tags)
    elif hasattr(tags, "getall"):  # ID3
        _read_id3_tags(report, tags)
    else:  # Vorbis comments (FLAC, Ogg, Opus...) and the rest
        _read_vorbis_tags(report, tags, audio)
    return report


def _pprint_lines(tags: Any) -> List[str]:
    try:
        text = tags.pprint()
    except Exception:  # noqa: BLE001
        return []
    return [clean(line, MAX_RAW_VALUE_CHARS) for line in str(text).splitlines()[:200] if line.strip()]


def _read_id3_tags(report: MetadataReport, tags: Any) -> None:
    def text(frame: str) -> Any:
        found = tags.getall(frame)
        return found[0].text[0] if found and getattr(found[0], "text", None) else None

    report.add(GROUP_DOCUMENT, "title", text("TIT2"))
    report.add(GROUP_AUTHOR, "artist", text("TPE1"))
    report.add(GROUP_MEDIA, "album", text("TALB"))
    date = text("TDRC") or text("TYER")
    report.add(GROUP_MEDIA, "year", str(date)[:10] if date else None)
    report.add(GROUP_MEDIA, "genre", text("TCON"))
    report.add(GROUP_MEDIA, "track", text("TRCK"))
    report.add(GROUP_SOFTWARE, "encoder", text("TSSE") or text("TENC"))
    report.add(GROUP_MEDIA, "cover", YES if tags.getall("APIC") else NO)


def _read_vorbis_tags(report: MetadataReport, tags: Any, audio: Any) -> None:
    report.add(GROUP_DOCUMENT, "title", _first(tags, "title", "TITLE"))
    report.add(GROUP_AUTHOR, "artist", _first(tags, "artist", "ARTIST"))
    report.add(GROUP_MEDIA, "album", _first(tags, "album", "ALBUM"))
    report.add(GROUP_MEDIA, "year", str(_first(tags, "date", "DATE", "year") or "")[:10])
    report.add(GROUP_MEDIA, "genre", _first(tags, "genre", "GENRE"))
    report.add(GROUP_MEDIA, "track", _first(tags, "tracknumber", "TRACKNUMBER"))
    report.add(GROUP_SOFTWARE, "encoder", _first(tags, "encoder", "ENCODER", "encodedby"))
    has_cover = bool(getattr(audio, "pictures", None)) or "metadata_block_picture" in tags \
        or "METADATA_BLOCK_PICTURE" in tags
    report.add(GROUP_MEDIA, "cover", YES if has_cover else NO)


def _read_mp4_tags(report: MetadataReport, tags: Any) -> None:
    report.add(GROUP_DOCUMENT, "title", _first(tags, "©nam"))
    report.add(GROUP_AUTHOR, "artist", _first(tags, "©ART"))
    report.add(GROUP_MEDIA, "album", _first(tags, "©alb"))
    report.add(GROUP_MEDIA, "year", str(_first(tags, "©day") or "")[:10])
    report.add(GROUP_MEDIA, "genre", _first(tags, "©gen"))
    report.add(GROUP_SOFTWARE, "encoder", _first(tags, "©too"))
    report.add(GROUP_DOCUMENT, "comment", _first(tags, "©cmt"))
    report.add(GROUP_MEDIA, "cover", YES if tags.get("covr") else NO)
    # Phone videos store the GPS fix as an ISO 6709 string in ©xyz.
    xyz = _first(tags, "©xyz")
    parsed = parse_iso6709(clean(xyz)) if xyz else None
    if parsed:
        _set_position(report, parsed[0], parsed[1], parsed[2])


# --------------------------------------------------------------------------- #
# Raw dump
# --------------------------------------------------------------------------- #


def raw_dump(report: MetadataReport, filename: str = "") -> str:
    """Every tag found, as the text of the ``.txt`` attached by "All tags"."""
    header = [f"File: {clean(filename) or '-'}", f"Type: {report.format} ({report.kind})",
              f"Size: {report.size} bytes", ""]
    if report.has_coordinates:
        header.append(f"GPS: {format_coordinates(report.latitude, report.longitude)}")
        header.append("")
    body: Iterable[str] = report.raw or ["(no tag)"]
    return "\n".join(header + list(body)) + "\n"
