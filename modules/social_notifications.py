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
from typing import Any, Dict, List, Optional

import discord
from discord import ui

from modules.module_manager import ModuleBase
from utils.emojis import SOCIAL, get_platform_emoji
from utils.i18n import t

logger = logging.getLogger('moddy.modules.social_notifications')


# --------------------------------------------------------------------------- #
# Platform catalogue
# --------------------------------------------------------------------------- #
# ``realtime`` platforms ignore poll_interval (the service pushes in real time).
# ``disabled`` platforms are not yet available on the service side.
PLATFORMS: Dict[str, Dict[str, Any]] = {
    "youtube": {"realtime": False, "disabled": False, "color": 0xFF0000},
    "twitch": {"realtime": False, "disabled": False, "color": 0x9146FF},
    "bluesky": {"realtime": True, "disabled": False, "color": 0x1185FE},
    "rss": {"realtime": False, "disabled": False, "color": 0xEE802F},
    "instagram": {"realtime": False, "disabled": True, "color": 0xE1306C},
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

# Supported message-template placeholders.
MESSAGE_PLACEHOLDERS = ("{author}", "{title}", "{url}", "{link}", "{platform}")

MAX_MESSAGE_LENGTH = 1500
MAX_CONTENT_PREVIEW = 500


def is_realtime(platform: str) -> bool:
    return PLATFORMS.get(platform, {}).get("realtime", False)


def is_platform_disabled(platform: str) -> bool:
    return PLATFORMS.get(platform, {}).get("disabled", False)


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
def _format_message_template(template: str, event: Dict[str, Any]) -> str:
    """Substitute supported placeholders inside a custom message template."""
    url = event.get("url", "")
    replacements = {
        "{author}": event.get("author_name", ""),
        "{title}": event.get("title", ""),
        "{url}": url,
        "{link}": url,
        "{platform}": event.get("platform", "").capitalize(),
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

    Returns ``(view, allowed_mentions)``. Optional event fields are accessed
    with ``.get()`` (they may be omitted by the service).
    """
    platform = event.get("platform", "")
    etype = event.get("type") or "default"
    meta = PLATFORMS.get(platform, {})
    emoji = get_platform_emoji(platform)

    author = event.get("author_name") or subscription.get("display_name") or platform.capitalize()
    title = event.get("title")
    content = event.get("content")
    url = event.get("url")
    thumbnail = event.get("thumbnail")
    avatar = event.get("author_avatar") or subscription.get("avatar_url")

    type_label = t(f"modules.social_notifications.notify.type.{etype}", locale=locale)
    if type_label.startswith("["):  # missing key -> fallback
        type_label = t("modules.social_notifications.notify.type.default", locale=locale)

    container = ui.Container(accent_colour=discord.Colour(meta.get("color", 0x5865F2)))

    # 1. Role mentions (must live inside the container to ping in a LayoutView).
    role_ids = subscription.get("mention_role_ids") or []
    if role_ids:
        mentions = " ".join(f"<@&{rid}>" for rid in role_ids)
        container.add_item(ui.TextDisplay(mentions))

    # 2. Header: platform emoji + event type.
    container.add_item(ui.TextDisplay(f"### {emoji} {type_label}"))

    # 3. Caption: custom message template, or a sensible default.
    custom = subscription.get("message")
    if custom:
        caption = _format_message_template(custom, event)
    else:
        caption = t(
            "modules.social_notifications.notify.default_caption",
            locale=locale,
            author=author,
        )
    if caption:
        container.add_item(ui.TextDisplay(caption))

    # 4. Author + title, with the author avatar as a thumbnail accessory.
    title_line = f"[**{discord.utils.escape_markdown(title)}**]({url})" if title and url else (
        f"**{discord.utils.escape_markdown(title)}**" if title else ""
    )
    section_texts = [ui.TextDisplay(f"-# {discord.utils.escape_markdown(author)}")]
    if title_line:
        section_texts.append(ui.TextDisplay(title_line))
    if avatar:
        container.add_item(ui.Section(*section_texts, accessory=ui.Thumbnail(media=avatar)))
    else:
        for item in section_texts:
            container.add_item(item)

    # 5. Content preview.
    if content:
        preview = content.strip()
        if len(preview) > MAX_CONTENT_PREVIEW:
            preview = preview[:MAX_CONTENT_PREVIEW].rstrip() + "…"
        container.add_item(ui.TextDisplay(preview))

    # 6. Large media (thumbnail/cover).
    if thumbnail:
        container.add_item(ui.MediaGallery(discord.MediaGalleryItem(media=thumbnail)))

    view = ui.LayoutView(timeout=None)
    view.add_item(container)

    # 7. Link button (open the post on the platform).
    if url:
        open_label = t(f"modules.social_notifications.notify.open.{etype}", locale=locale)
        if open_label.startswith("["):
            open_label = t("modules.social_notifications.notify.open.default", locale=locale)
        row = ui.ActionRow()
        row.add_item(ui.Button(label=open_label, style=discord.ButtonStyle.link, url=url))
        view.add_item(row)

    allowed = discord.AllowedMentions(everyone=False, users=False, roles=bool(role_ids))
    return view, allowed
