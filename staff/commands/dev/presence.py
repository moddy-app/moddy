"""`/dev presence` — change the bot's status and activity."""

import discord

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from utils import emojis
from utils.i18n import t

STATUS_MAP = {
    "online": discord.Status.online,
    "idle": discord.Status.idle,
    "dnd": discord.Status.dnd,
    "invisible": discord.Status.invisible,
}

STATUS_LABELS = {
    "online": f"{emojis.GREEN_STATUS} Online",
    "idle": f"{emojis.YELLOW_STATUS} Idle",
    "dnd": f"{emojis.RED_STATUS} Do Not Disturb",
    "invisible": "Invisible",
}


@staff_command
class PresenceCommand(StaffCommand):
    command_type = CommandType.DEV
    name = "presence"
    description = "Change the bot's presence (status + activity)."
    options = [
        SlashOption("status", "string", "Presence status.", required=True,
                    choices=["online", "idle", "dnd", "invisible"]),
        SlashOption("activity", "string", "Optional activity text.", required=False),
    ]

    def parse_message(self, raw: str) -> dict:
        raw = (raw or "").strip()
        parts = raw.split(None, 1)
        status = parts[0].lower() if parts else None
        activity = parts[1].strip() if len(parts) > 1 else None
        return {"status": status, "activity": activity}

    async def execute(self, ctx):
        status_key = (ctx.opt("status") or "").lower()
        activity_text = ctx.opt("activity")

        if status_key not in STATUS_MAP:
            options = " | ".join(f"`{k}`" for k in STATUS_MAP)
            await ctx.send(view=design.error(
                t("staff.common.invalid_usage.title", locale=ctx.locale),
                t("staff.dev.presence.usage", locale=ctx.locale, options=options),
            ))
            return

        activity = discord.CustomActivity(name=activity_text) if activity_text else None
        await ctx.bot.change_presence(status=STATUS_MAP[status_key], activity=activity)

        description = t("staff.dev.presence.updated", locale=ctx.locale, status=STATUS_LABELS[status_key])
        if activity_text:
            description += f"\n**{t('staff.dev.presence.activity', locale=ctx.locale)}:** `{activity_text}`"

        await ctx.send(view=design.success(
            t("staff.dev.presence.title", locale=ctx.locale), description,
        ))
