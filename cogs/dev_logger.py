"""
Système de logging pour les commandes développeur
Enregistre toutes les utilisations de commandes staff dans un canal dédié
"""

import nextcord
from nextcord.ext import commands
from datetime import datetime, timezone
import traceback
import json
from typing import Optional, Dict, Any

from config import COLORS


class DevCommandLogger(commands.Cog):
    """Logger automatique pour toutes les commandes dev"""

    def __init__(self, bot):
        self.bot = bot
        self.log_channel_id = 1394323753701212291  # Canal de logs dev
        self.command_stats = {}  # Statistiques d'utilisation

    async def get_log_channel(self) -> Optional[nextcord.TextChannel]:
        """Récupère le canal de logs"""
        return self.bot.get_channel(self.log_channel_id)

    def is_dev_command(self, ctx: commands.Context) -> bool:
        """Vérifie si c'est une commande dev"""
        # Vérifie si le cog est dans le dossier staff
        if ctx.command and ctx.command.cog:
            cog_module = ctx.command.cog.__module__
            return cog_module.startswith('staff.')
        return False

    async def log_command_execution(
            self,
            ctx: commands.Context,
            success: bool,
            error: Optional[Exception] = None,
            execution_time: float = 0.0,
            additional_info: Dict[str, Any] = None
    ):
        """Log l'exécution d'une commande dev"""
        channel = await self.get_log_channel()
        if not channel:
            return

        # Détermine la couleur selon le résultat
        if success:
            color = COLORS["success"]
            status = "✅ Succès"
        else:
            color = COLORS["error"]
            status = "❌ Échec"

        # Crée l'embed principal
        embed = nextcord.Embed(
            title=f"Commande Dev : `{ctx.command.name}`",
            color=color,
            timestamp=datetime.now(timezone.utc)
        )

        # Informations sur l'utilisateur
        embed.add_field(
            name="👤 Utilisateur",
            value=f"{ctx.author.mention}\n`{ctx.author}` (`{ctx.author.id}`)",
            inline=True
        )

        # Informations sur le lieu d'exécution
        if ctx.guild:
            location = f"**Serveur :** {ctx.guild.name}\n**Canal :** {ctx.channel.mention}"
        else:
            location = "**DM**"

        embed.add_field(
            name="📍 Lieu",
            value=location,
            inline=True
        )

        # Statut et temps d'exécution
        embed.add_field(
            name="📊 Statut",
            value=f"{status}\n**Temps :** `{execution_time:.2f}s`",
            inline=True
        )

        # Commande complète
        # Masquer les tokens ou infos sensibles
        command_text = ctx.message.content
        if "token" in command_text.lower() or "secret" in command_text.lower():
            # Masque les parties sensibles
            words = command_text.split()
            for i, word in enumerate(words):
                if len(word) > 20 and not word.startswith("<@"):  # Probablement un token
                    words[i] = f"{word[:6]}...{word[-4:]}"
            command_text = " ".join(words)

        embed.add_field(
            name="💬 Commande",
            value=f"```\n{command_text[:500]}\n```",
            inline=False
        )

        # Arguments de la commande
        if ctx.args or ctx.kwargs:
            args_str = ""
            if len(ctx.args) > 2:  # Ignore self et ctx
                args_list = [repr(arg) for arg in ctx.args[2:]]  # Skip self et ctx
                args_str += f"**Args :** {', '.join(args_list[:5])}\n"
            if ctx.kwargs:
                kwargs_list = [f"{k}={repr(v)}" for k, v in list(ctx.kwargs.items())[:5]]
                args_str += f"**Kwargs :** {', '.join(kwargs_list)}"

            if args_str:
                embed.add_field(
                    name="📝 Arguments",
                    value=args_str[:1024],
                    inline=False
                )

        # Erreur si échec
        if error:
            error_details = f"**Type :** `{type(error).__name__}`\n"
            error_details += f"**Message :** {str(error)[:200]}"

            # Traceback court pour les erreurs graves
            if not isinstance(error, (commands.CommandError, commands.CheckFailure)):
                tb_lines = traceback.format_exception(type(error), error, error.__traceback__)
                tb_short = '\n'.join(tb_lines[-3:])[:500]
                error_details += f"\n```py\n{tb_short}\n```"

            embed.add_field(
                name="⚠️ Erreur",
                value=error_details,
                inline=False
            )

        # Informations additionnelles
        if additional_info:
            info_str = "\n".join([f"**{k}:** {v}" for k, v in list(additional_info.items())[:5]])
            embed.add_field(
                name="ℹ️ Infos supplémentaires",
                value=info_str[:1024],
                inline=False
            )

        # Footer avec des stats
        command_count = self.command_stats.get(ctx.command.name, 0) + 1
        self.command_stats[ctx.command.name] = command_count
        embed.set_footer(
            text=f"Utilisation #{command_count} • Module: {ctx.command.cog.__class__.__name__}",
            icon_url=ctx.author.display_avatar.url
        )

        # Envoie le log
        try:
            await channel.send(embed=embed)
        except Exception as e:
            print(f"Erreur lors de l'envoi du log: {e}")

    @commands.Cog.listener()
    async def on_command(self, ctx: commands.Context):
        """Appelé quand une commande commence"""
        if self.is_dev_command(ctx):
            # Stocke le temps de début
            ctx.command_start_time = datetime.now()

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context):
        """Appelé quand une commande se termine avec succès"""
        if self.is_dev_command(ctx):
            # Calcule le temps d'exécution
            execution_time = 0.0
            if hasattr(ctx, 'command_start_time'):
                execution_time = (datetime.now() - ctx.command_start_time).total_seconds()

            # Log le succès
            await self.log_command_execution(
                ctx=ctx,
                success=True,
                execution_time=execution_time
            )

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        """Appelé quand une commande échoue"""
        if self.is_dev_command(ctx):
            # Calcule le temps d'exécution
            execution_time = 0.0
            if hasattr(ctx, 'command_start_time'):
                execution_time = (datetime.now() - ctx.command_start_time).total_seconds()

            # Log l'échec
            await self.log_command_execution(
                ctx=ctx,
                success=False,
                error=error,
                execution_time=execution_time
            )


