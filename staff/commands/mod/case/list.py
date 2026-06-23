"""`/mod case list` — list moderation cases for a user or guild."""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from staff.commands.mod.case._shared import resolve_entity
from utils import emojis
from utils.i18n import t
from utils.moderation_cases import ModerationCase, CaseStatus


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
        entity_type, entity_id, entity_name, error = await resolve_entity(ctx)
        if error:
            await ctx.send(view=error)
            return

        cases = await ctx.bot.db.get_entity_cases(entity_type=entity_type.value, entity_id=entity_id)
        if not cases:
            await ctx.send(view=design.info(
                t("staff.mod.case.list_empty_title", locale=ctx.locale),
                t("staff.mod.case.list_empty", locale=ctx.locale, name=f"**{entity_name}**"),
            ))
            return

        lines = []
        for case_dict in cases[:10]:
            case = ModerationCase.from_db(case_dict)
            dot = emojis.GREEN_STATUS if case.status == CaseStatus.OPEN else emojis.RED_STATUS
            lines.append(
                f"{dot} **#{case.case_id}** — {case.get_sanction_emoji()} {case.get_sanction_name()} "
                f"(<t:{int(case.created_at.timestamp())}:R>)"
            )

        body = "\n".join(lines)
        if len(cases) > 10:
            body += f"\n-# {t('staff.mod.case.list_more', locale=ctx.locale)}"

        await ctx.send(view=design.panel(
            "info",
            t("staff.mod.case.list_title", locale=ctx.locale, name=entity_name),
            f"**{t('staff.mod.case.list_count', locale=ctx.locale, shown=min(10, len(cases)), total=len(cases))}**\n\n{body}",
            emoji=emojis.BLACKLIST,
            accent="error",
        ))
