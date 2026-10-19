"""Text comparison for ``/diff`` (pure: stdlib ``difflib`` only).

A unified-style line diff (``- `` / ``+ `` / ``  `` prefixes) where long runs of
unchanged lines collapse to ``@@ … @@`` with two lines of context. The view puts
it in a Discord ```diff code block, which colours removed lines red and added
lines green.

CPU bound: the cog runs it with ``asyncio.to_thread``.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import List

MAX_TEXT_LENGTH = 4000
CONTEXT_LINES = 2


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


def stats_line(added: int, removed: int) -> str:
    """``+12 -3``."""
    return f"+{added} -{removed}"
