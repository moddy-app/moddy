"""
Case management Views & Modals (Components V2).

Interactive staff flows for the new case/sanction/event model:
- :class:`CaseCreationView` / :class:`CaseCreateModal` — open a case + first sanction.
- :class:`AddSanctionView` / :class:`AddSanctionModal` — add a sanction to a case.
- :class:`RevokeSanctionView` — revoke an active sanction.
- :class:`CaseCommentModal` / :class:`CaseNoteModal` — append a timeline entry.
- :class:`EditReasonModal` — edit the case reason.

These are short-lived, author-scoped, ephemeral staff flows (timeout-based, not
persistent — they wrap in-memory callbacks, mirroring the existing staff
``_ModalButtonView`` pattern).
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

import discord
from discord import ui

from cogs.error_handler import BaseView, BaseModal
from staff.framework import design
from utils import emojis
from utils.i18n import t
from utils.moderation_cases import (
    CaseType,
    SubjectType,
    SanctionAction,
    get_available_actions,
    get_action_emoji,
    TEMPORARY_ACTIONS,
)
from services.case_service import SOURCES, CaseSource

logger = logging.getLogger('moddy.case_management')


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _manual_sources() -> List[CaseSource]:
    """Sources a moderator may open by hand (no required external scope)."""
    return [s for s in SOURCES.values() if s.manual and not s.requires_scope_id]


def _parse_duration_hours(raw: Optional[str]) -> Optional[datetime]:
    """Parse an hours value into an absolute UTC ``expires_at`` (or None)."""
    if not raw or not raw.strip():
        return None
    hours = float(raw.strip())
    if hours <= 0:
        return None
    return datetime.now(timezone.utc) + timedelta(hours=hours)


# --------------------------------------------------------------------------- #
# Creation flow
# --------------------------------------------------------------------------- #

class CaseCreationView(BaseView):
    """Pick a case source then a sanction action, then open the detail modal.

    Choices are driven by the scalable source registry
    (``services.case_service.SOURCES``) so adding a manual sanction kind needs
    no change here.
    """

    def __init__(self, *, bot, staff_id: int, subject_type: SubjectType,
                 subject_id: int, subject_name: str, locale: str = "en-US",
                 on_created: Optional[Callable] = None):
        super().__init__(timeout=300)
        self.bot = bot
        self.staff_id = staff_id
        self.subject_type = subject_type
        self.subject_id = subject_id
        self.subject_name = subject_name
        self.locale = locale
        self.on_created = on_created

        self.sources = _manual_sources()
        self.selected_source: Optional[CaseSource] = self.sources[0] if len(self.sources) == 1 else None
        self.selected_action: Optional[SanctionAction] = None
        self._build()

    def _build(self):
        self.clear_items()
        container = design.make_container("error")
        container.add_item(ui.TextDisplay(design.title_line(
            emojis.BLACKLIST, t("staff.mod.case.create_title", locale=self.locale)
        )))
        container.add_item(ui.TextDisplay(
            f"**{t('staff.mod.case.subject', locale=self.locale)}:** {self.subject_name}\n"
            f"-# {t('staff.mod.case.create_hint', locale=self.locale)}"
        ))

        # Source (case type) select — only when more than one manual source.
        if len(self.sources) > 1:
            type_row = ui.ActionRow()
            type_options = [
                discord.SelectOption(
                    label=t(f"staff.mod.case.type_value.{s.case_type.value}", locale=self.locale),
                    value=s.key,
                    default=(self.selected_source is not None and self.selected_source.key == s.key),
                )
                for s in self.sources
            ]
            type_select = ui.Select(
                placeholder=t("staff.mod.case.select_type", locale=self.locale),
                options=type_options, max_values=1,
            )
            type_select.callback = self._on_type
            type_row.add_item(type_select)
            container.add_item(type_row)

        # Sanction action select (after a source is chosen).
        if self.selected_source:
            action_row = ui.ActionRow()
            action_options = [
                discord.SelectOption(
                    label=t(f"staff.mod.case.action.{a.value}", locale=self.locale),
                    value=a.value,
                    emoji=discord.PartialEmoji.from_str(get_action_emoji(a)),
                    default=(self.selected_action == a),
                )
                for a in self.selected_source.actions
            ]
            action_select = ui.Select(
                placeholder=t("staff.mod.case.select_action", locale=self.locale),
                options=action_options, max_values=1,
            )
            action_select.callback = self._on_action
            action_row.add_item(action_select)
            container.add_item(action_row)

        # Confirm / cancel.
        if self.selected_source and self.selected_action:
            btn_row = ui.ActionRow()
            confirm = ui.Button(
                label=t("staff.mod.case.create_button", locale=self.locale),
                style=discord.ButtonStyle.success,
                emoji=discord.PartialEmoji.from_str(emojis.DONE),
            )
            confirm.callback = self._on_confirm
            btn_row.add_item(confirm)
            cancel = ui.Button(
                label=t("staff.common.cancel", locale=self.locale),
                style=discord.ButtonStyle.secondary,
                emoji=discord.PartialEmoji.from_str(emojis.DELETE),
            )
            cancel.callback = self._on_cancel
            btn_row.add_item(cancel)
            container.add_item(btn_row)

        self.add_item(container)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.staff_id:
            await interaction.response.send_message(
                t("staff.common.not_your_menu", locale=self.locale), ephemeral=True
            )
            return False
        return True

    async def _on_type(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        self.selected_source = SOURCES.get(interaction.data["values"][0])
        self.selected_action = None
        self._build()
        await interaction.response.edit_message(view=self)

    async def _on_action(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        self.selected_action = SanctionAction(interaction.data["values"][0])
        self._build()
        await interaction.response.edit_message(view=self)

    async def _on_confirm(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        modal = CaseCreateModal(
            bot=self.bot, staff_id=self.staff_id, subject_type=self.subject_type,
            subject_id=self.subject_id, subject_name=self.subject_name,
            source=self.selected_source, action=self.selected_action,
            locale=self.locale, on_created=self.on_created,
        )
        await interaction.response.send_modal(modal)

    async def _on_cancel(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=design.info(
            t("staff.common.cancelled", locale=self.locale),
            t("staff.common.cancelled_desc", locale=self.locale),
        ))


class CaseCreateModal(BaseModal):
    """Collect reason (+ duration for temporary sanctions) and create the case."""

    def __init__(self, *, bot, staff_id, subject_type, subject_id, subject_name,
                 source: CaseSource, action: SanctionAction, locale="en-US",
                 on_created=None):
        super().__init__(title=t("staff.mod.case.create_title", locale=locale)[:45], timeout=600)
        self.bot = bot
        self.staff_id = staff_id
        self.subject_type = subject_type
        self.subject_id = subject_id
        self.subject_name = subject_name
        self.source = source
        self.action = action
        self.locale = locale
        self.on_created = on_created

        self.reason_input = ui.TextInput(
            label=t("staff.mod.case.reason", locale=locale)[:45],
            style=discord.TextStyle.paragraph,
            placeholder=t("staff.mod.case.reason_placeholder", locale=locale)[:100],
            required=True, max_length=1500,
        )
        self.add_item(self.reason_input)

        self.duration_input = None
        if action in TEMPORARY_ACTIONS:
            self.duration_input = ui.TextInput(
                label=t("staff.mod.case.duration_hours", locale=locale)[:45],
                style=discord.TextStyle.short,
                placeholder="24",
                required=False, max_length=10,
            )
            self.add_item(self.duration_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            expires_at = _parse_duration_hours(self.duration_input.value) if self.duration_input else None
        except ValueError:
            await interaction.response.send_message(view=design.error(
                t("staff.mod.case.invalid_duration_title", locale=self.locale),
                t("staff.mod.case.invalid_duration", locale=self.locale),
            ), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        result = await self.bot.db.create_case(
            case_type=self.source.case_type.value,
            subject_type=self.subject_type.value,
            subject_id=self.subject_id,
            issuer_type="moddy_staff",
            issuer_id=self.staff_id,
            scope_type=self.source.scope_type.value,
            scope_id=None,
            reason=self.reason_input.value.strip(),
            action=self.action.value,
            sanction_expires_at=expires_at,
        )

        if self.on_created:
            await self.on_created(interaction, result)
        else:
            await interaction.followup.send(view=design.success(
                t("staff.mod.case.create_done_title", locale=self.locale),
                t("staff.mod.case.create_done", locale=self.locale, id=f"`{result['reference']}`"),
            ), ephemeral=True)


# --------------------------------------------------------------------------- #
# Add sanction flow
# --------------------------------------------------------------------------- #

class AddSanctionView(BaseView):
    """Pick a sanction action to add to an existing case."""

    def __init__(self, *, bot, staff_id: int, case_id, reference: str,
                 case_type: CaseType, locale="en-US", on_done=None):
        super().__init__(timeout=300)
        self.bot = bot
        self.staff_id = staff_id
        self.case_id = case_id
        self.reference = reference
        self.case_type = case_type
        self.locale = locale
        self.on_done = on_done
        self._build()

    def _build(self):
        self.clear_items()
        container = design.make_container("error")
        container.add_item(ui.TextDisplay(design.title_line(
            emojis.ADD, t("staff.mod.case.add_sanction_title", locale=self.locale, id=self.reference)
        )))
        row = ui.ActionRow()
        options = [
            discord.SelectOption(
                label=t(f"staff.mod.case.action.{a.value}", locale=self.locale),
                value=a.value,
                emoji=discord.PartialEmoji.from_str(get_action_emoji(a)),
            )
            for a in get_available_actions(self.case_type)
        ]
        select = ui.Select(
            placeholder=t("staff.mod.case.select_action", locale=self.locale),
            options=options, max_values=1,
        )
        select.callback = self._on_action
        row.add_item(select)
        container.add_item(row)
        self.add_item(container)

    async def _on_action(self, interaction: discord.Interaction):
        if interaction.user.id != self.staff_id:
            await interaction.response.send_message(
                t("staff.common.not_your_menu", locale=self.locale), ephemeral=True)
            return
        action = SanctionAction(interaction.data["values"][0])
        modal = AddSanctionModal(
            bot=self.bot, staff_id=self.staff_id, case_id=self.case_id,
            reference=self.reference, action=action, locale=self.locale, on_done=self.on_done,
        )
        await interaction.response.send_modal(modal)


class AddSanctionModal(BaseModal):
    """Collect an optional note (+ duration) and add the sanction."""

    def __init__(self, *, bot, staff_id, case_id, reference, action: SanctionAction,
                 locale="en-US", on_done=None):
        super().__init__(title=t("staff.mod.case.add_sanction_modal", locale=locale)[:45], timeout=600)
        self.bot = bot
        self.staff_id = staff_id
        self.case_id = case_id
        self.reference = reference
        self.action = action
        self.locale = locale
        self.on_done = on_done

        self.note_input = ui.TextInput(
            label=t("staff.mod.case.sanction_note", locale=locale)[:45],
            style=discord.TextStyle.paragraph,
            required=False, max_length=1000,
        )
        self.add_item(self.note_input)

        self.duration_input = None
        if action in TEMPORARY_ACTIONS:
            self.duration_input = ui.TextInput(
                label=t("staff.mod.case.duration_hours", locale=locale)[:45],
                style=discord.TextStyle.short, placeholder="24",
                required=False, max_length=10,
            )
            self.add_item(self.duration_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            expires_at = _parse_duration_hours(self.duration_input.value) if self.duration_input else None
        except ValueError:
            await interaction.response.send_message(view=design.error(
                t("staff.mod.case.invalid_duration_title", locale=self.locale),
                t("staff.mod.case.invalid_duration", locale=self.locale),
            ), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await self.bot.db.add_sanction(
            self.case_id, self.action.value, "moddy_staff", self.staff_id,
            expires_at=expires_at, note=(self.note_input.value.strip() or None),
        )
        if self.on_done:
            await self.on_done(interaction)
        else:
            await interaction.followup.send(view=design.success(
                t("staff.mod.case.sanction_added_title", locale=self.locale),
                t("staff.mod.case.sanction_added", locale=self.locale, id=f"`{self.reference}`"),
            ), ephemeral=True)


class RevokeSanctionView(BaseView):
    """Select one active sanction of a case and revoke it."""

    def __init__(self, *, bot, staff_id: int, reference: str,
                 sanctions: List[dict], locale="en-US", on_done=None):
        super().__init__(timeout=300)
        self.bot = bot
        self.staff_id = staff_id
        self.reference = reference
        self.sanctions = sanctions
        self.locale = locale
        self.on_done = on_done
        self._build()

    def _build(self):
        self.clear_items()
        container = design.make_container("warning")
        container.add_item(ui.TextDisplay(design.title_line(
            emojis.UNDONE, t("staff.mod.case.revoke_title", locale=self.locale, id=self.reference)
        )))
        row = ui.ActionRow()
        options = []
        for s in self.sanctions[:25]:
            action = s["action"]
            options.append(discord.SelectOption(
                label=t(f"staff.mod.case.action.{action}", locale=self.locale),
                value=str(s["id"]),
                description=f"{str(s['id'])[:8]}",
                emoji=discord.PartialEmoji.from_str(get_action_emoji(SanctionAction(action))),
            ))
        select = ui.Select(
            placeholder=t("staff.mod.case.select_sanction", locale=self.locale),
            options=options, max_values=1,
        )
        select.callback = self._on_select
        row.add_item(select)
        container.add_item(row)
        self.add_item(container)

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.staff_id:
            await interaction.response.send_message(
                t("staff.common.not_your_menu", locale=self.locale), ephemeral=True)
            return
        import uuid as _uuid
        sanction_id = _uuid.UUID(interaction.data["values"][0])
        await interaction.response.defer(ephemeral=True)
        ok = await self.bot.db.revoke_sanction(sanction_id, "moddy_staff", self.staff_id)
        if not ok:
            await interaction.followup.send(view=design.error(
                t("staff.mod.case.revoke_failed_title", locale=self.locale),
                t("staff.mod.case.revoke_failed", locale=self.locale),
            ), ephemeral=True)
            return
        if self.on_done:
            await self.on_done(interaction)
        else:
            await interaction.followup.send(view=design.success(
                t("staff.mod.case.revoke_done_title", locale=self.locale),
                t("staff.mod.case.revoke_done", locale=self.locale, id=f"`{self.reference}`"),
            ), ephemeral=True)


# --------------------------------------------------------------------------- #
# Timeline & edit modals
# --------------------------------------------------------------------------- #

class _TimelineModal(BaseModal):
    """Base for comment/note modals — appends one timeline event."""

    EVENT_TYPE = "comment"
    TITLE_KEY = "staff.mod.case.comment_title"
    LABEL_KEY = "staff.mod.case.comment_label"
    DONE_TITLE_KEY = "staff.mod.case.comment_done_title"
    DONE_KEY = "staff.mod.case.comment_done"

    def __init__(self, *, bot, staff_id, case_id, reference, locale="en-US", on_done=None):
        super().__init__(title=t(self.TITLE_KEY, locale=locale)[:45], timeout=600)
        self.bot = bot
        self.staff_id = staff_id
        self.case_id = case_id
        self.reference = reference
        self.locale = locale
        self.on_done = on_done

        self.text_input = ui.TextInput(
            label=t(self.LABEL_KEY, locale=locale)[:45],
            style=discord.TextStyle.paragraph,
            required=True, max_length=1500,
        )
        self.add_item(self.text_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.bot.db.add_event(
            self.case_id, self.EVENT_TYPE,
            author_type="moddy_staff", author_id=self.staff_id,
            content=self.text_input.value.strip(),
        )
        if self.on_done:
            await self.on_done(interaction)
        else:
            await interaction.followup.send(view=design.success(
                t(self.DONE_TITLE_KEY, locale=self.locale),
                t(self.DONE_KEY, locale=self.locale, id=f"`{self.reference}`"),
            ), ephemeral=True)


class CaseCommentModal(_TimelineModal):
    EVENT_TYPE = "comment"
    TITLE_KEY = "staff.mod.case.comment_title"
    LABEL_KEY = "staff.mod.case.comment_label"
    DONE_TITLE_KEY = "staff.mod.case.comment_done_title"
    DONE_KEY = "staff.mod.case.comment_done"


class CaseNoteModal(_TimelineModal):
    EVENT_TYPE = "note"
    TITLE_KEY = "staff.mod.case.note_title"
    LABEL_KEY = "staff.mod.case.note_label"
    DONE_TITLE_KEY = "staff.mod.case.note_done_title"
    DONE_KEY = "staff.mod.case.note_done"


class EditReasonModal(BaseModal):
    """Edit the case reason."""

    def __init__(self, *, bot, staff_id, case_id, reference, current_reason,
                 locale="en-US", on_done=None):
        super().__init__(title=t("staff.mod.case.edit_title", locale=locale)[:45], timeout=600)
        self.bot = bot
        self.staff_id = staff_id
        self.case_id = case_id
        self.reference = reference
        self.locale = locale
        self.on_done = on_done

        self.reason_input = ui.TextInput(
            label=t("staff.mod.case.reason", locale=locale)[:45],
            style=discord.TextStyle.paragraph,
            default=(current_reason or "")[:1500],
            required=True, max_length=1500,
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        ok = await self.bot.db.update_case_reason(self.case_id, self.reason_input.value.strip())
        if not ok:
            await interaction.followup.send(view=design.error(
                t("staff.mod.case.notfound_title", locale=self.locale),
                t("staff.mod.case.notfound", locale=self.locale, id=f"`{self.reference}`"),
            ), ephemeral=True)
            return
        if self.on_done:
            await self.on_done(interaction)
        else:
            await interaction.followup.send(view=design.success(
                t("staff.mod.case.edit_done_title", locale=self.locale),
                t("staff.mod.case.edit_done", locale=self.locale, id=f"`{self.reference}`"),
            ), ephemeral=True)
