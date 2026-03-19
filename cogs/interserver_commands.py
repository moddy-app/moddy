"""
Commandes /interserver - Gestion des messages inter-serveur
Permet de signaler et d'obtenir des informations sur les messages inter-serveur
"""

import discord
from discord import app_commands, ui
from discord.ext import commands
from typing import Optional, Dict
import logging
from datetime import datetime, timezone

from utils.i18n import t
from utils.emojis import EMOJIS
from utils.components_v2 import create_error_message, create_info_message, create_success_message
from cogs.error_handler import BaseView, BaseModal

logger = logging.getLogger('moddy.cogs.interserver_commands')

# IDs des salons de rapports et de logs
REPORT_CHANNEL_ID = 1446560294733086750  # Salon de rapports généraux
ENGLISH_LOG_CHANNEL_ID = 1446555149031047388  # Logs anglais
FRENCH_LOG_CHANNEL_ID = 1446555476044284045  # Logs français


class InterServerCommands(commands.GroupCog, name="interserver"):
    """Commandes pour gérer les messages inter-serveur"""

    def __init__(self, bot):
        self.bot = bot
        super().__init__()

    async def _get_message_by_id(self, message_id: str) -> Optional[Dict]:
        """
        Récupère un message inter-serveur soit par ID MODDY (F6ZEU3VS) soit par snowflake Discord

        Args:
            message_id: L'ID du message (MODDY ou snowflake)

        Returns:
            Dict avec les données du message ou None si non trouvé
        """
        import re

        # Normalise l'ID
        message_id = message_id.strip().upper()

        # Vérifie si c'est un ID MODDY (8 caractères alphanumériques)
        if re.match(r'^[A-Z0-9]{8}$', message_id):
            # Format MODDY ID
            return await self.bot.db.get_interserver_message(message_id)

        # Sinon, essaie de le traiter comme un snowflake Discord
        try:
            snowflake_id = int(message_id)
            return await self.bot.db.get_interserver_message_by_original(snowflake_id)
        except ValueError:
            # Ce n'est ni un MODDY ID ni un snowflake valide
            return None

    @app_commands.command(
        name="report",
        description="Report an inter-server message to the moderation team"
    )
    @app_commands.guild_only()
    @app_commands.describe(
        message_id="The message ID to report (Moddy ID like F6ZEU3VS or Discord message ID)"
    )
    async def report(
        self,
        interaction: discord.Interaction,
        message_id: str
    ):
        """Signale un message inter-serveur à l'équipe de modération"""
        try:
            # Récupère les informations du message (supporte Moddy ID et snowflake)
            msg_data = await self._get_message_by_id(message_id)

            if not msg_data:
                view = create_error_message(
                    "Message Not Found",
                    f"No inter-server message found with ID `{message_id}`.\n\nMake sure the ID is correct (Moddy ID like `F6ZEU3VS` or Discord message ID) and the message hasn't been deleted."
                )
                await interaction.response.send_message(view=view, ephemeral=True)
                return
        except Exception as e:
            logger.error(f"Error validating/fetching message {message_id}: {e}", exc_info=True)
            view = create_error_message(
                "Error",
                f"An error occurred while fetching the message. Please verify the ID and try again."
            )
            await interaction.response.send_message(view=view, ephemeral=True)
            return

        # Récupère le Moddy ID réel du message
        moddy_id = msg_data['moddy_id']

        # Vérifie si le message est déjà supprimé
        if msg_data['status'] == 'deleted':
            view = create_error_message(
                "Message Already Deleted",
                f"The message `{moddy_id}` has already been deleted."
            )
            await interaction.response.send_message(view=view, ephemeral=True)
            return

        # Crée le rapport dans le salon de rapports
        try:
            report_channel = self.bot.get_channel(REPORT_CHANNEL_ID)
            if not report_channel:
                view = create_error_message(
                    "Error",
                    "Could not access the report channel. Please contact the Moddy team."
                )
                await interaction.response.send_message(view=view, ephemeral=True)
                return

            # Récupère l'auteur du message
            try:
                author = await self.bot.fetch_user(msg_data['author_id'])
                author_mention = f"{author.mention} (`{author.id}`)"
            except:
                author_mention = f"Unknown User (`{msg_data['author_id']}`)"

            # Crée le rapport avec Components V2
            class ReportView(BaseView):
                def __init__(self, bot, moddy_id: str, reporter_id: int, author_mention: str, guild_name: str, guild_id: int, reporter_mention: str, content: str):
                    super().__init__()
                    self.bot = bot
                    self.moddy_id = moddy_id
                    self.reporter_id = reporter_id
                    self.claimed_by = None
                    self.author_mention = author_mention
                    self.guild_name = guild_name
                    self.guild_id = guild_id
                    self.reporter_mention = reporter_mention
                    self.content = content

                    self._build_view()

                def _build_view(self):
                    """Construit la vue avec containers et boutons"""
                    # Clear items
                    self.clear_items()

                    # Container avec les informations
                    container = ui.Container(
                        ui.TextDisplay(content=f"### <:warning:1398729560895422505> Inter-Server Report"),
                        ui.TextDisplay(content=f"**Moddy ID:** `{self.moddy_id}`\n**Author:** {self.author_mention}\n**Server:** {self.guild_name} (`{self.guild_id}`)\n**Reported by:** {self.reporter_mention}\n{f'**Claimed by:** {self.claimed_by.mention}' if self.claimed_by else ''}\n**Content:**\n{self.content[:1000] if self.content else '*No content*'}"),
                    )
                    self.add_item(container)

                    # ActionRow avec les boutons
                    button_row = ui.ActionRow()

                    # Claim button
                    claim_btn = ui.Button(
                        label="Claim",
                        style=discord.ButtonStyle.primary,
                        emoji="👋",
                        custom_id="claim_btn",
                        disabled=self.claimed_by is not None
                    )
                    claim_btn.callback = self.on_claim
                    button_row.add_item(claim_btn)

                    # Processed button
                    processed_btn = ui.Button(
                        label="Processed",
                        style=discord.ButtonStyle.success,
                        emoji="✅",
                        custom_id="processed_btn",
                        disabled=self.claimed_by is None
                    )
                    processed_btn.callback = self.on_processed
                    button_row.add_item(processed_btn)

                    # Skip button
                    skip_btn = ui.Button(
                        label="Skip",
                        style=discord.ButtonStyle.secondary,
                        emoji="⏭️",
                        custom_id="skip_btn",
                        disabled=self.claimed_by is not None
                    )
                    skip_btn.callback = self.on_skip
                    button_row.add_item(skip_btn)

                    self.add_item(button_row)

                async def on_claim(self, interaction: discord.Interaction):
                    """Permet à un modérateur de claim le rapport"""
                    # Vérifie les permissions
                    from utils.staff_permissions import staff_permissions, StaffRole
                    user_roles = await staff_permissions.get_user_roles(interaction.user.id)

                    # Vérifie si l'utilisateur est au moins modérateur
                    allowed_roles = [StaffRole.DEV, StaffRole.MANAGER, StaffRole.SUPERVISOR_MOD, StaffRole.MODERATOR]
                    if not any(role in allowed_roles for role in user_roles):
                        await interaction.response.send_message(
                            "You don't have permission to claim reports.",
                            ephemeral=True
                        )
                        return

                    self.claimed_by = interaction.user
                    self._build_view()
                    await interaction.response.edit_message(view=self)

                async def on_processed(self, interaction: discord.Interaction):
                    """Marque le rapport comme traité avec un formulaire pour les actions prises"""
                    # Ouvre un modal pour les actions prises
                    modal = ProcessedModal(self.moddy_id)
                    modal.bot = self.bot
                    await interaction.response.send_modal(modal)

                async def on_skip(self, interaction: discord.Interaction):
                    """Skip le rapport sans raison"""
                    # Vérifie les permissions
                    from utils.staff_permissions import staff_permissions, StaffRole
                    user_roles = await staff_permissions.get_user_roles(interaction.user.id)

                    allowed_roles = [StaffRole.DEV, StaffRole.MANAGER, StaffRole.SUPERVISOR_MOD, StaffRole.MODERATOR]
                    if not any(role in allowed_roles for role in user_roles):
                        await interaction.response.send_message(
                            "You don't have permission to skip reports.",
                            ephemeral=True
                        )
                        return

                    # Clear and rebuild view with skipped message
                    self.clear_items()
                    container = ui.Container(
                        ui.TextDisplay(content=f"### <:warning:1398729560895422505> Inter-Server Report - Skipped"),
                        ui.TextDisplay(content=f"**Moddy ID:** `{self.moddy_id}`\n**Skipped by:** {interaction.user.mention}\n**Reason:** No action required"),
                    )
                    self.add_item(container)

                    await interaction.response.edit_message(view=self)

            # Envoie le rapport
            report_view = ReportView(
                self.bot,
                moddy_id,
                interaction.user.id,
                author_mention,
                interaction.guild.name,
                interaction.guild.id,
                interaction.user.mention,
                msg_data['content']
            )
            await report_channel.send(view=report_view)

            # Confirme à l'utilisateur
            view = create_success_message(
                "Report Sent",
                f"Your report for message `{moddy_id}` has been sent to the moderation team.\n\nThank you for helping keep the inter-server chat safe!"
            )
            await interaction.response.send_message(view=view, ephemeral=True)

            logger.info(f"Report sent for message {moddy_id} by {interaction.user} ({interaction.user.id})")

        except Exception as e:
            logger.error(f"Error sending report: {e}", exc_info=True)
            view = create_error_message(
                "Error",
                f"An error occurred while sending your report. Please try again later."
            )
            await interaction.response.send_message(view=view, ephemeral=True)

    @app_commands.command(
        name="info",
        description="Get information about an inter-server message"
    )
    @app_commands.guild_only()
    @app_commands.describe(
        message_id="The message ID to get info about (Moddy ID like F6ZEU3VS or Discord message ID)",
        incognito="Hide your identity in the request (default: False)"
    )
    async def info(
        self,
        interaction: discord.Interaction,
        message_id: str,
        incognito: bool = False
    ):
        """Obtient des informations sur un message inter-serveur"""
        try:
            # Récupère les informations du message (supporte Moddy ID et snowflake)
            msg_data = await self._get_message_by_id(message_id)

            if not msg_data:
                view = create_error_message(
                    "Message Not Found",
                    f"No inter-server message found with ID `{message_id}`.\n\nMake sure the ID is correct (Moddy ID like `F6ZEU3VS` or Discord message ID) and the message hasn't been deleted."
                )
                await interaction.response.send_message(view=view, ephemeral=True)
                return
        except Exception as e:
            logger.error(f"Error validating/fetching message {message_id}: {e}", exc_info=True)
            view = create_error_message(
                "Error",
                f"An error occurred while fetching the message. Please verify the ID and try again."
            )
            await interaction.response.send_message(view=view, ephemeral=True)
            return

        # Récupère le Moddy ID réel du message
        moddy_id = msg_data['moddy_id']

        # Récupère l'auteur du message
        try:
            author = await self.bot.fetch_user(msg_data['author_id'])
            author_info = f"{author.mention} (`{author.id}`)"
        except:
            author_info = f"Unknown User (`{msg_data['author_id']}`)"

        # Récupère le serveur d'origine
        original_guild = self.bot.get_guild(msg_data['original_guild_id'])
        guild_info = f"{original_guild.name} (`{original_guild.id}`)" if original_guild else f"Unknown Server (`{msg_data['original_guild_id']}`)"

        # Compte les serveurs relayés
        relayed_count = len(msg_data.get('relayed_messages', []))

        # Format timestamp
        timestamp = msg_data.get('timestamp', msg_data.get('created_at'))
        timestamp_str = f"<t:{int(timestamp.timestamp())}:R>" if timestamp else "Unknown"

        # Crée l'interface avec Components V2
        class InfoView(BaseView):
            def __init__(self):
                super().__init__()
                container = ui.Container(
                    ui.TextDisplay(content=f"### <:info:1401614681440784477> Inter-Server Message Info"),
                    ui.TextDisplay(content=f"**Moddy ID:** `{moddy_id}`\n**Author:** {author_info}\n**Original Server:** {guild_info}\n**Sent:** {timestamp_str}\n**Relayed to:** {relayed_count} servers\n**Status:** {msg_data['status']}\n**Moddy Team Message:** {'✅ Yes' if msg_data.get('is_moddy_team') else '❌ No'}\n\n**Content:**\n{msg_data['content'][:500] if msg_data['content'] else '*No content*'}"),
                )
                self.add_item(container)

        view = InfoView()

        # Log l'info request si pas incognito
        if not incognito:
            logger.info(f"Info request for message {moddy_id} by {interaction.user} ({interaction.user.id})")

        await interaction.response.send_message(view=view, ephemeral=True)


