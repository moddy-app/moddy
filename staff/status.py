"""
Commande pour changer le statut du bot
Réservée aux développeurs
"""

import nextcord as discord
from nextcord.ext import commands
from typing import Optional

# Import du système d'embeds épuré
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from utils.embeds import ModdyEmbed, ModdyResponse
from config import COLORS


class StatusCommand(commands.Cog):
    """Gestion du statut du bot"""

    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        """Vérifie que l'utilisateur est développeur"""
        return self.bot.is_developer(ctx.author.id)

    @commands.group(name="status", aliases=["presence"], invoke_without_command=True)
    async def status_group(self, ctx):
        """Commande principale pour gérer le statut"""
        if ctx.invoked_subcommand is None:
            embed = discord.Embed(
                title="Gestion du statut",
                description=(
                    "**Commandes disponibles:**\n\n"
                    "`status set <type> <texte>` - Change le statut\n"
                    "`status clear` - Retire le statut\n"
                    "`status pause` - Met en pause les changements auto\n"
                    "`status resume` - Reprend les changements auto\n\n"
                    "**Types disponibles:**\n"
                    "`playing`, `watching`, `listening`, `streaming`, `competing`"
                ),
                color=COLORS["info"]
            )
            await ctx.send(embed=embed)

    @status_group.command(name="set")
    async def set_status(self, ctx, activity_type: str, *, text: str):
        """Change le statut du bot"""
        # Map des types d'activité
        activity_types = {
            "playing": discord.ActivityType.playing,
            "watching": discord.ActivityType.watching,
            "listening": discord.ActivityType.listening,
            "streaming": discord.ActivityType.streaming,
            "competing": discord.ActivityType.competing
        }

        if activity_type.lower() not in activity_types:
            embed = ModdyResponse.error(
                "Type invalide",
                f"Utilise l'un de ces types : {', '.join(activity_types.keys())}"
            )
            await ctx.send(embed=embed)
            return

        # Arrête la tâche de mise à jour auto si elle tourne
        if hasattr(self.bot, 'status_update') and self.bot.status_update.is_running():
            self.bot.status_update.stop()

        # Change le statut
        activity = discord.Activity(
            type=activity_types[activity_type.lower()],
            name=text
        )

        await self.bot.change_presence(activity=activity)

        embed = ModdyResponse.success(
            "Statut changé",
            f"**Type:** {activity_type}\n**Texte:** {text}"
        )
        await ctx.send(embed=embed)

        # Log l'action
        if log_cog := self.bot.get_cog("LoggingSystem"):
            await log_cog.log_command(ctx, "status")

    @status_group.command(name="clear", aliases=["remove", "none"])
    async def clear_status(self, ctx):
        """Retire le statut du bot"""
        await self.bot.change_presence(activity=None)

        embed = ModdyResponse.success(
            "Statut retiré",
            "Le bot n'a plus de statut personnalisé."
        )
        await ctx.send(embed=embed)

        # Log
        if log_cog := self.bot.get_cog("LoggingSystem"):
            await log_cog.log_command(ctx, "status")

    @status_group.command(name="pause", aliases=["stop"])
    async def pause_auto_status(self, ctx):
        """Met en pause les changements automatiques de statut"""
        if hasattr(self.bot, 'status_update') and self.bot.status_update.is_running():
            self.bot.status_update.stop()
            embed = ModdyResponse.success(
                "Pause activée",
                "Les changements automatiques de statut sont mis en pause."
            )
        else:
            embed = ModdyResponse.info(
                "Déjà en pause",
                "Les changements automatiques sont déjà en pause."
            )
        await ctx.send(embed=embed)

    @status_group.command(name="resume", aliases=["start"])
    async def resume_auto_status(self, ctx):
        """Reprend les changements automatiques de statut"""
        if hasattr(self.bot, 'status_update') and not self.bot.status_update.is_running():
            self.bot.status_update.start()
            embed = ModdyResponse.success(
                "Reprise activée",
                "Les changements automatiques de statut ont repris."
            )
        else:
            embed = ModdyResponse.info(
                "Déjà actifs",
                "Les changements automatiques sont déjà actifs."
            )
        await ctx.send(embed=embed)

    @status_group.command(name="streaming")
    async def set_streaming(self, ctx, url: str, *, game: str):
        """Définit un statut de streaming"""
        # Vérifie que l'URL est Twitch ou YouTube
        if not any(domain in url.lower() for domain in ["twitch.tv", "youtube.com", "youtu.be"]):
            embed = ModdyResponse.error(
                "URL invalide",
                "L'URL doit être un lien Twitch ou YouTube."
            )
            await ctx.send(embed=embed)
            return

        # Arrête les mises à jour auto
        if hasattr(self.bot, 'status_update') and self.bot.status_update.is_running():
            self.bot.status_update.stop()

        # Crée l'activité streaming
        activity = discord.Streaming(name=game, url=url)
        await self.bot.change_presence(activity=activity)

        embed = ModdyResponse.success(
            "Statut streaming défini",
            f"**Jeu:** {game}\n**URL:** {url}"
        )
        await ctx.send(embed=embed)

        # Log
        if log_cog := self.bot.get_cog("LoggingSystem"):
            await log_cog.log_command(ctx, "streaming")

    @status_group.command(name="custom")
    async def set_custom(self, ctx, emoji: Optional[str] = None, *, text: str):
        """Définit un statut personnalisé (simulé avec playing)"""
        # Note: Les bots ne peuvent pas vraiment avoir de statut personnalisé
        # mais on peut simuler avec une activité playing

        if emoji:
            text = f"{emoji} {text}"

        activity = discord.Activity(
            type=discord.ActivityType.playing,
            name=text
        )

        await self.bot.change_presence(activity=activity)

        embed = ModdyResponse.success(
            "Statut personnalisé",
            f"Statut défini : {text}"
        )
        await ctx.send(embed=embed)


def setup(bot):
    bot.add_cog(StatusCommand(bot))