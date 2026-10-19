"""Colour parsing, conversion, WCAG contrast and the /color preview image — pure.

Parsing accepts ``#RGB``, ``#RGBA``, ``#RRGGBB``, ``#RRGGBBAA``, ``rgb()`` /
``rgba()``, ``hsl()`` / ``hsla()``, the 148 CSS named colours, a Discord role
colour integer and ``random``. Transparency is parsed but ignored: the card
shows the opaque colour and says so.

Pillow is imported lazily (only the preview needs it) to keep start-up lean.
"""

from __future__ import annotations

import colorsys
import io
import math
import re
import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

RGB = Tuple[int, int, int]

#: Discord chat backgrounds the preview and the contrast table are computed on.
#: Discord's four default themes are Light, Ash, Dark and Onyx (confirmed by
#: Discord's own announcement, https://discord.com/blog/bring-your-vibe-to-discord-with-new-themes-in-nitro
#: and the mobile refresh notes at
#: https://support.discord.com/hc/en-us/articles/42383370736023). Discord does
#: not publish the hex values of the refreshed surfaces and third-party sources
#: disagree, so these are deliberately simple reference values rather than
#: pixel-exact ones: pure white for Light, a near-black neutral for Dark. See
#: docs/features/TOOLS_SECURITY.md ("Known limitations").
DISCORD_LIGHT_BG: RGB = (0xFF, 0xFF, 0xFF)
DISCORD_DARK_BG: RGB = (0x1A, 0x1A, 0x1E)

WHITE: RGB = (255, 255, 255)
BLACK: RGB = (0, 0, 0)

#: A named colour is shown when it lies within this RGB distance.
NAMED_MAX_DISTANCE = 30.0

EXAMPLES = "#5865F2 · rgb(88, 101, 242) · hsl(235, 86%, 65%) · rebeccapurple · 5793266 · random"


class ColorError(Exception):
    """The text is not a colour. ``code`` maps to ``tools.color.errors.<code>``."""

    def __init__(self, code: str = "invalid"):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ParsedColor:
    rgb: RGB
    alpha: Optional[float] = None  # 0..1 when the input carried one
    random: bool = False


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

_HEX_RE = re.compile(r"^(?:#|0x)?([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$")
_FUNC_RE = re.compile(r"^(rgba?|hsla?)\(\s*(.*?)\s*\)$")
_NUM = r"[+-]?(?:\d+\.?\d*|\.\d+)"
_PART_RE = re.compile(rf"^({_NUM})(%|deg|turn|rad|grad)?$")


@lru_cache(maxsize=1)
def named_colors() -> Dict[str, RGB]:
    """The 148 CSS named colours (``gray`` and ``grey`` spellings included)."""
    from PIL import ImageColor
    # Pillow caches resolved names in ``colormap`` as (r, g, b) tuples once
    # anything calls ``getrgb(name)``, so values are either "#rrggbb" or tuples.
    return {
        name: _hex_to_rgb(value[1:]) if isinstance(value, str) else tuple(value[:3])
        for name, value in ImageColor.colormap.items()
    }


def _hex_to_rgb(digits: str) -> RGB:
    return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))


def random_color() -> int:
    """A uniformly random colour as a Discord integer."""
    return secrets.randbelow(0x1000000)


def int_to_rgb(value: int) -> RGB:
    return ((value >> 16) & 255, (value >> 8) & 255, value & 255)


def rgb_to_int(rgb: RGB) -> int:
    return (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]


def parse(text: str) -> ParsedColor:
    """Parse any supported notation. Raises :class:`ColorError`."""
    raw = (text or "").strip().strip("`\"'").strip()
    if not raw:
        raise ColorError("empty")
    value = raw.lower()
    if value == "random":
        return ParsedColor(int_to_rgb(random_color()), random=True)

    # Bare digits are a Discord integer, not hex (hex needs '#' or a letter a-f).
    if value.isdigit():
        number = int(value)
        if number > 0xFFFFFF:
            raise ColorError("out_of_range")
        return ParsedColor(int_to_rgb(number))

    match = _HEX_RE.match(value)
    if match:
        return _parse_hex(match.group(1))

    func = _FUNC_RE.match(value)
    if func:
        return _parse_function(func.group(1), func.group(2))

    name = re.sub(r"[\s_-]+", "", value)
    colors = named_colors()
    if name in colors:
        return ParsedColor(colors[name])
    raise ColorError("invalid")


