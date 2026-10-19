"""Unsigned generation parameters in PNG text chunks.

Local image generators write their settings into the PNG they save:

- AUTOMATIC1111 / Forge / most Stable Diffusion front-ends: a ``parameters``
  text chunk (prompt, then a ``Steps: …, Sampler: …, Model: …`` line);
- ComfyUI: a ``prompt`` chunk (the executed node graph, JSON) and usually a
  ``workflow`` chunk (the editor graph).

Nothing here is signed — anyone can add or strip these chunks — so a match is a
strong hint, never a proof (template 5 says exactly that). The prompt itself is
never returned: only the generator and a handful of technical settings.
"""

from __future__ import annotations

import json
import re
import struct
import zlib
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

from aidetector.formats import PNG_SIGNATURE

TOOL_STABLE_DIFFUSION = "Stable Diffusion"
TOOL_COMFYUI = "ComfyUI"

#: Keyword -> generator. ``prompt`` / ``workflow`` win over ``parameters`` when
#: both are present (ComfyUI extensions sometimes write all three).
_KEYWORDS = {
    "parameters": TOOL_STABLE_DIFFUSION,
    "prompt": TOOL_COMFYUI,
    "workflow": TOOL_COMFYUI,
}

#: A text chunk is never inflated beyond this (a zip bomb in a zTXt chunk must
#: not take the bot down); real ComfyUI graphs are a few hundred KB at most.
MAX_TEXT_BYTES = 2 * 1024 * 1024
#: Chunks walked before giving up on a malformed file.
MAX_CHUNKS = 10_000

#: Settings lifted from the A1111 ``parameters`` trailer, in display order.
_A1111_FIELDS = ("Model", "Sampler", "Steps", "CFG scale", "Size")
_SETTING_RE = re.compile(r"(?:^|,\s*)([A-Za-z][A-Za-z0-9 _\-]{0,40}):\s*([^,]{1,200})")
_COMFY_MODEL_INPUTS = ("ckpt_name", "unet_name", "model_name")


@dataclass
class GenerationMetadata:
    """What the PNG says about the generator that made it."""

    tool: str
    keywords: List[str] = field(default_factory=list)
    #: Technical settings safe to show (model, sampler, steps…) — never the prompt.
    settings: Dict[str, str] = field(default_factory=dict)


def iter_text_chunks(data: bytes) -> Iterator[Tuple[str, str]]:
    """Yield ``(keyword, text)`` for every tEXt / zTXt / iTXt chunk.

    Stops at IEND, on a truncated chunk or after :data:`MAX_CHUNKS`. Malformed
    chunks are skipped, never raised: this runs on files strangers upload.
    """
    if not data.startswith(PNG_SIGNATURE):
        return
    pos = len(PNG_SIGNATURE)
    size = len(data)
    for _ in range(MAX_CHUNKS):
        if pos + 8 > size:
            return
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        start = pos + 8
        end = start + length
        if end + 4 > size:
            return
        if ctype == b"IEND":
            return
        if ctype in (b"tEXt", b"zTXt", b"iTXt"):
            parsed = _parse_text_chunk(ctype, data[start:end])
            if parsed is not None:
                yield parsed
        pos = end + 4  # skip the CRC


def _inflate(payload: bytes) -> Optional[bytes]:
    try:
        inflater = zlib.decompressobj()
        out = inflater.decompress(payload, MAX_TEXT_BYTES)
        return out
    except zlib.error:
        return None


def _parse_text_chunk(ctype: bytes, body: bytes) -> Optional[Tuple[str, str]]:
    keyword, sep, rest = body.partition(b"\x00")
    if not sep or not keyword:
        return None
    key = keyword.decode("latin-1", errors="replace")
    if ctype == b"tEXt":
        return key, rest[:MAX_TEXT_BYTES].decode("latin-1", errors="replace")
    if ctype == b"zTXt":
        if not rest:
            return None
        text = _inflate(rest[1:])  # rest[0] is the compression method (0 = zlib)
        return (key, text.decode("latin-1", errors="replace")) if text is not None else None
    # iTXt: compression flag, method, language tag \0, translated keyword \0, text
    if len(rest) < 2:
        return None
    compressed, payload = rest[0], rest[2:]
    _lang, sep1, payload = payload.partition(b"\x00")
    _translated, sep2, payload = payload.partition(b"\x00")
    if not (sep1 and sep2):
        return None
    if compressed:
        payload = _inflate(payload)
        if payload is None:
            return None
    return key, payload[:MAX_TEXT_BYTES].decode("utf-8", errors="replace")


def _safe(value: str, limit: int = 120) -> str:
    """One display-safe line (the cards wrap values in backticks)."""
    return " ".join(str(value).replace("`", "'").split())[:limit]


def _a1111_settings(text: str) -> Dict[str, str]:
    """``Steps: 20, Sampler: Euler a, …`` trailer -> the whitelisted fields."""
    lines = [line for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return {}
    found = dict(_SETTING_RE.findall(lines[-1]))
    return {name: _safe(found[name]) for name in _A1111_FIELDS if _safe(found.get(name, ""))}


def _comfy_settings(text: str) -> Dict[str, str]:
    """The checkpoint name out of a ComfyUI ``prompt`` graph, when present."""
    try:
        graph = json.loads(text)
    except (ValueError, TypeError):
        return {}
    if not isinstance(graph, dict):
        return {}
    for node in graph.values():
        inputs = node.get("inputs") if isinstance(node, dict) else None
        if not isinstance(inputs, dict):
            continue
        for key in _COMFY_MODEL_INPUTS:
            value = inputs.get(key)
            if isinstance(value, str) and _safe(value):
                return {"Model": _safe(value)}
    return {}


def detect_generation_metadata(data: bytes) -> Optional[GenerationMetadata]:
    """The generator a PNG's text chunks point at, or None."""
    texts: Dict[str, str] = {}
    for keyword, text in iter_text_chunks(data):
        lowered = keyword.strip().lower()
        if lowered in _KEYWORDS and lowered not in texts:
            texts[lowered] = text
    if not texts:
        return None

    keywords = sorted(texts)
    if "prompt" in texts or "workflow" in texts:
        settings = _comfy_settings(texts.get("prompt", ""))
        return GenerationMetadata(tool=TOOL_COMFYUI, keywords=keywords, settings=settings)
    return GenerationMetadata(
        tool=TOOL_STABLE_DIFFUSION, keywords=keywords,
        settings=_a1111_settings(texts["parameters"]),
    )
