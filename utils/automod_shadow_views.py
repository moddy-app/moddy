"""
Automod shadow-mode (``dry_run``) simulation card + annotation buttons.

When a guild runs automod in **shadow mode**, a sanctionnable decision is not
applied: instead a **SIMULATION** card is posted to the alert channel showing the
sanction that *would* have been taken, plus three annotation buttons —
**✅ Correct**, **❌ Faux positif**, **⚠️ Disproportionné**. Each click records a
moderator's judgement into ``automod_eval_candidates``, feeding the offline
golden set (``automod/eval``) and, later, per-server precedents (session 7).

Persistence
-----------
The buttons are :class:`discord.ui.DynamicItem`; their ``custom_id`` encodes the
candidate id, so they survive a bot restart with no in-memory view kept alive
(registered via :class:`ShadowAnnotationPersistence` in
``utils/persistent_views.py``). Everything a rebuilt card needs is read back from
the candidate row in the DB — the classic persistent-view contract.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import discord
from discord import ui

from cogs.error_handler import BaseView
from utils.i18n import t, i18n
from utils.emojis import SHIELD, SEARCH, DONE, UNDONE, WARNING, get_sanction_accent
from utils.automod_render import bareme_breakdown, sanction_name

logger = logging.getLogger("moddy.automod_shadow")

_UUID = r"[0-9a-fA-F-]{36}"

# Button code → (human verdict stored in DB, emoji).
_VERDICT_BY_CODE = {
    "ok": ("correct", DONE),
    "fp": ("faux_positif", UNDONE),
    "disp": ("disproportionne", WARNING),
}
_CODE_BY_VERDICT = {v[0]: k for k, v in _VERDICT_BY_CODE.items()}

# How much of the offending message the diagnostic card shows inline.
_PREVIEW_MAX = 500


def _guarded(callback):
    """Funnel a dynamic-item callback error to the central handler (no live view)."""
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as e:  # noqa: BLE001
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, e, self.__class__.__name__)
    return wrapper


# --------------------------------------------------------------------------- #
# Card rendering (from a stored candidate row → identical before/after restart)
# --------------------------------------------------------------------------- #

def render_shadow_card(candidate: Dict[str, Any]) -> ui.LayoutView:
    """Build the SIMULATION card LayoutView from a candidate row.

    ``candidate`` is the dict returned by ``db.get_eval_candidate`` (or freshly
    inserted). When ``verdict_humain`` is set, the annotation buttons are
    replaced by a single line recording the moderator's ruling.
    """
    verdict = candidate.get("verdict") or {}
    bareme = candidate.get("bareme") or {}
    locale = verdict.get("locale") or "en-US"
    actions = bareme.get("actions") or verdict.get("actions") or []
    accent = get_sanction_accent(_primary_action(actions))

    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=discord.Colour(accent))

    # Header + SIMULATION badge.
    container.add_item(ui.TextDisplay(
        f"### {SHIELD} {t('modules.automod.log.title', locale=locale)}"))
    container.add_item(ui.TextDisplay(
        f"{SEARCH} **{t('modules.automod.shadow.badge', locale=locale)}**"))

    author_id = candidate.get("author_id")
    channel_id = candidate.get("channel_id")
    body = (
        f"- **{t('modules.automod.log.author', locale=locale)} :** "
        f"<@{author_id}> (`{author_id}`)\n"
        f"- **{t('modules.automod.log.channel', locale=locale)} :** <#{channel_id}>\n"
        f"- **{t('modules.automod.shadow.would_apply', locale=locale)} :** "
        f"{sanction_name(actions, locale)}\n"
        f"- **{t('modules.automod.log.reason', locale=locale)} :** "
        f"{verdict.get('raison') or '—'}"
    )
    if verdict.get("explication"):
        body += (f"\n- **{t('modules.automod.log.explanation', locale=locale)} :** "
                 f"{verdict['explication']}")
    container.add_item(ui.TextDisplay(body))

    # Barème breakdown (the would-be sanction, explained line by line).
    composantes = bareme.get("composantes") or []
    if composantes:
        header = t("modules.automod.bareme.title", locale=locale,
                   sanction=sanction_name(actions, locale),
                   cran=bareme.get("cran", 0))
        container.add_item(ui.TextDisplay(
            f"**{header}**\n{bareme_breakdown(composantes, locale)}"))

    # Offending message (inline preview, spoilered).
    contenu = candidate.get("contenu") or ""
    preview = contenu[:_PREVIEW_MAX] + ("…" if len(contenu) > _PREVIEW_MAX else "")
    quote = ui.Container(accent_colour=discord.Colour(accent), spoiler=True)
    quote.add_item(ui.TextDisplay(
        f"> {preview or '*…*'}\n"
        f"-# {t('modules.automod.log.message_id', locale=locale)} : "
        f"``{candidate.get('message_id')}``"))

    view.add_item(container)
    view.add_item(quote)

    annotated = candidate.get("verdict_humain")
    candidate_id = str(candidate.get("id")) if candidate.get("id") else ""
    if annotated:
        # Resolved: show who ruled what, no buttons.
        emoji = _VERDICT_BY_CODE.get(_CODE_BY_VERDICT.get(annotated, ""), (None, "•"))[1]
        by = candidate.get("annotated_by")
        line = t(f"modules.automod.shadow.annotated.{annotated}", locale=locale)
        by_txt = f" — <@{by}>" if by else ""
        container.add_item(ui.TextDisplay(f"{emoji} **{line}**{by_txt}"))
    elif candidate_id:
        container.add_item(ui.TextDisplay(
            f"-# {t('modules.automod.shadow.annotate_hint', locale=locale)}"))
        row = ui.ActionRow()
        row.add_item(ShadowAnnotateButton("ok", candidate_id, locale))
        row.add_item(ShadowAnnotateButton("fp", candidate_id, locale))
        row.add_item(ShadowAnnotateButton("disp", candidate_id, locale))
        view.add_item(row)

    return view


def _primary_action(actions: List[str]) -> Optional[str]:
    for a in ("ban", "mute", "warn"):
        if a in (actions or []):
            return a
    return None


def _render_for(candidate: Dict[str, Any]) -> ui.LayoutView:
    """Pick the card renderer for a candidate row (sanction vs situation).

    A session-8 diffuse-harassment candidate (``source == "situation"`` /
    ``verdict.kind == "situation"``) uses its own card; everything else is a
    ``dry_run`` sanction simulation.
    """
    verdict = candidate.get("verdict") or {}
    if candidate.get("source") == "situation" or verdict.get("kind") == "situation":
        from utils.automod_situation_views import render_situation_card
        return render_situation_card(candidate)
    return render_shadow_card(candidate)


# --------------------------------------------------------------------------- #
# Annotation button (DynamicItem, persistent)
# --------------------------------------------------------------------------- #

class ShadowAnnotateButton(
    ui.DynamicItem[ui.Button],
    template=rf"moddy:amev:(?P<code>ok|fp|disp):(?P<cand>{_UUID})",
):
    """One of the ✅ / ❌ / ⚠️ annotation buttons on a shadow simulation card."""

    _STYLE = {
        "ok": discord.ButtonStyle.success,
        "fp": discord.ButtonStyle.danger,
        "disp": discord.ButtonStyle.secondary,
    }

    def __init__(self, code: str, candidate_id: str, locale: str = "en-US"):
        human, emoji = _VERDICT_BY_CODE[code]
        super().__init__(
            ui.Button(
                label=t(f"modules.automod.shadow.button.{code}", locale=locale)[:80],
                style=self._STYLE[code],
                emoji=discord.PartialEmoji.from_str(emoji),
                custom_id=f"moddy:amev:{code}:{candidate_id}",
            )
        )
        self.code = code
        self.candidate_id = candidate_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["code"], match["cand"])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        bot = interaction.client

        # Only guild moderators may annotate (these live in the alert channel).
        perms = getattr(interaction.user, "guild_permissions", None)
        if perms is None or not perms.manage_messages:
            from utils.components_v2 import create_error_message
            await interaction.response.send_message(
                view=create_error_message(
                    t("modules.automod.shadow.error.title", locale=locale),
                    t("modules.automod.shadow.error.no_perms", locale=locale)),
                ephemeral=True)
            return

        verdict_humain = _VERDICT_BY_CODE[self.code][0]
        row = await bot.db.annotate_eval_candidate(
            self.candidate_id, verdict_humain, annotated_by=interaction.user.id)
        if not row:
            from utils.components_v2 import create_error_message
            await interaction.response.send_message(
                view=create_error_message(
                    t("modules.automod.shadow.error.title", locale=locale),
                    t("modules.automod.shadow.error.gone", locale=locale)),
                ephemeral=True)
            return

        # Session 7: a ✅/❌ ruling on a shadow card is a human precedent — feed it
        # so the automod learns this server's local culture. "disproportionné" is
        # a barème signal, not a sanctionnable/not ruling, so it is not a precedent.
        await self._feed_precedent(bot, row)

        # Re-render the card in place (buttons → the recorded ruling). A session-8
        # "situation" candidate uses its own card renderer.
        await interaction.response.edit_message(view=_render_for(row))

    @staticmethod
    async def _feed_precedent(bot, row: Dict[str, Any]) -> None:
        """Record a server precedent from a shadow-card ruling (best-effort)."""
        from automod import constants as ac
        # A session-8 "situation" candidate is a multi-message PATTERN ruling, not
        # a single-message sanctionnable/not call — it is not a server precedent.
        if row.get("source") == "situation":
            return
        mapping = {
            "correct": (ac.PRECEDENT_SANCTIONNABLE, ac.PRECEDENT_SOURCE_BOUTON_OK),
            "faux_positif": (ac.PRECEDENT_NON_SANCTIONNABLE, ac.PRECEDENT_SOURCE_BOUTON_FP),
        }
        entry = mapping.get(row.get("verdict_humain"))
        svc = getattr(bot, "precedents", None)
        if entry is None or svc is None:
            return
        verdict_humain, source = entry
        verdict = row.get("verdict") or {}
        try:
            await svc.record(
                int(row.get("guild_id")),
                row.get("contenu") or "",
                verdict_humain,
                source=source,
                categorie=verdict.get("categorie") or "",
                gravite=verdict.get("gravite") or "",
            )
        except Exception as e:  # noqa: BLE001 — precedent feeding is best-effort
            logger.debug("shadow precedent feed failed: %s", e)


class ShadowAnnotationPersistence(BaseView):
    """Marker view: registers the shadow annotation dynamic item at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(ShadowAnnotateButton)
