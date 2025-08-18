"""
Système de gestion de langue pour Moddy
Version modifiée : envoie un MP au lieu d'afficher une popup
"""

import nextcord
from nextcord.ext import commands
from typing import Optional, Dict
import asyncio
import logging

from config import COLORS

logger = logging.getLogger('moddy')


class LanguageManager(commands.Cog):
    """Gère la langue des utilisateurs pour toutes les interactions"""

    def __init__(self, bot):
        self.bot = bot
        self.lang_cache = {}  # Cache pour éviter trop de requêtes DB
        self.interaction_languages = {}  # Stocke les langues des interactions en cours
        self.notified_users = set()  # Pour suivre les utilisateurs ayant déjà reçu le MP

        # Textes multilingues
        self.texts = {
            "FR": {
                "welcome": "Bienvenue sur Moddy !",
                "language_prompt": "Quelle langue préférez-vous utiliser ?",
                "language_set": "Votre langue a été définie sur **Français** <:done:1398729525277229066>",
                "processing": "Traitement de votre demande...",
                "error": "Une erreur est survenue",
                "timeout": "Temps écoulé. Veuillez réessayer."
            },
            "EN": {
                "welcome": "Welcome to Moddy!",
                "language_prompt": "Which language do you prefer to use?",
                "language_set": "Your language has been set to **English** <:done:1398729525277229066>",
                "processing": "Processing your request...",
                "error": "An error occurred",
                "timeout": "Time expired. Please try again."
            }
        }

    def get_text(self, lang: str, key: str) -> str:
        """Récupère un texte dans la langue appropriée"""
        return self.texts.get(lang, self.texts["EN"]).get(key, key)

    async def get_user_language(self, user_id: int) -> Optional[str]:
        """Récupère la langue d'un utilisateur (avec cache)"""
        # Vérifie le cache d'abord
        if user_id in self.lang_cache:
            return self.lang_cache[user_id]

        # Sinon vérifie la DB
        if self.bot.db:
            try:
                lang = await self.bot.db.get_attribute('user', user_id, 'LANG')
                if lang:
                    self.lang_cache[user_id] = lang
                return lang
            except Exception as e:
                logger.error(f"Erreur récupération langue: {e}")
                return None
        return None

    def get_interaction_language(self, interaction: nextcord.Interaction) -> Optional[str]:
        """Récupère la langue stockée pour une interaction"""
        return self.interaction_languages.get(interaction.id)

    def set_interaction_language(self, interaction: nextcord.Interaction, lang: str):
        """Stocke la langue pour une interaction"""
        self.interaction_languages[interaction.id] = lang
        # Nettoie les vieilles entrées après 5 minutes
        asyncio.create_task(self._cleanup_interaction_language(interaction.id))

    async def _cleanup_interaction_language(self, interaction_id: str):
        """Nettoie la langue d'une interaction après 5 minutes"""
        await asyncio.sleep(300)  # 5 minutes
        self.interaction_languages.pop(interaction_id, None)

    async def set_user_language(self, user_id: int, lang: str, set_by: int = None):
        """Définit la langue d'un utilisateur"""
        if self.bot.db:
            try:
                await self.bot.db.set_attribute(
                    'user', user_id, 'LANG', lang,
                    set_by or user_id, "Langue sélectionnée par l'utilisateur"
                )
                # Met à jour le cache
                self.lang_cache[user_id] = lang
                logger.info(f"Langue définie pour {user_id}: {lang}")
                return True
            except Exception as e:
                logger.error(f"Erreur définition langue: {e}")
                return False
        return False

    async def send_language_info_dm(self, user: nextcord.User):
        """Envoie un MP à l'utilisateur pour l'informer sur le changement de langue"""
        # Vérifie si on a déjà envoyé un MP à cet utilisateur
        if user.id in self.notified_users:
            return True

        try:
            embed = nextcord.Embed(
                title="Language / Langue",
                color=COLORS["primary"]
            )

            # Message bilingue
            embed.add_field(
                name="English",
                value=(
                    "Welcome to Moddy! Your language has been set to **English** by default.\n\n"
                    "If you want to change your language to French, use the command:\n"
                    "`/preferences`"
                ),
                inline=False
            )

            embed.add_field(
                name="Français",
                value=(
                    "Bienvenue sur Moddy ! Votre langue a été définie sur **Anglais** par défaut.\n\n"
                    "Si vous souhaitez changer votre langue en français, utilisez la commande :\n"
                    "`/preferences`"
                ),
                inline=False
            )

            embed.set_footer(text="Moddy Bot")

            await user.send(embed=embed)
            self.notified_users.add(user.id)
            logger.info(f"MP langue envoyé à {user.name} ({user.id})")
            return True

        except nextcord.Forbidden:
            logger.warning(f"Impossible d'envoyer un MP à {user.name} ({user.id}) - MPs désactivés")
            self.notified_users.add(user.id)  # On marque quand même comme notifié
            return False
        except Exception as e:
            logger.error(f"Erreur envoi MP langue: {e}")
            return False

    @commands.Cog.listener()
    async def on_interaction(self, interaction: nextcord.Interaction):
        """Intercepte toutes les interactions pour vérifier la langue"""
        # Ignore les interactions du bot lui-même
        if interaction.user.bot:
            return

        # Ignore les interactions qui ne sont pas des commandes
        if interaction.type != nextcord.InteractionType.application_command:
            return

        # Ignore les commandes de développeur (elles restent en anglais)
        if hasattr(interaction.command, 'module') and 'staff' in str(interaction.command.module):
            return

        # Vérifie si l'utilisateur a une langue définie
        user_lang = await self.get_user_language(interaction.user.id)

        if not user_lang:
            # L'utilisateur n'a pas de langue définie
            # Définit l'anglais par défaut IMMÉDIATEMENT
            logger.info(f"Nouvel utilisateur détecté: {interaction.user.name} ({interaction.user.id})")

            # Définit la langue par défaut
            await self.set_user_language(interaction.user.id, "EN", interaction.user.id)
            self.set_interaction_language(interaction, "EN")

            # Envoie le MP de manière asynchrone (sans bloquer)
            asyncio.create_task(self.send_language_info_dm(interaction.user))

            # Log l'action
            if log_cog := self.bot.get_cog("LoggingSystem"):
                try:
                    await log_cog.log_command(
                        type('obj', (object,), {
                            'command': interaction.command,
                            'guild': interaction.guild
                        })(),
                        interaction.user,
                        {
                            'action': 'language_auto_set',
                            'lang': 'EN',
                            'method': 'default_with_dm'
                        }
                    )
                except:
                    pass

        else:
            # L'utilisateur a déjà une langue, on la stocke pour cette interaction
            self.set_interaction_language(interaction, user_lang)

    @commands.command(name="changelang", aliases=["lang", "language"])
    async def change_language_command(self, ctx, lang: str = None):
        """Change la langue de l'utilisateur (commande texte)"""
        if not lang:
            current = await self.get_user_language(ctx.author.id)
            if current == "FR":
                await ctx.send("Votre langue actuelle : **Français**\nUtilisez `/changelang EN` pour changer.")
            else:
                await ctx.send("Your current language: **English**\nUse `/changelang FR` to change.")
            return

        lang = lang.upper()
        if lang not in ["FR", "EN"]:
            await ctx.send("Invalid language / Langue invalide.\nUse `FR` or `EN`.")
            return

        # Change la langue
        success = await self.set_user_language(ctx.author.id, lang, ctx.author.id)

        if success:
            if lang == "FR":
                await ctx.send("<:done:1398729525277229066> Votre langue a été changée en **Français**")
            else:
                await ctx.send("<:done:1398729525277229066> Your language has been changed to **English**")
        else:
            await ctx.send("<:undone:1398729502028333218> Error changing language / Erreur lors du changement")

    @commands.Cog.listener()
    async def on_app_command_completion(self, interaction: nextcord.Interaction, command):
        """Nettoie le cache périodiquement après les commandes"""
        # Nettoie le cache si trop grand
        if len(self.lang_cache) > 1000:
            # Garde seulement les 500 derniers
            import itertools
            self.lang_cache = dict(itertools.islice(self.lang_cache.items(), 500))

        # Nettoie aussi la langue de l'interaction
        self.interaction_languages.pop(interaction.id, None)

    @commands.Cog.listener()
    async def on_ready(self):
        """Quand le bot est prêt"""
        logger.info("LanguageManager prêt - Mode: MP automatique")


# Fonction helper pour récupérer la langue d'une interaction
def get_user_lang(interaction: nextcord.Interaction, bot) -> str:
    """Récupère la langue de l'utilisateur pour une interaction"""
    # Essaye de récupérer depuis le manager
    if lang_manager := bot.get_cog("LanguageManager"):
        lang = lang_manager.get_interaction_language(interaction)
        if lang:
            return lang

        # Si pas trouvé dans l'interaction, cherche dans le cache
        if interaction.user.id in lang_manager.lang_cache:
            return lang_manager.lang_cache[interaction.user.id]

    # Par défaut, retourne EN
    return "EN"


async def setup(bot):
    await bot.add_cog(LanguageManager(bot))