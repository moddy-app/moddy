"""`/mod notif` — everything Moddy knows about one notification.

Takes the uuid a recipient reads at the bottom of any Moddy DM (or the
reference of an abuse report filed against one) and reconstructs the full
picture: who sent it and on whose behalf, who received it, on which platforms
it was delivered and with what result, how many times that exact wording has
been sent, and the state of every report against it.
"""

from typing import Any, Dict, List

import discord
from discord import ui

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from cogs.error_handler import BaseView
from notifications.models import NotificationContent, ReportStatus
from utils import emojis
from utils.i18n import t

#: Delivery status -> the dot shown in front of it.
_STATUS_DOT = {
    "sent": emojis.GREEN_STATUS,
    "pending": emojis.YELLOW_STATUS,
    "failed": emojis.RED_STATUS,
    "skipped": emojis.YELLOW_STATUS,
}

_REPORT_EMOJI = {
    ReportStatus.PENDING.value: emojis.PENDING,
    ReportStatus.CLAIMED.value: emojis.HAND,
    ReportStatus.ACCEPTED.value: emojis.DONE,
    ReportStatus.REFUSED.value: emojis.UNDONE,
}


@staff_command
class NotificationInfoCommand(StaffCommand):
    command_type = CommandType.MODERATOR
    name = "notif"
    permission = "notif_lookup"
    description = "Look up a notification (or an abuse report) by its uuid."
    options = [
        SlashOption("reference", "string",
                    "Notification uuid, or the reference of a report on it.",
                    required=True),
    ]

    async def execute(self, ctx):
        reference = (ctx.opt("reference") or "").strip()
        if not reference:
            await ctx.send(view=design.invalid_usage(ctx.locale, "mod.notif <uuid>"))
            return

        notifications = getattr(ctx.bot, "notifications", None)
        if notifications is None:
            await ctx.send(view=design.error(
                t("staff.notif.unavailable.title", locale=ctx.locale),
                t("staff.notif.unavailable.description", locale=ctx.locale)))
            return

        # A staffer copies whichever id they have in front of them, so accept
        # both: try the notification first, fall back to a report reference.
        record = await notifications.get(reference)
        if record is None:
            report = await notifications.get_report(reference)
            record = await notifications.get(report["notification_id"]) if report else None

        if record is None:
            await ctx.send(view=design.error(
                t("staff.notif.not_found.title", locale=ctx.locale),
                t("staff.notif.not_found.description", locale=ctx.locale, uuid=reference)))
            return

        ctx_source = await notifications.source_context(record, locale=ctx.locale)
        reports = await ctx.bot.db.get_reports_for_notification(record["id"])

        await ctx.send(view=_build_panel(record, ctx_source, reports, locale=ctx.locale))


def _build_panel(record: Dict[str, Any], source_ctx: Dict[str, Any],
                 reports: List[Dict[str, Any]], *, locale: str) -> BaseView:
    """The staff card: identity, routing, delivery, content, reports."""
    view = BaseView()
    container = design.make_container("info")

    container.add_item(ui.TextDisplay(design.title_line(
        emojis.MESSAGE, t("staff.notif.title", locale=locale))))
    container.add_item(ui.TextDisplay(
        f"`{record['id']}`\n"
        f"-# {t('staff.notif.created_at', locale=locale)} "
        f"<t:{int(record['created_at'].timestamp())}:f>"))

    container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

    # --- origin ---------------------------------------------------------- #
    origin = [
        f"**{t('staff.notif.kind', locale=locale)}** `{record['kind']}`",
        f"**{t('staff.notif.author', locale=locale)}** `{record['author']}`",
    ]
    if source_ctx.get("service_name"):
        origin.append(f"**{t('staff.notif.service', locale=locale)}** "
                      f"{source_ctx.get('service_emoji') or ''} {source_ctx['service_name']} "
                      f"(`{record['source_service']}`)")
    if record.get("source_guild_id"):
        origin.append(f"**{t('staff.notif.guild', locale=locale)}** "
                      f"{source_ctx.get('guild_name') or '?'}{source_ctx.get('badge') or ''} "
                      f"(`{record['source_guild_id']}`)")
    if record.get("actor_id"):
        origin.append(f"**{t('staff.notif.actor', locale=locale)}** "
                      f"<@{record['actor_id']}> (`{record['actor_id']}`)")
    origin.append(
        f"**{t('staff.notif.reportable', locale=locale)}** "
        + (f"{emojis.DONE} `true`" if record.get("reportable")
           else f"{emojis.UNDONE} `false`"
                + (f" — `{source_ctx.get('report_block')}`" if source_ctx.get("report_block") else ""))
    )
    container.add_item(ui.TextDisplay("\n".join(origin)))

    # --- recipient + delivery -------------------------------------------- #
    container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    recipient = [f"**{t('staff.notif.recipient', locale=locale)}** `{record['recipient_type']}`"]
    if record.get("recipient_id"):
        recipient.append(f"-# <@{record['recipient_id']}> (`{record['recipient_id']}`)")
    if record.get("recipient_ref"):
        recipient.append(f"-# `{record['recipient_ref']}`")
    if record.get("batch_id"):
        recipient.append(f"-# {t('staff.notif.batch', locale=locale)} `{record['batch_id']}`")
    container.add_item(ui.TextDisplay("\n".join(recipient)))

    lines = []
    for delivery in record.get("deliveries", []):
        dot = _STATUS_DOT.get(delivery["status"], emojis.YELLOW_STATUS)
        line = f"{dot} `{delivery['platform']}` — `{delivery['status']}`"
        if delivery.get("message_id"):
            line += f" · `{delivery['message_id']}`"
        if delivery.get("error"):
            line += f"\n-# {delivery['error'][:150]}"
        lines.append(line)
    container.add_item(ui.TextDisplay(
        f"**{t('staff.notif.delivery', locale=locale)}**\n"
        + ("\n".join(lines) or f"-# {t('staff.notif.no_delivery', locale=locale)}")))

    # --- content ---------------------------------------------------------- #
    container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    content = NotificationContent.from_dict(record.get("content") or {})
    resolved = content.render(record.get("variables") or {})
    body = " ".join((resolved.body or "").split())
    container.add_item(ui.TextDisplay(
        f"**{t('staff.notif.content', locale=locale)}**\n"
        f"{resolved.title or '—'}\n"
        f"-# {body[:600] or '—'}"))
    container.add_item(ui.TextDisplay(
        f"-# {t('staff.notif.hash', locale=locale)} `{record['content_hash'][:16]}…` · "
        f"{t('staff.notif.occurrences', locale=locale)} `{record.get('content_uses', 0)}`"
        + (f" · {t('staff.notif.template', locale=locale)} `{content.template_id}`"
           if content.template_id else "")))

    # --- reports ----------------------------------------------------------- #
    if reports:
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        rows = []
        for report in reports:
            emoji = _REPORT_EMOJI.get(report["status"], emojis.PENDING)
            rows.append(
                f"{emoji} `{report['status']}` — <@{report['reporter_id']}>\n"
                f"-# `{report['id']}`"
                + (f" · <@{report['decided_by']}>" if report.get("decided_by") else "")
            )
        container.add_item(ui.TextDisplay(
            f"**{t('staff.notif.reports', locale=locale)}** (`{len(reports)}`)\n"
            + "\n".join(rows[:5])))

    view.add_item(container)
    return view
