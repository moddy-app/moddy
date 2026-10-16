"""
Learned-precedents browser for the Automod `/config` panel.

Lists the server precedents the automod has learned from human rulings
(accepted / refused appeals, shadow-mode annotations — docs/AUTOMOD_AI.md
§2quinquies), paginated, with per-item deletion so a wrong precedent can be
purged. Opened from Advanced; **Back** returns there (a pending Advanced draft is
kept). Deleting a precedent is a data action, not a setting: it is applied at
once, there is nothing to save here.

Persistence
-----------
The page number rides in the custom_id, so every control is a ``DynamicItem``
(``moddy:automod:cfg:prec:<action>:<page>`` and ``moddy:automod:cfg:precdel:<page>``)
registered by ``AutomodConfigPersistence``. The view that holds them is not
registered (it cannot be built without a page). Auth: Manage Server on every
click; the rows are re-read from the database each time.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import discord
from discord import ui

from cogs.error_handler import BaseView
from modules.configs import automod_ai_common as common
from config import COLORS
from modules.configs.automod_ai_common import row, title, tp, trail
from utils.emojis import BACK, DELETE, NEXT, SEARCH
from utils.i18n import i18n, t

logger = logging.getLogger("moddy.modules.automod_precedents")

_PER_PAGE = 5
_PREVIEW = 120


def _guarded(callback):
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as e:  # noqa: BLE001
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, e, self.__class__.__name__)
    return wrapper


def page_count(total: int) -> int:
    return max(1, (total + _PER_PAGE - 1) // _PER_PAGE)


async def render_precedents(interaction: discord.Interaction, page: int = 0) -> None:
    await common.ack(interaction)
    bot = interaction.client
    try:
        rows = await bot.db.list_precedents(interaction.guild_id, with_vectors=False)
    except Exception as e:  # noqa: BLE001
        logger.debug("precedent list load failed: %s", e)
        rows = []
    await common.respond(interaction, AutomodAIPrecedentsView(
        bot, interaction.guild_id, i18n.get_user_locale(interaction), rows, page))


class PrecedentsNavButton(
    ui.DynamicItem[ui.Button],
    template=r"moddy:automod:cfg:prec:(?P<action>prev|next|back):(?P<page>\d{1,4})",
):
    """Previous / next page / back to Advanced."""

    def __init__(self, action: str, page: int, *, label: str = "—", emoji: Optional[str] = None,
                 disabled: bool = False):
        super().__init__(ui.Button(
            label=label, style=discord.ButtonStyle.secondary, disabled=disabled,
            emoji=common.emoji_of(emoji),
            custom_id=f"moddy:automod:cfg:prec:{action}:{page}"))
        self.action = action
        self.page = page

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["action"], int(match["page"]))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if not await common.gate(interaction):
            return
        if self.action == "back":
            await common.go(interaction, "advanced")
        else:
            await render_precedents(interaction, self.page + (1 if self.action == "next" else -1))


class PrecedentsDeleteSelect(
    ui.DynamicItem[ui.Select],
    template=r"moddy:automod:cfg:precdel:(?P<page>\d{1,4})",
):
    """Delete one wrong precedent of the current page."""

    def __init__(self, page: int, *, item: Optional[ui.Select] = None):
        super().__init__(item or ui.Select(
            options=[discord.SelectOption(label="—", value="none")],
            custom_id=f"moddy:automod:cfg:precdel:{page}"))
        self.page = page

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["page"]))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if not await common.gate(interaction):
            return
        await common.ack(interaction)
        values = interaction.data.get("values") or []
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        if values and values[0] != "none":
            ok = False
            try:
                ok = await bot.db.delete_precedent(interaction.guild_id, values[0])
            except Exception as e:  # noqa: BLE001
                logger.debug("precedent delete failed: %s", e)
            if ok:
                svc = getattr(bot, "precedents", None)
                if svc is not None:
                    svc.invalidate(interaction.guild_id)
        await render_precedents(interaction, self.page)
        if values:
            await interaction.followup.send(tp("precedents.deleted", locale), ephemeral=True)


class AutomodAIPrecedentsView(BaseView):
    """Paginated, deletable list of a guild's learned precedents."""

    def __init__(self, bot, guild_id: int, locale: str, rows: List[Dict[str, Any]], page: int = 0):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.locale = locale
        self.rows = rows
        self.page = max(0, min(page, page_count(len(rows)) - 1))
        self._build_view()

    def _verdict(self, verdict: str) -> str:
        return tp("precedents.verdict_not" if verdict == "non_sanctionnable"
                  else "precedents.verdict_yes", self.locale)

    def _build_view(self) -> None:
        self.clear_items()
        locale, page = self.locale, self.page
        container = ui.Container(accent_colour=discord.Colour(COLORS["primary"]))
        container.add_item(trail(locale, tp("nav.advanced", locale), tp("precedents.title", locale)))
        container.add_item(title(locale, "precedents.title", SEARCH))
        container.add_item(ui.TextDisplay(tp("precedents.intro", locale)))

        page_rows = self.rows[page * _PER_PAGE:(page + 1) * _PER_PAGE]
        if not page_rows:
            container.add_item(ui.TextDisplay(f"-# {tp('precedents.empty', locale)}"))
        else:
            container.add_item(ui.TextDisplay(
                f"-# {tp('precedents.count', locale, n=len(self.rows))} · "
                f"{tp('precedents.page', locale, current=page + 1, total=page_count(len(self.rows)))}"))
            for i, r in enumerate(page_rows, start=1):
                content = r.get("contenu_norm") or ""
                msg = common.safe_text(content[:_PREVIEW]) + ("…" if len(content) > _PREVIEW else "")
                container.add_item(ui.TextDisplay(
                    f"**{page * _PER_PAGE + i}.** `{self._verdict(r.get('verdict_humain') or '')}`"
                    f" · `{r.get('categorie') or '—'}`\n> {msg or '…'}"))
            container.add_item(row(PrecedentsDeleteSelect(page, item=ui.Select(
                placeholder=tp("precedents.delete_placeholder", locale),
                min_values=0, max_values=1,
                custom_id=f"moddy:automod:cfg:precdel:{page}",
                options=[discord.SelectOption(
                    label=tp("precedents.delete_option", locale, n=page * _PER_PAGE + i),
                    value=str(r.get("id")), emoji=common.emoji_of(DELETE))
                    for i, r in enumerate(page_rows, start=1)]))))
        self.add_item(container)

        pages = page_count(len(self.rows))
        nav = [PrecedentsNavButton("back", page, label=t("modules.config.buttons.back", locale=locale),
                                   emoji=BACK)]
        if pages > 1:
            nav.append(PrecedentsNavButton("prev", page, label=tp("precedents.prev", locale),
                                           emoji=BACK, disabled=page == 0))
            nav.append(PrecedentsNavButton("next", page, label=tp("precedents.next", locale),
                                           emoji=NEXT, disabled=page >= pages - 1))
        self.add_item(row(*nav))
