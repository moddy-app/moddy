"""
Appeal UI — sanction-DM appeal buttons + reviewer panels (Components/Modals V2).

When automod sanctions a member, the bot DMs them a notice (like a manual mod
action) carrying two appeal buttons:

* **Faire appel au serveur** — routed to the guild's moderators (reviewed in the
  guild's automod alert channel).
* **Faire appel à l'équipe Moddy** — routed to the Moddy team (reviewed in the
  team appeal channel, ``config.MODDY_APPEAL_CHANNEL_ID``).

A reviewer **Claims** the appeal (panel turns from #3661FF to yellow), can pull a
server **Invite** to investigate, then **Accept** (→ full cancellation or *modify
the case* via a Modal V2) or **Decline**. The appellant can never review their
own appeal. The decision is binding and is applied by
:class:`services.appeal_service.AppealService`.

Persistence
-----------
Every button is a :class:`discord.ui.DynamicItem` whose ``custom_id`` encodes the
ids it needs (case / sanction / appeal). They survive restarts via
``bot.add_dynamic_items(...)`` (registered in ``utils/persistent_views.py``) — no
in-memory view has to be kept alive. Modals are one-shot (opened from a live
interaction) and use the Modals V2 ``Label``-wrapping form.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import discord
from discord import ui

from cogs.error_handler import BaseView, BaseModal
from utils.i18n import t, i18n
from utils.emojis import (
    SHIELD, WARNING, DONE, ERROR, UNDONE, EDIT, TIME, GROUPS, MESSAGE, STAFF,
    MODDY, INFO, LEGAL, BACK, HAND, LINK, PENDING, SANCTION_ACTION_EMOJIS,
    get_sanction_dm_emoji, get_sanction_accent,
)
from utils.automod_render import is_long, make_text_file

logger = logging.getLogger("moddy.appeal_views")

# Public case URL (same shape as the manual sanction DMs).
_CASE_URL = "https://moddy.app/cases?{ref}"

# UUID fragment used in custom_id templates.
_UUID = r"[0-9a-fA-F-]{36}"

# Sanction actions a reviewer may transform an automod sanction into.
_TRANSFORM_ACTIONS = ["warn", "mute", "kick", "ban"]

# Status → (emoji, i18n key) for rendering an appeal outcome.
_STATUS_RENDER = {
    "pending": (PENDING, "modules.automod.appeal.status.pending"),
    "accepted": (DONE, "modules.automod.appeal.status.accepted"),
    "refused": (UNDONE, "modules.automod.appeal.status.refused"),
    "transformed": (EDIT, "modules.automod.appeal.status.transformed"),
    "cancelled": (UNDONE, "modules.automod.appeal.status.cancelled"),
}

# Status → accent colour for the appeal-status panel on the member DM.
_STATUS_ACCENT = {
    "pending": 0x3661FF,
    "accepted": 0x57F287,
    "refused": 0xED4245,
    "transformed": 0xFEE75C,
    "cancelled": 0x99AAB5,
}


def _action_emoji(action: Optional[str]) -> str:
    return SANCTION_ACTION_EMOJIS.get(action or "", WARNING)


# =========================================================================== #
# Render helpers (used by the module + the appeal service)
# =========================================================================== #

def build_sanction_dm_view(
    *,
    locale: str,
    guild_name: str,
    case_ref: str,
    action: str,
    reason: str,
    explication: str,
    case_id: str,
    sanction_id: str,
    guild_id: Optional[int] = None,
    expires_at=None,
    proof_text: Optional[str] = None,
    proof_author: Optional[str] = None,
    proof_message_id: Optional[str] = None,
    proof_ts: Optional[int] = None,
    appeal_status: Optional[str] = None,
    appeal_route: Optional[str] = None,
    decided: Optional[dict] = None,
):
    """The DM a sanctioned member receives — styled like a manual sanction DM.

    Returns ``(view, files)``: ``files`` carries the offending message as a
    ``.txt`` attachment when it is too long to quote inline. The accent colour
    and the title icon follow the sanction (orange warn / red ban…).

    Three states share one layout:
    * no appeal yet → the sanction block + the two appeal buttons;
    * appeal pending/decided → a coloured status panel (no buttons), and when
      the appeal was accepted the sanction block is struck through.
    """
    view = BaseView()
    files = []
    accent = get_sanction_accent(action)
    icon = get_sanction_dm_emoji(action)
    struck = (decided or {}).get("status") == "accepted"

    def _s(text: str) -> str:
        return f"~~{text}~~" if struck else text

    # — Sanction block —
    _title_key = f"modules.automod.dm.title_{action}"
    title = t(_title_key, locale=locale)
    if title.startswith("["):  # missing key → generic fallback
        title = t("modules.automod.dm.title", locale=locale)
    expires_txt = (f"<t:{int(expires_at.timestamp())}:R>" if expires_at
                   else t("modules.automod.dm.permanent", locale=locale))
    lines = [
        f"### {icon} {title}",
        f"- **{t('modules.automod.dm.reason', locale=locale)}:** {reason or '—'}",
    ]
    if explication:
        lines.append(f"- **{t('modules.automod.dm.explanation', locale=locale)}:** {explication}")
    lines.append(
        f"- **{t('modules.automod.dm.responsible', locale=locale)}:** "
        f"{t('modules.automod.dm.responsible_value', locale=locale)}")
    lines.append(f"- **{t('modules.automod.dm.expires', locale=locale)}:** {expires_txt}")

    c = ui.Container(accent_colour=discord.Colour(accent))
    c.add_item(ui.TextDisplay(_s("\n".join(lines))))
    case_link = f"[``{case_ref}``](<{_CASE_URL.format(ref=case_ref)}>)"
    footer = f"- **{t('modules.automod.dm.case', locale=locale)}:** {case_link}"
    if guild_id:
        footer += (f"\n-# {t('modules.automod.dm.sent_by', locale=locale, guild=guild_name, guild_id=guild_id)}")
    c.add_item(ui.TextDisplay(footer))

    if not appeal_status:
        c.add_item(ui.TextDisplay(f"-# {t('modules.automod.dm.appeal_hint', locale=locale)}"))
    view.add_item(c)

    # — Appeal status panel (coloured) or the appeal buttons —
    if appeal_status:
        emoji, key = _STATUS_RENDER.get(appeal_status, _STATUS_RENDER["pending"])
        panel = ui.Container(accent_colour=discord.Colour(_STATUS_ACCENT.get(appeal_status, 0x3661FF)))
        panel.add_item(ui.TextDisplay(
            f"**{t('modules.automod.dm.appeal_state', locale=locale)}:**\n"
            f"{emoji} {t(key, locale=locale)}"
        ))
        route = (decided or {}).get("route") or appeal_route
        extra = []
        if route:
            route_key = ("modules.automod.dm.appeal_team" if route == "team"
                         else "modules.automod.dm.appeal_server")
            extra.append(f"-# {t(route_key, locale=locale)}")
        if (decided or {}).get("new_action"):
            extra.append(
                f"-# → `{t('modules.automod.action.' + decided['new_action'], locale=locale)}`")
        if extra:
            panel.add_item(ui.TextDisplay("\n".join(extra)))
        view.add_item(panel)
    else:
        row = ui.ActionRow()
        row.add_item(AppealNewButton("s", case_id, sanction_id, locale=locale))
        row.add_item(AppealNewButton("t", case_id, sanction_id, locale=locale))
        view.add_item(row)

    # — Offending message (proof), spoilered; long content → file —
    if proof_text:
        meta = (f"-# {t('modules.automod.log.message_id', locale=locale)} : ``{proof_message_id}``"
                if proof_message_id else "")
        ts_part = f" — <t:{proof_ts}:S>" if proof_ts else ""
        head = f"**{proof_author or '—'}**{ts_part}"
        proof = ui.Container(spoiler=True)
        if is_long(proof_text):
            fname = f"message_{proof_message_id or 'content'}.txt"
            files.append(make_text_file(proof_text, fname))
            proof.add_item(ui.TextDisplay(head))
            proof.add_item(ui.File(f"attachment://{fname}"))
            if meta:
                proof.add_item(ui.TextDisplay(meta))
        else:
            body = f"{head}\n> {proof_text}"
            if meta:
                body += f"\n{meta}"
            proof.add_item(ui.TextDisplay(body))
        view.add_item(proof)

    return view, files


# Unclaimed panels are blurple-ish (#3661FF); a claimed panel turns yellow.
_REVIEW_UNCLAIMED = 0x3661FF
_REVIEW_CLAIMED = 0xFEE75C


def build_review_view(
    *,
    locale: str,
    route: str,
    appeal_id: str,
    subject: dict,
    guild: dict,
    case: dict,
    appeal_reason: str,
    claimed_by: Optional[int] = None,
    technical: Optional[dict] = None,
    proof: Optional[dict] = None,
    decided: Optional[dict] = None,
):
    """The reviewer panel (server mods or Moddy team).

    Returns ``(view, files)``. The accent is ``#3661FF`` while unclaimed and
    yellow once a reviewer claims it; a decided panel shows the outcome and
    drops the buttons. The server panel omits the technical identifiers. The
    offending message is attached as a ``.txt`` file when too long.
    """
    is_team = route == "team"
    files = []
    if decided:
        accent = _STATUS_ACCENT.get(decided.get("status"), _REVIEW_CLAIMED)
    else:
        accent = _REVIEW_CLAIMED if claimed_by else _REVIEW_UNCLAIMED

    view = BaseView()
    c = ui.Container(accent_colour=discord.Colour(accent))
    head = (t("modules.automod.appeal.review.title_team", locale=locale) if is_team
            else t("modules.automod.appeal.review.title_server", locale=locale))
    c.add_item(ui.TextDisplay(f"### {SHIELD} {head}"))

    # User
    c.add_item(ui.TextDisplay(
        f"**{t('modules.automod.appeal.review.user', locale=locale)}:**\n"
        f"- {t('modules.automod.appeal.review.display_name', locale=locale)}: {subject.get('display') or '—'}\n"
        f"- {t('modules.automod.appeal.review.username', locale=locale)}: {subject.get('username') or '—'}\n"
        f"- {t('modules.automod.appeal.review.id', locale=locale)}: ``{subject.get('id')}``"
    ))

    # Guild (members only on the team panel)
    guild_lines = [
        f"**{t('modules.automod.appeal.review.guild', locale=locale)}:**",
        f"- {t('modules.automod.appeal.review.name', locale=locale)}: {guild.get('name') or '—'}",
        f"- {t('modules.automod.appeal.review.id', locale=locale)}: ``{guild.get('id')}``",
    ]
    if is_team and guild.get("members") is not None:
        guild_lines.append(
            f"- {t('modules.automod.appeal.review.members', locale=locale)}: {guild['members']}")
    c.add_item(ui.TextDisplay("\n".join(guild_lines)))

    # Case
    actions = case.get("actions") or []
    actions_txt = ", ".join(t("modules.automod.action." + a, locale=locale) for a in actions) or "—"
    case_lines = [
        f"**{t('modules.automod.appeal.review.case', locale=locale)}:**",
        f"- REF: ``{case.get('ref')}``",
        f"- {t('modules.automod.appeal.review.actions', locale=locale)}: {actions_txt}",
        f"- {t('modules.automod.appeal.review.motive', locale=locale)}: {case.get('reason') or '—'}",
    ]
    if case.get("explication"):
        case_lines.append(
            f"- {t('modules.automod.appeal.review.explanation', locale=locale)}: {case['explication']}")
    if case.get("created_ts"):
        case_lines.append(
            f"- {t('modules.automod.appeal.review.created', locale=locale)}: <t:{case['created_ts']}:R>")
    c.add_item(ui.TextDisplay("\n".join(case_lines)))

    # User's appeal text
    c.add_item(ui.TextDisplay(
        f"{MESSAGE} **{t('modules.automod.appeal.review.user_says', locale=locale)}:**\n"
        f">>> {(appeal_reason or '—')[:1000]}"
    ))

    # Technical info — team only
    if is_team and technical:
        c.add_item(ui.TextDisplay(
            f"**{t('modules.automod.appeal.review.technical', locale=locale)}:**\n"
            f"- {t('modules.automod.appeal.review.case_uuid', locale=locale)}: ``{technical.get('case_uuid')}``\n"
            f"- {t('modules.automod.appeal.review.appeal_id', locale=locale)}: ``{technical.get('appeal_id')}``"
        ))

    # Claim / outcome footer
    if decided:
        emoji, key = _STATUS_RENDER.get(decided["status"], (INFO, "modules.automod.appeal.status.pending"))
        line = f"{emoji} **{t('modules.automod.appeal.review.outcome', locale=locale)}:** {t(key, locale=locale)}"
        if decided.get("new_action"):
            line += f" → `{t('modules.automod.action.' + decided['new_action'], locale=locale)}`"
        c.add_item(ui.TextDisplay(line))
        if decided.get("by_id"):
            c.add_item(ui.TextDisplay(
                f"-# {t('modules.automod.appeal.review.decided_by', locale=locale)} <@{decided['by_id']}>"))
    elif claimed_by:
        c.add_item(ui.TextDisplay(
            f"-# {HAND} {t('modules.automod.appeal.review.claimed_by', locale=locale)} <@{claimed_by}>"))
    view.add_item(c)

    # Proof (spoiler; long → file)
    if proof and proof.get("text"):
        meta = (f"-# {t('modules.automod.log.message_id', locale=locale)} : ``{proof.get('message_id')}``"
                if proof.get("message_id") else "")
        ts_part = f" — <t:{proof['ts']}:S>" if proof.get("ts") else ""
        head_line = f"**{proof.get('author') or '—'}**{ts_part}"
        pc = ui.Container(spoiler=True)
        pc.add_item(ui.TextDisplay(f"**{t('modules.automod.appeal.review.proof', locale=locale)}:**"))
        if is_long(proof["text"]):
            fname = f"proof_{proof.get('message_id') or 'content'}.txt"
            files.append(make_text_file(proof["text"], fname))
            pc.add_item(ui.TextDisplay(head_line))
            pc.add_item(ui.File(f"attachment://{fname}"))
            if meta:
                pc.add_item(ui.TextDisplay(meta))
        else:
            body = f"{head_line}\n> {proof['text']}"
            if meta:
                body += f"\n{meta}"
            pc.add_item(ui.TextDisplay(body))
        view.add_item(pc)

    if decided:
        return view, files

    # Buttons. Row 1: Claim / Invite. Row 2: Accept / Decline (active once claimed).
    claimed = claimed_by is not None
    row1 = ui.ActionRow()
    row1.add_item(AppealClaimButton(appeal_id, claimed=claimed, locale=locale))
    row1.add_item(AppealInviteButton(appeal_id, locale=locale))
    view.add_item(row1)
    row2 = ui.ActionRow()
    row2.add_item(AppealDecisionButton("accept", appeal_id, locale=locale, disabled=not claimed))
    row2.add_item(AppealDecisionButton("decline", appeal_id, locale=locale, disabled=not claimed))
    view.add_item(row2)
    return view, files


# =========================================================================== #
# Modals (Modals V2)
# =========================================================================== #

class AppealReasonModal(BaseModal):
    """Collects the member's appeal reason, then opens the appeal."""

    def __init__(self, route: str, case_id: str, sanction_id: str, locale: str,
                 dm_channel_id: Optional[int] = None, dm_message_id: Optional[int] = None):
        super().__init__(title=t("modules.automod.appeal.modal.title", locale=locale)[:45])
        self.route = route
        self.case_id = case_id
        self.sanction_id = sanction_id
        self.locale = locale
        self.dm_channel_id = dm_channel_id
        self.dm_message_id = dm_message_id
        self.reason = ui.Label(
            text=t("modules.automod.appeal.modal.label", locale=locale)[:45],
            description=t("modules.automod.appeal.modal.desc", locale=locale)[:100],
            component=ui.TextInput(
                style=discord.TextStyle.paragraph,
                max_length=1000,
                required=True,
                placeholder=t("modules.automod.appeal.modal.placeholder", locale=locale)[:100],
            ),
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        bot = interaction.client
        reason = (self.reason.component.value or "").strip()
        # Opening an appeal does real work (DB writes, posting the reviewer
        # panel, editing the DM) that can exceed the 3s interaction window, so
        # acknowledge first and answer with a followup — otherwise the token
        # expires and send_message raises 404 Unknown interaction.
        await interaction.response.defer(ephemeral=True)
        ok, status = await bot.appeals.open_appeal(
            case_id=self.case_id,
            sanction_id=self.sanction_id,
            subject_id=interaction.user.id,
            route="team" if self.route == "t" else "server",
            reason=reason,
            dm_channel_id=self.dm_channel_id,
            dm_message_id=self.dm_message_id,
        )
        if not ok:
            from utils.components_v2 import create_error_message
            await interaction.followup.send(
                view=create_error_message(
                    t("modules.automod.appeal.error.title", locale=self.locale),
                    t(f"modules.automod.appeal.error.{status}", locale=self.locale),
                ),
                ephemeral=True,
            )
            return
        from utils.components_v2 import create_success_message
        key = ("modules.automod.appeal.opened_team" if self.route == "t"
               else "modules.automod.appeal.opened_server")
        await interaction.followup.send(
            view=create_success_message(
                t("modules.automod.appeal.opened_title", locale=self.locale),
                t(key, locale=self.locale),
            ),
            ephemeral=True,
        )


class ModifyCaseModal(BaseModal):
    """Accept → *modify the case*: rewrite the sanction (action + duration), the
    case reason and an optional note. Applied as a binding ``transform``."""

    def __init__(self, appeal_id: str, current_action: str, current_reason: str, locale: str):
        super().__init__(title=t("modules.automod.appeal.modify.title", locale=locale)[:45])
        self.appeal_id = appeal_id
        self.locale = locale
        self.action = ui.Label(
            text=t("modules.automod.appeal.modify.action", locale=locale)[:45],
            component=ui.Select(
                options=[
                    discord.SelectOption(
                        label=t("modules.automod.action." + a, locale=locale),
                        value=a,
                        emoji=discord.PartialEmoji.from_str(_action_emoji(a)),
                        default=(a == current_action),
                    )
                    for a in _TRANSFORM_ACTIONS
                ],
                min_values=1, max_values=1,
            ),
        )
        self.duration = ui.Label(
            text=t("modules.automod.appeal.modify.duration", locale=locale)[:45],
            description=t("modules.automod.appeal.modify.duration_hint", locale=locale)[:100],
            component=ui.TextInput(
                style=discord.TextStyle.short, required=False, max_length=6, placeholder="24",
            ),
        )
        self.reason = ui.Label(
            text=t("modules.automod.appeal.modify.reason", locale=locale)[:45],
            component=ui.TextInput(
                style=discord.TextStyle.paragraph, required=False, max_length=1000,
                default=(current_reason or "")[:1000],
            ),
        )
        self.note = ui.Label(
            text=t("modules.automod.appeal.modify.note", locale=locale)[:45],
            component=ui.TextInput(
                style=discord.TextStyle.short, required=False, max_length=300,
            ),
        )
        self.add_item(self.action)
        self.add_item(self.duration)
        self.add_item(self.reason)
        self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction):
        bot = interaction.client
        new_action = self.action.component.values[0]
        raw = (self.duration.component.value or "").strip()
        if raw and not raw.isdigit():
            from utils.components_v2 import create_error_message
            await interaction.response.send_message(
                view=create_error_message(
                    t("modules.automod.appeal.error.title", locale=self.locale),
                    t("modules.automod.appeal.modify.invalid_duration", locale=self.locale),
                ), ephemeral=True)
            return
        duration_hours = int(raw) if raw.isdigit() else None
        new_reason = (self.reason.component.value or "").strip() or None
        note = (self.note.component.value or "").strip() or None
        await interaction.response.defer(ephemeral=True)
        await bot.appeals.decide(
            interaction=interaction,
            appeal_id=self.appeal_id,
            decision="transform",
            by_id=interaction.user.id,
            new_action=new_action,
            note=note,
            duration_hours=duration_hours,
            new_reason=new_reason,
        )


