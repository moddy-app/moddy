"""``/com beta`` — the one-off beta-launch announcement to server owners.

Temporary, like ``utils/beta_announcement.py``: it exists to tell the people
who installed Moddy during development that the bot is entering beta, and to
hand them their two free months of Moddy Max. Once the campaign is done, both
files can be deleted with nothing left behind.

Three targets:

``test``    one user, by id — the copy they receive is the real thing, with
            their own name and the servers they own. Use this first.
``owners``  every owner of a server Moddy is in: one DM per *owner*, naming
            all of their servers, not one DM per server.
``preview`` renders the card ephemerally without sending or recording anything.

Everything goes through the notification system, targeting Discord, the
dashboard and email at once (the backend serves the last two from the stored
row), and the whole run shares one ``batch_id``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid as uuid_module
from typing import Any, Dict, List, Tuple

import discord
from discord import ui

from cogs.error_handler import BaseModal, BaseView
from notifications.models import (
    ContentAuthor, NotificationSource, Platform, RecipientType,
)
from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from utils import emojis
from utils.beta_announcement import (
    DEFAULT_LOCALE, beta_content, build_beta_view, format_servers, owner_server_map,
)
from utils.i18n import t

logger = logging.getLogger("moddy.staff.com.beta")

#: Pause between two DMs. Same reasoning as the notification service's own
#: broadcast delay: a few thousand DMs must not eat the whole rate budget.
SEND_DELAY = 0.35

#: Platforms the campaign targets. Discord is delivered by the bot; the mail
#: and the dashboard card are served by the backend from the stored row.
PLATFORMS = (Platform.DISCORD, Platform.DASHBOARD, Platform.EMAIL)


@staff_command
class ComBetaCommand(StaffCommand):
    command_type = CommandType.COMMUNICATION
    name = "beta"
    permission = "broadcast"
    description = "Send the beta-launch announcement to server owners."
    options = [
        SlashOption("target", "string", "Who receives it.", required=True,
                    choices=["preview", "test", "owners"]),
        SlashOption("recipient", "string", "Target 'test': the user id to send to.",
                    required=False),
    ]

    def parse_message(self, raw: str) -> dict:
        parts = (raw or "").split()
        return {
            "target": parts[0] if parts else None,
            "recipient": parts[1] if len(parts) > 1 else None,
        }

    async def execute(self, ctx):
        target = (ctx.opt("target") or "").lower()
        recipient = (ctx.opt("recipient") or "").strip()

        if target not in ("preview", "test", "owners"):
            await ctx.send(view=design.invalid_usage(
                ctx.locale, "com.beta <preview|test|owners> [user_id]"))
            return

        if target == "preview":
            await ctx.send(view=build_beta_view(
                variables={"user": ctx.author.global_name or ctx.author.name,
                           "servers": format_servers([])},
                locale=ctx.locale))
            return

        if target == "test":
            user, error = await _resolve_user(ctx.bot, recipient, ctx.locale)
            if error is not None:
                await ctx.send(view=error)
                return
            stats = await _send_campaign(ctx.bot, [(user.id, _owned(ctx.bot, user.id))],
                                         actor_id=ctx.author.id)
            await ctx.send(view=_done_panel(stats, locale=ctx.locale))
            return

        # --- the real thing --------------------------------------------- #
        owners = owner_server_map(ctx.bot)
        audience: List[Tuple[int, List[discord.Guild]]] = sorted(
            owners.items(), key=lambda item: len(item[1]), reverse=True)
        if not audience:
            await ctx.send(view=design.error(
                t("staff.com.beta.errors.empty.title", locale=ctx.locale),
                t("staff.com.beta.errors.empty.description", locale=ctx.locale)))
            return

        # A campaign of thousands of DMs cannot be recalled, so confirming it
        # is a Modal the sender has to type into — not a button a mis-click can
        # hit. The word to type is deliberately not localized.
        await ctx.open_modal(
            lambda: BetaConfirmModal(ctx=ctx, audience=audience),
            label=t("staff.com.beta.confirm.button", locale=ctx.locale),
            emoji=emojis.MESSAGE,
            prompt_title=t("staff.com.beta.confirm.title", locale=ctx.locale),
            prompt_description=t("staff.com.beta.confirm.description", locale=ctx.locale,
                                 owners=len(audience), guilds=len(ctx.bot.guilds)),
        )


# --------------------------------------------------------------------------- #
# Confirmation
# --------------------------------------------------------------------------- #

#: What has to be typed to launch the campaign. Not localized on purpose: the
#: point is a deliberate, unambiguous gesture, not a comprehension test.
CONFIRM_WORD = "SEND"


class BetaConfirmModal(BaseModal):
    """Type the word, send the campaign.

    Modal V2 (docs/MODALS_V2.md): a TextDisplay states exactly what is about to
    happen, and one field takes the confirmation word.
    """

    def __init__(self, *, ctx, audience: List[Tuple[int, List[discord.Guild]]]):
        locale = ctx.locale
        super().__init__(title=t("staff.com.beta.confirm.title", locale=locale)[:45])
        self.ctx = ctx
        self.audience = audience
        self.locale = locale

        self.add_item(ui.TextDisplay(
            t("staff.com.beta.confirm.description", locale=locale,
              owners=len(audience), guilds=len(ctx.bot.guilds))))
        self.word = ui.Label(
            text=t("staff.com.beta.confirm.field.label", locale=locale)[:45],
            description=t("staff.com.beta.confirm.field.description",
                          locale=locale, word=CONFIRM_WORD)[:100],
            component=ui.TextInput(
                style=discord.TextStyle.short, max_length=20, required=True,
                placeholder=CONFIRM_WORD),
        )
        self.add_item(self.word)

    async def on_submit(self, interaction: discord.Interaction):
        if (self.word.component.value or "").strip().upper() != CONFIRM_WORD:
            await interaction.response.send_message(view=design.error(
                t("staff.com.beta.errors.not_confirmed.title", locale=self.locale),
                t("staff.com.beta.errors.not_confirmed.description",
                  locale=self.locale, word=CONFIRM_WORD),
            ), ephemeral=True)
            return

        await interaction.response.send_message(
            view=_progress_panel({"total": 0, "sent": 0, "failed": 0},
                                 total=len(self.audience), locale=self.locale),
            ephemeral=True)
        await _start(interaction, self.ctx, self.audience)


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #

def _owned(bot, user_id: int) -> List[discord.Guild]:
    return [g for g in bot.guilds if g.owner_id == user_id]


async def _resolve_user(bot, recipient: str, locale: str):
    if not recipient.isdigit():
        return None, design.error(
            t("staff.com.beta.errors.bad_id.title", locale=locale),
            t("staff.com.beta.errors.bad_id.description", locale=locale))
    user = bot.get_user(int(recipient))
    if user is None:
        try:
            user = await bot.fetch_user(int(recipient))
        except discord.HTTPException:
            user = None
    if user is None:
        return None, design.error(
            t("staff.com.beta.errors.unknown_user.title", locale=locale),
            t("staff.com.beta.errors.unknown_user.description", locale=locale,
              id=recipient))
    return user, None


async def _send_campaign(bot, audience: List[Tuple[int, List[discord.Guild]]], *,
                         actor_id: int, progress=None) -> Dict[str, Any]:
    """DM every owner their own copy of the announcement.

    Not ``broadcast_users``: each recipient's message names *their* servers, so
    the variables differ per copy — which is exactly what the notification
    system's template/variables split is for. One ``batch_id`` ties the run
    together, one stored body serves every copy.
    """
    batch_id = uuid_module.uuid4()
    content = beta_content(DEFAULT_LOCALE)
    source = NotificationSource.service(
        "moddy_team", author=ContentAuthor.STAFF, actor_id=actor_id)
    stats = {"total": 0, "sent": 0, "failed": 0, "batch_id": str(batch_id)}

    for user_id, guilds in audience:
        stats["total"] += 1
        user = bot.get_user(user_id)
        if user is None:
            try:
                user = await bot.fetch_user(user_id)
            except discord.HTTPException:
                user = None
        if user is None or getattr(user, "bot", False):
            stats["failed"] += 1
            continue

        variables = {
            "user": user.global_name or user.name,
            "servers": format_servers(guilds),
        }

        # The card carries the notification's own uuid (its Translate button
        # reads the stored variables), so the row has to exist first — hence
        # record, build, then send, rather than a plain send_dm.
        record = await bot.notifications.record(
            content=content, source=source,
            recipient_type=RecipientType.DISCORD_USER, recipient_id=user.id,
            variables=variables, platforms=PLATFORMS, locale=DEFAULT_LOCALE,
            batch_id=batch_id,
        )
        view = build_beta_view(
            variables=variables, locale=DEFAULT_LOCALE,
            notification_id=str(record["id"]) if record else None)
        result = await bot.notifications.send_dm(
            user, content=content, source=source, variables=variables,
            view=view, platforms=PLATFORMS, locale=DEFAULT_LOCALE,
            batch_id=batch_id, record=record,
        )
        stats["sent" if result.delivered else "failed"] += 1

        if progress and stats["total"] % 25 == 0:
            await progress(dict(stats))
        await asyncio.sleep(SEND_DELAY)

    if progress:
        await progress(dict(stats))
    return stats


async def _start(interaction: discord.Interaction, ctx,
                 audience: List[Tuple[int, List[discord.Guild]]]) -> None:
    """Run the campaign in the background, editing the panel as it goes.

    The caller has already answered the interaction with the first progress
    panel; everything from here on edits that message.
    """
    total = len(audience)

    async def _progress(stats: Dict[str, Any]) -> None:
        try:
            await interaction.edit_original_response(
                view=_progress_panel(stats, total=total, locale=ctx.locale))
        except discord.HTTPException:
            pass  # token expired on a long run: the batch continues regardless

    async def _run() -> None:
        try:
            stats = await _send_campaign(ctx.bot, audience, actor_id=ctx.author.id,
                                         progress=_progress)
            logger.info("Beta announcement %s finished: %s", stats["batch_id"], stats)
            try:
                await interaction.edit_original_response(
                    view=_done_panel(stats, locale=ctx.locale))
            except discord.HTTPException:
                pass
        except Exception as exc:  # noqa: BLE001 — a background task must not die silently
            logger.error("Beta announcement failed: %s", exc, exc_info=True)

    asyncio.create_task(_run())


def _progress_panel(stats: Dict[str, Any], *, total: int, locale: str) -> BaseView:
    return design.panel(
        "loading",
        t("staff.com.beta.progress.title", locale=locale),
        t("staff.com.beta.progress.description", locale=locale,
          done=stats.get("total", 0), total=total,
          sent=stats.get("sent", 0), failed=stats.get("failed", 0)),
    )


def _done_panel(stats: Dict[str, Any], *, locale: str) -> BaseView:
    return design.success(
        t("staff.com.beta.done.title", locale=locale),
        t("staff.com.beta.done.description", locale=locale,
          total=stats.get("total", 0), sent=stats.get("sent", 0),
          failed=stats.get("failed", 0)),
        footer=f"batch `{stats.get('batch_id')}`",
    )
