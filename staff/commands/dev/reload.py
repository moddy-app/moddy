"""`/dev reload` — reload one extension, or all of them."""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from utils import emojis
from utils.i18n import t


@staff_command
class ReloadCommand(StaffCommand):
    command_type = CommandType.DEV
    name = "reload"
    description = "Reload a bot extension (or 'all')."
    options = [
        SlashOption("extension", "string", "Extension to reload, or 'all'.", required=False, default="all"),
    ]

    async def execute(self, ctx):
        ext = (ctx.opt("extension") or "all").strip()
        locale = ctx.locale

        if not ext or ext == "all":
            msg = await ctx.send(view=design.loading(
                t("staff.dev.reload.loading_title", locale=locale),
                t("staff.dev.reload.loading_all", locale=locale),
            ))
            success, failed = [], []
            for name in list(ctx.bot.extensions.keys()):
                try:
                    await ctx.bot.reload_extension(name)
                    success.append(name)
                except Exception as exc:
                    failed.append(f"{name}: {exc}")

            fields = []
            if success:
                preview = "\n".join(f"`{e}`" for e in success[:12])
                if len(success) > 12:
                    preview += f"\n-# +{len(success) - 12}"
                fields.append({"name": f"{emojis.DONE} {t('staff.dev.reload.reloaded', locale=locale, count=len(success))}", "value": preview})
            if failed:
                preview = "\n".join(f"`{f[:120]}`" for f in failed[:6])
                fields.append({"name": f"{emojis.UNDONE} {t('staff.dev.reload.failed', locale=locale, count=len(failed))}", "value": preview})

            kind = "warning" if failed else "success"
            view = design.panel(
                kind,
                t("staff.dev.reload.done_title", locale=locale),
                t("staff.dev.reload.done_errors" if failed else "staff.dev.reload.done_ok", locale=locale),
                fields=fields,
            )
            if msg:
                await msg.edit(view=view)
            else:
                await ctx.send(view=view)
            return

        # Single extension — resolve a short name to a full module path.
        if not ext.startswith(("cogs.", "staff.")):
            for full in ctx.bot.extensions:
                if full.endswith(ext):
                    ext = full
                    break

        try:
            await ctx.bot.reload_extension(ext)
            await ctx.send(view=design.success(
                t("staff.dev.reload.single_ok_title", locale=locale),
                t("staff.dev.reload.single_ok", locale=locale, ext=f"`{ext}`"),
            ))
        except Exception as exc:
            await ctx.send(view=design.error(
                t("staff.dev.reload.single_fail_title", locale=locale),
                t("staff.dev.reload.single_fail", locale=locale, ext=f"`{ext}`"),
                fields=[{"name": "Error", "value": f"```{str(exc)[:500]}```"}],
            ))
