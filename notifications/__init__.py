"""
Centralized notification system.

Every message Moddy sends to a human — a DM, a mail, a dashboard card — is
created here, so that it is stored, attributable and (when someone else wrote
it) reportable. ``bot.notifications`` is the service; the model is uniform
across platforms.

    from notifications import NotificationContent, NotificationSource

    await bot.notifications.send_dm(
        member,
        content=NotificationContent(title="…", body="…", icon=WAVING_HAND),
        source=NotificationSource.guild(guild.id),
        variables={"user": member.mention},
    )

See docs/NOTIFICATIONS.md.
"""

from notifications.models import (
    SERVICES, ContentAuthor, DeliveryStatus, NotificationContent,
    NotificationSource, Platform, RecipientType, ReportStatus, ServiceInfo,
    SourceKind, get_service, strip_custom_emojis, substitute,
)
from notifications.render import build_content_view, resolve_source_context
from notifications.service import DeliveryResult, NotificationService

__all__ = [
    "SERVICES",
    "ContentAuthor",
    "DeliveryResult",
    "DeliveryStatus",
    "NotificationContent",
    "NotificationService",
    "NotificationSource",
    "Platform",
    "RecipientType",
    "ReportStatus",
    "ServiceInfo",
    "SourceKind",
    "build_content_view",
    "get_service",
    "resolve_source_context",
    "strip_custom_emojis",
    "substitute",
]
