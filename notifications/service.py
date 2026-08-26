"""
NotificationService — the single door every Moddy notification goes through.

Nothing in the bot should call ``user.send(...)`` directly any more. A feature
hands this service *what* it wants to say (a uniform
:class:`~notifications.models.NotificationContent`, optionally alongside its own
rich Components V2 card) and *who* is behind it
(:class:`~notifications.models.NotificationSource`); the service:

1. writes the notification row — uuid, sender, recipient, platforms, and the
   template hash that lets thousands of identical DMs share one stored body;
2. appends the attribution row (service / server / report flag) unless the
   message is an official Moddy notice, which carries none;
3. delivers it and records the outcome per platform, message id included;
4. owns the abuse-report lifecycle: open, claim, decide, log, notify.

``bot.notifications`` is the instance. Everything here is defensive by design:
a database outage degrades a DM to "sent without attribution", it never
swallows the message itself.

See docs/NOTIFICATIONS.md.
"""

from __future__ import annotations

import asyncio
import logging
import uuid as uuid_module
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

import discord

import config
from notifications.models import (
    DeliveryStatus, NotificationContent, NotificationSource,
    Platform, RecipientType, ReportStatus,
)
from notifications.render import (
    build_attribution_line, build_content_view, resolve_source_context,
)

logger = logging.getLogger("moddy.notifications")

#: Pause between two sends of a broadcast. Discord's global limit is far
#: higher, but a 4 000-server announcement has no reason to burn the whole
#: budget the rest of the bot shares.
BROADCAST_DELAY = 0.35

#: Staff review panels and report logs are rendered in a single language, the
#: same convention the appeal review panels follow.
STAFF_PANEL_LOCALE = "en-US"

#: Name of the channel `utils/announcement_setup.py` creates when a server has
#: no community-updates channel. Preferred destination for a server-wide notice.
MODDY_UPDATES_CHANNEL_NAME = "moddy-updates"


@dataclass
class DeliveryResult:
    """Outcome of one delivery attempt, from the caller's point of view."""

    notification_id: Optional[str] = None
    message: Optional[discord.Message] = None
    status: DeliveryStatus = DeliveryStatus.PENDING
    error: Optional[Exception] = None

    @property
    def delivered(self) -> bool:
        return self.status is DeliveryStatus.SENT

    @property
    def forbidden(self) -> bool:
        """The recipient refuses DMs — every further send would fail the same."""
        return isinstance(self.error, discord.Forbidden)


