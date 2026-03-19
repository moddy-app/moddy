"""
User Cases Command
Allows users to view their own moderation cases
"""

import discord
from discord import app_commands, ui
from discord.ext import commands
from typing import Optional, List
import logging
from datetime import datetime, timezone

from database import db
from cogs.error_handler import BaseView
from utils.moderation_cases import (
    CaseType, SanctionType, CaseStatus, EntityType, ModerationCase,
    get_sanction_name, get_sanction_emoji
)
from utils.emojis import EMOJIS

logger = logging.getLogger('moddy.cases_user')


class CaseDetailView(BaseView):
    """View for displaying case details to users"""

    def __init__(self, bot, user_id: int, cases: List[dict]):
        super().__init__(timeout=180)
        self.bot = bot
        self.user_id = user_id
        self.cases = cases
        self.current_page = 0

        self._build_view()

    def _build_view(self):
        """Build the view with current case"""
        self.clear_items()

        if not self.cases:
            container = ui.Container()
            container.add_item(ui.TextDisplay(f"### {EMOJIS['info']} Your Cases"))
            container.add_item(ui.TextDisplay(
                "You have no moderation cases on record.\n"
                "-# This is a good thing!"
            ))
            self.add_item(container)
            return

        # Get current case
        case_dict = self.cases[self.current_page]
        case = ModerationCase.from_db(case_dict)

        container = ui.Container()

        # Title
        status_emoji = "🟢" if case.status == CaseStatus.OPEN else "🔴"
        container.add_item(ui.TextDisplay(
            f"### {case.get_sanction_emoji()} Case #{case.case_id}\n"
            f"{status_emoji} **Status:** {case.status.value.title()}"
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Case details
        case_type_name = "Inter-Server" if case.case_type == CaseType.INTERSERVER else "Global Bot"
        details = (
            f"**Type:** {case_type_name}\n"
            f"**Sanction:** {case.get_sanction_name()}\n"
            f"**Created:** <t:{int(case.created_at.timestamp())}:F>\n"
        )

        if case.duration:
            hours = case.duration / 3600
            details += f"**Duration:** {hours:.1f} hours\n"

        container.add_item(ui.TextDisplay(details))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Reason
        container.add_item(ui.TextDisplay(f"**Reason:**\n{case.reason}"))

        # Evidence (if any)
        if case.evidence:
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(ui.TextDisplay(f"**Evidence:**\n{case.evidence[:500]}"))

        # Close info (if closed)
        if case.status == CaseStatus.CLOSED and case.closed_at:
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            close_info = f"**Closed:** <t:{int(case.closed_at.timestamp())}:F>"
            if case.close_reason:
                close_info += f"\n**Close Reason:** {case.close_reason}"
            container.add_item(ui.TextDisplay(close_info))

        # Navigation buttons (if multiple cases)
        if len(self.cases) > 1:
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

            nav_row = ui.ActionRow()

            # Previous button
            prev_btn = ui.Button(
                label="Previous",
                style=discord.ButtonStyle.secondary,
                emoji=EMOJIS['back'],
                disabled=self.current_page == 0
            )
            prev_btn.callback = self.on_previous
            nav_row.add_item(prev_btn)

            # Page indicator
            page_info = f"Case {self.current_page + 1} of {len(self.cases)}"
            container.add_item(ui.TextDisplay(f"-# {page_info}"))

            # Next button
            next_btn = ui.Button(
                label="Next",
                style=discord.ButtonStyle.secondary,
                emoji=EMOJIS['next'],
                disabled=self.current_page >= len(self.cases) - 1
            )
            next_btn.callback = self.on_next
            nav_row.add_item(next_btn)

            container.add_item(nav_row)

        self.add_item(container)

    async def on_previous(self, interaction: discord.Interaction):
        """Go to previous case"""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                f"{EMOJIS['error']} This is not your cases view.",
                ephemeral=True
            )
            return

        self.current_page = max(0, self.current_page - 1)
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_next(self, interaction: discord.Interaction):
        """Go to next case"""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                f"{EMOJIS['error']} This is not your cases view.",
                ephemeral=True
            )
            return

        self.current_page = min(len(self.cases) - 1, self.current_page + 1)
        self._build_view()
        await interaction.response.edit_message(view=self)


class CasesUserCog(commands.Cog):
    """User command to view their own cases"""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="cases",
        description="View your moderation cases"
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def cases_command(
        self,
        interaction: discord.Interaction,
        status: Optional[str] = None
    ):
        """
        View your moderation cases

        Args:
            status: Filter by status (open/closed) - optional
        """
        await interaction.response.defer(ephemeral=True)

        try:
            # Get user cases
            cases = await db.get_entity_cases(
                entity_type='user',
                entity_id=interaction.user.id,
                status=status
            )

            # Filter out staff notes (users shouldn't see them)
            for case in cases:
                case['staff_notes'] = []  # Remove staff notes

            if not cases:
                # No cases found
                container = ui.Container()
                container.add_item(ui.TextDisplay(f"### {EMOJIS['info']} Your Cases"))

                if status:
                    message = f"You have no {status} moderation cases."
                else:
                    message = "You have no moderation cases on record.\n-# This is a good thing!"

                container.add_item(ui.TextDisplay(message))

                view = BaseView()
                view.add_item(container)

                await interaction.followup.send(view=view, ephemeral=True)
                return

            # Show cases with navigation
            view = CaseDetailView(
                bot=self.bot,
                user_id=interaction.user.id,
                cases=cases
            )

            await interaction.followup.send(view=view, ephemeral=True)

        except Exception as e:
            logger.error(f"Error fetching cases for user {interaction.user.id}: {e}", exc_info=True)
            await interaction.followup.send(
                f"{EMOJIS['error']} An error occurred while fetching your cases. Please try again later.",
                ephemeral=True
            )

    @cases_command.autocomplete('status')
    async def cases_status_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete for status parameter"""
        statuses = ['open', 'closed']
        return [
            app_commands.Choice(name=status.title(), value=status)
            for status in statuses
            if current.lower() in status.lower()
        ]


async def setup(bot):
    await bot.add_cog(CasesUserCog(bot))
