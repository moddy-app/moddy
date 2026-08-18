"""`/mod altguard refusal` — explain one AltGuard decision from its id.

This is the only place the signals behind a refusal are readable. They are
never surfaced to the member being verified, and never to a guild's own
moderators beyond the labels already written to their log channel: a precise,
self-service explanation of *why* a check failed is a bypass recipe.
"""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from staff.commands.mod.altguard._shared import GROUP, GROUP_DESCRIPTION, PERMISSION
from utils.i18n import t


@staff_command
class AltGuardRefusalCommand(StaffCommand):
    command_type = CommandType.MODERATOR
    group = GROUP
    group_description = GROUP_DESCRIPTION
    name = "refusal"
    permission = PERMISSION
    description = "Explain an AltGuard decision from its verification id."
    options = [
        SlashOption("verification_id", "string",
                    "The verification id shown in the server's AltGuard logs.",
                    required=True),
    ]

    async def execute(self, ctx):
        verification_id = (ctx.opt("verification_id") or "").strip()
        if not verification_id:
            await ctx.send(view=design.error(
                t("staff.mod.altguard.invalid_title", locale=ctx.locale),
                t("staff.mod.altguard.refusal_invalid", locale=ctx.locale),
            ))
            return

        record = await ctx.bot.db.get_altguard_verification(verification_id)
        if not record:
            await ctx.send(view=design.error(
                t("staff.mod.altguard.refusal_notfound_title", locale=ctx.locale),
                t("staff.mod.altguard.refusal_notfound", locale=ctx.locale,
                  id=f"`{verification_id}`"),
            ))
            return

        guild = ctx.bot.get_guild(record["guild_id"])
        guild_label = f"{guild.name} (`{record['guild_id']}`)" if guild else f"`{record['guild_id']}`"

        reasons = record.get("reasons") or []
        reasons_value = ", ".join(f"`{reason}`" for reason in reasons) or "—"

        created_at = record.get("created_at")
        when = f"<t:{int(created_at.timestamp())}:F>" if created_at else "—"

        # The member's current gate state may differ from this verdict: a
        # moderator can have overridden it since.
        member_state = await ctx.bot.db.get_altguard_member(
            record["guild_id"], record["user_id"])
        current = member_state["status"] if member_state else "—"
        decided_by = member_state.get("source") if member_state else None

        fields = [
            {"name": t("staff.mod.altguard.field_member", locale=ctx.locale),
             "value": f"<@{record['user_id']}> (`{record['user_id']}`)"},
            {"name": t("staff.mod.altguard.field_guild", locale=ctx.locale),
             "value": guild_label},
            {"name": t("staff.mod.altguard.field_verdict", locale=ctx.locale),
             "value": f"`{record['verdict']}`"},
            {"name": t("staff.mod.altguard.field_score", locale=ctx.locale),
             "value": f"`{record['score']}`" if record.get("score") is not None else "—"},
            {"name": t("staff.mod.altguard.field_reasons", locale=ctx.locale),
             "value": reasons_value},
            {"name": t("staff.mod.altguard.field_enforced", locale=ctx.locale),
             "value": "`true`" if record.get("enforced") else "`false` (shadow)"},
            {"name": t("staff.mod.altguard.field_date", locale=ctx.locale),
             "value": when},
            {"name": t("staff.mod.altguard.field_current", locale=ctx.locale),
             "value": f"`{current}`" + (f" (`{decided_by}`)" if decided_by else "")},
        ]

        await ctx.send(view=design.panel(
            "info",
            t("staff.mod.altguard.refusal_title", locale=ctx.locale),
            t("staff.mod.altguard.refusal_description", locale=ctx.locale,
              id=f"`{verification_id}`"),
            fields=fields,
            footer=t("staff.mod.altguard.refusal_footer", locale=ctx.locale),
        ))
