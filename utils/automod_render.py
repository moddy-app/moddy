"""
Shared rendering helpers for automod's member- and server-facing messages.

Keeps the DM, the server alert log and the appeal panels visually consistent:
a long offending message is attached as a ``.txt`` file (Components V2 ``File``)
instead of being silently truncated into a quote block.
"""

from __future__ import annotations

import io

import discord

# A quoted message longer than this (chars) — or with this many line breaks —
# is attached as a .txt file rather than truncated into the panel.
INLINE_MAX_CHARS = 400
INLINE_MAX_LINES = 6


def is_long(content: str) -> bool:
    """Whether ``content`` should be shipped as a file instead of inline."""
    if not content:
        return False
    return len(content) > INLINE_MAX_CHARS or content.count("\n") >= INLINE_MAX_LINES


def make_text_file(content: str, filename: str) -> discord.File:
    """Build a ``discord.File`` from text (referenced via ``attachment://``)."""
    return discord.File(io.BytesIO((content or "").encode("utf-8")), filename=filename)
