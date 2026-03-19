import json
import logging
from typing import Any, Optional, Union

logger = logging.getLogger('moddy.database')


class AttributeRepository:
    """Attribute management database operations"""

    async def set_attribute(self, entity_type: str, entity_id: int,
                            attribute: str, value: Optional[Union[str, bool]],
                            changed_by: int, reason: str = None):
        """Définit un attribut pour un utilisateur ou serveur

        Pour les attributs booléens : si value est True, on stocke juste l'attribut
        Pour les attributs avec valeur : on stocke la valeur (ex: LANG=FR)
        Si value est None, on supprime l'attribut
        """
        table = 'users' if entity_type == 'user' else 'guilds'

        async with self.pool.acquire() as conn:
            # S'assure que l'entité existe d'abord
            if entity_type == 'user':
                await self.get_user(entity_id)
            else:
                await self.get_guild(entity_id)

            # Récupère l'ancienne valeur
            row = await conn.fetchrow(
                f"SELECT attributes FROM {table} WHERE {entity_type}_id = $1",
                entity_id
            )

            # Gère proprement le cas où attributes est None
            if row and row['attributes']:
                old_attributes = json.loads(row['attributes'])
            else:
                old_attributes = {}

            old_value = old_attributes.get(attribute)

            # Met à jour l'attribut selon le nouveau système
            if value is None:
                # Supprime l'attribut
                if attribute in old_attributes:
                    del old_attributes[attribute]
            elif value is True:
                # Pour les booléens True, on stocke juste la clé sans valeur
                old_attributes[attribute] = True
            elif value is False:
                # Pour les booléens False, on supprime l'attribut
                if attribute in old_attributes:
                    del old_attributes[attribute]
            else:
                # Pour les autres valeurs (string, int, etc), on stocke la valeur
                old_attributes[attribute] = value

            # Sauvegarde
            await conn.execute(f"""
                UPDATE {table}
                SET attributes = $1::jsonb, updated_at = NOW()
                WHERE {entity_type}_id = $2
            """, json.dumps(old_attributes), entity_id)

            # Log le changement
            await conn.execute("""
                INSERT INTO attribute_changes (entity_type, entity_id, attribute_name,
                                               old_value, new_value, changed_by, reason)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
                entity_type, entity_id, attribute,
                str(old_value) if old_value is not None else None,
                str(value) if value is not None else None,
                changed_by, reason
            )

    async def has_attribute(self, entity_type: str, entity_id: int, attribute: str) -> bool:
        """Vérifie si une entité a un attribut spécifique"""
        entity = await self.get_user(entity_id) if entity_type == 'user' else await self.get_guild(entity_id)
        return attribute in entity['attributes']

    async def get_attribute(self, entity_type: str, entity_id: int, attribute: str) -> Any:
        """Récupère la valeur d'un attribut

        Retourne True pour les attributs booléens présents
        Retourne la valeur pour les attributs avec valeur
        Retourne None si l'attribut n'existe pas
        """
        entity = await self.get_user(entity_id) if entity_type == 'user' else await self.get_guild(entity_id)
        return entity['attributes'].get(attribute)