def _parse_hex(digits: str) -> ParsedColor:
    if len(digits) in (3, 4):
        digits = "".join(ch * 2 for ch in digits)
    alpha = int(digits[6:8], 16) / 255 if len(digits) == 8 else None
    return ParsedColor(_hex_to_rgb(digits), alpha)


def _split_args(body: str) -> Tuple[List[str], Optional[str]]:
    """``"r, g, b / a"`` or ``"r g b / a"`` or ``"r, g, b, a"`` -> (3 parts, alpha)."""
    alpha = None
    if "/" in body:
        body, _, alpha = body.partition("/")
        alpha = alpha.strip()
    parts = [p for p in re.split(r"[\s,]+", body.strip()) if p]
    if len(parts) == 4 and alpha is None:
        alpha = parts.pop()
    if len(parts) != 3:
        raise ColorError("invalid")
    return parts, alpha


def _number(part: str) -> Tuple[float, Optional[str]]:
    match = _PART_RE.match(part)
    if not match:
        raise ColorError("invalid")
    return float(match.group(1)), match.group(2)


def _alpha(part: Optional[str]) -> Optional[float]:
    if part is None:
        return None
    number, unit = _number(part)
    value = number / 100 if unit == "%" else number
    return max(0.0, min(1.0, value))


def _clamp255(value: float) -> int:
    return max(0, min(255, int(round(value))))


def _parse_function(kind: str, body: str) -> ParsedColor:
    parts, alpha = _split_args(body)
    alpha_value = _alpha(alpha)
    if kind.startswith("rgb"):
        channels = []
        for part in parts:
            number, unit = _number(part)
            if unit not in (None, "%"):
                raise ColorError("invalid")
            channels.append(_clamp255(number * 2.55 if unit == "%" else number))
        return ParsedColor((channels[0], channels[1], channels[2]), alpha_value)

    hue, hue_unit = _number(parts[0])
    if hue_unit == "turn":
        hue *= 360
    elif hue_unit == "rad":
        hue = math.degrees(hue)
    elif hue_unit == "grad":
        hue *= 0.9
    elif hue_unit not in (None, "deg"):
        raise ColorError("invalid")
    sat, sat_unit = _number(parts[1])
    light, light_unit = _number(parts[2])
    if sat_unit not in (None, "%") or light_unit not in (None, "%"):
        raise ColorError("invalid")
    sat = max(0.0, min(100.0, sat)) / 100
    light = max(0.0, min(100.0, light)) / 100
    r, g, b = colorsys.hls_to_rgb((hue % 360) / 360, light, sat)
    return ParsedColor((_clamp255(r * 255), _clamp255(g * 255), _clamp255(b * 255)), alpha_value)


# --------------------------------------------------------------------------- #
# Conversions
# --------------------------------------------------------------------------- #


def to_hex(rgb: RGB) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def to_hsl(rgb: RGB) -> Tuple[int, int, int]:
    h, l, s = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
    return round(h * 360) % 360, round(s * 100), round(l * 100)


def to_hsv(rgb: RGB) -> Tuple[int, int, int]:
    h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    return round(h * 360) % 360, round(s * 100), round(v * 100)


def to_cmyk(rgb: RGB) -> Tuple[int, int, int, int]:
    r, g, b = (c / 255 for c in rgb)
    k = 1 - max(r, g, b)
    if k >= 1:
        return 0, 0, 0, 100
    c = (1 - r - k) / (1 - k)
    m = (1 - g - k) / (1 - k)
    y = (1 - b - k) / (1 - k)
    return round(c * 100), round(m * 100), round(y * 100), round(k * 100)


def conversions(rgb: RGB) -> List[Tuple[str, str]]:
    """``[(label, text)]`` of every notation shown on the card."""
    h, s, l = to_hsl(rgb)
    hv, sv, vv = to_hsv(rgb)
    c, m, y, k = to_cmyk(rgb)
    return [
        ("HEX", to_hex(rgb)),
        ("RGB", f"rgb({rgb[0]}, {rgb[1]}, {rgb[2]})"),
        ("HSL", f"hsl({h}, {s}%, {l}%)"),
        ("HSV", f"hsv({hv}, {sv}%, {vv}%)"),
        ("CMYK", f"cmyk({c}%, {m}%, {y}%, {k}%)"),
        ("Discord", str(rgb_to_int(rgb))),
    ]


# --------------------------------------------------------------------------- #
# WCAG 2.x contrast
# --------------------------------------------------------------------------- #


