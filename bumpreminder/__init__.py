"""Bump reminder — the pure detection core.

A *bump* is a slash command offered by Discord server directories (DISBOARD,
DiscordL, French.gg…) that pushes a server back to the top of a listing. It is
rate limited: one bump every one to four hours depending on the directory. The
window is easy to miss, which is the whole reason this module exists.

Everything in this package is **pure Python decision code**: it reads a message
and answers "did a bump just succeed, and when is the next one due?". It never
sends anything, never touches the database, and never imports anything from the
bot. That is what lets the whole detection surface be tested from the raw
message payloads captured from each directory.

The Discord-facing halves live elsewhere:

- ``modules/bump_reminder.py`` — the per-guild module and its config schema
- ``cogs/bump_reminder.py``    — the listener and the 30s sweeper loop
- ``utils/bump_views.py``      — the thank-you and reminder cards

See docs/BUMP_REMINDER.md.
"""

from bumpreminder.registry import (
    BUMP_BOTS,
    BumpBot,
    bot_by_app_id,
    bot_by_key,
    is_bump_bot,
)
from bumpreminder.detect import (
    BumpHit,
    MAX_INTERVAL,
    MIN_INTERVAL,
    detect,
    evaluate,
    format_interval,
    parse_interval,
)

__all__ = [
    "BUMP_BOTS",
    "BumpBot",
    "BumpHit",
    "MAX_INTERVAL",
    "MIN_INTERVAL",
    "bot_by_app_id",
    "bot_by_key",
    "detect",
    "evaluate",
    "format_interval",
    "is_bump_bot",
    "parse_interval",
]