def build_accept_choice_view(appeal_id: str, locale: str) -> BaseView:
    """The ephemeral 'Accept' prompt: full cancellation vs modify the case."""
    view = BaseView()
    c = ui.Container(accent_colour=discord.Colour(_REVIEW_CLAIMED))
    c.add_item(ui.TextDisplay(
        f"### {DONE} {t('modules.automod.appeal.accept_choice.title', locale=locale)}"))
    c.add_item(ui.TextDisplay(t("modules.automod.appeal.accept_choice.prompt", locale=locale)))
    view.add_item(c)
    row = ui.ActionRow()
    row.add_item(AppealAcceptChoiceButton("full", appeal_id, locale=locale))
    row.add_item(AppealAcceptChoiceButton("modify", appeal_id, locale=locale))
    view.add_item(row)
    return view


# =========================================================================== #
# Dynamic items (persistent)
# =========================================================================== #

def _guarded(callback):
    """Wrap a DynamicItem callback so unknown errors reach the central handler.

    Persistent dynamic items dispatched via ``add_dynamic_items`` have no live
    ``BaseView``, so their callback errors never reach ``BaseView.on_error``.
    This guarantees an error code + log + user-facing ErrorView all the same.
    """
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as e:  # noqa: BLE001 — funnel everything to the handler
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, e, self.__class__.__name__)
    return wrapper


