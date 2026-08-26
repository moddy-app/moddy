"""
/config — the server configuration panel.

One screen, one dropdown: every module Moddy offers, plus the server-wide
settings entry that used to sit on its own button. Below the container come the
two links a server owner reaches for when the panel is not enough — the support
server and the dashboard — as link buttons *outside* the container, the way
docs/DESIGN.md wants call-to-actions that leave Discord.

Access: anyone with **Manage Server**. The panel used to be gated behind the
TEAM/BETA attributes while the module system was being built; Moddy is in beta
now and the modules are live, so the gate is gone.
"""

import discord
from discord import app_commands, ui
from discord.ext import commands
from typing import Optional
import logging

from config import COLORS
from utils.i18n import i18n, t
from utils.emojis import EMOJIS, SETTINGS, SUPPORT, WEB, BOOK
from utils import global_sanctions
from utils.components_v2 import create_limited_message
from cogs.error_handler import BaseView
from modules.configs._common import check_guild_perms

logger = logging.getLogger('moddy.cogs.config')

_CID_MODULE_SELECT = "moddy:config:main:module_select"

#: Value of the dropdown entry that opens the server-wide settings screen.
#: Prefixed so it can never collide with a module id.
SETTINGS_OPTION = "__server_settings__"

#: Links shown under the panel. Outside the container, as link buttons.
SUPPORT_URL = "https://moddy.app/support"
DASHBOARD_URL = "https://dashboard.moddy.app"
DOCS_URL = "https://docs.moddy.app"


