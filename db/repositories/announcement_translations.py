"""Stored translations of the announcements posted in the support server.

An announcement is translated **once**, when it is posted: DeepL is called one
time per language and the whole set is written here, keyed by the announcement's
message id. Every later button click is a single indexed read — clicking the
five flags twenty times costs zero API calls, which is the entire point of
storing this rather than translating on demand.

The row is the only state the buttons have: they are ``DynamicItem``s carrying
the message id in their ``custom_id``, so a restart changes nothing.

See docs/ANNOUNCEMENT_TRANSLATION.md.
"""

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger('moddy.database')


class AnnouncementTranslationRepository:
    """Announcement translations (``announcement_translations``)."""

    async def save_announcement_translations(
        self,
        message_id: int,
        *,
        guild_id: int,
        channel_id: int,
        source_lang: Optional[str],
        translations: Dict[str, str],
    ) -> None:
        """Store the full translation set of one announcement.

        Upsert rather than insert: re-running the translation of an announcement
        (a manual re-post, an edit handled later) must replace the set, never
        stack a second one.
        """
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO announcement_translations (
                    message_id, guild_id, channel_id, source_lang, translations
                ) VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (message_id) DO UPDATE SET
                    guild_id     = EXCLUDED.guild_id,
                    channel_id   = EXCLUDED.channel_id,
                    source_lang  = EXCLUDED.source_lang,
                    translations = EXCLUDED.translations,
                    created_at   = now()
            """, message_id, guild_id, channel_id, source_lang,
                 json.dumps(translations))

    async def get_announcement_translations(
        self, message_id: int
    ) -> Optional[Dict[str, Any]]:
        """Read back one announcement's translations, or ``None`` if unknown."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM announcement_translations WHERE message_id = $1",
                message_id)
        if not row:
            return None
        return {
            "message_id": row["message_id"],
            "guild_id": row["guild_id"],
            "channel_id": row["channel_id"],
            "source_lang": row["source_lang"],
            "translations": self._parse_jsonb(row["translations"]),
            "created_at": row["created_at"],
        }
