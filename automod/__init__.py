"""
Moddy AI Automod — message processing pipeline.

Strict scope: take a message, run it through the funnel, and — when needed —
produce a moderation :class:`Decision`. This package **decides**; it never
applies sanctions, never writes to a database, never executes anything. The
caller (``modules/automod.py``) consumes the :class:`Decision` and acts on it.

Funnel order (see ``docs/AUTOMOD.md``):

    1. pre-filter        (bot / system / empty -> STOP)
    2. trivial allowlist ("ok", "mdr", "gg"…    -> STOP)
    3. regex blocklist   (match -> nano, source=regex)
    4. embedding         (score >= threshold -> nano, source=embedding;
                          else STOP)
    5. nano              (the ONLY decider)

Everything external (embeddings + nano chat) goes through ``bot.gateway`` so
quotas, resilience and logging are enforced centrally. No provider SDK is ever
imported here.

The package is intentionally **scalable**: the funnel above is the
``content`` detector (insults / problematic messages). Future detectors
(anti-link, anti-invite, anti-spam, anti-raid) plug in as additional automod
*features* in ``modules/automod.py`` without touching this package.
"""

from .schemas import (
    Signal,
    Decision,
    TargetMessage,
    ContextMessage,
    AuthorHistory,
    BlocklistEntry,
)
from .engine import AutomodEngine, get_engine
from . import constants

__all__ = [
    "Signal",
    "Decision",
    "TargetMessage",
    "ContextMessage",
    "AuthorHistory",
    "BlocklistEntry",
    "AutomodEngine",
    "get_engine",
    "constants",
]
