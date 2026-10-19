"""Text comparison for ``/diff`` (pure: stdlib ``difflib`` only).

Two modes:

* **lines** — a unified-style diff (``- `` / ``+ `` / ``  `` prefixes) where long
  runs of unchanged lines collapse to ``@@ … @@`` with two lines of context;
* **words** — the merged text with removed words struck through and added words
  in bold, as ``(kind, raw_text)`` segments the view escapes and marks up.

Everything here is CPU bound: the cog runs it with ``asyncio.to_thread``.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import List, Tuple

MAX_TEXT_LENGTH = 4000
CONTEXT_LINES = 2
#: Unchanged text longer than this is shortened in words mode.
EQUAL_KEEP = 70

_TOKEN_RE = re.compile(r"\s+|\w+|[^\w\s]", re.UNICODE)

Segment = Tuple[str, str]


@dataclass
class LineDiff:
    #: Body for a ```diff block (collapsed).
    text: str
    #: Full unified diff for the attachment.
    full: str
    added: int
    removed: int

    @property
    def identical(self) -> bool:
        return self.added == 0 and self.removed == 0


@dataclass
class WordDiff:
    #: ``("equal"|"added"|"removed", raw_text)``.
    segments: List[Segment]
    added: int
    removed: int
    #: Same diff in a flat ``[-removed-]{+added+}`` notation for the attachment.
    full: str = ""

    @property
    def identical(self) -> bool:
        return self.added == 0 and self.removed == 0


def _split_lines(text: str) -> List[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def diff_lines(before: str, after: str, *, context: int = CONTEXT_LINES) -> LineDiff:
    a, b = _split_lines(before), _split_lines(after)
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    out: List[str] = []
    full: List[str] = []
    added = removed = 0
    opcodes = matcher.get_opcodes()
    last = len(opcodes) - 1
    for index, (tag, i1, i2, j1, j2) in enumerate(opcodes):
        if tag == "equal":
            block = a[i1:i2]
            full.extend("  " + line for line in block)
            head = 0 if index == 0 else context
            tail = 0 if index == last else context
            if len(block) <= head + tail + 1:
                out.extend("  " + line for line in block)
            else:
                out.extend("  " + line for line in block[:head])
                out.append("@@ … @@")
                out.extend("  " + line for line in block[len(block) - tail:])
            continue
        for line in a[i1:i2]:
            out.append("- " + line)
            full.append("- " + line)
            removed += 1
        for line in b[j1:j2]:
            out.append("+ " + line)
            full.append("+ " + line)
            added += 1
    return LineDiff("\n".join(out), "\n".join(full), added, removed)


def _count_words(tokens: List[str]) -> int:
    return sum(1 for tok in tokens if tok[:1].isalnum() or tok[:1] == "_")


def _tokens(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.replace("\r\n", "\n").replace("\r", "\n"))


def _shorten_equal(raw: str, *, first: bool, last: bool) -> str:
    """Shorten a long unchanged run, keeping its edges next to the changes."""
    if len(raw) <= EQUAL_KEEP * 2 + 20:
        return raw
    if first:
        return "[…] " + raw[-EQUAL_KEEP:].lstrip()
    if last:
        return raw[:EQUAL_KEEP].rstrip() + " […]"
    return raw[:EQUAL_KEEP].rstrip() + " […] " + raw[-EQUAL_KEEP:].lstrip()


def diff_words(before: str, after: str, *, shorten: bool = True) -> WordDiff:
    a, b = _tokens(before), _tokens(after)
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    raw: List[Segment] = []
    added = removed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            raw.append(("equal", "".join(a[i1:i2])))
            continue
        old, new = "".join(a[i1:i2]), "".join(b[j1:j2])
        if old.strip():
            raw.append(("removed", old))
            removed += _count_words(a[i1:i2])
        if new.strip():
            if old.strip() and not old[-1:].isspace() and not new[:1].isspace():
                raw.append(("equal", " "))
            raw.append(("added", new))
            added += _count_words(b[j1:j2])
        elif new and not old.strip():
            raw.append(("equal", new))  # only whitespace changed: show the new one
    segments = _merge(raw)
    flat = "".join(_flat(kind, text) for kind, text in segments)
    if shorten:
        segments = _shorten(segments)
    return WordDiff(segments, added, removed, flat)


def _flat(kind: str, text: str) -> str:
    if kind == "equal":
        return text
    core = text.strip()
    open_mark, close_mark = ("[-", "-]") if kind == "removed" else ("{+", "+}")
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    return f"{lead}{open_mark}{core}{close_mark}{trail}"


def _merge(segments: List[Segment]) -> List[Segment]:
    """Join neighbours of the same kind."""
    merged: List[Segment] = []
    for kind, text in segments:
        if merged and merged[-1][0] == kind:
            merged[-1] = (kind, merged[-1][1] + text)
        else:
            merged.append((kind, text))
    return merged


def _shorten(segments: List[Segment]) -> List[Segment]:
    if len(segments) <= 1:
        return segments
    out: List[Segment] = []
    last = len(segments) - 1
    for index, (kind, text) in enumerate(segments):
        if kind == "equal":
            text = _shorten_equal(text, first=index == 0, last=index == last)
        out.append((kind, text))
    return out


def stats_line(added: int, removed: int) -> str:
    """``+12 -3``."""
    return f"+{added} -{removed}"
