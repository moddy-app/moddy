"""`/dev jsk` — evaluate Python code in the bot's runtime.

Slash opens a Modal V2 with a paragraph code field (multiline-friendly). A
message command runs inline code directly, or surfaces a button that opens the
same modal when no code is supplied.
"""

import contextlib
import io
import textwrap
import traceback
from datetime import datetime, timezone

import discord
from discord import ui

from staff.framework import StaffCommand, staff_command, design, CommandType
from utils import emojis
from utils.i18n import t
from cogs.error_handler import BaseModal


async def _run_code(bot, code: str, *, author, channel, guild, message, locale: str):
    """Execute ``code`` and return a result panel."""
    code = code.strip()
    if code.startswith("```") and code.endswith("```"):
        code = code[3:-3]
        if code.startswith(("python", "py")):
            code = code.split("\n", 1)[1] if "\n" in code else ""

    env = {
        "bot": bot, "message": message, "channel": channel, "author": author,
        "guild": guild, "db": bot.db, "discord": discord,
        "datetime": datetime, "timezone": timezone,
        "asyncio": __import__("asyncio"),
    }

    to_compile = f"async def _func():\n{textwrap.indent(code, '  ')}"
    try:
        exec(to_compile, env)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            result = await env["_func"]()
        output = stdout.getvalue()
        if result is not None:
            output += f"\n{result!r}"
        if not output:
            output = t("staff.dev.jsk.no_output", locale=locale)
        if len(output) > 1800:
            output = output[:1800] + "\n… (truncated)"
        return design.success(
            t("staff.dev.jsk.done_title", locale=locale),
            f"```python\n{code[:400]}\n```",
            fields=[{"name": "Output", "value": f"```python\n{output}\n```"}],
        )
    except Exception:
        tb = traceback.format_exc()
        return design.error(
            t("staff.dev.jsk.fail_title", locale=locale),
            f"```python\n{code[:400]}\n```",
            fields=[{"name": "Error", "value": f"```python\n{tb[-1500:]}\n```"}],
        )


class JskModal(BaseModal):
    """Modal V2 prompt collecting Python code to evaluate."""

    def __init__(self, bot, *, ephemeral: bool, locale: str,
                 channel=None, guild=None, message=None):
        super().__init__(title=t("staff.dev.jsk.modal_title", locale=locale)[:45])
        self.bot = bot
        self.ephemeral = ephemeral
        self.locale = locale
        self._channel = channel
        self._guild = guild
        self._message = message

        self.code_input = ui.TextInput(style=discord.TextStyle.paragraph, max_length=4000)
        self.add_item(ui.Label(
            text=t("staff.dev.jsk.modal_label", locale=locale)[:45],
            description=t("staff.dev.jsk.modal_hint", locale=locale)[:100],
            component=self.code_input,
        ))

    async def on_submit(self, interaction: discord.Interaction):
        view = await _run_code(
            self.bot, self.code_input.value,
            author=interaction.user,
            channel=self._channel or interaction.channel,
            guild=self._guild or interaction.guild,
            message=self._message,
            locale=self.locale,
        )
        await interaction.response.send_message(view=view, ephemeral=self.ephemeral)


@staff_command
class JskCommand(StaffCommand):
    command_type = CommandType.DEV
    name = "jsk"
    description = "Evaluate Python code in the bot runtime."
    sensitive = True

    async def execute(self, ctx):
        # Message command with inline code -> run directly.
        if not ctx.is_slash and ctx.raw_args.strip():
            view = await _run_code(
                ctx.bot, ctx.raw_args,
                author=ctx.author, channel=ctx.channel, guild=ctx.guild,
                message=ctx.message, locale=ctx.locale,
            )
            await ctx.send(view=view)
            return

        # Otherwise open the modal (slash) or a button that opens it (message).
        def factory():
            return JskModal(
                ctx.bot, ephemeral=ctx.incognito, locale=ctx.locale,
                channel=ctx.channel, guild=ctx.guild, message=ctx.message,
            )

        await ctx.open_modal(
            factory,
            label=t("staff.dev.jsk.open_button", locale=ctx.locale),
            emoji=emojis.CODE,
            prompt_title=t("staff.dev.jsk.modal_title", locale=ctx.locale),
            prompt_description=t("staff.dev.jsk.modal_hint", locale=ctx.locale),
        )
