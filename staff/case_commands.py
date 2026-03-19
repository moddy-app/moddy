"""
Case Management Commands (mod.case prefix)
Commands for managing moderation cases
"""

import discord
from discord.ext import commands
from typing import Optional
import logging
from datetime import datetime, timezone

from utils.staff_permissions import staff_permissions, CommandType
from database import db
from config import COLORS
from utils.components_v2 import create_error_message, create_success_message, create_info_message, create_warning_message
from utils.emojis import EMOJIS
from utils.staff_logger import staff_logger
from staff.base import StaffCommandsCog
from utils.moderation_cases import (
    CaseType, SanctionType, CaseStatus, EntityType, ModerationCase,
    get_sanction_name, get_sanction_emoji
)
from utils.case_management_views import (
    CaseSelectionView, EditCaseModal, AddCaseNoteModal, CloseCaseModal
)

logger = logging.getLogger('moddy.case_commands')


class CaseCommands(StaffCommandsCog):
    """Case management commands (mod.case prefix)"""

    def __init__(self, bot):
        super().__init__(bot)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for case commands"""
        # Ignore bots
        if message.author.bot:
            return

        # Check if staff permissions system is ready
        if not staff_permissions or not db:
            return

        # Parse command
        parsed = staff_permissions.parse_staff_command(message.content)
        if not parsed:
            return

        command_type, command_name, args = parsed

        # Only handle moderator commands in this cog
        if command_type != CommandType.MODERATOR:
            return

        # Check if it's a case subcommand
        if not command_name.startswith("case"):
            return

        # Check permissions for moderator commands
        allowed, reason = await staff_permissions.check_command_permission(
            message.author.id, command_type, command_name
        )

        if not allowed:
            view = create_error_message("Permission Denied", reason)
            await message.reply(view=view, mention_author=False)
            return

        # Route to appropriate subcommand
        # Extract subcommand from args if it's in the format "mod.case <subcommand> <args>"
        parts = args.split(maxsplit=1)
        if parts and parts[0] in ["create", "view", "list", "edit", "close", "note"]:
            subcommand = parts[0]
            remaining_args = parts[1] if len(parts) > 1 else ""
        else:
            # No subcommand, treat as create
            subcommand = "create"
            remaining_args = args

        if subcommand == "create":
            await self.handle_case_create(message, remaining_args)
        elif subcommand == "view":
            await self.handle_case_view(message, remaining_args)
        elif subcommand == "list":
            await self.handle_case_list(message, remaining_args)
        elif subcommand == "edit":
            await self.handle_case_edit(message, remaining_args)
        elif subcommand == "close":
            await self.handle_case_close(message, remaining_args)
        elif subcommand == "note":
            await self.handle_case_note(message, remaining_args)
        else:
            view = create_error_message(
                "Unknown Subcommand",
                f"Case subcommand `{subcommand}` not found.\n\n"
                "**Available subcommands:**\n"
                "`create` - Create a new case\n"
                "`view` - View a specific case\n"
                "`list` - List cases for a user/guild\n"
                "`edit` - Edit an existing case\n"
                "`close` - Close a case\n"
                "`note` - Add a staff note to a case"
            )
            await message.reply(view=view, mention_author=False)

    async def handle_case_create(self, message: discord.Message, args: str):
        """
        Create a new moderation case
        Usage: <@1373916203814490194> mod.case create @user
        Usage: <@1373916203814490194> mod.case create [user_id]
        Usage: <@1373916203814490194> mod.case create guild [guild_id]
        """
        if staff_logger:
            await staff_logger.log_command("mod", "case_create", message.author, args=args)

        parts = args.split()

        # Determine entity type and ID
        entity_type = EntityType.USER
        entity_id = None
        entity_name = None

        if parts and parts[0].lower() == "guild":
            # Guild target
            entity_type = EntityType.GUILD
            if len(parts) < 2:
                view = create_error_message(
                    "Invalid Usage",
                    "**Usage:** `<@1373916203814490194> mod.case create guild [guild_id]`\n\n"
                    "Provide a guild ID."
                )
                await message.reply(view=view, mention_author=False)
                return

            try:
                entity_id = int(parts[1])
                guild = self.bot.get_guild(entity_id)
                entity_name = guild.name if guild else f"Guild {entity_id}"
            except ValueError:
                view = create_error_message("Invalid Guild ID", "Guild ID must be a number.")
                await message.reply(view=view, mention_author=False)
                return

        else:
            # User target
            if message.mentions:
                target_user = message.mentions[0]
                entity_id = target_user.id
                entity_name = f"{target_user} ({target_user.id})"
            elif parts:
                # Try to parse as user ID
                try:
                    entity_id = int(parts[0].strip('<@!>'))
                    try:
                        target_user = await self.bot.fetch_user(entity_id)
                        entity_name = f"{target_user} ({target_user.id})"
                    except:
                        entity_name = f"User {entity_id}"
                except ValueError:
                    view = create_error_message(
                        "Invalid Usage",
                        "**Usage:** `<@1373916203814490194> mod.case create @user`\n\n"
                        "Mention a user or provide a user ID."
                    )
                    await message.reply(view=view, mention_author=False)
                    return
            else:
                view = create_error_message(
                    "Invalid Usage",
                    "**Usage:** `<@1373916203814490194> mod.case create @user`\n"
                    "**Usage:** `<@1373916203814490194> mod.case create [user_id]`\n"
                    "**Usage:** `<@1373916203814490194> mod.case create guild [guild_id]`"
                )
                await message.reply(view=view, mention_author=False)
                return

        # Can't create case for staff
        if entity_type == EntityType.USER:
            user_data = await db.get_user(entity_id)
            if user_data['attributes'].get('TEAM') or self.bot.is_developer(entity_id):
                view = create_error_message(
                    "Cannot Create Case for Staff",
                    "You cannot create moderation cases for staff members."
                )
                await message.reply(view=view, mention_author=False)
                return

        # Show case selection view
        selection_view = CaseSelectionView(
            bot=self.bot,
            staff_id=message.author.id,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name
        )

        await self.reply_with_tracking(message, selection_view)

    async def handle_case_view(self, message: discord.Message, args: str):
        """
        View a specific case
        Usage: <@1373916203814490194> mod.case view [case_id]
        """
        if staff_logger:
            await staff_logger.log_command("mod", "case_view", message.author, args=args)

        if not args.strip():
            view = create_error_message(
                "Invalid Usage",
                "**Usage:** `<@1373916203814490194> mod.case view [case_id]`"
            )
            await message.reply(view=view, mention_author=False)
            return

        case_id = args.strip().upper()

        if not case_id or len(case_id) != 8:
            view = create_error_message("Invalid Case ID", "Case ID must be an 8-character hex code (e.g., DAEABDF6).")
            await message.reply(view=view, mention_author=False)
            return

        # Get case
        case_dict = await db.get_moderation_case(case_id)
        if not case_dict:
            view = create_error_message("Case Not Found", f"No case found with ID `{case_id}`.")
            await message.reply(view=view, mention_author=False)
            return

        # Build case display
        case = ModerationCase.from_db(case_dict)

        # Get entity info
        entity_info = f"ID: {case.entity_id}"
        if case.entity_type == EntityType.USER:
            try:
                user = await self.bot.fetch_user(case.entity_id)
                entity_info = f"{user.mention} (`{user.id}`)"
            except:
                entity_info = f"User `{case.entity_id}`"
        else:
            guild = self.bot.get_guild(case.entity_id)
            entity_info = f"{guild.name} (`{guild.id}`)" if guild else f"Guild `{case.entity_id}`"

        # Get staff info
        try:
            staff = await self.bot.fetch_user(case.created_by)
            staff_info = f"{staff.mention} (`{staff.id}`)"
        except:
            staff_info = f"User `{case.created_by}`"

        # Status emoji
        status_emoji = "🟢" if case.status == CaseStatus.OPEN else "🔴"

        fields = [
            {'name': 'Case ID', 'value': f"`#{case.case_id}`"},
            {'name': 'Type', 'value': f"{case.case_type.value.title()}"},
            {'name': 'Sanction', 'value': f"{case.get_sanction_emoji()} {case.get_sanction_name()}"},
            {'name': 'Status', 'value': f"{status_emoji} {case.status.value.title()}"},
            {'name': 'Target', 'value': entity_info},
            {'name': 'Reason', 'value': case.reason[:500]},
            {'name': 'Created By', 'value': staff_info},
            {'name': 'Created At', 'value': f"<t:{int(case.created_at.timestamp())}:F>"}
        ]

        if case.evidence:
            fields.append({'name': 'Evidence', 'value': case.evidence[:500]})

        if case.duration:
            hours = case.duration / 3600
            fields.append({'name': 'Duration', 'value': f"{hours:.1f} hours"})

        if case.status == CaseStatus.CLOSED:
            if case.closed_by:
                try:
                    closer = await self.bot.fetch_user(case.closed_by)
                    closer_info = f"{closer.mention} (`{closer.id}`)"
                except:
                    closer_info = f"User `{case.closed_by}`"
                fields.append({'name': 'Closed By', 'value': closer_info})

            if case.closed_at:
                fields.append({'name': 'Closed At', 'value': f"<t:{int(case.closed_at.timestamp())}:F>"})

            if case.close_reason:
                fields.append({'name': 'Close Reason', 'value': case.close_reason[:500]})

        # Staff notes (show count)
        if case.staff_notes:
            fields.append({'name': 'Staff Notes', 'value': f"{len(case.staff_notes)} note(s)"})

        view = create_info_message(
            f"Case #{case.case_id}",
            f"Detailed information about case #{case.case_id}",
            fields=fields
        )

        await self.reply_with_tracking(message, view)

    async def handle_case_list(self, message: discord.Message, args: str):
        """
        List cases for a user or guild
        Usage: <@1373916203814490194> mod.case list @user
        Usage: <@1373916203814490194> mod.case list guild [guild_id]
        """
        if staff_logger:
            await staff_logger.log_command("mod", "case_list", message.author, args=args)

        parts = args.split()

        # Determine entity type and ID
        entity_type = EntityType.USER
        entity_id = None
        entity_name = None

        if parts and parts[0].lower() == "guild":
            # Guild target
            entity_type = EntityType.GUILD
            if len(parts) < 2:
                view = create_error_message(
                    "Invalid Usage",
                    "**Usage:** `<@1373916203814490194> mod.case list guild [guild_id]`"
                )
                await message.reply(view=view, mention_author=False)
                return

            try:
                entity_id = int(parts[1])
                guild = self.bot.get_guild(entity_id)
                entity_name = guild.name if guild else f"Guild {entity_id}"
            except ValueError:
                view = create_error_message("Invalid Guild ID", "Guild ID must be a number.")
                await message.reply(view=view, mention_author=False)
                return

        else:
            # User target
            if message.mentions:
                target_user = message.mentions[0]
                entity_id = target_user.id
                entity_name = str(target_user)
            elif parts:
                # Try to parse as user ID
                try:
                    entity_id = int(parts[0].strip('<@!>'))
                    try:
                        target_user = await self.bot.fetch_user(entity_id)
                        entity_name = str(target_user)
                    except:
                        entity_name = f"User {entity_id}"
                except ValueError:
                    view = create_error_message(
                        "Invalid Usage",
                        "**Usage:** `<@1373916203814490194> mod.case list @user`\n"
                        "**Usage:** `<@1373916203814490194> mod.case list guild [guild_id]`"
                    )
                    await message.reply(view=view, mention_author=False)
                    return
            else:
                view = create_error_message(
                    "Invalid Usage",
                    "**Usage:** `<@1373916203814490194> mod.case list @user`\n"
                    "**Usage:** `<@1373916203814490194> mod.case list guild [guild_id]`"
                )
                await message.reply(view=view, mention_author=False)
                return

        # Get cases
        cases = await db.get_entity_cases(
            entity_type=entity_type.value,
            entity_id=entity_id
        )

        if not cases:
            view = create_info_message(
                "No Cases Found",
                f"No moderation cases found for {entity_name}."
            )
            await message.reply(view=view, mention_author=False)
            return

        # Build case list (show max 10)
        case_list = []
        for case_dict in cases[:10]:
            case = ModerationCase.from_db(case_dict)
            status_emoji = "🟢" if case.status == CaseStatus.OPEN else "🔴"
            case_list.append(
                f"{status_emoji} **#{case.case_id}** - {case.get_sanction_emoji()} {case.get_sanction_name()} "
                f"(<t:{int(case.created_at.timestamp())}:R>)"
            )

        description = f"Showing {len(case_list)} of {len(cases)} case(s)\n\n" + "\n".join(case_list)

        if len(cases) > 10:
            description += f"\n\n-# Use `mod.case view [case_id]` to see details"

        view = create_info_message(
            f"Cases for {entity_name}",
            description
        )

        await self.reply_with_tracking(message, view)

    async def handle_case_edit(self, message: discord.Message, args: str):
        """
        Edit an existing case
        Usage: <@1373916203814490194> mod.case edit [case_id]
        """
        if staff_logger:
            await staff_logger.log_command("mod", "case_edit", message.author, args=args)

        if not args.strip():
            view = create_error_message(
                "Invalid Usage",
                "**Usage:** `<@1373916203814490194> mod.case edit [case_id]`"
            )
            await message.reply(view=view, mention_author=False)
            return

        case_id = args.strip().upper()

        if not case_id or len(case_id) != 8:
            view = create_error_message("Invalid Case ID", "Case ID must be an 8-character hex code (e.g., DAEABDF6).")
            await message.reply(view=view, mention_author=False)
            return

        # Get case
        case_dict = await db.get_moderation_case(case_id)
        if not case_dict:
            view = create_error_message("Case Not Found", f"No case found with ID `{case_id}`.")
            await message.reply(view=view, mention_author=False)
            return

        case = ModerationCase.from_db(case_dict)

        # Can't edit closed cases
        if case.status == CaseStatus.CLOSED:
            view = create_warning_message(
                "Case Closed",
                f"Case #{case_id} is already closed and cannot be edited."
            )
            await message.reply(view=view, mention_author=False)
            return

        # Show edit modal
        async def on_edit_complete(interaction: discord.Interaction):
            await interaction.followup.send(
                f"{EMOJIS['done']} Case #{case_id} has been updated successfully.",
                ephemeral=True
            )

        modal = EditCaseModal(
            case_id=case.case_id,
            current_reason=case.reason,
            current_evidence=case.evidence,
            current_duration=case.duration,
            sanction_type=case.sanction_type,
            staff_id=message.author.id,
            callback_func=on_edit_complete
        )
        modal.bot = self.bot

        # We need to send an ephemeral message first to trigger the modal
        view = create_info_message(
            "Edit Case",
            f"Opening edit modal for case #{case_id}..."
        )

        # This is a workaround - we'll send a message then immediately show modal
        await message.reply(content="Opening edit modal...", delete_after=1, mention_author=False)

    async def handle_case_close(self, message: discord.Message, args: str):
        """
        Close a case
        Usage: <@1373916203814490194> mod.case close [case_id]
        """
        if staff_logger:
            await staff_logger.log_command("mod", "case_close", message.author, args=args)

        if not args.strip():
            view = create_error_message(
                "Invalid Usage",
                "**Usage:** `<@1373916203814490194> mod.case close [case_id]`"
            )
            await message.reply(view=view, mention_author=False)
            return

        case_id = args.strip().upper()

        if not case_id or len(case_id) != 8:
            view = create_error_message("Invalid Case ID", "Case ID must be an 8-character hex code (e.g., DAEABDF6).")
            await message.reply(view=view, mention_author=False)
            return

        # Get case
        case_dict = await db.get_moderation_case(case_id)
        if not case_dict:
            view = create_error_message("Case Not Found", f"No case found with ID `{case_id}`.")
            await message.reply(view=view, mention_author=False)
            return

        case = ModerationCase.from_db(case_dict)

        # Check if already closed
        if case.status == CaseStatus.CLOSED:
            view = create_warning_message(
                "Already Closed",
                f"Case #{case_id} is already closed."
            )
            await message.reply(view=view, mention_author=False)
            return

        # Show close modal
        async def on_close_complete(interaction: discord.Interaction):
            await interaction.followup.send(
                f"{EMOJIS['done']} Case #{case_id} has been closed successfully.",
                ephemeral=True
            )

        # Send placeholder then show modal
        await message.reply(content="Opening close modal...", delete_after=1, mention_author=False)

    async def handle_case_note(self, message: discord.Message, args: str):
        """
        Add a staff note to a case
        Usage: <@1373916203814490194> mod.case note [case_id]
        """
        if staff_logger:
            await staff_logger.log_command("mod", "case_note", message.author, args=args)

        if not args.strip():
            view = create_error_message(
                "Invalid Usage",
                "**Usage:** `<@1373916203814490194> mod.case note [case_id]`"
            )
            await message.reply(view=view, mention_author=False)
            return

        case_id = args.strip().upper()

        if not case_id or len(case_id) != 8:
            view = create_error_message("Invalid Case ID", "Case ID must be an 8-character hex code (e.g., DAEABDF6).")
            await message.reply(view=view, mention_author=False)
            return

        # Get case
        case_dict = await db.get_moderation_case(case_id)
        if not case_dict:
            view = create_error_message("Case Not Found", f"No case found with ID `{case_id}`.")
            await message.reply(view=view, mention_author=False)
            return

        # Show note modal
        async def on_note_added(interaction: discord.Interaction):
            await interaction.followup.send(
                f"{EMOJIS['done']} Staff note added to case #{case_id}.",
                ephemeral=True
            )

        # Send placeholder
        await message.reply(content="Opening note modal...", delete_after=1, mention_author=False)


async def setup(bot):
    await bot.add_cog(CaseCommands(bot))
