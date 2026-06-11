"""
Module Events Handler
Gère tous les événements Discord pour les modules de serveur
"""

import discord
from discord.ext import commands
import logging

logger = logging.getLogger('moddy.cogs.module_events')


class ModuleEvents(commands.Cog):
    """
    Cog qui écoute les événements Discord et les transmet aux modules concernés
    """

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """
        Événement déclenché quand un membre rejoint un serveur
        Transmet l'événement aux modules concernés (Welcome, Auto Restore Roles, etc.)
        """
        if not self.bot.module_manager:
            return

        try:
            # Récupère l'instance du module Welcome pour ce serveur
            welcome_module = await self.bot.module_manager.get_module_instance(
                member.guild.id,
                'welcome'
            )

            # Si le module est actif, appelle sa méthode
            if welcome_module and welcome_module.enabled:
                await welcome_module.on_member_join(member)

        except Exception as e:
            logger.error(f"Error in on_member_join for guild {member.guild.id}: {e}", exc_info=True)

        try:
            # Récupère l'instance du module Auto Restore Roles pour ce serveur
            auto_restore_module = await self.bot.module_manager.get_module_instance(
                member.guild.id,
                'auto_restore_roles'
            )

            # Si le module est actif, appelle sa méthode
            if auto_restore_module and auto_restore_module.enabled:
                await auto_restore_module.on_member_join(member)

        except Exception as e:
            logger.error(f"Error in on_member_join (auto_restore_roles) for guild {member.guild.id}: {e}", exc_info=True)

        try:
            # Récupère l'instance du module Auto Role pour ce serveur
            auto_role_module = await self.bot.module_manager.get_module_instance(
                member.guild.id,
                'auto_role'
            )

            # Si le module est actif, appelle sa méthode
            if auto_role_module and auto_role_module.enabled:
                await auto_role_module.on_member_join(member)

        except Exception as e:
            logger.error(f"Error in on_member_join (auto_role) for guild {member.guild.id}: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """
        Événement déclenché quand un membre quitte un serveur
        Transmet l'événement aux modules concernés (Auto Restore Roles, etc.)
        """
        if not self.bot.module_manager:
            return

        try:
            # Récupère l'instance du module Auto Restore Roles pour ce serveur
            auto_restore_module = await self.bot.module_manager.get_module_instance(
                member.guild.id,
                'auto_restore_roles'
            )

            # Si le module est actif, appelle sa méthode
            if auto_restore_module and auto_restore_module.enabled:
                await auto_restore_module.on_member_remove(member)

        except Exception as e:
            logger.error(f"Error in on_member_remove (auto_restore_roles) for guild {member.guild.id}: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Événement déclenché pour chaque message
        Peut être utilisé pour les modules de modération, inter-serveur, etc.
        """
        # Ignore les messages du bot
        if message.author.bot:
            return

        # Ignore les DMs
        if not message.guild:
            return

        if not self.bot.module_manager:
            return

        try:
            # Récupère l'instance du module Inter-Server pour ce serveur
            interserver_module = await self.bot.module_manager.get_module_instance(
                message.guild.id,
                'interserver'
            )

            # Si le module est actif, appelle sa méthode
            if interserver_module and interserver_module.enabled:
                await interserver_module.on_message(message)

        except Exception as e:
            logger.error(f"Error in on_message for guild {message.guild.id}: {e}", exc_info=True)

        try:
            # Adaptive Slowmode: record the message in the rolling window
            slowmode_module = await self.bot.module_manager.get_module_instance(
                message.guild.id,
                'adaptive_slowmode'
            )

            if slowmode_module and slowmode_module.enabled:
                await slowmode_module.on_message(message)

        except Exception as e:
            logger.error(f"Error in on_message (adaptive_slowmode) for guild {message.guild.id}: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        """
        Événement déclenché quand un message est supprimé
        Supprime tous les messages relayés si c'est un message inter-serveur
        """
        # Ignore les messages des bots
        if message.author.bot:
            return

        # Ignore les DMs
        if not message.guild:
            return

        if not self.bot.module_manager or not self.bot.db:
            return

        try:
            # Vérifie si c'est un message inter-serveur
            interserver_msg = await self.bot.db.get_interserver_message_by_original(message.id)
            if not interserver_msg:
                return

            # Supprime tous les messages relayés
            relayed_messages = interserver_msg.get('relayed_messages', [])
            for relayed in relayed_messages:
                try:
                    guild = self.bot.get_guild(relayed['guild_id'])
                    if not guild:
                        continue

                    channel = guild.get_channel(relayed['channel_id'])
                    if not channel:
                        continue

                    # Supprime le message
                    msg = await channel.fetch_message(relayed['message_id'])
                    await msg.delete()
                except discord.NotFound:
                    # Message déjà supprimé
                    pass
                except Exception as e:
                    logger.error(f"Error deleting relayed message {relayed['message_id']}: {e}")

            # Marque le message comme supprimé en DB
            await self.bot.db.delete_interserver_message(interserver_msg['moddy_id'])
            logger.info(f"Deleted inter-server message {interserver_msg['moddy_id']} and all relayed copies")

        except Exception as e:
            logger.error(f"Error in on_message_delete for inter-server: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """
        Événement déclenché quand une réaction est ajoutée à un message
        Transmet l'événement au module Starboard si activé
        """
        # Ignore les réactions du bot
        if payload.user_id == self.bot.user.id:
            return

        # Ignore les DMs
        if not payload.guild_id:
            return

        if not self.bot.module_manager:
            return

        try:
            # Récupère l'instance du module Starboard pour ce serveur
            starboard_module = await self.bot.module_manager.get_module_instance(
                payload.guild_id,
                'starboard'
            )

            # Si le module est actif, appelle sa méthode
            if starboard_module and starboard_module.enabled:
                await starboard_module.on_reaction_add(payload)

        except Exception as e:
            logger.error(f"Error in on_raw_reaction_add for guild {payload.guild_id}: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        """
        Événement déclenché quand une réaction est retirée d'un message
        Transmet l'événement au module Starboard pour mise à jour
        """
        # Ignore les réactions du bot
        if payload.user_id == self.bot.user.id:
            return

        # Ignore les DMs
        if not payload.guild_id:
            return

        if not self.bot.module_manager:
            return

        try:
            # Récupère l'instance du module Starboard pour ce serveur
            starboard_module = await self.bot.module_manager.get_module_instance(
                payload.guild_id,
                'starboard'
            )

            # Si le module est actif, appelle sa méthode
            if starboard_module and starboard_module.enabled:
                await starboard_module.on_reaction_remove(payload)

        except Exception as e:
            logger.error(f"Error in on_raw_reaction_remove for guild {payload.guild_id}: {e}", exc_info=True)


async def setup(bot):
    """Charge le cog"""
    await bot.add_cog(ModuleEvents(bot))
