"""
Social Notifications module.

Posts a Discord notification whenever a followed social account publishes
something new (YouTube video, Twitch live, Bluesky post, RSS article, …).

Architecture
------------
Unlike most modules, the configuration is NOT stored in ``guilds.data.modules``
(JSONB). A single social target is shared by many guilds, and incoming events
must be dispatched by a fast reverse lookup (target -> guilds). That mapping
lives in the dedicated ``social_subscriptions`` table (see db/repositories/social.py).

This ``ModuleBase`` subclass therefore only exists so the module appears in the
``/config`` menu; the real logic lives in:
  - ``cogs/social_notifications.py``        (wiring, dispatch, service commands)
  - ``modules/configs/social_notifications_config.py`` (config UI)
  - ``services/feeds_client.py``            (Redis transport)

Polling intervals
-----------------
The external service clamps the requested ``poll_interval`` to each platform's
bounds. We deliberately request a FAST interval for premium guilds and a
slower-but-reasonable one for free guilds. These values MUST be mirrored in the
backend so both sides agree — see docs/SOCIAL_NOTIFICATIONS.md.
"""

import logging
import time
from typing import Any, Dict, List, Optional

import discord
from discord import ui

from modules.module_manager import ModuleBase
from utils.emojis import (
    SOCIAL, get_platform_emoji,
    YOUTUBE, TWITCH, BLUESKY, RSS, INSTAGRAM,
)
from utils.i18n import t

logger = logging.getLogger('moddy.modules.social_notifications')


# --------------------------------------------------------------------------- #
# Platform catalogue
# --------------------------------------------------------------------------- #
# ``realtime`` platforms ignore poll_interval (the service pushes in real time).
# ``disabled`` platforms are not yet available on the service side.
# ``avatar`` / ``media`` describe which optional visuals a platform can provide:
#   - ``avatar``: the author profile picture (rendered as a Section thumbnail),
#   - ``media``:  a large preview/cover (rendered as a MediaGallery).
# These gate the two "display option" checkboxes in the config modal.
# ``color`` is the platform's brand colour, used as the notification accent
# (the coloured bar on the left of the Components V2 container) by default.
PLATFORMS: Dict[str, Dict[str, Any]] = {
    "youtube": {"realtime": False, "disabled": False, "color": 0xFF0000, "avatar": True, "media": True},
    "twitch": {"realtime": False, "disabled": False, "color": 0x9146FF, "avatar": True, "media": True},
    "bluesky": {"realtime": True, "disabled": False, "color": 0x1185FE, "avatar": True, "media": False},
    "rss": {"realtime": False, "disabled": False, "color": 0xEE802F, "avatar": False, "media": False},
    "instagram": {"realtime": False, "disabled": True, "color": 0xE1306C, "avatar": True, "media": True},
}

# Order shown in the platform picker.
SUPPORTED_PLATFORMS: List[str] = ["youtube", "twitch", "bluesky", "rss", "instagram"]

# Requested poll interval (seconds) per platform, by tier.
# premium = fastest the platform allows; free = slower but still reasonable.
# Realtime platforms (bluesky) are omitted — interval is ignored by the service.
# >>> KEEP IN SYNC WITH THE BACKEND <<<
POLL_INTERVALS: Dict[str, Dict[str, int]] = {
    "youtube": {"premium": 60, "free": 300},
    "twitch": {"premium": 30, "free": 120},
    "rss": {"premium": 120, "free": 600},
    "instagram": {"premium": 600, "free": 1800},
}

# Supported message-template placeholders (all platforms understand these,
# but some values may be empty on platforms that don't expose them — see
# ``PLATFORM_PLACEHOLDERS`` for the per-platform availability shown in the UI).
MESSAGE_PLACEHOLDERS = ("{author}", "{title}", "{url}", "{link}", "{platform}", "{timestamp}")

# Placeholders advertised in the customization modal, per platform. ``{timestamp}``
# is always available (it falls back to the dispatch time). ``{url}`` and
# ``{platform}`` are always available too.
PLATFORM_PLACEHOLDERS: Dict[str, List[str]] = {
    "youtube": ["{author}", "{title}", "{url}", "{platform}", "{timestamp}"],
    "twitch": ["{author}", "{title}", "{url}", "{platform}", "{timestamp}"],
    "bluesky": ["{author}", "{url}", "{platform}", "{timestamp}"],
    "rss": ["{title}", "{url}", "{platform}", "{timestamp}"],
    "instagram": ["{author}", "{url}", "{platform}", "{timestamp}"],
}

MAX_MESSAGE_LENGTH = 1500


# Default message templates (English). The title is part of the message itself
# (``##`` heading with the platform emoji) so the whole notification is fully
# customizable. ``str`` concatenation keeps the ``{placeholder}`` braces literal
# while still interpolating the emoji constants.
DEFAULT_MESSAGES: Dict[str, str] = {
    "youtube": (
        "## " + YOUTUBE + " New video!\n"
        "{author} just posted a new video on {platform}: « {title} »\n"
        "{url}\n"
        "-# <t:{timestamp}:R>"
    ),
    "twitch": (
        "## " + TWITCH + " Live now!\n"
        "{author} is now live on {platform}: « {title} »\n"
        "{url}\n"
        "-# <t:{timestamp}:R>"
    ),
    "bluesky": (
        "## " + BLUESKY + " New post!\n"
        "{author} just posted on {platform}.\n"
        "{url}\n"
        "-# <t:{timestamp}:R>"
    ),
    "rss": (
        "## " + RSS + " New article!\n"
        "A new article was published: « {title} »\n"
        "{url}\n"
        "-# <t:{timestamp}:R>"
    ),
    "instagram": (
        "## " + INSTAGRAM + " New post!\n"
        "{author} just shared a new post on {platform}.\n"
        "{url}\n"
        "-# <t:{timestamp}:R>"
    ),
}


