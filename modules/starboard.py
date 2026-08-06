"""
Module Starboard - Système de tableau d'honneur pour messages populaires
"""

import discord
from typing import Dict, Any, Optional
import logging

from modules.module_manager import ModuleBase
from utils.emojis import (
    STAR, is_standard_discord_emoji,
    get_user_verification_badge, format_verification_badge,
)
from utils.i18n import t

logger = logging.getLogger('moddy.modules.starboard')


class StarboardModule(ModuleBase):
    """
    Module de starboard (tableau d'honneur)
    Forward automatiquement les messages qui reçoivent un nombre X de réactions
    (un émoji standard Discord, configurable) dans un salon dédié, avec un
    compteur de réactions et un lien vers le message d'origine.
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
        self.reaction_count: int = 5  # Number of reactions required
        self.emoji: str = "⭐"  # Standard Discord unicode emoji that triggers the starboard

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

        except Exception as e:
            return False, f"Erreur de validation du salon : {str(e)}"

        # Validate reaction count
        reaction_count = config_data.get('reaction_count', 5)
        if not isinstance(reaction_count, int) or reaction_count < 1 or reaction_count > 100:
            return False, "Le nombre de réactions doit être entre 1 et 100"

        # Validate the reaction emoji: only standard Discord emojis are accepted,
        # never a custom/guild emoji.
        emoji = config_data.get('emoji', "⭐")
        if not isinstance(emoji, str) or not emoji:
            return False, "Un émoji de réaction est requis"

        partial_emoji = discord.PartialEmoji.from_str(emoji)
        if partial_emoji.is_custom_emoji():
            return False, "Seuls les émojis standards de Discord sont autorisés (pas d'émoji personnalisé)"

        if not is_standard_discord_emoji(emoji):
            return False, "Émoji invalide, veuillez choisir un émoji standard de Discord"

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

        # Only standard Discord emojis can trigger the starboard, and only the
        # one configured for this server.
        if payload.emoji.is_custom_emoji() or str(payload.emoji) != self.emoji:
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

            # Count reactions matching the configured emoji
            star_count = self._count_reactions(message)

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

        if payload.emoji.is_custom_emoji() or str(payload.emoji) != self.emoji:
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

            star_count = self._count_reactions(message)

            # Update or remove starboard entry
            if star_count >= self.reaction_count:
                await self._update_starboard(message, star_count)
            else:
                # Remove from starboard if below threshold
                await self._remove_starboard(message)

        except Exception as e:
            logger.error(f"Error updating starboard on reaction remove: {e}", exc_info=True)

    def _count_reactions(self, message: discord.Message) -> int:
        """Count how many times the configured (standard) emoji was used on this message"""
        for reaction in message.reactions:
            if not reaction.is_custom_emoji() and str(reaction.emoji) == self.emoji:
                return reaction.count
        return 0

    async def _get_locale(self) -> str:
        """Locale used for the starboard message's static UI strings (title, jump button)"""
        try:
            guild = self.bot.get_guild(self.guild_id)
            return str(guild.preferred_locale) if guild and guild.preferred_locale else 'en-US'
        except Exception:
            return 'en-US'

    async def _get_author_badge(self, author: discord.abc.User) -> str:
        """Verification badge (hyperlinked) for the original message's author"""
        try:
            user_db_data = await self.bot.db.get_user(author.id)
            moddy_attributes = user_db_data.get('attributes', {}) if user_db_data else {}
        except Exception:
            moddy_attributes = {}

        public_flags_value = author.public_flags.value if hasattr(author, 'public_flags') else 0
        badge_emoji, _org_names, _tier = get_user_verification_badge(
            {'public_flags': public_flags_value}, moddy_attributes
        )
        return format_verification_badge(badge_emoji)

    async def _build_starboard_view(self, message: discord.Message, star_count: int) -> discord.ui.LayoutView:
        """Build the Components V2 card sent alongside the forwarded message"""
        locale = await self._get_locale()
        badge = await self._get_author_badge(message.author)

        view = discord.ui.LayoutView(timeout=None)

        view.add_item(discord.ui.TextDisplay(
            f"### {STAR} {t('modules.starboard.message.title', locale=locale)}\n"
            f"@**{message.author.display_name}**{badge} :"
        ))

        row = discord.ui.ActionRow()

        count_button = discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label=str(star_count),
            emoji=self.emoji,
            disabled=True,
            custom_id=f"moddy:starboard:count:{message.id}"
        )
        row.add_item(count_button)

        jump_button = discord.ui.Button(
            style=discord.ButtonStyle.link,
            label=t('modules.starboard.message.jump_button', locale=locale),
            url=message.jump_url
        )
        row.add_item(jump_button)

        view.add_item(row)
        return view

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

        # Check if we already have a starboard message for this
        if message.id in self.starboard_messages:
            # Update existing message (only the reaction counter changes)
            try:
                starboard_msg_id = self.starboard_messages[message.id]
                starboard_msg = await starboard_channel.fetch_message(starboard_msg_id)
                view = await self._build_starboard_view(message, star_count)
                await starboard_msg.edit(view=view)
                logger.info(f"Updated starboard message for {message.id} (stars: {star_count})")
            except discord.NotFound:
                # Message was deleted, create a new one
                del self.starboard_messages[message.id]
                await self._create_starboard_message(starboard_channel, message, star_count)
        else:
            # Create new starboard message
            await self._create_starboard_message(starboard_channel, message, star_count)

    async def _create_starboard_message(self, channel: discord.TextChannel,
                                         original_message: discord.Message, star_count: int):
        """Forward the original message to the starboard channel with the reaction card"""
        try:
            view = await self._build_starboard_view(original_message, star_count)

            reference = discord.MessageReference(
                message_id=original_message.id,
                channel_id=original_message.channel.id,
                guild_id=self.guild_id,
                fail_if_not_exists=False,
                type=discord.MessageReferenceType.forward,
            )

            starboard_msg = await channel.send(view=view, reference=reference)

            # Track it for future updates
            self.starboard_messages[original_message.id] = starboard_msg.id

            logger.info(f"Created starboard message for {original_message.id}")
        except discord.Forbidden:
            logger.warning(f"Missing permissions to send starboard message in guild {self.guild_id}")
        except discord.HTTPException as e:
            logger.error(f"Error forwarding message {original_message.id} to starboard: {e}")
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
