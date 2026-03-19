"""
Case Management Views and Modals
Components V2 interfaces for managing moderation cases
"""

import discord
from discord import ui
from typing import Optional, Callable
from datetime import datetime, timezone, timedelta
import logging

from cogs.error_handler import BaseView, BaseModal
from utils.moderation_cases import (
    CaseType, SanctionType, CaseStatus, EntityType,
    get_sanction_name, get_sanction_emoji, get_available_sanctions,
    CASE_TYPE_SANCTIONS
)
from database import db
from utils.emojis import EMOJIS

logger = logging.getLogger('moddy.case_management')


class CreateCaseModal(BaseModal, title="Create Moderation Case"):
    """Modal for creating a new moderation case"""

    def __init__(
        self,
        case_type: CaseType,
        sanction_type: SanctionType,
        entity_type: EntityType,
        entity_id: int,
        staff_id: int,
        callback_func: Callable
    ):
        super().__init__(timeout=600)
        self.case_type = case_type
        self.sanction_type = sanction_type
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.staff_id = staff_id
        self.callback_func = callback_func

        # Reason field (required)
        self.reason_input = ui.TextInput(
            label="Reason",
            style=discord.TextStyle.paragraph,
            placeholder="Provide a clear reason for this sanction...",
            required=True,
            max_length=1000
        )
        self.add_item(self.reason_input)

        # Evidence field (optional)
        self.evidence_input = ui.TextInput(
            label="Evidence/Proof",
            style=discord.TextStyle.paragraph,
            placeholder="Links to messages, screenshots, or other proof...",
            required=False,
            max_length=1000
        )
        self.add_item(self.evidence_input)

        # Duration field (only for timeout)
        if sanction_type == SanctionType.INTERSERVER_TIMEOUT:
            self.duration_input = ui.TextInput(
                label="Duration (in hours)",
                style=discord.TextStyle.short,
                placeholder="24",
                required=True,
                max_length=10
            )
            self.add_item(self.duration_input)

    async def on_submit(self, interaction: discord.Interaction):
        """Handle form submission"""
        # Get values
        reason = self.reason_input.value.strip()
        evidence = self.evidence_input.value.strip() if self.evidence_input.value else None

        # Parse duration if applicable
        duration_seconds = None
        if self.sanction_type == SanctionType.INTERSERVER_TIMEOUT:
            try:
                hours = float(self.duration_input.value.strip())
                duration_seconds = int(hours * 3600)
            except ValueError:
                await interaction.response.send_message(
                    f"{EMOJIS['error']} Invalid duration format. Please provide a number (hours).",
                    ephemeral=True
                )
                return

        # Defer response as database operation might take time
        await interaction.response.defer(ephemeral=True)

        try:
            # Create the case in database
            case_id = await db.create_moderation_case(
                case_type=self.case_type.value,
                sanction_type=self.sanction_type.value,
                entity_type=self.entity_type.value,
                entity_id=self.entity_id,
                reason=reason,
                created_by=self.staff_id,
                evidence=evidence,
                duration=duration_seconds
            )

            # Call the callback function
            await self.callback_func(interaction, case_id)

        except Exception as e:
            logger.error(f"Error creating case: {e}", exc_info=True)
            await interaction.followup.send(
                f"{EMOJIS['error']} Failed to create case: {str(e)}",
                ephemeral=True
            )


