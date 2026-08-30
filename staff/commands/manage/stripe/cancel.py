"""`/manage stripe cancel` — cancel a user's Stripe subscription."""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from staff.commands.manage.stripe._shared import GROUP, GROUP_DESCRIPTION, resolve_target, send_result_panel


@staff_command
class StripeCancelCommand(StaffCommand):
    command_type = CommandType.MANAGEMENT
    group = GROUP
    group_description = GROUP_DESCRIPTION
    name = "cancel"
    permission = "stripe_manage"
    description = "Cancel a user's Stripe subscription."
    options = [
        SlashOption("user", "user", "Target user.", required=True),
        SlashOption("immediate", "boolean",
                    "Cancel immediately instead of at the end of the billing period.",
                    required=False),
    ]

    def parse_message(self, raw: str) -> dict:
        parts = (raw or "").split()
        immediate = parts[1].lower() in ("immediate", "now", "true", "yes") if len(parts) > 1 else None
        return {"user_id": parts[0] if parts else None, "immediate": immediate}

    async def execute(self, ctx):
        uid = resolve_target(ctx)
        if not uid:
            await ctx.send(view=design.invalid_usage(
                ctx.locale, "m.stripe cancel <@user|user_id> [immediate]"))
            return

        await ctx.defer()
        result = await ctx.bot.stripe_admin.cancel_subscription(uid, immediate=bool(ctx.opt("immediate")))
        await send_result_panel(ctx, "cancel", uid, result)
