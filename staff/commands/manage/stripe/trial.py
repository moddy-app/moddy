"""`/manage stripe trial` — start a Stripe trial subscription for a user."""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from staff.commands.manage.stripe._shared import GROUP, GROUP_DESCRIPTION, resolve_target, send_result_panel
from staff.commands.manage.stripe_create import EMAIL_RE

#: Stripe's own ceiling on ``trial_period_days``; anything above is refused by
#: the API. https://docs.stripe.com/api/subscriptions/create
MAX_TRIAL_DAYS = 730


@staff_command
class StripeTrialCommand(StaffCommand):
    command_type = CommandType.MANAGEMENT
    group = GROUP
    group_description = GROUP_DESCRIPTION
    name = "trial"
    permission = "stripe_manage"
    description = "Start a Stripe trial subscription for a user."
    options = [
        SlashOption("user", "user", "Target user.", required=True),
        SlashOption("email", "string", "Email to attach to the subscription.", required=True),
        SlashOption("plan", "string", "Billing plan.", required=False,
                    default="monthly", choices=["monthly", "yearly"]),
        SlashOption("trial_days", "integer",
                    "Trial length in days (defaults to 7, up to Stripe's 730-day maximum).",
                    required=False),
    ]

    def parse_message(self, raw: str) -> dict:
        parts = (raw or "").split()
        trial_days = None
        if len(parts) > 3:
            try:
                trial_days = int(parts[3])
            except ValueError:
                trial_days = None
        return {
            "user_id": parts[0] if parts else None,
            "email": parts[1] if len(parts) > 1 else None,
            "plan": parts[2] if len(parts) > 2 else None,
            "trial_days": trial_days,
        }

    async def execute(self, ctx):
        uid = resolve_target(ctx)
        email = (ctx.opt("email") or "").strip()
        if not uid or not email or not EMAIL_RE.match(email):
            await ctx.send(view=design.invalid_usage(
                ctx.locale, "m.stripe trial <@user|user_id> <email> [plan] [trial_days]"))
            return

        plan = ctx.opt("plan") or "monthly"
        trial_days = ctx.opt("trial_days")
        if trial_days is not None:
            # No Moddy-side cap any more: a staff member may grant a trial of
            # any length. The only bounds left are Stripe's own — a trial is at
            # least a day, and `trial_period_days` is rejected above 730 — so
            # clamping here turns an opaque API error into the value asked for.
            trial_days = max(1, min(MAX_TRIAL_DAYS, trial_days))

        await ctx.defer()
        result = await ctx.bot.stripe_admin.start_trial(
            uid, email=email, plan=plan, trial_days=trial_days or 7,
        )
        await send_result_panel(ctx, "trial", uid, result)