class ConfigMainView(BaseView):
    """
    Vue principale de la commande /config
    Affiche la liste des modules disponibles et permet d'accéder à leur configuration

    Persistent: yes. Auth: Manage Server in the guild (checked on every
    click via check_guild_perms — NOT via a stored user_id, which cannot
    survive a restarted shell).
    """

    __persistent__ = True

    def __init__(self, bot=None, guild_id: Optional[int] = None, user_id: Optional[int] = None,
                 locale: str = "en-US", limited: bool = False):
        """
        Initialise la vue principale

        Args:
            bot: Instance du bot
            guild_id: ID du serveur
            user_id: ID de l'utilisateur qui configure (informational only —
                not used for authorization, see check_guild_perms)
            locale: Langue de l'utilisateur
            limited: Le serveur (ou l'utilisateur) porte une sanction globale
                "limité" — un bandeau l'annonce, et le module manager refuse
                de configurer un module qui ne l'a jamais été
        """
        super().__init__()  # timeout=None
        # Set bot for error handling
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale
        self.limited = limited

        self._build_view()

    # ----------------------------------------------------------------- #
    # Rendering
    # ----------------------------------------------------------------- #

    def _build_view(self):
        """Construit l'interface principale."""
        self.clear_items()

        container = ui.Container(accent_colour=discord.Colour(COLORS["primary"]))

        container.add_item(ui.TextDisplay(
            f"### {SETTINGS} {t('modules.config.main.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t('modules.config.main.description', locale=self.locale)
        ))

        # Bandeau de sanction globale "limité".
        if self.limited:
            container.add_item(ui.TextDisplay(
                f"{EMOJIS['warning']} **{t('global_sanctions.limited.banner_title', locale=self.locale)}**\n"
                f"-# {t('global_sanctions.limited.no_new_modules', locale=self.locale)}"
            ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(
            f"-# {t('modules.config.main.hint', locale=self.locale)}"
        ))
        container.add_item(self._select_row())

        self.add_item(container)
        self.add_item(self._links_row())

    def _select_row(self) -> ui.ActionRow:
        """The one dropdown: the server settings, then every module.

        The settings entry lives *in* the menu rather than on a button next to
        it — from the reader's side "the language Moddy speaks here" is one
        more thing to configure, not a different kind of action.
        """
        row = ui.ActionRow()
        options = [self._settings_option()]

        if self.bot is None:
            # Registration shell: no live module_manager to list modules from.
            # Register the custom_id anyway so a real message's select still
            # dispatches after a restart — the callback re-derives everything
            # from the interaction.
            select = ui.Select(
                placeholder=t('modules.config.main.select_placeholder', locale=self.locale),
                options=options, min_values=1, max_values=1,
                custom_id=_CID_MODULE_SELECT,
            )
            select.callback = self.on_module_select
            row.add_item(select)
            return row

        for module_info in self.bot.module_manager.get_available_modules():
            description = t(f"modules.{module_info['id']}.description", locale=self.locale)
            options.append(discord.SelectOption(
                label=module_info['name'],
                value=module_info['id'],
                description=description[:100],
                emoji=module_info['emoji'],
            ))

        select = ui.Select(
            placeholder=t('modules.config.main.select_placeholder', locale=self.locale),
            # Discord caps a select at 25 options; the settings entry is the
            # one that must never be pushed out, hence its slot at the top.
            options=options[:25],
            min_values=1,
            max_values=1,
            custom_id=_CID_MODULE_SELECT,
        )
        select.callback = self.on_module_select
        row.add_item(select)
        return row

    def _settings_option(self) -> discord.SelectOption:
        return discord.SelectOption(
            label=t('modules.config.settings.title', locale=self.locale),
            value=SETTINGS_OPTION,
            description=t('modules.config.settings.short_description',
                          locale=self.locale)[:100],
            emoji=discord.PartialEmoji.from_str(SETTINGS),
        )

    def _links_row(self) -> ui.ActionRow:
        """Support, dashboard and documentation — outside the container."""
        row = ui.ActionRow()
        row.add_item(ui.Button(
            label=t('modules.config.main.links.dashboard', locale=self.locale),
            emoji=discord.PartialEmoji.from_str(WEB),
            style=discord.ButtonStyle.link, url=DASHBOARD_URL,
        ))
        row.add_item(ui.Button(
            label=t('modules.config.main.links.support', locale=self.locale),
            emoji=discord.PartialEmoji.from_str(SUPPORT),
            style=discord.ButtonStyle.link, url=SUPPORT_URL,
        ))
        row.add_item(ui.Button(
            label=t('modules.config.main.links.docs', locale=self.locale),
            emoji=discord.PartialEmoji.from_str(BOOK),
            style=discord.ButtonStyle.link, url=DOCS_URL,
        ))
        return row

    # ----------------------------------------------------------------- #
    # Callbacks
    # ----------------------------------------------------------------- #

    async def on_module_select(self, interaction: discord.Interaction):
        """Callback quand un module (ou les paramètres du serveur) est choisi."""
        if not await check_guild_perms(interaction):
            return

        bot = interaction.client
        guild_id = interaction.guild_id
        user_id = interaction.user.id
        locale = i18n.get_user_locale(interaction)

        module_id = interaction.data['values'][0]

        if module_id == SETTINGS_OPTION:
            from modules.configs.server_settings_config import ServerSettingsConfigView

            view = await ServerSettingsConfigView.create(bot, guild_id, user_id, locale)
            await interaction.response.edit_message(view=view)
            return

        # Désactive temporairement pour éviter les double-clics
        await interaction.response.defer()

        # Récupère la configuration actuelle du module
        module_config = await bot.module_manager.get_module_config(guild_id, module_id)

        # Sanction globale "limité" : un module jamais configuré ne peut plus
        # être mis en place. On le dit tout de suite plutôt qu'à la sauvegarde.
        if not module_config and await global_sanctions.is_limited(
            bot, user_id=user_id, guild_id=guild_id
        ):
            guild_limited = await global_sanctions.is_limited(bot, guild_id=guild_id)
            await interaction.followup.send(
                view=create_limited_message(locale, guild=guild_limited),
                ephemeral=True,
            )
            return

        # Import dynamique de la vue de configuration correspondante
        config_view = None

        if module_id == 'welcome_channel':
            from modules.configs.welcome_channel_config import WelcomeChannelConfigView
            config_view = await WelcomeChannelConfigView.create(
                bot,
                guild_id,
                user_id,
                locale
            )
        elif module_id == 'welcome_dm':
            from modules.configs.welcome_dm_config import WelcomeDmConfigView
            config_view = await WelcomeDmConfigView.create(
                bot,
                guild_id,
                user_id,
                locale
            )
        elif module_id == 'interserver':
            from modules.configs.interserver_config import InterServerConfigView
            config_view = InterServerConfigView(
                bot,
                guild_id,
                user_id,
                locale,
                module_config
            )
        elif module_id == 'starboard':
            from modules.configs.starboard_config import StarboardConfigView
            config_view = StarboardConfigView(
                bot,
                guild_id,
                user_id,
                locale,
                module_config
            )
        elif module_id == 'auto_restore_roles':
            from modules.configs.auto_restore_roles_config import AutoRestoreRolesConfigView
            config_view = AutoRestoreRolesConfigView(
                bot,
                guild_id,
                user_id,
                locale,
                module_config
            )
        elif module_id == 'auto_role':
            from modules.configs.auto_role_config import AutoRoleConfigView
            config_view = AutoRoleConfigView(
                bot,
                guild_id,
                user_id,
                locale,
                module_config
            )
        elif module_id == 'social_notifications':
            from modules.configs.social_notifications_config import SocialNotificationsConfigView
            config_view = await SocialNotificationsConfigView.create(
                bot,
                guild_id,
                user_id,
                locale
            )
        elif module_id == 'adaptive_slowmode':
            from modules.configs.adaptive_slowmode_config import AdaptiveSlowmodeConfigView
            config_view = AdaptiveSlowmodeConfigView(
                bot,
                guild_id,
                user_id,
                locale,
                module_config
            )
        elif module_id == 'voice_transcription':
            from modules.configs.voice_transcription_config import VoiceTranscriptionConfigView
            config_view = VoiceTranscriptionConfigView(
                bot,
                guild_id,
                user_id,
                locale,
                module_config
            )
        elif module_id == 'bot_customization':
            from modules.configs.bot_customization_config import BotCustomizationConfigView
            config_view = await BotCustomizationConfigView.create(
                bot,
                guild_id,
                user_id,
                locale
            )
        elif module_id == 'altguard':
            from modules.configs.altguard_config import AltGuardConfigView
            config_view = AltGuardConfigView(
                bot,
                guild_id,
                user_id,
                locale,
                module_config
            )
        elif module_id == 'logs':
            from modules.configs.logs_config import LogsConfigView
            config_view = await LogsConfigView.create(
                bot,
                guild_id,
                user_id,
                locale
            )
        elif module_id == 'tickets':
            from modules.configs.tickets_config import TicketsConfigView
            config_view = await TicketsConfigView.create(
                bot,
                guild_id,
                user_id,
                locale
            )
        elif module_id == 'automod_ai':
            from modules.configs.automod_ai_config import AutomodAIConfigView
            config_view = AutomodAIConfigView(
                bot,
                guild_id,
                user_id,
                locale,
                module_config
            )
            # Session 7: load the learned-precedents count before first render.
            await config_view.load_precedent_stats()
        # Ajouter d'autres modules ici au fur et à mesure

        if config_view:
            # Affiche la vue de configuration du module
            await interaction.edit_original_response(view=config_view)
        else:
            # Module non implémenté
            await interaction.followup.send(
                t('modules.config.main.not_implemented', locale=locale, module_name=module_id),
                ephemeral=True
            )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Vérifie les permissions pour chaque interaction"""
        return await check_guild_perms(interaction)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_view(cls())


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

        # Vérifie que Moddy a les permissions administrateur
        bot_member = interaction.guild.me
        if not bot_member.guild_permissions.administrator:
            # Crée un message d'erreur avec Components V2
            error_view = ui.LayoutView(timeout=None)
            error_container = ui.Container(accent_colour=discord.Colour(COLORS["error"]))

            error_container.add_item(ui.TextDisplay(
                f"### {EMOJIS['error']} {t('modules.config.errors.no_admin_perms.title', interaction)}"
            ))
            error_container.add_item(ui.TextDisplay(
                t('modules.config.errors.no_admin_perms.description', interaction)
            ))

            error_view.add_item(error_container)

            # Bouton pour inviter le bot avec les bonnes permissions
            button_row = ui.ActionRow()
            reinvite_btn = ui.Button(
                label=t('modules.config.errors.no_admin_perms.button', interaction),
                style=discord.ButtonStyle.link,
                url=f"https://discord.com/oauth2/authorize?client_id={self.bot.user.id}&scope=bot&permissions=8"
            )
            button_row.add_item(reinvite_btn)
            error_view.add_item(button_row)

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

        # Sanction globale "limité" : les modules déjà configurés restent
        # modifiables, mais aucun NOUVEAU module ne peut être mis en place.
        limited = await global_sanctions.is_limited(
            self.bot, user_id=interaction.user.id, guild_id=interaction.guild.id
        )

        # Affiche le menu principal de configuration
        main_view = ConfigMainView(
            self.bot,
            interaction.guild.id,
            interaction.user.id,
            str(interaction.locale),
            limited=limited,
        )

        await interaction.response.send_message(
            view=main_view,
            ephemeral=ephemeral
        )


async def setup(bot):
    """Charge le cog"""
    await bot.add_cog(Config(bot))
