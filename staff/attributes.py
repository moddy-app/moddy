"""
Commandes pour gérer les attributs utilisateurs/serveurs
Réservées aux développeurs
"""

import nextcord as discord
from nextcord.ext import commands
from datetime import datetime
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from utils.embeds import ModdyEmbed, ModdyResponse
from config import COLORS


class AttributeCommands(commands.Cog):
    """Gestion des attributs système"""

    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        """Vérifie que l'utilisateur est développeur"""
        return self.bot.is_developer(ctx.author.id)

    @commands.command(name="attr", aliases=["attribute"])
    async def attribute(self, ctx, action: str = None, target: discord.User = None, attr_name: str = None, *,
                        value: str = None):
        """Gère les attributs utilisateurs"""

        if not self.bot.db:
            await ctx.send("<:undone:1398729502028333218> Base de données non connectée")
            return

        if not action:
            # Affiche l'aide
            embed = discord.Embed(
                title="<:manageuser:1398729745293774919> Gestion des attributs",
                description=(
                    "**Utilisation :**\n"
                    "`attr get @user` - Voir les attributs d'un utilisateur\n"
                    "`attr set @user ATTRIBUT valeur` - Définir un attribut\n"
                    "`attr remove @user ATTRIBUT` - Supprimer un attribut\n"
                    "`attr list` - Lister tous les utilisateurs avec attributs\n\n"
                    "**Attributs disponibles :**\n"
                    "`DEVELOPER`, `BETA`, `PREMIUM`, `BLACKLISTED`, `VERIFIED`"
                ),
                color=COLORS["info"]
            )
            await ctx.send(embed=embed)
            return

        if action.lower() == "get":
            if not target:
                target = ctx.author

            # Récupère l'utilisateur
            user_data = await self.bot.db.get_user(target.id)

            embed = discord.Embed(
                title=f"<:label:1398729473649676440> Attributs de {target}",
                color=COLORS["info"]
            )

            if user_data['attributes']:
                attrs_text = "\n".join([f"`{k}`: **{v}**" for k, v in user_data['attributes'].items()])
                embed.add_field(name="Attributs", value=attrs_text, inline=False)
            else:
                embed.description = "Aucun attribut défini"

            # Ajoute les infos de la BDD
            embed.add_field(
                name="<:info:1401614681440784477> Informations",
                value=(
                    f"**ID :** `{target.id}`\n"
                    f"**Créé :** <t:{int(user_data.get('created_at', datetime.now()).timestamp())}:R>\n"
                    f"**Modifié :** <t:{int(user_data.get('updated_at', datetime.now()).timestamp())}:R>"
                ),
                inline=False
            )

            await ctx.send(embed=embed)

        elif action.lower() == "set":
            if not target or not attr_name:
                await ctx.send("<:undone:1398729502028333218> Usage: `attr set @user ATTRIBUT valeur`")
                return

            # Convertit la valeur
            if value and value.lower() in ['true', 'yes', '1']:
                value = True
            elif value and value.lower() in ['false', 'no', '0']:
                value = False
            elif value and value.isdigit():
                value = int(value)

            # Définit l'attribut
            try:
                await self.bot.db.set_attribute(
                    'user', target.id, attr_name.upper(),
                    value, ctx.author.id, f"Commande manuelle par {ctx.author}"
                )

                embed = ModdyResponse.success(
                    "Attribut défini",
                    f"**{attr_name.upper()}** = `{value}` pour {target.mention}"
                )
                await ctx.send(embed=embed)

            except Exception as e:
                embed = ModdyResponse.error(
                    "Erreur",
                    f"Impossible de définir l'attribut : {str(e)}"
                )
                await ctx.send(embed=embed)

        elif action.lower() == "remove":
            if not target or not attr_name:
                await ctx.send("<:undone:1398729502028333218> Usage: `attr remove @user ATTRIBUT`")
                return

            try:
                await self.bot.db.set_attribute(
                    'user', target.id, attr_name.upper(),
                    None, ctx.author.id, f"Suppression par {ctx.author}"
                )

                embed = ModdyResponse.success(
                    "Attribut supprimé",
                    f"**{attr_name.upper()}** supprimé pour {target.mention}"
                )
                await ctx.send(embed=embed)

            except Exception as e:
                embed = ModdyResponse.error(
                    "Erreur",
                    f"Impossible de supprimer l'attribut : {str(e)}"
                )
                await ctx.send(embed=embed)

        elif action.lower() == "list":
            # Liste tous les utilisateurs avec des attributs
            try:
                # Récupère les utilisateurs avec des attributs
                async with self.bot.db.pool.acquire() as conn:
                    rows = await conn.fetch("""
                        SELECT user_id, attributes
                        FROM users
                        WHERE attributes != '{}'::jsonb
                        LIMIT 20
                    """)

                if not rows:
                    await ctx.send("<:info:1401614681440784477> Aucun utilisateur avec des attributs")
                    return

                embed = discord.Embed(
                    title="<:user:1398729712204779571> Utilisateurs avec attributs",
                    color=COLORS["info"]
                )

                for row in rows:
                    user_id = row['user_id']
                    attrs = row['attributes']

                    # Essaye de récupérer l'utilisateur
                    try:
                        user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                        user_str = f"{user} ({user_id})"
                    except:
                        user_str = f"Utilisateur {user_id}"

                    attrs_str = ", ".join([f"`{k}`" for k in attrs.keys()])
                    embed.add_field(
                        name=user_str,
                        value=attrs_str or "Aucun",
                        inline=False
                    )

                await ctx.send(embed=embed)

            except Exception as e:
                await ctx.send(f"<:undone:1398729502028333218> Erreur : {str(e)}")

    @commands.command(name="fixdev", aliases=["devfix"])
    async def fix_developers(self, ctx):
        """Force l'ajout de l'attribut DEVELOPER"""
        if not self.bot.db:
            await ctx.send("<:undone:1398729502028333218> Base de données non connectée")
            return

        embed = discord.Embed(
            title="<:settings:1398729549323440208> Mise à jour des développeurs",
            description="<:loading:1395047662092550194> Attribution du statut DEVELOPER...",
            color=COLORS["warning"]
        )
        msg = await ctx.send(embed=embed)

        success = []
        errors = []

        for dev_id in self.bot._dev_team_ids:
            try:
                # Crée l'utilisateur s'il n'existe pas
                await self.bot.db.get_user(dev_id)

                # Définit l'attribut DEVELOPER (True dans le nouveau système)
                await self.bot.db.set_attribute(
                    'user', dev_id, 'DEVELOPER', True,
                    ctx.author.id, "Commande fixdev"
                )

                # Vérifie
                if await self.bot.db.has_attribute('user', dev_id, 'DEVELOPER'):
                    success.append(dev_id)
                else:
                    errors.append((dev_id, "Attribut non défini"))

            except Exception as e:
                errors.append((dev_id, str(e)))

        # Met à jour l'embed
        embed.color = COLORS["success"] if not errors else COLORS["warning"]
        embed.description = ""

        if success:
            embed.add_field(
                name="<:done:1398729525277229066> Succès",
                value="\n".join([f"<@{uid}>" for uid in success]),
                inline=True
            )

        if errors:
            embed.add_field(
                name="<:undone:1398729502028333218> Erreurs",
                value="\n".join([f"<@{uid}>: {err}" for uid, err in errors]),
                inline=True
            )

        await msg.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(AttributeCommands(bot))