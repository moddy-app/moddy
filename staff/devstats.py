"""
Commande pour voir les statistiques d'utilisation des commandes dev
"""

import nextcord as discord
from nextcord.ext import commands
from datetime import datetime, timedelta
import sys
from pathlib import Path
from typing import Dict, List
from collections import Counter

sys.path.append(str(Path(__file__).parent.parent))
from utils.embeds import ModdyEmbed, ModdyResponse
from config import COLORS


class DevStats(commands.Cog):
    """Statistiques des commandes développeur"""

    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        """Vérifie que l'utilisateur est développeur"""
        return self.bot.is_developer(ctx.author.id)

    @commands.command(name="devstats", aliases=["ds", "devs"])
    async def dev_stats(self, ctx):
        """Affiche les statistiques des commandes dev"""

        # Récupère le logger
        dev_logger = self.bot.get_cog("DevCommandLogger")
        if not dev_logger:
            await ctx.send("❌ Système de logging non chargé")
            return

        # Embed principal
        embed = discord.Embed(
            title="📊 Statistiques des Commandes Dev",
            color=COLORS["primary"],
            timestamp=datetime.now()
        )

        # Top commandes utilisées
        if dev_logger.command_stats:
            sorted_commands = sorted(
                dev_logger.command_stats.items(),
                key=lambda x: x[1],
                reverse=True
            )[:10]

            commands_text = "\n".join([
                f"`{cmd}` : **{count}** utilisations"
                for cmd, count in sorted_commands
            ])

            embed.add_field(
                name="🏆 Top Commandes",
                value=commands_text or "Aucune commande utilisée",
                inline=False
            )

            # Total d'utilisations
            total_uses = sum(dev_logger.command_stats.values())
            embed.add_field(
                name="📈 Total",
                value=f"**{total_uses}** commandes exécutées",
                inline=True
            )

            # Commande la plus utilisée
            if sorted_commands:
                most_used = sorted_commands[0]
                embed.add_field(
                    name="👑 Plus utilisée",
                    value=f"`{most_used[0]}` ({most_used[1]} fois)",
                    inline=True
                )

        else:
            embed.description = "Aucune statistique disponible pour le moment."

        # Informations sur le système de logging
        embed.add_field(
            name="📝 Système de Logging",
            value=(
                f"**Canal :** <#{dev_logger.log_channel_id}>\n"
                f"**État :** ✅ Actif\n"
                f"**Module :** `{dev_logger.__class__.__name__}`"
            ),
            inline=False
        )

        await ctx.send(embed=embed)

    @commands.command(name="devlog", aliases=["dl"])
    async def manual_log(self, ctx, action: str, *, details: str = None):
        """Log manuel d'une action dev"""

        # Récupère le système de logging
        log_system = self.bot.get_cog("LoggingSystem")
        if not log_system:
            await ctx.send("❌ Système de logging non chargé")
            return

        # Prépare les détails
        log_details = {}
        if details:
            log_details["Détails"] = details

        # Log l'action
        await log_system.log_command(ctx, action, log_details)

        # Confirmation
        embed = ModdyResponse.success(
            "Action loggée",
            f"L'action `{action}` a été enregistrée dans les logs dev."
        )
        await ctx.send(embed=embed)

    @commands.command(name="devalert", aliases=["alert", "critical"])
    async def dev_alert(self, ctx, *, message: str):
        """Envoie une alerte critique dans les logs"""

        # Récupère le système de logging
        log_system = self.bot.get_cog("LoggingSystem")
        if not log_system:
            await ctx.send("❌ Système de logging non chargé")
            return

        # Envoie l'alerte
        await log_system.log_critical(
            title="Alerte Manuelle",
            description=f"{message}\n\n**Envoyée par :** {ctx.author.mention}",
            ping_dev=True
        )

        # Confirmation
        embed = discord.Embed(
            title="🚨 Alerte envoyée",
            description="L'alerte a été envoyée dans le canal de logs avec un ping.",
            color=COLORS["error"]
        )
        await ctx.send(embed=embed)

    @commands.command(name="clearstats", aliases=["cs", "resetstats"])
    async def clear_stats(self, ctx):
        """Réinitialise les statistiques des commandes"""

        # Récupère le logger
        dev_logger = self.bot.get_cog("DevCommandLogger")
        if not dev_logger:
            await ctx.send("❌ Système de logging non chargé")
            return

        # Sauvegarde pour confirmation
        old_count = sum(dev_logger.command_stats.values())

        # Réinitialise
        dev_logger.command_stats.clear()

        embed = ModdyResponse.success(
            "Statistiques réinitialisées",
            f"`{old_count}` entrées ont été supprimées."
        )
        await ctx.send(embed=embed)

        # Log l'action
        if log_system := self.bot.get_cog("LoggingSystem"):
            await log_system.log_command(
                ctx,
                "clear_stats",
                {"Entrées supprimées": old_count}
            )


async def setup(bot):
    await bot.add_cog(DevStats(bot))