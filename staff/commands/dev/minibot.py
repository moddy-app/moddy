"""`/dev minibot` — act on one mini-bot from Moddy.

- ``restart <app>``: reconnect that app (new gateway session);
- ``reload <app|*> <guild_id>``: drop its cached modules for one server so the
  next event re-reads the configuration.

Both go through ``minibots:control`` and are idempotent. There is no way to
move a module from here on purpose: a transfer is the server admin's call
(``/config``, or ``t.config`` for the team).
"""

from minibots import status as mstatus
from minibots.spec import app_display_name, get_spec
from staff.framework import (
    StaffCommand, SlashOption, staff_command, design, CommandType, parse_guild_id,
)
from utils import emojis
from utils.i18n import t

ACTIONS = ("restart", "reload")


@staff_command
class MinibotCommand(StaffCommand):
    command_type = CommandType.DEV
    name = "minibot"
    description = "Restart a mini-bot, or reload its modules for one server."
    options = [
        SlashOption("action", "string", "restart or reload.", required=True,
                    choices=list(ACTIONS)),
        SlashOption("app", "string", "Mini-bot id (* = every mini-bot, reload only).",
                    required=True),
        SlashOption("guild_id", "string", "Server id (reload only).", required=False),
    ]

    def parse_message(self, raw: str) -> dict:
        parts = (raw or "").split()
        return {
            "action": parts[0].lower() if parts else None,
            "app": parts[1] if len(parts) > 1 else None,
            "guild_id": parts[2] if len(parts) > 2 else None,
        }

    async def execute(self, ctx):
        bot = ctx.bot
        locale = ctx.locale
        action = (ctx.opt("action") or "").lower()
        app = (ctx.opt("app") or "").strip()
        usage = "d.minibot <restart|reload> <app|*> [guild_id]"

        if action not in ACTIONS or not app:
            await ctx.send(view=design.invalid_usage(locale, usage))
            return
        if not (app == "*" and action == "reload") and get_spec(app) is None:
            await ctx.send(view=design.error(
                t("staff.dev.minibot.unknown.title", locale=locale),
                t("staff.dev.minibot.unknown.description", locale=locale, app=f"`{app}`")))
            return

        fields = {"app": app}
        if action == "reload":
            guild_id = parse_guild_id(ctx.opt("guild_id") or "")
            if not guild_id:
                await ctx.send(view=design.invalid_usage(locale, usage))
                return
            fields["guild_id"] = str(guild_id)
            kind = mstatus.CTRL_RELOAD_GUILD
        else:
            kind = mstatus.CTRL_RESTART

        redis = getattr(bot, "redis", None)
        listeners = 0
        if redis is not None:
            try:
                listeners = await redis.publish(mstatus.CONTROL_CHANNEL,
                                                mstatus.control_message(kind, **fields))
            except Exception:
                listeners = 0
        if not listeners:
            await ctx.send(view=design.error(
                t("staff.dev.minibot.unreachable.title", locale=locale),
                t("staff.dev.minibot.unreachable.description", locale=locale)))
            return

        name = app_display_name(app) if app != "*" else "*"
        await ctx.send(view=design.success(
            t("staff.dev.minibot.sent.title", locale=locale),
            t(f"staff.dev.minibot.sent.{action}", locale=locale, app=f"`{name}`",
              guild=f"`{fields.get('guild_id', '')}`"),
            emoji=emojis.SYNC))
