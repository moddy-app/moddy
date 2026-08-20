"""Discord Components V2 primitives and Moddy response builders."""

from dataclasses import dataclass
from typing import Iterable

import discord


# Explicit aliases keep imports familiar while making Components V2 the default.
LayoutView = discord.ui.LayoutView
Container = discord.ui.Container
TextDisplay = discord.ui.TextDisplay
Separator = discord.ui.Separator
ActionRow = discord.ui.ActionRow
Button = discord.ui.Button
Modal = discord.ui.Modal
Label = discord.ui.Label


@dataclass(frozen=True, slots=True)
class Field:
    """A text field rendered inside a Components V2 card."""

    name: str
    value: str


def message(
    title: str,
    description: str,
    *,
    fields: Iterable[Field] = (),
) -> LayoutView:
    """Build a minimal Components V2 message.

    Callers remain responsible for i18n and for supplying Moddy custom emojis
    in ``title`` where required by the design system.
    """

    view = LayoutView()
    container = Container()
    container.add_item(TextDisplay(f"### {title}\n{description}"))
    for field in fields:
        container.add_item(Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(TextDisplay(f"**{field.name}**\n{field.value}"))
    view.add_item(container)
    return view


__all__ = (
    "ActionRow",
    "Button",
    "Container",
    "Field",
    "Label",
    "LayoutView",
    "Modal",
    "Separator",
    "TextDisplay",
    "message",
)
