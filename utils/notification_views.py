"""
Notification UI — attribution buttons, abuse reports, and the staff review.

Every DM Moddy sends carries, at the bottom, one small row that answers "where
does this come from and what do I do about it":

``[ <service> ] [ <server> ] [ 🚩 ]``

The first two open an ephemeral panel identifying the sender (with the server's
icon, its verification badge, a link into it, and the notification's uuid); the
red flag opens the report Modal V2. Official Moddy messages (a suspension, a
leaked-token alert) carry **no** row at all, and messages whose wording is
Moddy's own show the flag greyed out — the panel then explains why.

Staff side: a report is posted to ``MODDY_NOTIF_REPORT_CHANNEL_ID`` as a review
panel (Claim / See the message / Accept / Refuse) and every step is mirrored to
``MODDY_NOTIF_REPORT_LOG_CHANNEL_ID``.

Persistence
-----------
Every button is a :class:`discord.ui.DynamicItem` whose ``custom_id`` carries
the notification (or report) uuid, registered through
:class:`NotificationsPersistence` — a DM sent today must still be reportable
after next week's deploy. Modals are one-shot, per the documented exclusion.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

import discord
from discord import ui

from cogs.error_handler import BaseView, BaseModal
from notifications.models import NotificationContent, ReportStatus
from notifications.render import build_content_view, source_button_emoji
from utils.components_v2 import create_error_message
from utils.emojis import (
    DONE, FLAG, GROUPS, HAND, LEGAL, MESSAGE, MODDY, NOTE, PENDING,
    SNOWFLAKE, TIME, UNDONE, USER, WARNING,
)
from utils.i18n import i18n, t

logger = logging.getLogger("moddy.notification_views")

#: UUID fragment shared by every custom_id template below.
_UUID = r"[0-9a-fA-F-]{36}"

#: Public support entry point, shown under every attribution panel.
SUPPORT_URL = "https://moddy.app/support"

#: Deep link to a server from a DM — Discord opens it if the user is a member.
GUILD_URL = "https://discord.com/channels/{guild_id}"

#: Accent colours of the review panel, by report status.
_REVIEW_ACCENT = {
    ReportStatus.PENDING.value: 0x3661FF,
    ReportStatus.CLAIMED.value: 0xFEE75C,
    ReportStatus.ACCEPTED.value: 0x57F287,
    ReportStatus.REFUSED.value: 0xED4245,
}

_STATUS_EMOJI = {
    ReportStatus.PENDING.value: PENDING,
    ReportStatus.CLAIMED.value: HAND,
    ReportStatus.ACCEPTED.value: DONE,
    ReportStatus.REFUSED.value: UNDONE,
}


def _guarded(callback):
    """Route unknown errors from a dynamic item to the central error handler.

    Dynamic items dispatched through ``add_dynamic_items`` have no live
    ``BaseView``, so ``BaseView.on_error`` never fires and an unwrapped
    exception would vanish. Same pattern as ``utils/appeal_views.py``.
    """
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as exc:  # noqa: BLE001 — funnel everything to the handler
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, exc, self.__class__.__name__)
    return wrapper


def _short(text: str, limit: int = 80) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _excerpt(content: NotificationContent, variables: Dict[str, Any], limit: int = 300) -> str:
    """A few lines of the message, for a recap where the full card won't fit."""
    resolved = content.render(variables)
    body = " ".join((resolved.body or "").split())
    return _short(body, limit) or _short(resolved.title, limit)


# =========================================================================== #
# Attribution row
# =========================================================================== #

