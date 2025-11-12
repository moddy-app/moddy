"""
Système de vérification de blacklist
Intercepte toutes les interactions avant traitement
"""

import discord
from discord.ext import commands
from typing import Union

from config import COLORS, EMOJIS


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

        # Override la méthode process_commands pour bloquer AVANT le traitement
        original_process_commands = bot.process_commands

        async def blacklist_aware_process_commands(message):
            """Intercepte les commandes AVANT qu'elles ne soient traitées"""
            if message.author.bot:
                return await original_process_commands(message)

            # Vérifie si l'utilisateur est blacklisté
            if await self.is_blacklisted(message.author.id):
                # Envoie le message de blacklist
                embed = discord.Embed(
                    description=(
                        f"{EMOJIS['undone']} You cannot interact with Moddy because your account has been blacklisted by our team.\n\n"
                        f"*Vous ne pouvez pas interagir avec Moddy car votre compte a été blacklisté par notre équipe.*"
                    ),
                    color=COLORS["error"]
                )
                embed.set_footer(text=f"User ID: {message.author.id}")
                view = BlacklistButton()

                try:
                    await message.reply(embed=embed, view=view, mention_author=False)
                except:
                    try:
                        await message.channel.send(embed=embed, view=view)
                    except:
                        pass

                # NE PAS traiter la commande - return sans appeler original_process_commands
                return

            # Si pas blacklisté, traite normalement
            return await original_process_commands(message)

        bot.process_commands = blacklist_aware_process_commands

        # Override on_interaction pour bloquer les interactions AVANT dispatch
        original_on_interaction = bot.on_interaction if hasattr(bot, 'on_interaction') else None

        async def blacklist_aware_on_interaction(interaction: discord.Interaction):
            """Intercepte TOUTES les interactions AVANT qu'elles soient dispatchées"""
            # Ignore les bots
            if interaction.user.bot:
                if original_on_interaction:
                    return await original_on_interaction(interaction)
                return

            # Vérifie si l'utilisateur est blacklisté
            if await self.is_blacklisted(interaction.user.id):
                # BLOQUE l'interaction en y répondant immédiatement
                # Une fois qu'on a répondu, les handlers ne peuvent plus traiter l'interaction
                try:
                    await self.send_blacklist_message(interaction)
                except Exception as e:
                    # Fallback si l'envoi échoue
                    try:
                        if not interaction.response.is_done():
                            await interaction.response.send_message(
                                f"{EMOJIS['undone']} You cannot interact with Moddy.",
                                ephemeral=True
                            )
                    except:
                        pass

                # Log l'interaction bloquée
                if log_cog := self.bot.get_cog("LoggingSystem"):
                    try:
                        await log_cog.log_critical(
                            title="🚫 Interaction Blacklistée Bloquée",
                            description=(
                                f"**Utilisateur:** {interaction.user.mention} (`{interaction.user.id}`)\n"
                                f"**Type:** {interaction.type.name}\n"
                                f"**Custom ID:** {interaction.data.get('custom_id', 'N/A')}\n"
                                f"**Serveur:** {interaction.guild.name if interaction.guild else 'DM'}\n"
                                f"**Action:** Interaction bloquée AVANT dispatch"
                            ),
                            ping_dev=False
                        )
                    except:
                        pass

                # NE PAS appeler original_on_interaction - on bloque complètement
                return

            # Si pas blacklisté, laisse l'interaction continuer normalement
            if original_on_interaction:
                return await original_on_interaction(interaction)

        # Replace la méthode au niveau du bot
        bot.on_interaction = blacklist_aware_on_interaction

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
                f"{EMOJIS['undone']} You cannot interact with Moddy because your account has been blacklisted by our team.\n\n"
                f"*Vous ne pouvez pas interagir avec Moddy car votre compte a été blacklisté par notre équipe.*"
            ),
            color=COLORS["error"]
        )

        embed.set_footer(text=f"User ID: {interaction.user.id}")

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
                f"{EMOJIS['undone']} You cannot interact with Moddy because your account has been blacklisted by our team.\n\n"
                f"*Vous ne pouvez pas interagir avec Moddy car votre compte a été blacklisté par notre équipe.*"
            ),
            color=COLORS["error"]
        )

        embed.set_footer(text=f"User ID: {ctx.author.id}")

        view = BlacklistButton()

        await ctx.send("**[TEST MODE]** Voici ce que verrait un utilisateur blacklisté:", embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(BlacklistCheck(bot))
