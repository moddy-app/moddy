import json
import logging
from typing import Dict, Any, List

logger = logging.getLogger('moddy.database')


class StaffRepository:
    """Staff permissions database operations"""

    async def get_staff_permissions(self, user_id: int) -> Dict[str, Any]:
        """Récupère les permissions staff d'un utilisateur"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM staff_permissions WHERE user_id = $1",
                user_id
            )

            if not row:
                return {
                    'user_id': user_id,
                    'roles': [],
                    'denied_commands': [],
                    'role_permissions': {},
                    'created_at': None,
                    'updated_at': None
                }

            return {
                'user_id': row['user_id'],
                'roles': self._parse_jsonb_list(row['roles']),
                'denied_commands': self._parse_jsonb_list(row['denied_commands']),
                'role_permissions': self._parse_jsonb(row.get('role_permissions')),
                'created_at': row.get('created_at'),
                'updated_at': row.get('updated_at'),
                'created_by': row.get('created_by'),
                'updated_by': row.get('updated_by')
            }

    async def set_staff_roles(self, user_id: int, roles: List[str], updated_by: int):
        """Définit les rôles staff d'un utilisateur"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO staff_permissions (user_id, roles, updated_by, created_by)
                VALUES ($1, $2, $3, $3)
                ON CONFLICT (user_id)
                DO UPDATE SET roles = $2, updated_by = $3, updated_at = NOW()
            """, user_id, json.dumps(roles), updated_by)

            # Set TEAM attribute automatically
            await self.set_attribute('user', user_id, 'TEAM', True, updated_by, "Added to staff team")

    async def add_staff_role(self, user_id: int, role: str, updated_by: int):
        """Ajoute un rôle staff à un utilisateur"""
        perms = await self.get_staff_permissions(user_id)
        roles = perms['roles']

        if role not in roles:
            roles.append(role)
            await self.set_staff_roles(user_id, roles, updated_by)

    async def remove_staff_role(self, user_id: int, role: str, updated_by: int):
        """Retire un rôle staff d'un utilisateur"""
        perms = await self.get_staff_permissions(user_id)
        roles = perms['roles']

        if role in roles:
            roles.remove(role)
            await self.set_staff_roles(user_id, roles, updated_by)

            # If no more roles, remove TEAM attribute
            if not roles:
                await self.set_attribute('user', user_id, 'TEAM', None, updated_by, "Removed from staff team")

    async def set_denied_commands(self, user_id: int, denied_commands: List[str], updated_by: int):
        """Définit les commandes interdites pour un utilisateur"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO staff_permissions (user_id, denied_commands, updated_by, created_by)
                VALUES ($1, $2, $3, $3)
                ON CONFLICT (user_id)
                DO UPDATE SET denied_commands = $2, updated_by = $3, updated_at = NOW()
            """, user_id, json.dumps(denied_commands), updated_by)

    async def add_denied_command(self, user_id: int, command: str, updated_by: int):
        """Ajoute une commande à la liste des commandes interdites"""
        perms = await self.get_staff_permissions(user_id)
        denied = perms['denied_commands']

        if command not in denied:
            denied.append(command)
            await self.set_denied_commands(user_id, denied, updated_by)

    async def remove_denied_command(self, user_id: int, command: str, updated_by: int):
        """Retire une commande de la liste des commandes interdites"""
        perms = await self.get_staff_permissions(user_id)
        denied = perms['denied_commands']

        if command in denied:
            denied.remove(command)
            await self.set_denied_commands(user_id, denied, updated_by)

    async def remove_staff_permissions(self, user_id: int):
        """Supprime complètement les permissions staff d'un utilisateur"""
        async with self.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM staff_permissions WHERE user_id = $1",
                user_id
            )

    async def get_all_staff_members(self) -> List[Dict[str, Any]]:
        """Récupère tous les membres du staff"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM staff_permissions ORDER BY created_at"
            )

            return [{
                'user_id': row['user_id'],
                'roles': self._parse_jsonb_list(row['roles']),
                'denied_commands': self._parse_jsonb_list(row['denied_commands']),
                'role_permissions': self._parse_jsonb(row.get('role_permissions')),
                'created_at': row.get('created_at'),
                'updated_at': row.get('updated_at')
            } for row in rows]

    async def set_role_permissions(self, user_id: int, role: str, permissions: List[str], updated_by: int):
        """Définit les permissions pour un rôle spécifique d'un utilisateur"""
        perms = await self.get_staff_permissions(user_id)
        role_perms = perms['role_permissions']
        role_perms[role] = permissions

        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO staff_permissions (user_id, role_permissions, updated_by, created_by)
                VALUES ($1, $2, $3, $3)
                ON CONFLICT (user_id)
                DO UPDATE SET role_permissions = $2, updated_by = $3, updated_at = NOW()
            """, user_id, json.dumps(role_perms), updated_by)

    async def get_role_permissions(self, user_id: int, role: str) -> List[str]:
        """Récupère les permissions d'un rôle spécifique"""
        perms = await self.get_staff_permissions(user_id)
        return perms['role_permissions'].get(role, [])
