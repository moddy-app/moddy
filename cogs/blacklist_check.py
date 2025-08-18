"""
Système de vérification de blacklist
Intercepte toutes les interactions avant traitement
"""

import nextcord as discord
from nextcord.ext import commands
from typing import Union

from config import COLORS


class BlacklistButton(discord.ui.View):
    """Vue avec le bouton de demande d'unblacklist"""

    def __init__(self):
        super().__init__()
        # Ajoute le bouton avec un lien
        self.add_item(discord.ui.Button(
            label="Unblacklist request",
            url="https://moddy.app/unbl_request",
            style=discord.ButtonStyle.link
        ))


class BlacklistCheck(commands.Cog):
    """Vérifie le statut blacklist avant toute interaction"""

    def __init__(self, bot):
        self.bot = bot
        self.blacklist_cache = {}  # Cache pour éviter trop de requêtes DB

    async def is_blacklisted(self, user_id: int) -> bool:
        """Vérifie si un utilisateur est blacklisté (avec cache)"""
        # Vérifie le cache d'abord
        if user_id in self.blacklist_cache:
            return self.blacklist_cache[user_id]

        # Sinon vérifie la DB
        if self.bot.db:
            try:
                is_bl = await self.bot.db.has_attribute('user', user_id, 'BLACKLISTED')
                self.blacklist_cache[user_id] = is_bl
                return is_bl
            except:
                return False
        return False

    async def send_blacklist_message(self, interaction: discord.Interaction):
        """Envoie le message de blacklist"""
        embed = discord.Embed(
            description=(
                "<:blacklist:1401596864784777363> You have been blacklisted from using Moddy.\n"
                "<:blacklist:1401596864784777363> Vous avez été blacklisté de Moddy."
            ),
            color=COLORS["error"]
        )

        embed.set_footer(text=f"ID: {interaction.user.id}")

        view = BlacklistButton()

        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except:
            # Si tout échoue, essaye en message normal
            try:
                await interaction.channel.send(embed=embed, view=view)
            except:
                pass

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """Intercepte TOUTES les interactions Discord"""
        # Ignore les interactions du bot lui-même
        if interaction.user.bot:
            return

        # Vérifie seulement pour les commandes et composants
        if interaction.type not in [
            discord.InteractionType.application_command,
            discord.InteractionType.component,
            discord.InteractionType.modal_submit
        ]:
            return

        # Vérifie si l'utilisateur est blacklisté
        if await self.is_blacklisted(interaction.user.id):
            # Log l'interaction bloquée
            if log_cog := self.bot.get_cog("LoggingSystem"):
                await log_cog.log_critical(
                    title="Interaction Blacklistée",
                    description=(
                        f"**Utilisateur:** {interaction.user.mention} (`{interaction.user.id}`)\n"
                        f"**Type:** {interaction.type.name}\n"
                        f"**Commande:** {getattr(interaction.command, 'name', 'N/A')}\n"
                        f"**Serveur:** {interaction.guild.name if interaction.guild else 'DM'}"
                    ),
                    ping_dev=False
                )

            # Envoie le message de blacklist
            await self.send_blacklist_message(interaction)

            # IMPORTANT: Empêche la propagation de l'interaction
            # En levant une exception spéciale, on force Discord.py à arrêter le traitement
            raise commands.CheckFailure("User is blacklisted")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Vérifie aussi pour les commandes texte (préfixe)"""
        # Ignore les bots
        if message.author.bot:
            return

        # Vérifie si c'est potentiellement une commande
        if message.content:
            # Récupère les préfixes possibles
            prefixes = await self.bot.get_prefix(message)

            # Vérifie si le message commence par un préfixe
            for prefix in prefixes:
                if message.content.startswith(prefix):
                    # C'est une commande, vérifie le blacklist
                    if await self.is_blacklisted(message.author.id):
                        # Crée l'embed
                        embed = discord.Embed(
                            description=(
                                "<:blacklist:1401596864784777363> You have been blacklisted from using Moddy.\n"
                                "<:blacklist:1401596864784777363> Vous avez été blacklisté de Moddy."
                            ),
                            color=COLORS["error"]
                        )

                        embed.set_footer(text=f"ID: {message.author.id}")

                        view = BlacklistButton()

                        try:
                            await message.reply(embed=embed, view=view, mention_author=False)
                        except:
                            try:
                                await message.channel.send(embed=embed, view=view)
                            except:
                                pass

                        # Empêche le traitement de la commande
                        return

    @commands.command(name="clearcache", aliases=["cc"])
    async def clear_blacklist_cache(self, ctx):
        """Vide le cache de blacklist (commande dev)"""
        if not self.bot.is_developer(ctx.author.id):
            return

        self.blacklist_cache.clear()
        await ctx.send("<:done:1398729525277229066> Cache de blacklist vidé")

    @commands.command(name="testbl")
    async def test_blacklist(self, ctx):
        """Teste le message de blacklist (commande dev)"""
        if not self.bot.is_developer(ctx.author.id):
            return

        # Crée une fausse interaction
        class FakeInteraction:
            def __init__(self, user, channel):
                self.user = user
                self.channel = channel
                self.response = type('obj', (object,), {'is_done': lambda: False})()

            async def response(self):
                return self.response

        fake_interaction = FakeInteraction(ctx.author, ctx.channel)

        # Simule l'envoi du message
        embed = discord.Embed(
            description=(
                "<:blacklist:1401596864784777363> You have been blacklisted from using Moddy.\n"
                "<:blacklist:1401596864784777363> Vous avez été blacklisté de Moddy."
            ),
            color=COLORS["error"]
        )

        embed.set_footer(text=f"ID: {ctx.author.id}")

        view = BlacklistButton()

        await ctx.send("**[TEST MODE]** Voici ce que verrait un utilisateur blacklisté:", embed=embed, view=view)


def setup(bot):
    bot.add_cog(BlacklistCheck(bot))