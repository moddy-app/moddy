"""`/dev minibots` — health of every mini-bot.

Reads what the mini-bots process publishes (``minibots:status``, every 30 s)
and the presence table (``app_guilds``). An app whose heartbeat is older than
90 s shows as *silent* — the process is down or stuck.
See docs/features/MINIBOTS.md.
"""

import time

from minibots import status as mstatus
from minibots.ownership import MODDY_APP
from minibots.spec import all_specs, app_display_name
from staff.framework import StaffCommand, staff_command, design, CommandType
from utils import emojis
from utils.i18n import t

_STATE_EMOJIS = {
    mstatus.STATE_READY: emojis.DONE,
    mstatus.STATE_CONNECTING: emojis.LOADING,
    mstatus.STATE_CRASHED: emojis.ERROR,
    mstatus.STATE_SILENT: emojis.WARNING,
    mstatus.STATE_STOPPED: emojis.UNDONE,
    mstatus.STATE_NO_TOKEN: emojis.UNDONE,
}


def app_field(locale: str, app: str, status, guild_count: int, now: float) -> dict:
    state = status.effective_state(now) if status else mstatus.STATE_SILENT
    lines = [
        f"{_STATE_EMOJIS.get(state, emojis.INFO)} **{t('staff.dev.minibots.state', locale=locale)}** "
        f"`{t(f'staff.dev.minibots.states.{state}', locale=locale)}`",
        f"**{t('staff.dev.minibots.guilds', locale=locale)}** `{guild_count}`",
    ]
    if status is not None:
        if status.latency_ms is not None:
            lines.append(f"**{t('staff.dev.minibots.latency', locale=locale)}** `{status.latency_ms}ms`")
        if status.started_at:
            lines.append(f"**{t('staff.dev.minibots.since', locale=locale)}** <t:{int(status.started_at)}:R>")
        if status.restarts:
            lines.append(f"**{t('staff.dev.minibots.restarts', locale=locale)}** `{status.restarts}`")
        if status.last_error and state != mstatus.STATE_READY:
            lines.append(f"**{t('staff.dev.minibots.last_error', locale=locale)}** `{status.last_error[:120]}`")
    return {"name": f"{app_display_name(app)} `{app}`", "value": "\n".join(lines)}


@staff_command
class MinibotsCommand(StaffCommand):
    command_type = CommandType.DEV
    name = "minibots"
    description = "Show the health of every mini-bot."
    defer = True

    async def execute(self, ctx):
        bot = ctx.bot
        locale = ctx.locale
        apps, process = await mstatus.read_statuses(getattr(bot, "redis", None))
        counts = {}
        if bot.db:
            try:
                counts = await bot.db.count_guilds_by_app()
            except Exception:
                counts = {}

        known = [spec.id for spec in all_specs()]
        ids = sorted(set(known) | set(apps))
        now = time.time()
        fields = [app_field(locale, app, apps.get(app), counts.get(app, 0), now) for app in ids]

        if process is not None and (now - (process.updated_at or 0)) <= mstatus.STALE_AFTER:
            proc = t("staff.dev.minibots.process", locale=locale,
                     ram=f"`{process.rss_mb if process.rss_mb is not None else '?'} MB`",
                     version=f"`{process.version or '?'}`",
                     since=f"<t:{int(process.started_at or now)}:R>")
        else:
            proc = t("staff.dev.minibots.process_down", locale=locale)

        description = (
            t("staff.dev.minibots.description", locale=locale,
              count=f"`{len(ids)}`", moddy=f"`{counts.get(MODDY_APP, 0)}`")
            + "\n" + proc
        )
        if not ids:
            description += "\n-# " + t("staff.dev.minibots.none", locale=locale)
        await ctx.send(view=design.panel(
            "developer", t("staff.dev.minibots.title", locale=locale), description,
            emoji=emojis.MODDY, fields=fields[:20],
        ))
