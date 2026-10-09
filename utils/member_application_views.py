"""Member Applications — the review card, its buttons and the reject modal.

**The card** is posted once per submitted application in the server's review
channel and edited in place for the rest of its life: pending → approved,
rejected or withdrawn. It is written in the *server* language (the whole staff
reads it) and rebuilt from the stored snapshot every time, because bots cannot
fetch a single join request back from Discord.

**The buttons** are ``DynamicItem``s carrying the join request id. Authority is
re-derived from the click every time — the clicker must hold Kick Members (what
Discord itself requires to decide) or one of the reviewer roles the server
chose — so a card left in the channel for a month is as safe as a fresh one.

**The reject modal** (Modal V2) asks for the reason Discord shows the
applicant, with the server's preset reasons in a select above a free-text field.

See docs/MEMBER_APPLICATIONS.md.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

import discord
from discord import ui

from cogs.error_handler import BaseModal, BaseView
from config import COLORS
from db.repositories.member_applications import (
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_SUBMITTED,
    STATUS_WITHDRAWN,
)
from modules.member_applications import MAX_REJECTION_REASON_LENGTH, MODULE_ID, inline_code
from utils.emojis import (
    DONE, SHAPES, UNDONE,
    format_verification_badge, get_user_verification_badge,
)
from utils.i18n import i18n, t

logger = logging.getLogger("moddy.member_application_views")

_CID_PREFIX = "moddy:member_apps:card"
_CID_REJECT_PRESET = "moddy:member_apps:reject:preset"
_CID_REJECT_REASON = "moddy:member_apps:reject:reason"

# An account younger than this is flagged as recent on the card.
NEW_ACCOUNT_AGE = timedelta(days=7)

# Components V2 caps a message at 4000 characters of text in total. The
# answers get what is left once the fixed parts are counted generously — a
# paragraph answer alone can be 1000 characters, and a form can hold several.
ANSWERS_BUDGET = 2800
MIN_ANSWER_SHARE = 120

ACCENTS = {
    STATUS_SUBMITTED: COLORS["primary"],
    STATUS_APPROVED: COLORS["success"],
    STATUS_REJECTED: COLORS["error"],
    STATUS_WITHDRAWN: COLORS["neutral"],
}


def _guarded(callback):
    """Route a dynamic-item callback error to the central handler (no live view)."""
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as e:  # noqa: BLE001
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, e, self.__class__.__name__)
    return wrapper


# --------------------------------------------------------------------------- #
# Pure rendering helpers
# --------------------------------------------------------------------------- #
_C = "modules.member_applications.card"


def field_line(label_key: str, value: str, locale: str) -> str:
    """``**Label:** value`` — the only shape an information line takes.

    The label and its punctuation come from the locale (French puts a space
    before the colon, English does not); no emoji, ever: the card is read as
    a form, one fact per line, in a fixed order.
    """
    return t(f"{_C}.field", locale=locale,
             label=t(f"{_C}.fields.{label_key}", locale=locale), value=value)


def _quote(text: str) -> str:
    """An applicant's answer as a block quote that cannot ping anyone."""
    text = discord.utils.escape_mentions(text.strip())
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def answer_text(field: Dict[str, Any], locale: str) -> Optional[str]:
    """What the applicant answered to one form field, as plain text.

    ``None`` means "nothing to quote": a TERMS field renders as a single
    line of its own, and an unanswered optional question says so.
    """
    kind = field.get("field_type")
    response = field.get("response")
    if kind == "MULTIPLE_CHOICE":
        choices = field.get("choices") or []
        if isinstance(response, int) and 0 <= response < len(choices):
            return str(choices[response])
        return None
    if kind in ("TEXT_INPUT", "PARAGRAPH"):
        return str(response) if isinstance(response, str) and response.strip() else None
    return None


