"""
Configuration UI pour le module Inter-Server
Utilise les Composants V2 pour une interface moderne
"""

import discord
from discord import ui
from typing import Optional, Dict, Any
import logging

from utils.i18n import t
from cogs.error_handler import BaseView
from utils.emojis import GROUPS, REQUIRED_FIELDS, WARNING, BACK, SAVE, UNDONE, DELETE

logger = logging.getLogger('moddy.modules.interserver_config')


class InterServerConfigView(BaseView):
    """
    Interface de configuration du module Inter-Server
    Permet de configurer le salon de communication inter-serveurs
    """

    def __init__(self, bot, guild_id: int, user_id: int, locale: str, current_config: Optional[Dict[str, Any]] = None):
        """
        Initialise la vue de configuration

        Args:
            bot: Instance du bot
            guild_id: ID du serveur
            user_id: ID de l'utilisateur qui configure
            locale: Langue de l'utilisateur
            current_config: Configuration actuelle du module (None si première config)
        """
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale

        # Configuration actuelle (ou par défaut)
        # Vérifie si on a une vraie config sauvegardée en regardant channel_id
        if current_config and current_config.get('channel_id') is not None:
            self.current_config = current_config.copy()
            self.has_existing_config = True
        else:
            # Utilise la config par défaut du module
            from modules.interserver import InterServerModule
            self.current_config = InterServerModule(bot, guild_id).get_default_config()
            self.has_existing_config = False

        # Configuration en cours de modification (copie de travail)
        self.working_config = self.current_config.copy()

        # État de modification
        self.has_changes = False

        self._build_view()

    def _build_view(self):
        """Construit l'interface de configuration"""
        self.clear_items()

        container = ui.Container()

        # Titre et description
        container.add_item(ui.TextDisplay(
            f"### {GROUPS} {t('modules.interserver.config.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t('modules.interserver.config.description', locale=self.locale)
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Section : Salon inter-serveur
        container.add_item(ui.TextDisplay(
            f"**{t('modules.interserver.config.channel.section_title', locale=self.locale)}**{REQUIRED_FIELDS}\n"
            f"-# {t('modules.interserver.config.channel.section_description', locale=self.locale)}"
        ))

        # Sélecteur de salon
        channel_row = ui.ActionRow()
        channel_select = ui.ChannelSelect(
            placeholder=t('modules.interserver.config.channel.placeholder', locale=self.locale),
            channel_types=[discord.ChannelType.text],
            min_values=0,
            max_values=1
        )

        # Pré-sélectionne le salon actuel si configuré
        if self.working_config.get('channel_id'):
            channel = self.bot.get_channel(self.working_config['channel_id'])
            if channel:
                channel_select.default_values = [channel]

        channel_select.callback = self.on_channel_select
        channel_row.add_item(channel_select)
        container.add_item(channel_row)

        # Section : Type d'inter-serveur
        container.add_item(ui.TextDisplay(
            f"**{t('modules.interserver.config.type.section_title', locale=self.locale)}**{REQUIRED_FIELDS}\n"
            f"-# {t('modules.interserver.config.type.section_description', locale=self.locale)}"
        ))

        # Sélecteur de type d'inter-serveur
        type_row = ui.ActionRow()
        type_select = ui.Select(
            placeholder=t('modules.interserver.config.type.placeholder', locale=self.locale),
            options=[
                discord.SelectOption(
                    label=t('modules.interserver.config.type.english', locale=self.locale),
                    value="english",
                    description=t('modules.interserver.config.type.english_desc', locale=self.locale),
                    emoji="🇬🇧",
                    default=self.working_config.get('interserver_type', 'english') == 'english'
                ),
                discord.SelectOption(
                    label=t('modules.interserver.config.type.french', locale=self.locale),
                    value="french",
                    description=t('modules.interserver.config.type.french_desc', locale=self.locale),
                    emoji="🇫🇷",
                    default=self.working_config.get('interserver_type', 'english') == 'french'
                )
            ],
            min_values=1,
            max_values=1
        )
        type_select.callback = self.on_type_select
        type_row.add_item(type_select)
        container.add_item(type_row)

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Avertissement de sécurité
        container.add_item(ui.TextDisplay(
            f"{WARNING} **{t('modules.interserver.config.warning.title', locale=self.locale)}**\n"
            f"-# {t('modules.interserver.config.warning.description', locale=self.locale)}"
        ))

        self.add_item(container)

        # Boutons d'action en bas
        self._add_action_buttons()

    def _add_action_buttons(self):
        """Ajoute les boutons d'action en bas de la vue"""
        button_row = ui.ActionRow()

        # Bouton Back (toujours présent, désactivé si modifications en cours)
        back_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(BACK),
            label=t('modules.config.buttons.back', locale=self.locale),
            style=discord.ButtonStyle.secondary,
            custom_id="back_btn",
            disabled=self.has_changes
        )
        back_btn.callback = self.on_back
        button_row.add_item(back_btn)

        # Bouton Save (visible uniquement si modifications)
        if self.has_changes:
            save_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(SAVE),
                label=t('modules.config.buttons.save', locale=self.locale),
                style=discord.ButtonStyle.success,
                custom_id="save_btn"
            )
            save_btn.callback = self.on_save
            button_row.add_item(save_btn)

            # Bouton Annuler
            cancel_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(UNDONE),
                label=t('modules.config.buttons.cancel', locale=self.locale),
                style=discord.ButtonStyle.danger,
                custom_id="cancel_btn"
            )
            cancel_btn.callback = self.on_cancel
            button_row.add_item(cancel_btn)
        else:
            # Bouton Supprimer la configuration (si config existe)
            if self.has_existing_config:
                delete_btn = ui.Button(
                    emoji=discord.PartialEmoji.from_str(DELETE),
                    label=t('modules.config.buttons.delete', locale=self.locale),
                    style=discord.ButtonStyle.danger,
                    custom_id="delete_btn"
                )
                delete_btn.callback = self.on_delete
                button_row.add_item(delete_btn)

        self.add_item(button_row)

    async def on_channel_select(self, interaction: discord.Interaction):
        """Callback quand un salon est sélectionné"""
        if not await self.check_user(interaction):
            return

        # Récupère le salon sélectionné (ou None si désélectionné)
        if interaction.data['values']:
            channel_id = int(interaction.data['values'][0])
            self.working_config['channel_id'] = channel_id
        else:
            self.working_config['channel_id'] = None

        # Marque comme modifié
        self.has_changes = True

        # Reconstruit la vue
        self._build_view()

        # Met à jour le message
        await interaction.response.edit_message(view=self)

    async def on_type_select(self, interaction: discord.Interaction):
        """Callback quand un type d'inter-serveur est sélectionné"""
        if not await self.check_user(interaction):
            return

        # Récupère le type sélectionné
        selected_type = interaction.data['values'][0]
        self.working_config['interserver_type'] = selected_type

        # Marque comme modifié
        self.has_changes = True

        # Reconstruit la vue
        self._build_view()

        # Met à jour le message
        await interaction.response.edit_message(view=self)

    async def on_back(self, interaction: discord.Interaction):
        """Retour au menu principal"""
        if not await self.check_user(interaction):
            return

        # Importe et affiche le menu principal
        from cogs.config import ConfigMainView
        main_view = ConfigMainView(self.bot, self.guild_id, self.user_id, self.locale)
        await interaction.response.edit_message(view=main_view)

    async def on_save(self, interaction: discord.Interaction):
        """Sauvegarde la configuration"""
        if not await self.check_user(interaction):
            return

        # Désactive temporairement les boutons
        await interaction.response.defer()

        # Récupère le gestionnaire de modules
        module_manager = self.bot.module_manager

        # Sauvegarde la configuration
        success, error_msg = await module_manager.save_module_config(
            self.guild_id,
            'interserver',
            self.working_config
        )

        if success:
            # Met à jour l'état
            self.current_config = self.working_config.copy()
            self.has_changes = False
            self.has_existing_config = True

            # Reconstruit la vue
            self._build_view()

            # Met à jour le message avec un feedback
            await interaction.followup.send(
                t('modules.config.save.success', locale=self.locale),
                ephemeral=True
            )
            await interaction.edit_original_response(view=self)
        else:
            # Affiche l'erreur
            await interaction.followup.send(
                t('modules.config.save.error', locale=self.locale, error=error_msg),
                ephemeral=True
            )

    async def on_cancel(self, interaction: discord.Interaction):
        """Annule les modifications"""
        if not await self.check_user(interaction):
            return

        # Restaure la configuration originale
        self.working_config = self.current_config.copy()
        self.has_changes = False

        # Reconstruit la vue
        self._build_view()

        # Met à jour le message
        await interaction.response.edit_message(view=self)

    async def on_delete(self, interaction: discord.Interaction):
        """Supprime la configuration"""
        if not await self.check_user(interaction):
            return

        # Désactive temporairement les boutons
        await interaction.response.defer()

        # Récupère le gestionnaire de modules
        module_manager = self.bot.module_manager

        # Supprime la configuration
        success = await module_manager.delete_module_config(self.guild_id, 'interserver')

        if success:
            # Met à jour l'état
            from modules.interserver import InterServerModule
            self.current_config = InterServerModule(self.bot, self.guild_id).get_default_config()
            self.working_config = self.current_config.copy()
            self.has_changes = False
            self.has_existing_config = False

            # Reconstruit la vue
            self._build_view()

            # Met à jour le message avec un feedback
            await interaction.followup.send(
                t('modules.config.delete.success', locale=self.locale),
                ephemeral=True
            )
            await interaction.edit_original_response(view=self)
        else:
            # Affiche l'erreur
            await interaction.followup.send(
                t('modules.config.delete.error', locale=self.locale),
                ephemeral=True
            )

    async def check_user(self, interaction: discord.Interaction) -> bool:
        """Vérifie que c'est le bon utilisateur"""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                t('modules.config.errors.wrong_user', locale=self.locale),
                ephemeral=True
            )
            return False
        return True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Vérifie les permissions pour chaque interaction"""
        return await self.check_user(interaction)
