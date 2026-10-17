"""Components V2 cards for the internal staff/dev log channels.

One builder replaces the former ``discord.Embed`` logs: a single container
with an accent colour, a ``###`` title, an optional description, ``**name**``
fields and a ``-#`` footer carrying the timestamp.

A Components V2 message cannot carry ``content`` and holds at most 4000
characters of text, so the builder trims fields to fit and callers that need
a ping send it as a separate message (see :func:`send_log_card`).
"""

from __future__ import annotations

import time
from typing import Iterable, Optional, Sequence, Tuple

import discord
from discord import ui

# Total text budget of one V2 message is 4000; keep a margin for separators.
TEXT_BUDGET = 3800

Field = Tuple[str, str]


def _clip(text: str, limit: int) -> str:
    text = str(text)
    if len(text) <= limit:
        return text
    closing = "\n```" if text.count("```") % 2 else ""
    return text[: max(limit - 3 - len(closing), 0)] + "..." + closing


def build_log_card(
    title: str,
    *,
    accent: int,
    description: Optional[str] = None,
    fields: Iterable[Field] = (),
    footer: Optional[str] = None,
    timestamp: bool = True,
) -> ui.LayoutView:
    """Build a log card. Fields are ``(name, value)`` pairs rendered in order."""
    footer_parts = []
    if footer:
        footer_parts.append(footer)
    if timestamp:
        footer_parts.append(f"<t:{int(time.time())}:f>")
    footer_text = f"-# {' • '.join(footer_parts)}" if footer_parts else ""

    header = f"### {title}"
    remaining = TEXT_BUDGET - len(header) - len(footer_text)

    blocks = []
    if description:
        block = _clip(description, min(remaining, 2000))
        remaining -= len(block)
        blocks.append(block)

    field_blocks = []
    field_list: Sequence[Field] = list(fields)
    for index, (name, value) in enumerate(field_list):
        # Share what is left fairly between the fields still to come.
        share = max(remaining // (len(field_list) - index), 80)
        text = f"**{name}**\n{_clip(value, share - len(name) - 6)}"
        remaining -= len(text)
        field_blocks.append(text)

    container = ui.Container(accent_colour=discord.Colour(accent))
    container.add_item(ui.TextDisplay(header))
    for block in blocks:
        container.add_item(ui.TextDisplay(block))
    if field_blocks:
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        for block in field_blocks:
            container.add_item(ui.TextDisplay(block))
    if footer_text:
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(footer_text))

    view = ui.LayoutView()
    view.add_item(container)
    return view


async def send_log_card(channel, view: ui.LayoutView, *, ping: Optional[str] = None) -> None:
    """Send a card, with an optional ping as its own message beforehand."""
    if ping:
        await channel.send(content=ping)
    await channel.send(view=view)
