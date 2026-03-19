"""
Module Starboard - Système de tableau d'honneur pour messages populaires
"""

import discord
from typing import Dict, Any, Optional
import logging

from modules.module_manager import ModuleBase
from utils.emojis import STAR, MESSAGE, TEXT

logger = logging.getLogger('moddy.modules.starboard')


class StarboardModule(ModuleBase):
    """
    Module de starboard (tableau d'honneur)
    Envoie automatiquement les messages qui reçoivent un nombre X de réactions étoiles
    dans un salon dédié avec le contenu, l'auteur, le nombre de réactions et le lien
    """

    MODULE_ID = "starboard"
    MODULE_NAME = "Starboard"
    MODULE_DESCRIPTION = "Tableau d'honneur des messages populaires"
    MODULE_EMOJI = STAR

    def __init__(self, bot, guild_id: int):
        super().__init__(bot, guild_id)

        # Channel configuration
        self.channel_id: Optional[int] = None

        # Starboard configuration
        self.reaction_count: int = 5  # Number of star reactions required
        self.emoji: str = "⭐"  # Star emoji

        # Track sent starboard messages to update them in real-time
        # Format: {original_message_id: starboard_message_id}
        self.starboard_messages: Dict[int, int] = {}

    async def load_config(self, config_data: Dict[str, Any]) -> bool:
        """Load configuration from DB"""
        try:
            self.config = config_data

            # Channel configuration
            self.channel_id = config_data.get('channel_id')

            # Starboard configuration
            self.reaction_count = config_data.get('reaction_count', 5)
            self.emoji = config_data.get('emoji', "⭐")

            # Module is enabled if channel is configured
            self.enabled = self.channel_id is not None

            return True
        except Exception as e:
            logger.error(f"Error loading starboard config: {e}")
            return False

    async def validate_config(self, config_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate configuration"""
        # Channel ID is required
        if not config_data.get('channel_id'):
            return False, "Un salon est requis pour le starboard"

        # Verify channel exists and bot has permissions
        try:
            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                return False, "Serveur introuvable"

            channel = guild.get_channel(config_data['channel_id'])
            if not channel:
                return False, "Salon introuvable"

            if not isinstance(channel, discord.TextChannel):
                return False, "Le salon doit être un salon textuel"

            # Check permissions
            perms = channel.permissions_for(guild.me)
            if not perms.send_messages:
                return False, f"Je n'ai pas la permission d'envoyer des messages dans {channel.mention}"

            if not perms.embed_links:
                return False, f"Je n'ai pas la permission d'envoyer des embeds dans {channel.mention}"

        except Exception as e:
            return False, f"Erreur de validation du salon : {str(e)}"

        # Validate reaction count
        reaction_count = config_data.get('reaction_count', 5)
        if not isinstance(reaction_count, int) or reaction_count < 1 or reaction_count > 100:
            return False, "Le nombre de réactions doit être entre 1 et 100"

        return True, None

    def get_default_config(self) -> Dict[str, Any]:
        """Return default configuration"""
        return {
            'channel_id': None,
            'reaction_count': 5,
            'emoji': "⭐"
        }

    async def on_reaction_add(self, payload: discord.RawReactionActionEvent):
        """
        Called when a reaction is added to a message
        Checks if the message should be added to starboard
        """
        if not self.enabled or not self.channel_id:
            return

        # Only track star emoji
        if str(payload.emoji) != self.emoji:
            return

        try:
            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                return

            # Get the channel where the reaction was added
            channel = guild.get_channel(payload.channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                return

            # Don't track reactions in the starboard channel itself
            if payload.channel_id == self.channel_id:
                return

            # Get the message
            try:
                message = await channel.fetch_message(payload.message_id)
            except discord.NotFound:
                logger.warning(f"Message {payload.message_id} not found")
                return

            # Count star reactions
            star_count = 0
            for reaction in message.reactions:
                if str(reaction.emoji) == self.emoji:
                    star_count = reaction.count
                    break

            # Check if we should send/update starboard entry
            if star_count >= self.reaction_count:
                await self._update_starboard(message, star_count)

        except discord.Forbidden:
            logger.warning(f"Missing permissions for starboard in guild {self.guild_id}")
        except Exception as e:
            logger.error(f"Error processing starboard reaction: {e}", exc_info=True)

    async def on_reaction_remove(self, payload: discord.RawReactionActionEvent):
        """
        Called when a reaction is removed from a message
        Updates the starboard message with the new count
        """
        if not self.enabled or not self.channel_id:
            return

        # Only track star emoji
        if str(payload.emoji) != self.emoji:
            return

        # Check if this message has a starboard entry
        if payload.message_id not in self.starboard_messages:
            return

        try:
            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                return

            # Get the channel where the reaction was removed
            channel = guild.get_channel(payload.channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                return

            # Get the message
            try:
                message = await channel.fetch_message(payload.message_id)
            except discord.NotFound:
                return

            # Count star reactions
            star_count = 0
            for reaction in message.reactions:
                if str(reaction.emoji) == self.emoji:
                    star_count = reaction.count
                    break

            # Update or remove starboard entry
            if star_count >= self.reaction_count:
                await self._update_starboard(message, star_count)
            else:
                # Remove from starboard if below threshold
                await self._remove_starboard(message)

        except Exception as e:
            logger.error(f"Error updating starboard on reaction remove: {e}", exc_info=True)

    async def _update_starboard(self, message: discord.Message, star_count: int):
        """
        Update or create a starboard entry for a message
        """
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            return

        starboard_channel = guild.get_channel(self.channel_id)
        if not starboard_channel or not isinstance(starboard_channel, discord.TextChannel):
            logger.warning(f"Starboard channel {self.channel_id} not found or not a text channel")
            return

        # Create embed
        embed = await self._create_starboard_embed(message, star_count)

        # Check if we already have a starboard message for this
        if message.id in self.starboard_messages:
            # Update existing message
            try:
                starboard_msg_id = self.starboard_messages[message.id]
                starboard_msg = await starboard_channel.fetch_message(starboard_msg_id)
                await starboard_msg.edit(embed=embed)
                logger.info(f"Updated starboard message for {message.id} (stars: {star_count})")
            except discord.NotFound:
                # Message was deleted, create a new one
                del self.starboard_messages[message.id]
                await self._create_starboard_message(starboard_channel, message, embed)
        else:
            # Create new starboard message
            await self._create_starboard_message(starboard_channel, message, embed)

    async def _create_starboard_message(self, channel: discord.TextChannel,
                                       original_message: discord.Message, embed: discord.Embed):
        """Create a new starboard message"""
        try:
            # Send the starboard message
            starboard_msg = await channel.send(embed=embed)

            # Track it for future updates
            self.starboard_messages[original_message.id] = starboard_msg.id

            logger.info(f"Created starboard message for {original_message.id}")
        except discord.Forbidden:
            logger.warning(f"Missing permissions to send starboard message in guild {self.guild_id}")
        except Exception as e:
            logger.error(f"Error creating starboard message: {e}", exc_info=True)

    async def _remove_starboard(self, message: discord.Message):
        """Remove a message from starboard"""
        if message.id not in self.starboard_messages:
            return

        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            return

        starboard_channel = guild.get_channel(self.channel_id)
        if not starboard_channel or not isinstance(starboard_channel, discord.TextChannel):
            return

        try:
            starboard_msg_id = self.starboard_messages[message.id]
            starboard_msg = await starboard_channel.fetch_message(starboard_msg_id)
            await starboard_msg.delete()
            del self.starboard_messages[message.id]
            logger.info(f"Removed starboard message for {message.id}")
        except discord.NotFound:
            # Already deleted
            del self.starboard_messages[message.id]
        except Exception as e:
            logger.error(f"Error removing starboard message: {e}", exc_info=True)

    async def _create_starboard_embed(self, message: discord.Message, star_count: int) -> discord.Embed:
        """Create an embed for a starboard entry"""

        # Create base embed with message content
        embed = discord.Embed(
            description=message.content if message.content else "*Pas de contenu texte*",
            color=0xFFAC33,  # Gold color for starboard
            timestamp=message.created_at
        )

        # Set author (message author)
        embed.set_author(
            name=message.author.display_name,
            icon_url=message.author.display_avatar.url
        )

        # Add star count and jump link
        embed.add_field(
            name=f"{self.emoji} Réactions",
            value=f"**{star_count}** {self.emoji}",
            inline=True
        )

        embed.add_field(
            name=f"{MESSAGE} Message",
            value=f"[Aller au message]({message.jump_url})",
            inline=True
        )

        embed.add_field(
            name=f"{TEXT} Salon",
            value=message.channel.mention,
            inline=True
        )

        # Add image if the message has one
        if message.attachments:
            # Get the first image attachment
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith('image/'):
                    embed.set_image(url=attachment.url)
                    break

        # Add embed image if the message has embeds with images
        if not embed.image and message.embeds:
            for msg_embed in message.embeds:
                if msg_embed.image:
                    embed.set_image(url=msg_embed.image.url)
                    break
                elif msg_embed.thumbnail:
                    embed.set_thumbnail(url=msg_embed.thumbnail.url)
                    break

        # Add footer
        embed.set_footer(
            text=f"ID: {message.id}"
        )

        return embed
