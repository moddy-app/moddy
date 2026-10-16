"""
Automod alert card + moderator approval buttons.

One renderer, :func:`render_alert_card`, draws the card the automod posts in its
alert channel, in every state of an incident:

* ``done``     — actions were executed (what was applied / what failed shown);
* ``pending``  — an infraction with ``require_approval``: the card waits for a
  moderator and carries the Approve / Reject buttons;
* ``approved`` / ``rejected`` / ``expired`` — the pending card once resolved
  (buttons gone, who decided shown).

The buttons are :class:`discord.ui.DynamicItem` whose ``custom_id`` carries the
approval id (``moddy:automod:approval:<ok|no>:<id>``); everything else is read
back from ``automod_pending_approvals`` on every click, so they survive a
restart and nothing lives in memory (docs/guides/PERSISTENT_VIEWS.md).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

import discord
from discord import ui

from automod import actions as act
from automod import approvals
from cogs.error_handler import BaseView
from config import COLORS
from utils.automod_render import (
    action_label, actions_text, bareme_breakdown, failed_lines, sanction_name,
)
from utils.emojis import SHIELD, DONE, UNDONE, get_sanction_accent
from utils.i18n import t, i18n

logger = logging.getLogger("moddy.automod_approval")

_BASE = "modules.automod_ai"


def _guarded(callback):
    """Funnel a dynamic-item callback error to the central handler."""
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as e:  # noqa: BLE001
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, e, self.__class__.__name__)
    return wrapper


def accent_for(status: str, outcome: approvals.IncidentOutcome) -> int:
    """Card accent: yellow while pending / when something failed, grey when
    rejected or expired, otherwise the colour of the applied sanction."""
    if status == "pending" or (status in ("done", "approved") and outcome.failed):
        return COLORS["warning"]
    if status in ("rejected", "expired"):
        return COLORS["neutral"]
    return get_sanction_accent(outcome.primary_action) if outcome.primary_action \
        else COLORS["warning"]


def render_alert_card(*, locale: str, decision, bareme, snapshot: Dict[str, Any],
                      outcome: Optional[approvals.IncidentOutcome] = None,
                      status: str = "done", planned=None, seconds=None,
                      infractions_perso=(), approval_id: Optional[int] = None,
                      decided_by: Optional[int] = None, deleted: bool = False,
                      text_file: Optional[str] = None,
                      image_name: Optional[str] = None) -> ui.LayoutView:
    """Build the alert card (see module docstring for the ``status`` values)."""
    from utils.automod_render import infraction_label  # noqa: F401  (kept for parity)
    outcome = outcome or approvals.IncidentOutcome()
    accent = accent_for(status, outcome)

    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=discord.Colour(accent))
    title_key = "approval.title" if status != "done" else "log.title"
    container.add_item(ui.TextDisplay(f"### {SHIELD} {t(f'{_BASE}.{title_key}', locale=locale)}"))

    if status == "pending":
        container.add_item(ui.TextDisplay(f"**{t(f'{_BASE}.approval.pending', locale=locale)}**"))
    elif status in ("approved", "rejected"):
        icon = DONE if status == "approved" else UNDONE
        by = f" — <@{decided_by}>" if decided_by else ""
        container.add_item(ui.TextDisplay(
            f"{icon} **{t(f'{_BASE}.approval.{status}', locale=locale)}**{by}"))
    elif status == "expired":
        container.add_item(ui.TextDisplay(
            f"{UNDONE} **{t(f'{_BASE}.approval.expired', locale=locale)}**"))

    author_id = decision.auteur_id
    shown = outcome.applied if status in ("done", "approved") else (planned or [])
    actions_line_key = "log.actions" if status in ("done", "approved") else "log.planned"
    actions_txt = actions_text(shown, locale, seconds) or \
        t(f"{_BASE}.log.detection_only", locale=locale)
    body = (
        f"- **{t(f'{_BASE}.log.author', locale=locale)} :** <@{author_id}> (`{author_id}`)\n"
        f"- **{t(f'{_BASE}.log.channel', locale=locale)} :** <#{snapshot.get('channel_id')}>\n"
        f"- **{t(f'{_BASE}.{actions_line_key}', locale=locale)} :** {actions_txt}\n"
        f"- **{t(f'{_BASE}.log.reason', locale=locale)} :** {decision.raison or '—'}"
    )
    if decision.explication:
        body += (f"\n- **{t(f'{_BASE}.log.explanation', locale=locale)} :** "
                 f"{decision.explication}")
    if outcome.case_ref:
        body += f"\n- **{t(f'{_BASE}.log.case', locale=locale)} :** `{outcome.case_ref}`"
    container.add_item(ui.TextDisplay(body))

    if outcome.failed and status in ("done", "approved"):
        container.add_item(ui.TextDisplay(
            f"**{t(f'{_BASE}.log.not_applied', locale=locale)}**\n"
            f"{failed_lines(outcome.failed, locale)}"))

    if bareme is not None and bareme.composantes:
        header = t(f"{_BASE}.bareme.title", locale=locale,
                   sanction=sanction_name(bareme.actions, locale), cran=bareme.cran)
        container.add_item(ui.TextDisplay(
            f"**{header}**\n{bareme_breakdown(bareme.composantes, locale, infractions_perso)}"))
        if bareme.needs_review:
            downgraded = any(c.code == "confirmation_refusee" for c in bareme.composantes)
            hint_key = "bareme.review_hint_unconfirmed" if downgraded else "bareme.review_hint"
            container.add_item(ui.TextDisplay(f"-# {t(f'{_BASE}.{hint_key}', locale=locale)}"))

    if status == "pending" and approval_id is not None:
        container.add_item(ui.TextDisplay(
            f"-# {t(f'{_BASE}.approval.hint', locale=locale)}"))
        row = ui.ActionRow()
        row.add_item(ApprovalButton("ok", approval_id, locale))
        row.add_item(ApprovalButton("no", approval_id, locale))
        container.add_item(row)
    elif outcome.primary_action and status in ("done", "approved"):
        container.add_item(ui.TextDisplay(
            f"-# {t(f'{_BASE}.log.appeal_hint', locale=locale)}"))
    view.add_item(container)

    # The judged content, spoilered (long text rides as a file, images as media).
    content = snapshot.get("content") or ""
    meta = f"-# {t(f'{_BASE}.log.message_id', locale=locale)} : ``{snapshot.get('message_id')}``"
    if deleted:
        meta += f" · {t(f'{_BASE}.log.deleted', locale=locale)}"
    quote = ui.Container(accent_colour=discord.Colour(accent), spoiler=True)
    head = f"**{snapshot.get('author_name') or author_id}** — <t:{snapshot.get('ts', 0)}:S>"
    if text_file:
        quote.add_item(ui.TextDisplay(head))
        quote.add_item(ui.File(f"attachment://{text_file}"))
        quote.add_item(ui.TextDisplay(meta))
    else:
        quote.add_item(ui.TextDisplay(f"{head}\n> {content or '*…*'}\n{meta}"))
    if image_name:
        quote.add_item(ui.MediaGallery(discord.MediaGalleryItem(
            f"attachment://{image_name}", spoiler=True)))
    view.add_item(quote)
    return view


# --------------------------------------------------------------------------- #
# Approve / Reject buttons (DynamicItem, persistent)
# --------------------------------------------------------------------------- #

class ApprovalButton(
    ui.DynamicItem[ui.Button],
    template=r"moddy:automod:approval:(?P<verb>ok|no):(?P<aid>\d+)",
):
    """Approve (``ok``) or Reject (``no``) button of a pending sanction card."""

    def __init__(self, verb: str, approval_id: int, locale: str = "en-US"):
        approve = verb == "ok"
        super().__init__(ui.Button(
            label=t(f"{_BASE}.approval.button_{verb}", locale=locale)[:80],
            style=discord.ButtonStyle.success if approve else discord.ButtonStyle.danger,
            emoji=discord.PartialEmoji.from_str(DONE if approve else UNDONE),
            custom_id=f"moddy:automod:approval:{verb}:{approval_id}",
        ))
        self.verb = verb
        self.approval_id = int(approval_id)

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["verb"], int(match["aid"]))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        # Everything is re-derived from the interaction and the DB row: after a
        # restart this item is rebuilt from the custom_id alone.
        from utils.components_v2 import create_error_message
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)

        async def refuse(key: str) -> None:
            await interaction.response.send_message(
                view=create_error_message(
                    t(f"{_BASE}.approval.error_title", locale=locale),
                    t(f"{_BASE}.approval.{key}", locale=locale)),
                ephemeral=True)

        row = await bot.db.get_automod_approval(self.approval_id)
        if not row or row["guild_id"] != interaction.guild_id:
            return await refuse("gone")
        if row["status"] != "pending":
            return await refuse("already_handled")
        payload = row["payload"] or {}
        if not approvals.can_resolve(
                getattr(interaction.user, "guild_permissions", None),
                approvals.sanction_actions_of(payload)):
            return await refuse("no_perms")

        mm = getattr(bot, "module_manager", None)
        module = await mm.get_module_instance(interaction.guild_id, "automod_ai") if mm else None
        if module is None:
            return await refuse("unavailable")

        await interaction.response.defer()
        expired = approvals.is_expired(row.get("expires_at"))
        status = "expired" if expired else ("approved" if self.verb == "ok" else "rejected")
        claimed = await bot.db.claim_automod_approval(
            self.approval_id, status, interaction.user.id)
        if claimed is None:     # somebody else was faster
            await interaction.followup.send(
                view=create_error_message(
                    t(f"{_BASE}.approval.error_title", locale=locale),
                    t(f"{_BASE}.approval.already_handled", locale=locale)),
                ephemeral=True)
            return
        if status == "approved":
            view = await module.apply_approval(claimed, interaction.user)
        else:
            view = await module.close_approval(claimed, status, interaction.user)
        await interaction.edit_original_response(view=view)


class AutomodApprovalPersistence(BaseView):
    """Marker view: registers the approval dynamic item at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(ApprovalButton)