def build_attribution_row(
    notification_id: str, ctx: Dict[str, Any], *, locale: str = "en-US"
) -> Optional[ui.ActionRow]:
    """The row appended under every non-official notification.

    Order is fixed so it reads the same everywhere: the Moddy service that
    acted, then the server it acted for, then the flag. A source with neither
    service nor server (which should not happen) gets no row rather than an
    empty one.
    """
    service_name = ctx.get("service_name")
    guild_name = ctx.get("guild_name")
    if not service_name and not guild_name:
        return None

    row = ui.ActionRow()

    if service_name:
        row.add_item(NotificationServiceButton(
            notification_id, label=service_name,
            emoji=ctx.get("service_emoji") or MODDY,
        ))

    if guild_name:
        row.add_item(NotificationSourceButton(
            notification_id, label=guild_name,
            emoji=source_button_emoji(ctx),
        ))

    row.add_item(NotificationReportButton(
        notification_id, disabled=not ctx.get("reportable"),
    ))

    return row


def attach_attribution(
    view: ui.LayoutView, notification_id: str, ctx: Dict[str, Any], *, locale: str = "en-US"
) -> ui.LayoutView:
    """Append the attribution row to an already-built view, in place."""
    row = build_attribution_row(notification_id, ctx, locale=locale)
    if row is not None:
        view.add_item(row)
    return view


# =========================================================================== #
# Panels
# =========================================================================== #

def build_source_panel(
    *, notification_id: str, ctx: Dict[str, Any], created_at=None, locale: str = "en-US"
) -> BaseView:
    """The ephemeral card behind the *server* attribution button.

    Answers, in this order: which server, is it trustworthy, how do I get
    there, which notification is this exactly, and where do I go if something
    is wrong.
    """
    view = BaseView()
    container = ui.Container(accent_colour=discord.Colour(0x3661FF))

    name = ctx.get("guild_name") or t("notifications.attribution.unknown_guild", locale=locale)
    badge = ctx.get("badge") or ""
    container.add_item(ui.TextDisplay(
        f"### {GROUPS} {t('notifications.source.title', locale=locale)}"
    ))
    container.add_item(ui.TextDisplay(
        f"{t('notifications.source.guild_label', locale=locale)} **{name}**{badge}"
    ))

    lines = []
    if ctx.get("guild_id"):
        lines.append(f"{SNOWFLAKE} {t('notifications.source.id_label', locale=locale)} "
                     f"`{ctx['guild_id']}`")
    if ctx.get("guild_member_count") is not None:
        lines.append(f"{USER} {t('notifications.source.members', locale=locale)} "
                     f"`{ctx['guild_member_count']}`")
    if ctx.get("guild_created_at") is not None:
        lines.append(f"{TIME} {t('notifications.source.created', locale=locale)} "
                     f"<t:{int(ctx['guild_created_at'].timestamp())}:D>")
    if lines:
        container.add_item(ui.TextDisplay("\n".join(lines)))

    if ctx.get("official"):
        container.add_item(ui.TextDisplay(
            f"{MODDY} **{t('notifications.source.official', locale=locale)}**"))
    elif ctx.get("verified"):
        container.add_item(ui.TextDisplay(
            f"{DONE} **{t('notifications.source.verified', locale=locale)}**"))

    if ctx.get("service_name"):
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(
            f"{ctx.get('service_emoji') or MODDY} "
            f"{t('notifications.source.service_label', locale=locale)} "
            f"**{ctx['service_name']}**"
        ))

    block = ctx.get("report_block")
    if block:
        container.add_item(ui.TextDisplay(
            f"-# {WARNING} {t(f'notifications.source.not_reportable.{block}', locale=locale)}"
        ))

    container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    container.add_item(ui.TextDisplay(_identity_lines(
        notification_id, created_at=created_at, locale=locale)))

    view.add_item(container)

    if ctx.get("guild_id"):
        row = ui.ActionRow()
        row.add_item(ui.Button(
            label=_short(t("notifications.source.open_server", locale=locale)),
            url=GUILD_URL.format(guild_id=ctx["guild_id"]),
            style=discord.ButtonStyle.link,
        ))
        view.add_item(row)

    return view


