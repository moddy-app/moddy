"""
Gestionnaire de base de données PostgreSQL pour Moddy
Base de données locale sur le VPS
"""

import asyncpg
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Union
from enum import Enum
import logging

logger = logging.getLogger('moddy.database')


class UpdateSource(Enum):
    """Sources de mise à jour des données"""
    BOT_JOIN = "bot_join"
    USER_PROFILE = "user_profile"
    API_CALL = "api_call"
    MANUAL = "manual"
    SCHEDULED = "scheduled"


class ModdyDatabase:
    """Gestionnaire principal de la base de données"""

    def __init__(self, database_url: str = None):
        self.pool: Optional[asyncpg.Pool] = None
        self.database_url = database_url or "postgresql://moddy:password@localhost/moddy"

    async def connect(self):
        """Establishes the database connection"""
        try:
            self.pool = await asyncpg.create_pool(
                self.database_url,
                min_size=5,
                max_size=20,
                command_timeout=60,
                server_settings={
                    'application_name': 'Moddy Bot',
                    'jit': 'off'
                }
            )
            logger.info("✅ PostgreSQL database connected")

            # Initialize tables
            await self._init_tables()

        except Exception as e:
            logger.error(f"❌ PostgreSQL connection error: {e}")
            raise

    async def close(self):
        """Closes the connection"""
        if self.pool:
            await self.pool.close()

    async def _init_tables(self):
        """Creates tables if they do not exist"""
        async with self.pool.acquire() as conn:
            # Errors table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS errors (
                    error_code VARCHAR(8) PRIMARY KEY,
                    error_type VARCHAR(100),
                    message TEXT,
                    file_source VARCHAR(255),
                    line_number INTEGER,
                    traceback TEXT,
                    user_id BIGINT,
                    guild_id BIGINT,
                    command VARCHAR(100),
                    timestamp TIMESTAMPTZ DEFAULT NOW(),
                    context JSONB DEFAULT '{}'::jsonb
                )
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_errors_timestamp ON errors(timestamp)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_errors_user ON errors(user_id)
            """)

            # Cache des serveurs
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS guilds_cache (
                    guild_id BIGINT PRIMARY KEY,
                    name VARCHAR(100),
                    icon_url TEXT,
                    features TEXT[],
                    member_count INTEGER,
                    created_at TIMESTAMPTZ,
                    last_updated TIMESTAMPTZ DEFAULT NOW(),
                    update_source VARCHAR(50),
                    raw_data JSONB DEFAULT '{}'::jsonb
                )
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_guilds_cache_updated ON guilds_cache(last_updated)
            """)

            # Table des utilisateurs (fonctionnelle)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    attributes JSONB DEFAULT '{}'::jsonb,
                    data JSONB DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_users_attributes ON users USING GIN (attributes)
            """)

            # Table des serveurs (fonctionnelle)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS guilds (
                    guild_id BIGINT PRIMARY KEY,
                    attributes JSONB DEFAULT '{}'::jsonb,
                    data JSONB DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_guilds_attributes ON guilds USING GIN (attributes)
            """)

            # Table d'audit des attributs
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS attribute_changes (
                    id SERIAL PRIMARY KEY,
                    entity_type VARCHAR(10) CHECK (entity_type IN ('user', 'guild')),
                    entity_id BIGINT NOT NULL,
                    attribute_name VARCHAR(50),
                    old_value TEXT,
                    new_value TEXT,
                    changed_by BIGINT,
                    changed_at TIMESTAMPTZ DEFAULT NOW(),
                    reason TEXT
                )
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_attribute_changes_entity 
                ON attribute_changes(entity_type, entity_id)
            """)

            logger.info("✅ Tables initialisées")

    # ================ GESTION DES ERREURS ================

    async def log_error(self, error_code: str, error_data: Dict[str, Any]):
        """Enregistre une erreur dans la base de données"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO errors (error_code, error_type, message, file_source,
                                    line_number, traceback, user_id, guild_id,
                                    command, context)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
                error_code,
                error_data.get('type'),
                error_data.get('message'),
                error_data.get('file'),
                error_data.get('line'),
                error_data.get('traceback'),
                error_data.get('user_id'),
                error_data.get('guild_id'),
                error_data.get('command'),
                json.dumps(error_data.get('context', {}))
            )

    async def get_error(self, error_code: str) -> Optional[Dict[str, Any]]:
        """Récupère une erreur par son code"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM errors WHERE error_code = $1",
                error_code
            )
            return dict(row) if row else None

    # ================ CACHE DES LOOKUPS ================

    async def cache_guild_info(self, guild_id: int, info: Dict[str, Any],
                               source: UpdateSource = UpdateSource.API_CALL):
        """Met en cache les informations d'un serveur"""
        created_at_dt = info.get('created_at')
        logger.info(f"[DIAG] cache_guild_info for {guild_id}: initial created_at is {created_at_dt} (type: {type(created_at_dt)})")

        # Correction du fuseau horaire pour 'created_at'
        if isinstance(created_at_dt, datetime):
            if created_at_dt.tzinfo is None:
                logger.warning(f"[DIAG] For {guild_id}, created_at is NAIVE. Applying UTC timezone.")
                created_at_dt = created_at_dt.replace(tzinfo=timezone.utc)
            else:
                logger.info(f"[DIAG] For {guild_id}, created_at is AWARE. No change needed.")
        else:
            logger.error(f"[DIAG] For {guild_id}, created_at is NOT a datetime object.")

        # Crée une copie des données pour la sérialisation JSON
        # afin de ne pas modifier le dictionnaire original.
        serializable_info = info.copy()
        if 'created_at' in serializable_info and isinstance(serializable_info['created_at'], datetime):
            serializable_info['created_at'] = serializable_info['created_at'].isoformat()

        logger.info(f"[DIAG] For {guild_id}, final created_at value for DB is {created_at_dt}")

        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO guilds_cache (guild_id, name, icon_url, features, member_count,
                                          created_at, update_source, raw_data)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (guild_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    icon_url = EXCLUDED.icon_url,
                    features = EXCLUDED.features,
                    member_count = EXCLUDED.member_count,
                    last_updated = NOW(),
                    update_source = EXCLUDED.update_source,
                    raw_data = EXCLUDED.raw_data
            """,
                guild_id,
                info.get('name'),
                info.get('icon_url'),
                info.get('features', []),
                info.get('member_count'),
                created_at_dt,  # Utilise le datetime corrigé
                source.value,
                json.dumps(serializable_info)  # Utilise la copie sérialisable pour JSONB
            )

    async def get_cached_guild(self, guild_id: int, max_age_days: int = 7) -> Optional[Dict[str, Any]]:
        """Récupère les infos cachées d'un serveur si elles sont assez récentes"""
        async with self.pool.acquire() as conn:
            query = f"""
                SELECT * FROM guilds_cache 
                WHERE guild_id = $1 
                AND last_updated > NOW() - INTERVAL '{max_age_days} days'
            """
            row = await conn.fetchrow(query, guild_id)

            if row:
                data = dict(row)
                data['raw_data'] = json.loads(data['raw_data']) if data['raw_data'] else {}
                return data
            return None

    # ================ GESTION DES ATTRIBUTS ================

    async def get_user(self, user_id: int) -> Dict[str, Any]:
        """Récupère ou crée un utilisateur"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE user_id = $1",
                user_id
            )

            if not row:
                # Crée l'utilisateur s'il n'existe pas, gère la concurrence
                await conn.execute(
                    "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
                    user_id
                )
                # Re-fetch pour être sûr d'avoir les données
                row = await conn.fetchrow(
                    "SELECT * FROM users WHERE user_id = $1",
                    user_id
                )

            return {
                'user_id': row['user_id'],
                'attributes': json.loads(row['attributes']) if row['attributes'] else {},
                'data': json.loads(row['data']) if row['data'] else {},
                'created_at': row.get('created_at', datetime.now(timezone.utc)),
                'updated_at': row.get('updated_at', datetime.now(timezone.utc))
            }

    async def get_guild(self, guild_id: int) -> Dict[str, Any]:
        """Récupère ou crée un serveur"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM guilds WHERE guild_id = $1",
                guild_id
            )

            if not row:
                # Crée le serveur s'il n'existe pas, gère la concurrence
                await conn.execute(
                    "INSERT INTO guilds (guild_id) VALUES ($1) ON CONFLICT (guild_id) DO NOTHING",
                    guild_id
                )
                # Re-fetch pour être sûr d'avoir les données
                row = await conn.fetchrow(
                    "SELECT * FROM guilds WHERE guild_id = $1",
                    guild_id
                )

            return {
                'guild_id': row['guild_id'],
                'attributes': json.loads(row['attributes']) if row['attributes'] else {},
                'data': json.loads(row['data']) if row['data'] else {},
                'created_at': row.get('created_at', datetime.now(timezone.utc)),
                'updated_at': row.get('updated_at', datetime.now(timezone.utc))
            }

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

            # CORRECTION ICI : Gère proprement le cas où attributes est None
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

    # ================ GESTION DE LA DATA ================

    async def update_user_data(self, user_id: int, path: str, value: Any):
        """Met à jour une partie spécifique de la data utilisateur"""
        async with self.pool.acquire() as conn:
            # Utilise jsonb_set pour mettre à jour un chemin spécifique
            path_parts = path.split('.')
            json_path = '{' + ','.join(path_parts) + '}'
            await conn.execute("""
                UPDATE users 
                SET data = jsonb_set(data, $1, $2, true),
                    updated_at = NOW()
                WHERE user_id = $3
            """,
                json_path,
                json.dumps(value),
                user_id
            )

    async def update_guild_data(self, guild_id: int, path: str, value: Any):
        """Met à jour une partie spécifique de la data serveur"""
        async with self.pool.acquire() as conn:
            path_parts = path.split('.')
            json_path = '{' + ','.join(path_parts) + '}'
            await conn.execute("""
                UPDATE guilds 
                SET data = jsonb_set(data, $1, $2, true),
                    updated_at = NOW()
                WHERE guild_id = $3
            """,
                json_path,
                json.dumps(value),
                guild_id
            )

    # ================ REQUÊTES UTILES ================

    async def get_users_with_attribute(self, attribute: str, value: Any = None) -> List[int]:
        """Récupère tous les utilisateurs ayant un attribut spécifique

        Si value est None, cherche juste la présence de l'attribut
        Si value est fournie, cherche cette valeur spécifique
        """
        async with self.pool.acquire() as conn:
            if value is None:
                # Cherche juste la présence de l'attribut
                rows = await conn.fetch("""
                    SELECT user_id FROM users 
                    WHERE attributes ? $1
                """, attribute)
            else:
                # Cherche une valeur spécifique
                rows = await conn.fetch("""
                    SELECT user_id FROM users 
                    WHERE attributes @> $1
                """, json.dumps({attribute: value}))

            return [row['user_id'] for row in rows]

    async def get_guilds_with_attribute(self, attribute: str, value: Any = None) -> List[int]:
        """Récupère tous les serveurs ayant un attribut spécifique"""
        async with self.pool.acquire() as conn:
            if value is None:
                rows = await conn.fetch("""
                    SELECT guild_id FROM guilds 
                    WHERE attributes ? $1
                """, attribute)
            else:
                rows = await conn.fetch("""
                    SELECT guild_id FROM guilds 
                    WHERE attributes @> $1
                """, json.dumps({attribute: value}))

            return [row['guild_id'] for row in rows]

    async def cleanup_old_errors(self, days: int = 30):
        """Nettoie les erreurs de plus de X jours"""
        async with self.pool.acquire() as conn:
            deleted = await conn.execute(f"""
                DELETE FROM errors 
                WHERE timestamp < NOW() - INTERVAL '{days} days'
            """)
            return deleted

    async def get_stats(self) -> Dict[str, int]:
        """Récupère des statistiques sur la base de données"""
        async with self.pool.acquire() as conn:
            stats = {}

            # Compte les enregistrements
            for table in ['errors', 'users', 'guilds', 'guilds_cache']:
                count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
                stats[table] = count

            # Statistiques spécifiques avec le nouveau système
            # Compte les utilisateurs ayant l'attribut BETA (peu importe la valeur)
            stats['beta_users'] = await conn.fetchval("""
                SELECT COUNT(*) FROM users 
                WHERE attributes ? 'BETA'
            """)

            # Compte les utilisateurs ayant l'attribut PREMIUM
            stats['premium_users'] = await conn.fetchval("""
                SELECT COUNT(*) FROM users 
                WHERE attributes ? 'PREMIUM'
            """)

            # Compte les utilisateurs blacklistés (ayant l'attribut BLACKLISTED)
            stats['blacklisted_users'] = await conn.fetchval("""
                SELECT COUNT(*) FROM users 
                WHERE attributes ? 'BLACKLISTED'
            """)

            return stats


# Instance globale (sera initialisée dans bot.py)
db = None


async def setup_database(database_url: str = None) -> ModdyDatabase:
    """Initialise et retourne l'instance de base de données"""
    global db
    db = ModdyDatabase(database_url)
    await db.connect()
    return db