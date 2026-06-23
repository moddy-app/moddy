"""`/manage subrefresh` — invalidate a user's subscription Redis cache.

Recategorized out of the dev prefix: this is a billing/Stripe operation gated by
the ``stripe_manage`` permission node.
"""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType, parse_user_id
from utils.i18n import t


@staff_command
class SubRefreshCommand(StaffCommand):
    command_type = CommandType.MANAGEMENT
    name = "subrefresh"
    aliases = ("sub-refresh", "sub_refresh")
    permission = "stripe_manage"
    description = "Invalidate a user's subscription cache (forces a fresh DB read)."
    options = [
        SlashOption("user", "user", "Target user.", required=False),
        SlashOption("user_id", "string", "Target user id.", required=False),
    ]

    def parse_message(self, raw: str) -> dict:
        return {"user_id": (raw or "").strip()}

    async def execute(self, ctx):
        locale = ctx.locale
        target = ctx.opt("user")
        uid = target.id if target else parse_user_id(ctx.opt("user_id") or "")
        if not uid:
            await ctx.send(view=design.invalid_usage(locale, "m.subrefresh <@user|user_id>"))
            return

        from utils.subscription import invalidate_cache
        await invalidate_cache(ctx.bot, uid)
        await ctx.send(view=design.success(
            t("staff.manage.subrefresh.title", locale=locale),
            t("staff.manage.subrefresh.done", locale=locale, id=f"`{uid}`"),
        ))