def build_service_panel(
    *, notification_id: str, ctx: Dict[str, Any], created_at=None, locale: str = "en-US"
) -> BaseView:
    """The ephemeral card behind the *service* attribution button."""
    view = BaseView()
    container = ui.Container(accent_colour=discord.Colour(0x3661FF))
    emoji = ctx.get("service_emoji") or MODDY
    name = ctx.get("service_name") or t("notifications.services.moddy", locale=locale)

    container.add_item(ui.TextDisplay(
        f"### {emoji} {t('notifications.service.title', locale=locale)}"))
    container.add_item(ui.TextDisplay(
        f"{t('notifications.source.service_label', locale=locale)} **{name}**"))
    container.add_item(ui.TextDisplay(
        t("notifications.service.description", locale=locale)))

    if ctx.get("guild_name"):
        container.add_item(ui.TextDisplay(
            f"{GROUPS} {t('notifications.source.guild_label', locale=locale)} "
            f"**{ctx['guild_name']}**{ctx.get('badge') or ''}"
        ))

    container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    container.add_item(ui.TextDisplay(_identity_lines(
        notification_id, created_at=created_at, locale=locale)))
    view.add_item(container)
    return view


def _identity_lines(notification_id: str, *, created_at=None, locale: str = "en-US") -> str:
    """The uuid + support footer shared by both attribution panels."""
    lines = [f"-# {t('notifications.source.uuid_label', locale=locale)} `{notification_id}`"]
    if created_at is not None:
        lines.append(f"-# {t('notifications.source.sent_at', locale=locale)} "
                     f"<t:{int(created_at.timestamp())}:f>")
    lines.append(f"-# {t('notifications.source.support_hint', locale=locale, url=SUPPORT_URL)}")
    return "\n".join(lines)


# =========================================================================== #
# Attribution buttons (persistent)
# =========================================================================== #

class NotificationServiceButton(
    ui.DynamicItem[ui.Button],
    template=rf"moddy:notif:svc:(?P<notification>{_UUID})",
):
    """Opens the *service* identity panel. Public: anyone who sees the message."""

    def __init__(self, notification_id: str, *, label: str = "Moddy", emoji: str = MODDY):
        super().__init__(ui.Button(
            label=_short(label),
            style=discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(emoji) if emoji else None,
            custom_id=f"moddy:notif:svc:{notification_id}",
        ))
        self.notification_id = str(notification_id)

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["notification"])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        await _open_attribution_panel(interaction, self.notification_id, service=True)


class NotificationSourceButton(
    ui.DynamicItem[ui.Button],
    template=rf"moddy:notif:src:(?P<notification>{_UUID})",
):
    """Opens the *server* identity panel. Public: anyone who sees the message."""

    def __init__(self, notification_id: str, *, label: str = "Server", emoji: str = GROUPS):
        super().__init__(ui.Button(
            label=_short(label),
            style=discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(emoji) if emoji else None,
            custom_id=f"moddy:notif:src:{notification_id}",
        ))
        self.notification_id = str(notification_id)

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["notification"])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        await _open_attribution_panel(interaction, self.notification_id, service=False)


async def _open_attribution_panel(
    interaction: discord.Interaction, notification_id: str, *, service: bool
) -> None:
    """Shared body of both attribution buttons.

    Everything is re-derived from the interaction and the database: after a
    restart ``self`` carries nothing but the uuid in the custom_id.
    """
    locale = i18n.get_user_locale(interaction)
    bot = interaction.client
    notifications = getattr(bot, "notifications", None)
    record = await notifications.get(notification_id) if notifications else None

    if not record:
        await interaction.response.send_message(view=create_error_message(
            t("notifications.errors.unknown.title", locale=locale),
            t("notifications.errors.unknown.description", locale=locale,
              uuid=notification_id),
        ), ephemeral=True)
        return

    ctx = await notifications.source_context(record, locale=locale)
    builder = build_service_panel if service else build_source_panel
    await interaction.response.send_message(
        view=builder(notification_id=str(record["id"]), ctx=ctx,
                     created_at=record.get("created_at"), locale=locale),
        ephemeral=True,
    )


