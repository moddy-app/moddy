"""
Script de migration: Sépare les configurations Welcome en Welcome Channel et Welcome DM
"""

import asyncio
import asyncpg
import json
import logging
import sys

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('migration')


async def migrate_welcome_configs():
    """Migre les anciennes configurations welcome vers welcome_channel et welcome_dm"""

    # Connexion à la base de données
    try:
        conn = await asyncpg.connect("postgresql://moddy:password@localhost/moddy")
        logger.info("✅ Connecté à la base de données")
    except Exception as e:
        logger.error(f"❌ Erreur de connexion: {e}")
        return

    try:
        # Récupère toutes les guildes
        guilds = await conn.fetch("SELECT guild_id, data FROM guilds")
        logger.info(f"📊 {len(guilds)} guildes trouvées")

        migrated_count = 0

        for guild_row in guilds:
            guild_id = guild_row['guild_id']

            # Parse le champ data
            if isinstance(guild_row['data'], str):
                guild_data = json.loads(guild_row['data'])
            elif isinstance(guild_row['data'], dict):
                guild_data = guild_row['data']
            else:
                guild_data = {}

            modules = guild_data.get('modules', {})

            # Vérifie si l'ancienne config "welcome" existe
            if 'welcome' not in modules:
                continue

            old_welcome_config = modules['welcome']
            logger.info(f"🔄 Migration de la guilde {guild_id}")

            # Crée les nouvelles configurations
            welcome_channel_config = {
                'channel_id': old_welcome_config.get('channel_id'),
                'message_template': old_welcome_config.get('message_template', "Bienvenue {user} sur le serveur !"),
                'mention_user': old_welcome_config.get('mention_user', True),
                'embed_enabled': old_welcome_config.get('embed_enabled', False),
                'embed_title': old_welcome_config.get('embed_title', "Bienvenue !"),
                'embed_description': old_welcome_config.get('embed_description'),
                'embed_color': old_welcome_config.get('embed_color', 0x5865F2),
                'embed_footer': old_welcome_config.get('embed_footer'),
                'embed_image_url': old_welcome_config.get('embed_image_url'),
                'embed_thumbnail_enabled': old_welcome_config.get('embed_thumbnail_enabled', True),
                'embed_author_enabled': old_welcome_config.get('embed_author_enabled', False)
            }

            # Pour le DM, on adapte le message (pas de mention dans les DMs)
            dm_message = old_welcome_config.get('message_template', "Bienvenue sur le serveur {server} !")
            # Remplace {user} par {username} car pas de mention en DM
            if '{user}' in dm_message:
                dm_message = dm_message.replace('{user}', '{username}')

            welcome_dm_config = {
                'message_template': dm_message,
                'embed_enabled': old_welcome_config.get('embed_enabled', False),
                'embed_title': old_welcome_config.get('embed_title', "Bienvenue !"),
                'embed_description': old_welcome_config.get('embed_description'),
                'embed_color': old_welcome_config.get('embed_color', 0x5865F2),
                'embed_footer': old_welcome_config.get('embed_footer'),
                'embed_image_url': old_welcome_config.get('embed_image_url'),
                'embed_thumbnail_enabled': old_welcome_config.get('embed_thumbnail_enabled', True),
                'embed_author_enabled': old_welcome_config.get('embed_author_enabled', False)
            }

            # Ajoute les nouvelles configs et supprime l'ancienne
            modules['welcome_channel'] = welcome_channel_config
            modules['welcome_dm'] = welcome_dm_config
            del modules['welcome']

            # Sauvegarde dans la base
            guild_data['modules'] = modules
            await conn.execute(
                "UPDATE guilds SET data = $1::jsonb, updated_at = NOW() WHERE guild_id = $2",
                json.dumps(guild_data),
                guild_id
            )

            logger.info(f"✅ Guilde {guild_id} migrée avec succès")
            migrated_count += 1

        logger.info(f"\n🎉 Migration terminée! {migrated_count} guildes migrées")

    except Exception as e:
        logger.error(f"❌ Erreur pendant la migration: {e}", exc_info=True)
    finally:
        await conn.close()
        logger.info("🔌 Connexion fermée")


if __name__ == "__main__":
    asyncio.run(migrate_welcome_configs())
