"""
Commande preferences pour Moddy
Gère toutes les préférences utilisateur : langue, incognito, notifications DM
"""

import nextcord as discord
from nextcord import app_commands
from nextcord.ext import commands
from typing import Optional
from datetime import datetime
import asyncio

from utils.embeds import ModdyEmbed, ModdyResponse, ModdyColors
from config import COLORS


class PreferencesView(discord.ui.View):
    """Vue principale des préférences"""

    def __init__(self, bot, user: discord.User, user_data: dict, lang: str):
        super().__init__(timeout=300)  # 5 minutes
        self.bot = bot
        self.user = user
        self.user_data = user_data
        self.lang = lang
        self.current_page = "main"

        # Textes multilingues
        self.texts = {
            "FR": {
                # Titres
                "preferences_title": "<:settings:1398729549323440208> Préférences",
                "language_settings": "<:translate:1398720130950627600> Langue",
                "privacy_settings": "<:person_off:1401600620284219412> Confidentialité",
                "notification_settings": "<:info:1401614681440784477> Notifications",

                # Descriptions
                "main_description": "Gérez vos préférences personnelles sur Moddy",
                "current_language": "Langue actuelle",
                "current_incognito": "Mode incognito par défaut",
                "notifications_status": "Notifications DM",

                # Valeurs
                "enabled": "Activé",
                "disabled": "Désactivé",
                "french": "Français",
                "english": "Anglais",

                # Boutons
                "change": "Modifier",
                "back": "Retour",
                "save": "Enregistrer",
                "cancel": "Annuler",
                "language_button": "Langue",
                "privacy_button": "Confidentialité",
                "notification_button": "Notifications",

                # Messages
                "preferences_saved": "Vos préférences ont été enregistrées",
                "notification_types": "Types de notifications",
                "server_notifications": "Notifications serveurs",
                "moddy_notifications": "Messages de l'équipe Moddy",
                "server_notif_desc": "Sanctions, messages des serveurs",
                "moddy_notif_desc": "Annonces, mises à jour, informations importantes",

                # Titres des pages
                "language_title": "<:translate:1398720130950627600> Paramètres de langue",
                "privacy_title": "<:person_off:1401600620284219412> Paramètres de confidentialité",
                "notification_title": "<:notifications:1402261437493022775> Paramètres de notifications"
            },
            "EN": {
                # Titles
                "preferences_title": "<:settings:1398729549323440208> Preferences",
                "language_settings": "<:translate:1398720130950627600> Language",
                "privacy_settings": "<:person_off:1401600620284219412> Privacy",
                "notification_settings": "<:info:1401614681440784477> Notifications",

                # Descriptions
                "main_description": "Manage your personal preferences on Moddy",
                "current_language": "Current language",
                "current_incognito": "Default incognito mode",
                "notifications_status": "DM notifications",

                # Values
                "enabled": "Enabled",
                "disabled": "Disabled",
                "french": "French",
                "english": "English",

                # Buttons
                "change": "Change",
                "back": "Back",
                "save": "Save",
                "cancel": "Cancel",
                "language_button": "Language",
                "privacy_button": "Privacy",
                "notification_button": "Notifications",

                # Messages
                "preferences_saved": "Your preferences have been saved",
                "notification_types": "Notification types",
                "server_notifications": "Server notifications",
                "moddy_notifications": "Moddy team messages",
                "server_notif_desc": "Sanctions, server messages",
                "moddy_notif_desc": "Announcements, updates, important information",

                # Page titles
                "language_title": "<:translate:1398720130950627600> Language settings",
                "privacy_title": "<:person_off:1401600620284219412> Privacy settings",
                "notification_title": "<:notifications:1402261437493022775> Notification settings"
            }
        }

        self._update_buttons()

    def get_text(self, key: str) -> str:
        """Récupère un texte traduit"""
        return self.texts.get(self.lang, self.texts["EN"]).get(key, key)

    def _update_buttons(self):
        """Met à jour les boutons selon la page actuelle"""
        self.clear_items()

        if self.current_page == "main":
            self._add_main_buttons()
        elif self.current_page == "language":
            self._add_language_buttons()
        elif self.current_page == "privacy":
            self._add_privacy_buttons()
        elif self.current_page == "notifications":
            self._add_notification_buttons()

    def _add_main_buttons(self):
        """Ajoute les boutons du menu principal"""
        # Bouton Langue
        lang_button = discord.ui.Button(
            label=self.get_text("language_button"),
            style=discord.ButtonStyle.primary,
            row=0
        )
        lang_button.callback = self.show_language_settings
        self.add_item(lang_button)

        # Bouton Confidentialité
        privacy_button = discord.ui.Button(
            label=self.get_text("privacy_button"),
            style=discord.ButtonStyle.primary,
            row=0
        )
        privacy_button.callback = self.show_privacy_settings
        self.add_item(privacy_button)

        # Bouton Notifications
        notif_button = discord.ui.Button(
            label=self.get_text("notification_button"),
            style=discord.ButtonStyle.primary,
            row=1
        )
        notif_button.callback = self.show_notification_settings
        self.add_item(notif_button)

    def _add_language_buttons(self):
        """Ajoute les boutons de sélection de langue"""
        current_lang = self.user_data['attributes'].get('LANG', 'EN')

        # Bouton Français
        fr_button = discord.ui.Button(
            label="Français",
            style=discord.ButtonStyle.success if current_lang == "FR" else discord.ButtonStyle.secondary,
            disabled=current_lang == "FR",
            row=0
        )
        fr_button.callback = self.set_french
        self.add_item(fr_button)

        # Bouton English
        en_button = discord.ui.Button(
            label="English",
            style=discord.ButtonStyle.success if current_lang == "EN" else discord.ButtonStyle.secondary,
            disabled=current_lang == "EN",
            row=0
        )
        en_button.callback = self.set_english
        self.add_item(en_button)

        # Bouton Retour
        back_button = discord.ui.Button(
            label=self.get_text("back"),
            emoji="<:back:1401600847733067806>",
            style=discord.ButtonStyle.danger,
            row=1
        )
        back_button.callback = self.go_back
        self.add_item(back_button)

    def _add_privacy_buttons(self):
        """Ajoute les boutons de confidentialité"""
        default_incognito = self.user_data['attributes'].get('DEFAULT_INCOGNITO', True)

        # Bouton Activer Incognito
        enable_button = discord.ui.Button(
            label=f"{self.get_text('enabled')}",
            emoji="<:done:1398729525277229066>",
            style=discord.ButtonStyle.success if default_incognito else discord.ButtonStyle.secondary,
            disabled=default_incognito,
            row=0
        )
        enable_button.callback = self.enable_incognito
        self.add_item(enable_button)

        # Bouton Désactiver Incognito
        disable_button = discord.ui.Button(
            label=f"{self.get_text('disabled')}",
            emoji="<:undone:1398729502028333218>",
            style=discord.ButtonStyle.danger if not default_incognito else discord.ButtonStyle.secondary,
            disabled=not default_incognito,
            row=0
        )
        disable_button.callback = self.disable_incognito
        self.add_item(disable_button)

        # Bouton Retour
        back_button = discord.ui.Button(
            label=self.get_text("back"),
            emoji="<:back:1401600847733067806>",
            style=discord.ButtonStyle.danger,
            row=1
        )
        back_button.callback = self.go_back
        self.add_item(back_button)

    def _add_notification_buttons(self):
        """Ajoute les boutons de notifications"""
        # Récupère les préférences actuelles
        notif_data = self.user_data['data'].get('notifications', {
            'server_notifications': True,
            'moddy_notifications': True
        })

        # Bouton Notifications Serveurs
        server_notif = notif_data.get('server_notifications', True)
        server_button = discord.ui.Button(
            label=f"{self.get_text('server_notifications')}",
            emoji="<:done:1398729525277229066>" if server_notif else "<:undone:1398729502028333218>",
            style=discord.ButtonStyle.success if server_notif else discord.ButtonStyle.danger,
            row=0
        )
        server_button.callback = self.toggle_server_notifications
        self.add_item(server_button)

        # Bouton Notifications Moddy
        moddy_notif = notif_data.get('moddy_notifications', True)
        moddy_button = discord.ui.Button(
            label=f"{self.get_text('moddy_notifications')}",
            emoji="<:done:1398729525277229066>" if moddy_notif else "<:undone:1398729502028333218>",
            style=discord.ButtonStyle.success if moddy_notif else discord.ButtonStyle.danger,
            row=1
        )
        moddy_button.callback = self.toggle_moddy_notifications
        self.add_item(moddy_button)

        # Bouton Retour
        back_button = discord.ui.Button(
            label=self.get_text("back"),
            emoji="<:back:1401600847733067806>",
            style=discord.ButtonStyle.danger,
            row=2
        )
        back_button.callback = self.go_back
        self.add_item(back_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Vérifie que c'est bien l'utilisateur qui interagit"""
        if interaction.user.id != self.user.id:
            if self.lang == "FR":
                msg = "Ces préférences ne sont pas les vôtres."
            else:
                msg = "These preferences are not yours."
            await interaction.response.send_message(msg, ephemeral=True)
            return False
        return True

    # Callbacks pour la langue
    async def show_language_settings(self, interaction: discord.Interaction):
        """Affiche les paramètres de langue"""
        self.current_page = "language"
        self._update_buttons()

        embed = self.create_language_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def set_french(self, interaction: discord.Interaction):
        """Définit le français comme langue"""
        await self.bot.db.set_attribute(
            'user', self.user.id, 'LANG', 'FR',
            self.user.id, "Changement via préférences"
        )

        # Met à jour les données et la langue actuelle
        self.user_data['attributes']['LANG'] = 'FR'
        self.lang = 'FR'

        # Recrée les boutons et l'embed
        self._update_buttons()
        embed = self.create_language_embed()
        embed.color = COLORS["success"]

        await interaction.response.edit_message(embed=embed, view=self)

    async def set_english(self, interaction: discord.Interaction):
        """Définit l'anglais comme langue"""
        await self.bot.db.set_attribute(
            'user', self.user.id, 'LANG', 'EN',
            self.user.id, "Changed via preferences"
        )

        # Met à jour les données et la langue actuelle
        self.user_data['attributes']['LANG'] = 'EN'
        self.lang = 'EN'

        # Recrée les boutons et l'embed
        self._update_buttons()
        embed = self.create_language_embed()
        embed.color = COLORS["success"]

        await interaction.response.edit_message(embed=embed, view=self)

    # Callbacks pour la confidentialité
    async def show_privacy_settings(self, interaction: discord.Interaction):
        """Affiche les paramètres de confidentialité"""
        self.current_page = "privacy"
        self._update_buttons()

        embed = self.create_privacy_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def enable_incognito(self, interaction: discord.Interaction):
        """Active le mode incognito par défaut"""
        await self.bot.db.set_attribute(
            'user', self.user.id, 'DEFAULT_INCOGNITO', True,
            self.user.id, "Activation via préférences"
        )

        self.user_data['attributes']['DEFAULT_INCOGNITO'] = True
        self._update_buttons()

        embed = self.create_privacy_embed()
        embed.color = COLORS["success"]

        await interaction.response.edit_message(embed=embed, view=self)

    async def disable_incognito(self, interaction: discord.Interaction):
        """Désactive le mode incognito par défaut"""
        await self.bot.db.set_attribute(
            'user', self.user.id, 'DEFAULT_INCOGNITO', False,
            self.user.id, "Désactivation via préférences"
        )

        self.user_data['attributes']['DEFAULT_INCOGNITO'] = False
        self._update_buttons()

        embed = self.create_privacy_embed()
        embed.color = COLORS["success"]

        await interaction.response.edit_message(embed=embed, view=self)

    # Callbacks pour les notifications
    async def show_notification_settings(self, interaction: discord.Interaction):
        """Affiche les paramètres de notifications"""
        self.current_page = "notifications"
        self._update_buttons()

        embed = self.create_notifications_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def toggle_server_notifications(self, interaction: discord.Interaction):
        """Active/désactive les notifications serveurs"""
        # Récupère d'abord les données actuelles depuis la DB
        self.user_data = await self.bot.db.get_user(self.user.id)

        notif_data = self.user_data['data'].get('notifications', {
            'server_notifications': True,
            'moddy_notifications': True
        })
        current = notif_data.get('server_notifications', True)

        notif_data['server_notifications'] = not current
        await self.bot.db.update_user_data(self.user.id, 'notifications', notif_data)

        # Met à jour les données locales
        if 'notifications' not in self.user_data['data']:
            self.user_data['data']['notifications'] = {}
        self.user_data['data']['notifications'] = notif_data

        self._update_buttons()

        embed = self.create_notifications_embed()
        embed.color = COLORS["success"]

        await interaction.response.edit_message(embed=embed, view=self)

    async def toggle_moddy_notifications(self, interaction: discord.Interaction):
        """Active/désactive les notifications Moddy"""
        # Récupère d'abord les données actuelles depuis la DB
        self.user_data = await self.bot.db.get_user(self.user.id)

        notif_data = self.user_data['data'].get('notifications', {
            'server_notifications': True,
            'moddy_notifications': True
        })
        current = notif_data.get('moddy_notifications', True)

        notif_data['moddy_notifications'] = not current
        await self.bot.db.update_user_data(self.user.id, 'notifications', notif_data)

        # Met à jour les données locales
        if 'notifications' not in self.user_data['data']:
            self.user_data['data']['notifications'] = {}
        self.user_data['data']['notifications'] = notif_data

        self._update_buttons()

        embed = self.create_notifications_embed()
        embed.color = COLORS["success"]

        await interaction.response.edit_message(embed=embed, view=self)

    # Navigation
    async def go_back(self, interaction: discord.Interaction):
        """Retour au menu principal"""
        self.current_page = "main"
        self._update_buttons()

        embed = self.create_main_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    # Création des embeds
    def create_main_embed(self) -> discord.Embed:
        """Crée l'embed principal"""
        # Récupère les valeurs actuelles
        current_lang = self.user_data['attributes'].get('LANG', 'EN')
        lang_display = self.get_text("french") if current_lang == "FR" else self.get_text("english")

        default_incognito = self.user_data['attributes'].get('DEFAULT_INCOGNITO', True)
        incognito_display = self.get_text("enabled") if default_incognito else self.get_text("disabled")

        notif_data = self.user_data['data'].get('notifications', {})
        notif_count = sum([
            notif_data.get('server_notifications', True),
            notif_data.get('moddy_notifications', True)
        ])

        embed = discord.Embed(
            title=self.get_text("preferences_title"),
            description=self.get_text("main_description"),
            color=COLORS["primary"]
        )

        # Vue d'ensemble des préférences
        embed.add_field(
            name=f"<:translate:1398720130950627600> {self.get_text('current_language')}",
            value=f"`{lang_display}` ({current_lang})",
            inline=True
        )

        embed.add_field(
            name=f"<:eye_m:1402261502492151878> {self.get_text('current_incognito')}",
            value=f"`{incognito_display}`",
            inline=True
        )

        embed.add_field(
            name=f"<:notifications:1402261437493022775> {self.get_text('notifications_status')}",
            value=f"`{notif_count}/2` {self.get_text('enabled').lower()}",
            inline=True
        )

        embed.set_footer(
            text=f"{self.user}",
            icon_url=self.user.display_avatar.url
        )

        return embed

    def create_language_embed(self) -> discord.Embed:
        """Crée l'embed de langue"""
        current_lang = self.user_data['attributes'].get('LANG', 'EN')

        embed = discord.Embed(
            title=self.get_text('language_title'),
            color=COLORS["info"]
        )

        if self.lang == "FR":
            description = (
                f"**Langue actuelle :** `{self.get_text('french') if current_lang == 'FR' else self.get_text('english')}`\n\n"
                "Choisissez votre langue préférée. Toutes les interactions avec Moddy seront dans cette langue."
            )
        else:
            description = (
                f"**Current language:** `{self.get_text('english') if current_lang == 'EN' else self.get_text('french')}`\n\n"
                "Choose your preferred language. All interactions with Moddy will be in this language."
            )

        embed.description = description

        return embed

    def create_privacy_embed(self) -> discord.Embed:
        """Crée l'embed de confidentialité"""
        default_incognito = self.user_data['attributes'].get('DEFAULT_INCOGNITO', True)

        embed = discord.Embed(
            title=self.get_text('privacy_title'),
            color=COLORS["info"]
        )

        if self.lang == "FR":
            description = (
                f"**Mode incognito par défaut :** `{self.get_text('enabled') if default_incognito else self.get_text('disabled')}`\n\n"
                "Lorsque le mode incognito est activé, les réponses du bot sont visibles uniquement par vous.\n\n"
                "Vous pouvez toujours changer ce paramètre pour chaque commande individuellement."
            )
        else:
            description = (
                f"**Default incognito mode:** `{self.get_text('enabled') if default_incognito else self.get_text('disabled')}`\n\n"
                "When incognito mode is enabled, bot responses are only visible to you.\n\n"
                "You can always change this setting for each command individually."
            )

        embed.description = description

        return embed

    def create_notifications_embed(self) -> discord.Embed:
        """Crée l'embed de notifications"""
        notif_data = self.user_data['data'].get('notifications', {
            'server_notifications': True,
            'moddy_notifications': True
        })

        embed = discord.Embed(
            title=self.get_text('notification_title'),
            color=COLORS["info"]
        )

        if self.lang == "FR":
            description = "Gérez les notifications que vous recevez en messages privés.\n"
        else:
            description = "Manage the notifications you receive in direct messages.\n"

        embed.description = description

        # Notifications serveurs
        server_status = "<:done:1398729525277229066>" if notif_data.get('server_notifications', True) else "<:undone:1398729502028333218>"
        embed.add_field(
            name=f"{server_status} {self.get_text('server_notifications')}",
            value=self.get_text('server_notif_desc'),
            inline=False
        )

        # Notifications Moddy
        moddy_status = "<:done:1398729525277229066>" if notif_data.get('moddy_notifications', True) else "<:undone:1398729502028333218>"
        embed.add_field(
            name=f"{moddy_status} {self.get_text('moddy_notifications')}",
            value=self.get_text('moddy_notif_desc'),
            inline=False
        )

        return embed


class Preferences(commands.Cog):
    """Système de préférences utilisateur"""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="preferences",
        description="Gérez vos préférences personnelles / Manage your personal preferences"
    )
    async def preferences(self, interaction: discord.Interaction):
        """Commande principale des préférences"""

        # Vérifie la DB
        if not self.bot.db:
            error_text = (
                "<:undone:1398729502028333218> Base de données non disponible / Database unavailable"
            )
            # Vérifie si déjà répondu
            if not interaction.response.is_done():
                await interaction.response.send_message(error_text, ephemeral=True)
            else:
                await interaction.followup.send(error_text, ephemeral=True)
            return

        # Attend un peu pour laisser le système de langue faire son travail
        await asyncio.sleep(0.1)

        # Vérifie si l'interaction a déjà été répondue (par le système de langue)
        if interaction.response.is_done():
            # Le système de langue a demandé la sélection, on attend qu'il finisse
            # et on affiche les préférences après
            await asyncio.sleep(2)

            # Récupère les données mises à jour
            try:
                user_data = await self.bot.db.get_user(interaction.user.id)
                lang = user_data['attributes'].get('LANG', 'EN')

                # Crée la vue et l'embed
                view = PreferencesView(self.bot, interaction.user, user_data, lang)
                embed = view.create_main_embed()

                # Envoie en followup
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                return
            except Exception as e:
                error_embed = ModdyResponse.error(
                    "Erreur / Error",
                    f"Impossible de récupérer vos données / Unable to retrieve your data\n`{str(e)}`"
                )
                await interaction.followup.send(embed=error_embed, ephemeral=True)
                return

        # Récupère les données utilisateur
        try:
            user_data = await self.bot.db.get_user(interaction.user.id)
            lang = user_data['attributes'].get('LANG', 'EN')
        except Exception as e:
            # IMPORTANT: Vérifie si l'interaction a déjà été répondue avant d'envoyer l'erreur
            if not interaction.response.is_done():
                error_embed = ModdyResponse.error(
                    "Erreur / Error",
                    f"Impossible de récupérer vos données / Unable to retrieve your data\n`{str(e)}`"
                )
                await interaction.response.send_message(embed=error_embed, ephemeral=True)
            else:
                # Si déjà répondu, utilise followup
                error_embed = ModdyResponse.error(
                    "Erreur / Error",
                    f"Impossible de récupérer vos données / Unable to retrieve your data\n`{str(e)}`"
                )
                await interaction.followup.send(embed=error_embed, ephemeral=True)
            return

        # Crée la vue et l'embed
        view = PreferencesView(self.bot, interaction.user, user_data, lang)
        embed = view.create_main_embed()

        # IMPORTANT: Vérifie si l'interaction a déjà été répondue
        if not interaction.response.is_done():
            # Envoie la réponse (toujours en ephemeral pour les préférences)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            # Si déjà répondu (par le système de langue), utilise followup
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @app_commands.command(
        name="prefs",
        description="Raccourci pour préférences / Shortcut for preferences"
    )
    async def prefs_shortcut(self, interaction: discord.Interaction):
        """Raccourci pour la commande preferences"""
        await self.preferences(interaction)


async def setup(bot):
    await bot.add_cog(Preferences(bot))