class NotificationReportButton(
    ui.DynamicItem[ui.Button],
    template=rf"moddy:notif:flag:(?P<notification>{_UUID})",
):
    """The red flag. Auth: the recipient of the notification, and only them."""

    def __init__(self, notification_id: str, *, disabled: bool = False):
        super().__init__(ui.Button(
            style=discord.ButtonStyle.danger,
            emoji=discord.PartialEmoji.from_str(FLAG),
            custom_id=f"moddy:notif:flag:{notification_id}",
            disabled=disabled,
        ))
        self.notification_id = str(notification_id)

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["notification"])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        bot = interaction.client
        notifications = getattr(bot, "notifications", None)
        record = await notifications.get(self.notification_id) if notifications else None

        if not record:
            await interaction.response.send_message(view=create_error_message(
                t("notifications.errors.unknown.title", locale=locale),
                t("notifications.errors.unknown.description", locale=locale,
                  uuid=self.notification_id),
            ), ephemeral=True)
            return

        # Re-check reportability on every click: a server can be marked
        # official long after its DM went out.
        ctx = await notifications.source_context(record, locale=locale)
        if not ctx.get("reportable"):
            block = ctx.get("report_block") or "moddy_authored"
            await interaction.response.send_message(view=create_error_message(
                t("notifications.report.errors.not_reportable.title", locale=locale),
                t(f"notifications.source.not_reportable.{block}", locale=locale),
            ), ephemeral=True)
            return

        if not await notifications.may_report(record, interaction):
            await interaction.response.send_message(view=create_error_message(
                t("notifications.report.errors.not_recipient.title", locale=locale),
                t("notifications.report.errors.not_recipient.description", locale=locale),
            ), ephemeral=True)
            return

        existing = await notifications.existing_report(record["id"], interaction.user.id)
        if existing:
            await interaction.response.send_message(
                view=build_report_status_panel(existing, locale=locale), ephemeral=True)
            return

        await interaction.response.send_modal(
            NotificationReportModal(record=record, ctx=ctx, locale=locale))


# =========================================================================== #
# Report modal
# =========================================================================== #

class NotificationReportModal(BaseModal):
    """Why is this DM abusive — plus an explicit "this is a real report" tick.

    The recap block matters: a member who has scrolled past ten DMs must see
    which one they are about to report before typing anything.
    """

    def __init__(self, *, record: Dict[str, Any], ctx: Dict[str, Any], locale: str = "en-US"):
        super().__init__(title=_short(t("notifications.report.modal.title", locale=locale), 45))
        self.record = record
        self.locale = locale

        content = NotificationContent.from_dict(record.get("content") or {})
        source_name = ctx.get("guild_name") or ctx.get("service_name") or "Moddy"
        recap = t(
            "notifications.report.modal.recap", locale=locale,
            source=source_name,
            uuid=str(record["id"]),
            excerpt=_excerpt(content, record.get("variables") or {}),
        )
        self.add_item(ui.TextDisplay(recap[:4000]))

        self.reason = ui.Label(
            text=_short(t("notifications.report.modal.reason.label", locale=locale), 45),
            description=_short(
                t("notifications.report.modal.reason.description", locale=locale), 100),
            component=ui.TextInput(
                style=discord.TextStyle.paragraph,
                placeholder=_short(
                    t("notifications.report.modal.reason.placeholder", locale=locale), 100),
                min_length=15,
                max_length=1000,
                required=True,
            ),
        )
        self.add_item(self.reason)

        self.confirm = ui.Label(
            text=_short(t("notifications.report.modal.confirm.label", locale=locale), 45),
            component=ui.CheckboxGroup(
                options=[discord.CheckboxGroupOption(
                    label=_short(
                        t("notifications.report.modal.confirm.option", locale=locale), 100),
                    value="confirm",
                )],
                min_values=1,
                max_values=1,
                required=True,
            ),
        )
        self.add_item(self.confirm)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        notifications = getattr(interaction.client, "notifications", None)
        report = await notifications.open_report(
            notification_id=self.record["id"],
            reporter=interaction.user,
            reason=self.reason.component.value or "",
            locale=self.locale,
        )
        if report is None:
            await interaction.followup.send(view=create_error_message(
                t("notifications.report.errors.failed.title", locale=self.locale),
                t("notifications.report.errors.failed.description", locale=self.locale),
            ), ephemeral=True)
            return

        await interaction.followup.send(
            view=build_report_status_panel(report, locale=self.locale, just_sent=True),
            ephemeral=True,
        )