class LoggingSystem(commands.Cog):
    """Système de logging manuel pour les commandes dev"""

    def __init__(self, bot):
        self.bot = bot
        self.log_channel_id = 1394323753701212291

    async def log_command(self, ctx: commands.Context, action: str, details: Dict[str, Any] = None):
        """Log manuel pour des actions spécifiques"""
        channel = self.bot.get_channel(self.log_channel_id)
        if not channel:
            return

        embed = nextcord.Embed(
            title=f"🔧 Action Dev : {action}",
            color=COLORS["info"],
            timestamp=datetime.now(timezone.utc)
        )

        embed.add_field(
            name="Utilisateur",
            value=f"{ctx.author.mention} (`{ctx.author.id}`)",
            inline=True
        )

        if ctx.guild:
            embed.add_field(
                name="Serveur",
                value=f"{ctx.guild.name}",
                inline=True
            )

        if details:
            for key, value in details.items():
                embed.add_field(
                    name=key,
                    value=str(value)[:1024],
                    inline=False
                )

        embed.set_footer(
            text=f"Action: {action}",
            icon_url=ctx.author.display_avatar.url
        )

        try:
            await channel.send(embed=embed)
        except:
            pass

    async def log_critical(self, title: str, description: str, ping_dev: bool = True):
        """Log pour les événements critiques"""
        channel = self.bot.get_channel(self.log_channel_id)
        if not channel:
            return

        embed = nextcord.Embed(
            title=f"🚨 {title}",
            description=description,
            color=COLORS["error"],
            timestamp=datetime.now(timezone.utc)
        )

        content = None
        if ping_dev:
            # Ping le premier dev de l'équipe
            if self.bot._dev_team_ids:
                dev_id = next(iter(self.bot._dev_team_ids))
                content = f"<@{dev_id}> Alerte critique !"

        try:
            await channel.send(content=content, embed=embed)
        except:
            pass


async def setup(bot):
    # Charge d'abord le logger automatique
    await bot.add_cog(DevCommandLogger(bot))
    # Puis le système de logging manuel
    await bot.add_cog(LoggingSystem(bot))