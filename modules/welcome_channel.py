"""
Module Welcome Channel - Message de bienvenue dans un salon public
"""

import discord
from typing import Dict, Any, Optional
import logging

from modules.module_manager import ModuleBase
from utils.emojis import WAVING_HAND

logger = logging.getLogger('moddy.modules.welcome_channel')


class WelcomeChannelModule(ModuleBase):
    """
    Module de messages de bienvenue dans un salon public
    Envoie un message personnalisé dans un salon quand un nouveau membre rejoint
    """

    MODULE_ID = "welcome_channel"
    MODULE_NAME = "Welcome Channel"
    MODULE_DESCRIPTION = "Message de bienvenue dans un salon public"
    MODULE_EMOJI = WAVING_HAND

    def __init__(self, bot, guild_id: int):
        super().__init__(bot, guild_id)

        # Channel configuration
        self.channel_id: Optional[int] = None

        # Message configuration
        self.message_template: str = "Bienvenue {user} sur le serveur !"
        self.mention_user: bool = True

        # Embed configuration
        self.embed_enabled: bool = False
        self.embed_title: str = "Bienvenue !"
        self.embed_description: Optional[str] = None
        self.embed_color: int = 0x5865F2
        self.embed_footer: Optional[str] = None
        self.embed_image_url: Optional[str] = None
        self.embed_thumbnail_enabled: bool = True
        self.embed_author_enabled: bool = False

    async def load_config(self, config_data: Dict[str, Any]) -> bool:
        """Charge la configuration depuis la DB"""
        try:
            self.config = config_data

            # Channel configuration
            self.channel_id = config_data.get('channel_id')

            # Message configuration
            self.message_template = config_data.get('message_template', "Bienvenue {user} sur le serveur !")
            self.mention_user = config_data.get('mention_user', True)

            # Embed configuration
            self.embed_enabled = config_data.get('embed_enabled', False)
            self.embed_title = config_data.get('embed_title', "Bienvenue !")
            self.embed_description = config_data.get('embed_description')
            self.embed_color = config_data.get('embed_color', 0x5865F2)
            self.embed_footer = config_data.get('embed_footer')
            self.embed_image_url = config_data.get('embed_image_url')
            self.embed_thumbnail_enabled = config_data.get('embed_thumbnail_enabled', True)
            self.embed_author_enabled = config_data.get('embed_author_enabled', False)

            # Module is enabled if channel is configured
            self.enabled = self.channel_id is not None

            return True
        except Exception as e:
            logger.error(f"Error loading welcome_channel config: {e}")
            return False

    async def validate_config(self, config_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Valide la configuration"""
        # Channel ID is required
        if not config_data.get('channel_id'):
            return False, "Un salon est requis pour le message de bienvenue"

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

            if config_data.get('embed_enabled', False) and not perms.embed_links:
                return False, f"Je n'ai pas la permission d'envoyer des embeds dans {channel.mention}"

        except Exception as e:
            return False, f"Erreur de validation du salon : {str(e)}"

        # Validate message template
        template = config_data.get('message_template', '')
        if not template or len(template.strip()) == 0:
            return False, "Le message de bienvenue ne peut pas être vide"
        if len(template) > 2000:
            return False, "Le message de bienvenue ne peut pas dépasser 2000 caractères"

        # Validate embed settings
        if config_data.get('embed_enabled'):
            if 'embed_title' in config_data and len(config_data['embed_title']) > 256:
                return False, "Le titre de l'embed ne peut pas dépasser 256 caractères"
            if 'embed_description' in config_data and config_data['embed_description']:
                if len(config_data['embed_description']) > 4096:
                    return False, "La description de l'embed ne peut pas dépasser 4096 caractères"
            if 'embed_footer' in config_data and config_data['embed_footer']:
                if len(config_data['embed_footer']) > 2048:
                    return False, "Le footer de l'embed ne peut pas dépasser 2048 caractères"
            if 'embed_image_url' in config_data and config_data['embed_image_url']:
                url = config_data['embed_image_url']
                if not url.startswith(('http://', 'https://')):
                    return False, "L'URL de l'image doit commencer par http:// ou https://"
            if 'embed_color' in config_data:
                color = config_data['embed_color']
                if not isinstance(color, int) or color < 0 or color > 0xFFFFFF:
                    return False, "La couleur de l'embed est invalide"

        return True, None

    def get_default_config(self) -> Dict[str, Any]:
        """Retourne la configuration par défaut"""
        return {
            'channel_id': None,
            'message_template': "Bienvenue {user} sur le serveur !",
            'mention_user': True,
            'embed_enabled': False,
            'embed_title': "Bienvenue !",
            'embed_description': None,
            'embed_color': 0x5865F2,
            'embed_footer': None,
            'embed_image_url': None,
            'embed_thumbnail_enabled': True,
            'embed_author_enabled': False
        }

    async def on_member_join(self, member: discord.Member):
        """
        Appelé quand un membre rejoint le serveur
        Envoie le message de bienvenue dans le salon configuré
        """
        if not self.enabled or not self.channel_id:
            return

        try:
            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                return

            channel = guild.get_channel(self.channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                logger.warning(f"Welcome channel {self.channel_id} not found or not a text channel")
                return

            # Prepare message variables
            user_mention = member.mention if self.mention_user else member.name
            message_content = self.message_template.format(
                user=user_mention,
                username=member.name,
                server=guild.name,
                member_count=guild.member_count
            )

            # Send with or without embed
            if self.embed_enabled:
                embed = self._create_embed(member, guild, message_content)

                # Send with content if no custom embed description
                if self.embed_description:
                    await channel.send(embed=embed)
                else:
                    await channel.send(content=message_content, embed=embed)
            else:
                await channel.send(message_content)

            logger.info(f"✅ Channel welcome sent for {member.name} in channel {self.channel_id} (guild {self.guild_id})")

        except discord.Forbidden:
            logger.warning(f"Missing permissions to send channel welcome in guild {self.guild_id}")
        except Exception as e:
            logger.error(f"Error sending channel welcome: {e}", exc_info=True)

    def _create_embed(self, member: discord.Member, guild: discord.Guild, default_message: str) -> discord.Embed:
        """Create an embed for welcome message"""
        user_ref = member.mention if self.mention_user else member.name
        embed_desc = self.embed_description if self.embed_description else default_message
        embed_desc = embed_desc.format(
            user=user_ref,
            username=member.name,
            server=guild.name,
            member_count=guild.member_count
        )

        embed = discord.Embed(
            title=self.embed_title,
            description=embed_desc,
            color=self.embed_color
        )

        # Add author if enabled
        if self.embed_author_enabled:
            embed.set_author(
                name=member.display_name,
                icon_url=member.display_avatar.url
            )

        # Add thumbnail if enabled
        if self.embed_thumbnail_enabled:
            embed.set_thumbnail(url=member.display_avatar.url)

        # Add image if URL provided
        if self.embed_image_url:
            embed.set_image(url=self.embed_image_url)

        # Add footer if provided
        if self.embed_footer:
            footer_text = self.embed_footer.format(
                user=user_ref,
                username=member.name,
                server=guild.name,
                member_count=guild.member_count
            )
            embed.set_footer(text=footer_text)

        return embed