def build_report_status_panel(
    report: Dict[str, Any], *, locale: str = "en-US", just_sent: bool = False
) -> BaseView:
    """What the reporter sees: their report's reference and where it stands."""
    view = BaseView()
    status = report.get("status") or ReportStatus.PENDING.value
    container = ui.Container(accent_colour=discord.Colour(
        _REVIEW_ACCENT.get(status, 0x3661FF)))

    key = "sent" if just_sent else "already"
    container.add_item(ui.TextDisplay(
        f"### {DONE if just_sent else _STATUS_EMOJI.get(status, PENDING)} "
        f"{t(f'notifications.report.{key}.title', locale=locale)}"))
    container.add_item(ui.TextDisplay(
        t(f"notifications.report.{key}.description", locale=locale)))
    container.add_item(ui.TextDisplay(
        f"{_STATUS_EMOJI.get(status, PENDING)} "
        f"{t('notifications.report.status_label', locale=locale)} "
        f"**{t(f'notifications.report.status.{status}', locale=locale)}**"))
    if report.get("decision_note"):
        container.add_item(ui.TextDisplay(
            f"-# {t('notifications.report.decision_note', locale=locale)} "
            f"{_short(report['decision_note'], 300)}"))
    container.add_item(ui.TextDisplay(
        f"-# {t('notifications.report.reference', locale=locale)} `{report['id']}`"))
    view.add_item(container)
    return view


# =========================================================================== #
# Staff review panel
# =========================================================================== #

