"""
User Cases command.

Lets a user view their own moderation cases (subject = their Discord account).
Internal staff notes are never shown. Read-only, paginated, ephemeral.
"""

import logging
from typing import List

import discord
from discord import app_commands, ui
from discord.ext import commands

from cogs.error_handler import BaseView
from config import COLORS
from utils import emojis
from utils.i18n import t, get_locale
from utils.moderation_cases import Case, SanctionStatus, EventType

logger = logging.getLogger('moddy.cases_user')


class UserCasesView(BaseView):
    """Paginated, read-only view of a user's own cases."""

    def __init__(self, bot, user_id: int, cases: List[Case], locale: str):
        super().__init__(timeout=180)
        self.bot = bot
        self.user_id = user_id
        self.cases = cases
        self.locale = locale
        self.page = 0
        self._build()

    def _build(self):
        self.clear_items()
        case = self.cases[self.page]
        accent = COLORS["info"] if case.is_open else COLORS["neutral"]
        container = ui.Container(accent_colour=discord.Colour(accent))

        status_dot = emojis.GREEN_STATUS if case.is_open else emojis.RED_STATUS
        container.add_item(ui.TextDisplay(
            f"### {case.type_emoji()} {t('commands.cases.case_title', locale=self.locale, id=case.reference)}\n"
            f"{status_dot} **{t('commands.cases.status', locale=self.locale)}:** "
            f"`{t('commands.cases.status_value.' + case.status.value, locale=self.locale)}`"
        ))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        container.add_item(ui.TextDisplay(
            f"**{t('commands.cases.type', locale=self.locale)}:** "
            f"`{t('commands.cases.type_value.' + case.type.value, locale=self.locale)}`\n"
            f"**{t('commands.cases.opened', locale=self.locale)}:** <t:{int(case.created_at.timestamp())}:F>"
        ))
        container.add_item(ui.TextDisplay(
            f"**{t('commands.cases.reason', locale=self.locale)}:**\n{case.reason[:600]}"
        ))

        # Sanctions.
        if case.sanctions:
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(ui.TextDisplay(f"**{t('commands.cases.sanctions', locale=self.locale)}**"))
            for s in case.sanctions:
                dot = emojis.GREEN_STATUS if s.status == SanctionStatus.ACTIVE else emojis.RED_STATUS
                action = t('commands.cases.action.' + s.action.value, locale=self.locale)
                line = f"{dot} {s.emoji()} **{action}** • `{t('commands.cases.sanction_status.' + s.status.value, locale=self.locale)}`"
                if s.expires_at and s.status == SanctionStatus.ACTIVE:
                    line += f" • {emojis.TIME} <t:{int(s.expires_at.timestamp())}:R>"
                container.add_item(ui.TextDisplay(line))

        # Public timeline (no internal notes).
        public = [e for e in case.events if e.type == EventType.COMMENT]
        if public:
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(ui.TextDisplay(f"**{t('commands.cases.comments', locale=self.locale)}**"))
            for e in public[-5:]:
                container.add_item(ui.TextDisplay(f"-# <t:{int(e.created_at.timestamp())}:R>\n{e.content or ''}"))

        # Pagination.
        if len(self.cases) > 1:
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(ui.TextDisplay(
                f"-# {t('commands.cases.page', locale=self.locale, current=self.page + 1, total=len(self.cases))}"
            ))
            row = ui.ActionRow()
            prev_btn = ui.Button(
                style=discord.ButtonStyle.secondary,
                emoji=discord.PartialEmoji.from_str(emojis.BACK),
                disabled=self.page == 0,
            )
            prev_btn.callback = self._prev
            row.add_item(prev_btn)
            next_btn = ui.Button(
                style=discord.ButtonStyle.secondary,
                emoji=discord.PartialEmoji.from_str(emojis.NEXT),
                disabled=self.page >= len(self.cases) - 1,
            )
            next_btn.callback = self._next
            row.add_item(next_btn)
            container.add_item(row)

        self.add_item(container)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                t("commands.cases.not_yours", locale=self.locale), ephemeral=True)
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
        self.page = min(len(self.cases) - 1, self.page + 1)
        self._build()
        await interaction.response.edit_message(view=self)


class CasesUserCog(commands.Cog):
    """User command to view their own cases."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="cases", description="View your moderation cases")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def cases_command(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        locale = get_locale(interaction)

        try:
            rows = await self.bot.db.list_subject_cases(
                'discord_user', interaction.user.id, limit=25,
            )

            if not rows:
                view = BaseView()
                container = ui.Container(accent_colour=discord.Colour(COLORS["success"]))
                container.add_item(ui.TextDisplay(
                    f"### {emojis.DONE} {t('commands.cases.empty_title', locale=locale)}"
                ))
                container.add_item(ui.TextDisplay(t('commands.cases.empty', locale=locale)))
                view.add_item(container)
                await interaction.followup.send(view=view, ephemeral=True)
                return

            cases: List[Case] = []
            for row in rows:
                data = await self.bot.db.get_case_by_id(row["id"])
                if data:
                    cases.append(Case.from_db(data["case"], data["sanctions"], data["events"]))

            view = UserCasesView(self.bot, interaction.user.id, cases, locale)
            await interaction.followup.send(view=view, ephemeral=True)

        except Exception as e:
            logger.error(f"Error fetching cases for user {interaction.user.id}: {e}", exc_info=True)
            await interaction.followup.send(
                t("commands.cases.error", locale=locale), ephemeral=True)


async def setup(bot):
    await bot.add_cog(CasesUserCog(bot))
