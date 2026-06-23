"""`/team subscription` — view a user's subscription details.

Note: this is read-only. When the /support group is built it can be gated with
the ``subscription_view`` permission node; left open to all staff for now to
preserve current behaviour.
"""

import logging
from datetime import timezone

import discord

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType, parse_user_id
from staff.framework import badges
from utils import emojis
from utils.i18n import t

logger = logging.getLogger("moddy.staff.team.subscription")


@staff_command
class SubscriptionCommand(StaffCommand):
    command_type = CommandType.TEAM
    name = "subscription"
    description = "View a user's subscription details."
    options = [
        SlashOption("user", "user", "Target user.", required=True),
    ]

    def parse_message(self, raw: str) -> dict:
        return {"user_id": (raw or "").strip()}

    async def execute(self, ctx):
        bot = ctx.bot
        locale = ctx.locale

        target = ctx.opt("user")
        user_id = target.id if target else parse_user_id(ctx.opt("user_id") or "")
        if not user_id:
            await ctx.send(view=design.invalid_usage(locale, "t.subscription <user_id>"))
            return

        try:
            user = await bot.fetch_user(user_id)
        except discord.NotFound:
            await ctx.send(view=design.error(
                t("staff.team.user_notfound_title", locale=locale),
                t("staff.team.user_notfound", locale=locale, id=f"`{user_id}`"),
            ))
            return

        from utils.subscription import get_subscription
        try:
            sub = await get_subscription(bot, user_id)
            servers = await bot.db.get_subscription_servers(user_id) if bot.db else []
        except Exception as exc:
            logger.error("subscription fetch error for %s: %s", user_id, exc, exc_info=True)
            await ctx.send(view=design.error(
                t("staff.common.error.title", locale=locale),
                t("staff.team.subscription.fetch_fail", locale=locale),
            ))
            return

        rendered, _, _ = await badges.render_user(bot, user)
        is_active = bool(sub and sub.get("is_active"))
        tier = (sub.get("tier") or "Moddy Max") if sub else "—"
        dot = emojis.GREEN_STATUS if is_active else emojis.RED_STATUS
        status = f"{dot} {t('staff.team.subscription.active' if is_active else 'staff.team.subscription.inactive', locale=locale)}"
        if is_active:
            status += f" — `{tier}`"

        fields = [{
            "name": f"{emojis.PREMIUM} {t('staff.team.subscription.status', locale=locale)}",
            "value": status,
        }]

        expires = sub.get("expires_at") if sub else None
        if expires:
            ts = int(expires.astimezone(timezone.utc).timestamp())
            fields.append({"name": t("staff.team.subscription.expires", locale=locale),
                           "value": f"<t:{ts}:F> (<t:{ts}:R>)"})

        stripe_id = sub.get("stripe_customer_id") if sub else None
        fields.append({"name": "Stripe", "value": f"`{stripe_id}`" if stripe_id else "`—`"})

        lines = [f"`{len(servers)}/5` " + t("staff.team.subscription.linked", locale=locale)]
        for s in servers:
            added = s.get("added_at")
            ts = int(added.astimezone(timezone.utc).timestamp()) if added else 0
            lines.append(f"• `{s['server_id']}` — <t:{ts}:D>")
        fields.append({"name": f"{emojis.WEB} {t('staff.team.subscription.servers', locale=locale)}",
                       "value": "\n".join(lines)})

        await ctx.send(view=design.panel(
            "info",
            t("staff.team.subscription.title", locale=locale),
            rendered,
            fields=fields,
            emoji=emojis.PREMIUM,
            accent="primary",
        ))
