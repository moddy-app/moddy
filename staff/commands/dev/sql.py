"""`/dev sql` — execute a SQL query against the database.

Dangerous statements (DROP/DELETE/TRUNCATE/ALTER/UPDATE) require an explicit
confirmation via buttons before running.
"""

import discord
from discord import ui

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from utils import emojis
from utils.i18n import t
from cogs.error_handler import BaseView

DANGEROUS = ("DROP", "DELETE", "TRUNCATE", "ALTER", "UPDATE")


async def _execute_query(bot, query: str, locale: str) -> BaseView:
    """Run the query and return a result panel."""
    try:
        async with bot.db.pool.acquire() as conn:
            if query.upper().lstrip().startswith("SELECT"):
                rows = await conn.fetch(query)
                if not rows:
                    return design.success(t("staff.dev.sql.done_title", locale=locale),
                                          t("staff.dev.sql.no_rows", locale=locale))
                lines = [" | ".join(str(v) for v in row.values()) for row in rows[:10]]
                result = "```\n" + "\n".join(lines) + "\n```"
                if len(rows) > 10:
                    result += f"\n-# +{len(rows) - 10}"
                return design.success(
                    t("staff.dev.sql.done_title", locale=locale),
                    t("staff.dev.sql.rows", locale=locale, count=len(rows)) + f"\n{result}",
                )
            result = await conn.execute(query)
            return design.success(
                t("staff.dev.sql.done_title", locale=locale),
                f"```sql\n{query[:400]}\n```\n**{t('staff.dev.sql.result', locale=locale)}:** `{result}`",
            )
    except Exception as exc:
        return design.error(
            t("staff.dev.sql.fail_title", locale=locale),
            f"```sql\n{query[:400]}\n```",
            fields=[{"name": "Error", "value": f"```{str(exc)[:500]}```"}],
        )


class SqlConfirmView(BaseView):
    """Confirm/cancel a dangerous query. Author-checked."""

    def __init__(self, bot, author_id: int, query: str, locale: str):
        super().__init__(timeout=60)
        self.bot = bot
        self.author_id = author_id
        self.query = query
        self.locale = locale

        container = design.make_container("warning")
        container.add_item(ui.TextDisplay(design.title_line(
            emojis.WARNING,
            t("staff.dev.sql.danger_title", locale=locale),
        )))
        container.add_item(ui.TextDisplay(
            t("staff.dev.sql.danger_description", locale=locale) + f"\n```sql\n{query[:400]}\n```"
        ))
        self.add_item(container)

        row = ui.ActionRow()
        confirm = ui.Button(label=t("staff.common.confirm", locale=locale), style=discord.ButtonStyle.danger)
        confirm.callback = self._confirm
        row.add_item(confirm)
        cancel = ui.Button(label=t("staff.common.cancel", locale=locale), style=discord.ButtonStyle.secondary)
        cancel.callback = self._cancel
        row.add_item(cancel)
        self.add_item(row)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                t("staff.common.not_your_menu", locale=self.locale), ephemeral=True
            )
            return False
        return True

    async def _confirm(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        await interaction.response.defer()
        view = await _execute_query(self.bot, self.query, self.locale)
        await interaction.edit_original_response(view=view)

    async def _cancel(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        await interaction.response.edit_message(view=design.error(
            t("staff.common.cancelled", locale=self.locale),
            t("staff.dev.sql.cancelled", locale=self.locale),
        ))


@staff_command
class SqlCommand(StaffCommand):
    command_type = CommandType.DEV
    name = "sql"
    description = "Execute a SQL query against the database."
    sensitive = True
    options = [
        SlashOption("query", "string", "The SQL query to execute.", required=True),
    ]

    async def execute(self, ctx):
        query = (ctx.opt("query") or "").strip()
        if not query:
            await ctx.send(view=design.invalid_usage(ctx.locale, "d.sql <query>"))
            return
        if not ctx.bot.db:
            await ctx.send(view=design.error(
                t("staff.common.error.title", locale=ctx.locale),
                t("staff.dev.db_unavailable", locale=ctx.locale),
            ))
            return

        if any(keyword in query.upper() for keyword in DANGEROUS):
            await ctx.send(view=SqlConfirmView(ctx.bot, ctx.author.id, query, ctx.locale))
            return

        await ctx.send(view=await _execute_query(ctx.bot, query, ctx.locale))
