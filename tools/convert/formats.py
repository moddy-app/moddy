"""Single source of truth of /convert (and of the format Select of /image).

Everything the rest of the code and the tests need to know about formats lives
here: the format table, the conversion matrix (which source can become which
target, and which engine does it), the size/time caps. The Select options, the
direct ``/convert file format:`` check, the dispatcher and the tests all read
this module, so adding a format is a change in one place.

Pure Python, no heavy import: optional libraries are probed lazily by
:func:`capabilities`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Caps (size / time). One place, used by the converters and shown in the doc.
# --------------------------------------------------------------------------- #

MAX_INPUT_BYTES = 25 * 1024 * 1024          # any input file
MAX_TEXT_BYTES = 5 * 1024 * 1024            # data / markup / text inputs
MAX_SHEET_CELLS = 500_000                   # rows x columns read from a spreadsheet
MAX_ARCHIVE_FILES = 1_000                   # entries in an archive
MAX_ARCHIVE_UNCOMPRESSED = 100 * 1024 * 1024  # total bytes once extracted (zip-bomb guard)
MAX_ARCHIVE_RATIO = 1_000                   # per entry: uncompressed / compressed
MAX_PDF_RENDER_PAGES = 10                   # PDF -> PNG/JPG renders at most this many pages
MAX_PDF_TEXT_PAGES = 300                    # PDF -> TXT reads at most this many pages
MAX_PDF_RENDER_SIDE = 2000                  # longest side of a rendered page, in px
MAX_AUDIO_SECONDS = 10 * 60
MAX_VIDEO_SECONDS = 60
MAX_GIF_SECONDS = 20                        # video -> GIF (GIFs grow very fast)
FFMPEG_TIMEOUT = 180                        # seconds, per conversion
FFMPEG_THREADS = 2
MAX_SELECT_OPTIONS = 25                     # a Discord Select holds 25 options

# --------------------------------------------------------------------------- #
# Format table
# --------------------------------------------------------------------------- #

IMAGE, DATA, SHEET, DOCUMENT, EBOOK, ARCHIVE, AUDIO, VIDEO = (
    "image", "data", "sheet", "document", "ebook", "archive", "audio", "video")
GROUPS: Tuple[str, ...] = (IMAGE, DATA, SHEET, DOCUMENT, EBOOK, ARCHIVE, AUDIO, VIDEO)

# Engines (who performs a conversion).
E_IMAGE, E_DATA, E_SHEET, E_DOC, E_PDF, E_ARCHIVE, E_FFMPEG, E_CLOUD = (
    "image", "data", "sheet", "doc", "pdf", "archive", "ffmpeg", "cloud")
LOCAL_ENGINES: Tuple[str, ...] = (E_IMAGE, E_DATA, E_SHEET, E_DOC, E_PDF, E_ARCHIVE, E_FFMPEG)


@dataclass(frozen=True)
class Fmt:
    key: str                       # stable id, also the i18n / custom_id value
    label: str                     # what the Select shows
    group: str
    ext: str                       # extension of the produced file (no dot)
    mime: str
    aliases: Tuple[str, ...] = ()  # other extensions that mean the same format


FORMATS: Dict[str, Fmt] = {f.key: f for f in (
    # Images
    Fmt("png", "PNG", IMAGE, "png", "image/png"),
    Fmt("jpg", "JPEG", IMAGE, "jpg", "image/jpeg", ("jpeg", "jpe", "jfif")),
    Fmt("webp", "WebP", IMAGE, "webp", "image/webp"),
    Fmt("gif", "GIF", IMAGE, "gif", "image/gif"),
    Fmt("bmp", "BMP", IMAGE, "bmp", "image/bmp"),
    Fmt("tiff", "TIFF", IMAGE, "tiff", "image/tiff", ("tif",)),
    Fmt("ico", "ICO", IMAGE, "ico", "image/x-icon"),
    Fmt("avif", "AVIF", IMAGE, "avif", "image/avif"),
    Fmt("heic", "HEIC", IMAGE, "heic", "image/heic", ("heif", "hif")),
    # Structured data
    Fmt("json", "JSON", DATA, "json", "application/json"),
    Fmt("yaml", "YAML", DATA, "yaml", "application/yaml", ("yml",)),
    Fmt("toml", "TOML", DATA, "toml", "application/toml"),
    Fmt("xml", "XML", DATA, "xml", "application/xml"),
    Fmt("csv", "CSV", SHEET, "csv", "text/csv"),
    # Spreadsheets
    Fmt("xlsx", "XLSX", SHEET, "xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    Fmt("ods", "ODS", SHEET, "ods", "application/vnd.oasis.opendocument.spreadsheet"),
    # Documents
    Fmt("md", "Markdown", DOCUMENT, "md", "text/markdown", ("markdown", "mdown")),
    Fmt("html", "HTML", DOCUMENT, "html", "text/html", ("htm",)),
    Fmt("txt", "TXT", DOCUMENT, "txt", "text/plain", ("text", "log")),
    Fmt("docx", "DOCX", DOCUMENT, "docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    Fmt("odt", "ODT", DOCUMENT, "odt", "application/vnd.oasis.opendocument.text"),
    Fmt("pptx", "PPTX", DOCUMENT, "pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    Fmt("pdf", "PDF", DOCUMENT, "pdf", "application/pdf"),
    # Ebooks
    Fmt("epub", "EPUB", EBOOK, "epub", "application/epub+zip"),
    Fmt("mobi", "MOBI", EBOOK, "mobi", "application/x-mobipocket-ebook"),
    Fmt("azw3", "AZW3", EBOOK, "azw3", "application/vnd.amazon.ebook"),
    # Archives
    Fmt("zip", "ZIP", ARCHIVE, "zip", "application/zip"),
    Fmt("tgz", "TAR.GZ", ARCHIVE, "tar.gz", "application/gzip", ("tar.gz",)),
    Fmt("7z", "7Z", ARCHIVE, "7z", "application/x-7z-compressed"),
    # Audio
    Fmt("mp3", "MP3", AUDIO, "mp3", "audio/mpeg"),
    Fmt("ogg", "OGG", AUDIO, "ogg", "audio/ogg", ("oga",)),
    Fmt("opus", "Opus", AUDIO, "opus", "audio/opus"),
    Fmt("wav", "WAV", AUDIO, "wav", "audio/wav", ("wave",)),
    Fmt("flac", "FLAC", AUDIO, "flac", "audio/flac"),
    Fmt("m4a", "M4A", AUDIO, "m4a", "audio/mp4"),
    Fmt("aac", "AAC", AUDIO, "aac", "audio/aac"),
    # Video
    Fmt("mp4", "MP4", VIDEO, "mp4", "video/mp4", ("m4v",)),
    Fmt("webm", "WebM", VIDEO, "webm", "video/webm"),
    Fmt("mov", "MOV", VIDEO, "mov", "video/quicktime"),
    Fmt("mkv", "MKV", VIDEO, "mkv", "video/x-matroska"),
)}

IMAGE_KEYS: Tuple[str, ...] = tuple(k for k, f in FORMATS.items() if f.group == IMAGE)
AUDIO_KEYS: Tuple[str, ...] = tuple(k for k, f in FORMATS.items() if f.group == AUDIO)
VIDEO_KEYS: Tuple[str, ...] = tuple(k for k, f in FORMATS.items() if f.group == VIDEO)
DATA_KEYS: Tuple[str, ...] = ("json", "yaml", "toml", "xml", "csv")
SHEET_KEYS: Tuple[str, ...] = ("csv", "xlsx", "ods")
ARCHIVE_KEYS: Tuple[str, ...] = ("zip", "tgz", "7z")

#: Image formats Discord can render inline in a card (the others get a PNG preview).
PREVIEWABLE_IMAGE_KEYS: Tuple[str, ...] = ("png", "jpg", "webp", "gif")

#: Extension -> format key (aliases included; ``tar.gz`` is handled by detect).
EXTENSION_TO_KEY: Dict[str, str] = {}
for _fmt in FORMATS.values():
    EXTENSION_TO_KEY[_fmt.ext] = _fmt.key
    for _alias in _fmt.aliases:
        EXTENSION_TO_KEY[_alias] = _fmt.key
EXTENSION_TO_KEY["tgz"] = "tgz"

MIME_TO_KEY: Dict[str, str] = {f.mime: f.key for f in FORMATS.values()}
MIME_TO_KEY.update({
    "image/jpg": "jpg", "image/x-ms-bmp": "bmp", "image/vnd.microsoft.icon": "ico",
    "image/heif": "heic", "application/x-yaml": "yaml", "text/yaml": "yaml",
    "text/x-markdown": "md", "text/xml": "xml", "audio/x-wav": "wav", "audio/mp3": "mp3",
    "audio/x-flac": "flac", "audio/x-m4a": "m4a", "audio/vorbis": "ogg",
    "application/x-gzip": "tgz", "application/x-tar": "tgz", "application/json5": "json",
    "application/x-zip-compressed": "zip", "text/x-csv": "csv", "application/csv": "csv",
})


def label_of(key: str) -> str:
    fmt = FORMATS.get(key)
    return fmt.label if fmt else key.upper()


def group_of(key: str) -> Optional[str]:
    fmt = FORMATS.get(key)
    return fmt.group if fmt else None


# --------------------------------------------------------------------------- #
# Conversion matrix
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Route:
    engine: str

    @property
    def cloud(self) -> bool:
        return self.engine == E_CLOUD


def _build_matrix() -> Dict[str, Dict[str, Route]]:
    matrix: Dict[str, Dict[str, Route]] = {}

    def add(src: str, dsts, engine: str) -> None:
        for dst in dsts:
            if dst != src:
                matrix.setdefault(src, {})[dst] = Route(engine)

    image_out = ("png", "jpg", "webp", "gif", "bmp", "tiff", "ico", "avif")
    for src in IMAGE_KEYS:
        add(src, image_out, E_IMAGE)
        add(src, ("pdf",), E_IMAGE)
    add("gif", ("mp4", "webm"), E_FFMPEG)

    for src in DATA_KEYS:
        add(src, DATA_KEYS, E_DATA)
    add("json", ("xlsx", "ods"), E_SHEET)
    for src in SHEET_KEYS:
        add(src, SHEET_KEYS, E_SHEET)
    add("xlsx", ("json",), E_SHEET)
    add("ods", ("json",), E_SHEET)

    add("md", ("html", "txt"), E_DOC)
    add("html", ("md", "txt"), E_DOC)
    add("txt", ("txt",), E_DOC)
    matrix.setdefault("txt", {})["txt"] = Route(E_DOC)   # "re-encode to UTF-8"
    add("docx", ("md", "html", "txt"), E_DOC)

    add("pdf", ("png", "jpg", "txt"), E_PDF)

    # Office documents with a layout and ebooks need a real office/ebook engine.
    for src in ("docx", "odt", "pptx", "xlsx", "ods"):
        add(src, ("pdf",), E_CLOUD)
    add("pdf", ("docx", "epub"), E_CLOUD)
    add("epub", ("pdf", "mobi", "azw3"), E_CLOUD)
    add("mobi", ("pdf", "epub"), E_CLOUD)
    add("azw3", ("pdf", "epub"), E_CLOUD)

    for src in ARCHIVE_KEYS:
        add(src, ARCHIVE_KEYS, E_ARCHIVE)

    for src in AUDIO_KEYS:
        add(src, AUDIO_KEYS, E_FFMPEG)
    video_out = ("mp4", "webm", "gif")
    audio_out = ("mp3", "m4a", "ogg", "wav", "flac")
    for src in VIDEO_KEYS:
        add(src, video_out, E_FFMPEG)
        add(src, audio_out, E_FFMPEG)
    return matrix


CONVERSIONS: Dict[str, Dict[str, Route]] = _build_matrix()


@dataclass(frozen=True)
class Capabilities:
    """What this deployment can do on top of the always-available formats."""

    cloud: bool = False   # CLOUDCONVERT_API_KEY configured
    avif: bool = True     # Pillow built with AVIF
    heic: bool = True     # pillow-heif importable


def capabilities(*, cloud: bool = False) -> Capabilities:
    """Probe the optional image codecs (cheap, cached by the import system)."""
    avif = True
    try:
        from PIL import features
        avif = bool(features.check("avif"))
    except Exception:  # noqa: BLE001
        avif = False
    heic = True
    try:
        import pillow_heif  # noqa: F401
    except Exception:  # noqa: BLE001
        heic = False
    return Capabilities(cloud=cloud, avif=avif or heic, heic=heic)


def route(src: str, dst: str) -> Optional[Route]:
    return CONVERSIONS.get(src, {}).get(dst)


def targets(src: str, caps: Optional[Capabilities] = None) -> List[str]:
    """Ordered target keys a ``src`` file can be converted to (<= 25)."""
    caps = caps or Capabilities()
    out: List[str] = []
    for dst, r in CONVERSIONS.get(src, {}).items():
        if r.cloud and not caps.cloud:
            continue
        if dst == "avif" and not caps.avif:
            continue
        out.append(dst)
    return out[:MAX_SELECT_OPTIONS]


def image_targets(src: str, caps: Optional[Capabilities] = None) -> List[str]:
    """Image-only targets (the /image Convert button): never a PDF or a video."""
    return [k for k in targets(src, caps) if FORMATS[k].group == IMAGE]


def can_read(src: str, caps: Optional[Capabilities] = None) -> bool:
    caps = caps or Capabilities()
    if src == "heic" and not caps.heic:
        return False
    if src == "avif" and not caps.avif:
        return False
    return bool(targets(src, caps))


def engine_for(src: str, dst: str) -> Optional[str]:
    r = route(src, dst)
    return r.engine if r else None


def is_local(src: str, dst: str) -> bool:
    r = route(src, dst)
    return bool(r and not r.cloud)


def output_name(source_filename: str, dst: str) -> str:
    """``report.docx`` + ``pdf`` -> ``report.pdf`` (a safe, short stem)."""
    import re
    stem = source_filename.rsplit("/", 1)[-1]
    lowered = stem.lower()
    for ext in ("tar.gz",):
        if lowered.endswith("." + ext):
            stem = stem[: -(len(ext) + 1)]
            break
    else:
        if "." in stem:
            stem = stem.rsplit(".", 1)[0]
    stem = re.sub(r"[^\w.\- ]+", "_", stem, flags=re.UNICODE).strip(" ._") or "file"
    return f"{stem[:80]}.{FORMATS[dst].ext}"
