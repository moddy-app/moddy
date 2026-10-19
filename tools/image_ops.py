"""Image operations of /image: inspect, resize, compress, convert.

Pure Pillow, synchronous (callers run it with ``asyncio.to_thread``). Every
function takes the raw bytes of an image and returns bytes plus a description
of what happened — nothing here knows Discord.

Safety, in the same spirit as ``automod/image_hash.py``:

* the pixel count is checked from the header **before** any pixel is decoded
  (decompression-bomb guard, :data:`MAX_PIXELS`);
* animations are capped to :data:`MAX_FRAMES` frames and to a total pixel budget
  (:data:`MAX_ANIM_PIXELS`), since every frame lives in memory while re-encoding;
* HEIC / HEIF is read through ``pillow-heif``, registered lazily the first time
  such a file shows up.

Everything is re-encoded from decoded pixels: EXIF (including GPS) never
survives, the orientation is baked in.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Tuple

from .convert import formats
from .media_errors import MediaError

# --------------------------------------------------------------------------- #
# Limits
# --------------------------------------------------------------------------- #

MAX_INPUT_BYTES = formats.MAX_INPUT_BYTES
MAX_PIXELS = 80_000_000          # one frame, width x height
MAX_FRAMES = 300
MAX_ANIM_PIXELS = 40_000_000     # frames x width x height, source and result
MAX_SIDE = 8192                  # resize target, per side
MAX_PERCENT = 800
PREVIEW_SIDE = 1024              # PNG preview for formats Discord cannot render

#: Pillow format name -> /convert format key.
PIL_TO_KEY = {
    "PNG": "png", "JPEG": "jpg", "MPO": "jpg", "WEBP": "webp", "GIF": "gif",
    "BMP": "bmp", "TIFF": "tiff", "ICO": "ico", "AVIF": "avif", "HEIF": "heic",
    "HEIC": "heic",
}
#: format key -> Pillow save format name.
KEY_TO_PIL = {
    "png": "PNG", "jpg": "JPEG", "webp": "WEBP", "gif": "GIF", "bmp": "BMP",
    "tiff": "TIFF", "ico": "ICO", "avif": "AVIF", "heic": "HEIF", "pdf": "PDF",
}
#: Formats that can hold an animation here.
ANIMATED_KEYS = ("gif", "webp", "png")
#: Formats without an alpha channel: transparency is flattened on white.
NO_ALPHA_KEYS = ("jpg", "pdf")

FIT, EXACT, FILL = "fit", "exact", "fill"
RESIZE_MODES = (FIT, EXACT, FILL)

LIGHT, BALANCED, STRONG = "light", "balanced", "strong"
COMPRESS_LEVELS = (LIGHT, BALANCED, STRONG)
#: level -> (lossy quality, PNG/GIF palette size or None for lossless).
_LEVEL_PARAMS = {
    LIGHT: (85, None),
    BALANCED: (72, 256),
    STRONG: (55, 96),
}

_heif_state: Optional[bool] = None


def ensure_heif() -> bool:
    """Register ``pillow-heif`` once (lazy: it is a sizeable native library)."""
    global _heif_state
    if _heif_state is None:
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
            _heif_state = True
        except Exception:  # noqa: BLE001 — optional dependency
            _heif_state = False
    return _heif_state


def _import_pil():
    from PIL import Image  # noqa: WPS433 — lazy on purpose
    return Image


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #


@dataclass
class ImageInfo:
    format_key: str            # png, jpg, ...
    width: int                 # as displayed (EXIF orientation applied)
    height: int
    size_bytes: int
    mode: str                  # Pillow mode: RGB, RGBA, P, L, CMYK...
    has_alpha: bool            # really uses transparency
    animated: bool
    frames: int

    @property
    def pixels(self) -> int:
        return self.width * self.height


@dataclass
class Processed:
    """What an operation produced."""

    data: bytes
    format_key: str
    width: int
    height: int
    frames: int = 1
    animated: bool = False
    has_alpha: bool = False
    unchanged: bool = False                    # compress: nothing to gain, input returned
    notes: List[str] = field(default_factory=list)   # i18n keys under tools.image.notes.*
    suggestion: Optional[Tuple[str, int]] = None     # (format_key, estimated bytes)


# --------------------------------------------------------------------------- #
# Opening / inspecting
# --------------------------------------------------------------------------- #


def _looks_heif(raw: bytes) -> bool:
    return raw[4:8] == b"ftyp" and raw[8:12] in (
        b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1",
        b"avif", b"avis")


def _oriented(size: Tuple[int, int], img) -> Tuple[int, int]:
    """Displayed size once the EXIF orientation is applied."""
    try:
        orientation = img.getexif().get(274, 1)
    except Exception:  # noqa: BLE001
        orientation = 1
    return (size[1], size[0]) if orientation in (5, 6, 7, 8) else size


def open_image(raw: bytes):
    """Open ``raw`` lazily (header only) after every cheap check.

    Raises :class:`MediaError` — ``too_large``, ``unreadable``,
    ``too_many_pixels`` — never a Pillow exception.
    """
    Image = _import_pil()
    if not raw:
        raise MediaError("unreadable")
    if len(raw) > MAX_INPUT_BYTES:
        raise MediaError("too_large", max=MAX_INPUT_BYTES // (1024 * 1024))
    if _looks_heif(raw):
        ensure_heif()
    try:
        img = Image.open(io.BytesIO(raw))
        width, height = img.size
    except Image.DecompressionBombError as exc:
        raise MediaError("too_many_pixels", max=MAX_PIXELS // 1_000_000) from exc
    except Exception as exc:  # noqa: BLE001 — Pillow raises a zoo of types
        raise MediaError("unreadable") from exc
    if width <= 0 or height <= 0:
        raise MediaError("unreadable")
    if width * height > MAX_PIXELS:
        raise MediaError("too_many_pixels", max=MAX_PIXELS // 1_000_000)
    return img


def _frame_count(img) -> int:
    return max(1, int(getattr(img, "n_frames", 1) or 1))


def _alpha_in_use(img) -> bool:
    """True when the picture really has transparent pixels (not just a channel)."""
    try:
        if img.mode in ("RGBA", "LA", "PA"):
            return img.getchannel("A").getextrema()[0] < 255
        if img.mode == "P" and "transparency" in img.info:
            return True
        if img.mode in ("RGB", "L") and "transparency" in img.info:
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def read_info(raw: bytes) -> ImageInfo:
    """Describe an image without keeping it in memory."""
    img = open_image(raw)
    try:
        key = PIL_TO_KEY.get((img.format or "").upper())
        if key is None:
            raise MediaError("unsupported")
        frames = _frame_count(img)
        width, height = _oriented(img.size, img)
        mode = img.mode
        img.load()
        return ImageInfo(
            format_key=key, width=width, height=height, size_bytes=len(raw), mode=mode,
            has_alpha=_alpha_in_use(img), animated=frames > 1, frames=min(frames, MAX_FRAMES))
    except MediaError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise MediaError("unreadable") from exc
    finally:
        img.close()


def _load_frames(raw: bytes, *, animated_ok: bool) -> Tuple[List, List[int], int, int]:
    """Decode ``raw`` into ``(frames, durations_ms, loop, oriented?)``.

    Frames are RGBA/RGB copies, EXIF orientation applied. Enforces the frame and
    animation pixel budgets. Only the first frame when ``animated_ok`` is False.
    """
    Image = _import_pil()
    from PIL import ImageOps, ImageSequence
    img = open_image(raw)
    try:
        total = _frame_count(img)
        width, height = img.size
        want = total if animated_ok else 1
        if want > MAX_FRAMES:
            raise MediaError("too_many_frames", max=MAX_FRAMES)
        if want > 1 and want * width * height > MAX_ANIM_PIXELS:
            raise MediaError("animation_too_big")
        frames, durations = [], []
        loop = int(img.info.get("loop", 0) or 0)
        iterator = ImageSequence.Iterator(img) if want > 1 else [img]
        for frame in iterator:
            frame.load()
            converted = frame.convert("RGBA" if _needs_rgba(frame) else "RGB")
            converted = ImageOps.exif_transpose(converted) if want == 1 else converted
            frames.append(converted)
            durations.append(int(frame.info.get("duration", 100) or 100))
        return frames, durations, loop, total
    except MediaError:
        raise
    except (Image.DecompressionBombError, MemoryError) as exc:
        raise MediaError("too_many_pixels", max=MAX_PIXELS // 1_000_000) from exc
    except Exception as exc:  # noqa: BLE001
        raise MediaError("unreadable") from exc
    finally:
        img.close()


def load_frames(raw: bytes, *, animated_ok: bool = False):
    """Public face of the safe decoder (used by ``tools/bg_removal.py``)."""
    return _load_frames(raw, animated_ok=animated_ok)


def _needs_rgba(frame) -> bool:
    return (frame.mode in ("RGBA", "LA", "PA", "P") and
            (frame.mode != "P" or "transparency" in frame.info)) or \
        (frame.mode in ("RGB", "L") and "transparency" in frame.info)


# --------------------------------------------------------------------------- #
# Resize maths
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ResizePlan:
    width: int                                   # final width
    height: int                                  # final height
    scale_to: Tuple[int, int]                    # size before the (optional) crop
    crop: Optional[Tuple[int, int, int, int]] = None  # (left, top, right, bottom) after scaling


def _valid_dim(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > MAX_SIDE:
        raise MediaError("invalid_size", max=MAX_SIDE)
    return value


def plan_resize(src_w: int, src_h: int, *, width: Optional[int] = None,
                height: Optional[int] = None, percent: Optional[float] = None,
                mode: str = FIT) -> ResizePlan:
    """Work out the target size.

    * ``percent`` wins over the box: both sides scale by the same factor.
    * ``fit``   — keep the ratio, stay inside ``width x height`` (one side given:
      the other follows).
    * ``exact`` — stretch to exactly ``width x height`` (needs both sides).
    * ``fill``  — keep the ratio, cover ``width x height`` and crop the overflow
      around the centre (needs both sides).

    ``exact`` / ``fill`` with a single side degrade to ``fit``. Raises
    :class:`MediaError` ``invalid_size`` for anything outside 1-8192 px (or a
    percentage outside 1-800 %), ``nothing_to_do`` when no size was given.
    """
    if mode not in RESIZE_MODES:
        mode = FIT
    if src_w < 1 or src_h < 1:
        raise MediaError("unreadable")

    if percent is not None:
        if not (1 <= percent <= MAX_PERCENT):
            raise MediaError("invalid_percent", max=MAX_PERCENT)
        new_w = max(1, round(src_w * percent / 100))
        new_h = max(1, round(src_h * percent / 100))
        return _checked(ResizePlan(new_w, new_h, (new_w, new_h)))

    width, height = _valid_dim(width), _valid_dim(height)
    if width is None and height is None:
        raise MediaError("nothing_to_do")

    if width is None or height is None or mode == FIT:
        if width is not None and height is not None:
            scale = min(width / src_w, height / src_h)
        elif width is not None:
            scale = width / src_w
        else:
            scale = height / src_h
        new_w = max(1, round(src_w * scale))
        new_h = max(1, round(src_h * scale))
        # Rounding must never push a side beyond the box the user gave.
        if width is not None:
            new_w = min(new_w, width)
        if height is not None:
            new_h = min(new_h, height)
        return _checked(ResizePlan(new_w, new_h, (new_w, new_h)))

    if mode == EXACT:
        return _checked(ResizePlan(width, height, (width, height)))

    # FILL: cover the box, then crop the centre.
    scale = max(width / src_w, height / src_h)
    scaled_w = max(width, math.ceil(src_w * scale))
    scaled_h = max(height, math.ceil(src_h * scale))
    left = (scaled_w - width) // 2
    top = (scaled_h - height) // 2
    return _checked(ResizePlan(width, height, (scaled_w, scaled_h),
                               (left, top, left + width, top + height)))


def _checked(plan: ResizePlan) -> ResizePlan:
    biggest = max(plan.width, plan.height, *plan.scale_to)
    if biggest > MAX_SIDE:
        raise MediaError("invalid_size", max=MAX_SIDE)
    if plan.width * plan.height > MAX_PIXELS or plan.scale_to[0] * plan.scale_to[1] > MAX_PIXELS:
        raise MediaError("too_many_pixels", max=MAX_PIXELS // 1_000_000)
    return plan


# --------------------------------------------------------------------------- #
# Encoding
# --------------------------------------------------------------------------- #


def _flatten(frame, background=(255, 255, 255)):
    """RGBA -> RGB on a plain background (JPEG / PDF have no alpha)."""
    Image = _import_pil()
    if frame.mode == "RGB":
        return frame
    canvas = Image.new("RGB", frame.size, background)
    canvas.paste(frame, mask=frame.getchannel("A") if "A" in frame.getbands() else None)
    return canvas


def _quantise(frame, colors: int):
    """Palette image with at most ``colors`` entries (alpha kept as a palette index)."""
    Image = _import_pil()
    if frame.mode == "RGBA":
        return frame.quantize(colors=colors, method=Image.Quantize.FASTOCTREE,
                              dither=Image.Dither.FLOYDSTEINBERG)
    return frame.convert("RGB").quantize(colors=colors, method=Image.Quantize.MEDIANCUT,
                                          dither=Image.Dither.FLOYDSTEINBERG)


def encode(frames: List, key: str, *, durations: Optional[List[int]] = None, loop: int = 0,
           quality: int = 92, palette: Optional[int] = None,
           notes: Optional[List[str]] = None) -> bytes:
    """Encode decoded frames as ``key``. One frame -> a still image."""
    Image = _import_pil()
    notes = notes if notes is not None else []
    pil_format = KEY_TO_PIL.get(key)
    if pil_format is None:
        raise MediaError("unsupported")
    if key in ("heic", "avif") and not ensure_heif() and key == "heic":
        raise MediaError("unsupported")
    animated = len(frames) > 1 and key in ANIMATED_KEYS
    if len(frames) > 1 and not animated:
        notes.append("animation_lost")
        frames = frames[:1]
    has_alpha = any(f.mode == "RGBA" for f in frames)
    out = io.BytesIO()
    first = frames[0]

    if key in NO_ALPHA_KEYS:
        if has_alpha:
            notes.append("alpha_flattened")
        frames = [_flatten(f) for f in frames]
        first = frames[0]

    if key == "jpg":
        first.convert("RGB").save(out, "JPEG", quality=quality, optimize=True,
                                  progressive=True, subsampling=2 if quality < 80 else 0)
    elif key == "png":
        imgs = frames
        if palette:
            imgs = [_quantise(f, palette) for f in frames]
        if animated:
            imgs[0].save(out, "PNG", save_all=True, append_images=imgs[1:], optimize=True,
                         duration=durations or 100, loop=loop)
        else:
            imgs[0].save(out, "PNG", optimize=True, compress_level=9)
    elif key == "webp":
        kwargs = dict(quality=quality, method=4)
        if animated:
            first.save(out, "WEBP", save_all=True, append_images=frames[1:],
                       duration=durations or 100, loop=loop, **kwargs)
        else:
            first.save(out, "WEBP", **kwargs)
    elif key == "gif":
        size = palette or 256
        imgs = [_quantise(f, size) if f.mode != "P" or palette else f for f in frames]
        if animated:
            imgs[0].save(out, "GIF", save_all=True, append_images=imgs[1:], optimize=True,
                         duration=durations or 100, loop=loop, disposal=2)
        else:
            imgs[0].save(out, "GIF", optimize=True)
    elif key == "bmp":
        (first if first.mode == "RGBA" else first.convert("RGB")).save(out, "BMP")
    elif key == "tiff":
        mode_ok = first if first.mode in ("RGB", "RGBA") else first.convert("RGB")
        mode_ok.save(out, "TIFF", compression="tiff_adobe_deflate")
    elif key == "ico":
        _save_ico(first, out, notes)
    elif key == "avif":
        first.save(out, "AVIF", quality=quality)
    elif key == "heic":
        first.save(out, "HEIF", quality=quality)
    elif key == "pdf":
        first.convert("RGB").save(out, "PDF", resolution=150.0)
    return out.getvalue()


def _save_ico(frame, out, notes: List[str]) -> None:
    Image = _import_pil()
    side = max(frame.size)
    if side > 256:
        frame = frame.copy()
        frame.thumbnail((256, 256), Image.LANCZOS)
        notes.append("ico_resized")
    # ICO icons are square: centre the picture on a transparent canvas.
    rgba = frame.convert("RGBA")
    size = max(rgba.size)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(rgba, ((size - rgba.width) // 2, (size - rgba.height) // 2))
    sizes = [(s, s) for s in (16, 32, 48, 64, 128, 256) if s <= size] or [(size, size)]
    canvas.save(out, "ICO", sizes=sizes)


def _result(data: bytes, key: str, frames: List, *, animated: bool, notes: List[str]) -> Processed:
    return Processed(
        data=data, format_key=key, width=frames[0].width, height=frames[0].height,
        frames=len(frames) if animated else 1, animated=animated,
        has_alpha=any(f.mode == "RGBA" for f in frames) and key not in NO_ALPHA_KEYS,
        notes=notes)


def _out_key(source_key: str, notes: List[str]) -> str:
    """Format a re-encode keeps; those without an encoder here fall back to PNG."""
    if source_key == "heic" and not ensure_heif():
        notes.append("saved_as_png")
        return "png"
    return source_key


# --------------------------------------------------------------------------- #
# The operations
# --------------------------------------------------------------------------- #


def resize(raw: bytes, *, width: Optional[int] = None, height: Optional[int] = None,
           percent: Optional[float] = None, mode: str = FIT) -> Processed:
    """Resize keeping the source format (animations stay animated)."""
    Image = _import_pil()
    probe = open_image(raw)
    try:
        key = PIL_TO_KEY.get((probe.format or "").upper())
        if key is None:
            raise MediaError("unsupported")
        src_w, src_h = _oriented(probe.size, probe)
    finally:
        probe.close()

    plan = plan_resize(src_w, src_h, width=width, height=height, percent=percent, mode=mode)
    animated_in = key in ANIMATED_KEYS
    frames, durations, loop, total = _load_frames(raw, animated_ok=animated_in)
    n = len(frames)
    if n > 1 and n * plan.scale_to[0] * plan.scale_to[1] > MAX_ANIM_PIXELS:
        raise MediaError("animation_too_big")

    notes: List[str] = []
    out_frames = []
    for frame in frames:
        scaled = frame.resize(plan.scale_to, Image.LANCZOS) if frame.size != plan.scale_to else frame
        if plan.crop:
            scaled = scaled.crop(plan.crop)
        out_frames.append(scaled)
    frames.clear()
    if total > MAX_FRAMES:
        notes.append("frames_capped")
    out_key = _out_key(key, notes)
    data = encode(out_frames, out_key, durations=durations, loop=loop, quality=92, notes=notes)
    return _result(data, out_key, out_frames, animated=n > 1 and out_key in ANIMATED_KEYS,
                   notes=notes)


def compress(raw: bytes, *, level: str = BALANCED, max_side: Optional[int] = None) -> Processed:
    """Re-encode smaller. Keeps the format, except where it cannot shrink (BMP -> PNG)."""
    Image = _import_pil()
    if level not in COMPRESS_LEVELS:
        level = BALANCED
    quality, palette = _LEVEL_PARAMS[level]
    max_side = _valid_dim(max_side)

    probe = open_image(raw)
    try:
        key = PIL_TO_KEY.get((probe.format or "").upper())
        if key is None:
            raise MediaError("unsupported")
        src_w, src_h = _oriented(probe.size, probe)
    finally:
        probe.close()
    if key == "ico":
        raise MediaError("not_compressible")

    notes: List[str] = []
    out_key = key
    if key == "bmp":
        out_key = "png"
        notes.append("saved_as_png")
    out_key = _out_key(out_key, notes)

    frames, durations, loop, total = _load_frames(raw, animated_ok=out_key in ANIMATED_KEYS)
    if max_side and max(src_w, src_h) > max_side:
        scale = max_side / max(src_w, src_h)
        size = (max(1, round(src_w * scale)), max(1, round(src_h * scale)))
        frames = [f.resize(size, Image.LANCZOS) for f in frames]
        notes.append("downscaled")
    if total > MAX_FRAMES:
        notes.append("frames_capped")

    if out_key == "tiff":
        data = _encode_tiff_compressed(frames[0], level, quality)
    else:
        # Palette reduction only helps PNG / GIF; lossy codecs use the quality.
        pal = palette if out_key in ("png", "gif") else None
        data = encode(frames, out_key, durations=durations, loop=loop, quality=quality,
                      palette=pal, notes=notes)

    animated = len(frames) > 1 and out_key in ANIMATED_KEYS
    result = _result(data, out_key, frames, animated=animated, notes=notes)
    if len(data) >= len(raw) and out_key == key and not max_side:
        # Nothing to gain: hand the original back untouched rather than a bigger file.
        info = read_info(raw)
        return Processed(
            data=raw, format_key=key, width=info.width, height=info.height,
            frames=info.frames if info.animated else 1, animated=info.animated,
            has_alpha=info.has_alpha, unchanged=True, notes=[n for n in notes if n == "frames_capped"])
    if out_key in ("png", "bmp", "tiff") and not animated and \
            result.width * result.height <= 12_000_000 and len(data) > 100 * 1024:
        result.suggestion = _webp_suggestion(frames[0], quality, len(data))
    return result


def _encode_tiff_compressed(frame, level: str, quality: int) -> bytes:
    out = io.BytesIO()
    rgb = frame if frame.mode in ("RGB", "L") else frame.convert("RGB")
    if level == LIGHT:
        rgb.save(out, "TIFF", compression="tiff_adobe_deflate")
    else:
        rgb.save(out, "TIFF", compression="jpeg", quality=quality)
    return out.getvalue()


def _webp_suggestion(frame, quality: int, current: int) -> Optional[Tuple[str, int]]:
    """Would WebP shrink a PNG/TIFF a lot more? Returns ``("webp", bytes)`` or None."""
    try:
        buf = io.BytesIO()
        frame.save(buf, "WEBP", quality=quality, method=4)
        size = buf.tell()
    except Exception:  # noqa: BLE001 — a suggestion must never break the result
        return None
    return ("webp", size) if size < current * 0.75 else None


def convert(raw: bytes, target: str) -> Processed:
    """Convert to another image format (``target`` is a /convert format key)."""
    if target not in KEY_TO_PIL or target == "heic":
        raise MediaError("unsupported")
    probe = open_image(raw)
    try:
        key = PIL_TO_KEY.get((probe.format or "").upper())
    finally:
        probe.close()
    if key is None:
        raise MediaError("unsupported")
    animated_ok = target in ANIMATED_KEYS
    frames, durations, loop, total = _load_frames(raw, animated_ok=animated_ok)
    notes: List[str] = []
    if total > 1 and not animated_ok:
        notes.append("animation_lost")
    if total > MAX_FRAMES:
        notes.append("frames_capped")
    quality = {"jpg": 92, "webp": 90, "avif": 75}.get(target, 92)
    data = encode(frames, target, durations=durations, loop=loop, quality=quality, notes=notes)
    return _result(data, target, frames, animated=len(frames) > 1 and target in ANIMATED_KEYS,
                   notes=notes)


def preview_png(raw: bytes, side: int = PREVIEW_SIDE) -> bytes:
    """Small PNG preview of any readable image (first frame), for the formats
    Discord cannot display itself (HEIC, AVIF, BMP, TIFF, ICO)."""
    Image = _import_pil()
    frames, _, _, _ = _load_frames(raw, animated_ok=False)
    frame = frames[0]
    if max(frame.size) > side:
        frame = frame.copy()
        frame.thumbnail((side, side), Image.LANCZOS)
    out = io.BytesIO()
    frame.save(out, "PNG", optimize=True)
    return out.getvalue()


def percent_saved(before: int, after: int) -> int:
    """Rounded percentage saved (positive = smaller). 0 when ``before`` is 0."""
    if before <= 0:
        return 0
    return round((1 - after / before) * 100)


def mime_of(key: str) -> str:
    fmt = formats.FORMATS.get(key)
    return fmt.mime if fmt else "application/octet-stream"
