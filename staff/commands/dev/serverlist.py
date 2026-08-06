"""`/dev serverlist` — paginated list of every server Moddy is in."""

import re

import discord
from discord import ui

from staff.framework import StaffCommand, staff_command, design, CommandType
from staff.framework.design import make_container, title_line
from utils import emojis
from utils.i18n import i18n, t
from utils.components_v2 import create_error_message
from cogs.error_handler import BaseView

_PER_PAGE = 10
# \d{1,20} rather than a tighter \d{17,20} snowflake range: a bare shell
# built with owner_id=0 must still match its own template (see
# docs/PERSISTENT_VIEWS.md Migration log, Step 6).
_CID_NAV_TEMPLATE = r"moddy:devservers:nav:(?P<action>prev|next):(?P<owner>\d{1,20}):(?P<page>\d{1,6})"


def _guarded(callback):
    """Route DynamicItem callback errors to the central error handler.

    A DynamicItem dispatched via ``bot.add_dynamic_items`` has no live
    ``BaseView``, so ``BaseView.on_error`` never fires. Copied from
    ``utils/appeal_views.py``.
    """
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as e:  # noqa: BLE001 — funnel everything to the handler
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, e, self.__class__.__name__)
    return wrapper


def _sorted_guilds(bot):
    return sorted(bot.guilds, key=lambda g: g.member_count or 0, reverse=True)


class ServerListNavButton(ui.DynamicItem[ui.Button], template=_CID_NAV_TEMPLATE):
    """Prev/Next button on the /dev servers list. Auth: owner only.

    The target page is baked directly into the custom_id (computed at
    render time, already clamped via ``disabled``) rather than the current
    page — no "current page" state needs to survive anywhere.
    """

    def __init__(self, action: str, owner_id: int, page: int, *, disabled: bool = False):
        emoji = emojis.BACK if action == "prev" else emojis.NEXT
        super().__init__(
            ui.Button(
                style=discord.ButtonStyle.secondary,
                emoji=discord.PartialEmoji.from_str(emoji),
                custom_id=f"moddy:devservers:nav:{action}:{owner_id}:{page}",
                disabled=disabled,
            )
        )
        self.action = action
        self.owner_id = owner_id
        self.page = page

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match: re.Match):
        return cls(match["action"], int(match["owner"]), int(match["page"]))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            locale = i18n.get_user_locale(interaction)
            await interaction.response.send_message(
                view=create_error_message(
                    t("errors.not_your_message.title", locale=locale),
                    t("errors.not_your_message.description", locale=locale),
                ),
                ephemeral=True,
            )
            return

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        guilds = _sorted_guilds(bot)
        view = ServerListView(bot, interaction.user.id, guilds, locale, page=self.page)
        await interaction.response.edit_message(view=view)


class ServerListView(BaseView):
    """Paginated server list. Persistent: yes (via ServerListNavButton).

    Auth: owner only — enforced per-button by the encoded owner_id, NOT by
    interaction_check (which cannot work on a restarted shell).
    """

    __persistent__ = True

    def __init__(self, bot=None, author_id: int = None, guilds: list = None,
                 locale: str = "en-US", per_page: int = _PER_PAGE, page: int = 0):
        super().__init__()  # timeout=None
        self.bot = bot
        self.author_id = author_id
        self.guilds = guilds or []
        self.locale = locale
        self.per_page = per_page
        self.page = page
        self.total_pages = max(1, (len(self.guilds) - 1) // per_page + 1)
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
            row.add_item(ServerListNavButton(
                "prev", self.author_id or 0, max(self.page - 1, 0), disabled=self.page == 0,
            ))
            row.add_item(ServerListNavButton(
                "next", self.author_id or 0, min(self.page + 1, self.total_pages - 1),
                disabled=self.page >= self.total_pages - 1,
            ))
            self.add_item(row)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: owner only — owner_id is encoded in each button's
        custom_id and compared to interaction.user.id on click."""
        bot.add_dynamic_items(ServerListNavButton)


@staff_command
class ServerListCommand(StaffCommand):
    command_type = CommandType.DEV
    name = "servers"
    aliases = ("serverlist",)
    description = "Paginated list of every server Moddy is in."

    async def execute(self, ctx):
        guilds = _sorted_guilds(ctx.bot)
        view = ServerListView(ctx.bot, ctx.author.id, guilds, ctx.locale)
        await ctx.send(view=view)