def render_answers(form_responses: Sequence[Dict[str, Any]], locale: str,
                   budget: int = ANSWERS_BUDGET) -> List[str]:
    """One block per question, the answers trimmed so the whole fits ``budget``."""
    blocks: List[tuple] = []
    for field in form_responses or []:
        if not isinstance(field, dict):
            continue
        if field.get("field_type") == "TERMS":
            accepted = field.get("response") is True
            value = t(f"{_C}.values.{'terms_accepted' if accepted else 'terms_refused'}",
                      locale=locale)
            blocks.append((field_line("terms", value, locale), None, False))
            continue
        label = _truncate(" ".join(str(field.get("label") or "").split()), 150) or "—"
        blocks.append((f"**{label}**", answer_text(field, locale), True))

    answers = [a for _, a, _ in blocks if a]
    fixed = sum(len(h) + 4 for h, _, _ in blocks)
    share = None
    if answers and fixed + sum(len(a) + len(a.splitlines()) * 2 for a in answers) > budget:
        share = max(MIN_ANSWER_SHARE, (budget - fixed) // len(answers))

    rendered = []
    no_answer = t(f"{_C}.values.no_answer", locale=locale)
    for heading, answer, is_question in blocks:
        if not is_question:
            rendered.append(heading)
        elif answer is None:
            rendered.append(f"{heading}\n-# {no_answer}")
        else:
            rendered.append(f"{heading}\n{_quote(_truncate(answer, share) if share else answer)}")
    return rendered


def avatar_url(user: Dict[str, Any], user_id: int) -> str:
    avatar = user.get("avatar")
    if avatar:
        ext = "gif" if str(avatar).startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.{ext}?size=128"
    return f"https://cdn.discordapp.com/embed/avatars/{(user_id >> 22) % 6}.png"


def history_value(history: Dict[str, int], locale: str) -> str:
    """``3`` / ``3, 2 rejected`` — ``0`` for a first application."""
    total = sum(history.values())
    rejected = history.get(STATUS_REJECTED, 0)
    if rejected:
        return t(f"{_C}.values.history_rejected", locale=locale,
                 count=f"`{total}`", rejected=f"`{rejected}`")
    return f"`{total}`"


def identity_lines(user: Dict[str, Any], user_id: int, name: str,
                   history: Dict[str, int], locale: str) -> List[str]:
    """Who applied — one labelled fact per line, always in the same order."""
    created = discord.utils.snowflake_time(user_id)
    stamp = int(created.timestamp())
    created_value = f"<t:{stamp}:D> (<t:{stamp}:R>)"
    if datetime.now(timezone.utc) - created < NEW_ACCOUNT_AGE:
        created_value += " · " + t(f"{_C}.values.new_account", locale=locale)

    lines = [
        field_line("member", f"<@{user_id}>", locale),
        field_line("display_name", name, locale),
    ]
    username = user.get("username")
    if username:
        lines.append(field_line("username", inline_code(username), locale))
    lines += [
        field_line("id", f"`{user_id}`", locale),
        field_line("created", created_value, locale),
        field_line("history", history_value(history, locale), locale),
    ]
    return lines


def status_lines(row: Dict[str, Any], locale: str) -> List[str]:
    """Where the application stands — labelled lines, like the identity block."""
    status = row.get("status")
    key = {
        STATUS_SUBMITTED: "pending", STATUS_APPROVED: "approved",
        STATUS_REJECTED: "rejected", STATUS_WITHDRAWN: "withdrawn",
    }.get(status, "pending")
    lines = [field_line("status", t(f"{_C}.status.{key}", locale=locale), locale)]
    if status not in (STATUS_APPROVED, STATUS_REJECTED):
        return lines

    if row.get("reviewed_by"):
        lines.append(field_line("reviewer", f"<@{row['reviewed_by']}>", locale))
    reviewed_at = row.get("reviewed_at")
    if isinstance(reviewed_at, datetime):
        stamp = int(reviewed_at.timestamp())
        lines.append(field_line("decided_at", f"<t:{stamp}:f>", locale))
    if row.get("decided_in") == "discord":
        lines.append(field_line("via", t(f"{_C}.values.via_discord", locale=locale), locale))
    if status == STATUS_REJECTED:
        reason = row.get("rejection_reason")
        value = (inline_code(reason) if reason
                 else t(f"{_C}.values.no_reason", locale=locale))
        lines.append(field_line("reason", value, locale))
    return lines


async def applicant_name(bot, user: Dict[str, Any], user_id: int) -> str:
    """``**display name**badge`` per the verification-badge rule in CLAUDE.md."""
    attributes: Dict[str, Any] = {}
    verification = None
    if getattr(bot, "db", None):
        try:
            record = await bot.db.get_user(user_id)
            attributes = (record or {}).get("attributes", {}) or {}
            verification = ((record or {}).get("data", {}) or {}).get("verification")
        except Exception as e:
            logger.debug(f"Could not load attributes for {user_id}: {e}")
    name = user.get("global_name") or user.get("username") or str(user_id)
    badge, _orgs, _tier = get_user_verification_badge(
        {"public_flags": user.get("public_flags") or 0}, attributes, verification)
    return f"**{discord.utils.escape_markdown(name)}**{format_verification_badge(badge)}"


# --------------------------------------------------------------------------- #
# The card
# --------------------------------------------------------------------------- #
async def build_card(bot, guild: discord.Guild, row: Dict[str, Any], *, locale: str,
                     mention_role_ids: Sequence[int] = ()) -> ui.LayoutView:
    """The review card for one application, in whatever state it is in.

    Layout, top to bottom: the role pings (first post only — the one place a
    Components V2 message can carry a real ping; which of them notifies is
    decided by the caller's ``allowed_mentions``), the container — applicant,
    answers, status — and, while the application is pending, the Approve /
    Reject row **outside** the container.
    """
    request = row.get("request") or {}
    user = request.get("user") or {}
    user_id = int(row["user_id"])
    status = row.get("status")

    history: Dict[str, int] = {}
    if getattr(bot, "db", None):
        try:
            history = await bot.db.count_member_applications(
                guild.id, user_id, exclude_request_id=row["request_id"])
        except Exception as e:
            logger.debug(f"Could not count earlier applications of {user_id}: {e}")

    view = ui.LayoutView(timeout=None)
    if mention_role_ids:
        view.add_item(ui.TextDisplay(" ".join(f"<@&{rid}>" for rid in mention_role_ids)))

    container = ui.Container(accent_colour=discord.Colour(ACCENTS.get(status, COLORS["primary"])))
    container.add_item(ui.TextDisplay(f"### {SHAPES} {t(f'{_C}.title', locale=locale)}"))

    name = await applicant_name(bot, user, user_id)
    container.add_item(ui.Section(
        ui.TextDisplay("\n".join(identity_lines(user, user_id, name, history, locale))),
        accessory=ui.Thumbnail(avatar_url(user, user_id)),
    ))

    answers = render_answers(request.get("form_responses") or [], locale)
    if answers:
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(
            f"**{t(f'{_C}.answers_title', locale=locale)}**\n\n" + "\n\n".join(answers)))

    container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    container.add_item(ui.TextDisplay("\n".join(status_lines(row, locale))))

    footer = [t(f"{_C}.footer", locale=locale, id=f"`{row['request_id']}`")]
    submitted_at = row.get("submitted_at")
    if isinstance(submitted_at, datetime):
        footer.append(t(f"{_C}.submitted", locale=locale,
                        timestamp=f"<t:{int(submitted_at.timestamp())}:R>"))
    container.add_item(ui.TextDisplay("-# " + " · ".join(footer)))
    view.add_item(container)

    if status == STATUS_SUBMITTED:
        buttons = ui.ActionRow()
        buttons.add_item(ApproveButton(row["request_id"], locale=locale))
        buttons.add_item(RejectButton(row["request_id"], locale=locale))
        view.add_item(buttons)

    return view

