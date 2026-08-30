"""`/manage stripe refund` — refund a user's Stripe payment.

Without ``payment_intent_id`` the backend refunds the user's last paid
payment; without ``amount_cents`` it refunds the full amount. The backend
enforces its own refund ceiling (200 EUR at the time of writing) and returns
``ok: false`` with a readable error when it is exceeded.
"""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from staff.commands.manage.stripe._shared import GROUP, GROUP_DESCRIPTION, resolve_target, send_result_panel


@staff_command
class StripeRefundCommand(StaffCommand):
    command_type = CommandType.MANAGEMENT
    group = GROUP
    group_description = GROUP_DESCRIPTION
    name = "refund"
    permission = "stripe_manage"
    description = "Refund a user's Stripe payment (last paid payment / full amount by default)."
    options = [
        SlashOption("user", "user", "Target user.", required=True),
        SlashOption("payment_intent_id", "string",
                    "Payment to refund (defaults to the user's last paid payment).",
                    required=False),
        SlashOption("amount_cents", "integer",
                    "Amount to refund in cents (defaults to the full payment amount).",
                    required=False),
    ]

    def parse_message(self, raw: str) -> dict:
        parts = (raw or "").split()
        amount_cents = None
        if len(parts) > 2:
            try:
                amount_cents = int(parts[2])
            except ValueError:
                amount_cents = None
        return {
            "user_id": parts[0] if parts else None,
            "payment_intent_id": parts[1] if len(parts) > 1 else None,
            "amount_cents": amount_cents,
        }

    async def execute(self, ctx):
        uid = resolve_target(ctx)
        if not uid:
            await ctx.send(view=design.invalid_usage(
                ctx.locale, "m.stripe refund <@user|user_id> [payment_intent_id] [amount_cents]"))
            return

        await ctx.defer()
        result = await ctx.bot.stripe_admin.refund(
            uid,
            payment_intent_id=ctx.opt("payment_intent_id"),
            amount_cents=ctx.opt("amount_cents"),
        )
        await send_result_panel(ctx, "refund", uid, result)