class AppealNewButton(
    ui.DynamicItem[ui.Button],
    template=rf"moddy:apl:new:(?P<route>[st]):(?P<case>{_UUID}):(?P<sanction>{_UUID})",
):
    """An appeal button on the sanction DM (route ``s`` = server, ``t`` = team)."""

    def __init__(self, route: str, case_id: str, sanction_id: str, locale: str = "fr"):
        is_team = route == "t"
        label_key = ("modules.automod.dm.appeal_team" if is_team
                     else "modules.automod.dm.appeal_server")
        super().__init__(
            ui.Button(
                label=t(label_key, locale=locale)[:80],
                style=discord.ButtonStyle.primary if is_team else discord.ButtonStyle.secondary,
                emoji=discord.PartialEmoji.from_str(MODDY if is_team else GROUPS),
                custom_id=f"moddy:apl:new:{route}:{case_id}:{sanction_id}",
            )
        )
        self.route, self.case_id, self.sanction_id = route, case_id, sanction_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["route"], match["case"], match["sanction"])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        bot = interaction.client
        # Guard against appealing an already-decided sanction.
        existing = await bot.db.get_active_for_sanction(self.sanction_id)
        if existing:
            from utils.components_v2 import create_warning_message
            await interaction.response.send_message(
                view=create_warning_message(
                    t("modules.automod.appeal.error.title", locale=locale),
                    t("modules.automod.appeal.error.already", locale=locale),
                ),
                ephemeral=True,
            )
            return
        dm_channel_id = interaction.channel.id if interaction.channel else None
        dm_message_id = interaction.message.id if interaction.message else None
        await interaction.response.send_modal(
            AppealReasonModal(
                self.route, self.case_id, self.sanction_id, locale,
                dm_channel_id=dm_channel_id, dm_message_id=dm_message_id,
            )
        )


