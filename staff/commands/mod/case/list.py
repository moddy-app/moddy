"""`/mod case list` — list moderation cases for a user or guild."""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from staff.commands.mod.case._shared import resolve_subject
from utils import emojis
from utils.i18n import t
from utils.moderation_cases import CaseStatus, get_case_type_emoji, CaseType


@staff_command
class CaseListCommand(StaffCommand):
    command_type = CommandType.MODERATOR
    group = "case"
    group_description = "Moderation case management"
    name = "list"
    permission = "case_list"
    description = "List moderation cases for a user or guild."
    options = [
        SlashOption("user", "user", "Target user.", required=False),
        SlashOption("guild_id", "string", "Target guild id (instead of a user).", required=False),
    ]

    async def execute(self, ctx):
        subject_type, subject_id, subject_name, error = await resolve_subject(ctx)
        if error:
            await ctx.send(view=error)
            return

        rows = await ctx.bot.db.list_subject_cases(subject_type.value, subject_id, limit=25)
        total = await ctx.bot.db.count_subject_cases(subject_type.value, subject_id)
        if not rows:
            await ctx.send(view=design.info(
                t("staff.mod.case.list_empty_title", locale=ctx.locale),
                t("staff.mod.case.list_empty", locale=ctx.locale, name=f"**{subject_name}**"),
            ))
            return

        lines = []
        for row in rows[:15]:
            status = CaseStatus(row["status"])
            dot = emojis.GREEN_STATUS if status == CaseStatus.OPEN else emojis.RED_STATUS
            type_emoji = get_case_type_emoji(CaseType(row["type"]))
            created_ts = int(row["created_at"].timestamp())
            type_label = t("staff.mod.case.type_value." + row["type"], locale=ctx.locale)
            reference = row["reference"]
            lines.append(
                f"{dot} {type_emoji} **{reference}** `{type_label}` • <t:{created_ts}:R>"
            )

        body = "\n".join(lines)
        if total > 15:
            body += f"\n-# {t('staff.mod.case.list_more', locale=ctx.locale)}"

        await ctx.send(view=design.panel(
            "info",
            t("staff.mod.case.list_title", locale=ctx.locale, name=subject_name),
            f"**{t('staff.mod.case.list_count', locale=ctx.locale, shown=min(15, len(rows)), total=total)}**\n\n{body}",
            emoji=emojis.BLACKLIST,
            accent="error",
        ))
