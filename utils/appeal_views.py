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
    FILTER, WARNING, DONE, ERROR, UNDONE, EDIT, TIME, GROUPS, MESSAGE, STAFF,
    MODDY, INFO, LEGAL, BACK, SANCTION_ACTION_EMOJIS,
)

logger = logging.getLogger("moddy.appeal_views")

# UUID fragment used in custom_id templates.
_UUID = r"[0-9a-fA-F-]{36}"

# Sanction actions a reviewer may transform an automod sanction into.
_TRANSFORM_ACTIONS = ["warn", "mute", "kick", "ban"]

# Status → (emoji, i18n key) for rendering an appeal outcome.
_STATUS_RENDER = {
    "pending": (TIME, "modules.automod.appeal.status.pending"),
    "accepted": (DONE, "modules.automod.appeal.status.accepted"),
    "refused": (ERROR, "modules.automod.appeal.status.refused"),
    "transformed": (EDIT, "modules.automod.appeal.status.transformed"),
    "cancelled": (UNDONE, "modules.automod.appeal.status.cancelled"),
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
    appeal_status: Optional[str] = None,
    appeal_route: Optional[str] = None,
) -> BaseView:
    """The DM a sanctioned member receives. Shows the sanction + appeal buttons.

    When ``appeal_status`` is set, the buttons are replaced by a status line so
    the member can follow the appeal's progress on the same message.
    """
    view = BaseView()
    c = ui.Container()
    c.add_item(ui.TextDisplay(
        f"### {FILTER} {t('modules.automod.dm.title', locale=locale)}"
    ))
    c.add_item(ui.TextDisplay(
        t("modules.automod.dm.intro", locale=locale, guild=f"**{guild_name}**")
    ))
    c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    c.add_item(ui.TextDisplay(
        f"{_action_emoji(action)} **{t('modules.automod.dm.action', locale=locale)} :** "
        f"`{t('modules.automod.action.' + action, locale=locale)}`\n"
        f"**{t('modules.automod.dm.reason', locale=locale)} :** {reason or '—'}\n"
        f"-# {t('modules.automod.dm.case', locale=locale)} `{case_ref}`"
    ))
    if explication:
        c.add_item(ui.TextDisplay(f"-# {explication}"))
    view.add_item(c)

    if appeal_status:
        emoji, key = _STATUS_RENDER.get(appeal_status, (TIME, "modules.automod.appeal.status.pending"))
        info = ui.Container()
        info.add_item(ui.TextDisplay(
            f"{emoji} **{t('modules.automod.dm.appeal_state', locale=locale)} :** "
            f"{t(key, locale=locale)}"
        ))
        view.add_item(info)
    else:
        c.add_item(ui.TextDisplay(
            f"-# {t('modules.automod.dm.appeal_hint', locale=locale)}"
        ))
        row = ui.ActionRow()
        row.add_item(AppealNewButton("s", case_id, sanction_id, locale=locale))
        row.add_item(AppealNewButton("t", case_id, sanction_id, locale=locale))
        view.add_item(row)
    return view


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
    """Server route → guild Manage Messages; team route → Moddy staff."""
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