async def _guard_review(interaction: discord.Interaction, appeal: Optional[dict], locale: str) -> bool:
    """Shared gate for reviewer actions: still pending + may review (+ not own)."""
    from utils.components_v2 import create_warning_message, create_error_message
    if not appeal or appeal["status"] != "pending":
        await interaction.response.send_message(
            view=create_warning_message(
                t("modules.automod.appeal.error.title", locale=locale),
                t("modules.automod.appeal.error.handled", locale=locale)),
            ephemeral=True)
        return False
    if not await _can_review(interaction, appeal):
        await interaction.response.send_message(
            view=create_error_message(
                t("modules.automod.appeal.error.title", locale=locale),
                t("modules.automod.appeal.error.no_perms", locale=locale)),
            ephemeral=True)
        return False
    return True


async def _require_claimer(interaction: discord.Interaction, appeal: dict, locale: str) -> bool:
    """A decision requires the appeal to be claimed by the acting reviewer."""
    from utils.components_v2 import create_warning_message
    claimed_by = appeal.get("claimed_by_id")
    if not claimed_by:
        await interaction.response.send_message(
            view=create_warning_message(
                t("modules.automod.appeal.error.title", locale=locale),
                t("modules.automod.appeal.error.claim_first", locale=locale)),
            ephemeral=True)
        return False
    if str(claimed_by) != str(interaction.user.id):
        await interaction.response.send_message(
            view=create_warning_message(
                t("modules.automod.appeal.error.title", locale=locale),
                t("modules.automod.appeal.error.not_claimer", locale=locale)),
            ephemeral=True)
        return False
    return True