def build_review_panel(
    *, report: Dict[str, Any], record: Dict[str, Any], ctx: Dict[str, Any],
    locale: str = "en-US",
) -> BaseView:
    """The panel staff act on, in the report channel.

    It carries the whole case — who reported, what they said, what was sent, by
    whom, to whom — so a reviewer never has to leave the message to decide.
    Buttons disappear once the report is decided; the accent tracks the status.
    """
    status = report.get("status") or ReportStatus.PENDING.value
    content = NotificationContent.from_dict(record.get("content") or {})

    view = BaseView()
    container = ui.Container(accent_colour=discord.Colour(
        _REVIEW_ACCENT.get(status, 0x3661FF)))

    container.add_item(ui.TextDisplay(
        f"### {FLAG} {t('notifications.review.title', locale=locale)}"))
    container.add_item(ui.TextDisplay(
        f"{_STATUS_EMOJI.get(status, PENDING)} "
        f"{t('notifications.report.status_label', locale=locale)} "
        f"**{t(f'notifications.report.status.{status}', locale=locale)}**"))

    container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

    source_name = ctx.get("guild_name") or ctx.get("service_name") or "Moddy"
    details = [
        f"{USER} **{t('notifications.review.reporter', locale=locale)}** "
        f"<@{report['reporter_id']}> (`{report['reporter_id']}`)",
        f"{GROUPS} **{t('notifications.review.source', locale=locale)}** "
        f"{source_name}{ctx.get('badge') or ''}"
        + (f" (`{ctx['guild_id']}`)" if ctx.get("guild_id") else ""),
    ]
    if ctx.get("service_name"):
        details.append(f"{ctx.get('service_emoji') or MODDY} "
                       f"**{t('notifications.review.service', locale=locale)}** "
                       f"{ctx['service_name']}")
    if record.get("recipient_id"):
        details.append(f"{MESSAGE} **{t('notifications.review.recipient', locale=locale)}** "
                       f"<@{record['recipient_id']}> (`{record['recipient_id']}`)")
    if record.get("created_at") is not None:
        details.append(f"{TIME} **{t('notifications.review.sent_at', locale=locale)}** "
                       f"<t:{int(record['created_at'].timestamp())}:f>")
    details.append(f"{NOTE} **{t('notifications.review.occurrences', locale=locale)}** "
                   f"`{record.get('content_uses', 1)}`")
    container.add_item(ui.TextDisplay("\n".join(details)))

    container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    container.add_item(ui.TextDisplay(
        f"**{t('notifications.review.reason', locale=locale)}**\n"
        f"{_short(report.get('reason') or '', 900)}"))
    container.add_item(ui.TextDisplay(
        f"**{t('notifications.review.content', locale=locale)}**\n"
        f"-# {_excerpt(content, record.get('variables') or {}, 600)}"))

    if report.get("claimed_by"):
        container.add_item(ui.TextDisplay(
            f"-# {HAND} {t('notifications.review.claimed_by', locale=locale)} "
            f"<@{report['claimed_by']}>"))
    if report.get("decided_by"):
        container.add_item(ui.TextDisplay(
            f"-# {LEGAL} {t('notifications.review.decided_by', locale=locale)} "
            f"<@{report['decided_by']}>"))
    if report.get("decision_note"):
        container.add_item(ui.TextDisplay(
            f"-# {NOTE} {_short(report['decision_note'], 300)}"))

    container.add_item(ui.TextDisplay(
        f"-# {t('notifications.source.uuid_label', locale=locale)} `{record['id']}`\n"
        f"-# {t('notifications.report.reference', locale=locale)} `{report['id']}`"))

    view.add_item(container)

    report_id = str(report["id"])
    decided = status in (ReportStatus.ACCEPTED.value, ReportStatus.REFUSED.value)
    row = ui.ActionRow()
    row.add_item(NotifReviewClaimButton(
        report_id, claimed=bool(report.get("claimed_by")) or decided, locale=locale))
    row.add_item(NotifReviewPreviewButton(report_id, locale=locale))
    row.add_item(NotifReviewDecisionButton(
        "accept", report_id, disabled=decided, locale=locale))
    row.add_item(NotifReviewDecisionButton(
        "refuse", report_id, disabled=decided, locale=locale))
    view.add_item(row)

    return view


def build_report_log(
    *, event: str, report: Dict[str, Any], record: Dict[str, Any],
    actor: Optional[discord.abc.User] = None, jump_url: Optional[str] = None,
    locale: str = "en-US",
) -> BaseView:
    """One immutable line of history, for the report log channel.

    ``event`` is one of ``created`` / ``claimed`` / ``accepted`` / ``refused``.
    """
    accent = {
        "created": 0x3661FF, "claimed": 0xFEE75C,
        "accepted": 0x57F287, "refused": 0xED4245,
    }.get(event, 0x99AAB5)

    view = BaseView()
    container = ui.Container(accent_colour=discord.Colour(accent))
    container.add_item(ui.TextDisplay(
        f"### {FLAG} {t(f'notifications.log.title.{event}', locale=locale)}"))

    lines = [
        f"{NOTE} **{t('notifications.report.reference', locale=locale)}** `{report['id']}`",
        f"{SNOWFLAKE} **{t('notifications.source.uuid_label', locale=locale)}** `{record['id']}`",
        f"{USER} **{t('notifications.review.reporter', locale=locale)}** "
        f"<@{report['reporter_id']}> (`{report['reporter_id']}`)",
    ]
    if actor is not None:
        lines.append(f"{HAND} **{t('notifications.log.actor', locale=locale)}** "
                     f"{actor.mention} (`{actor.id}`)")
    if record.get("source_guild_id"):
        lines.append(f"{GROUPS} **{t('notifications.review.source', locale=locale)}** "
                     f"`{record['source_guild_id']}`")
    if record.get("source_service"):
        lines.append(f"{MODDY} **{t('notifications.review.service', locale=locale)}** "
                     f"`{record['source_service']}`")
    if report.get("decision_note"):
        lines.append(f"{MESSAGE} **{t('notifications.review.note', locale=locale)}** "
                     f"{_short(report['decision_note'], 300)}")
    container.add_item(ui.TextDisplay("\n".join(lines)))
    view.add_item(container)

    if jump_url:
        row = ui.ActionRow()
        row.add_item(ui.Button(
            label=_short(t("notifications.log.jump", locale=locale)),
            url=jump_url, style=discord.ButtonStyle.link))
        view.add_item(row)

    return view


