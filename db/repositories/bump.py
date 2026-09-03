"""Live state of the bump reminders — one row per (guild, directory).

The *configuration* of the Bump Reminder module lives in
``guilds.data.modules.bump_reminder`` like every other module. This table holds
the opposite half: what is actually pending right now. Which server bumped
where, who ran the command, and when the next reminder is owed.

Two properties are the point of the shape:

**One row per directory, forever.** The key is ``(guild_id, bot_key)`` and not
the config entry, because a cooldown belongs to the *server*: bumping DISBOARD
once locks the whole server for two hours no matter which channel the command
ran in. A premium server pointing three channels at DISBOARD therefore still
owns one row, and the sweeper fans that one row out to its three channels. So a
server running ``/bump`` twenty times a day owns at most seven rows — nothing
accumulates, nothing needs a cleanup job. And a second bump before the reminder
fired is an upsert that pushes ``due_at`` back and clears ``sent``, which is
precisely the "restart the countdown" behaviour the module promises, obtained
for free rather than coded.

**Nothing lives in memory.** A pending reminder is a row with ``sent = FALSE``
and a ``due_at`` in the future. No timer, no task, no cache — so a restart costs
nothing and loses nothing: the sweeper's next pass picks up whatever came due
while the bot was down.

See docs/BUMP_REMINDER.md.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger('moddy.database')


def _row_to_dict(row) -> Dict[str, Any]:
    return {
        "guild_id": row["guild_id"],
        "bot_key": row["bot_key"],
        "channel_id": row["channel_id"],
        "due_at": row["due_at"],
        "sent": row["sent"],
        "bumper_id": row["bumper_id"],
        "opt_in": row["opt_in"],
        "thanks_channel_id": row["thanks_channel_id"],
        "thanks_message_id": row["thanks_message_id"],
        "bumped_at": row["bumped_at"],
    }


class BumpReminderRepository:
    """Pending bump reminders (``bump_reminders``)."""

    async def record_bump(self, guild_id: int, bot_key: str, *, channel_id: int,
                          due_at: datetime, bumper_id: Optional[int],
                          thanks_channel_id: Optional[int] = None,
                          thanks_message_id: Optional[int] = None) -> None:
        """Arm (or re-arm) the reminder for one directory in one guild.

        Deliberately an upsert: somebody bumping again before the previous
        reminder fired must push the countdown back, not stack a second one.
        ``opt_in`` resets with it — a "ping me next time" armed for the previous
        bump has been honoured or superseded, and carrying it over would ping
        somebody for a bump that is no longer theirs.
        """
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO bump_reminders (
                    guild_id, bot_key, channel_id, due_at, sent, bumper_id,
                    opt_in, thanks_channel_id, thanks_message_id, bumped_at
                ) VALUES ($1, $2, $3, $4, FALSE, $5, FALSE, $6, $7, now())
                ON CONFLICT (guild_id, bot_key) DO UPDATE SET
                    channel_id        = EXCLUDED.channel_id,
                    due_at            = EXCLUDED.due_at,
                    sent              = FALSE,
                    bumper_id         = EXCLUDED.bumper_id,
                    opt_in            = FALSE,
                    thanks_channel_id = EXCLUDED.thanks_channel_id,
                    thanks_message_id = EXCLUDED.thanks_message_id,
                    bumped_at         = now()
            """, guild_id, bot_key, channel_id, due_at, bumper_id,
                 thanks_channel_id, thanks_message_id)

    async def claim_due_bumps(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Take the reminders that have come due, marking them in the same breath.

        The rows flip to ``sent`` inside the statement that returns them, so a
        second sweeper — or this one restarting mid-pass — can never post the
        same reminder twice. ``SKIP LOCKED`` means a row another worker is
        already holding is passed over rather than waited on.
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                UPDATE bump_reminders SET sent = TRUE
                WHERE (guild_id, bot_key) IN (
                    SELECT guild_id, bot_key FROM bump_reminders
                    WHERE sent = FALSE AND due_at <= now()
                    ORDER BY due_at
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING *
            """, limit)
        return [_row_to_dict(row) for row in rows]

    async def get_guild_bump_states(self, guild_id: int) -> Dict[str, Dict[str, Any]]:
        """Every pending/last state of a guild, keyed by directory.

        One query feeds the whole config panel, which is what lets it show a
        live "next reminder <t:…:R>" per entry instead of a dead form.
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM bump_reminders WHERE guild_id = $1", guild_id)
        return {row["bot_key"]: _row_to_dict(row) for row in rows}

    async def set_bump_opt_in(self, guild_id: int, bot_key: str,
                              user_id: int, opt_in: bool) -> bool:
        """Arm or disarm the "mention me next time" flag from the thank-you card.

        Scoped to ``bumper_id`` in the statement itself: if another bump landed
        between the card being posted and the button being clicked, the click
        belongs to a bump that is over and quietly does nothing.
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE bump_reminders SET opt_in = $4
                WHERE guild_id = $1 AND bot_key = $2 AND bumper_id = $3
                RETURNING opt_in
            """, guild_id, bot_key, user_id, opt_in)
        return row is not None

    async def drop_bump_reminder(self, guild_id: int, bot_key: str) -> None:
        """Forget one directory — the reminder was deleted from the config."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM bump_reminders WHERE guild_id = $1 AND bot_key = $2",
                guild_id, bot_key)

    async def drop_guild_bump_reminders(self, guild_id: int) -> None:
        """Forget a whole guild — the module was disabled or reset."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM bump_reminders WHERE guild_id = $1", guild_id)