class AppealClaimButton(
    ui.DynamicItem[ui.Button],
    template=rf"moddy:apl:claim:(?P<appeal>{_UUID})",
):
    """Claim (assign) an appeal to the acting reviewer."""

    def __init__(self, appeal_id: str, claimed: bool = False, locale: str = "fr"):
        label_key = ("modules.automod.appeal.review.claimed" if claimed
                     else "modules.automod.appeal.button.claim")
        super().__init__(
            ui.Button(
                label=t(label_key, locale=locale)[:80],
                style=discord.ButtonStyle.secondary if claimed else discord.ButtonStyle.primary,
                emoji=discord.PartialEmoji.from_str(HAND),
                custom_id=f"moddy:apl:claim:{appeal_id}",
                disabled=claimed,
            )
        )
        self.appeal_id = appeal_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["appeal"])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        bot = interaction.client
        appeal = await bot.db.get_appeal(self.appeal_id)
        if not await _guard_review(interaction, appeal, locale):
            return
        await bot.appeals.claim(interaction=interaction, appeal_id=self.appeal_id,
                                by_id=interaction.user.id)


class AppealInviteButton(
    ui.DynamicItem[ui.Button],
    template=rf"moddy:apl:invite:(?P<appeal>{_UUID})",
):
    """Get a server invite so the reviewer can join the guild to investigate."""

    def __init__(self, appeal_id: str, locale: str = "fr"):
        super().__init__(
            ui.Button(
                label=t("modules.automod.appeal.button.invite", locale=locale)[:80],
                style=discord.ButtonStyle.secondary,
                emoji=discord.PartialEmoji.from_str(LINK),
                custom_id=f"moddy:apl:invite:{appeal_id}",
            )
        )
        self.appeal_id = appeal_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["appeal"])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        bot = interaction.client
        appeal = await bot.db.get_appeal(self.appeal_id)
        if not appeal:
            return
        if not await _can_review(interaction, appeal):
            from utils.components_v2 import create_error_message
            await interaction.response.send_message(
                view=create_error_message(
                    t("modules.automod.appeal.error.title", locale=locale),
                    t("modules.automod.appeal.error.no_perms", locale=locale)),
                ephemeral=True)
            return
        await bot.appeals.invite(interaction=interaction, appeal_id=self.appeal_id)