def _linear(channel: int) -> float:
    c = channel / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb: RGB) -> float:
    """Relative luminance (WCAG 2.x)."""
    r, g, b = (_linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: RGB, b: RGB) -> float:
    """Contrast ratio between two colours, from 1 to 21."""
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


@dataclass(frozen=True)
class Contrast:
    ratio: float
    aa: bool          # normal text, >= 4.5
    aaa: bool         # normal text, >= 7
    aa_large: bool    # large text, >= 3
    aaa_large: bool   # large text, >= 4.5


def wcag(a: RGB, b: RGB) -> Contrast:
    """Ratio and WCAG levels. Levels use the exact ratio, never the rounded one."""
    ratio = contrast_ratio(a, b)
    return Contrast(ratio, ratio >= 4.5, ratio >= 7.0, ratio >= 3.0, ratio >= 4.5)


def readability(ratio: float) -> str:
    """Plain verdict for text of one colour on the other: ``excellent`` (WCAG AAA),
    ``good`` (AA), ``weak`` (large text only) or ``poor``."""
    if ratio >= 7.0:
        return "excellent"
    if ratio >= 4.5:
        return "good"
    if ratio >= 3.0:
        return "weak"
    return "poor"


def format_ratio(ratio: float) -> str:
    """Two decimals (``4.48:1``). A ratio just below a WCAG threshold is floored
    instead, so a failing 4.4999 never reads ``4.50:1`` next to a red cross."""
    shown = round(ratio, 2)
    if shown in (3.0, 4.5, 7.0) and ratio < shown:
        shown = math.floor(ratio * 100) / 100
    return f"{shown:.2f}:1"


# --------------------------------------------------------------------------- #
# Closest CSS name
# --------------------------------------------------------------------------- #


def closest_name(rgb: RGB) -> Optional[Tuple[str, bool]]:
    """``(name, exact)`` of the nearest CSS colour within :data:`NAMED_MAX_DISTANCE`."""
    best_name, best = None, float("inf")
    for name, value in named_colors().items():
        if "grey" in name:  # every grey has a gray twin: show one spelling
            continue
        dist = math.dist(rgb, value)
        if dist < best:
            best_name, best = name, dist
    if best_name is None or best > NAMED_MAX_DISTANCE:
        return None
    return best_name, best == 0


# --------------------------------------------------------------------------- #
# Preview image
# --------------------------------------------------------------------------- #

IMAGE_WIDTH = 960
_SWATCH_H = 220
_PANEL_H = 200
_GAP = 16
_RADIUS = 28
_SAMPLE = "Aa  Moddy"
_PANEL_OUTLINE: RGB = (0x8E, 0x90, 0x9C)


#: Inter (SIL OFL), shipped in the repo; see assets/fonts/README.md.
FONT_PATH = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "Inter-Bold.otf"


def _load_font(image_font, size: int):
    """Inter Bold when present, Pillow's built-in font otherwise."""
    try:
        return image_font.truetype(str(FONT_PATH), size)
    except OSError:
        try:
            return image_font.load_default(size=size)  # scalable since Pillow 10.1
        except TypeError:  # pragma: no cover - very old Pillow: tiny bitmap font
            return image_font.load_default()


def render_preview(rgb: RGB) -> bytes:
    """PNG: a wide swatch, then the colour as text on Discord dark and light.

    CPU-bound (Pillow): call it through ``asyncio.to_thread``.
    """
    from PIL import Image, ImageDraw, ImageFont

    height = _SWATCH_H + _GAP + _PANEL_H
    image = Image.new("RGBA", (IMAGE_WIDTH, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((0, 0, IMAGE_WIDTH - 1, _SWATCH_H - 1), _RADIUS, fill=rgb + (255,))

    font = _load_font(ImageFont, 72)

    panel_w = (IMAGE_WIDTH - _GAP) // 2
    top = _SWATCH_H + _GAP
    for index, background in enumerate((DISCORD_DARK_BG, DISCORD_LIGHT_BG)):
        left = index * (panel_w + _GAP)
        box = (left, top, left + panel_w - 1, top + _PANEL_H - 1)
        # A hairline keeps a white panel visible on a light Discord theme.
        draw.rounded_rectangle(box, _RADIUS, fill=background + (255,),
                               outline=_PANEL_OUTLINE + (255,), width=2)
        draw.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), _SAMPLE,
                  font=font, fill=rgb + (255,), anchor="mm")

    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()
