"""
Global suspension check for prefix commands — INTERCEPTION TOTALE.

A **suspension** is the heaviest global sanction (a ``ban`` sanction on a
``global`` case, see ``utils/global_sanctions.py``): the subject — a user or a
whole server — has access to no Moddy service at all. It replaces the legacy
``BLACKLISTED`` attribute, which no longer exists.

Interactions (slash commands, buttons, modals, selects) are intercepted
directly in bot.py (``_global_sanction_check`` / ``_check_suspension_and_respond``)
for maximum efficiency. This cog only covers the prefix-command path, through a
``process_commands`` override.
"""

import discord
from discord.ext import commands

from utils import global_sanctions
from utils.components_v2 import create_suspension_message
from utils.emojis import DONE


class BlacklistCheck(commands.Cog):
    """
    Blocks prefix commands coming from a globally suspended user or server.

    The resolved level is cached by ``utils.global_sanctions`` (short TTL,
    bounded by the sanction's own expiry), so this adds no query on the hot
    path.
    """

    def __init__(self, bot):
        self.bot = bot

        # Override process_commands to block prefix commands upfront.
        original_process_commands = bot.process_commands

        async def suspension_aware_process_commands(message):
            """Intercepte les commandes par préfixe AVANT qu'elles ne soient traitées"""
            if message.author.bot:
                return await original_process_commands(message)

            # Vérifie si le message commence par un préfixe (commande ou mention)
            prefixes = await self.bot.get_prefix(message)
            if isinstance(prefixes, str):
                prefixes = [prefixes]

            # Vérifie si le message commence par un des préfixes
            is_command = any(message.content.startswith(prefix) for prefix in prefixes)

            # Si ce n'est pas une commande, laisse passer sans vérifier la suspension
            if not is_command:
                return await original_process_commands(message)

            # C'est une commande : l'auteur ou son serveur est-il suspendu ?
            user_suspended = await self.is_suspended(message.author.id)
            guild_suspended = (
                not user_suspended
                and message.guild is not None
                and await global_sanctions.is_suspended(self.bot, guild_id=message.guild.id)
            )

            if user_suspended or guild_suspended:
                locale = 'en-US'
                if message.guild and message.guild.preferred_locale:
                    locale = str(message.guild.preferred_locale)
                view = create_suspension_message(locale, guild=guild_suspended)

                try:
                    await message.reply(
                        view=view,
                        mention_author=False
                    )
                except Exception:
                    try:
                        await message.channel.send(
                            view=view
                        )
                    except Exception:
                        pass

                # Log l'interaction bloquée
                if log_cog := self.bot.get_cog("LoggingSystem"):
                    try:
                        await log_cog.log_critical(
                            title="🚫 Commande Préfixe Bloquée (Suspension Globale)",
                            description=(
                                f"**Utilisateur:** {message.author.mention} (`{message.author.id}`)\n"
                                f"**Commande:** {message.content[:100]}\n"
                                f"**Serveur:** {message.guild.name if message.guild else 'DM'}\n"
                                f"**Suspendu:** {'serveur' if guild_suspended else 'utilisateur'}\n"
                                f"**Action:** ✋ Commande par préfixe bloquée AVANT traitement"
                            ),
                            ping_dev=False
                        )
                    except Exception:
                        pass

                # NE PAS traiter la commande
                return

            # Si pas suspendu, traite normalement
            return await original_process_commands(message)

        bot.process_commands = suspension_aware_process_commands

    async def is_suspended(self, user_id: int) -> bool:
        """Whether this user holds an active global suspension."""
        return await global_sanctions.is_suspended(self.bot, user_id=user_id)

    @commands.command(name="clearcache", aliases=["cc"])
    async def clear_sanction_cache(self, ctx):
        """Vide le cache des sanctions globales (commande dev)"""
        if not self.bot.is_developer(ctx.author.id):
            return

        global_sanctions.invalidate(self.bot)
        await ctx.send(f"{DONE} Cache des sanctions globales vidé")

    @commands.command(name="testbl")
    async def test_suspension(self, ctx):
        """Teste le message de suspension (commande dev)"""
        if not self.bot.is_developer(ctx.author.id):
            return

        locale = str(ctx.guild.preferred_locale) if ctx.guild and ctx.guild.preferred_locale else 'en-US'
        view = create_suspension_message(locale)

        await ctx.send(
            "**[TEST MODE]** Voici ce que verrait un utilisateur suspendu:",
            view=view
        )


async def setup(bot):
    await bot.add_cog(BlacklistCheck(bot))
