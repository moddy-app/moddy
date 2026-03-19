"""
Core database class for Moddy.
ModdyDatabase inherits from all repository mixins.
"""

import asyncpg
import json
import copy
import logging
from typing import Optional, Dict, Any, List

from db.repositories._utils import set_nested_value
from db.repositories.errors import ErrorRepository
from db.repositories.users import UserRepository
from db.repositories.guilds import GuildRepository
from db.repositories.attributes import AttributeRepository
from db.repositories.staff import StaffRepository
from db.repositories.reminders import ReminderRepository
from db.repositories.saved_messages import SavedMessageRepository
from db.repositories.interserver import InterserverRepository
from db.repositories.moderation import ModerationRepository
from db.repositories.saved_roles import SavedRolesRepository

logger = logging.getLogger('moddy.database')

# Connection pool configuration
POOL_MIN_SIZE = 5
POOL_MAX_SIZE = 20
COMMAND_TIMEOUT = 60


class ModdyDatabase(
    ErrorRepository,
    UserRepository,
    GuildRepository,
    AttributeRepository,
    StaffRepository,
    ReminderRepository,
    SavedMessageRepository,
    InterserverRepository,
    ModerationRepository,
    SavedRolesRepository,
):
    """Gestionnaire principal de la base de données"""

    def __init__(self, database_url: str = None):
        self.pool: Optional[asyncpg.Pool] = None
        self.database_url = database_url or "postgresql://moddy:password@localhost/moddy"

    def _parse_jsonb(self, value: Any) -> dict:
        """Parse JSONB value that can be either a dict or a JSON string"""
        if not value:
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    def _parse_jsonb_list(self, value: Any) -> list:
        """Parse JSONB value that should be a list"""
        if not value:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                result = json.loads(value)
                return result if isinstance(result, list) else []
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    async def connect(self):
        """Establishes the database connection"""
        try:
            self.pool = await asyncpg.create_pool(
                self.database_url,
                min_size=POOL_MIN_SIZE,
                max_size=POOL_MAX_SIZE,
                command_timeout=COMMAND_TIMEOUT,
                server_settings={
                    'application_name': 'Moddy Bot',
                    'jit': 'off'
                }
            )
            logger.info("[OK] PostgreSQL database connected")

            # Initialize tables
            await self._init_tables()

        except Exception as e:
            logger.error(f"[ERROR] PostgreSQL connection error: {e}")
            raise

    async def close(self):
        """Closes the connection"""
        if self.pool:
            await self.pool.close()

    async def _update_entity_data(self, table: str, id_column: str, entity_id: int, path: str, value: Any):
        """Update a specific part of an entity's data (shared by update_user_data and update_guild_data)"""
        async with self.pool.acquire() as conn:
            # First, ensure the entity exists
            await conn.execute(f"""
                INSERT INTO {table} ({id_column}, data, attributes, created_at, updated_at)
                VALUES ($1, '{{}}'::jsonb, '{{}}'::jsonb, NOW(), NOW())
                ON CONFLICT ({id_column}) DO NOTHING
            """, entity_id)

            # Get current data
            row = await conn.fetchrow(f"SELECT data FROM {table} WHERE {id_column} = $1", entity_id)

            # Handle both dict and string JSON responses from PostgreSQL
            if row and row['data']:
                if isinstance(row['data'], str):
                    current_data = json.loads(row['data'])
                elif isinstance(row['data'], dict):
                    current_data = row['data']
                else:
                    current_data = {}
            else:
                current_data = {}

            logger.debug(f"[DB] Before update for {id_column} {entity_id}: {current_data}")
            logger.debug(f"[DB] Updating path '{path}' with value {json.dumps(value)}")

            # Build the nested structure in Python
            path_parts = path.split('.')

            # Update the data structure (use deepcopy to avoid modifying original)
            updated_data = set_nested_value(copy.deepcopy(current_data), path_parts, value)

            # Save the complete updated structure
            result = await conn.execute(f"""
                UPDATE {table}
                SET data = $1::jsonb,
                    updated_at = NOW()
                WHERE {id_column} = $2
            """,
                json.dumps(updated_data),
                entity_id
            )

            # Verify the data was saved
            after = await conn.fetchrow(f"SELECT data FROM {table} WHERE {id_column} = $1", entity_id)
            logger.debug(f"[DB] After update for {id_column} {entity_id}: {after['data'] if after else 'None'}")
            logger.debug(f"[DB] Update result: {result}")

            # Verify the path exists - handle both dict and string JSON
            if after and after['data']:
                # Convert to dict if it's a string
                if isinstance(after['data'], str):
                    saved_data = json.loads(after['data'])
                else:
                    saved_data = after['data']

                current = saved_data
                for part in path_parts:
                    if isinstance(current, dict) and part in current:
                        current = current[part]
                    else:
                        logger.error(f"[DB] [ERROR] Verification failed! Path {path} not found in saved data")
                        raise Exception(f"Data verification failed: path {path} not found after update")

                logger.debug(f"[DB] [OK] Verification successful: data correctly saved at path {path}")
            else:
                logger.error(f"[DB] [ERROR] Verification failed! No data found for {id_column} {entity_id}")
                raise Exception("Data verification failed: no data in database")

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
                    context JSONB DEFAULT '{}'::jsonb,
                    sentry_event_id VARCHAR(32),
                    sentry_issue_id VARCHAR(20)
                )
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_errors_timestamp ON errors(timestamp)
            """)

            # Add Sentry columns if they don't exist (migration)
            await conn.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='errors' AND column_name='sentry_event_id'
                    ) THEN
                        ALTER TABLE errors ADD COLUMN sentry_event_id VARCHAR(32);
                    END IF;

                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='errors' AND column_name='sentry_issue_id'
                    ) THEN
                        ALTER TABLE errors ADD COLUMN sentry_issue_id VARCHAR(20);
                    END IF;
                END $$;
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_errors_user ON errors(user_id)
            """)

            # Table des utilisateurs
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

            # Table des serveurs
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

            # Table des permissions staff
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS staff_permissions (
                    user_id BIGINT PRIMARY KEY,
                    roles JSONB DEFAULT '[]'::jsonb,
                    denied_commands JSONB DEFAULT '[]'::jsonb,
                    role_permissions JSONB DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    created_by BIGINT,
                    updated_by BIGINT
                )
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_staff_permissions_roles
                ON staff_permissions USING GIN (roles)
            """)

            # Table des rappels
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    guild_id BIGINT,
                    channel_id BIGINT,
                    message TEXT NOT NULL,
                    remind_at TIMESTAMPTZ NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    sent BOOLEAN DEFAULT FALSE,
                    sent_at TIMESTAMPTZ,
                    failed BOOLEAN DEFAULT FALSE,
                    send_in_channel BOOLEAN DEFAULT FALSE
                )
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_reminders_user_id ON reminders(user_id)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_reminders_remind_at ON reminders(remind_at)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_reminders_sent ON reminders(sent)
            """)

            # Table des messages sauvegardés
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS saved_messages (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    message_id BIGINT NOT NULL,
                    channel_id BIGINT NOT NULL,
                    guild_id BIGINT,
                    author_id BIGINT NOT NULL,
                    author_username TEXT,
                    content TEXT,
                    attachments JSONB DEFAULT '[]'::jsonb,
                    embeds JSONB DEFAULT '[]'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL,
                    saved_at TIMESTAMPTZ DEFAULT NOW(),
                    message_url TEXT,
                    note TEXT,
                    raw_message_data JSONB DEFAULT '{}'::jsonb
                )
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_saved_messages_user_id ON saved_messages(user_id)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_saved_messages_saved_at ON saved_messages(saved_at)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_saved_messages_author_id ON saved_messages(author_id)
            """)

            # Table des messages inter-serveur
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS interserver_messages (
                    moddy_id VARCHAR(8) PRIMARY KEY,
                    original_message_id BIGINT NOT NULL,
                    original_guild_id BIGINT NOT NULL,
                    original_channel_id BIGINT NOT NULL,
                    author_id BIGINT NOT NULL,
                    author_username TEXT,
                    content TEXT,
                    timestamp TIMESTAMPTZ DEFAULT NOW(),
                    status VARCHAR(20) DEFAULT 'active',
                    is_moddy_team BOOLEAN DEFAULT FALSE,
                    relayed_messages JSONB DEFAULT '[]'::jsonb,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_interserver_original_message ON interserver_messages(original_message_id)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_interserver_author ON interserver_messages(author_id)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_interserver_status ON interserver_messages(status)
            """)

            # Migration: Add role_permissions column if it doesn't exist
            await conn.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'staff_permissions'
                        AND column_name = 'role_permissions'
                    ) THEN
                        ALTER TABLE staff_permissions
                        ADD COLUMN role_permissions JSONB DEFAULT '{}'::jsonb;
                    END IF;
                END $$;
            """)

            # Migration: Add author_username and raw_message_data to saved_messages if they don't exist
            await conn.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'saved_messages'
                        AND column_name = 'author_username'
                    ) THEN
                        ALTER TABLE saved_messages
                        ADD COLUMN author_username TEXT;
                    END IF;

                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'saved_messages'
                        AND column_name = 'raw_message_data'
                    ) THEN
                        ALTER TABLE saved_messages
                        ADD COLUMN raw_message_data JSONB DEFAULT '{}'::jsonb;
                    END IF;
                END $$;
            """)

            # Table des cases de modération
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS moderation_cases (
                    case_id VARCHAR(8) PRIMARY KEY,
                    case_type VARCHAR(20) NOT NULL,
                    sanction_type VARCHAR(50) NOT NULL,
                    entity_type VARCHAR(10) NOT NULL CHECK (entity_type IN ('user', 'guild')),
                    entity_id BIGINT NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'open',
                    reason TEXT NOT NULL,
                    evidence TEXT,
                    duration INTEGER,
                    staff_notes JSONB DEFAULT '[]'::jsonb,
                    created_by BIGINT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_by BIGINT,
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    closed_by BIGINT,
                    closed_at TIMESTAMPTZ,
                    close_reason TEXT
                )
            """)

            # Migration: Convert case_id from SERIAL to VARCHAR(8)
            await conn.execute("""
                DO $$
                BEGIN
                    -- Check if case_id is still an integer type
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'moderation_cases'
                        AND column_name = 'case_id'
                        AND data_type IN ('integer', 'bigint')
                    ) THEN
                        -- Drop all existing cases (they use old system)
                        TRUNCATE TABLE moderation_cases;

                        -- Drop the sequence if it exists
                        DROP SEQUENCE IF EXISTS moderation_cases_case_id_seq CASCADE;

                        -- Alter the column type
                        ALTER TABLE moderation_cases
                        ALTER COLUMN case_id TYPE VARCHAR(8);

                        RAISE NOTICE 'Migrated case_id from SERIAL to VARCHAR(8)';
                    END IF;
                END $$;
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_moderation_cases_entity
                ON moderation_cases(entity_type, entity_id)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_moderation_cases_status
                ON moderation_cases(status)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_moderation_cases_type
                ON moderation_cases(case_type, sanction_type)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_moderation_cases_created_at
                ON moderation_cases(created_at DESC)
            """)

            # Table des rôles sauvegardés (Auto Restore Roles module)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS saved_roles (
                    id SERIAL PRIMARY KEY,
                    guild_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    roles BIGINT[] NOT NULL,
                    username TEXT,
                    saved_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(guild_id, user_id)
                )
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_saved_roles_guild_id
                ON saved_roles(guild_id)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_saved_roles_user_id
                ON saved_roles(user_id)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_saved_roles_saved_at
                ON saved_roles(saved_at)
            """)

            logger.info("[OK] Tables initialisées")

    async def cleanup_old_errors(self, days: int = 30):
        """Nettoie les erreurs de plus de X jours"""
        async with self.pool.acquire() as conn:
            deleted = await conn.execute("""
                DELETE FROM errors
                WHERE timestamp < NOW() - make_interval(days => $1)
            """, days)
            return deleted

    async def get_stats(self) -> Dict[str, int]:
        """Récupère des statistiques sur la base de données"""
        async with self.pool.acquire() as conn:
            stats = {}

            # Compte les enregistrements (sans guilds_cache)
            for table in ['errors', 'users', 'guilds']:
                count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
                stats[table] = count

            # Statistiques spécifiques avec le nouveau système
            # Compte les utilisateurs ayant l'attribut BETA
            stats['beta_users'] = await conn.fetchval("""
                SELECT COUNT(*) FROM users
                WHERE attributes ? 'BETA'
            """)

            # Compte les utilisateurs ayant l'attribut PREMIUM
            stats['premium_users'] = await conn.fetchval("""
                SELECT COUNT(*) FROM users
                WHERE attributes ? 'PREMIUM'
            """)

            # Compte les utilisateurs blacklistés
            stats['blacklisted_users'] = await conn.fetchval("""
                SELECT COUNT(*) FROM users
                WHERE attributes ? 'BLACKLISTED'
            """)

            return stats
