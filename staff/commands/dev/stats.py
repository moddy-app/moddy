"""`/dev stats` — runtime, Discord, database and system statistics."""

from datetime import datetime, timezone

from staff.framework import StaffCommand, staff_command, design, CommandType
from utils import emojis
from utils.i18n import t


@staff_command
class StatsCommand(StaffCommand):
    command_type = CommandType.DEV
    name = "stats"
    description = "Show bot, Discord, database and system statistics."

    async def execute(self, ctx):
        bot = ctx.bot
        locale = ctx.locale

        uptime = datetime.now(timezone.utc) - bot.launch_time
        days = uptime.days
        hours, remainder = divmod(uptime.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        fields = [{
            "name": f"{emojis.MODDY} {t('staff.dev.stats.bot', locale=locale)}",
            "value": (
                f"**{t('staff.dev.stats.uptime', locale=locale)}:** `{days}d {hours}h {minutes}m {seconds}s`\n"
                f"**{t('staff.dev.stats.latency', locale=locale)}:** `{round(bot.latency * 1000)}ms`"
            ),
        }, {
            "name": f"{emojis.WEB} {t('staff.dev.stats.discord', locale=locale)}",
            "value": (
                f"**{t('staff.dev.stats.guilds', locale=locale)}:** `{len(bot.guilds):,}`\n"
                f"**{t('staff.dev.stats.users', locale=locale)}:** `{len(bot.users):,}`\n"
                f"**{t('staff.dev.stats.commands', locale=locale)}:** `{len(bot.tree.get_commands())}`"
            ),
        }]

        if bot.db:
            try:
                stats = await bot.db.get_stats()
                fields.append({
                    "name": f"{emojis.HISTORY} {t('staff.dev.stats.database', locale=locale)}",
                    "value": (
                        f"**{t('staff.dev.stats.users', locale=locale)}:** `{stats.get('users', 0):,}`\n"
                        f"**{t('staff.dev.stats.guilds', locale=locale)}:** `{stats.get('guilds', 0):,}`\n"
                        f"**{t('staff.dev.stats.errors', locale=locale)}:** `{stats.get('errors', 0):,}`"
                    ),
                })
            except Exception:
                pass

        try:
            import psutil
            process = psutil.Process()
            mem = process.memory_info().rss / 1024 / 1024
            fields.append({
                "name": f"{emojis.CODE} {t('staff.dev.stats.system', locale=locale)}",
                "value": (
                    f"**RAM:** `{mem:.1f} MB`\n"
                    f"**CPU:** `{process.cpu_percent()}%`\n"
                    f"**Threads:** `{process.num_threads()}`"
                ),
            })
        except Exception:
            pass

        fields.append({
            "name": f"{emojis.COMMANDS} {t('staff.dev.stats.extensions', locale=locale)}",
            "value": f"**Loaded:** `{len(bot.extensions)}`\n**Cogs:** `{len(bot.cogs)}`",
        })

        await ctx.send(view=design.panel(
            "developer",
            t("staff.dev.stats.title", locale=locale),
            t("staff.dev.stats.description", locale=locale),
            fields=fields,
        ))
