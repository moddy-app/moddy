"""
Module Auto Restore Roles - Restauration automatique des rôles
Sauvegarde les rôles des utilisateurs qui quittent et les restaure automatiquement quand ils reviennent
"""

import discord
from typing import Dict, Any, Optional, List
import logging
from datetime import datetime

from modules.module_manager import ModuleBase
from utils.emojis import HISTORY, DONE

logger = logging.getLogger('moddy.modules.auto_restore_roles')


class AutoRestoreRolesModule(ModuleBase):
    """
    Module de restauration automatique des rôles
    Sauvegarde les rôles quand un utilisateur quitte et les restaure quand il revient
    Les rôles sont stockés dans la table saved_roles de PostgreSQL
    """

    MODULE_ID = "auto_restore_roles"
    MODULE_NAME = "Auto Restore Roles"
    MODULE_DESCRIPTION = "Restaure automatiquement les rôles des utilisateurs qui reviennent"
    MODULE_EMOJI = HISTORY

    # Modes de sauvegarde
    MODE_ALL = "all"  # Tous les rôles
    MODE_EXCEPT = "except"  # Tous les rôles sauf certains
    MODE_ONLY = "only"  # Uniquement les rôles sélectionnés

    def __init__(self, bot, guild_id: int):
        super().__init__(bot, guild_id)

        # Configuration
        self.mode: Optional[str] = None
        self.excluded_roles: List[int] = []  # Pour mode EXCEPT
        self.included_roles: List[int] = []  # Pour mode ONLY
        self.log_channel_id: Optional[int] = None

    async def load_config(self, config_data: Dict[str, Any]) -> bool:
        """Charge la configuration depuis la DB"""
        try:
            self.config = config_data

            # Configuration du mode
            self.mode = config_data.get('mode')
            self.excluded_roles = config_data.get('excluded_roles', [])
            self.included_roles = config_data.get('included_roles', [])
            self.log_channel_id = config_data.get('log_channel_id')

            # Module is enabled only if mode is configured
            self.enabled = self.mode is not None

            return True
        except Exception as e:
            logger.error(f"Error loading auto_restore_roles config: {e}")
            return False

    async def validate_config(self, config_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Valide la configuration"""
        # Valide le mode
        mode = config_data.get('mode')
        if mode is None:
            return False, "Vous devez sélectionner un mode de sauvegarde"

        if mode not in [self.MODE_ALL, self.MODE_EXCEPT, self.MODE_ONLY]:
            return False, "Mode de sauvegarde invalide"

        # Valide les rôles exclus (mode EXCEPT)
        if mode == self.MODE_EXCEPT:
            excluded_roles = config_data.get('excluded_roles', [])
            if not excluded_roles:
                return False, "Vous devez sélectionner au moins un rôle à exclure pour ce mode"

        # Valide les rôles inclus (mode ONLY)
        if mode == self.MODE_ONLY:
            included_roles = config_data.get('included_roles', [])
            if not included_roles:
                return False, "Vous devez sélectionner au moins un rôle à inclure pour ce mode"

        # Valide le salon de logs si configuré
        if config_data.get('log_channel_id'):
            try:
                guild = self.bot.get_guild(self.guild_id)
                if not guild:
                    return False, "Serveur introuvable"

                channel = guild.get_channel(config_data['log_channel_id'])
                if not channel:
                    return False, "Salon de logs introuvable"

                if not isinstance(channel, discord.TextChannel):
                    return False, "Le salon de logs doit être un salon textuel"

                # Vérifie les permissions
                perms = channel.permissions_for(guild.me)
                if not perms.send_messages:
                    return False, f"Je n'ai pas la permission d'envoyer des messages dans {channel.mention}"
                if not perms.embed_links:
                    return False, f"Je n'ai pas la permission d'envoyer des embeds dans {channel.mention}"

            except Exception as e:
                return False, f"Erreur de validation du salon : {str(e)}"

        return True, None

    def get_default_config(self) -> Dict[str, Any]:
        """Retourne la configuration par défaut"""
        return {
            'mode': None,
            'excluded_roles': [],
            'included_roles': [],
            'log_channel_id': None
        }

    async def on_member_remove(self, member: discord.Member):
        """
        Appelé quand un membre quitte le serveur
        Sauvegarde ses rôles selon le mode configuré
        """
        if not self.enabled:
            return

        if not self.bot.db:
            logger.error("Database not available, cannot save roles")
            return

        try:
            # Récupère les rôles à sauvegarder
            roles_to_save = self._get_roles_to_save(member)

            if not roles_to_save:
                logger.info(f"No roles to save for {member.name} (guild {self.guild_id})")
                return

            # Sauvegarde les rôles dans la DB
            role_ids = [role.id for role in roles_to_save]
            success = await self.bot.db.save_user_roles(
                self.guild_id,
                member.id,
                role_ids,
                str(member)
            )

            if success:
                logger.info(f"✅ Saved {len(roles_to_save)} roles for {member.name} (guild {self.guild_id})")

                # Envoie un log si configuré
                if self.log_channel_id:
                    await self._send_log_saved(member, roles_to_save)

        except Exception as e:
            logger.error(f"Error saving roles for {member.name}: {e}", exc_info=True)

    async def on_member_join(self, member: discord.Member):
        """
        Appelé quand un membre rejoint le serveur
        Restaure ses rôles s'ils ont été sauvegardés
        """
        if not self.enabled:
            return

        if not self.bot.db:
            logger.error("Database not available, cannot restore roles")
            return

        try:
            # Récupère les rôles sauvegardés depuis la DB
            saved_data = await self.bot.db.get_saved_roles(self.guild_id, member.id)

            if not saved_data:
                return

            role_ids = saved_data['roles']

            # Récupère les rôles qui existent encore
            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                return

            roles_to_restore = []
            for role_id in role_ids:
                role = guild.get_role(role_id)
                if role and role < guild.me.top_role:  # Vérifie que le bot peut attribuer ce rôle
                    roles_to_restore.append(role)

            if not roles_to_restore:
                logger.info(f"No roles to restore for {member.name} (guild {self.guild_id})")
                # Supprime les données sauvegardées
                await self.bot.db.delete_saved_roles(self.guild_id, member.id)
                return

            # Restaure les rôles
            await member.add_roles(*roles_to_restore, reason="Auto Restore Roles")

            logger.info(f"✅ Restored {len(roles_to_restore)} roles for {member.name} (guild {self.guild_id})")

            # Supprime les données sauvegardées
            await self.bot.db.delete_saved_roles(self.guild_id, member.id)

            # Envoie un log si configuré
            if self.log_channel_id:
                await self._send_log_restored(member, roles_to_restore)

        except discord.Forbidden:
            logger.warning(f"Missing permissions to restore roles for {member.name} (guild {self.guild_id})")
        except Exception as e:
            logger.error(f"Error restoring roles for {member.name}: {e}", exc_info=True)

    def _get_roles_to_save(self, member: discord.Member) -> List[discord.Role]:
        """
        Détermine quels rôles doivent être sauvegardés selon le mode configuré
        """
        # Filtre les rôles (ignore @everyone et les rôles managed)
        roles = [role for role in member.roles if role.id != member.guild.id and not role.managed]

        if self.mode == self.MODE_ALL:
            return roles

        elif self.mode == self.MODE_EXCEPT:
            # Exclut les rôles spécifiés
            return [role for role in roles if role.id not in self.excluded_roles]

        elif self.mode == self.MODE_ONLY:
            # Inclut uniquement les rôles spécifiés
            return [role for role in roles if role.id in self.included_roles]

        return []

    async def _send_log_saved(self, member: discord.Member, roles: List[discord.Role]):
        """Envoie un log quand des rôles sont sauvegardés"""
        try:
            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                return

            channel = guild.get_channel(self.log_channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                return

            embed = discord.Embed(
                title=f"{HISTORY} Rôles sauvegardés",
                description=f"Les rôles de {member.mention} ont été sauvegardés",
                color=0xFFA500,
                timestamp=datetime.utcnow()
            )
            embed.add_field(
                name="Utilisateur",
                value=f"{member.mention} (`{member.id}`)",
                inline=False
            )
            embed.add_field(
                name=f"Rôles sauvegardés ({len(roles)})",
                value=", ".join([role.mention for role in roles]),
                inline=False
            )
            embed.set_thumbnail(url=member.display_avatar.url)

            await channel.send(embed=embed)

        except Exception as e:
            logger.error(f"Error sending saved log: {e}", exc_info=True)

    async def _send_log_restored(self, member: discord.Member, roles: List[discord.Role]):
        """Envoie un log quand des rôles sont restaurés"""
        try:
            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                return

            channel = guild.get_channel(self.log_channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                return

            embed = discord.Embed(
                title=f"{DONE} Rôles restaurés",
                description=f"Les rôles de {member.mention} ont été restaurés automatiquement",
                color=0x00FF00,
                timestamp=datetime.utcnow()
            )
            embed.add_field(
                name="Utilisateur",
                value=f"{member.mention} (`{member.id}`)",
                inline=False
            )
            embed.add_field(
                name=f"Rôles restaurés ({len(roles)})",
                value=", ".join([role.mention for role in roles]),
                inline=False
            )
            embed.set_thumbnail(url=member.display_avatar.url)

            await channel.send(embed=embed)

        except Exception as e:
            logger.error(f"Error sending restored log: {e}", exc_info=True)

    async def clear_saved_roles(self, user_id: int) -> bool:
        """
        Supprime les rôles sauvegardés d'un utilisateur spécifique
        Utilisé par la commande slash

        Args:
            user_id: ID de l'utilisateur

        Returns:
            True si supprimé, False si non trouvé
        """
        if not self.bot.db:
            logger.error("Database not available")
            return False

        success = await self.bot.db.delete_saved_roles(self.guild_id, user_id)
        if success:
            logger.info(f"Cleared saved roles for user {user_id} (guild {self.guild_id})")
        return success

    async def get_saved_roles_count(self) -> int:
        """Retourne le nombre d'utilisateurs avec des rôles sauvegardés"""
        if not self.bot.db:
            return 0

        return await self.bot.db.get_saved_roles_count(self.guild_id)

    async def get_saved_roles_info(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Retourne les informations sur les rôles sauvegardés d'un utilisateur"""
        if not self.bot.db:
            return None

        return await self.bot.db.get_saved_roles(self.guild_id, user_id)

    async def get_all_saved_roles(self) -> List[Dict[str, Any]]:
        """Retourne tous les utilisateurs avec des rôles sauvegardés"""
        if not self.bot.db:
            return []

        return await self.bot.db.get_all_saved_roles_for_guild(self.guild_id)
