"""Regex tester engine for ``/regex`` (pure: no Discord, no database).

Uses the third-party ``regex`` package in its ``V0`` (``re``-compatible) mode
with a matching ``timeout``: a catastrophic pattern such as ``(a+)+$`` is cut
off instead of freezing the bot. The functions here are synchronous and CPU
bound — the cog runs them with ``asyncio.to_thread``.

The ``regex`` import is lazy so merely importing this module costs nothing.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: Flag key -> ``regex`` flag attribute name.
FLAG_NAMES: Dict[str, str] = {
    "i": "IGNORECASE",
    "m": "MULTILINE",
    "s": "DOTALL",
    "x": "VERBOSE",
}
FLAG_ORDER: Tuple[str, ...] = ("i", "m", "s", "x")

MAX_PATTERN_LENGTH = 500
MAX_TEXT_LENGTH = 4000
#: Seconds the whole match scan may take.
MATCH_TIMEOUT = 0.5
#: Matches collected before giving up on counting ("1000+").
MAX_MATCHES = 1000
#: Matches kept with their groups (the card shows 10, the attachment all).
MAX_DETAILED = 200
#: A repeat bound above this is rejected before compiling (memory bomb guard).
MAX_REPEAT_BOUND = 1000

_REPEAT_RE = re.compile(r"\{\s*(\d+)\s*(?:,\s*(\d*)\s*)?\}")


class RegexCompileError(Exception):
    """The pattern is not valid. ``position`` is 0-based or ``None``."""

    def __init__(self, message: str, position: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.position = position


class RegexTimeout(Exception):
    """The match scan exceeded :data:`MATCH_TIMEOUT`."""


@dataclass
class MatchInfo:
    index: int
    start: int
    end: int
    text: str
    #: ``(label, value)`` pairs; label is the group name or its number, value ``None`` if unmatched.
    groups: List[Tuple[str, Optional[str]]] = field(default_factory=list)


@dataclass
class RegexResult:
    matches: List[MatchInfo]
    #: Total matches found (capped at :data:`MAX_MATCHES`).
    total: int
    capped: bool = False
    #: Spans of every match as ``(start, end)`` — used to highlight the text.
    spans: List[Tuple[int, int]] = field(default_factory=list)


def normalize_flags(flags: Sequence[str]) -> Tuple[str, ...]:
    """Keep known flag keys, once each, in the canonical order."""
    wanted = set(flags or ())
    return tuple(f for f in FLAG_ORDER if f in wanted)


def _regex_flags(regex_mod, flags: Sequence[str]) -> int:
    value = regex_mod.V0
    for key in normalize_flags(flags):
        value |= getattr(regex_mod, FLAG_NAMES[key])
    return value


def _check_repeat_bounds(pattern: str) -> None:
    for match in _REPEAT_RE.finditer(pattern):
        for group in match.groups():
            if group and int(group) > MAX_REPEAT_BOUND:
                raise RegexCompileError("repeat count too large", match.start())


def compile_pattern(pattern: str, flags: Sequence[str] = ()):
    """Compile ``pattern`` (``regex`` package, V0 mode). Raises :class:`RegexCompileError`."""
    import regex

    if not pattern:
        raise RegexCompileError("empty pattern", 0)
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise RegexCompileError("pattern too long", MAX_PATTERN_LENGTH)
    _check_repeat_bounds(pattern)
    try:
        return regex.compile(pattern, _regex_flags(regex, flags))
    except regex.error as exc:
        position = getattr(exc, "pos", None)
        message = getattr(exc, "msg", None) or str(exc)
        raise RegexCompileError(str(message), position if isinstance(position, int) else None) from exc
    except (OverflowError, RecursionError, MemoryError) as exc:
        raise RegexCompileError("pattern too complex", None) from exc


def _group_labels(compiled) -> Dict[int, str]:
    labels = {index: str(index) for index in range(1, compiled.groups + 1)}
    for name, index in compiled.groupindex.items():
        labels[index] = name
    return labels


def run(pattern: str, text: str, flags: Sequence[str] = (), *,
        timeout: float = MATCH_TIMEOUT) -> RegexResult:
    """Find every non-overlapping match of ``pattern`` in ``text``.

    Raises :class:`RegexCompileError` for an invalid pattern and
    :class:`RegexTimeout` when the scan takes longer than ``timeout`` seconds.
    """
    compiled = compile_pattern(pattern, flags)
    labels = _group_labels(compiled)
    deadline = time.monotonic() + timeout
    matches: List[MatchInfo] = []
    spans: List[Tuple[int, int]] = []
    total = 0
    capped = False
    try:
        for match in compiled.finditer(text, timeout=timeout, concurrent=True):
            total += 1
            spans.append((match.start(), match.end()))
            if len(matches) < MAX_DETAILED:
                matches.append(MatchInfo(
                    index=total,
                    start=match.start(),
                    end=match.end(),
                    text=match.group(0),
                    groups=[(labels[i], match.group(i)) for i in range(1, compiled.groups + 1)],
                ))
            if total >= MAX_MATCHES:
                capped = True
                break
            if time.monotonic() > deadline:
                raise RegexTimeout()
    except TimeoutError as exc:  # raised by the regex module itself
        raise RegexTimeout() from exc
    return RegexResult(matches=matches, total=total, capped=capped, spans=spans)


def highlight_segments(text: str, spans: Sequence[Tuple[int, int]]) -> List[Tuple[str, str]]:
    """Split ``text`` into ``("equal"|"match", raw)`` segments along ``spans``.

    Empty matches are skipped (nothing to highlight); the spans from
    ``finditer`` never overlap.
    """
    segments: List[Tuple[str, str]] = []
    cursor = 0
    for start, end in spans:
        if end <= start:
            continue
        if start > cursor:
            segments.append(("equal", text[cursor:start]))
        segments.append(("match", text[start:end]))
        cursor = end
    if cursor < len(text):
        segments.append(("equal", text[cursor:]))
    return segments


def _cell(value: Optional[str], width: int) -> str:
    if value is None:
        return "–"
    shown = value.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    if not shown:
        return "∅"
    shown = shown.replace("`", "ˋ")
    return shown if len(shown) <= width else shown[: width - 1] + "…"


def format_groups(groups: Sequence[Tuple[str, Optional[str]]], width: int = 40) -> str:
    parts = [f"{label}={_cell(value, 14)}" for label, value in groups]
    text = " ".join(parts)
    return text if len(text) <= width else text[: width - 1] + "…"


def match_table(matches: Sequence[MatchInfo], headers: Tuple[str, str, str, str], *,
                limit: int = 10, text_width: int = 24, groups_width: int = 40) -> str:
    """Aligned plain-text table of the first ``limit`` matches (for a code block)."""
    rows = []
    for match in list(matches)[:limit]:
        rows.append((
            str(match.index),
            f"{match.start}-{match.end}",
            _cell(match.text, text_width),
            format_groups(match.groups, groups_width) if match.groups else "",
        ))
    if not rows:
        return ""
    has_groups = any(row[3] for row in rows)
    columns = list(zip(*([headers] + rows)))
    widths = [max(len(cell) for cell in column) for column in columns]
    if not has_groups:
        widths[3] = 0

    def line(row) -> str:
        cells = [row[i].ljust(widths[i]) for i in range(3)]
        if has_groups:
            cells.append(row[3])
        return "  ".join(cells).rstrip()

    return "\n".join([line(headers)] + [line(row) for row in rows])


def export_text(pattern: str, flags: Sequence[str], text: str, result: RegexResult) -> str:
    """Full plain-text report attached to the card when it had to be shortened."""
    lines = [f"pattern: {pattern}", f"flags: {''.join(normalize_flags(flags)) or '-'}",
             f"matches: {result.total}{'+' if result.capped else ''}", ""]
    for match in result.matches:
        lines.append(f"#{match.index} [{match.start}-{match.end}] {match.text!r}")
        for label, value in match.groups:
            lines.append(f"    {label} = {value!r}")
    return "\n".join(lines) + "\n"


def error_context(pattern: str, position: Optional[int], radius: int = 12) -> Optional[str]:
    """The few characters around an error position, for the error card."""
    if position is None or not pattern:
        return None
    start = max(0, position - radius)
    end = min(len(pattern), position + radius)
    return pattern[start:end]


def regex_version() -> str:
    try:
        import regex
        return getattr(regex, "__version__", "")
    except ImportError:
        return ""


__all__ = [
    "FLAG_ORDER", "MATCH_TIMEOUT", "MAX_PATTERN_LENGTH", "MAX_TEXT_LENGTH",
    "MatchInfo", "RegexCompileError", "RegexResult", "RegexTimeout", "compile_pattern",
    "error_context", "export_text", "highlight_segments", "match_table", "normalize_flags", "run",
]