# --------------------------------------------------------------------------- #
# Shared click handling
# --------------------------------------------------------------------------- #
async def _card_locale(interaction: discord.Interaction) -> str:
    from utils.guild_language import guild_locale
    return await guild_locale(interaction.client, interaction.guild)


async def _reply_error(interaction: discord.Interaction, key: str, **kwargs) -> None:
    from utils.components_v2 import create_error_message
    locale = i18n.get_user_locale(interaction)
    view = create_error_message(
        t("modules.member_applications.errors.action_title", locale=locale),
        t(f"modules.member_applications.errors.{key}", locale=locale, **kwargs),
    )
    if interaction.response.is_done():
        await interaction.followup.send(view=view, ephemeral=True)
    else:
        await interaction.response.send_message(view=view, ephemeral=True)


async def _authorize(interaction: discord.Interaction, request_id: int):
    """The module and the application row, or ``None`` after telling the clicker why."""
    bot = interaction.client
    if interaction.guild is None:
        return None
    module = await bot.module_manager.get_module_instance(interaction.guild.id, MODULE_ID)
    if module is None or not module.can_review(interaction.user):
        await _reply_error(interaction, "not_reviewer")
        return None
    row = await bot.db.get_member_application(request_id)
    if row is None or row["guild_id"] != interaction.guild.id:
        await _reply_error(interaction, "not_found")
        return None
    return module, row


async def _apply_decision(interaction: discord.Interaction, request_id: int,
                          action: str, reason: Optional[str]) -> None:
    """Call the API, then redraw the card on the message that was clicked."""
    from services.member_application_service import (
        ERR_ALREADY, ERR_GONE, get_service,
    )

    await interaction.response.defer()
    error, row = await get_service(interaction.client).decide(
        interaction.guild, request_id, action,
        reviewer_id=interaction.user.id, rejection_reason=reason,
    )

    if row is not None:
        view = await build_card(interaction.client, interaction.guild, row,
                                locale=await _card_locale(interaction))
        try:
            await interaction.edit_original_response(
                view=view, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as e:
            logger.warning(f"Could not redraw application card {request_id}: {e}")

    if error is None:
        return
    if error == ERR_ALREADY:
        await _reply_error(interaction, "already_decided")
    elif error == ERR_GONE:
        await _reply_error(interaction, "gone")
    else:
        await _reply_error(interaction, error)


# --------------------------------------------------------------------------- #
# Buttons
# --------------------------------------------------------------------------- #
class ApproveButton(
    ui.DynamicItem[ui.Button],
    template=rf"{_CID_PREFIX}:approve:(?P<rid>\d{{1,20}})",
):
    """Approves the application — one click, no confirmation.

    An approval is what the applicant is waiting for, and it can be undone by
    kicking them; a rejection is final for them, which is why only the other
    button asks for anything.
    """

    def __init__(self, request_id: int, *, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=t("modules.member_applications.card.approve", locale=locale)[:80],
            style=discord.ButtonStyle.success,
            emoji=discord.PartialEmoji.from_str(DONE),
            custom_id=f"{_CID_PREFIX}:approve:{request_id}",
        ))
        self.request_id = request_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction,
                             item: ui.Button, match: re.Match):
        return cls(int(match["rid"]))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if await _authorize(interaction, self.request_id) is None:
            return
        await _apply_decision(interaction, self.request_id, STATUS_APPROVED, None)


