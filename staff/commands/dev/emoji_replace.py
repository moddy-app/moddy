"""`/dev emoji_replace` — replace an application emoji with a new image.

Slash-only (no message transport). Logs `<old> -> <new>` to the emoji
tracking channel so the code can be updated in bulk afterwards.
"""

import re

import discord
from discord import ui

from cogs.error_handler import BaseView
from staff.framework import StaffCommand, SlashOption, staff_command, CommandType

_LOG_CHANNEL_ID = 1519754560208375838
_EMOJI_RE = re.compile(r"<a?:(\w+):(\d+)>")


def _parse_input(raw: str):
    """Return (name_or_None, id_or_None) from: name / id / <:name:id>."""
    raw = raw.strip()
    m = _EMOJI_RE.match(raw)
    if m:
        return m.group(1), int(m.group(2))
    if raw.isdigit():
        return None, int(raw)
    return raw, None


async def _find_app_emoji(bot, name, emoji_id):
    for e in await bot.fetch_application_emojis():
        if emoji_id and e.id == emoji_id:
            return e
        if name and e.name.lower() == name.lower():
            return e
    return None


def _plain(text: str) -> BaseView:
    view = BaseView()
    c = ui.Container()
    c.add_item(ui.TextDisplay(text))
    view.add_item(c)
    return view


@staff_command
class EmojiReplaceCommand(StaffCommand):
    command_type = CommandType.DEV
    name = "emoji_replace"
    description = "Replace an application emoji with a new image. Logs old->new to the tracking channel."
    options = [
        SlashOption("emoji", "string",
                    "Name, ID, or full syntax (<:name:id>) of the emoji to replace.",
                    required=True),
        SlashOption("image", "attachment",
                    "New image for the emoji (PNG/GIF recommended).",
                    required=True),
    ]

    async def execute(self, ctx):
        if not ctx.is_slash:
            await ctx.send(view=_plain("This command is slash-only."))
            return

        raw: str = ctx.opt("emoji", "").strip()
        attachment: discord.Attachment = ctx.opt("image")

        name, emoji_id = _parse_input(raw)
        old = await _find_app_emoji(ctx.bot, name, emoji_id)
        if old is None:
            await ctx.send(view=_plain(f"No application emoji found matching `{raw}`."))
            return

        old_syntax = f"<:{old.name}:{old.id}>"
        old_name = old.name

        try:
            image_bytes = await attachment.read()
        except Exception as exc:
            await ctx.send(view=_plain(f"Failed to read image: `{exc}`"))
            return

        try:
            await old.delete()
        except Exception as exc:
            await ctx.send(view=_plain(f"Failed to delete `{old_syntax}`: `{exc}`"))
            return

        try:
            new = await ctx.bot.create_application_emoji(name=old_name, image=image_bytes)
        except Exception as exc:
            await ctx.send(view=_plain(
                f"Old emoji deleted but failed to create new one: `{exc}`\n"
                f"-# Old syntax was: `{old_syntax}`"
            ))
            return

        new_syntax = f"<:{new.name}:{new.id}>"
        log_line = f"{old_syntax} -> {new_syntax}"

        ch = ctx.bot.get_channel(_LOG_CHANNEL_ID)
        if ch:
            try:
                await ch.send(log_line)
            except Exception:
                pass

        await ctx.send(view=_plain(f"Done. Logged:\n`{log_line}`"))