# =========================================================================== #
# Review buttons (persistent)
# =========================================================================== #

async def _load_review(interaction: discord.Interaction, report_id: str, locale: str):
    """Fetch (report, notification) or answer the click with an error."""
    notifications = getattr(interaction.client, "notifications", None)
    report = await notifications.get_report(report_id) if notifications else None
    if not report:
        await interaction.response.send_message(view=create_error_message(
            t("notifications.errors.unknown.title", locale=locale),
            t("notifications.errors.unknown_report.description", locale=locale,
              uuid=report_id),
        ), ephemeral=True)
        return None, None
    record = await notifications.get(report["notification_id"])
    return report, record


async def _guard_reviewer(interaction: discord.Interaction, report: Dict[str, Any],
                          locale: str) -> bool:
    """Staff node + "not your own report" — re-checked on every single click."""
    from utils.staff_permissions import has_staff_node

    if not await has_staff_node(interaction.client, interaction.user.id, "notif_review"):
        await interaction.response.send_message(view=create_error_message(
            t("notifications.review.no_permission.title", locale=locale),
            t("notifications.review.no_permission.description", locale=locale),
        ), ephemeral=True)
        return False

    if interaction.user.id == report.get("reporter_id"):
        await interaction.response.send_message(view=create_error_message(
            t("notifications.review.own_report.title", locale=locale),
            t("notifications.review.own_report.description", locale=locale),
        ), ephemeral=True)
        return False

    return True


class NotifReviewClaimButton(
    ui.DynamicItem[ui.Button],
    template=rf"moddy:notif:rvclaim:(?P<report>{_UUID})",
):
    """Take ownership of a report so two reviewers don't work it at once."""

    def __init__(self, report_id: str, *, claimed: bool = False, locale: str = "en-US"):
        label_key = ("notifications.review.buttons.claimed" if claimed
                     else "notifications.review.buttons.claim")
        super().__init__(ui.Button(
            label=_short(t(label_key, locale=locale)),
            style=discord.ButtonStyle.secondary if claimed else discord.ButtonStyle.primary,
            emoji=discord.PartialEmoji.from_str(HAND),
            custom_id=f"moddy:notif:rvclaim:{report_id}",
            disabled=claimed,
        ))
        self.report_id = str(report_id)

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["report"])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        report, _record = await _load_review(interaction, self.report_id, locale)
        if not report or not await _guard_reviewer(interaction, report, locale):
            return
        await interaction.client.notifications.claim_report(
            report_id=self.report_id, staff=interaction.user, interaction=interaction)


class NotifReviewPreviewButton(
    ui.DynamicItem[ui.Button],
    template=rf"moddy:notif:rvshow:(?P<report>{_UUID})",
):
    """Show the notification exactly as its recipient received it."""

    def __init__(self, report_id: str, *, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=_short(t("notifications.review.buttons.preview", locale=locale)),
            style=discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(MESSAGE),
            custom_id=f"moddy:notif:rvshow:{report_id}",
        ))
        self.report_id = str(report_id)

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["report"])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        report, record = await _load_review(interaction, self.report_id, locale)
        if not report or not record:
            return
        if not await _guard_reviewer(interaction, report, locale):
            return

        # Rendered from the stored template + this notification's variables:
        # the exact wording that reached the recipient, attribution row left
        # off (its buttons belong to the recipient, not to the reviewer).
        content = NotificationContent.from_dict(record.get("content") or {})
        await interaction.response.send_message(
            view=build_content_view(content, record.get("variables") or {},
                                    locale=record.get("locale") or locale),
            ephemeral=True,
        )


