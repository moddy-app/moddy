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

from utils.emojis import log_emoji

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
# Icons come from utils/emojis.py::LOG_EMOJIS — the server-logs icon set,
# the only one this feature is allowed to draw from (see docs/LOGS.md).
# ---------------------------------------------------------------------------

_CATALOGUE: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    (
        "server", log_emoji("updateserver"),
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
        "messages", log_emoji("dm"),
        (
            "message_delete", "message_bulk_delete", "message_edit",
            "message_publish", "message_command_used",
        ),
    ),
    (
        "users", log_emoji("updatemember"),
        (
            "user_name_update", "user_roles_update", "user_roles_add",
            "user_roles_remove", "user_avatar_update", "user_timed_out",
            "user_timeout_removed",
        ),
    ),
    (
        "moderation", log_emoji("modview"),
        (
            "auto_moderation", "ban_add", "ban_remove", "case_delete",
            "mass_case_delete", "case_update", "kick_add", "kick_remove",
            "mute_add", "mute_remove", "warn_add", "warn_remove",
            "report_create", "reports_ignore", "reports_accept",
            "user_note_add", "user_note_remove",
        ),
    ),
    (
        "channels", log_emoji("channls"),
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
        "roles", log_emoji("roleicon"),
        (
            "role_create", "role_delete", "role_color_update",
            "role_hoist_update", "role_mentionable_update", "role_name_update",
            "role_permissions_update", "role_icon_update",
        ),
    ),
    (
        "threads", log_emoji("updatethread"),
        (
            "thread_create", "thread_delete", "thread_name_update",
            "thread_slowmode_update", "thread_archive_duration_update",
            "thread_archive", "thread_unarchive", "thread_lock",
            "thread_unlock",
        ),
    ),
    (
        "voice", log_emoji("Play"),
        (
            "voice_channel_full", "voice_user_join", "voice_user_switch",
            "voice_user_leave", "voice_user_move", "voice_user_kick",
        ),
    ),
    (
        "invites", log_emoji("links"),
        ("invite_create", "invite_delete", "invite_post"),
    ),
    (
        "automod", log_emoji("Filter"),
        (
            "automod_rule_create", "automod_rule_delete",
            "automod_rule_toggle", "automod_rule_name_update",
            "automod_rule_actions_update", "automod_rule_content_update",
            "automod_rule_roles_update", "automod_rule_channels_update",
            "automod_rule_whitelist_update",
        ),
    ),
    (
        "emojis", log_emoji("emojisslots"),
        (
            "emoji_create", "emoji_delete", "emoji_name_update",
            "emoji_roles_update",
        ),
    ),
    (
        "stickers", log_emoji("stickerscreated"),
        (
            "sticker_create", "sticker_delete", "sticker_name_update",
            "sticker_description_update", "sticker_related_emoji_update",
        ),
    ),
    (
        "soundboard", log_emoji("soundboardadd"),
        (
            "sound_upload", "sound_name_update", "sound_volume_update",
            "sound_emoji_update", "sound_delete",
        ),
    ),
    (
        "events", log_emoji("eventcreated"),
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
        "stage", log_emoji("stagecreated"),
        ("stage_start", "stage_end", "stage_topic_update", "stage_privacy_update"),
    ),
    (
        "polls", log_emoji("questions"),
        (
            "poll_create", "poll_delete", "poll_finalize", "poll_votes_add",
            "poll_votes_remove",
        ),
    ),
    (
        "webhooks", log_emoji("Webhooks"),
        (
            "webhook_create", "webhook_name_update", "webhook_avatar_update",
            "webhook_channel_update", "webhook_delete",
        ),
    ),
    (
        "applications", log_emoji("bot"),
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
# Per-event icons
# ---------------------------------------------------------------------------
#
# Every log carries an icon, taken from the server-logs set in
# ``utils/emojis.py``. Only the events whose act has its own icon are listed
# here — anything else falls back to its category icon, so a new event is
# never iconless and adding a name to this table is optional polish, not a
# step of "adding an event".
#
# Keys are bare event names: the same act keeps the same icon in whichever
# category it is declared (a ban looks like a ban in ``server`` and in
# ``moderation``).

_EVENT_ICONS: Dict[str, str] = {
    # People
    "user_join": "addmember", "user_leave": "removemember", "user_kick": "kick",
    "kick_add": "kick", "kick_remove": "accept", "member_prune": "removemember",
    "ban_add": "ban", "ban_remove": "accept",
    "mute_add": "timedout", "mute_remove": "timeout",
    "user_timed_out": "timedout", "user_timeout_removed": "timeout",
    "warn_add": "Warn", "warn_remove": "accept",
    "user_name_update": "updatemember", "user_avatar_update": "uploadimage",
    "user_roles_add": "addrole", "user_roles_remove": "removeuserfromrole",
    "user_roles_update": "updaterole",
    # Moddy cases
    "auto_moderation": "Filter", "case_update": "Edit",
    "case_delete": "close", "mass_case_delete": "close",
    "report_create": "sendalert", "reports_accept": "accept",
    "reports_ignore": "deny",
    "user_note_add": "addview", "user_note_remove": "close",
    # Messages
    "message_delete": "messageremove", "message_bulk_delete": "messageremove",
    "message_edit": "Edit", "message_publish": "Channelsfollowed",
    "message_command_used": "slash",
    # Channels
    "channel_create": "createchannel", "channel_delete": "deletechannel",
    "channel_permissions_update": "permissions",
    "channel_slowmode_update": "timeout", "channel_nsfw_update": "locked",
    # Roles
    "role_create": "createrole", "role_delete": "removerole",
    "role_color_update": "pickcolor", "role_icon_update": "roleicon1",
    "role_permissions_update": "permissions", "role_name_update": "editrole",
    # Threads
    "thread_create": "createthread", "thread_delete": "removethread",
    "thread_lock": "locked", "thread_unlock": "locked1",
    "thread_archive": "sort", "thread_unarchive": "sort",
    # Voice
    "voice_user_join": "addmember", "voice_user_leave": "removemember",
    "voice_user_switch": "right", "voice_user_move": "right",
    "voice_user_kick": "kick", "voice_channel_full": "locked",
    # Invites
    "invite_create": "invitecreate", "invite_delete": "inviteremove",
    "invite_post": "links",
    # Discord AutoMod
    "automod_rule_create": "addrule", "automod_rule_delete": "deny",
    "automod_rule_name_update": "Edit",
    "automod_rule_actions_update": "Blockmessage",
    "automod_rule_content_update": "Blockcustomwords",
    "automod_rule_roles_update": "addrole",
    "automod_rule_channels_update": "channls",
    "automod_rule_whitelist_update": "grantedperm",
    # Assets
    "emoji_create": "Emojicreate", "emoji_delete": "Emojiremove",
    "emoji_name_update": "Emojiupdate", "emoji_roles_update": "Emojiupdate",
    "sticker_create": "stickerscreated", "sticker_delete": "stickersdeleted",
    "sticker_name_update": "stickersupdated",
    "sticker_description_update": "stickersupdated",
    "sticker_related_emoji_update": "stickersupdated",
    "sound_upload": "soundboardadd", "sound_delete": "soundboardremove",
    "sound_name_update": "soundboardupdate",
    "sound_volume_update": "soundboardupdate",
    "sound_emoji_update": "soundboardupdate",
    # Scheduled events and stage
    "event_create": "eventcreated", "event_delete": "eventdeleted",
    "event_image_update": "uploadimage",
    "event_user_subscribe": "addmember", "event_user_unsubscribe": "removemember",
    "event_name_update": "eventupdated", "event_description_update": "eventupdated",
    "event_location_update": "eventupdated", "event_privacy_level_update": "eventupdated",
    "event_start_time_update": "eventupdated", "event_end_time_update": "eventupdated",
    "event_status_update": "eventupdated",
    "stage_start": "stagecreated", "stage_end": "stageremoved",
    "stage_topic_update": "stageupdated", "stage_privacy_update": "stageupdated",
    # Polls
    "poll_create": "questions", "poll_delete": "deny",
    "poll_finalize": "Checked", "poll_votes_add": "added",
    "poll_votes_remove": "deny",
    # Webhooks and applications
    "webhook_create": "webhookcreate", "webhook_delete": "webhookremove",
    "webhook_name_update": "webhookupdate",
    "webhook_avatar_update": "webhookupdate",
    "webhook_channel_update": "webhookupdate",
    "app_add": "bot", "app_remove": "deny",
    "app_command_permission_update": "commandpremissionupdated",
    # Server
    "server_name_update": "updateserver",
    "server_description_update": "updateserver",
    "server_owner_update": "owner", "server_icon_update": "uploadimage",
    "server_banner_update": "uploadimage", "server_splash_update": "uploadimage",
    "server_discovery_splash_update": "nodiscovery",
    "server_vanity_update": "links", "server_widget_update": "view",
    "server_features_update": "featurecreated",
    "server_boost_level_update": "newtier",
    "boost_progress_bar_toggle": "boostlevelonly",
    "server_content_filter_update": "Filter",
    "verification_level_update": "passedverification",
    "verified_update": "premiumbadge", "partnered_update": "premiumbadge",
    "mfa_level_update": "key",
    "message_notifications_update": "sendalert",
    "server_rules_channel_update": "Rules",
    "public_updates_channel_update": "updatechannel",
    "system_channel_update": "updatechannel",
    "server_preferred_locale_update": "updateserver",
    "afk_channel_update": "updatechannel", "afk_timeout_update": "timeout",
    "onboarding_toggle": "updatehome",
    "onboarding_channels_update": "updatehome",
    "onboarding_question_add": "createhome",
    "onboarding_question_remove": "removehome",
    "onboarding_question_update": "updatehome",
}


def event_emoji(key: str) -> str:
    """Icon of one event — its own if it has one, else its category's."""
    spec = EVENTS.get(key)
    if spec is None:
        return log_emoji(_EVENT_ICONS.get(key, ""))
    icon = _EVENT_ICONS.get(spec.event)
    if icon:
        return log_emoji(icon)
    category = CATEGORIES.get(spec.category)
    return category.emoji if category else ""


# ---------------------------------------------------------------------------
# Merge families — one real act, several registry events
# ---------------------------------------------------------------------------
#
# Discord and Moddy describe the same act in several ways, on purpose: a mute
# applied by Moddy is a case (``mute_add``), a Discord timeout
# (``user_timed_out``) and, because the timeout is what Discord actually
# reports, a second ``mute_add`` mirrored from the gateway. Three true, useful
# events — and three near-identical embeds when a server sends them all to the
# same channel.
#
# A **family** declares "these events describe one act". When the server has
# ``merge_duplicates`` on, the service holds them for a moment and delivers the
# highest-priority one, enriched with whatever the others knew that it did not
# (see :mod:`serverlogs.service`). Priority = how much context the event
# carries: a Moddy case knows the case reference and the moderator, a gateway
# mirror only knows what Discord said.
#
# Only declared families are ever held back. Everything else is dispatched
# straight away — merging two unrelated occurrences that happen to share a
# subject (two messages from the same author deleted in a row) would lose a
# log, which is worse than showing two.

_MERGE_FAMILIES: Dict[str, Tuple[str, int]] = {
    # A mute: Moddy's case, Discord's timeout, and the timeout mirrored back
    # into the moderation category.
    "mute_add": ("member_mute", 30),
    "user_timed_out": ("member_mute", 10),
    "mute_remove": ("member_unmute", 30),
    "user_timeout_removed": ("member_unmute", 10),
    # A ban: Moddy's case and the gateway's own ban event share the key, so
    # the family exists to collapse the two emissions of the same event.
    "ban_add": ("member_ban", 30),
    "ban_remove": ("member_unban", 30),
    # A kick, told once as a server event and once as a moderation one.
    "kick_add": ("member_kick", 30),
    "user_kick": ("member_kick", 20),
    # A warn: only Moddy emits it, but twice if it is re-recorded.
    "warn_add": ("member_warn", 30),
    "warn_remove": ("member_unwarn", 30),
    # Role changes: the combined entry already says everything the two
    # focused ones say.
    "user_roles_update": ("member_roles", 30),
    "user_roles_add": ("member_roles", 20),
    "user_roles_remove": ("member_roles", 10),
}


def merge_family(event: str) -> Optional[Tuple[str, int]]:
    """``(family, priority)`` for a bare event name, or ``None``.

    ``None`` means "never hold this event back" — the overwhelming majority
    of the catalogue.
    """
    return _MERGE_FAMILIES.get(event)


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
