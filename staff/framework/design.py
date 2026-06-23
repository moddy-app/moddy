"""
Standardized Components V2 design helpers for the staff command system.

Every staff command renders through these helpers so the whole staff surface
stays visually consistent: ``### <emoji> Title`` headers, coloured accent bars
on every container, ``-#`` hints, and backticked dynamic values (see
``docs/DESIGN.md``).

The helpers return ready-to-send :class:`BaseView` instances (rule: every view
inherits from ``BaseView``). Commands that need interactive components build a
container with :func:`make_container` and add their own action rows.
"""

from __future__ import annotations

from typing import List, Dict, Optional

import discord
from discord import ui

from config import COLORS
from utils import emojis
from utils.i18n import t
from cogs.error_handler import BaseView


# --- Accent colours --------------------------------------------------------

# Message "kind" -> accent colour. Used for result panels.
KIND_COLOURS = {
    "success": COLORS["success"],
    "error": COLORS["error"],
    "warning": COLORS["warning"],
    "info": COLORS["info"],
    "loading": COLORS["neutral"],
    "neutral": COLORS["neutral"],
    "developer": COLORS["developer"],
}

# Per command-type accent colour, used for the "main" panels of a command so
# each staff department reads at a glance.
TYPE_COLOURS = {
    "d": COLORS["developer"],   # dev
    "t": COLORS["primary"],     # team
    "m": COLORS["premium"] if "premium" in COLORS else COLORS["primary"],
    "mod": COLORS["error"],     # moderation
    "sup": COLORS["info"],      # support
    "com": COLORS["warning"],   # communication
}

KIND_EMOJIS = {
    "success": emojis.DONE,
    "error": emojis.ERROR,
    "warning": emojis.WARNING,
    "info": emojis.INFO,
    "loading": emojis.LOADING,
    "neutral": emojis.INFO,
    "developer": emojis.DEV,
}


def colour(kind) -> discord.Colour:
    """Return a :class:`discord.Colour` for a message kind, COLORS key or hex int."""
    if isinstance(kind, discord.Colour):
        return kind
    if isinstance(kind, int):
        return discord.Colour(kind)
    value = KIND_COLOURS.get(kind) or COLORS.get(kind) or COLORS["neutral"]
    return discord.Colour(value)


def make_container(accent: Optional[object] = "neutral") -> ui.Container:
    """Create a Container with a coloured accent bar.

    ``accent`` may be a kind string ("success", "info", ...), a raw hex int,
    or ``None`` for no accent bar.
    """
    if accent is None:
        return ui.Container()
    accent_colour = accent if isinstance(accent, discord.Colour) else colour(accent)
    return ui.Container(accent_colour=accent_colour)


def title_line(emoji: str, title: str) -> str:
    """Build a ``### <emoji> Title`` header line (DESIGN.md standard)."""
    return f"### {emoji} {title}"


def _add_fields(container: ui.Container, fields: Optional[List[Dict[str, str]]]):
    if not fields:
        return
    container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    for field in fields:
        name = field.get("name", "")
        value = field.get("value", "")
        container.add_item(ui.TextDisplay(f"**{name}**\n{value}" if name else value))


def panel(
    kind: str,
    title: str,
    description: str = "",
    *,
    emoji: Optional[str] = None,
    fields: Optional[List[Dict[str, str]]] = None,
    footer: Optional[str] = None,
    accent: Optional[object] = None,
) -> BaseView:
    """Build a standardized result panel as a :class:`BaseView`.

    ``kind`` drives both the default emoji and the accent colour unless an
    explicit ``emoji`` / ``accent`` is provided.
    """
    view = BaseView()
    header_emoji = emoji or KIND_EMOJIS.get(kind, emojis.INFO)
    container = make_container(accent if accent is not None else kind)
    container.add_item(ui.TextDisplay(title_line(header_emoji, title)))
    if description:
        container.add_item(ui.TextDisplay(description))
    _add_fields(container, fields)
    if footer:
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(f"-# {footer}"))
    view.add_item(container)
    return view


# --- Convenience wrappers (localized chrome handled by callers) -------------

def success(title: str, description: str = "", **kw) -> BaseView:
    return panel("success", title, description, **kw)


def error(title: str, description: str = "", **kw) -> BaseView:
    return panel("error", title, description, **kw)


def info(title: str, description: str = "", **kw) -> BaseView:
    return panel("info", title, description, **kw)


def warning(title: str, description: str = "", **kw) -> BaseView:
    return panel("warning", title, description, **kw)


def loading(title: str, description: str = "", **kw) -> BaseView:
    return panel("loading", title, description, **kw)


def permission_denied(locale: str, reason: str = "") -> BaseView:
    """Standard "permission denied" panel, localized."""
    return panel(
        "error",
        t("staff.common.permission_denied.title", locale=locale),
        reason or t("staff.common.permission_denied.description", locale=locale),
    )


def invalid_usage(locale: str, usage: str) -> BaseView:
    """Standard "invalid usage" panel, localized. ``usage`` is a code snippet."""
    return panel(
        "error",
        t("staff.common.invalid_usage.title", locale=locale),
        f"`{usage}`",
    )
