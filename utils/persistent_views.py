"""
Persistent view registry for Moddy.

This module is the single place where every ``BaseView`` subclass that must
survive a bot restart is registered. It is called once in
``bot.setup_hook`` after all cogs have been loaded.

See ``docs/PERSISTENT_VIEWS.md`` for the full pattern, custom_id convention,
and a cookbook for adding a new persistent view.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List, Type

if TYPE_CHECKING:
    from cogs.error_handler import BaseView
    from bot import ModdyBot

logger = logging.getLogger('moddy.persistent_views')


def _collect_persistent_view_classes() -> List[Type["BaseView"]]:
    """
    Explicit registry of persistent view classes.

    New persistent views are added here by hand (no auto-discovery) so the
    full list of what survives a restart is always visible in one place and
    trivially auditable in a PR diff.
    """
    # Imported lazily so this module can be imported before cogs are loaded.
    from cogs.moddy import ModdyMainView, AttributionView, WeSupportView

    return [
        # Group 1 — /moddy (public informational, no user auth)
        ModdyMainView,
        AttributionView,
        WeSupportView,
    ]


def register_all_persistent_views(bot: "ModdyBot") -> None:
    """
    Instantiate and register every persistent view class.

    Each class is expected to:
    - Have ``__persistent__ = True``
    - Implement ``register_persistent(bot)`` which calls
      ``bot.add_view(cls())`` and/or ``bot.add_dynamic_items(...)``.

    Failures are logged but do not abort bot startup — a broken persistent
    view should never take the entire bot down.
    """
    classes = _collect_persistent_view_classes()
    logger.info(f"Registering {len(classes)} persistent view classes...")

    registered = 0
    for cls in classes:
        if not getattr(cls, '__persistent__', False):
            logger.warning(
                f"{cls.__name__} is in the persistent view registry but its "
                f"__persistent__ attribute is False — skipping."
            )
            continue
        try:
            cls.register_persistent(bot)
            registered += 1
            logger.debug(f"Registered persistent view: {cls.__name__}")
        except Exception as e:
            logger.error(
                f"Failed to register persistent view {cls.__name__}: {e}",
                exc_info=True,
            )

    logger.info(f"Persistent views registered ({registered}/{len(classes)})")
