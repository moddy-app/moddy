"""Markdown helpers shared by the text tools (/regex, /diff).

User text shown *outside* a code block must not be able to format the card, ping
anyone or open a link: every character Discord's markdown understands is
backslash-escaped (Discord consumes a backslash before any punctuation), the
line-start constructs (headings, lists, ``-#`` subtext) are broken up, and
``@`` gets a zero-width space so a name never looks like a mention.

Pure functions — no Discord import.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Tuple

ZWSP = "​"

_SPECIAL = re.compile(r"([\\*_~|`<>\[\]])")
# A line that Discord would turn into a heading, a list item or subtext.
_LINE_START = re.compile(r"^([ \t]*)(#{1,3}(?=\s)|-#(?=\s)|[-+*](?=\s)|\d{1,9}[.)](?=\s))")

Segment = Tuple[str, str]
Marks = Dict[str, Tuple[str, str]]


def _guard_line(line: str) -> str:
    match = _LINE_START.match(line)
    if not match:
        return line
    lead, token = match.group(1), match.group(2)
    if token[0].isdigit():
        token = token[:-1] + "\\" + token[-1]
    else:
        token = "\\" + token
    return lead + token + line[match.end():]


def escape_text(text: str, *, at_line_start: bool = True) -> str:
    """Escape ``text`` so it renders literally.

    ``at_line_start=False`` when the text continues a line already started by a
    previous segment (its first line then needs no heading/list guard).
    """
    if not text:
        return ""
    text = text.replace("@", "@" + ZWSP)
    text = _SPECIAL.sub(r"\\\1", text)
    out = []
    for index, line in enumerate(text.split("\n")):
        out.append(_guard_line(line) if (index > 0 or at_line_start) else line)
    return "\n".join(out)


def wrap_marker(text: str, open_mark: str, close_mark: str) -> str:
    """Wrap ``text`` in a markdown marker pair, line by line.

    Markers break when they touch whitespace (``** a**`` is not bold) and when a
    span crosses a paragraph break, so each line's visible core is wrapped on
    its own and the surrounding whitespace stays outside. Whitespace-only lines
    are left as they are.
    """
    pieces: List[str] = []
    for line in text.split("\n"):
        core = line.strip(" \t")
        if not core:
            pieces.append(line)
            continue
        start = line.index(core[0])
        end = start + len(core)
        pieces.append(line[:start] + open_mark + line[start:end] + close_mark + line[end:])
    return "\n".join(pieces)


def render_segment(kind: str, raw: str, marks: Marks, *, at_line_start: bool) -> str:
    """One ``(kind, raw_text)`` segment as escaped markdown (marked if ``kind`` is in ``marks``)."""
    escaped = escape_text(raw, at_line_start=at_line_start)
    if kind in marks:
        open_mark, close_mark = marks[kind]
        escaped = wrap_marker(escaped, open_mark, close_mark)
    return escaped


def render_segments(segments: Iterable[Segment], marks: Marks) -> str:
    """Join segments into escaped markdown; a kind absent from ``marks`` stays plain."""
    out: List[str] = []
    at_start = True
    for kind, raw in segments:
        if not raw:
            continue
        out.append(render_segment(kind, raw, marks, at_line_start=at_start))
        at_start = raw.endswith("\n")
    return "".join(out)


def clip_segments(segments: Iterable[Segment], limit: int, marks: Marks) -> Tuple[str, bool]:
    """Render leading segments while they fit in ``limit`` characters.

    Returns ``(markdown, was_clipped)``. A segment that does not fit whole is
    cut on its raw text (then escaped again), so the output never ends in the
    middle of an escape sequence or an open marker. A clipped output ends in ``…``.
    """
    out: List[str] = []
    used = 0
    at_start = True
    for kind, raw in segments:
        if not raw:
            continue
        piece = render_segment(kind, raw, marks, at_line_start=at_start)
        if used + len(piece) <= limit:
            out.append(piece)
            used += len(piece)
            at_start = raw.endswith("\n")
            continue
        low, high = 0, len(raw)
        while low < high:
            mid = (low + high + 1) // 2
            if used + len(render_segment(kind, raw[:mid], marks, at_line_start=at_start)) <= limit:
                low = mid
            else:
                high = mid - 1
        if low:
            out.append(render_segment(kind, raw[:low], marks, at_line_start=at_start))
        return "".join(out) + "…", True
    return "".join(out), False