class EditCaseModal(BaseModal, title="Edit Moderation Case"):
    """Modal for editing an existing moderation case"""

    def __init__(
        self,
        case_id: int,
        current_reason: str,
        current_evidence: Optional[str],
        current_duration: Optional[int],
        sanction_type: SanctionType,
        staff_id: int,
        callback_func: Callable
    ):
        super().__init__(timeout=600)
        self.case_id = case_id
        self.sanction_type = sanction_type
        self.staff_id = staff_id
        self.callback_func = callback_func

        # Reason field (pre-filled)
        self.reason_input = ui.TextInput(
            label="Reason",
            style=discord.TextStyle.paragraph,
            default=current_reason,
            required=True,
            max_length=1000
        )
        self.add_item(self.reason_input)

        # Evidence field (pre-filled if exists)
        self.evidence_input = ui.TextInput(
            label="Evidence/Proof",
            style=discord.TextStyle.paragraph,
            default=current_evidence or "",
            required=False,
            max_length=1000
        )
        self.add_item(self.evidence_input)

        # Duration field (only for timeout, pre-filled if exists)
        if sanction_type == SanctionType.INTERSERVER_TIMEOUT:
            hours = round(current_duration / 3600, 2) if current_duration else 24
            self.duration_input = ui.TextInput(
                label="Duration (in hours)",
                style=discord.TextStyle.short,
                default=str(hours),
                required=True,
                max_length=10
            )
            self.add_item(self.duration_input)

    async def on_submit(self, interaction: discord.Interaction):
        """Handle form submission"""
        # Get values
        reason = self.reason_input.value.strip()
        evidence = self.evidence_input.value.strip() if self.evidence_input.value else None

        # Parse duration if applicable
        duration_seconds = None
        if self.sanction_type == SanctionType.INTERSERVER_TIMEOUT:
            try:
                hours = float(self.duration_input.value.strip())
                duration_seconds = int(hours * 3600)
            except ValueError:
                await interaction.response.send_message(
                    f"{EMOJIS['error']} Invalid duration format. Please provide a number (hours).",
                    ephemeral=True
                )
                return

        # Defer response
        await interaction.response.defer(ephemeral=True)

        try:
            # Update the case in database
            success = await db.update_moderation_case(
                case_id=self.case_id,
                updated_by=self.staff_id,
                reason=reason,
                evidence=evidence,
                duration=duration_seconds
            )

            if not success:
                await interaction.followup.send(
                    f"{EMOJIS['error']} Case not found or could not be updated.",
                    ephemeral=True
                )
                return

            # Call the callback function
            await self.callback_func(interaction)

        except Exception as e:
            logger.error(f"Error updating case: {e}", exc_info=True)
            await interaction.followup.send(
                f"{EMOJIS['error']} Failed to update case: {str(e)}",
                ephemeral=True
            )


class AddCaseNoteModal(BaseModal, title="Add Staff Note"):
    """Modal for adding a staff note to a case"""

    def __init__(
        self,
        case_id: int,
        staff_id: int,
        callback_func: Callable
    ):
        super().__init__(timeout=600)
        self.case_id = case_id
        self.staff_id = staff_id
        self.callback_func = callback_func

        # Note field
        self.note_input = ui.TextInput(
            label="Staff Note",
            style=discord.TextStyle.paragraph,
            placeholder="Add internal note visible only to staff...",
            required=True,
            max_length=1000
        )
        self.add_item(self.note_input)

    async def on_submit(self, interaction: discord.Interaction):
        """Handle form submission"""
        note = self.note_input.value.strip()

        await interaction.response.defer(ephemeral=True)

        try:
            # Add note to database
            success = await db.add_case_note(
                case_id=self.case_id,
                staff_id=self.staff_id,
                note=note
            )

            if not success:
                await interaction.followup.send(
                    f"{EMOJIS['error']} Case not found.",
                    ephemeral=True
                )
                return

            # Call the callback function
            await self.callback_func(interaction)

        except Exception as e:
            logger.error(f"Error adding note: {e}", exc_info=True)
            await interaction.followup.send(
                f"{EMOJIS['error']} Failed to add note: {str(e)}",
                ephemeral=True
            )


