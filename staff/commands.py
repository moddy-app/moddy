"""
Commande pour lister toutes les commandes disponibles
Utile pour le debug et la gestion
"""

import nextcord as discord
from nextcord.ext import commands
from typing import List

# Import du système d'embeds épuré
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from utils.embeds import ModdyEmbed, ModdyResponse, ModdyColors
from config import COLORS


class CommandsList(commands.Cog):
    """Liste les commandes du bot"""

    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        """Vérifie que l'utilisateur est développeur"""
        return self.bot.is_developer(ctx.author.id)

    @commands.command(name="commands", aliases=["cmds", "list"])
    async def list_commands(self, ctx):
        """Liste toutes les commandes disponibles"""

        embed = discord.Embed(
            title="Commandes Disponibles",
            color=COLORS["primary"]
        )

        # Commandes par cog
        for cog_name, cog in self.bot.cogs.items():
            commands_list = []

            # Commandes texte
            for cmd in cog.get_commands():
                if not cmd.hidden:
                    aliases = f" ({', '.join(cmd.aliases)})" if cmd.aliases else ""
                    commands_list.append(f"`{cmd.name}{aliases}`")

            # Commandes slash
            for cmd in cog.get_app_commands():
                commands_list.append(f"`/{cmd.name}` (slash)")

            if commands_list:
                # Limiter la longueur pour respecter la limite Discord
                value = "\n".join(commands_list)
                if len(value) > 1024:
                    value = value[:1021] + "..."

                embed.add_field(
                    name=f"**{cog_name}**",
                    value=value,
                    inline=True
                )

        # Commandes sans cog
        no_cog_commands = []
        for cmd in self.bot.commands:
            if not cmd.cog and not cmd.hidden:
                aliases = f" ({', '.join(cmd.aliases)})" if cmd.aliases else ""
                no_cog_commands.append(f"`{cmd.name}{aliases}`")

        if no_cog_commands:
            value = "\n".join(no_cog_commands)
            if len(value) > 1024:
                value = value[:1021] + "..."

            embed.add_field(
                name="**Sans catégorie**",
                value=value,
                inline=True
            )

        # Stats
        total_commands = len([c for c in self.bot.commands if not c.hidden])
        total_slash = len(self.bot.tree.get_commands())

        embed.set_footer(text=f"Total : {total_commands} commandes texte, {total_slash} commandes slash")

        await ctx.send(embed=embed)

    @commands.command(name="cogs", aliases=["modules"])
    async def list_cogs(self, ctx):
        """Liste tous les cogs chargés"""

        embed = discord.Embed(
            title="Modules chargés",
            description="",
            color=COLORS["primary"]
        )

        for cog_name, cog in self.bot.cogs.items():
            # Compte les commandes
            text_cmds = len([c for c in cog.get_commands() if not c.hidden])
            app_cmds = len(cog.get_app_commands())

            # Détermine le type
            cog_type = "Staff" if "staff" in cog.__module__ else "Public"

            embed.description += (
                f"**{cog_name}** ({cog_type})\n"
                f"→ `{text_cmds}` cmd(s) texte, `{app_cmds}` cmd(s) slash\n\n"
            )

        embed.set_footer(text=f"Total : {len(self.bot.cogs)} cogs")

        await ctx.send(embed=embed)


def setup(bot):
    bot.add_cog(CommandsList(bot))