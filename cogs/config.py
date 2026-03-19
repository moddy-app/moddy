"""
Commande /config - Configuration des modules de serveur
Permet de configurer tous les modules disponibles avec une interface moderne V2
"""

import discord
from discord import app_commands, ui
from discord.ext import commands
from typing import Optional
import logging

from utils.i18n import t
from utils.emojis import EMOJIS
from cogs.error_handler import BaseView

logger = logging.getLogger('moddy.cogs.config')


class ConfigMainView(BaseView):
    """
    Vue principale de la commande /config
    Affiche la liste des modules disponibles et permet d'accéder à leur configuration
    """

    def __init__(self, bot, guild_id: int, user_id: int, locale: str):
        """
        Initialise la vue principale

        Args:
            bot: Instance du bot
            guild_id: ID du serveur
            user_id: ID de l'utilisateur qui configure
            locale: Langue de l'utilisateur
        """
        super().__init__(timeout=300)
        # Set bot for error handling
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale

        self._build_view()

    def _build_view(self):
        """Construit l'interface principale"""
        self.clear_items()

        container = ui.Container()

        # Titre et message de bienvenue
        container.add_item(ui.TextDisplay(
            f"### <:settings:1398729549323440208> {t('modules.config.main.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            f"\n{t('modules.config.main.description', locale=self.locale)}"
        ))

        # Menu déroulant pour sélectionner un module
        select_row = ui.ActionRow()

        # Récupère la liste des modules disponibles
        available_modules = self.bot.module_manager.get_available_modules()

        # Crée les options du menu
        options = []
        for module_info in available_modules:
            # Utilise i18n pour la description du module
            description_key = f"modules.{module_info['id']}.description"
            description = t(description_key, locale=self.locale)

            options.append(discord.SelectOption(
                label=module_info['name'],
                value=module_info['id'],
                description=description[:100],  # Limite à 100 caractères
                emoji=module_info['emoji']
            ))

        # Si aucun module disponible
        if not options:
            container.add_item(ui.TextDisplay(
                f"{EMOJIS['warning']} {t('modules.config.main.no_modules', locale=self.locale)}"
            ))
            self.add_item(container)
            return

        # Crée le menu déroulant
        module_select = ui.Select(
            placeholder=t('modules.config.main.select_placeholder', locale=self.locale),
            options=options,
            min_values=1,
            max_values=1
        )
        module_select.callback = self.on_module_select
        select_row.add_item(module_select)
        container.add_item(select_row)

        self.add_item(container)

    async def on_module_select(self, interaction: discord.Interaction):
        """Callback quand un module est sélectionné"""
        if not await self.check_user(interaction):
            return

        # Récupère le module sélectionné
        module_id = interaction.data['values'][0]

        # Désactive temporairement pour éviter les double-clics
        await interaction.response.defer()

        # Récupère la configuration actuelle du module
        module_config = await self.bot.module_manager.get_module_config(self.guild_id, module_id)

        # Import dynamique de la vue de configuration correspondante
        config_view = None

        if module_id == 'welcome_channel':
            from modules.configs.welcome_channel_config import WelcomeChannelConfigView
            config_view = WelcomeChannelConfigView(
                self.bot,
                self.guild_id,
                self.user_id,
                self.locale,
                module_config
            )
        elif module_id == 'welcome_dm':
            from modules.configs.welcome_dm_config import WelcomeDmConfigView
            config_view = WelcomeDmConfigView(
                self.bot,
                self.guild_id,
                self.user_id,
                self.locale,
                module_config
            )
        elif module_id == 'interserver':
            from modules.configs.interserver_config import InterServerConfigView
            config_view = InterServerConfigView(
                self.bot,
                self.guild_id,
                self.user_id,
                self.locale,
                module_config
            )
        elif module_id == 'starboard':
            from modules.configs.starboard_config import StarboardConfigView
            config_view = StarboardConfigView(
                self.bot,
                self.guild_id,
                self.user_id,
                self.locale,
                module_config
            )
        elif module_id == 'auto_restore_roles':
            from modules.configs.auto_restore_roles_config import AutoRestoreRolesConfigView
            config_view = AutoRestoreRolesConfigView(
                self.bot,
                self.guild_id,
                self.user_id,
                self.locale,
                module_config
            )
        elif module_id == 'auto_role':
            from modules.configs.auto_role_config import AutoRoleConfigView
            config_view = AutoRoleConfigView(
                self.bot,
                self.guild_id,
                self.user_id,
                self.locale,
                module_config
            )
        elif module_id == 'youtube_notifications':
            from modules.configs.youtube_notifications_config import YoutubeNotificationsConfigView
            config_view = YoutubeNotificationsConfigView(
                self.bot,
                self.guild_id,
                self.user_id,
                self.locale,
                module_config
            )
        # Ajouter d'autres modules ici au fur et à mesure
        # elif module_id == 'ticket':
        #     from modules.configs.ticket_config import TicketConfigView
        #     config_view = TicketConfigView(...)

        if config_view:
            # Affiche la vue de configuration du module
            await interaction.edit_original_response(view=config_view)
        else:
            # Module non implémenté
            await interaction.followup.send(
                t('modules.config.main.not_implemented', locale=self.locale, module_name=module_id),
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


class Config(commands.Cog):
    """
    Cog de configuration des modules de serveur
    """

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="config",
        description="Configure server modules"
    )
    @app_commands.guild_only()
    @app_commands.describe(
        incognito="Make response visible only to you"
    )
    async def config(self, interaction: discord.Interaction, incognito: Optional[bool] = None):
        """
        Commande /config
        Permet de configurer les différents modules du serveur
        """

        # Gestion du mode incognito
        if incognito is None and self.bot.db:
            try:
                user_pref = await self.bot.db.get_attribute('user', interaction.user.id, 'DEFAULT_INCOGNITO')
                ephemeral = True if user_pref is None else user_pref
            except:
                ephemeral = True
        else:
            ephemeral = incognito if incognito is not None else True

        # Vérifie que c'est bien dans un serveur
        if not interaction.guild:
            await interaction.response.send_message(
                t('modules.config.errors.guild_only', interaction),
                ephemeral=True
            )
            return

        # Vérifie que le bot est bien membre du serveur
        if not interaction.guild.me:
            await interaction.response.send_message(
                t('modules.config.errors.bot_not_in_guild', interaction),
                ephemeral=True
            )
            return

        # Vérifie que l'utilisateur a l'attribut TEAM ou BETA
        # Les modules de serveur sont en développement et réservés aux testeurs
        has_team = await self.bot.db.has_attribute('user', interaction.user.id, 'TEAM')
        has_beta = await self.bot.db.has_attribute('user', interaction.user.id, 'BETA')

        if not (has_team or has_beta):
            # Message d'erreur avec Components V2
            error_view = ui.LayoutView(timeout=None)
            error_container = ui.Container()

            error_container.add_item(ui.TextDisplay(
                f"### {EMOJIS['warning']} {t('modules.config.errors.dev_only.title', interaction)}"
            ))
            error_container.add_item(ui.TextDisplay(
                t('modules.config.errors.dev_only.description', interaction)
            ))

            error_container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

            # Bouton pour rejoindre le serveur support
            button_row = ui.ActionRow()
            support_btn = ui.Button(
                label=t('modules.config.errors.dev_only.button', interaction),
                style=discord.ButtonStyle.link,
                url="https://moddy.app/support"
            )
            button_row.add_item(support_btn)
            error_container.add_item(button_row)

            error_view.add_item(error_container)

            await interaction.response.send_message(
                view=error_view,
                ephemeral=True
            )
            return

        # Vérifie que Moddy a les permissions administrateur
        bot_member = interaction.guild.me
        if not bot_member.guild_permissions.administrator:
            # Crée un message d'erreur avec Components V2
            error_view = ui.LayoutView(timeout=None)
            error_container = ui.Container()

            error_container.add_item(ui.TextDisplay(
                f"### {EMOJIS['error']} {t('modules.config.errors.no_admin_perms.title', interaction)}"
            ))
            error_container.add_item(ui.TextDisplay(
                t('modules.config.errors.no_admin_perms.description', interaction)
            ))

            error_container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

            # Bouton pour inviter le bot avec les bonnes permissions
            button_row = ui.ActionRow()
            reinvite_btn = ui.Button(
                label=t('modules.config.errors.no_admin_perms.button', interaction),
                style=discord.ButtonStyle.link,
                url=f"https://discord.com/oauth2/authorize?client_id={self.bot.user.id}&scope=bot&permissions=8"
            )
            button_row.add_item(reinvite_btn)
            error_container.add_item(button_row)

            error_view.add_item(error_container)

            await interaction.response.send_message(
                view=error_view,
                ephemeral=True
            )
            return

        # Vérifie que l'utilisateur a les permissions de gérer le serveur
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                t('modules.config.errors.no_user_perms', interaction),
                ephemeral=True
            )
            return

        # Affiche le menu principal de configuration
        main_view = ConfigMainView(
            self.bot,
            interaction.guild.id,
            interaction.user.id,
            str(interaction.locale)
        )

        await interaction.response.send_message(
            view=main_view,
            ephemeral=ephemeral
        )


async def setup(bot):
    """Charge le cog"""
    await bot.add_cog(Config(bot))
