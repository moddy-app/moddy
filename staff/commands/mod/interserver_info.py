"""`/mod interserver_info` — details about an inter-server relayed message."""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from utils import emojis
from utils.i18n import t


@staff_command
class InterserverInfoCommand(StaffCommand):
    command_type = CommandType.MODERATOR
    name = "interserver_info"
    permission = "interserver_info"
    description = "Show details about an inter-server message."
    options = [
        SlashOption("moddy_id", "string", "The Moddy message id.", required=True),
    ]

    async def execute(self, ctx):
        bot = ctx.bot
        locale = ctx.locale
        moddy_id = (ctx.opt("moddy_id") or "").strip().upper()
        if not moddy_id:
            await ctx.send(view=design.invalid_usage(locale, "mod.interserver_info <moddy_id>"))
            return

        data = await bot.db.get_interserver_message(moddy_id)
        if not data:
            await ctx.send(view=design.error(
                t("staff.mod.interserver.notfound_title", locale=locale),
                t("staff.mod.interserver.notfound", locale=locale, id=f"`{moddy_id}`"),
            ))
            return

        ts = data.get("timestamp", data.get("created_at"))
        ts_str = f"<t:{int(ts.timestamp())}:F>" if ts else "—"
        content = data.get("content") or ""
        content = (content[:500] + "…") if len(content) > 500 else (content or f"-# {t('staff.mod.interserver.no_content', locale=locale)}")
        relayed = len(data.get("relayed_messages", []))

        fields = [
            {"name": t("staff.mod.interserver.author", locale=locale), "value": f"<@{data['author_id']}> (`{data['author_id']}`)"},
            {"name": t("staff.mod.interserver.origin", locale=locale),
             "value": f"**Guild:** `{data['original_guild_id']}`\n**Channel:** `{data['original_channel_id']}`\n**Message:** `{data['original_message_id']}`"},
            {"name": t("staff.mod.interserver.content", locale=locale), "value": content},
            {"name": t("staff.mod.interserver.meta", locale=locale),
             "value": (
                 f"**{t('staff.mod.interserver.timestamp', locale=locale)}:** {ts_str}\n"
                 f"**{t('staff.mod.interserver.status', locale=locale)}:** `{data['status']}`\n"
                 f"**{t('staff.mod.interserver.team_msg', locale=locale)}:** {emojis.DONE if data.get('is_moddy_team') else emojis.UNDONE}\n"
                 f"**{t('staff.mod.interserver.relayed', locale=locale)}:** `{relayed}`"
             )},
        ]

        await ctx.send(view=design.panel(
            "info",
            t("staff.mod.interserver.info_title", locale=locale, id=moddy_id),
            "",
            fields=fields,
            emoji=emojis.MESSAGE,
            accent="error",
        ))
