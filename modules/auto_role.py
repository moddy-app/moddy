"""
Module Auto Role - Attribution automatique de rôles
Attribue automatiquement des rôles aux nouveaux membres et aux bots qui rejoignent le serveur
"""

import discord
from typing import Dict, Any, Optional, List
import logging

from modules.module_manager import ModuleBase
from utils.emojis import MANAGE_USER

logger = logging.getLogger('moddy.modules.auto_role')


class AutoRoleModule(ModuleBase):
    """
    Module d'attribution automatique de rôles
    Attribue des rôles automatiquement aux membres et bots qui rejoignent le serveur
    """

    MODULE_ID = "auto_role"
    MODULE_NAME = "Auto Role"
    MODULE_DESCRIPTION = "Attribue automatiquement des rôles aux nouveaux membres"
    MODULE_EMOJI = MANAGE_USER

    def __init__(self, bot, guild_id: int):
        super().__init__(bot, guild_id)

        # Configuration
        self.member_roles: List[int] = []  # Rôles pour les utilisateurs normaux
        self.bot_roles: List[int] = []     # Rôles pour les bots

    async def load_config(self, config_data: Dict[str, Any]) -> bool:
        """Charge la configuration depuis la DB"""
        try:
            self.config = config_data

            # Charge les rôles
            self.member_roles = config_data.get('member_roles', [])
            self.bot_roles = config_data.get('bot_roles', [])

            # Le module est activé si au moins un rôle est configuré
            self.enabled = len(self.member_roles) > 0 or len(self.bot_roles) > 0

            return True
        except Exception as e:
            logger.error(f"Error loading auto_role config: {e}")
            return False

    async def validate_config(self, config_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Valide la configuration"""
        member_roles = config_data.get('member_roles', [])
        bot_roles = config_data.get('bot_roles', [])

        # Au moins un rôle doit être configuré
        if not member_roles and not bot_roles:
            return False, "Vous devez configurer au moins un rôle (pour les membres ou pour les bots)"

        # Vérifie que les rôles existent
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            return False, "Serveur introuvable"

        # Listes pour collecter tous les problèmes
        roles_too_high = []  # Rôles au-dessus de la hiérarchie du bot
        everyone_roles = []  # Rôles @everyone
        managed_roles = []   # Rôles gérés automatiquement
        missing_roles = []   # Rôles introuvables

        # Vérifie les rôles membres
        for role_id in member_roles:
            role = guild.get_role(role_id)
            if not role:
                missing_roles.append(f"<@&{role_id}>")
                continue

            # Vérifie que le rôle n'est pas @everyone
            if role.id == guild.id:
                everyone_roles.append(role.mention)
                continue

            # Vérifie que le rôle n'est pas managé (bot, intégration, etc.)
            if role.managed:
                managed_roles.append(role.mention)
                continue

            # Vérifie que le bot peut attribuer ce rôle
            if role >= guild.me.top_role:
                roles_too_high.append(role.mention)

        # Vérifie les rôles bots
        for role_id in bot_roles:
            role = guild.get_role(role_id)
            if not role:
                missing_roles.append(f"<@&{role_id}>")
                continue

            # Vérifie que le rôle n'est pas @everyone
            if role.id == guild.id:
                if role.mention not in everyone_roles:
                    everyone_roles.append(role.mention)
                continue

            # Vérifie que le rôle n'est pas managé
            if role.managed:
                if role.mention not in managed_roles:
                    managed_roles.append(role.mention)
                continue

            # Vérifie que le bot peut attribuer ce rôle
            if role >= guild.me.top_role:
                if role.mention not in roles_too_high:
                    roles_too_high.append(role.mention)

        # Retourne le premier problème trouvé avec tous les rôles concernés
        if missing_roles:
            roles_list = ", ".join(missing_roles)
            return False, f"Rôle(s) introuvable(s) : {roles_list}"

        if everyone_roles:
            return False, "Vous ne pouvez pas utiliser @everyone comme rôle automatique"

        if managed_roles:
            roles_list = ", ".join(managed_roles)
            if len(managed_roles) == 1:
                return False, f"Le rôle {roles_list} est géré automatiquement et ne peut pas être attribué manuellement"
            else:
                return False, f"Les rôles {roles_list} sont gérés automatiquement et ne peuvent pas être attribués manuellement"

        if roles_too_high:
            roles_list = ", ".join(roles_too_high)
            if len(roles_too_high) == 1:
                return False, f"Je ne peux pas attribuer le rôle {roles_list} car il est au-dessus de mon rôle le plus élevé"
            else:
                return False, f"Je ne peux pas attribuer les rôles {roles_list} car ils sont au-dessus de mon rôle le plus élevé"

        # Vérifie que le bot a la permission de gérer les rôles
        if not guild.me.guild_permissions.manage_roles:
            return False, "Je n'ai pas la permission de gérer les rôles sur ce serveur"

        return True, None

    def get_default_config(self) -> Dict[str, Any]:
        """Retourne la configuration par défaut"""
        return {
            'member_roles': [],
            'bot_roles': []
        }

    async def on_member_join(self, member: discord.Member):
        """
        Appelé quand un membre rejoint le serveur
        Attribue les rôles automatiques selon qu'il s'agit d'un bot ou d'un utilisateur
        """
        if not self.enabled:
            return

        try:
            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                return

            # Détermine quels rôles attribuer selon le type de membre
            roles_to_add = []

            if member.bot:
                # C'est un bot, on attribue les rôles bots
                if self.bot_roles:
                    for role_id in self.bot_roles:
                        role = guild.get_role(role_id)
                        if role and role < guild.me.top_role:
                            roles_to_add.append(role)
            else:
                # C'est un utilisateur normal, on attribue les rôles membres
                if self.member_roles:
                    for role_id in self.member_roles:
                        role = guild.get_role(role_id)
                        if role and role < guild.me.top_role:
                            roles_to_add.append(role)

            # Attribue les rôles si il y en a
            if roles_to_add:
                await member.add_roles(*roles_to_add, reason="Auto Role")
                role_names = ", ".join([role.name for role in roles_to_add])
                logger.info(
                    f"✅ Added {len(roles_to_add)} auto role(s) to {member.name} ({'bot' if member.bot else 'member'}) "
                    f"in guild {self.guild_id}: {role_names}"
                )

        except discord.Forbidden:
            logger.warning(
                f"Missing permissions to add auto roles to {member.name} in guild {self.guild_id}"
            )
        except Exception as e:
            logger.error(
                f"Error adding auto roles to {member.name} in guild {self.guild_id}: {e}",
                exc_info=True
            )