class NotificationService:
    """Records, renders, delivers and polices every notification."""

    def __init__(self, bot):
        self.bot = bot

    @property
    def db(self):
        return getattr(self.bot, "db", None)

    # ------------------------------------------------------------------ #
    # Reading
    # ------------------------------------------------------------------ #

    async def get(self, notification_id: Any) -> Optional[Dict[str, Any]]:
        """One notification, with its template payload and its deliveries."""
        if not self.db:
            return None
        try:
            return await self.db.get_notification(notification_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to load notification %s: %s", notification_id, exc)
            return None

    async def get_report(self, report_id: Any) -> Optional[Dict[str, Any]]:
        if not self.db:
            return None
        return await self.db.get_notification_report(report_id)

    async def existing_report(self, notification_id: Any, reporter_id: int) -> Optional[Dict[str, Any]]:
        """The report this user already filed against this notification, if any."""
        if not self.db:
            return None
        for report in await self.db.get_reports_for_notification(notification_id):
            if report["reporter_id"] == reporter_id:
                return report
        return None

    async def source_context(self, record: Dict[str, Any], *, locale: str = "en-US") -> Dict[str, Any]:
        """Attribution context for a stored notification row."""
        source = NotificationSource.from_row(record)
        ctx = await resolve_source_context(self.bot, source, locale=locale)
        # The row's own flag wins when it is stricter: a notification recorded
        # as non-reportable must never become reportable later.
        if not record.get("reportable"):
            ctx["reportable"] = False
            ctx["report_block"] = ctx.get("report_block") or "moddy_authored"
        return ctx

    async def may_report(self, record: Dict[str, Any], interaction: discord.Interaction) -> bool:
        """Only the addressee may report a notification.

        For a DM that is the recipient themselves. For a notice delivered into a
        server's channel, the addressee is the server, so the check becomes
        "can this member manage the server".
        """
        recipient_type = record.get("recipient_type")
        if recipient_type == RecipientType.DISCORD_USER.value:
            return interaction.user.id == record.get("recipient_id")
        if recipient_type == RecipientType.DISCORD_GUILD.value:
            if interaction.guild_id != record.get("recipient_id"):
                return False
            perms = getattr(interaction.user, "guild_permissions", None)
            return bool(perms and (perms.manage_guild or perms.administrator))
        return False

    # ------------------------------------------------------------------ #
    # Recording
    # ------------------------------------------------------------------ #

    async def record(
        self,
        *,
        content: NotificationContent,
        source: NotificationSource,
        recipient_type: RecipientType,
        recipient_id: Optional[int] = None,
        recipient_ref: Optional[str] = None,
        variables: Optional[Dict[str, Any]] = None,
        platforms: Sequence[Platform] = (Platform.DISCORD,),
        locale: Optional[str] = None,
        batch_id: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
        """Persist a notification and return the stored row (``None`` if the
        database is unavailable — the caller still sends, just unattributed)."""
        if not self.db:
            return None

        reportable = source.base_reportable
        if reportable and source.guild_id:
            # A message from one of Moddy's own servers is not reportable to
            # Moddy. Resolved once, at send time, and frozen on the row.
            ctx = await resolve_source_context(self.bot, source)
            reportable = bool(ctx.get("reportable"))

        try:
            return await self.db.create_notification(
                kind=source.kind.value,
                author=source.author.value,
                source_service=source.service_id,
                source_guild_id=source.guild_id,
                actor_id=source.actor_id,
                recipient_type=recipient_type.value,
                recipient_id=recipient_id,
                recipient_ref=recipient_ref,
                content_hash=content.template_hash(),
                content_payload=content.to_dict(),
                variables=variables or {},
                platforms=[p.value for p in platforms],
                reportable=reportable,
                locale=locale,
                batch_id=batch_id,
            )
        except Exception as exc:  # noqa: BLE001 — never block a delivery on the DB
            logger.error("Failed to record notification: %s", exc, exc_info=True)
            return None

    async def _mark(self, notification_id, platform: Platform, status: DeliveryStatus,
                    **kwargs) -> None:
        if not self.db or not notification_id:
            return
        try:
            await self.db.set_notification_delivery(
                notification_id, platform.value, status.value, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to update delivery status: %s", exc)

    async def mark_delivered(self, record: Optional[Dict[str, Any]],
                             message: Optional[discord.Message] = None,
                             platform: Platform = Platform.DISCORD) -> None:
        """Mark a recorded notification as delivered.

        For the rare senders that cannot use :meth:`send_dm` because they own an
        exotic delivery path — the token detector opens the DM channel with the
        *user's* own token when the bot cannot — record the notification, send
        it your way, then call this.
        """
        if not record:
            return
        await self._mark(
            record["id"], platform, DeliveryStatus.SENT,
            channel_id=getattr(getattr(message, "channel", None), "id", None),
            message_id=getattr(message, "id", None),
        )

    async def mark_failed(self, record: Optional[Dict[str, Any]],
                          error: Optional[str] = None,
                          platform: Platform = Platform.DISCORD) -> None:
        """Counterpart of :meth:`mark_delivered` for a delivery that failed."""
        if not record:
            return
        await self._mark(record["id"], platform, DeliveryStatus.FAILED, error=error)

    # ------------------------------------------------------------------ #
    # Sending
    # ------------------------------------------------------------------ #

    async def send_dm(
        self,
        user: discord.abc.Snowflake,
        *,
        content: NotificationContent,
        source: NotificationSource,
        variables: Optional[Dict[str, Any]] = None,
        view: Optional[discord.ui.LayoutView] = None,
        files: Optional[List[discord.File]] = None,
        allowed_mentions: Optional[discord.AllowedMentions] = None,
        platforms: Sequence[Platform] = (Platform.DISCORD,),
        locale: Optional[str] = None,
        batch_id: Optional[Any] = None,
        attribution: bool = True,
        record: Optional[Dict[str, Any]] = None,
    ) -> DeliveryResult:
        """Send one notification as a DM.

        ``view`` lets a feature keep the card it already designed (a sanction
        notice, a ticket transcript); the uniform ``content`` is still required
        because it is what the dashboard, the mail and the staff preview read.
        When ``view`` is omitted the content renders itself.

        ``record`` is for the callers that had to write the row *before*
        building their card, because the card's buttons carry the
        notification's own uuid (the beta announcement's Translate button).
        Passing it back here delivers against that row instead of writing a
        second one.
        """
        if record is None:
            record = await self.record(
                content=content, source=source,
                recipient_type=RecipientType.DISCORD_USER,
                recipient_id=getattr(user, "id", None),
                variables=variables, platforms=platforms, locale=locale,
                batch_id=batch_id,
            )
        payload = await self._build_view(record, content, source, variables, view,
                                         locale, attribution)
        return await self._deliver(
            destination=user, record=record, view=payload,
            files=files, allowed_mentions=allowed_mentions,
        )

    async def send_channel(
        self,
        channel: discord.abc.Messageable,
        *,
        content: NotificationContent,
        source: NotificationSource,
        guild_id: Optional[int] = None,
        variables: Optional[Dict[str, Any]] = None,
        view: Optional[discord.ui.LayoutView] = None,
        allowed_mentions: Optional[discord.AllowedMentions] = None,
        platforms: Sequence[Platform] = (Platform.DISCORD,),
        locale: Optional[str] = None,
        batch_id: Optional[Any] = None,
        attribution: bool = True,
    ) -> DeliveryResult:
        """Send one notification into a server channel (a server-wide notice)."""
        record = await self.record(
            content=content, source=source,
            recipient_type=RecipientType.DISCORD_GUILD,
            recipient_id=guild_id or getattr(getattr(channel, "guild", None), "id", None),
            variables=variables, platforms=platforms, locale=locale, batch_id=batch_id,
        )
        payload = await self._build_view(record, content, source, variables, view,
                                         locale, attribution)
        return await self._deliver(
            destination=channel, record=record, view=payload,
            allowed_mentions=allowed_mentions,
        )

    async def _build_view(self, record, content, source, variables, view, locale,
                          attribution: bool = True):
        """Render (or take) the view and close it with the attribution line.

        The line is appended **inside the last container** of the view — at the
        bottom of the card the recipient reads, not as a separate component —
        so a welcome DM and a sanction DM end the same way.
        """
        payload = view if view is not None else build_content_view(
            content, variables, locale=locale or "en-US")

        if not attribution or not source.has_attribution:
            # Official notices say nothing about their origin: they ARE Moddy.
            # `attribution=False` is for the callers whose card already prints
            # its own "sent by" line (sanction DMs, expiry DMs).
            return payload

        # The line is plain text built from the source itself, so a failed
        # database write costs the record, never the attribution: the recipient
        # is still told which server wrote to them.
        if record:
            ctx = await self.source_context(
                record, locale=locale or record.get("locale") or "en-US")
        else:
            logger.warning("Notification not recorded — attributing from the source")
            ctx = await resolve_source_context(self.bot, source, locale=locale or "en-US")

        line = build_attribution_line(ctx, locale=locale or "en-US")
        if line:
            _append_footer_line(payload, line)
        return payload

    async def _deliver(self, *, destination, record, view, files=None,
                       allowed_mentions=None) -> DeliveryResult:
        notification_id = str(record["id"]) if record else None
        kwargs: Dict[str, Any] = {"view": view}
        if files:
            kwargs["files"] = files
        if allowed_mentions is not None:
            kwargs["allowed_mentions"] = allowed_mentions

        try:
            message = await destination.send(**kwargs)
        except discord.Forbidden as exc:
            await self._mark(notification_id, Platform.DISCORD, DeliveryStatus.FAILED,
                             error="forbidden")
            return DeliveryResult(notification_id, None, DeliveryStatus.FAILED, exc)
        except discord.HTTPException as exc:
            await self._mark(notification_id, Platform.DISCORD, DeliveryStatus.FAILED,
                             error=str(exc))
            return DeliveryResult(notification_id, None, DeliveryStatus.FAILED, exc)

        # ``message`` is always a Message from a real Discord send; guard
        # anyway so a stubbed destination cannot turn a successful delivery
        # into an AttributeError.
        await self._mark(
            notification_id, Platform.DISCORD, DeliveryStatus.SENT,
            channel_id=getattr(getattr(message, "channel", None), "id", None),
            message_id=getattr(message, "id", None),
        )
        # The mail and the dashboard are served by the backend from the stored
        # row; the bot only declares them as targeted and leaves them pending.
        return DeliveryResult(notification_id, message, DeliveryStatus.SENT, None)

    # ------------------------------------------------------------------ #
    # Server-wide notices
    # ------------------------------------------------------------------ #

    def resolve_guild_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        """Where a server-wide Moddy notice should land.

        Priority, best first: the ``moddy-updates`` channel Moddy creates for
        exactly this, the server's Community Updates channel, its system
        channel, then any channel Moddy can actually talk in.
        """
        me = guild.me
        if me is None:
            return None

        def usable(channel) -> bool:
            return (isinstance(channel, discord.TextChannel)
                    and channel.permissions_for(me).send_messages)

        named = discord.utils.get(guild.text_channels, name=MODDY_UPDATES_CHANNEL_NAME)
        for candidate in (named, guild.public_updates_channel, guild.system_channel):
            if candidate is not None and usable(candidate):
                return candidate
        return next((c for c in guild.text_channels if usable(c)), None)

    async def notify_guild(
        self,
        guild: discord.Guild,
        *,
        content: NotificationContent,
        source: NotificationSource,
        variables: Optional[Dict[str, Any]] = None,
        dm_owner: bool = False,
        locale: Optional[str] = None,
        batch_id: Optional[Any] = None,
    ) -> List[DeliveryResult]:
        """Notify a whole server: its Moddy channel, and optionally its owner.

        Returns one :class:`DeliveryResult` per attempt. When no channel is
        usable, the owner DM is attempted even if it was not requested — a
        server with no reachable channel is exactly the case where the owner is
        the only way through.
        """
        locale = locale or (str(guild.preferred_locale) if guild.preferred_locale else "en-US")
        results: List[DeliveryResult] = []

        channel = self.resolve_guild_channel(guild)
        if channel is not None:
            results.append(await self.send_channel(
                channel, content=content, source=source, guild_id=guild.id,
                variables=variables, locale=locale, batch_id=batch_id,
                allowed_mentions=discord.AllowedMentions.none(),
            ))

        if dm_owner or channel is None:
            owner = guild.owner or (await self._fetch_owner(guild))
            if owner is not None:
                results.append(await self.send_dm(
                    owner, content=content, source=source, variables=variables,
                    locale=locale, batch_id=batch_id,
                ))
        return results

    async def _fetch_owner(self, guild: discord.Guild) -> Optional[discord.abc.User]:
        try:
            return await self.bot.fetch_user(guild.owner_id) if guild.owner_id else None
        except discord.HTTPException:
            return None

    # ------------------------------------------------------------------ #
    # Broadcasts
    # ------------------------------------------------------------------ #

    async def broadcast_users(
        self,
        user_ids: Iterable[int],
        *,
        content: NotificationContent,
        source: NotificationSource,
        variables: Optional[Dict[str, Any]] = None,
        segment: Optional[str] = None,
        locale: Optional[str] = None,
        progress: Optional[Callable[[Dict[str, int]], Any]] = None,
    ) -> Dict[str, Any]:
        """DM a group of users — potentially thousands of them.

        One notification row per recipient (they share a ``batch_id``, so the
        whole campaign is queryable), paced by :data:`BROADCAST_DELAY`, and
        resilient: a user with closed DMs is a ``failed`` delivery, not the end
        of the run. ``progress`` is called every 25 recipients so a staff
        command can keep its status message alive.
        """
        batch_id = uuid_module.uuid4()
        stats = {"total": 0, "sent": 0, "failed": 0}

        for user_id in user_ids:
            stats["total"] += 1
            user = self.bot.get_user(user_id)
            if user is None:
                try:
                    user = await self.bot.fetch_user(user_id)
                except discord.HTTPException:
                    user = None
            if user is None:
                await self.record(
                    content=content, source=source,
                    recipient_type=RecipientType.DISCORD_USER, recipient_id=user_id,
                    recipient_ref=segment, variables=variables, locale=locale,
                    batch_id=batch_id,
                )
                stats["failed"] += 1
            else:
                result = await self.send_dm(
                    user, content=content, source=source, variables=variables,
                    locale=locale, batch_id=batch_id,
                )
                stats["sent" if result.delivered else "failed"] += 1

            if progress and stats["total"] % 25 == 0:
                await _maybe_await(progress(dict(stats)))
            await asyncio.sleep(BROADCAST_DELAY)

        if progress:
            await _maybe_await(progress(dict(stats)))
        return {"batch_id": str(batch_id), **stats}

    async def broadcast_guilds(
        self,
        guild_ids: Iterable[int],
        *,
        content: NotificationContent,
        source: NotificationSource,
        variables: Optional[Dict[str, Any]] = None,
        dm_owner: bool = False,
        segment: Optional[str] = None,
        progress: Optional[Callable[[Dict[str, int]], Any]] = None,
    ) -> Dict[str, Any]:
        """Post a notice in a group of servers — potentially thousands."""
        batch_id = uuid_module.uuid4()
        stats = {"total": 0, "sent": 0, "failed": 0}

        for guild_id in guild_ids:
            stats["total"] += 1
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                stats["failed"] += 1
            else:
                results = await self.notify_guild(
                    guild, content=content, source=source, variables=variables,
                    dm_owner=dm_owner, batch_id=batch_id,
                )
                stats["sent" if any(r.delivered for r in results) else "failed"] += 1

            if progress and stats["total"] % 25 == 0:
                await _maybe_await(progress(dict(stats)))
            await asyncio.sleep(BROADCAST_DELAY)

        if progress:
            await _maybe_await(progress(dict(stats)))
        return {"batch_id": str(batch_id), **stats}

    # ------------------------------------------------------------------ #
    # Reports
    # ------------------------------------------------------------------ #

    async def open_report(
        self, *, notification_id: Any, reporter: discord.abc.User, reason: str,
        locale: str = "en-US",
    ) -> Optional[Dict[str, Any]]:
        """File a report, post the review panel, log it. Returns the report row."""
        if not self.db:
            return None
        report = await self.db.create_notification_report(
            notification_id=notification_id, reporter_id=reporter.id, reason=reason)
        if report is None:
            # Racing double-submit: hand back the existing one so the reporter
            # sees their status rather than an error.
            return await self.existing_report(notification_id, reporter.id)

        record = await self.get(notification_id)
        if record:
            await self._post_review(report, record)
            await self._log_report("created", report, record, actor=reporter)
        return report

    async def claim_report(
        self, *, report_id: Any, staff: discord.abc.User,
        interaction: Optional[discord.Interaction] = None,
    ) -> bool:
        """Assign a pending report and refresh its panel."""
        if not self.db:
            return False
        report = await self.db.claim_notification_report(report_id, staff.id)
        if report is None:
            if interaction is not None and not interaction.response.is_done():
                await interaction.response.defer()
            return False

        record = await self.get(report["notification_id"])
        if interaction is not None and not interaction.response.is_done():
            await interaction.response.defer()
        if record:
            await self._refresh_review(report, record)
            await self._log_report("claimed", report, record, actor=staff)
        return True

    async def decide_report(
        self, *, report_id: Any, staff: discord.abc.User, status: str,
        note: Optional[str] = None, locale: str = "en-US",
    ) -> bool:
        """Accept or refuse a report, refresh the panel, log it, tell the reporter."""
        if not self.db:
            return False
        report = await self.db.decide_notification_report(report_id, staff.id, status, note)
        if report is None:
            return False

        record = await self.get(report["notification_id"])
        if record:
            await self._refresh_review(report, record)
            await self._log_report(status, report, record, actor=staff)
            await self._notify_reporter(report, record)
        return True

    # -- report plumbing --------------------------------------------------- #

    def _channel(self, channel_id: int) -> Optional[discord.abc.Messageable]:
        return self.bot.get_channel(channel_id) if channel_id else None

    async def _post_review(self, report: Dict[str, Any], record: Dict[str, Any]) -> None:
        from utils.notification_views import build_review_panel

        channel = self._channel(config.MODDY_NOTIF_REPORT_CHANNEL_ID)
        if channel is None:
            logger.warning("Notification report channel unavailable — report %s not posted",
                           report["id"])
            return
        ctx = await self.source_context(record, locale=self._staff_locale())
        try:
            message = await channel.send(
                view=build_review_panel(report=report, record=record, ctx=ctx,
                                        locale=self._staff_locale()),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException as exc:
            logger.warning("Could not post notification report panel: %s", exc)
            return
        await self.db.set_report_review_message(report["id"], channel.id, message.id)
        # Keep the caller's dict in step so the "created" log entry can already
        # link to the panel it just posted.
        report["review_channel_id"] = channel.id
        report["review_message_id"] = message.id

    async def _refresh_review(self, report: Dict[str, Any], record: Dict[str, Any]) -> None:
        """Edit the stored panel — never the interaction's own message, which
        may be an ephemeral follow-up."""
        from utils.notification_views import build_review_panel

        channel_id, message_id = report.get("review_channel_id"), report.get("review_message_id")
        if not channel_id or not message_id:
            return
        channel = self._channel(channel_id)
        if channel is None:
            return
        ctx = await self.source_context(record, locale=self._staff_locale())
        try:
            message = await channel.fetch_message(message_id)
            await message.edit(view=build_review_panel(
                report=report, record=record, ctx=ctx, locale=self._staff_locale()))
        except discord.HTTPException as exc:
            logger.warning("Could not refresh notification report panel: %s", exc)

    async def _log_report(self, event: str, report: Dict[str, Any],
                          record: Dict[str, Any], *, actor=None) -> None:
        from utils.notification_views import build_report_log

        channel = self._channel(config.MODDY_NOTIF_REPORT_LOG_CHANNEL_ID)
        if channel is None:
            return
        jump_url = None
        if report.get("review_channel_id") and report.get("review_message_id"):
            jump_url = (f"https://discord.com/channels/{config.MODDY_TEAM_GUILD_ID}/"
                        f"{report['review_channel_id']}/{report['review_message_id']}")
        try:
            await channel.send(
                view=build_report_log(event=event, report=report, record=record,
                                      actor=actor, jump_url=jump_url,
                                      locale=self._staff_locale()),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException as exc:
            logger.warning("Could not log notification report event: %s", exc)

    async def _notify_reporter(self, report: Dict[str, Any], record: Dict[str, Any]) -> None:
        """Close the loop: the reporter hears back, through this same system."""
        from utils.i18n import t

        user = self.bot.get_user(report["reporter_id"])
        if user is None:
            try:
                user = await self.bot.fetch_user(report["reporter_id"])
            except discord.HTTPException:
                return

        locale = record.get("locale") or "en-US"
        status = report["status"]
        from utils.emojis import DONE, UNDONE
        content = NotificationContent(
            title=t(f"notifications.report.outcome.{status}.title", locale=locale),
            body=t(f"notifications.report.outcome.{status}.description", locale=locale),
            icon=DONE if status == ReportStatus.ACCEPTED.value else UNDONE,
            accent_color=0x57F287 if status == ReportStatus.ACCEPTED.value else 0x99AAB5,
            footer=t("notifications.report.outcome.footer", locale=locale,
                     report="{report}"),
            template_id="notifications.report.outcome",
        )
        if report.get("decision_note"):
            content.sections = [{
                "title": t("notifications.review.note", locale=locale),
                "body": "{note}",
            }]
        await self.send_dm(
            user, content=content,
            source=NotificationSource.official("moddy"),
            variables={"report": str(report["id"]), "note": report.get("decision_note") or ""},
            locale=locale,
        )

    def _staff_locale(self) -> str:
        """Staff-facing panels are rendered in one language, like the appeal
        review panels (``services/appeal_service._PANEL_LOCALE``)."""
        return STAFF_PANEL_LOCALE


def _append_footer_line(view: discord.ui.LayoutView, line: str) -> None:
    """Add ``line`` at the bottom of the view's last container.

    Inside the container rather than under it: a `-#` line floating as its own
    top-level component reads as a separate message. Falls back to a top-level
    text block when the view has no container, or when the container is already
    at Discord's child limit.
    """
    for child in reversed(list(view.children)):
        if isinstance(child, discord.ui.Container):
            try:
                child.add_item(discord.ui.TextDisplay(line))
                return
            except Exception as exc:  # noqa: BLE001 — full container, keep the line
                logger.debug("Could not append the attribution line inside the "
                             "container (%s), falling back to top level", exc)
                break
    try:
        view.add_item(discord.ui.TextDisplay(line))
    except Exception as exc:  # noqa: BLE001 — never lose the message over its footer
        logger.warning("Could not append the attribution line: %s", exc)


async def _maybe_await(value):
    if asyncio.iscoroutine(value):
        return await value
    return value
