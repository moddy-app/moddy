"""`/dev serverlist` — paginated list of every server Moddy is in."""

import discord
from discord import ui

from staff.framework import StaffCommand, staff_command, design, CommandType
from staff.framework.design import make_container, title_line
from utils import emojis
from utils.i18n import t
from cogs.error_handler import BaseView


class ServerListView(BaseView):
    """Paginated server list. Author-checked; short-lived (timeout)."""

    def __init__(self, bot, author_id: int, guilds: list, locale: str, per_page: int = 10):
        super().__init__(timeout=300)
        self.bot = bot
        self.author_id = author_id
        self.guilds = guilds
        self.locale = locale
        self.per_page = per_page
        self.page = 0
        self.total_pages = max(1, (len(guilds) - 1) // per_page + 1)
        self._build()

    def _build(self):
        self.clear_items()
        start = self.page * self.per_page
        page_guilds = self.guilds[start:start + self.per_page]

        container = make_container("developer")
        container.add_item(ui.TextDisplay(
            f"{title_line(emojis.WEB, t('staff.dev.serverlist.title', locale=self.locale))}\n"
            f"**{t('staff.dev.serverlist.total', locale=self.locale, count=len(self.guilds))}**\n"
            f"-# {t('staff.dev.serverlist.page', locale=self.locale, current=self.page + 1, total=self.total_pages)}"
        ))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        if not page_guilds:
            container.add_item(ui.TextDisplay(f"-# {t('staff.dev.serverlist.empty', locale=self.locale)}"))
        for guild in page_guilds:
            joined = guild.me.joined_at if guild.me else None
            joined_str = f"<t:{int(joined.timestamp())}:d>" if joined else "—"
            container.add_item(ui.TextDisplay(
                f"**{guild.name}**\n"
                f"-# `{guild.id}` • {t('staff.dev.serverlist.members', locale=self.locale)}: `{guild.member_count:,}` • {t('staff.dev.serverlist.joined', locale=self.locale)}: {joined_str}"
            ))
        self.add_item(container)

        if self.total_pages > 1:
            row = ui.ActionRow()
            prev_btn = ui.Button(style=discord.ButtonStyle.secondary, disabled=self.page == 0,
                                 emoji=discord.PartialEmoji.from_str(emojis.BACK))
            prev_btn.callback = self._prev
            row.add_item(prev_btn)
            next_btn = ui.Button(style=discord.ButtonStyle.secondary, disabled=self.page >= self.total_pages - 1,
                                 emoji=discord.PartialEmoji.from_str(emojis.NEXT))
            next_btn.callback = self._next
            row.add_item(next_btn)
            self.add_item(row)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                t("staff.common.not_your_menu", locale=self.locale), ephemeral=True
            )
            return False
        return True

    async def _prev(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        self.page = max(0, self.page - 1)
        self._build()
        await interaction.response.edit_message(view=self)

    async def _next(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        self.page = min(self.total_pages - 1, self.page + 1)
        self._build()
        await interaction.response.edit_message(view=self)


@staff_command
class ServerListCommand(StaffCommand):
    command_type = CommandType.DEV
    name = "servers"
    aliases = ("serverlist",)
    description = "Paginated list of every server Moddy is in."

    async def execute(self, ctx):
        guilds = sorted(ctx.bot.guilds, key=lambda g: g.member_count or 0, reverse=True)
        view = ServerListView(ctx.bot, ctx.author.id, guilds, ctx.locale)
        await ctx.send(view=view)