def is_realtime(platform: str) -> bool:
    return PLATFORMS.get(platform, {}).get("realtime", False)


def is_platform_disabled(platform: str) -> bool:
    return PLATFORMS.get(platform, {}).get("disabled", False)


def platform_color(platform: str) -> int:
    """Brand colour used as the notification accent for a platform."""
    return PLATFORMS.get(platform, {}).get("color", 0x5865F2)


def supports_avatar(platform: str) -> bool:
    """Whether the platform exposes an author avatar (pp as a thumbnail)."""
    return PLATFORMS.get(platform, {}).get("avatar", False)


def supports_media(platform: str) -> bool:
    """Whether the platform exposes a large preview/cover (media gallery)."""
    return PLATFORMS.get(platform, {}).get("media", False)


def get_default_message(platform: str) -> str:
    """Pre-filled message template shown when a subscription has none."""
    return DEFAULT_MESSAGES.get(platform, DEFAULT_MESSAGES["rss"])


def platform_placeholders(platform: str) -> List[str]:
    """Placeholders advertised in the customization modal for a platform."""
    return PLATFORM_PLACEHOLDERS.get(platform, ["{title}", "{url}", "{platform}", "{timestamp}"])


def desired_poll_interval(platform: str, is_premium: bool) -> Optional[int]:
    """Poll interval the bot should request for a guild following ``platform``.

    Returns ``None`` for realtime/unknown platforms (interval omitted).
    """
    if is_realtime(platform):
        return None
    cfg = POLL_INTERVALS.get(platform)
    if not cfg:
        return None
    return cfg["premium"] if is_premium else cfg["free"]


class SocialNotificationsModule(ModuleBase):
    """Registration shell so the module appears in ``/config`` (table-backed)."""

    MODULE_ID = "social_notifications"
    MODULE_NAME = "Social Notifications"
    MODULE_DESCRIPTION = "Get notified when social accounts post new content"
    MODULE_EMOJI = SOCIAL

    async def load_config(self, config_data: Dict[str, Any]) -> bool:
        # Configuration lives in the social_subscriptions table, not JSONB.
        return True

    async def validate_config(self, config_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        return True, None

    def get_default_config(self) -> Dict[str, Any]:
        return {}


# --------------------------------------------------------------------------- #
# Notification rendering (Components V2)
# --------------------------------------------------------------------------- #
def _resolve_timestamp(event: Dict[str, Any]) -> int:
    """Best-effort unix timestamp (seconds) for the ``{timestamp}`` placeholder.

    Falls back to the dispatch time when the event carries no usable date.
    """
    for key in ("timestamp", "published_at", "published", "created_at"):
        value = event.get(key)
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return int(time.time())


def _format_message_template(template: str, event: Dict[str, Any]) -> str:
    """Substitute supported placeholders inside a message template."""
    url = event.get("url", "") or ""
    replacements = {
        "{author}": event.get("author_name", "") or "",
        "{title}": event.get("title", "") or "",
        "{url}": url,
        "{link}": url,
        "{platform}": (event.get("platform", "") or "").capitalize(),
        "{timestamp}": str(_resolve_timestamp(event)),
    }
    for key, value in replacements.items():
        template = template.replace(key, str(value))
    return template


def build_notification_view(
    event: Dict[str, Any],
    subscription: Dict[str, Any],
    locale: str,
) -> tuple[ui.LayoutView, discord.AllowedMentions]:
    """Build the Components V2 notification message for one guild.

    The container holds **only** the user's message (their custom template or
    the platform default) — no extra bot-authored text. Optional visuals:
      - the author avatar as a Section thumbnail (if enabled + available),
      - a large media preview as a MediaGallery (if enabled + available).

    Role mentions are placed **outside** the container, above it. The accent
    colour is the subscription's ``embed_color`` or the platform default.

    Returns ``(view, allowed_mentions)``.
    """
    platform = event.get("platform", "")

    media = event.get("thumbnail")
    avatar = event.get("author_avatar") or subscription.get("avatar_url")

    # Message: user's custom template, or the platform default. This is the
    # ONLY text rendered in the notification.
    template = subscription.get("message") or get_default_message(platform)
    text = _format_message_template(template, event).strip()

    color_int = subscription.get("embed_color")
    if color_int is None:
        color_int = platform_color(platform)

    show_avatar = bool(
        subscription.get("show_avatar", True) and supports_avatar(platform) and avatar
    )
    show_media = bool(
        subscription.get("show_media", True) and supports_media(platform) and media
    )

    view = ui.LayoutView(timeout=None)

    # 1. Role mentions — OUTSIDE the container, above it.
    role_ids = subscription.get("mention_role_ids") or []
    if role_ids:
        mentions = " ".join(f"<@&{rid}>" for rid in role_ids)
        view.add_item(ui.TextDisplay(mentions))

    container = ui.Container(accent_colour=discord.Colour(color_int))

    # 2. The message. With the avatar as a Section thumbnail (top, beside the
    #    text — same integration pattern as /user) when enabled.
    if text:
        if show_avatar:
            container.add_item(
                ui.Section(ui.TextDisplay(text), accessory=ui.Thumbnail(media=avatar))
            )
        else:
            container.add_item(ui.TextDisplay(text))

    # 3. Large media preview (video/stream cover) at the bottom.
    if show_media:
        container.add_item(ui.MediaGallery(discord.MediaGalleryItem(media=media)))

    view.add_item(container)

    allowed = discord.AllowedMentions(everyone=False, users=False, roles=bool(role_ids))
    return view, allowed
