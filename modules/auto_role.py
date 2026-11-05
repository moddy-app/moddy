"""
Module Auto Role - Attribution automatique de rôles
Attribue automatiquement des rôles aux nouveaux membres et aux bots qui rejoignent le serveur
"""

import discord
from typing import Dict, Any, Optional, List
import logging

from modules.module_manager import ModuleBase
from utils.emojis import MANAGE_USER
from utils.guild_tags import wears_tag

logger = logging.getLogger('moddy.modules.auto_role')


class AutoRoleModule(ModuleBase):
    """
    Module d'attribution automatique de rôles
    Attribue des rôles automatiquement aux membres et bots qui rejoignent le serveur
    """

    MODULE_ID = "auto_role"
    CONFIG_DELETABLE = True
    MODULE_NAME = "Auto Role"
    MODULE_DESCRIPTION = "Attribue automatiquement des rôles aux nouveaux membres"
    MODULE_EMOJI = MANAGE_USER
    MODULE_ORDER = 30
    REQUIRED_BOT_PERMISSIONS = ["manage_roles"]
    SENDS_MESSAGES = False  # roles only, never writes in a channel

    def __init__(self, bot, guild_id: int):
        super().__init__(bot, guild_id)

        # Configuration
        self.member_roles: List[int] = []  # Rôles pour les utilisateurs normaux
        self.bot_roles: List[int] = []     # Rôles pour les bots
        self.tag_roles: List[int] = []     # Rôles tant que le membre porte le tag du serveur

    async def load_config(self, config_data: Dict[str, Any]) -> bool:
        """Charge la configuration depuis la DB"""
        try:
            self.config = config_data

            # Charge les rôles
            self.member_roles = config_data.get('member_roles', [])
            self.bot_roles = config_data.get('bot_roles', [])
            self.tag_roles = config_data.get('tag_roles', [])

            # Le module est activé si au moins un rôle est configuré
            self.enabled = bool(self.member_roles or self.bot_roles or self.tag_roles)

            return True
        except Exception as e:
            logger.error(f"Error loading auto_role config: {e}")
            return False

    async def validate_config(self, config_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Valide la configuration"""
        member_roles = config_data.get('member_roles', [])
        bot_roles = config_data.get('bot_roles', [])
        tag_roles = config_data.get('tag_roles', [])

        # Au moins un rôle doit être configuré
        if not member_roles and not bot_roles and not tag_roles:
            return False, "Vous devez configurer au moins un rôle (pour les membres, les bots ou le tag du serveur)"

        # Vérifie que les rôles existent
        guild = self.bot.get_guild(self.guild_id)
        if not guild or guild.me is None:
            return False, "Serveur introuvable"

        # Listes pour collecter tous les problèmes
        roles_too_high = []  # Rôles au-dessus de la hiérarchie du bot
        everyone_roles = []  # Rôles @everyone
        managed_roles = []   # Rôles gérés automatiquement
        missing_roles = []   # Rôles introuvables

        for role_id in dict.fromkeys([*member_roles, *bot_roles, *tag_roles]):
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
            'bot_roles': [],
            'tag_roles': [],
        }

    async def _altguard_holds_back(self, member: discord.Member) -> bool:
        """True when AltGuard gates this member and they have not passed yet.

        Handing the auto roles to someone who has not cleared the anti
        multi-account check would defeat the gate entirely — they would land
        with the server's regular roles before verifying. Bots are never gated
        (they cannot click a button), so their roles are applied as usual.
        The roles are granted later by
        ``AltGuardModule._run_auto_role`` — the moment the gate opens.
        """
        if member.bot:
            return False

        try:
            altguard = await self.bot.module_manager.get_module_instance(
                self.guild_id, 'altguard')
        except Exception as e:
            # Fail closed: a lookup error must not silently open the gate.
            logger.error(f"AltGuard lookup failed for guild {self.guild_id}: {e}")
            return True

        if not altguard or not altguard.enabled:
            return False

        return not await altguard.is_verified(member.id)

    async def on_member_join(self, member: discord.Member):
        """
        Appelé quand un membre rejoint le serveur
        Attribue les rôles automatiques selon qu'il s'agit d'un bot ou d'un utilisateur
        """
        if not self.enabled:
            return

        if await self._altguard_holds_back(member):
            logger.debug(
                f"Auto roles held back for {member.id} in guild {self.guild_id} "
                f"(AltGuard verification pending)"
            )
            return

        await self.apply_roles(member)

    async def apply_roles(self, member: discord.Member):
        """
        Attribue les rôles automatiques sans repasser par le gate AltGuard.

        Called directly by the AltGuard module once a member passes the
        verification, and by :meth:`on_member_join` when no gate applies.
        """
        if not self.enabled:
            return

        try:
            guild = self.bot.get_guild(self.guild_id)
            if not guild or guild.me is None:
                return

            # Détermine quels rôles attribuer selon le type de membre.
            # Every role goes through _assignable(): one role that Discord
            # refuses (deleted, above the bot, managed, @everyone) would
            # otherwise make the whole request fail and the member would get
            # none of the others.
            if member.bot:
                # C'est un bot, on attribue les rôles bots
                roles_to_add = self._assignable(guild, self.bot_roles)
            else:
                # C'est un utilisateur normal, on attribue les rôles membres
                roles_to_add = self._assignable(guild, self.member_roles)
                # Il rejoint en portant déjà le tag du serveur
                if wears_tag(member, self.guild_id):
                    for role in self._assignable(guild, self.tag_roles):
                        if role not in roles_to_add:
                            roles_to_add.append(role)

            if roles_to_add and not self._can_manage_roles(guild):
                logger.info(
                    f"Auto roles skipped in guild {self.guild_id}: Manage Roles is missing "
                    f"(or the bot is timed out)"
                )
                return

            # Attribue les rôles si il y en a
            if roles_to_add:
                granted = await self._grant(member, roles_to_add, "Auto Role")
                if granted:
                    self.count("roles_added", len(granted))
                    role_names = ", ".join([role.name for role in granted])
                    logger.info(
                        f"Added {len(granted)} auto role(s) to {member.id} ({'bot' if member.bot else 'member'}) "
                        f"in guild {self.guild_id}: {role_names}"
                    )

        except discord.Forbidden:
            logger.warning(
                f"Missing permissions to add auto roles to {member.id} in guild {self.guild_id}"
            )
        except discord.NotFound:
            # The member left (10007) before we got to them.
            logger.debug(f"Member {member.id} left guild {self.guild_id} before auto roles")
        except Exception as e:
            logger.error(
                f"Error adding auto roles to {member.id} in guild {self.guild_id}: {e}",
                exc_info=True
            )

    @staticmethod
    def _can_manage_roles(guild: discord.Guild) -> bool:
        """Manage Roles is checked at GUILD level (docs: Add Guild Member Role).

        ``guild_permissions`` already applies the timeout mask, so a timed-out
        bot reports False here instead of earning a 403.
        """
        me = guild.me
        return me is not None and me.guild_permissions.manage_roles

    async def _grant(self, member: discord.Member, roles: List[discord.Role],
                     reason: str) -> List[discord.Role]:
        """Add ``roles`` one request at a time (``atomic=False``).

        Unlike the atomic edit, the per-role endpoint never rewrites the
        member's whole role list from a possibly stale cache, and a role Discord
        rejects (hierarchy changed meanwhile, deleted) does not cost the others.
        Returns the roles actually granted.
        """
        granted: List[discord.Role] = []
        for role in roles:
            try:
                await member.add_roles(role, reason=reason, atomic=False)
                granted.append(role)
            except discord.NotFound as e:
                if getattr(e, "code", None) == 10011:  # unknown role: deleted meanwhile
                    logger.debug(f"Auto role {role.id} deleted (guild {self.guild_id})")
                    continue
                raise  # unknown member: they left, nothing more to do
            except discord.Forbidden:
                logger.info(
                    f"Auto role {role.id} refused for {member.id} "
                    f"(guild {self.guild_id}): hierarchy, 2FA or permissions"
                )
        return granted

    @staticmethod
    def _assignable(guild: discord.Guild, role_ids: List[int]) -> List[discord.Role]:
        roles = []
        me = guild.me
        if me is None:
            return roles
        for role_id in role_ids or []:
            role = guild.get_role(role_id)
            if (role and role.id != guild.id and not role.managed
                    and role < me.top_role and role not in roles):
                roles.append(role)
        return roles

    async def sync_tag_roles(self, member: discord.Member):
        """Give the tag roles while ``member`` wears the server tag, take them back after.

        Called on every member update carrying the user's ``primary_guild``
        (``cogs/server_tag_events.py``). Idempotent: only the difference
        between what the member has and what the tag says reaches Discord, so
        the update our own role edit triggers is a no-op.
        """
        if not self.enabled or not self.tag_roles or member.bot:
            return

        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            return

        tag_roles = self._assignable(guild, self.tag_roles)
        if not tag_roles or not self._can_manage_roles(guild):
            return

        try:
            if wears_tag(member, self.guild_id):
                missing = [r for r in tag_roles if r not in member.roles]
                if not missing:
                    return
                # Same AltGuard rule as the join roles: nothing before the gate opens.
                if await self._altguard_holds_back(member):
                    return
                granted = await self._grant(member, missing, "Auto Role (server tag)")
                if granted:
                    self.count("roles_added", len(granted))
                    logger.info(
                        f"Added {len(granted)} tag role(s) to {member.id} in guild {self.guild_id}"
                    )
            else:
                extra = [r for r in tag_roles if r in member.roles]
                if not extra:
                    return
                await member.remove_roles(*extra, reason="Auto Role (server tag removed)",
                                          atomic=False)
                logger.info(
                    f"Removed {len(extra)} tag role(s) from {member.id} in guild {self.guild_id}"
                )
        except discord.Forbidden:
            logger.warning(
                f"Missing permissions to sync tag roles for {member.id} in guild {self.guild_id}"
            )
        except discord.NotFound:
            logger.debug(f"Member {member.id} left guild {self.guild_id} during a tag sync")
        except discord.HTTPException as e:
            logger.error(
                f"Error syncing tag roles for {member.id} in guild {self.guild_id}: {e}"
            )
