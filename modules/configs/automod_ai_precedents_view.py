"""
Learned-precedents browser for the Automod `/config` panel (session 7).

Lists the server precedents the automod has learned from human rulings (accepted
/ refused appeals, shadow ``✅``/``❌`` clicks — see docs/AUTOMOD_AI.md §2quinquies),
paginated, with per-item deletion so a wrong precedent can be purged. Opened from
the Automod config panel's "Précédents" section; **Back** returns to it.

Like the other module config panels this is a plain (non-persistent) `BaseView`
with a timeout — it is always opened fresh from `/config`.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import discord
from discord import ui

from cogs.error_handler import BaseView
from utils.i18n import t, i18n
from utils.emojis import SHIELD, SEARCH, BACK, NEXT, DELETE

logger = logging.getLogger("moddy.modules.automod_precedents")

_PER_PAGE = 5
_PREVIEW = 120


class AutomodAIPrecedentsView(BaseView):
    """Paginated, deletable list of a guild's learned precedents."""

    def __init__(self, bot, guild_id: int, user_id: int, locale: str = "en-US",
                 parent=None):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale
        self.parent_view = parent
        self.page = 0
        self.rows: List[Dict[str, Any]] = []

    async def load(self):
        """Fetch the guild's precedents and (re)build the view."""
        try:
            self.rows = await self.bot.db.list_precedents(
                self.guild_id, with_vectors=False)
        except Exception as e:  # noqa: BLE001
            logger.debug("precedent list load failed: %s", e)
            self.rows = []
        self._clamp_page()
        self._build_view()

    # -- helpers -------------------------------------------------------- #
    def _pages(self) -> int:
        return max(1, (len(self.rows) + _PER_PAGE - 1) // _PER_PAGE)

    def _clamp_page(self):
        self.page = max(0, min(self.page, self._pages() - 1))

    def _page_rows(self) -> List[Dict[str, Any]]:
        start = self.page * _PER_PAGE
        return self.rows[start:start + _PER_PAGE]

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                t("modules.config.errors.wrong_user",
                  locale=i18n.get_user_locale(interaction)),
                ephemeral=True)
            return False
        return True

    def _verdict_label(self, verdict: str) -> str:
        key = ("modules.automod_ai.config.precedents.verdict_not"
               if verdict == "non_sanctionnable"
               else "modules.automod_ai.config.precedents.verdict_yes")
        return t(key, locale=self.locale)

    # -- build ---------------------------------------------------------- #
    def _build_view(self):
        self.clear_items()
        container = ui.Container()
        container.add_item(ui.TextDisplay(
            f"### {SHIELD} {t('modules.automod_ai.config.precedents.title', locale=self.locale)}"))

        if not self.rows:
            container.add_item(ui.TextDisplay(
                t("modules.automod_ai.config.precedents.empty", locale=self.locale)))
            self.add_item(container)
            self._add_nav(has_items=False)
            return

        container.add_item(ui.TextDisplay(
            f"-# {t('modules.automod_ai.config.precedents.count', locale=self.locale, n=len(self.rows))}"
            f" · {t('modules.automod_ai.config.precedents.page', locale=self.locale, current=self.page + 1, total=self._pages())}"))

        page_rows = self._page_rows()
        for i, r in enumerate(page_rows, start=1):
            msg = (r.get("contenu_norm") or "")[:_PREVIEW]
            if len(r.get("contenu_norm") or "") > _PREVIEW:
                msg += "…"
            verdict = self._verdict_label(r.get("verdict_humain") or "")
            cat = r.get("categorie") or "—"
            container.add_item(ui.TextDisplay(
                f"**{self.page * _PER_PAGE + i}.** `{verdict}` · `{cat}`\n"
                f"> {msg or '*…*'}"))

        self.add_item(container)

        # Delete select (one of the current page's precedents).
        del_row = ui.ActionRow()
        del_select = ui.Select(
            placeholder=t("modules.automod_ai.config.precedents.delete_placeholder", locale=self.locale),
            min_values=0, max_values=1,
            options=[
                discord.SelectOption(
                    label=t("modules.automod_ai.config.precedents.delete_option",
                            locale=self.locale, n=self.page * _PER_PAGE + i),
                    value=str(r.get("id")),
                    emoji=discord.PartialEmoji.from_str(DELETE),
                )
                for i, r in enumerate(page_rows, start=1)
            ],
        )
        del_select.callback = self.on_delete
        del_row.add_item(del_select)
        self.add_item(del_row)

        self._add_nav(has_items=True)

    def _add_nav(self, *, has_items: bool):
        nav = ui.ActionRow()
        back_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(BACK),
            label=t("modules.config.buttons.back", locale=self.locale),
            style=discord.ButtonStyle.secondary,
        )
        back_btn.callback = self.on_back
        nav.add_item(back_btn)

        if has_items and self._pages() > 1:
            prev_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(BACK),
                label=t("modules.automod_ai.config.precedents.prev", locale=self.locale),
                style=discord.ButtonStyle.secondary,
                disabled=(self.page == 0),
            )
            prev_btn.callback = self.on_prev
            nav.add_item(prev_btn)
            next_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(NEXT),
                label=t("modules.automod_ai.config.precedents.next", locale=self.locale),
                style=discord.ButtonStyle.secondary,
                disabled=(self.page >= self._pages() - 1),
            )
            next_btn.callback = self.on_next
            nav.add_item(next_btn)
        self.add_item(nav)

    async def _rerender(self, interaction: discord.Interaction):
        self._build_view()
        await interaction.response.edit_message(view=self)

    # -- callbacks ------------------------------------------------------ #
    async def on_prev(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        self.page = max(0, self.page - 1)
        await self._rerender(interaction)

    async def on_next(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        self.page = min(self._pages() - 1, self.page + 1)
        await self._rerender(interaction)

    async def on_delete(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        values = interaction.data.get("values", [])
        if not values:
            await self._rerender(interaction)
            return
        ok = False
        try:
            ok = await self.bot.db.delete_precedent(self.guild_id, values[0])
        except Exception as e:  # noqa: BLE001
            logger.debug("precedent delete failed: %s", e)
        if ok:
            svc = getattr(self.bot, "precedents", None)
            if svc is not None:
                svc.invalidate(self.guild_id)
            self.rows = [r for r in self.rows if str(r.get("id")) != values[0]]
            self._clamp_page()
        self._build_view()
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            t("modules.automod_ai.config.precedents.deleted", locale=self.locale),
            ephemeral=True)

    async def on_back(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        parent = self.parent_view
        if parent is None:
            from modules.configs.automod_ai_config import AutomodAIConfigView
            parent = AutomodAIConfigView(self.bot, self.guild_id, self.user_id,
                                       self.locale)
        # Refresh the count shown on the parent panel after any deletions.
        if hasattr(parent, "load_precedent_stats"):
            await parent.load_precedent_stats()
        await interaction.response.edit_message(view=parent)
