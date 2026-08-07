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
    from cogs.preferences import PreferencesView
    from cogs.reminder import RemindersManageView
    from cogs.saved_messages import SavedMessagesLibraryView
    from modules.configs.social_notifications_config import (
        SocialNotificationsConfigView, AddSubscriptionView, ManageSubscriptionView,
    )
    from modules.configs.interserver_config import InterServerConfigView
    from modules.configs.auto_role_config import AutoRoleConfigView
    from modules.configs.auto_restore_roles_config import AutoRestoreRolesConfigView
    from modules.configs.starboard_config import StarboardConfigView
    from modules.configs.welcome_channel_config import WelcomeChannelConfigView
    from modules.configs.welcome_dm_config import WelcomeDmConfigView
    from cogs.config import ConfigMainView
    from modules.configs.adaptive_slowmode_config import AdaptiveSlowmodeConfigView
    from modules.configs.automod_ai_config import AutomodAIConfigView
    from staff.commands.team.help import HelpView
    from staff.commands.dev.serverlist import ServerListView
    from staff.commands.manage.staff import StaffManagerPanel
    from utils.cases_views import CasesBrowserView
    from utils.appeal_views import AppealPersistence
    from utils.automod_shadow_views import ShadowAnnotationPersistence

    return [
        # Group 1 — /moddy (public informational, no user auth)
        ModdyMainView,
        AttributionView,
        WeSupportView,
        # Group 2 — /config module panels (guild permission auth)
        SocialNotificationsConfigView,
        AddSubscriptionView,
        ManageSubscriptionView,
        # Group 3 — /cases & /mycases (per-mode auth)
        CasesBrowserView,
        # Group 4 — automod sanction appeals (dynamic items)
        AppealPersistence,
        # Group 5 — automod shadow-mode annotation buttons (dynamic items)
        ShadowAnnotationPersistence,
        # Group 6 — /preferences (owner-only dynamic items)
        PreferencesView,
        # Group 7 — /reminders manage (owner-only dynamic items)
        RemindersManageView,
        # Group 8 — /library (owner-only dynamic items)
        SavedMessagesLibraryView,
        # Group 9 — /config small guild panels (guild permission auth)
        InterServerConfigView,
        AutoRoleConfigView,
        AutoRestoreRolesConfigView,
        StarboardConfigView,
        # Group 10 — /config welcome pair (colliding custom_ids, must move together)
        WelcomeChannelConfigView,
        WelcomeDmConfigView,
        # Group 11 — /config router (guild permission auth)
        ConfigMainView,
        # Group 12 — /config adaptive slowmode panel (guild permission +
        # channel-scoped dynamic items; AdaptiveSlowmodeChannelConfigView
        # is deliberately excluded, see docs/PERSISTENT_VIEWS.md Step 11)
        AdaptiveSlowmodeConfigView,
        # Group 13 — /config automod AI panel (guild permission auth;
        # AutomodAIPrecedentsView is deliberately excluded, see
        # docs/PERSISTENT_VIEWS.md Step 12)
        AutomodAIConfigView,
        # Group 14 — staff read-only views (owner-scoped dynamic items;
        # EmojiPreviewView is deliberately excluded — "Non-persistent...
        # Temporary by design" per its own docstring)
        HelpView,
        ServerListView,
        # Group 15 — /manage staff panel (owner-scoped dynamic items).
        # HIGH PRIVILEGE — grants/revokes staff roles. See
        # docs/PERSISTENT_VIEWS.md Step 15: requires human review before merge.
        StaffManagerPanel,
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
