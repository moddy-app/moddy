"""
Commande shutdown pour développeurs
Permet d'arrêter le bot proprement
"""

import nextcord as discord
from nextcord.ext import commands
import asyncio
import sys
from datetime import datetime, timezone

# Import du système d'embeds épuré
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from utils.embeds import ModdyEmbed, ModdyResponse
from config import COLORS


class Shutdown(commands.Cog):
    """Commande pour arrêter le bot"""

    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        """Vérifie que l'utilisateur est développeur"""
        return self.bot.is_developer(ctx.author.id)

    @commands.command(name="shutdown", aliases=["stop", "kill", "exit", "quit"])
    async def shutdown(self, ctx):
        """Arrête le bot proprement"""

        # Embed de confirmation
        embed = discord.Embed(
            title="Confirmation d'arrêt",
            description="Êtes-vous sûr de vouloir arrêter le bot ?",
            color=COLORS["warning"]
        )

        # Calcul de l'uptime
        uptime_str = self._get_uptime()

        embed.add_field(
            name="Informations",
            value=f"**Serveurs actifs:** `{len(self.bot.guilds)}`\n"
                  f"**Utilisateurs:** `{len(self.bot.users)}`\n"
                  f"**Uptime:** `{uptime_str}`",
            inline=False
        )

        embed.set_footer(text="Cette action fermera complètement le bot")

        # Vue avec boutons
        view = ShutdownView(self.bot, ctx.author)

        # Log l'action
        if log_cog := self.bot.get_cog("LoggingSystem"):
            await log_cog.log_command(ctx, "shutdown")

        await ctx.send(embed=embed, view=view)

    def _get_uptime(self):
        """Calcule l'uptime du bot"""
        if not hasattr(self.bot, 'launch_time'):
            return "N/A"

        uptime = datetime.now(timezone.utc) - self.bot.launch_time
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)

        if hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"


class ShutdownView(discord.ui.View):
    """Vue pour confirmer l'arrêt"""

    def __init__(self, bot, author):
        super().__init__(timeout=30)
        self.bot = bot
        self.author = author
        self.confirmed = False
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Seul l'auteur peut utiliser les boutons"""
        if interaction.user != self.author:
            await interaction.response.send_message(
                "Seul l'auteur de la commande peut confirmer l'arrêt.",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirmer l'arrêt", style=discord.ButtonStyle.danger)
    async def confirm_shutdown(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Confirme l'arrêt du bot"""
        self.confirmed = True

        # Désactiver tous les boutons
        for item in self.children:
            item.disabled = True

        # Embed d'arrêt
        embed = discord.Embed(
            title="Arrêt en cours...",
            description="Le bot s'arrête proprement.",
            color=COLORS["error"],
            timestamp=datetime.utcnow()
        )

        embed.add_field(
            name="Action effectuée par",
            value=f"{interaction.user.mention} (`{interaction.user.id}`)",
            inline=False
        )

        await interaction.response.edit_message(embed=embed, view=self)

        # Log l'action
        import logging
        logger = logging.getLogger('moddy')
        logger.info(f"Arrêt confirmé par {interaction.user} ({interaction.user.id})")

        # Attendre un peu pour que le message soit mis à jour
        await asyncio.sleep(1)

        # Arrêter le bot
        await self.bot.close()
        sys.exit(0)

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Annule l'arrêt"""
        # Désactiver tous les boutons
        for item in self.children:
            item.disabled = True

        embed = discord.Embed(
            title="Arrêt annulé",
            description="Le bot continue de fonctionner normalement.",
            color=COLORS["success"]
        )

        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    async def on_timeout(self):
        """Appelé après le timeout"""
        if not self.confirmed and self.message:
            # Désactiver tous les boutons
            for item in self.children:
                item.disabled = True

            try:
                embed = discord.Embed(
                    title="Temps écoulé",
                    description="La demande d'arrêt a expiré.",
                    color=COLORS["info"]
                )

                await self.message.edit(embed=embed, view=self)
            except:
                pass


async def setup(bot):
    await bot.add_cog(Shutdown(bot))