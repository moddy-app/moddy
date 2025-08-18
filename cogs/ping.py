"""
Commande ping publique
Simple et accessible à tous
"""

import nextcord as discord
from nextcord.ext import commands
import time
from datetime import datetime

from config import COLORS, EMOJIS


class PublicPing(commands.Cog):
    """Commande ping pour tous les utilisateurs"""

    def __init__(self, bot):
        self.bot = bot

    @discord.slash_command(name="ping", description="Vérifie la latence du bot")
    async def ping_slash(self, interaction: discord.Interaction):
        """Commande slash /ping simple pour tout le monde"""

        # Calcul des latences
        start = time.perf_counter()

        # Latence API
        api_latency = round(self.bot.latency * 1000)

        # Déterminer la qualité de la connexion
        if api_latency < 50:
            status = "Excellente"
            emoji = EMOJIS["done"]
        elif api_latency < 100:
            status = "Bonne"
            emoji = EMOJIS["done"]
        elif api_latency < 200:
            status = "Moyenne"
            emoji = EMOJIS["undone"]
        else:
            status = "Mauvaise"
            emoji = EMOJIS["undone"]

        # Créer l'embed avec du contenu
        embed = discord.Embed(
            title=f"{EMOJIS['ping']} Pong!",
            description=(
                f"{emoji} **Connexion {status}**\n\n"
                f"**Latence API Discord:** `{api_latency}ms`\n"
                f"**Temps de réponse:** `Calcul en cours...`"
            ),
            color=COLORS["primary"],
            timestamp=datetime.utcnow()
        )

        # Footer avec le nombre de serveurs
        embed.set_footer(
            text=f"Moddy • {len(self.bot.guilds)} serveurs",
            icon_url=self.bot.user.display_avatar.url if self.bot.user else None
        )

        # Envoyer le message
        await interaction.response.send_message(embed=embed)
        end = time.perf_counter()
        message_latency = round((end - start) * 1000)

        # Mettre à jour avec la latence du message
        embed.description = (
            f"{emoji} **Connexion {status}**\n\n"
            f"**Latence API Discord:** `{api_latency}ms`\n"
            f"**Temps de réponse:** `{message_latency}ms`"
        )

        # Modifier le message avec l'embed final
        await interaction.edit_original_response(embed=embed)


def setup(bot):
    bot.add_cog(PublicPing(bot))