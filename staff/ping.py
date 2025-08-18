"""
Commande ping pour développeurs
Affiche des informations détaillées sur le statut du bot
Style épuré sans emojis système
"""

import nextcord
from nextcord.ext import commands
import asyncio
import time
import platform
import psutil
from datetime import datetime, timezone
from typing import Optional

# Import du système d'embeds épuré
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from utils.embeds import ModdyEmbed, ModdyResponse, format_diagnostic_embed
from config import COLORS


class StaffDiagnostic(commands.Cog):
    """Commandes de diagnostic pour développeurs"""

    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        """Vérifie que l'utilisateur est développeur"""
        return self.bot.is_developer(ctx.author.id)

    @commands.command(name="diag", aliases=["diagnostic", "sysinfo"])
    async def diagnostic(self, ctx):
        """Affiche le statut détaillé du bot"""

        # Message de chargement
        loading_embed = ModdyResponse.loading("Diagnostic en cours...")
        msg = await ctx.send(embed=loading_embed)

        # Mesure de la latence du message
        start_time = time.perf_counter()

        # Tests de latence
        api_latency = round(self.bot.latency * 1000, 2)

        # Latence du message
        end_time = time.perf_counter()
        message_latency = round((end_time - start_time) * 1000, 2)

        # Test de la base de données
        db_status = "Non connectée"
        db_latency = "N/A"

        if self.bot.db_pool:
            try:
                db_start = time.perf_counter()
                async with self.bot.db_pool.acquire() as conn:
                    await conn.fetchval("SELECT 1")
                db_end = time.perf_counter()
                db_latency = f"`{round((db_end - db_start) * 1000, 2)}ms`"
                db_status = "Opérationnelle"
            except Exception as e:
                db_status = f"Erreur : `{type(e).__name__}`"

        # Informations système
        process = psutil.Process()
        memory_usage = process.memory_info().rss / 1024 / 1024  # MB
        cpu_percent = process.cpu_percent(interval=0.1)

        # Uptime
        uptime = datetime.now(timezone.utc) - self.bot.launch_time
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{hours}h {minutes}m {seconds}s"

        # Données pour l'embed
        diagnostic_data = {
            'api_latency': api_latency,
            'message_latency': message_latency,
            'discord_version': nextcord.__version__,
            'db_status': db_status,
            'db_latency': db_latency,
            'uptime': uptime_str,
            'cpu_percent': cpu_percent,
            'memory_usage': memory_usage,
            'threads': len(self.bot.guilds),
            'guilds': len(self.bot.guilds),
            'users': len(self.bot.users),
            'commands': len(self.bot.commands),
            'os': f"{platform.system()} {platform.release()}",
            'python_version': platform.python_version(),
            'node': platform.node(),
            'author': str(ctx.author)
        }

        # Créer l'embed de diagnostic
        embed = format_diagnostic_embed(diagnostic_data)

        # Créer la vue avec les boutons
        view = DiagnosticView(self.bot, ctx.author)

        await msg.edit(embed=embed, view=view)

    @commands.command(name="ping", aliases=["p"])
    async def fast_ping(self, ctx):
        """Ping rapide sans détails"""
        start = time.perf_counter()
        msg = await ctx.send("Pong!")
        end = time.perf_counter()

        await msg.edit(
            content=f"Pong! | API: `{round(self.bot.latency * 1000)}ms` | Message: `{round((end - start) * 1000)}ms`"
        )


class DiagnosticView(nextcord.ui.View):
    """Vue avec boutons pour le diagnostic"""

    def __init__(self, bot, author):
        super().__init__(timeout=60)
        self.bot = bot
        self.author = author

    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        """Vérifie que seul l'auteur peut utiliser les boutons"""
        if interaction.user != self.author:
            await interaction.response.send_message(
                "Seul l'auteur de la commande peut utiliser ces boutons.",
                ephemeral=True
            )
            return False
        return True

    @nextcord.ui.button(label="Rafraîchir", style=nextcord.ButtonStyle.primary)
    async def refresh(self, interaction: nextcord.Interaction, button: nextcord.ui.Button):
        """Rafraîchit les statistiques"""
        await interaction.response.send_message("Rafraîchissement...", ephemeral=True)

        # Relance la commande
        ctx = await self.bot.get_context(interaction.message)
        ctx.author = self.author
        await self.bot.get_command("diag").invoke(ctx)

    @nextcord.ui.button(label="Collecter les déchets", style=nextcord.ButtonStyle.secondary)
    async def garbage_collect(self, interaction: nextcord.Interaction, button: nextcord.ui.Button):
        """Force le garbage collector Python"""
        import gc
        collected = gc.collect()
        await interaction.response.send_message(
            f"Garbage collector exécuté : `{collected}` objets libérés",
            ephemeral=True
        )

    @nextcord.ui.button(label="Logs", style=nextcord.ButtonStyle.secondary)
    async def show_logs(self, interaction: nextcord.Interaction, button: nextcord.ui.Button):
        """Affiche les derniers logs"""
        from config import LOG_FILE

        if LOG_FILE.exists():
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                last_logs = ''.join(lines[-10:])  # 10 dernières lignes

            embed = nextcord.Embed(
                title="Derniers logs",
                description=f"```\n{last_logs[-1900:]}\n```",
                color=COLORS["developer"]
            )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "Aucun fichier de log trouvé",
                ephemeral=True
            )

    @nextcord.ui.button(label="Fermer", style=nextcord.ButtonStyle.danger)
    async def close(self, interaction: nextcord.Interaction, button: nextcord.ui.Button):
        """Ferme le diagnostic"""
        await interaction.response.defer()
        await interaction.delete_original_response()
        self.stop()


async def setup(bot):
    await bot.add_cog(StaffDiagnostic(bot))