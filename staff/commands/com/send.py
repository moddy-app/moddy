"""`/com send` — send a Moddy notification to a user, a server, or thousands.

Four audiences from one command:

``user``    one Discord user, by id — a DM.
``guild``   one server: its Moddy channel (``moddy-updates`` → Community
            Updates → system channel), optionally its owner too.
``users``   a group of users: ``all``, or every user carrying an attribute
            (``PREMIUM``, ``BETA``…). Can be thousands.
``guilds``  a group of servers, same segment syntax (``OFFICIAL``, ``PARTNER``…).

The wording is written in a Modal V2 and stored as a uniform notification
payload, so the same announcement can be rendered by the dashboard and the mail
pipeline without being retyped. Group sends are confirmed first, then run in the
background with a live progress panel; everything is recorded under one
``batch_id``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

import discord

from cogs.error_handler import BaseView
from notifications.models import ContentAuthor, NotificationContent, NotificationSource
from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from staff.commands.com._send_modal import NotificationComposeModal
from staff.framework.views import ConfirmView
from utils import emojis
from utils.i18n import t
from utils.interaction_response import safe_defer

logger = logging.getLogger("moddy.staff.com.send")

#: Audience keyword meaning "everyone Moddy knows".
SEGMENT_ALL = "all"

#: Above this many recipients the confirmation panel carries a warning: a
#: broadcast cannot be recalled, and it paces itself at ~3 sends a second.
LARGE_AUDIENCE = 500


@staff_command
class ComSendCommand(StaffCommand):
    command_type = CommandType.COMMUNICATION
    name = "send"
    permission = "broadcast"
    description = "Send a Moddy notification to a user, a server, or a group of them."
    # Answers with a Modal: Discord refuses one on a deferred interaction.
    opens_modal = True
    options = [
        SlashOption("target", "string", "Who receives it.", required=True,
                    choices=["user", "guild", "users", "guilds"]),
        SlashOption("recipient", "string",
                    "A Discord id, or a segment: 'all' or an attribute (PREMIUM, OFFICIAL…).",
                    required=True),
        SlashOption("dm_owner", "boolean",
                    "Server targets: also DM the server owner.", required=False, default=False),
    ]

    def parse_message(self, raw: str) -> dict:
        parts = (raw or "").split()
        return {
            "target": parts[0] if parts else None,
            "recipient": parts[1] if len(parts) > 1 else None,
            "dm_owner": len(parts) > 2 and parts[2].lower() in ("true", "yes", "1"),
        }

    async def execute(self, ctx):
        target = (ctx.opt("target") or "").lower()
        recipient = (ctx.opt("recipient") or "").strip()
        dm_owner = bool(ctx.opt("dm_owner", False))

        if target not in ("user", "guild", "users", "guilds") or not recipient:
            await ctx.send(view=design.invalid_usage(
                ctx.locale, "com.send <user|guild|users|guilds> <id|segment> [dm_owner]"))
            return

        # Resolving the audience costs a user fetch or a segment query. On the
        # slash transport that has to wait: this command is ``opens_modal``, so
        # the interaction is still unacknowledged and the composer must go out
        # inside Discord's 3-second window. ``_handle_composed`` resolves it on
        # the modal's own interaction instead. The message transport has no such
        # window, and its prompt names the audience, so it resolves up front.
        audience = None
        if not ctx.is_slash:
            audience, error = await _resolve_audience(ctx.bot, target, recipient, ctx.locale)
            if error is not None:
                await ctx.send(view=error)
                return

        async def _composed(interaction: discord.Interaction, payload: Dict[str, Any]):
            await _handle_composed(
                interaction, ctx, target=target, recipient=recipient,
                audience=audience, dm_owner=dm_owner, payload=payload)

        await ctx.open_modal(
            lambda: NotificationComposeModal(locale=ctx.locale, on_content=_composed),
            label=t("staff.com.send.compose", locale=ctx.locale), emoji=emojis.MESSAGE,
            prompt_title=t("staff.com.send.compose", locale=ctx.locale),
            prompt_description=t("staff.com.send.compose_hint", locale=ctx.locale,
                                 audience=_audience_label(target, recipient, audience)),
        )


# --------------------------------------------------------------------------- #
# Audience
# --------------------------------------------------------------------------- #

async def _resolve_audience(bot, target: str, recipient: str, locale: str
                            ) -> Tuple[Optional[Any], Optional[BaseView]]:
    """Turn ``target`` + ``recipient`` into something sendable, or an error panel."""
    if target in ("user", "guild"):
        if not recipient.isdigit():
            return None, design.error(
                t("staff.com.send.errors.bad_id.title", locale=locale),
                t("staff.com.send.errors.bad_id.description", locale=locale))
        entity_id = int(recipient)
        if target == "user":
            user = bot.get_user(entity_id)
            if user is None:
                try:
                    user = await bot.fetch_user(entity_id)
                except discord.HTTPException:
                    user = None
            if user is None:
                return None, design.error(
                    t("staff.com.send.errors.unknown_user.title", locale=locale),
                    t("staff.com.send.errors.unknown_user.description", locale=locale,
                      id=entity_id))
            return user, None

        guild = bot.get_guild(entity_id)
        if guild is None:
            return None, design.error(
                t("staff.com.send.errors.unknown_guild.title", locale=locale),
                t("staff.com.send.errors.unknown_guild.description", locale=locale,
                  id=entity_id))
        return guild, None

    segment = recipient.strip()
    if target == "users":
        if segment.lower() == SEGMENT_ALL:
            ids = await bot.db.get_all_user_ids()
        else:
            ids = await bot.db.get_users_with_attribute(segment.upper())
    else:
        known = {g.id for g in bot.guilds}
        if segment.lower() == SEGMENT_ALL:
            ids = sorted(known)
        else:
            ids = [gid for gid in await bot.db.get_guilds_with_attribute(segment.upper())
                   if gid in known]

    if not ids:
        return None, design.error(
            t("staff.com.send.errors.empty_segment.title", locale=locale),
            t("staff.com.send.errors.empty_segment.description", locale=locale,
              segment=segment))
    return ids, None


def _audience_label(target: str, recipient: str, audience: Any) -> str:
    """Name the audience for the prompt — ``audience`` may not be resolved yet."""
    if audience is None:
        return f"`{recipient}`"
    if target in ("user", "guild"):
        name = getattr(audience, "name", None) or getattr(audience, "display_name", recipient)
        return f"{name} (`{getattr(audience, 'id', recipient)}`)"
    return f"`{len(audience)}` · `{recipient}`"


# --------------------------------------------------------------------------- #
# Composition -> send
# --------------------------------------------------------------------------- #

def _build_content(payload: Dict[str, Any]) -> NotificationContent:
    """The staff-written announcement, as a uniform notification payload."""
    return NotificationContent(
        title=payload["title"],
        body=payload["body"],
        icon=emojis.MODDY_SQUARE_MIN,
        accent_color=0x3661FF,
        links=payload.get("links") or [],
        template_id="staff.com.send",
    )


async def _handle_composed(interaction: discord.Interaction, ctx, *, target: str,
                           recipient: str, audience: Any, dm_owner: bool,
                           payload: Dict[str, Any]) -> None:
    """Single targets go out immediately; group targets are confirmed first.

    ``audience`` is ``None`` when the command opened the composer before
    resolving it (the slash path): this interaction is fresh, so the lookup
    happens here, behind a defer.
    """
    locale = ctx.locale
    # One defer for every branch: the audience lookup and the sends that follow
    # all outlast the 3-second window, and a deferred interaction still answers
    # — through `followup`, or by editing the placeholder it just put up.
    await safe_defer(interaction, ephemeral=True, thinking=True)

    if audience is None:
        audience, error = await _resolve_audience(ctx.bot, target, recipient, locale)
        if error is not None:
            await interaction.followup.send(view=error, ephemeral=True)
            return

    content = _build_content(payload)
    source = NotificationSource.service(
        "moddy", author=ContentAuthor.STAFF, actor_id=interaction.user.id)

    if target == "user":
        result = await ctx.bot.notifications.send_dm(
            audience, content=content, source=source, locale=locale)
        await interaction.followup.send(view=_single_result(
            result, name=str(audience), locale=locale), ephemeral=True)
        return

    if target == "guild":
        results = await ctx.bot.notifications.notify_guild(
            audience, content=content, source=source, dm_owner=dm_owner)
        delivered = [r for r in results if r.delivered]
        await interaction.followup.send(view=design.panel(
            "success" if delivered else "error",
            t("staff.com.send.guild_done.title" if delivered
              else "staff.com.send.errors.no_channel.title", locale=locale),
            t("staff.com.send.guild_done.description" if delivered
              else "staff.com.send.errors.no_channel.description",
              locale=locale, guild=audience.name, count=len(delivered)),
            footer=(f"{t('staff.notif.title', locale=locale)}: "
                    f"{', '.join(r.notification_id or '—' for r in results)}"),
        ), ephemeral=True)
        return

    # --- group targets ---------------------------------------------------- #
    total = len(audience)
    confirm = ConfirmView(
        bot=ctx.bot, author_id=interaction.user.id, locale=locale,
        title=t("staff.com.send.confirm.title", locale=locale),
        description=t("staff.com.send.confirm.description", locale=locale,
                      count=total, segment=recipient,
                      kind=t(f"staff.com.send.audience.{target}", locale=locale))
        + (f"\n\n{emojis.WARNING} **"
           f"{t('staff.com.send.confirm.large', locale=locale)}**"
           if total >= LARGE_AUDIENCE else "")
        + f"\n\n**{payload['title']}**\n-# {payload['body'][:300]}",
        confirm_label=t("staff.com.send.confirm.button", locale=locale),
        danger=True, emoji=emojis.MESSAGE,
        on_confirm=lambda i: _start_broadcast(
            i, ctx, target=target, recipient=recipient, audience=audience,
            content=content, source=source, dm_owner=dm_owner, locale=locale),
    )
    # Edit, not followup: the defer above already put a placeholder in front of
    # the sender, and the confirmation belongs in it rather than beside it.
    await interaction.edit_original_response(view=confirm)


def _single_result(result, *, name: str, locale: str) -> BaseView:
    if result.delivered:
        return design.success(
            t("staff.com.send.user_done.title", locale=locale),
            t("staff.com.send.user_done.description", locale=locale, user=name),
            footer=f"`{result.notification_id}`")
    key = "dms_closed" if result.forbidden else "failed"
    return design.error(
        t(f"staff.com.send.errors.{key}.title", locale=locale),
        t(f"staff.com.send.errors.{key}.description", locale=locale, user=name))


async def _start_broadcast(interaction: discord.Interaction, ctx, *, target: str,
                           recipient: str, audience: List[int],
                           content: NotificationContent, source: NotificationSource,
                           dm_owner: bool, locale: str) -> BaseView:
    """Kick the broadcast off in the background and hand back a live panel.

    ``ConfirmView`` edits the message with whatever this returns, so the first
    panel is "started"; the task then keeps editing it as recipients are
    processed. A broadcast outliving the interaction token simply stops
    updating — the batch itself keeps running and stays queryable by id.
    """
    async def _progress(stats: Dict[str, int]) -> None:
        try:
            await interaction.edit_original_response(
                view=_progress_panel(stats, total=len(audience), locale=locale))
        except discord.HTTPException:
            pass  # token expired on a long run: the batch continues regardless

    async def _run() -> None:
        try:
            if target == "users":
                stats = await ctx.bot.notifications.broadcast_users(
                    audience, content=content, source=source, segment=recipient,
                    progress=_progress)
            else:
                stats = await ctx.bot.notifications.broadcast_guilds(
                    audience, content=content, source=source, segment=recipient,
                    dm_owner=dm_owner, progress=_progress)
            logger.info("Broadcast %s finished: %s", stats.get("batch_id"), stats)
            try:
                await interaction.edit_original_response(
                    view=_done_panel(stats, locale=locale))
            except discord.HTTPException:
                pass
        except Exception as exc:  # noqa: BLE001 — a background task must not die silently
            # Same reasoning as the progress edits above: the sender is watching
            # a panel that would otherwise never finish, so the failure gets an
            # error code they can quote instead of a silent log line.
            from cogs.error_handler import ErrorView, report_error
            error_code = await report_error(
                ctx.bot, exc, source="Staff:com.send", user=ctx.author,
                guild=ctx.guild, channel=ctx.channel,
                error_type="Staff Command Error",
            )
            if error_code:
                try:
                    await interaction.edit_original_response(view=ErrorView(error_code))
                except discord.HTTPException:
                    pass

    asyncio.create_task(_run())
    return _progress_panel({"total": 0, "sent": 0, "failed": 0},
                           total=len(audience), locale=locale)


def _progress_panel(stats: Dict[str, int], *, total: int, locale: str) -> BaseView:
    done = stats.get("total", 0)
    return design.panel(
        "loading",
        t("staff.com.send.progress.title", locale=locale),
        t("staff.com.send.progress.description", locale=locale,
          done=done, total=total, sent=stats.get("sent", 0),
          failed=stats.get("failed", 0)),
    )


def _done_panel(stats: Dict[str, int], *, locale: str) -> BaseView:
    return design.success(
        t("staff.com.send.done.title", locale=locale),
        t("staff.com.send.done.description", locale=locale,
          total=stats.get("total", 0), sent=stats.get("sent", 0),
          failed=stats.get("failed", 0)),
        footer=f"batch `{stats.get('batch_id')}`",
    )