class AppealDecisionButton(
    ui.DynamicItem[ui.Button],
    template=rf"moddy:apl:dec:(?P<decision>accept|decline):(?P<appeal>{_UUID})",
):
    """Accept / Decline on a reviewer panel (active once the appeal is claimed)."""

    _STYLE = {"accept": discord.ButtonStyle.success, "decline": discord.ButtonStyle.danger}
    _EMOJI = {"accept": DONE, "decline": UNDONE}

    def __init__(self, decision: str, appeal_id: str, locale: str = "fr", disabled: bool = False):
        super().__init__(
            ui.Button(
                label=t(f"modules.automod.appeal.button.{decision}", locale=locale)[:80],
                style=self._STYLE[decision],
                emoji=discord.PartialEmoji.from_str(self._EMOJI[decision]),
                custom_id=f"moddy:apl:dec:{decision}:{appeal_id}",
                disabled=disabled,
            )
        )
        self.decision, self.appeal_id = decision, appeal_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["decision"], match["appeal"])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        bot = interaction.client
        appeal = await bot.db.get_appeal(self.appeal_id)
        if not await _guard_review(interaction, appeal, locale):
            return
        if not await _require_claimer(interaction, appeal, locale):
            return
        if self.decision == "accept":
            # Ask whether to fully cancel or modify the case.
            await interaction.response.send_message(
                view=build_accept_choice_view(self.appeal_id, locale), ephemeral=True)
            return
        # decline → refuse (binding effects can exceed 3s → ack first)
        await interaction.response.defer(ephemeral=True)
        await bot.appeals.decide(
            interaction=interaction, appeal_id=self.appeal_id,
            decision="refuse", by_id=interaction.user.id)


