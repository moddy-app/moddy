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
    from modules.starboard import StarboardCardPersistence
    from modules.configs.welcome_channel_config import (
        WelcomeChannelConfigView, AddWelcomeMessageView, ManageWelcomeMessageView,
    )
    from modules.configs.welcome_dm_config import (
        WelcomeDmConfigView, ManageWelcomeDmView,
    )
    from cogs.config import ConfigMainView
    from modules.configs.server_settings_config import ServerSettingsConfigView
    from modules.configs.adaptive_slowmode_config import AdaptiveSlowmodeConfigView
    from modules.configs.automod_ai_config import AutomodAIConfigView
    from staff.commands.team.help import HelpView
    from staff.commands.dev.serverlist import ServerListView
    from staff.commands.manage.staff import StaffManagerPanel
    from utils.cases_views import CasesBrowserView
    from utils.appeal_views import AppealPersistence
    from utils.global_sanction_views import GlobalSanctionPersistence
    from utils.automod_shadow_views import ShadowAnnotationPersistence
    from utils.transcription_views import TranscriptionPersistence
    from utils.brocoli_views import BrocoliDecisionPersistence
    from modules.configs.voice_transcription_config import VoiceTranscriptionConfigView
    from modules.configs.bot_customization_config import BotCustomizationConfigView
    from modules.configs.altguard_config import AltGuardConfigView
    from modules.configs.logs_config import (
        LogsConfigView, LogsOptionsView, LogsPersistence,
    )
    from utils.altguard_views import AltGuardPanelView
    from modules.configs.tickets_config import TicketsConfigView
    from modules.configs.tickets_category_config import TicketsConfigPersistence
    from utils.ticket_views import (
        TicketControlView, TicketClosedView, TicketCloseRequestView,
        TicketEscalationView, TicketEscalateConfirmView, TicketsPersistence,
    )
    from utils.notification_views import NotificationsPersistence
    from utils.support_request_views import SupportPersistence
    from utils.beta_announcement import BetaPersistence

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
        # Group 4bis — global sanction staff actions (dynamic items)
        GlobalSanctionPersistence,
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
        # Group 9b — starboard card reactors button (dynamic items; the
        # card message itself carries a real discord.Embed, see
        # modules/starboard.py's CLAUDE.md exception note)
        StarboardCardPersistence,
        # Group 10 — /config welcome panels. The channel module owns three
        # views (list / add / manage), like Social Notifications; the DM
        # module owns two (list / manage — "add" is the modal itself).
        WelcomeChannelConfigView,
        AddWelcomeMessageView,
        ManageWelcomeMessageView,
        WelcomeDmConfigView,
        ManageWelcomeDmView,
        # Group 11 — /config router (guild permission auth) and the
        # server-wide settings screen it opens (language of the server —
        # see utils/guild_language.py)
        ConfigMainView,
        ServerSettingsConfigView,
        # Group 12 — /config adaptive slowmode panel (guild permission +
        # channel-scoped dynamic items; AdaptiveSlowmodeChannelConfigView
        # is deliberately excluded, see docs/PERSISTENT_VIEWS.md Step 11)
        AdaptiveSlowmodeConfigView,
        # Group 12b — /config voice transcription panel (guild permission auth)
        VoiceTranscriptionConfigView,
        # Group 12c — the "Transcribe" button posted under voice messages
        # (dynamic item, public: anyone who sees the voice message may click)
        TranscriptionPersistence,
        BrocoliDecisionPersistence,
        # Group 12d — /config bot customization panel (guild permission auth;
        # its two modals are excluded like every other modal)
        BotCustomizationConfigView,
        # Group 12e — /config AltGuard panel (guild permission auth) and the
        # verification panel posted in the guild's verification channel
        # (public: whoever clicks is the member being verified; its consent
        # modal is excluded like every other modal)
        AltGuardConfigView,
        AltGuardPanelView,
        # Group 12f — /config server logs: the root and options panels
        # (guild permission auth) plus the category panel's dynamic items,
        # which carry the category and page in their custom_id.
        # LogsCategoryView itself is deliberately not registered — see its
        # docstring and docs/PERSISTENT_VIEWS.md "Deliberate exclusions".
        LogsConfigView,
        LogsOptionsView,
        LogsPersistence,
        # Group 12g — Tickets. The /config root panel is a normal registered
        # view (guild permission auth); the panel/category/permission screens
        # are built from dynamic items carrying the entity ids, registered by
        # TicketsConfigPersistence — see docs/PERSISTENT_VIEWS.md "Deliberate
        # exclusions". The ticket-channel views need no id at all: the channel
        # a click comes from IS the ticket.
        TicketsConfigView,
        TicketsConfigPersistence,
        TicketControlView,
        TicketClosedView,
        TicketCloseRequestView,
        TicketEscalationView,
        TicketEscalateConfirmView,
        # Group 12h — the public ticket panel's open buttons / dropdown
        # (dynamic items; public: whoever clicks is the member opening).
        TicketsPersistence,
        # Group 12i — the attribution + report buttons carried by every
        # notification Moddy sends, and the staff review panel they feed
        # (dynamic items keyed by the notification / report uuid).
        NotificationsPersistence,
        # Group 12j — support requests: the staff card's Claim/Reply/Resolve,
        # the reporter's Reply button on the DM, and the "Configure it for me"
        # entry point Moddy puts under its own announcements (dynamic items
        # keyed by the request uuid; the entry point carries no owner — the
        # clicker IS the requester).
        SupportPersistence,
        # Group 12k — the beta-launch announcement's Translate button
        # (dynamic item keyed by the notification uuid; public — it only ever
        # re-renders the DM its reader already has). Temporary: goes away with
        # utils/beta_announcement.py when the campaign is over.
        BetaPersistence,
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