class ProcessedModal(BaseModal, title="Report Processing"):
    """Modal pour les actions prises sur un rapport"""

    actions_taken = discord.ui.TextInput(
        label="Actions Taken",
        placeholder="Describe the actions you took...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000
    )

    def __init__(self, moddy_id: str):
        super().__init__()
        self.moddy_id = moddy_id

    async def on_submit(self, interaction: discord.Interaction):
        """Appelé quand le formulaire est soumis"""
        # Crée une nouvelle vue avec Components V2 pour afficher le message de succès
        class ProcessedView(BaseView):
            def __init__(self, moddy_id: str, processed_by: discord.User, actions: str):
                super().__init__()
                container = ui.Container(
                    ui.TextDisplay(content=f"### <:done:1398729525277229066> Report Processed"),
                    ui.TextDisplay(content=f"**Moddy ID:** `{moddy_id}`\n**Processed by:** {processed_by.mention}\n**Actions taken:**\n{actions}"),
                )
                self.add_item(container)

        view = ProcessedView(self.moddy_id, interaction.user, self.actions_taken.value)
        await interaction.response.edit_message(view=view)

        logger.info(f"Report {self.moddy_id} processed by {interaction.user} ({interaction.user.id})")


async def setup(bot):
    await bot.add_cog(InterServerCommands(bot))
