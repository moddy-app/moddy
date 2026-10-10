"""
Shared rendering helpers for automod's member- and server-facing messages.

Keeps the DM, the server alert log and the appeal panels visually consistent:
a long offending message is attached as a ``.txt`` file (Components V2 ``File``)
instead of being silently truncated into a quote block.
"""

from __future__ import annotations

import io
import re

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

# Component machine code → i18n key suffix under ``modules.automod_ai.bareme.*``.
BAREME_LABELS = {
    "plancher": "floor",
    "plancher_perso": "floor_custom",
    "recidive": "recidivism",
    "severite": "severity",
    "confiance": "confidence",
    "veteran": "veteran",
    "compte_recent": "fresh_account",
    "plafond": "ceiling",
    "categorie_desactivee": "disabled_category",
    "borne": "bounds",
    # Heavy sanction downgraded after the mini senior review refused it (§6.3).
    "confirmation_refusee": "unconfirmed",
}


def _comp_field(comp, name):
    """Read a component field whether it's a dataclass or a plain dict."""
    if isinstance(comp, dict):
        return comp.get(name)
    return getattr(comp, name, None)


# --------------------------------------------------------------------------- #
# Infraction labels (built-in i18n names + server-defined custom infractions)
# --------------------------------------------------------------------------- #

_PERSO_TOKEN_RE = re.compile(r"perso:[a-z0-9]{1,12}")


def _safe_name(name: str) -> str:
    """Server-authored text made safe to drop inside a backtick span / a card:
    no backtick (would close the span), no live ``@`` mention."""
    return str(name).replace("`", "'").replace("@", "@\u200b").strip()


def infraction_label(categorie: str, locale: str, infractions_perso=()) -> str:
    """Plain-text, human name of an infraction category.

    * ``perso:<id>`` -> the server's own ``nom`` (server-authored: sanitised, the
      caller wraps it in backticks) — falls back to the raw id when the
      infraction no longer exists;
    * a built-in id -> ``modules.automod_ai.infractions.<id>.name`` (i18n);
    * anything else (or a missing translation) -> the raw category.
    """
    from automod import custom_infractions
    from utils.i18n import t

    if not categorie:
        return ""
    if categorie.startswith(custom_infractions.PERSO_PREFIX):
        entry = custom_infractions.find(infractions_perso, categorie)
        return _safe_name(entry["nom"]) if entry and entry.get("nom") else categorie
    key = f"modules.automod_ai.infractions.{categorie}.name"
    name = t(key, locale=locale)
    return categorie if not name or name.startswith("[") else name


def label_in_text(text: str, locale: str, infractions_perso=()) -> str:
    """Replace every ``perso:<id>`` token of ``text`` by its infraction name."""
    if not text or "perso:" not in text:
        return text
    return _PERSO_TOKEN_RE.sub(
        lambda m: infraction_label(m.group(0), locale, infractions_perso), text)


def bareme_breakdown(composantes, locale: str, infractions_perso=()) -> str:
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
        label = t(f"modules.automod_ai.bareme.{BAREME_LABELS.get(code, code)}", locale=locale)
        if code in ("plancher", "plancher_perso"):
            amount = t("modules.automod_ai.bareme.cran", locale=locale, n=delta)
        else:
            amount = f"{'+' if delta >= 0 else ''}{delta}"
        suffix = f" ({label_in_text(detail, locale, infractions_perso)})" if detail else ""
        lines.append(f"- {label}{suffix} · {amount}")
    return "\n".join(lines)


def sanction_name(actions, locale: str) -> str:
    """Human name of the applied/simulated sanction (most punitive action)."""
    from utils.i18n import t

    actions = actions or []
    for a in ("ban", "mute", "warn"):
        if a in actions:
            return t(f"modules.automod_ai.action.{a}", locale=locale)
    return t("modules.automod_ai.action.supprimer", locale=locale)
