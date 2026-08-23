"""Single source of truth for the advanced server logs.

Every loggable event in Moddy is declared **once**, here. Everything else —
the ``/config`` panel, the stored configuration, the dispatcher and the
listeners — reads this registry instead of hardcoding its own list.

Adding a new event is therefore a three-step change:

1. add its key to the relevant :data:`CATEGORIES` entry,
2. add the two i18n strings (``modules.logs.events.<cat>.<event>`` for the
   short name shown in ``/config`` and ``modules.logs.titles.<cat>.<event>``
   for the embed title),
3. emit it from a listener in :mod:`serverlogs.listeners`.

Nothing else has to be touched: the configuration UI paginates itself, the
module validates unknown keys away, and the dispatcher routes on the key.

Event keys are namespaced ``"<category>.<event>"``. The same real-world
occurrence may legitimately exist in two categories (a ban is both a
``server`` event and a ``moderation`` event) — they are two distinct keys
with two distinct destinations, exactly like Sapphire and Dyno do it, so a
server can send bans to #server-logs, to #mod-logs, or to both.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from utils.emojis import (
    BALANCE, COMMANDS, EMOJI, FILTER, FOLDERS, GROUPS, IMAGE, LINK,
    MANAGE_USER, MESSAGE, MIC_OFF, PLAY, SHIELD, TEXT, TIME, USER,
    VOICE_CHAT, WEBHOOK,
)

# ---------------------------------------------------------------------------
# Colour "kind" of an event — drives the embed accent so a log channel reads
# at a glance: green = something appeared, red = something disappeared,
# blurple = something changed, orange = a moderator acted.
# ---------------------------------------------------------------------------

KIND_CREATE = "create"
KIND_DELETE = "delete"
KIND_UPDATE = "update"
KIND_MODERATION = "moderation"

# Suffix/prefix inference so 160+ events don't each need a hand-written kind.
# Checked in order, on the event key (without its category prefix).
_KIND_TOKENS: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("create", "add", "join", "start", "upload", "subscribe", "post", "publish"), KIND_CREATE),
    (("delete", "remove", "leave", "end", "kick", "unsubscribe", "prune"), KIND_DELETE),
)

# Events whose name would infer the wrong colour, or that belong to the
# moderation palette regardless of what they do.
_KIND_OVERRIDES: Dict[str, str] = {
    "server.ban_add": KIND_MODERATION,
    "server.ban_remove": KIND_MODERATION,
    "server.user_kick": KIND_MODERATION,
    "server.member_prune": KIND_MODERATION,
    "users.user_timed_out": KIND_MODERATION,
    "users.user_timeout_removed": KIND_MODERATION,
    "voice.voice_user_kick": KIND_MODERATION,
    "messages.message_delete": KIND_DELETE,
    "messages.message_bulk_delete": KIND_DELETE,
    "threads.thread_archive": KIND_UPDATE,
    "threads.thread_unarchive": KIND_UPDATE,
    "threads.thread_lock": KIND_UPDATE,
    "threads.thread_unlock": KIND_UPDATE,
    "polls.poll_finalize": KIND_UPDATE,
    "voice.voice_channel_full": KIND_UPDATE,
    "voice.voice_user_switch": KIND_UPDATE,
    "voice.voice_user_move": KIND_MODERATION,
    "invites.invite_post": KIND_UPDATE,
    "automod.automod_rule_toggle": KIND_UPDATE,
    "server.onboarding_toggle": KIND_UPDATE,
}


def _infer_kind(key: str, event: str) -> str:
    override = _KIND_OVERRIDES.get(key)
    if override:
        return override
    if key.split(".", 1)[0] == "moderation":
        return KIND_MODERATION
    for tokens, kind in _KIND_TOKENS:
        for token in tokens:
            if event.endswith(f"_{token}") or event.startswith(f"{token}_") or event == token:
                return kind
    return KIND_UPDATE


@dataclass(frozen=True)
class LogEventSpec:
    """One loggable event."""

    key: str          # "server.user_join"
    category: str     # "server"
    event: str        # "user_join"
    kind: str         # KIND_* — the embed accent colour

    @property
    def name_key(self) -> str:
        """i18n key of the short name shown in the /config picker."""
        return f"modules.logs.events.{self.category}.{self.event}"

    @property
    def title_key(self) -> str:
        """i18n key of the sentence used as the embed title."""
        return f"modules.logs.titles.{self.category}.{self.event}"


@dataclass(frozen=True)
class LogCategorySpec:
    """One category of events — the unit a channel is bound to."""

    id: str
    emoji: str
    order: int
    events: Tuple[str, ...]

    @property
    def name_key(self) -> str:
        return f"modules.logs.categories.{self.id}.name"

    @property
    def description_key(self) -> str:
        return f"modules.logs.categories.{self.id}.description"

    def event_key(self, event: str) -> str:
        return f"{self.id}.{event}"


# ---------------------------------------------------------------------------
# The catalogue. Order inside a category is the order shown in /config.
# Emojis come from utils/emojis.py (custom emojis only — CLAUDE.md rule 3).
# ---------------------------------------------------------------------------

_CATALOGUE: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    (
        "server", GROUPS,
        (
            "ban_add", "ban_remove", "user_join", "user_leave", "user_kick",
            "member_prune", "afk_channel_update", "afk_timeout_update",
            "server_banner_update", "message_notifications_update",
            "server_discovery_splash_update", "server_content_filter_update",
            "server_features_update", "server_icon_update", "mfa_level_update",
            "server_name_update", "server_description_update",
            "server_owner_update", "partnered_update",
            "server_boost_level_update", "boost_progress_bar_toggle",
            "public_updates_channel_update", "server_rules_channel_update",
            "server_splash_update", "system_channel_update",
            "server_vanity_update", "verification_level_update",
            "verified_update", "server_widget_update",
            "server_preferred_locale_update", "onboarding_toggle",
            "onboarding_channels_update", "onboarding_question_add",
            "onboarding_question_remove", "onboarding_question_update",
        ),
    ),
    (
        "messages", MESSAGE,
        (
            "message_delete", "message_bulk_delete", "message_edit",
            "message_publish", "message_command_used",
        ),
    ),
    (
        "users", USER,
        (
            "user_name_update", "user_roles_update", "user_roles_add",
            "user_roles_remove", "user_avatar_update", "user_timed_out",
            "user_timeout_removed",
        ),
    ),
    (
        "moderation", SHIELD,
        (
            "auto_moderation", "ban_add", "ban_remove", "case_delete",
            "mass_case_delete", "case_update", "kick_add", "kick_remove",
            "mute_add", "mute_remove", "warn_add", "warn_remove",
            "report_create", "reports_ignore", "reports_accept",
            "user_note_add", "user_note_remove",
        ),
    ),
    (
        "channels", TEXT,
        (
            "channel_create", "channel_delete", "channel_pins_update",
            "channel_name_update", "channel_topic_update",
            "channel_nsfw_update", "channel_parent_update",
            "channel_permissions_update", "channel_type_update",
            "channel_bitrate_update", "channel_user_limit_update",
            "channel_slowmode_update", "channel_rtc_region_update",
            "channel_video_quality_update",
            "channel_default_archive_duration_update",
            "channel_default_thread_slowmode_update",
            "channel_default_reaction_emoji_update",
            "channel_default_sort_order_update", "channel_forum_tags_update",
            "channel_forum_layout_update", "channel_voice_status_update",
        ),
    ),
    (
        "roles", MANAGE_USER,
        (
            "role_create", "role_delete", "role_color_update",
            "role_hoist_update", "role_mentionable_update", "role_name_update",
            "role_permissions_update", "role_icon_update",
        ),
    ),
    (
        "threads", FOLDERS,
        (
            "thread_create", "thread_delete", "thread_name_update",
            "thread_slowmode_update", "thread_archive_duration_update",
            "thread_archive", "thread_unarchive", "thread_lock",
            "thread_unlock",
        ),
    ),
    (
        "voice", VOICE_CHAT,
        (
            "voice_channel_full", "voice_user_join", "voice_user_switch",
            "voice_user_leave", "voice_user_move", "voice_user_kick",
        ),
    ),
    (
        "invites", LINK,
        ("invite_create", "invite_delete", "invite_post"),
    ),
    (
        "automod", FILTER,
        (
            "automod_rule_create", "automod_rule_delete",
            "automod_rule_toggle", "automod_rule_name_update",
            "automod_rule_actions_update", "automod_rule_content_update",
            "automod_rule_roles_update", "automod_rule_channels_update",
            "automod_rule_whitelist_update",
        ),
    ),
    (
        "emojis", EMOJI,
        (
            "emoji_create", "emoji_delete", "emoji_name_update",
            "emoji_roles_update",
        ),
    ),
    (
        "stickers", IMAGE,
        (
            "sticker_create", "sticker_delete", "sticker_name_update",
            "sticker_description_update", "sticker_related_emoji_update",
        ),
    ),
    (
        "soundboard", PLAY,
        (
            "sound_upload", "sound_name_update", "sound_volume_update",
            "sound_emoji_update", "sound_delete",
        ),
    ),
    (
        "events", TIME,
        (
            "event_create", "event_delete", "event_name_update",
            "event_description_update", "event_location_update",
            "event_privacy_level_update", "event_start_time_update",
            "event_end_time_update", "event_status_update",
            "event_image_update", "event_user_subscribe",
            "event_user_unsubscribe",
        ),
    ),
    (
        "stage", MIC_OFF,
        ("stage_start", "stage_end", "stage_topic_update", "stage_privacy_update"),
    ),
    (
        "polls", BALANCE,
        (
            "poll_create", "poll_delete", "poll_finalize", "poll_votes_add",
            "poll_votes_remove",
        ),
    ),
    (
        "webhooks", WEBHOOK,
        (
            "webhook_create", "webhook_name_update", "webhook_avatar_update",
            "webhook_channel_update", "webhook_delete",
        ),
    ),
    (
        "applications", COMMANDS,
        ("app_add", "app_remove", "app_command_permission_update"),
    ),
)


CATEGORIES: Dict[str, LogCategorySpec] = {
    cat_id: LogCategorySpec(id=cat_id, emoji=emoji, order=index * 10, events=events)
    for index, (cat_id, emoji, events) in enumerate(_CATALOGUE)
}

EVENTS: Dict[str, LogEventSpec] = {
    f"{cat.id}.{event}": LogEventSpec(
        key=f"{cat.id}.{event}",
        category=cat.id,
        event=event,
        kind=_infer_kind(f"{cat.id}.{event}", event),
    )
    for cat in CATEGORIES.values()
    for event in cat.events
}


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def ordered_categories() -> List[LogCategorySpec]:
    """Categories in the order they are shown in ``/config``."""
    return sorted(CATEGORIES.values(), key=lambda c: c.order)


def get_category(category_id: str) -> Optional[LogCategorySpec]:
    return CATEGORIES.get(category_id)


def get_event(key: str) -> Optional[LogEventSpec]:
    return EVENTS.get(key)


def category_events(category_id: str) -> Tuple[str, ...]:
    """Event keys (``"<cat>.<event>"``) of one category, in display order."""
    category = CATEGORIES.get(category_id)
    if not category:
        return ()
    return tuple(f"{category_id}.{event}" for event in category.events)


def keys_for(event: str) -> Tuple[str, ...]:
    """Every registry key matching a bare event name.

    A single occurrence can feed several categories (``ban_add`` lives in
    both ``server`` and ``moderation``); listeners emit the bare name and the
    dispatcher fans it out to every category that declares it.
    """
    return tuple(key for key, spec in EVENTS.items() if spec.event == event)