class CloseCaseModal(BaseModal, title="Close Moderation Case"):
    """Modal for closing a case"""

    def __init__(
        self,
        case_id: int,
        staff_id: int,
        callback_func: Callable
    ):
        super().__init__(timeout=600)
        self.case_id = case_id
        self.staff_id = staff_id
        self.callback_func = callback_func

        # Close reason field
        self.reason_input = ui.TextInput(
            label="Close Reason",
            style=discord.TextStyle.paragraph,
            placeholder="Explain why this case is being closed...",
            required=False,
            max_length=500
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        """Handle form submission"""
        close_reason = self.reason_input.value.strip() if self.reason_input.value else None

        await interaction.response.defer(ephemeral=True)

        try:
            # Close the case in database
            success = await db.close_moderation_case(
                case_id=self.case_id,
                closed_by=self.staff_id,
                close_reason=close_reason
            )

            if not success:
                await interaction.followup.send(
                    f"{EMOJIS['error']} Case not found or already closed.",
                    ephemeral=True
                )
                return

            # Call the callback function
            await self.callback_func(interaction)

        except Exception as e:
            logger.error(f"Error closing case: {e}", exc_info=True)
            await interaction.followup.send(
                f"{EMOJIS['error']} Failed to close case: {str(e)}",
                ephemeral=True
            )


class CaseSelectionView(BaseView):
    """View for selecting case type and sanction type"""

    def __init__(
        self,
        bot,
        staff_id: int,
        entity_type: EntityType,
        entity_id: int,
        entity_name: str
    ):
        super().__init__(timeout=180)
        self.bot = bot
        self.staff_id = staff_id
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.entity_name = entity_name

        self.selected_case_type: Optional[CaseType] = None
        self.selected_sanction_type: Optional[SanctionType] = None

        self._build_view()

    def _build_view(self):
        """Build the view with current selections"""
        self.clear_items()

        container = ui.Container()

        # Title
        entity_icon = EMOJIS['user'] if self.entity_type == EntityType.USER else "🏰"
        container.add_item(ui.TextDisplay(
            f"### {EMOJIS['settings']} Create Moderation Case\n"
            f"**Target:** {entity_icon} {self.entity_name}\n"
            f"-# Select case type and sanction below"
        ))

        # Case type selection
        case_type_row = ui.ActionRow()

        # Build options with default value if selected
        case_type_options = [
            discord.SelectOption(
                label="Inter-Server",
                value="interserver",
                description="Inter-server chat sanctions",
                emoji="🌐",
                default=(self.selected_case_type == CaseType.INTERSERVER if self.selected_case_type else False)
            ),
            discord.SelectOption(
                label="Global Bot",
                value="global",
                description="Global bot usage sanctions",
                emoji="🤖",
                default=(self.selected_case_type == CaseType.GLOBAL if self.selected_case_type else False)
            )
        ]

        case_type_select = ui.Select(
            placeholder="Select case type...",
            options=case_type_options,
            max_values=1
        )
        case_type_select.callback = self.on_case_type_select
        case_type_row.add_item(case_type_select)
        container.add_item(case_type_row)

        # Show sanction selection if case type is selected
        if self.selected_case_type:
            sanction_row = ui.ActionRow()
            available_sanctions = get_available_sanctions(self.selected_case_type)

            sanction_options = [
                discord.SelectOption(
                    label=get_sanction_name(sanction),
                    value=sanction.value,
                    emoji=get_sanction_emoji(sanction),
                    default=(self.selected_sanction_type == sanction if self.selected_sanction_type else False)
                )
                for sanction in available_sanctions
            ]

            sanction_select = ui.Select(
                placeholder="Select sanction type...",
                options=sanction_options,
                max_values=1
            )
            sanction_select.callback = self.on_sanction_select
            sanction_row.add_item(sanction_select)
            container.add_item(sanction_row)

        # Create button (enabled only when both selections are made)
        if self.selected_case_type and self.selected_sanction_type:
            button_row = ui.ActionRow()
            create_button = ui.Button(
                label="Create Case",
                style=discord.ButtonStyle.success,
                emoji=EMOJIS['done']
            )
            create_button.callback = self.on_create_button
            button_row.add_item(create_button)

            cancel_button = ui.Button(
                label="Cancel",
                style=discord.ButtonStyle.secondary,
                emoji=EMOJIS['delete']
            )
            cancel_button.callback = self.on_cancel_button
            button_row.add_item(cancel_button)

            container.add_item(button_row)

        self.add_item(container)

    async def on_case_type_select(self, interaction: discord.Interaction):
        """Handle case type selection"""
        value = interaction.data['values'][0]
        self.selected_case_type = CaseType(value)
        self.selected_sanction_type = None  # Reset sanction selection

        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_sanction_select(self, interaction: discord.Interaction):
        """Handle sanction type selection"""
        value = interaction.data['values'][0]
        self.selected_sanction_type = SanctionType(value)

        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_create_button(self, interaction: discord.Interaction):
        """Handle create case button"""
        # Open modal to get reason and evidence
        modal = CreateCaseModal(
            case_type=self.selected_case_type,
            sanction_type=self.selected_sanction_type,
            entity_type=self.entity_type,
            entity_id=self.entity_id,
            staff_id=self.staff_id,
            callback_func=self.on_case_created
        )
        modal.bot = self.bot
        await interaction.response.send_modal(modal)

    async def on_cancel_button(self, interaction: discord.Interaction):
        """Handle cancel button"""
        await interaction.message.delete()
        await interaction.response.send_message(
            f"{EMOJIS['delete']} Case creation cancelled.",
            ephemeral=True
        )

    async def on_case_created(self, interaction: discord.Interaction, case_id: str):
        """Callback after case is created"""
        # Get the created case
        case = await db.get_moderation_case(case_id)

        if not case:
            await interaction.followup.send(
                f"{EMOJIS['error']} Case was created but could not be retrieved.",
                ephemeral=True
            )
            return

        # Send success message
        await interaction.followup.send(
            f"{EMOJIS['done']} **Case #{case_id} created successfully!**\n\n"
            f"**Type:** {get_sanction_name(SanctionType(case['sanction_type']))}\n"
            f"**Target:** {self.entity_name}\n"
            f"**Status:** Open",
            ephemeral=True
        )

        # Delete the original message
        try:
            await interaction.message.delete()
        except:
            pass
