"""
Appeal UI — sanction-DM appeal buttons + reviewer panels (Components/Modals V2).

When automod sanctions a member, the bot DMs them a notice (like a manual mod
action) carrying two appeal buttons:

* **Faire appel au serveur** — routed to the guild's moderators (reviewed in the
  guild's automod alert channel).
* **Faire appel à l'équipe Moddy** — routed to the Moddy team (reviewed in the
  team appeal channel, ``config.MODDY_APPEAL_CHANNEL_ID``).

A reviewer can **Accepter / Refuser / Transformer** the sanction. The decision is
binding and is applied by :class:`services.appeal_service.AppealService`.

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


def build_review_view(
    *,
    locale: str,
    route: str,
    appeal_id: str,
    subject_id: int,
    guild_name: str,
    guild_id: int,
    case_ref: str,
    action: str,
    reason: str,
    explication: str,
    evidence: str,
    appeal_reason: str,
    decided: Optional[dict] = None,
) -> BaseView:
    """The reviewer panel (server mods or Moddy team). Carries decision buttons.

    ``decided`` (when set) renders the final outcome and drops the buttons.
    """
    view = BaseView()
    c = ui.Container()
    head = (t("modules.automod.appeal.review.title_team", locale=locale)
            if route == "team"
            else t("modules.automod.appeal.review.title_server", locale=locale))
    c.add_item(ui.TextDisplay(f"### {LEGAL} {head}"))
    c.add_item(ui.TextDisplay(
        f"**{t('modules.automod.appeal.review.member', locale=locale)} :** <@{subject_id}> (`{subject_id}`)\n"
        f"**{t('modules.automod.appeal.review.server', locale=locale)} :** {guild_name} (`{guild_id}`)\n"
        f"**{t('modules.automod.appeal.review.case', locale=locale)} :** `{case_ref}`\n"
        f"{_action_emoji(action)} **{t('modules.automod.appeal.review.sanction', locale=locale)} :** "
        f"`{t('modules.automod.action.' + action, locale=locale)}`"
    ))
    c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    c.add_item(ui.TextDisplay(
        f"**{t('modules.automod.appeal.review.motive', locale=locale)} :** {reason or '—'}"
    ))
    if explication:
        c.add_item(ui.TextDisplay(f"-# {explication}"))
    if evidence:
        c.add_item(ui.TextDisplay(
            f"**{t('modules.automod.appeal.review.evidence', locale=locale)} :**\n> {evidence[:900]}"
        ))
    c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    c.add_item(ui.TextDisplay(
        f"{MESSAGE} **{t('modules.automod.appeal.review.user_says', locale=locale)} :**\n"
        f">>> {appeal_reason[:1200] or '—'}"
    ))

    if decided:
        emoji, key = _STATUS_RENDER.get(decided["status"], (INFO, "modules.automod.appeal.status.pending"))
        line = f"{emoji} **{t('modules.automod.appeal.review.outcome', locale=locale)} :** {t(key, locale=locale)}"
        if decided.get("new_action"):
            line += f" → `{t('modules.automod.action.' + decided['new_action'], locale=locale)}`"
        c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        c.add_item(ui.TextDisplay(line))
        if decided.get("by_id"):
            c.add_item(ui.TextDisplay(
                f"-# {t('modules.automod.appeal.review.decided_by', locale=locale)} <@{decided['by_id']}>"
            ))
        view.add_item(c)
        return view

    view.add_item(c)
    row = ui.ActionRow()
    row.add_item(AppealDecisionButton("accept", appeal_id, locale=locale))
    row.add_item(AppealDecisionButton("transform", appeal_id, locale=locale))
    row.add_item(AppealDecisionButton("refuse", appeal_id, locale=locale))
    view.add_item(row)
    return view


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
            await interaction.response.send_message(
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
        await interaction.response.send_message(
            view=create_success_message(
                t("modules.automod.appeal.opened_title", locale=self.locale),
                t(key, locale=self.locale),
            ),
            ephemeral=True,
        )


class AppealTransformModal(BaseModal):
    """Reviewer picks a replacement sanction (and an optional note)."""

    def __init__(self, appeal_id: str, current_action: str, locale: str):
        super().__init__(title=t("modules.automod.appeal.transform.title", locale=locale)[:45])
        self.appeal_id = appeal_id
        self.locale = locale
        self.action = ui.Label(
            text=t("modules.automod.appeal.transform.label", locale=locale)[:45],
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
        self.note = ui.Label(
            text=t("modules.automod.appeal.transform.note", locale=locale)[:45],
            component=ui.TextInput(
                style=discord.TextStyle.short, required=False, max_length=300,
            ),
        )
        self.add_item(self.action)
        self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction):
        bot = interaction.client
        new_action = self.action.component.values[0]
        note = (self.note.component.value or "").strip() or None
        await bot.appeals.decide(
            interaction=interaction,
            appeal_id=self.appeal_id,
            decision="transform",
            by_id=interaction.user.id,
            new_action=new_action,
            note=note,
        )


# =========================================================================== #
# Dynamic items (persistent)
# =========================================================================== #

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


class AppealDecisionButton(
    ui.DynamicItem[ui.Button],
    template=rf"moddy:apl:dec:(?P<decision>accept|refuse|transform):(?P<appeal>{_UUID})",
):
    """Accept / Refuse / Transform on a reviewer panel."""

    _STYLE = {
        "accept": discord.ButtonStyle.success,
        "refuse": discord.ButtonStyle.danger,
        "transform": discord.ButtonStyle.secondary,
    }
    _EMOJI = {"accept": DONE, "refuse": ERROR, "transform": EDIT}

    def __init__(self, decision: str, appeal_id: str, locale: str = "fr"):
        super().__init__(
            ui.Button(
                label=t(f"modules.automod.appeal.button.{decision}", locale=locale)[:80],
                style=self._STYLE[decision],
                emoji=discord.PartialEmoji.from_str(self._EMOJI[decision]),
                custom_id=f"moddy:apl:dec:{decision}:{appeal_id}",
            )
        )
        self.decision, self.appeal_id = decision, appeal_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["decision"], match["appeal"])

    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        bot = interaction.client
        appeal = await bot.db.get_appeal(self.appeal_id)
        if not appeal or appeal["status"] != "pending":
            from utils.components_v2 import create_warning_message
            await interaction.response.send_message(
                view=create_warning_message(
                    t("modules.automod.appeal.error.title", locale=locale),
                    t("modules.automod.appeal.error.handled", locale=locale),
                ),
                ephemeral=True,
            )
            return
        if not await _can_review(interaction, appeal):
            from utils.components_v2 import create_error_message
            await interaction.response.send_message(
                view=create_error_message(
                    t("modules.automod.appeal.error.title", locale=locale),
                    t("modules.automod.appeal.error.no_perms", locale=locale),
                ),
                ephemeral=True,
            )
            return
        if self.decision == "transform":
            await interaction.response.send_modal(
                AppealTransformModal(self.appeal_id, appeal.get("action") or "warn", locale)
            )
            return
        await bot.appeals.decide(
            interaction=interaction,
            appeal_id=self.appeal_id,
            decision=self.decision,
            by_id=interaction.user.id,
        )


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
        bot.add_dynamic_items(AppealNewButton, AppealDecisionButton)