class AppealAcceptChoiceButton(
    ui.DynamicItem[ui.Button],
    template=rf"moddy:apl:acc:(?P<choice>full|modify):(?P<appeal>{_UUID})",
):
    """The two choices behind 'Accept': full cancellation vs modify the case."""

    def __init__(self, choice: str, appeal_id: str, locale: str = "fr"):
        is_full = choice == "full"
        label_key = ("modules.automod.appeal.accept_choice.full" if is_full
                     else "modules.automod.appeal.accept_choice.modify")
        super().__init__(
            ui.Button(
                label=t(label_key, locale=locale)[:80],
                style=discord.ButtonStyle.success if is_full else discord.ButtonStyle.secondary,
                emoji=discord.PartialEmoji.from_str(DONE if is_full else EDIT),
                custom_id=f"moddy:apl:acc:{choice}:{appeal_id}",
            )
        )
        self.choice, self.appeal_id = choice, appeal_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["choice"], match["appeal"])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        bot = interaction.client
        appeal = await bot.db.get_appeal(self.appeal_id)
        if not await _guard_review(interaction, appeal, locale):
            return
        if not await _require_claimer(interaction, appeal, locale):
            return
        if self.choice == "modify":
            await interaction.response.send_modal(
                ModifyCaseModal(self.appeal_id, appeal.get("action") or "warn",
                                "", locale))
            return
        await interaction.response.defer(ephemeral=True)
        await bot.appeals.decide(
            interaction=interaction, appeal_id=self.appeal_id,
            decision="accept", by_id=interaction.user.id)


