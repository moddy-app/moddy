"""
Système de vérification de blacklist - INTERCEPTION TOTALE
Bloque TOUTES les interactions des utilisateurs blacklistés AVANT qu'elles n'arrivent à destination

Note: L'interception des interactions (slash commands, boutons, modals, selects) se fait
maintenant directement dans bot.py via on_interaction() pour une efficacité maximale.

Ce cog gère:
- Interception des commandes par préfixe (via process_commands override)
- Cache de blacklist pour performance
- Commandes utilitaires pour les devs
"""

import discord
from discord.ext import commands
from utils.components_v2 import create_blacklist_message
from utils.emojis import DONE


class BlacklistCheck(commands.Cog):
    """
    Vérifie le statut blacklist pour les commandes par préfixe.

    Note: Les interactions (slash commands, boutons, etc.) sont interceptées
    directement dans bot.py via on_interaction() pour une efficacité maximale.
    """

    def __init__(self, bot):
        self.bot = bot
        self.blacklist_cache = {}  # Cache pour éviter trop de requêtes DB

        # Override la méthode process_commands pour bloquer les commandes par préfixe
        original_process_commands = bot.process_commands

        async def blacklist_aware_process_commands(message):
            """Intercepte les commandes par préfixe AVANT qu'elles ne soient traitées"""
            if message.author.bot:
                return await original_process_commands(message)

            # Vérifie si le message commence par un préfixe (commande ou mention)
            prefixes = await self.bot.get_prefix(message)
            if isinstance(prefixes, str):
                prefixes = [prefixes]

            # Vérifie si le message commence par un des préfixes
            is_command = any(message.content.startswith(prefix) for prefix in prefixes)

            # Si ce n'est pas une commande, laisse passer sans vérifier la blacklist
            if not is_command:
                return await original_process_commands(message)

            # C'est une commande, vérifie si l'utilisateur est blacklisté
            if await self.is_blacklisted(message.author.id):
                # Envoie le message de blacklist avec Components V2
                view = create_blacklist_message()

                try:
                    await message.reply(
                        view=view,
                        mention_author=False
                    )
                except:
                    try:
                        await message.channel.send(
                            view=view
                        )
                    except:
                        pass

                # Log l'interaction bloquée
                if log_cog := self.bot.get_cog("LoggingSystem"):
                    try:
                        await log_cog.log_critical(
                            title="🚫 Commande Préfixe Blacklistée Bloquée",
                            description=(
                                f"**Utilisateur:** {message.author.mention} (`{message.author.id}`)\n"
                                f"**Commande:** {message.content[:100]}\n"
                                f"**Serveur:** {message.guild.name if message.guild else 'DM'}\n"
                                f"**Action:** ✋ Commande par préfixe bloquée AVANT traitement"
                            ),
                            ping_dev=False
                        )
                    except:
                        pass

                # NE PAS traiter la commande
                return

            # Si pas blacklisté, traite normalement
            return await original_process_commands(message)

        bot.process_commands = blacklist_aware_process_commands

    async def is_blacklisted(self, user_id: int) -> bool:
        """Vérifie si un utilisateur est blacklisté (avec cache)"""
        # Vérifie le cache d'abord
        if user_id in self.blacklist_cache:
            return self.blacklist_cache[user_id]

        # Sinon vérifie la DB - check for active GLOBAL_BLACKLIST case
        if self.bot.db:
            try:
                # Check if user has an active global blacklist case
                from utils.moderation_cases import SanctionType
                is_bl = await self.bot.db.has_active_sanction(
                    'user',
                    user_id,
                    SanctionType.GLOBAL_BLACKLIST.value
                )
                self.blacklist_cache[user_id] = is_bl
                return is_bl
            except:
                return False
        return False

    @commands.command(name="clearcache", aliases=["cc"])
    async def clear_blacklist_cache(self, ctx):
        """Vide le cache de blacklist (commande dev)"""
        if not self.bot.is_developer(ctx.author.id):
            return

        self.blacklist_cache.clear()
        await ctx.send(f"{DONE} Cache de blacklist vidé")

    @commands.command(name="testbl")
    async def test_blacklist(self, ctx):
        """Teste le message de blacklist (commande dev)"""
        if not self.bot.is_developer(ctx.author.id):
            return

        # Utilise le système Components V2 pour le test
        view = create_blacklist_message()

        await ctx.send(
            "**[TEST MODE]** Voici ce que verrait un utilisateur blacklisté:",
            view=view
        )


async def setup(bot):
    await bot.add_cog(BlacklistCheck(bot))
