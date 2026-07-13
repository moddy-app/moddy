"""
Diffuse-harassment (``situation``) SIMULATION card + annotation buttons (S8).

Session 8 ships the ``situation`` feature in **forced shadow mode**: when the
friction machine detects sustained targeting / dogpiling and the mini analyst
confirms a pattern, this card is posted to the alert channel — never a sanction.
It reuses the exact same persistent annotation buttons as the ``dry_run`` shadow
card (:class:`~utils.automod_shadow_views.ShadowAnnotateButton`), which annotate
the underlying ``automod_eval_candidates`` row, so a moderator's ruling feeds a
future "situations" golden set.

The card is rebuilt from the stored candidate row (``verdict.kind == "situation"``)
so it renders identically before and after a bot restart — the classic
persistent-view contract.
"""

from __future__ import annotations

from typing import Any, Dict, List

import discord
from discord import ui

from config import COLORS
from utils.i18n import t
from utils.emojis import SHIELD, SEARCH, GROUPS, WARNING

# How much of the analyst's summary the card shows inline.
_RESUME_MAX = 700

# Situation gravity → card accent colour.
_ACCENT_BY_GRAVITE = {
    "basse": COLORS["neutral"],
    "moyenne": COLORS["warning"],
    "haute": COLORS["error"],
}


def _jump(guild_id, channel_id, message_id) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def render_situation_card(candidate: Dict[str, Any]) -> ui.LayoutView:
    """Build the situation SIMULATION card from a stored candidate row.

    ``candidate`` is the dict returned by ``db.get_eval_candidate`` (or freshly
    inserted) with ``verdict.kind == "situation"``. When ``verdict_humain`` is
    set, the annotation buttons are replaced by the recorded ruling.
    """
    verdict = candidate.get("verdict") or {}
    locale = verdict.get("locale") or "en-US"
    gravite = verdict.get("gravite") or "basse"
    situation = verdict.get("situation") or "rien"
    accent = _ACCENT_BY_GRAVITE.get(gravite, COLORS["warning"])

    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=discord.Colour(accent))

    # Header + SIMULATION badge (shared with the dry_run shadow card).
    container.add_item(ui.TextDisplay(
        f"### {SHIELD} {t('modules.automod.situation.title', locale=locale)}"))
    container.add_item(ui.TextDisplay(
        f"{SEARCH} **{t('modules.automod.shadow.badge', locale=locale)}**"))

    cible = verdict.get("cible")
    type_label = t(f"modules.automod.situation.type.{situation}", locale=locale)
    body = (
        f"- **{t('modules.automod.situation.pattern', locale=locale)} :** {type_label}\n"
        f"- **{t('modules.automod.situation.gravity', locale=locale)} :** `{gravite}`\n"
        f"- **{t('modules.automod.situation.target', locale=locale)} :** "
        f"<@{cible}> (`{cible}`)"
    )
    if verdict.get("resume"):
        resume = str(verdict["resume"])[:_RESUME_MAX]
        body += (f"\n- **{t('modules.automod.situation.summary', locale=locale)} :** "
                 f"{resume}")
    container.add_item(ui.TextDisplay(body))

    # Participants (@mention + role).
    participants = verdict.get("participants") or []
    if participants:
        lines = []
        for p in participants:
            aid = p.get("auteur_id")
            role = p.get("role") or ""
            role_label = t(f"modules.automod.situation.role.{role}", locale=locale) \
                if role else role
            lines.append(f"- <@{aid}> · {role_label}")
        container.add_item(ui.TextDisplay(
            f"{GROUPS} **{t('modules.automod.situation.participants', locale=locale)}**\n"
            + "\n".join(lines)))

    # Key messages (jump links).
    keys: List[str] = verdict.get("messages_cles") or []
    if keys:
        guild_id = candidate.get("guild_id")
        channel_id = candidate.get("channel_id")
        links = " · ".join(
            f"[#{i + 1}]({_jump(guild_id, channel_id, mid)})"
            for i, mid in enumerate(keys[:10]))
        container.add_item(ui.TextDisplay(
            f"-# {t('modules.automod.situation.key_messages', locale=locale)} : {links}"))

    view.add_item(container)

    annotated = candidate.get("verdict_humain")
    candidate_id = str(candidate.get("id")) if candidate.get("id") else ""
    if annotated:
        from utils.automod_shadow_views import _VERDICT_BY_CODE, _CODE_BY_VERDICT
        emoji = _VERDICT_BY_CODE.get(_CODE_BY_VERDICT.get(annotated, ""), (None, "•"))[1]
        by = candidate.get("annotated_by")
        line = t(f"modules.automod.shadow.annotated.{annotated}", locale=locale)
        by_txt = f" — <@{by}>" if by else ""
        container.add_item(ui.TextDisplay(f"{emoji} **{line}**{by_txt}"))
    elif candidate_id:
        from utils.automod_shadow_views import ShadowAnnotateButton
        container.add_item(ui.TextDisplay(
            f"-# {WARNING} {t('modules.automod.situation.shadow_hint', locale=locale)}\n"
            f"-# {t('modules.automod.shadow.annotate_hint', locale=locale)}"))
        row = ui.ActionRow()
        row.add_item(ShadowAnnotateButton("ok", candidate_id, locale))
        row.add_item(ShadowAnnotateButton("fp", candidate_id, locale))
        row.add_item(ShadowAnnotateButton("disp", candidate_id, locale))
        view.add_item(row)

    return view