# =========================================================================== #
# Permission gate
# =========================================================================== #

async def _can_review(interaction: discord.Interaction, appeal: dict) -> bool:
    """Server route → guild Manage Messages; team route → Moddy staff.

    In every case the appellant can never review their own appeal.
    """
    # Nobody handles their own appeal (server or team route).
    try:
        if interaction.user.id == int(appeal.get("subject_id") or 0):
            return False
    except (TypeError, ValueError):
        pass
    if appeal["route"] == "server":
        perms = getattr(interaction.user, "guild_permissions", None)
        if perms is None:
            return False
        return bool(perms.manage_messages or perms.manage_guild or perms.administrator)
    return await _is_team_reviewer(interaction.client, interaction.user.id)


async def _is_team_reviewer(bot, user_id: int) -> bool:
    """A Moddy staff member (any staff role), a dev, or the super admin."""
    try:
        from utils.staff_permissions import StaffPermissionManager
        mgr = StaffPermissionManager(bot)
        if user_id == getattr(mgr, "SUPER_ADMIN_ID", 0):
            return True
        if user_id in getattr(bot, "_dev_team_ids", set()):
            return True
        roles = await mgr.get_user_roles(user_id)
        return bool(roles)
    except Exception as e:  # never block on a perms lookup failure path
        logger.error("appeal: team reviewer check failed: %s", e)
        return False


# =========================================================================== #
# Persistence registration
# =========================================================================== #

class AppealPersistence(BaseView):
    """Marker view used only to register the appeal dynamic items at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(
            AppealNewButton,
            AppealClaimButton,
            AppealInviteButton,
            AppealDecisionButton,
            AppealAcceptChoiceButton,
        )
