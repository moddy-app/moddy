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


# --------------------------------------------------------------------------- #
# Barème breakdown rendering (shared by the alert card + the shadow card)
# --------------------------------------------------------------------------- #

# Component machine code → i18n key suffix under ``modules.automod.bareme.*``.
BAREME_LABELS = {
    "plancher": "floor",
    "recidive": "recidivism",
    "severite": "severity",
    "confiance": "confidence",
    "veteran": "veteran",
    "compte_recent": "fresh_account",
    "plafond": "ceiling",
    "categorie_desactivee": "disabled_category",
    "borne": "bounds",
}


def _comp_field(comp, name):
    """Read a component field whether it's a dataclass or a plain dict."""
    if isinstance(comp, dict):
        return comp.get(name)
    return getattr(comp, name, None)


def bareme_breakdown(composantes, locale: str) -> str:
    """Localized, line-by-line explanation of how the cran was reached.

    ``composantes`` may be :class:`automod.bareme.Composante` objects or the
    plain dicts stored on the case evidence / eval candidate — both render the
    same, so the live alert card and a rebuilt (post-restart / shadow) card match.
    """
    from utils.i18n import t

    lines = []
    for c in composantes:
        code = _comp_field(c, "code") or ""
        delta = _comp_field(c, "delta") or 0
        detail = _comp_field(c, "detail") or ""
        label = t(f"modules.automod.bareme.{BAREME_LABELS.get(code, code)}", locale=locale)
        if code == "plancher":
            amount = t("modules.automod.bareme.cran", locale=locale, n=delta)
        else:
            amount = f"{'+' if delta >= 0 else ''}{delta}"
        suffix = f" ({detail})" if detail else ""
        lines.append(f"- {label}{suffix} · {amount}")
    return "\n".join(lines)


def sanction_name(actions, locale: str) -> str:
    """Human name of the applied/simulated sanction (most punitive action)."""
    from utils.i18n import t

    actions = actions or []
    for a in ("ban", "mute", "warn"):
        if a in actions:
            return t(f"modules.automod.action.{a}", locale=locale)
    return t("modules.automod.action.supprimer", locale=locale)
