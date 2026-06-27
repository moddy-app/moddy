"""`/dev emoji_preview` — preview an application emoji in various Discord contexts.

Slash-only. Shows the emoji in: inline text, heading, small caption,
a dropdown (select menu), and buttons in every available style.
"""

import re

import discord
from discord import ui

from cogs.error_handler import BaseView
from staff.framework import StaffCommand, SlashOption, staff_command, CommandType

_EMOJI_RE = re.compile(r"<a?:(\w+):(\d+)>")


def _parse_input(raw: str):
    raw = raw.strip()
    m = _EMOJI_RE.match(raw)
    if m:
        return m.group(1), int(m.group(2))
    if raw.isdigit():
        return None, int(raw)
    return raw, None


async def _resolve_emoji(bot, raw: str):
    """Return (partial_emoji, display_str) or (None, None) if not found."""
    name, emoji_id = _parse_input(raw)

    # Already a full syntax — construct directly without a network call.
    if name and emoji_id:
        pe = discord.PartialEmoji(name=name, id=emoji_id)
        return pe, f"<:{name}:{emoji_id}>"

    # Name or ID only — need to look up the application emoji list.
    for e in await bot.fetch_application_emojis():
        if emoji_id and e.id == emoji_id:
            pe = discord.PartialEmoji(name=e.name, id=e.id)
            return pe, f"<:{e.name}:{e.id}>"
        if name and e.name.lower() == name.lower():
            pe = discord.PartialEmoji(name=e.name, id=e.id)
            return pe, f"<:{e.name}:{e.id}>"

    return None, None


class EmojiPreviewView(BaseView):
    """Non-persistent preview view. Temporary by design."""

    def __init__(self, partial_emoji: discord.PartialEmoji, emoji_str: str):
        super().__init__()  # timeout=None

        container = ui.Container()

        # ── Header ──────────────────────────────────────────────────────────
        container.add_item(ui.TextDisplay(
            f"**Emoji preview** — `{emoji_str}`"
        ))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.large))

        # ── Text contexts ────────────────────────────────────────────────────
        container.add_item(ui.TextDisplay(
            f"**Text contexts**\n"
            f"Inline sentence: {emoji_str} This is the emoji in a line of text.\n"
            f"### {emoji_str} Heading (###)\n"
            f"**Bold prefix:** {emoji_str} Bold label followed by the emoji.\n"
            f"-# Small/caption: {emoji_str} This is a small greyed-out line."
        ))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.large))

        # ── Dropdown ─────────────────────────────────────────────────────────
        container.add_item(ui.TextDisplay("**Dropdown (select menu)**"))
        select_row = ui.ActionRow()
        select = ui.Select(
            placeholder=f"{partial_emoji} Select menu placeholder...",
            options=[
                discord.SelectOption(
                    label=f"Option {i}",
                    value=str(i),
                    description=f"This is option {i}",
                    emoji=partial_emoji,
                )
                for i in range(1, 4)
            ],
        )
        select.callback = self._noop
        select_row.add_item(select)
        container.add_item(select_row)
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.large))

        # ── Buttons ──────────────────────────────────────────────────────────
        container.add_item(ui.TextDisplay("**Buttons (all styles)**"))
        btn_row = ui.ActionRow()
        for label, style in (
            ("Primary", discord.ButtonStyle.primary),
            ("Secondary", discord.ButtonStyle.secondary),
            ("Success", discord.ButtonStyle.success),
            ("Danger", discord.ButtonStyle.danger),
        ):
            btn = ui.Button(label=label, style=style, emoji=partial_emoji)
            btn.callback = self._noop
            btn_row.add_item(btn)
        container.add_item(btn_row)

        # Link button (5th style)
        link_row = ui.ActionRow()
        link_btn = ui.Button(
            label="Link button",
            style=discord.ButtonStyle.link,
            emoji=partial_emoji,
            url="https://moddy.app",
        )
        link_row.add_item(link_btn)
        container.add_item(link_row)

        self.add_item(container)

    async def _noop(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "This is just an emoji preview.", ephemeral=True
        )


def _plain(text: str) -> BaseView:
    view = BaseView()
    c = ui.Container()
    c.add_item(ui.TextDisplay(text))
    view.add_item(c)
    return view


@staff_command
class EmojiPreviewCommand(StaffCommand):
    command_type = CommandType.DEV
    name = "emoji_preview"
    description = "Preview an emoji in every Discord context (text, heading, dropdown, buttons)."
    options = [
        SlashOption("emoji", "string",
                    "Name, ID, or full syntax (<:name:id>) of the emoji to preview.",
                    required=True),
    ]

    async def execute(self, ctx):
        if not ctx.is_slash:
            await ctx.send(view=_plain("This command is slash-only."))
            return

        raw: str = ctx.opt("emoji", "").strip()
        partial_emoji, emoji_str = await _resolve_emoji(ctx.bot, raw)

        if partial_emoji is None:
            await ctx.send(view=_plain(f"No application emoji found matching `{raw}`."))
            return

        await ctx.send(view=EmojiPreviewView(partial_emoji, emoji_str))
