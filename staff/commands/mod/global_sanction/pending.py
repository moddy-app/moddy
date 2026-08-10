"""`/mod global pending` — the enforcement queue.

Lists the global sanction groups whose grace period is still running: who is
about to lose their subscription, which servers Moddy is about to leave, and
when. This is the board a moderator checks before the deadlines fire.
"""

import discord
from discord import ui

from cogs.error_handler import BaseView
from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from staff.commands.mod.global_sanction._shared import GROUP, GROUP_DESCRIPTION
from utils import emojis
from utils.global_sanctions import GlobalLevel, level_accent, level_emoji
from utils.i18n import t

_STATUSES = ("pending", "halted", "executed", "cancelled")
_LIMIT = 15


@staff_command
class GlobalPendingCommand(StaffCommand):
    command_type = CommandType.MODERATOR
    group = GROUP
    group_description = GROUP_DESCRIPTION
    name = "pending"
    permission = "case_list"
    description = "List global sanction groups whose enforcement countdown is running."
    options = [
        SlashOption("status", "string", "Which countdowns to list.",
                    required=False, default="pending", choices=list(_STATUSES)),
    ]

    async def execute(self, ctx):
        status = ctx.opt("status") or "pending"
        if status not in _STATUSES:
            status = "pending"

        rows = await ctx.bot.db.list_enforcements_by_status(status, limit=_LIMIT)

        if not rows:
            await ctx.send(view=design.info(
                t("global_sanctions.staff.pending_title", locale=ctx.locale),
                t("global_sanctions.staff.pending_empty", locale=ctx.locale),
            ))
            return

        # The list is about pending enforcement, so it wears the heaviest
        # accent present in it — red as soon as one suspension is queued.
        levels = [_level_of(r) for r in rows]
        accent = max(levels, key=lambda l: (l is GlobalLevel.SUSPENDED,
                                            l is GlobalLevel.LIMITED))

        view = BaseView()
        container = ui.Container(accent_colour=discord.Colour(level_accent(accent)))
        container.add_item(ui.TextDisplay(
            f"### {emojis.TIME} {t('global_sanctions.staff.pending_title', locale=ctx.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            f"-# {t('global_sanctions.staff.pending_hint', locale=ctx.locale, status=status)}"
        ))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        for row in rows:
            level = _level_of(row)
            deadline = row.get("deadline")
            when = f"<t:{int(deadline.timestamp())}:R>" if deadline else "—"
            premium = f" • {emojis.PREMIUM}" if row.get("premium") else ""
            container.add_item(ui.TextDisplay(
                f"{level_emoji(level)} <@{row['subject_id']}> (`{row['subject_id']}`)"
                f" • {when}{premium}\n"
                f"-# `{row['group_id']}`"
            ))

        view.add_item(container)
        await ctx.send(view=view)


def _level_of(row) -> GlobalLevel:
    try:
        return GlobalLevel(row.get("level") or "none")
    except ValueError:
        return GlobalLevel.NONE
