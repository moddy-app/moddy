"""`/manage stripecreate` — ask the backend to create a Stripe customer for a user.

Fire-and-forget: publishes a `create_stripe_customer` event on `moddy:dashboard`
(see docs/REDIS_COMMUNICATION.md). The backend listener
(`app/redis/stripe_events.py`) creates the local user row if missing, creates
the Stripe customer, and stores `stripe_customer_id`/`email` — idempotent if
one already exists. No acknowledgement is expected here; if the backend is
down when this publishes, the customer is created automatically the next time
the user goes through the dashboard checkout/portal.
"""

import json
import re

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType, parse_user_id
from utils.i18n import t

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@staff_command
class StripeCreateCommand(StaffCommand):
    command_type = CommandType.MANAGEMENT
    name = "stripe_create"
    aliases = ("stripecreate", "stripe-create", "stripe_create")
    permission = "stripe_manage"
    description = "Create a Stripe customer for a user (fire-and-forget request to the backend)."
    options = [
        SlashOption("user", "user", "Target user.", required=True),
        SlashOption("email", "string", "Email to attach to the Stripe customer.", required=True),
    ]

    def parse_message(self, raw: str) -> dict:
        parts = (raw or "").split()
        return {
            "user_id": parts[0] if parts else None,
            "email": parts[1] if len(parts) > 1 else None,
        }

    async def execute(self, ctx):
        locale = ctx.locale
        target = ctx.opt("user")
        uid = target.id if target else parse_user_id(ctx.opt("user_id") or "")
        email = (ctx.opt("email") or "").strip()

        if not uid or not email or not EMAIL_RE.match(email):
            await ctx.send(view=design.invalid_usage(locale, "m.stripecreate <@user|user_id> <email>"))
            return

        if not getattr(ctx.bot, "redis", None):
            await ctx.send(view=design.error(
                t("staff.manage.stripe_create.title", locale=locale),
                t("staff.manage.stripe_create.no_redis", locale=locale),
            ))
            return

        try:
            await ctx.bot.redis.publish("moddy:dashboard", json.dumps({
                "type": "create_stripe_customer",
                "discord_id": str(uid),
                "email": email,
            }))
        except Exception as exc:
            await ctx.send(view=design.error(
                t("staff.manage.stripe_create.title", locale=locale),
                t("staff.manage.stripe_create.publish_failed", locale=locale, error=f"`{exc}`"),
            ))
            return

        await ctx.send(view=design.success(
            t("staff.manage.stripe_create.title", locale=locale),
            t("staff.manage.stripe_create.done", locale=locale, id=f"`{uid}`", email=f"`{email}`"),
            footer=t("staff.manage.stripe_create.footer", locale=locale),
        ))