class NotifReviewDecisionButton(
    ui.DynamicItem[ui.Button],
    template=rf"moddy:notif:rvdec:(?P<action>accept|refuse):(?P<report>{_UUID})",
):
    """Accept (the report is founded) or refuse it, with a note for the record."""

    _STYLE = {"accept": discord.ButtonStyle.success, "refuse": discord.ButtonStyle.danger}
    _EMOJI = {"accept": DONE, "refuse": UNDONE}

    def __init__(self, action: str, report_id: str, *, disabled: bool = False,
                 locale: str = "en-US"):
        super().__init__(ui.Button(
            label=_short(t(f"notifications.review.buttons.{action}", locale=locale)),
            style=self._STYLE[action],
            emoji=discord.PartialEmoji.from_str(self._EMOJI[action]),
            custom_id=f"moddy:notif:rvdec:{action}:{report_id}",
            disabled=disabled,
        ))
        self.action = action
        self.report_id = str(report_id)

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["action"], match["report"])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        report, _record = await _load_review(interaction, self.report_id, locale)
        if not report or not await _guard_reviewer(interaction, report, locale):
            return
        if report.get("status") in (ReportStatus.ACCEPTED.value, ReportStatus.REFUSED.value):
            await interaction.response.send_message(view=create_error_message(
                t("notifications.review.already_decided.title", locale=locale),
                t("notifications.review.already_decided.description", locale=locale),
            ), ephemeral=True)
            return
        await interaction.response.send_modal(
            NotifDecisionModal(action=self.action, report_id=self.report_id, locale=locale))


class NotifDecisionModal(BaseModal):
    """The note attached to a decision — optional, but it is what the reporter
    is shown and what the next reviewer reads six months later."""

    def __init__(self, *, action: str, report_id: str, locale: str = "en-US"):
        super().__init__(title=_short(
            t(f"notifications.review.decision.{action}.title", locale=locale), 45))
        self.action = action
        self.report_id = report_id
        self.locale = locale

        self.add_item(ui.TextDisplay(
            t(f"notifications.review.decision.{action}.recap", locale=locale)))
        self.note = ui.Label(
            text=_short(t("notifications.review.decision.note.label", locale=locale), 45),
            description=_short(
                t("notifications.review.decision.note.description", locale=locale), 100),
            component=ui.TextInput(
                style=discord.TextStyle.paragraph,
                placeholder=_short(
                    t("notifications.review.decision.note.placeholder", locale=locale), 100),
                max_length=500,
                required=False,
            ),
        )
        self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        status = (ReportStatus.ACCEPTED.value if self.action == "accept"
                  else ReportStatus.REFUSED.value)
        ok = await interaction.client.notifications.decide_report(
            report_id=self.report_id, staff=interaction.user, status=status,
            note=self.note.component.value or None, locale=self.locale,
        )
        if not ok:
            await interaction.followup.send(view=create_error_message(
                t("notifications.review.already_decided.title", locale=self.locale),
                t("notifications.review.already_decided.description", locale=self.locale),
            ), ephemeral=True)
            return
        from utils.components_v2 import create_success_message
        await interaction.followup.send(view=create_success_message(
            t(f"notifications.review.decision.{self.action}.done.title", locale=self.locale),
            t(f"notifications.review.decision.{self.action}.done.description",
              locale=self.locale),
        ), ephemeral=True)


# =========================================================================== #
# Persistence
# =========================================================================== #

class NotificationsPersistence(BaseView):
    """Marker view: registers every notification dynamic item at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(
            NotificationServiceButton,
            NotificationSourceButton,
            NotificationReportButton,
            NotifReviewClaimButton,
            NotifReviewPreviewButton,
            NotifReviewDecisionButton,
        )