class RejectButton(
    ui.DynamicItem[ui.Button],
    template=rf"{_CID_PREFIX}:reject:(?P<rid>\d{{1,20}})",
):
    """Opens the reject modal for the reason Discord will show the applicant."""

    def __init__(self, request_id: int, *, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=t("modules.member_applications.card.reject", locale=locale)[:80],
            style=discord.ButtonStyle.danger,
            emoji=discord.PartialEmoji.from_str(UNDONE),
            custom_id=f"{_CID_PREFIX}:reject:{request_id}",
        ))
        self.request_id = request_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction,
                             item: ui.Button, match: re.Match):
        return cls(int(match["rid"]))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        found = await _authorize(interaction, self.request_id)
        if found is None:
            return
        module, row = found
        if row["status"] != STATUS_SUBMITTED:
            await _reply_error(interaction, "already_decided")
            return

        user = (row.get("request") or {}).get("user") or {}
        name = user.get("global_name") or user.get("username") or str(row["user_id"])
        await interaction.response.send_modal(RejectModal(
            self.request_id,
            locale=i18n.get_user_locale(interaction),
            applicant=name,
            presets=module.rejection_reasons,
        ))


class RejectModal(BaseModal):
    """Modal V2 — why the application is rejected.

    Up to three top-level components: who is being rejected, the server's
    preset reasons (only when it has some) and a free-text reason. A typed
    reason wins over a picked one; both empty rejects without a reason, which
    Discord allows. Discord shows the reason to the applicant, and says so.
    """

    def __init__(self, request_id: int, *, locale: str, applicant: str,
                 presets: Sequence[str] = ()):
        super().__init__(
            title=t("modules.member_applications.reject_modal.title", locale=locale)[:45],
            timeout=None,
        )
        self.request_id = request_id

        self.add_item(ui.TextDisplay(
            t("modules.member_applications.reject_modal.intro", locale=locale,
              user=f"**{discord.utils.escape_markdown(applicant)}**")
        ))

        self.preset_select: Optional[ui.Select] = None
        if presets:
            self.preset_select = ui.Select(
                options=[discord.SelectOption(label=reason[:100], value=str(index))
                         for index, reason in enumerate(presets[:25])],
                min_values=0, max_values=1, required=False,
                custom_id=_CID_REJECT_PRESET,
            )
            self.add_item(ui.Label(
                text=t("modules.member_applications.reject_modal.preset_label", locale=locale)[:45],
                description=t("modules.member_applications.reject_modal.preset_description",
                              locale=locale)[:100],
                component=self.preset_select,
            ))
        self.presets = list(presets)

        self.reason_input = ui.TextInput(
            style=discord.TextStyle.paragraph,
            max_length=MAX_REJECTION_REASON_LENGTH,
            required=False,
            placeholder=t("modules.member_applications.reject_modal.reason_placeholder",
                          locale=locale)[:100],
            custom_id=_CID_REJECT_REASON,
        )
        self.add_item(ui.Label(
            text=t("modules.member_applications.reject_modal.reason_label", locale=locale)[:45],
            description=t("modules.member_applications.reject_modal.reason_description",
                          locale=locale)[:100],
            component=self.reason_input,
        ))

    def chosen_reason(self) -> Optional[str]:
        typed = (self.reason_input.value or "").strip()
        if typed:
            return typed
        if self.preset_select is not None and self.preset_select.values:
            index = int(self.preset_select.values[0])
            if 0 <= index < len(self.presets):
                return self.presets[index]
        return None

    async def on_submit(self, interaction: discord.Interaction):
        # Re-checked: the modal may have stayed open while roles changed.
        if await _authorize(interaction, self.request_id) is None:
            return
        await _apply_decision(interaction, self.request_id, STATUS_REJECTED,
                              self.chosen_reason())


class MemberApplicationsPersistence(BaseView):
    """Marker view: registers the card's dynamic buttons at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        # Auth model: the request id is in the custom_id; who may decide is
        # re-derived from the clicker's permissions and the module's reviewer
        # roles on every click, so nothing about the viewer is ever stored.
        bot.add_dynamic_items(ApproveButton, RejectButton)
