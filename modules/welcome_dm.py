"""
Module Welcome DM - Message de bienvenue en message privé
"""

import discord
from typing import Dict, Any, Optional
import logging

from modules.module_manager import ModuleBase
from utils.emojis import WAVING_HAND

logger = logging.getLogger('moddy.modules.welcome_dm')


class WelcomeDmModule(ModuleBase):
    """
    Module de messages de bienvenue en DM
    Envoie un message personnalisé en privé quand un nouveau membre rejoint
    """

    MODULE_ID = "welcome_dm"
    MODULE_NAME = "Welcome DM"
    MODULE_DESCRIPTION = "Message de bienvenue en message privé"
    MODULE_EMOJI = WAVING_HAND

    def __init__(self, bot, guild_id: int):
        super().__init__(bot, guild_id)

        # Message configuration (no mention in DMs)
        self.message_template: str = "Bienvenue sur le serveur {server} !"

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

            # Message configuration
            self.message_template = config_data.get('message_template', "Bienvenue sur le serveur {server} !")

            # Embed configuration
            self.embed_enabled = config_data.get('embed_enabled', False)
            self.embed_title = config_data.get('embed_title', "Bienvenue !")
            self.embed_description = config_data.get('embed_description')
            self.embed_color = config_data.get('embed_color', 0x5865F2)
            self.embed_footer = config_data.get('embed_footer')
            self.embed_image_url = config_data.get('embed_image_url')
            self.embed_thumbnail_enabled = config_data.get('embed_thumbnail_enabled', True)
            self.embed_author_enabled = config_data.get('embed_author_enabled', False)

            # Module is always enabled if configured
            self.enabled = True

            return True
        except Exception as e:
            logger.error(f"Error loading welcome_dm config: {e}")
            return False

    async def validate_config(self, config_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Valide la configuration"""
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
            'message_template': "Bienvenue sur le serveur {server} !",
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
        Envoie le message de bienvenue en DM
        """
        if not self.enabled:
            return

        try:
            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                return

            # Prepare message variables (no mention in DMs)
            message_content = self.message_template.format(
                user=member.name,
                username=member.name,
                server=guild.name,
                member_count=guild.member_count
            )

            # Send with or without embed
            if self.embed_enabled:
                embed = self._create_embed(member, guild, message_content)

                # Send with content if no custom embed description
                if self.embed_description:
                    await member.send(embed=embed)
                else:
                    await member.send(content=message_content, embed=embed)
            else:
                await member.send(message_content)

            logger.info(f"✅ DM welcome sent to {member.name} (guild {self.guild_id})")

        except discord.Forbidden:
            logger.warning(f"Cannot send DM welcome to {member.name} - DMs disabled")
        except Exception as e:
            logger.error(f"Error sending DM welcome: {e}", exc_info=True)

    def _create_embed(self, member: discord.Member, guild: discord.Guild, default_message: str) -> discord.Embed:
        """Create an embed for welcome message"""
        embed_desc = self.embed_description if self.embed_description else default_message
        embed_desc = embed_desc.format(
            user=member.name,
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
                user=member.name,
                username=member.name,
                server=guild.name,
                member_count=guild.member_count
            )
            embed.set_footer(text=footer_text)

        return embed
