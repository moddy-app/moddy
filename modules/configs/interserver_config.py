"""
Configuration UI pour le module Inter-Server
Utilise les Composants V2 pour une interface moderne
"""

import discord
from discord import ui
from typing import Optional, Dict, Any
import logging

from utils.i18n import i18n, t
from cogs.error_handler import BaseView
from utils.emojis import GROUPS, REQUIRED_FIELDS, WARNING, BACK, SAVE, UNDONE, DELETE
from modules.configs._common import check_guild_perms

logger = logging.getLogger('moddy.modules.interserver_config')

_CID_BACK = "moddy:interserver:config:back"
_CID_SAVE = "moddy:interserver:config:save"
_CID_CANCEL = "moddy:interserver:config:cancel"
_CID_DELETE = "moddy:interserver:config:delete"
_CID_CHANNEL_SELECT = "moddy:interserver:config:channel_select"
_CID_TYPE_SELECT = "moddy:interserver:config:type_select"


class InterServerConfigView(BaseView):
    """
    Interface de configuration du module Inter-Server
    Permet de configurer le salon de communication inter-serveurs

    Persistent: yes. Auth: Manage Server in the guild (checked on every
    click via check_guild_perms — NOT via a stored user_id, which cannot
    survive a restarted shell).
    """

    __persistent__ = True

    def __init__(self, bot=None, guild_id: Optional[int] = None, user_id: Optional[int] = None,
                 locale: str = "en-US", current_config: Optional[Dict[str, Any]] = None):
        """
        Initialise la vue de configuration

        Args:
            bot: Instance du bot
            guild_id: ID du serveur
            user_id: ID de l'utilisateur qui configure (informational only —
                not used for authorization, see check_guild_perms)
            locale: Langue de l'utilisateur
            current_config: Configuration actuelle du module (None si première config)
        """
        super().__init__()  # timeout=None
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
            max_values=1,
            custom_id=_CID_CHANNEL_SELECT,
        )

        # Pré-sélectionne le salon actuel si configuré
        if self.working_config.get('channel_id') and self.bot is not None:
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
            max_values=1,
            custom_id=_CID_TYPE_SELECT,
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
            custom_id=_CID_BACK,
            disabled=self.has_changes
        )
        back_btn.callback = self.on_back
        button_row.add_item(back_btn)

        # Registration shell (self.bot is None): register EVERY button's
        # custom_id regardless of has_changes/has_existing_config, or a
        # restored click on a live message stuck in a state this bare shell
        # doesn't happen to be in would dispatch nowhere (discord.py only
        # knows the custom_ids actually present on the registered instance).
        is_shell = self.bot is None

        # Bouton Save (visible uniquement si modifications)
        if self.has_changes or is_shell:
            save_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(SAVE),
                label=t('modules.config.buttons.save', locale=self.locale),
                style=discord.ButtonStyle.success,
                custom_id=_CID_SAVE
            )
            save_btn.callback = self.on_save
            button_row.add_item(save_btn)

            # Bouton Annuler
            cancel_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(UNDONE),
                label=t('modules.config.buttons.cancel', locale=self.locale),
                style=discord.ButtonStyle.danger,
                custom_id=_CID_CANCEL
            )
            cancel_btn.callback = self.on_cancel
            button_row.add_item(cancel_btn)
        if (not self.has_changes and self.has_existing_config) or is_shell:
            # Bouton Supprimer la configuration (si config existe)
            delete_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(DELETE),
                label=t('modules.config.buttons.delete', locale=self.locale),
                style=discord.ButtonStyle.danger,
                custom_id=_CID_DELETE
            )
            delete_btn.callback = self.on_delete
            button_row.add_item(delete_btn)

        self.add_item(button_row)

    def _is_live_for(self, interaction: discord.Interaction) -> bool:
        """True if self was actually built for this guild (a live view, not
        the shared registered shell falling back after a restart — or a
        leftover instance from a different guild). Only then is it safe to
        trust self.working_config/self.current_config."""
        return self.bot is not None and self.guild_id == interaction.guild_id

    async def _fresh_working_config(self, interaction: discord.Interaction) -> Dict[str, Any]:
        """The working copy to mutate: self's if this is a live view for this
        guild, otherwise reloaded from the DB (Appendix B.1.a: unsaved edits
        on a restarted shell are lost, not silently mixed with another
        guild's session)."""
        if self._is_live_for(interaction):
            return self.working_config.copy()
        bot = interaction.client
        saved = await bot.module_manager.get_module_config(interaction.guild_id, 'interserver')
        from modules.interserver import InterServerModule
        if saved and saved.get('channel_id') is not None:
            return saved.copy()
        return InterServerModule(bot, interaction.guild_id).get_default_config()

    async def _rebuild(self, interaction: discord.Interaction, working_config: Dict[str, Any],
                        has_changes: bool) -> "InterServerConfigView":
        """Always construct a NEW view instance rather than mutate/resend
        self — self may be the single shared shell serving every guild after
        a restart, and reusing it in place would leak one guild's edits into
        another's session."""
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        saved = await bot.module_manager.get_module_config(interaction.guild_id, 'interserver')
        view = InterServerConfigView(bot, interaction.guild_id, interaction.user.id, locale, current_config=saved)
        view.working_config = working_config
        view.has_changes = has_changes
        view._build_view()
        return view

    async def on_channel_select(self, interaction: discord.Interaction):
        """Callback quand un salon est sélectionné"""
        if not await check_guild_perms(interaction):
            return

        working_config = await self._fresh_working_config(interaction)
        if interaction.data['values']:
            working_config['channel_id'] = int(interaction.data['values'][0])
        else:
            working_config['channel_id'] = None

        view = await self._rebuild(interaction, working_config, has_changes=True)
        await interaction.response.edit_message(view=view)

    async def on_type_select(self, interaction: discord.Interaction):
        """Callback quand un type d'inter-serveur est sélectionné"""
        if not await check_guild_perms(interaction):
            return

        working_config = await self._fresh_working_config(interaction)
        working_config['interserver_type'] = interaction.data['values'][0]

        view = await self._rebuild(interaction, working_config, has_changes=True)
        await interaction.response.edit_message(view=view)

    async def on_back(self, interaction: discord.Interaction):
        """Retour au menu principal"""
        if not await check_guild_perms(interaction):
            return

        from cogs.config import ConfigMainView
        locale = i18n.get_user_locale(interaction)
        main_view = ConfigMainView(interaction.client, interaction.guild_id, interaction.user.id, locale)
        await interaction.response.edit_message(view=main_view)

    async def on_save(self, interaction: discord.Interaction):
        """Sauvegarde la configuration"""
        if not await check_guild_perms(interaction):
            return

        await interaction.response.defer()

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        working_config = await self._fresh_working_config(interaction)

        success, error_msg = await bot.module_manager.save_module_config(
            interaction.guild_id, 'interserver', working_config,
        )

        if success:
            view = InterServerConfigView(bot, interaction.guild_id, interaction.user.id, locale,
                                          current_config=working_config)
            await interaction.followup.send(t('modules.config.save.success', locale=locale), ephemeral=True)
            await interaction.edit_original_response(view=view)
        else:
            await interaction.followup.send(
                t('modules.config.save.error', locale=locale, error=error_msg), ephemeral=True,
            )

    async def on_cancel(self, interaction: discord.Interaction):
        """Annule les modifications"""
        if not await check_guild_perms(interaction):
            return

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        saved = await bot.module_manager.get_module_config(interaction.guild_id, 'interserver')
        view = InterServerConfigView(bot, interaction.guild_id, interaction.user.id, locale, current_config=saved)
        await interaction.response.edit_message(view=view)

    async def on_delete(self, interaction: discord.Interaction):
        """Supprime la configuration"""
        if not await check_guild_perms(interaction):
            return

        await interaction.response.defer()

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        success = await bot.module_manager.delete_module_config(interaction.guild_id, 'interserver')

        if success:
            view = InterServerConfigView(bot, interaction.guild_id, interaction.user.id, locale, current_config=None)
            await interaction.followup.send(t('modules.config.delete.success', locale=locale), ephemeral=True)
            await interaction.edit_original_response(view=view)
        else:
            await interaction.followup.send(t('modules.config.delete.error', locale=locale), ephemeral=True)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Vérifie les permissions pour chaque interaction"""
        return await check_guild_perms(interaction)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_view(cls())
