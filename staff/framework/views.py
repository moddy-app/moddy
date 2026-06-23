"""Reusable interactive views for the staff command system."""

from __future__ import annotations

from typing import Awaitable, Callable, Optional

import discord
from discord import ui

from staff.framework import design
from utils import emojis
from utils.i18n import t
from cogs.error_handler import BaseView


class ConfirmView(BaseView):
    """A standardized confirm/cancel prompt for destructive staff actions.

    ``on_confirm`` is an async callable ``(interaction) -> BaseView`` that
    performs the action and returns the result panel to display. Author-checked
    and short-lived.
    """

    def __init__(self, *, bot, author_id: int, locale: str, title: str, description: str,
                 on_confirm: Callable[[discord.Interaction], Awaitable[BaseView]],
                 confirm_label: Optional[str] = None, danger: bool = True,
                 emoji: Optional[str] = None):
        super().__init__(timeout=60)
        self.bot = bot
        self.author_id = author_id
        self.locale = locale
        self.on_confirm = on_confirm

        container = design.make_container("warning")
        container.add_item(ui.TextDisplay(design.title_line(emoji or emojis.WARNING, title)))
        container.add_item(ui.TextDisplay(description))
        self.add_item(container)

        row = ui.ActionRow()
        confirm = ui.Button(
            label=confirm_label or t("staff.common.confirm", locale=locale),
            style=discord.ButtonStyle.danger if danger else discord.ButtonStyle.success,
        )
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
        view = await self.on_confirm(interaction)
        await interaction.edit_original_response(view=view)

    async def _cancel(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        await interaction.response.edit_message(view=design.info(
            t("staff.common.cancelled", locale=self.locale),
            t("staff.common.cancelled_desc", locale=self.locale),
        ))
