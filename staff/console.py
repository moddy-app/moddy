"""
Commandes de gestion de la console pour développeurs
Permet de contrôler les logs console
"""

import nextcord as discord
from nextcord.ext import commands
from datetime import datetime
import logging
import sys
from pathlib import Path
import asyncio

sys.path.append(str(Path(__file__).parent.parent))
from utils.embeds import ModdyEmbed, ModdyResponse
from config import COLORS


class ConsoleCommands(commands.Cog):
    """Commandes pour gérer la console"""

    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        """Vérifie que l'utilisateur est développeur"""
        return self.bot.is_developer(ctx.author.id)

    @commands.command(name="console", aliases=["logs", "output"])
    async def show_console(self, ctx, lines: int = 20):
        """Affiche les derniers logs de la console"""
        console_logger = self.bot.get_cog("ConsoleLogger")
        if not console_logger:
            await ctx.send("❌ Système de logs console non chargé")
            return

        if lines > 50:
            lines = 50
        elif lines < 1:
            lines = 1

        # Récupère les derniers logs
        recent_logs = list(console_logger.log_buffer)[-lines:]

        if not recent_logs:
            embed = ModdyResponse.info(
                "Console vide",
                "Aucun log récent dans le buffer"
            )
            await ctx.send(embed=embed)
            return

        # Formate les logs
        log_content = []
        for log in recent_logs:
            log_content.append(log['content'])

        # Crée l'embed
        content = '\n'.join(log_content)
        if len(content) > 4000:
            content = content[:3997] + '...'

        embed = discord.Embed(
            title=f"Console - {len(recent_logs)} derniers logs",
            description=f"```\n{content}\n```",
            color=COLORS["primary"],
            timestamp=datetime.now()
        )

        embed.set_footer(text=f"Buffer: {len(console_logger.log_buffer)}/50 logs")

        await ctx.send(embed=embed)

    @commands.command(name="clearconsole", aliases=["clc", "clearcon"])
    async def clear_console(self, ctx):
        """Vide le buffer de la console"""
        console_logger = self.bot.get_cog("ConsoleLogger")
        if not console_logger:
            await ctx.send("❌ Système de logs console non chargé")
            return

        count = len(console_logger.log_buffer)
        console_logger.log_buffer.clear()

        # Vide aussi la queue
        while not console_logger.log_queue.empty():
            try:
                console_logger.log_queue.get_nowait()
            except:
                break

        embed = ModdyResponse.success(
            "Console vidée",
            f"`{count}` logs ont été supprimés du buffer"
        )
        await ctx.send(embed=embed)

    @commands.command(name="loglevel", aliases=["ll", "level"])
    async def set_log_level(self, ctx, level: str = None):
        """Change le niveau de logging"""
        if not level:
            current = logging.getLogger().getEffectiveLevel()
            embed = discord.Embed(
                title="Niveau de logging",
                description=f"**Niveau actuel :** `{logging.getLevelName(current)}`\n\n"
                            "**Niveaux disponibles :**\n"
                            "`DEBUG` - Tout les détails\n"
                            "`INFO` - Informations générales\n"
                            "`WARNING` - Avertissements\n"
                            "`ERROR` - Erreurs seulement\n"
                            "`CRITICAL` - Erreurs critiques seulement",
                color=COLORS["info"]
            )
            await ctx.send(embed=embed)
            return

        levels = {
            'DEBUG': logging.DEBUG,
            'INFO': logging.INFO,
            'WARNING': logging.WARNING,
            'ERROR': logging.ERROR,
            'CRITICAL': logging.CRITICAL
        }

        level_upper = level.upper()
        if level_upper not in levels:
            embed = ModdyResponse.error(
                "Niveau invalide",
                f"Utilise un de ces niveaux : {', '.join(levels.keys())}"
            )
            await ctx.send(embed=embed)
            return

        # Change le niveau
        logging.getLogger().setLevel(levels[level_upper])

        embed = ModdyResponse.success(
            "Niveau modifié",
            f"Nouveau niveau de logging : `{level_upper}`"
        )
        await ctx.send(embed=embed)

    @commands.command(name="print", aliases=["echo"])
    async def print_to_console(self, ctx, *, message: str):
        """Affiche un message dans la console"""
        print(f"[DEV {ctx.author}] {message}")

        embed = ModdyResponse.success(
            "Message envoyé",
            f"Le message a été affiché dans la console"
        )
        await ctx.send(embed=embed)

    @commands.command(name="exec", aliases=["eval"])
    async def execute_code(self, ctx, *, code: str = None):
        """Exécute du code Python (DANGEREUX - Owner uniquement)"""
        if not code:
            embed = ModdyResponse.error(
                "Code manquant",
                "Tu dois fournir du code à exécuter"
            )
            await ctx.send(embed=embed)
            return

        # Vérifie que c'est l'owner du bot
        app_info = await self.bot.application_info()
        if ctx.author.id != app_info.owner.id:
            embed = ModdyResponse.error(
                "Accès refusé",
                "Cette commande est réservée au propriétaire du bot."
            )
            await ctx.send(embed=embed)
            return

        # Owner confirmé, on exécute
        await self._execute_code(ctx, code)

    async def _execute_code(self, ctx, code: str):
        """Exécute réellement le code après vérification owner"""
        # Retire les backticks si présents
        if code.startswith("```python"):
            code = code[9:-3]
        elif code.startswith("```py"):
            code = code[5:-3]
        elif code.startswith("```"):
            code = code[3:-3]

        # Variables disponibles dans l'exécution
        env = {
            'bot': self.bot,
            'ctx': ctx,
            'channel': ctx.channel,
            'author': ctx.author,
            'guild': ctx.guild,
            'message': ctx.message,
            'discord': discord,
            'commands': commands
        }

        # Embed de départ
        embed = discord.Embed(
            title="Exécution de code",
            description=f"```py\n{code[:500]}{'...' if len(code) > 500 else ''}\n```",
            color=COLORS["warning"],
            timestamp=datetime.now()
        )
        embed.set_footer(text=f"Owner uniquement • {ctx.author}")
        msg = await ctx.send(embed=embed)

        # Capture la sortie
        import io
        import contextlib

        stdout = io.StringIO()

        try:
            # Redirige stdout
            with contextlib.redirect_stdout(stdout):
                # Si le code contient await, on l'exécute en async
                if 'await' in code:
                    # Crée une fonction async
                    exec(f'async def _exec():\n' + '\n'.join(f'    {line}' for line in code.split('\n')), env)
                    result = await env['_exec']()
                else:
                    # Exécution normale
                    exec(code, env)
                    result = None

            # Récupère la sortie
            output = stdout.getvalue()

            # Met à jour l'embed
            embed.color = COLORS["success"]

            if output:
                embed.add_field(
                    name="Sortie",
                    value=f"```\n{output[:1000]}{'...' if len(output) > 1000 else ''}\n```",
                    inline=False
                )

            if result is not None:
                embed.add_field(
                    name="Résultat",
                    value=f"```py\n{repr(result)[:500]}{'...' if len(repr(result)) > 500 else ''}\n```",
                    inline=False
                )

            await msg.edit(embed=embed)

        except Exception as e:
            # En cas d'erreur
            embed.color = COLORS["error"]
            embed.add_field(
                name="Erreur",
                value=f"```py\n{type(e).__name__}: {str(e)[:500]}\n```",
                inline=False
            )
            await msg.edit(embed=embed)

    @commands.command(name="logstats", aliases=["lstats"])
    async def log_stats(self, ctx):
        """Affiche des statistiques sur les logs"""
        console_logger = self.bot.get_cog("ConsoleLogger")
        if not console_logger:
            await ctx.send("❌ Système de logs console non chargé")
            return

        # Compte les logs par type
        log_types = {}
        for log in console_logger.log_buffer:
            log_type = log.get('type', 'unknown')
            log_types[log_type] = log_types.get(log_type, 0) + 1

        embed = discord.Embed(
            title="Statistiques des logs",
            description=f"**Total :** `{len(console_logger.log_buffer)}` logs en buffer",
            color=COLORS["info"],
            timestamp=datetime.now()
        )

        # Types de logs
        if log_types:
            types_text = "\n".join(
                [f"`{t}` : **{c}**" for t, c in sorted(log_types.items(), key=lambda x: x[1], reverse=True)])
            embed.add_field(
                name="Par type",
                value=types_text,
                inline=True
            )

        # État de la queue
        embed.add_field(
            name="Queue d'envoi",
            value=f"`{console_logger.log_queue.qsize()}` logs en attente",
            inline=True
        )

        # Canal de logs
        embed.add_field(
            name="Canal Discord",
            value=f"<#{console_logger.console_channel_id}>",
            inline=True
        )

        await ctx.send(embed=embed)


def setup(bot):
    bot.add_cog(ConsoleCommands(bot))