"""`/manage stripe resume` — resume a user's cancelled Stripe subscription."""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from staff.commands.manage.stripe._shared import GROUP, GROUP_DESCRIPTION, resolve_target, send_result_panel


@staff_command
class StripeResumeCommand(StaffCommand):
    command_type = CommandType.MANAGEMENT
    group = GROUP
    group_description = GROUP_DESCRIPTION
    name = "resume"
    permission = "stripe_manage"
    description = "Resume a user's cancelled Stripe subscription."
    options = [
        SlashOption("user", "user", "Target user.", required=True),
    ]

    def parse_message(self, raw: str) -> dict:
        return {"user_id": (raw or "").strip()}

    async def execute(self, ctx):
        uid = resolve_target(ctx)
        if not uid:
            await ctx.send(view=design.invalid_usage(ctx.locale, "m.stripe resume <@user|user_id>"))
            return

        await ctx.defer()
        result = await ctx.bot.stripe_admin.resume_subscription(uid)
        await send_result_panel(ctx, "resume", uid, result)
