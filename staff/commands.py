"""
Commande pour lister toutes les commandes disponibles
Utile pour le debug et la gestion
"""

import nextcord
from nextcord.ext import commands
from typing import List

# Import du système d'embeds épuré
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from utils.embeds import ModdyEmbed, ModdyColors
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

        embed = ModdyEmbed.create(
            title="<:commands:1401610449136648283> Commandes disponibles",
            color=ModdyColors.PRIMARY,
        )

        for cog_name, cog in self.bot.cogs.items():
            text_cmds: List[str] = []
            slash_cmds: List[str] = []

            for cmd in cog.get_commands():
                if not cmd.hidden:
                    aliases = f" ({', '.join(cmd.aliases)})" if cmd.aliases else ""
                    text_cmds.append(f"`{cmd.name}{aliases}`")

            for cmd in cog.get_app_commands():
                slash_cmds.append(f"`/{cmd.name}`")

            if text_cmds or slash_cmds:
                lines: List[str] = []
                if text_cmds:
                    lines.append("<:code:1401610523803652196> " + ", ".join(text_cmds))
                if slash_cmds:
                    lines.append("<:commands:1401610449136648283> " + ", ".join(slash_cmds))

                value = "\n".join(lines)
                if len(value) > 1024:
                    value = value[:1021] + "..."

                embed.add_field(
                    name=f"**{cog_name}**",
                    value=value,
                    inline=True,
                )

        no_cog_text: List[str] = []
        no_cog_slash: List[str] = []

        for cmd in self.bot.commands:
            if not cmd.cog and not cmd.hidden:
                aliases = f" ({', '.join(cmd.aliases)})" if cmd.aliases else ""
                no_cog_text.append(f"`{cmd.name}{aliases}`")

        for cmd in self.bot.tree.get_commands():
            if cmd.binding is None:
                no_cog_slash.append(f"`/{cmd.name}`")

        if no_cog_text or no_cog_slash:
            lines: List[str] = []
            if no_cog_text:
                lines.append("<:code:1401610523803652196> " + ", ".join(no_cog_text))
            if no_cog_slash:
                lines.append("<:commands:1401610449136648283> " + ", ".join(no_cog_slash))

            value = "\n".join(lines)
            if len(value) > 1024:
                value = value[:1021] + "..."

            embed.add_field(
                name="**Sans catégorie**",
                value=value,
                inline=True,
            )

        total_commands = len([c for c in self.bot.commands if not c.hidden])
        total_slash = len(self.bot.tree.get_commands())

        embed.set_footer(
            text=(
                f"<:code:1401610523803652196> {total_commands} | "
                f"<:commands:1401610449136648283> {total_slash}"
            )
        )

        await ctx.send(embed=embed)

    @commands.command(name="cogs", aliases=["modules"])
    async def list_cogs(self, ctx):
        """Liste tous les cogs chargés"""

        embed = nextcord.Embed(
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


async def setup(bot):
    await bot.add_cog(CommandsList(bot))
