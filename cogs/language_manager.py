"""
Système de gestion de langue pour Moddy
Intercepte les interactions et vérifie/définit la langue de l'utilisateur
"""

import nextcord as discord
from nextcord.ext import commands
from typing import Optional, Dict
import asyncio

from config import COLORS


class LanguageSelectView(discord.ui.View):
    """Vue pour sélectionner la langue préférée"""

    def __init__(self, user_id: int):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.selected_lang = None

    @discord.ui.button(label="Français", emoji="🇫🇷", style=discord.ButtonStyle.primary)
    async def french_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Sélectionne le français"""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Cette sélection n'est pas pour vous.",
                ephemeral=True
            )
            return

        self.selected_lang = "FR"
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="English", emoji="🇬🇧", style=discord.ButtonStyle.primary)
    async def english_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Sélectionne l'anglais"""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This selection is not for you.",
                ephemeral=True
            )
            return

        self.selected_lang = "EN"
        await interaction.response.defer()
        self.stop()


class LanguageManager(commands.Cog):
    """Gère la langue des utilisateurs pour toutes les interactions"""

    def __init__(self, bot):
        self.bot = bot
        self.lang_cache = {}  # Cache pour éviter trop de requêtes DB
        self.pending_interactions = {}  # Stocke les interactions en attente
        # Dictionnaire pour stocker les langues des interactions en cours
        self.interaction_languages = {}

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
                self.lang_cache[user_id] = lang
                return lang
            except:
                return None
        return None

    def get_interaction_language(self, interaction: discord.Interaction) -> Optional[str]:
        """Récupère la langue stockée pour une interaction"""
        return self.interaction_languages.get(interaction.id)

    def set_interaction_language(self, interaction: discord.Interaction, lang: str):
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
                return True
            except Exception as e:
                import logging
                logger = logging.getLogger('moddy')
                logger.error(f"Erreur définition langue: {e}")
                return False
        return False

    async def prompt_language_selection(self, interaction: discord.Interaction) -> Optional[str]:
        """Demande à l'utilisateur de choisir sa langue"""
        # Crée l'embed bilingue
        embed = discord.Embed(
            title="Language Selection / Sélection de la langue",
            description=(
                "🇫🇷 **Français**\n"
                "Quelle langue préférez-vous utiliser ?\n\n"
                "🇬🇧 **English**\n"
                "Which language do you prefer to use?"
            ),
            color=COLORS["info"]
        )

        # Crée la vue avec les boutons
        view = LanguageSelectView(interaction.user.id)

        # Envoie le message
        try:
            if interaction.response.is_done():
                msg = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
                msg = await interaction.original_response()

            # Attend la sélection
            await view.wait()

            if view.selected_lang:
                # Sauvegarde la langue
                success = await self.set_user_language(interaction.user.id, view.selected_lang)

                if success:
                    # Message de confirmation
                    confirm_embed = discord.Embed(
                        description=self.get_text(view.selected_lang, "language_set"),
                        color=COLORS["success"]
                    )

                    try:
                        await msg.edit(embed=confirm_embed, view=None)
                    except:
                        pass

                    return view.selected_lang
                else:
                    # Erreur lors de la sauvegarde
                    error_embed = discord.Embed(
                        description="Error saving language preference / Erreur lors de la sauvegarde",
                        color=COLORS["error"]
                    )
                    try:
                        await msg.edit(embed=error_embed, view=None)
                    except:
                        pass
                    return None
            else:
                # Timeout
                timeout_embed = discord.Embed(
                    description="Time expired / Temps écoulé",
                    color=COLORS["warning"]
                )
                try:
                    await msg.edit(embed=timeout_embed, view=None)
                except:
                    pass
                return None

        except Exception as e:
            import logging
            logger = logging.getLogger('moddy')
            logger.error(f"Erreur sélection langue: {e}")
            return None

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """Intercepte toutes les interactions pour vérifier la langue"""
        # Ignore les interactions du bot lui-même
        if interaction.user.bot:
            return

        # Ignore les interactions qui ne sont pas des commandes
        if interaction.type != discord.InteractionType.application_command:
            return

        # Ignore les commandes de développeur (elles restent en anglais)
        if hasattr(interaction.command, 'module') and 'staff' in str(interaction.command.module):
            return

        # Vérifie si l'utilisateur a une langue définie
        user_lang = await self.get_user_language(interaction.user.id)

        if not user_lang:
            # L'utilisateur n'a pas de langue définie, on lui demande
            # Stocke l'interaction originale
            self.pending_interactions[interaction.user.id] = interaction

            # Demande la langue
            selected_lang = await self.prompt_language_selection(interaction)

            if selected_lang:
                # La langue a été sélectionnée, on peut continuer
                # Stocke la langue dans notre dictionnaire
                self.set_interaction_language(interaction, selected_lang)

                # Log l'action
                if log_cog := self.bot.get_cog("LoggingSystem"):
                    await log_cog.log_command(
                        type('obj', (object,), {
                            'author': interaction.user,
                            'guild': interaction.guild,
                            'channel': interaction.channel
                        })(),
                        "language_set",
                        {"language": selected_lang, "first_interaction": True}
                    )
            else:
                # Erreur ou timeout, on arrête l'interaction
                if interaction.user.id in self.pending_interactions:
                    del self.pending_interactions[interaction.user.id]
                return

            # Nettoie
            if interaction.user.id in self.pending_interactions:
                del self.pending_interactions[interaction.user.id]
        else:
            # L'utilisateur a déjà une langue, on la stocke
            self.set_interaction_language(interaction, user_lang)

    @commands.command(name="changelang", aliases=["cl", "lang"])
    async def change_language(self, ctx, lang: str = None):
        """Change la langue de l'utilisateur (commande texte)"""
        if not lang:
            current_lang = await self.get_user_language(ctx.author.id)

            embed = discord.Embed(
                title="Language / Langue",
                description=(
                    f"**Current / Actuelle:** `{current_lang or 'Not set / Non définie'}`\n\n"
                    "**Available / Disponibles:**\n"
                    "`FR` - Français\n"
                    "`EN` - English\n\n"
                    "**Usage:** `!changelang FR` or `!changelang EN`"
                ),
                color=COLORS["info"]
            )
            await ctx.send(embed=embed)
            return

        # Valide la langue
        lang = lang.upper()
        if lang not in ["FR", "EN"]:
            await ctx.send("<:undone:1398729502028333218> Invalid language / Langue invalide. Use `FR` or `EN`.")
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
    async def on_application_command_completion(self, interaction: discord.Interaction, command):
        """Nettoie le cache périodiquement après les commandes"""
        # Nettoie le cache si trop grand
        if len(self.lang_cache) > 1000:
            # Garde seulement les 500 derniers
            import itertools
            self.lang_cache = dict(itertools.islice(self.lang_cache.items(), 500))

        # Nettoie aussi la langue de l'interaction
        self.interaction_languages.pop(interaction.id, None)


# Fonction helper pour récupérer la langue d'une interaction
def get_user_lang(interaction: discord.Interaction, bot) -> str:
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


def setup(bot):
    bot.add_cog(LanguageManager(bot))