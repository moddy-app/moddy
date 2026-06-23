"""`/dev error` — look up a logged error by its code (cache + database)."""

from datetime import datetime

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from utils import emojis
from utils.i18n import t


@staff_command
class ErrorCommand(StaffCommand):
    command_type = CommandType.DEV
    name = "error"
    description = "Show detailed information about a logged error code."
    options = [
        SlashOption("code", "string", "The error code (e.g. A1B2C3D4).", required=True),
    ]

    async def execute(self, ctx):
        bot = ctx.bot
        locale = ctx.locale
        code = (ctx.opt("code") or "").strip().upper()
        if not code:
            await ctx.send(view=design.invalid_usage(locale, "d.error <code>"))
            return

        cached = None
        tracker = bot.get_cog("ErrorTracker")
        if tracker:
            for entry in tracker.error_cache:
                if entry["code"] == code:
                    cached = entry
                    break

        db_error = None
        if bot.db:
            try:
                db_error = await bot.db.get_error(code)
            except Exception:
                db_error = None

        if not cached and not db_error:
            await ctx.send(view=design.error(
                t("staff.dev.error.notfound_title", locale=locale),
                t("staff.dev.error.notfound", locale=locale, code=f"`{code}`"),
            ))
            return

        data = db_error if db_error else cached["data"]
        timestamp = db_error.get("timestamp") if db_error else cached.get("timestamp")

        details = [
            f"**Code:** `{code}`",
            f"**Type:** `{data.get('error_type') or data.get('type')}`",
            f"**File:** `{data.get('file_source') or data.get('file')}:{data.get('line_number') or data.get('line')}`",
        ]
        if data.get("sentry_issue_id"):
            sid = data["sentry_issue_id"]
            details.append(f"**Sentry:** [#{sid}](https://moddy-0f.sentry.io/issues/{sid}/)")

        fields = [{"name": f"{emojis.INFO} {t('staff.dev.error.details', locale=locale)}", "value": "\n".join(details)}]

        message = data.get("message", "—")
        fields.append({"name": t("staff.dev.error.message", locale=locale), "value": f"```{message[:1000]}```"})

        context_parts = []
        if data.get("command"):
            context_parts.append(f"**Command:** `{data['command']}`")
        if data.get("user_id"):
            context_parts.append(f"**User:** `{data['user_id']}`")
        if data.get("guild_id"):
            context_parts.append(f"**Guild:** `{data['guild_id']}`")
        fields.append({
            "name": f"{emojis.INFO} {t('staff.dev.error.context', locale=locale)}",
            "value": "\n".join(context_parts) if context_parts else f"-# {t('staff.dev.error.no_context', locale=locale)}",
        })

        tb = data.get("traceback", "—")
        fields.append({"name": "Traceback", "value": f"```python\n{tb[-1000:]}\n```"})

        if timestamp:
            try:
                ts = int(timestamp.timestamp()) if hasattr(timestamp, "timestamp") else int(datetime.fromisoformat(str(timestamp)).timestamp())
                fields.append({"name": f"{emojis.TIME} {t('staff.dev.error.occurred', locale=locale)}", "value": f"<t:{ts}:R>"})
            except Exception:
                pass

        source = "Database" if db_error else "Cache"
        await ctx.send(view=design.panel(
            "error",
            t("staff.dev.error.title", locale=locale, code=code),
            t("staff.dev.error.subtitle", locale=locale, source=source),
            fields=fields,
            emoji=emojis.BUG,
        ))
