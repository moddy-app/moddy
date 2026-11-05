"""
Extras under the welcome DMs of a few hand-picked servers.

Exceptionally (owner request), the welcome DMs of Moddy's own server get, under
the card: a blue **Translate** button that translates the welcome text into the
reader's language (DeepL, through the translate button service and its quotas),
and two link buttons — Website (moddy.app) and Documentation (docs.moddy.app).

The Translate button is a persistent ``DynamicItem``: its custom_id only carries
the guild id; the text is re-read from the DM it sits under on every click, so
it keeps working after restarts and after the welcome message was edited.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

import discord
from discord import ui

from cogs.error_handler import BaseView
from utils.emojis import BOOK, DEEPL, TRANSLATE, WEB
from utils.i18n import i18n, t
from utils.tool_card import NO_MENTIONS, ToolCard, button_emoji, guarded, loading_card, \
    source_line, truncate

logger = logging.getLogger("moddy.welcome_dm_views")

#: Servers whose welcome DMs carry the Translate + Website + Documentation row.
WELCOME_EXTRAS_GUILDS = frozenset({1394001780148535387})

WEBSITE_URL = "https://moddy.app"
DOCS_URL = "https://docs.moddy.app"

_CID_PREFIX = "moddy:welcome_dm:translate"

#: DeepL input / card text budget (Components V2 allows 4000 per message).
_MAX_CHARS = 3300


def _tw(key: str, locale: str, **kwargs) -> str:
    return t(f"modules.welcome_dm.extras.{key}", locale=locale, **kwargs)


def has_extras(guild_id: Optional[int]) -> bool:
    return guild_id is not None and int(guild_id) in WELCOME_EXTRAS_GUILDS


def add_extras_row(view: ui.LayoutView, guild_id: int, locale: str) -> None:
    """Append the Translate / Website / Documentation row under the card."""
    row = ui.ActionRow()
    row.add_item(WelcomeDmTranslateButton(guild_id, locale))
    row.add_item(ui.Button(style=discord.ButtonStyle.link, url=WEBSITE_URL,
                           label=_tw("website", locale)[:80], emoji=button_emoji(WEB)))
    row.add_item(ui.Button(style=discord.ButtonStyle.link, url=DOCS_URL,
                           label=_tw("docs", locale)[:80], emoji=button_emoji(BOOK)))
    view.add_item(row)


def welcome_text_of(message: Optional[discord.Message]) -> str:
    """The welcome text of a DM: the first text block of its first container.

    The attribution line (``-# Sent by …``) is a separate, later text block, so
    it is never translated.
    """
    if message is None:
        return ""

    def first_text(components) -> Optional[str]:
        for comp in components or ():
            content = getattr(comp, "content", None)
            if isinstance(content, str) and content.strip():
                return content
            found = first_text(getattr(comp, "children", None) or getattr(comp, "components", None))
            if found:
                return found
        return None

    return (first_text(getattr(message, "components", None)) or "").strip()


class WelcomeDmTranslateButton(
    ui.DynamicItem[ui.Button],
    template=rf"{_CID_PREFIX}:(?P<guild_id>\d+)",
):
    """Translates the welcome DM it sits under, privately, into the reader's language."""

    def __init__(self, guild_id: int, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=_tw("translate", locale)[:80],
            style=discord.ButtonStyle.primary,
            emoji=button_emoji(TRANSLATE),
            custom_id=f"{_CID_PREFIX}:{guild_id}",
        ))
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button,
                             match: re.Match):
        return cls(int(match["guild_id"]))

    @guarded
    async def callback(self, interaction: discord.Interaction):
        from services.translate_button_service import TranslateError
        from utils.translate_button_views import language_label, quote, render_error_card

        locale = i18n.get_user_locale(interaction)
        service = getattr(interaction.client, "translate_button", None)
        if service is None or not service.available():
            await interaction.response.send_message(
                view=render_error_card("unavailable", locale), ephemeral=True)
            return
        if not service.allow_click(interaction.user.id):
            await interaction.response.send_message(
                view=render_error_card("rate_limited", locale), ephemeral=True)
            return
        text = welcome_text_of(interaction.message)
        if not text:
            await interaction.response.send_message(
                view=render_error_card("empty", locale), ephemeral=True)
            return

        # Answer first: the DeepL call can take a second or two.
        await interaction.response.send_message(
            view=loading_card(locale, _tw("loading", locale)), ephemeral=True)
        target = service.deepl_target(interaction)
        try:
            source_text = service.neutralize_mentions(text, None)[:_MAX_CHARS]
            data = await service._deepl(interaction.user.id, source_text, target)
        except TranslateError as exc:
            await interaction.edit_original_response(view=render_error_card(exc.code, locale))
            return

        source = data.get("source")
        subtitle = (f"-# {language_label(source, locale)} → {language_label(target, locale)}"
                    if source else f"-# → {language_label(target, locale)}")
        card = ToolCard(title=_tw("card_title", locale), icon=TRANSLATE, subtitle=subtitle)
        translated, _ = truncate(data["text"], _MAX_CHARS)
        card.text(quote(translated))
        card.footer(source_line(locale, (DEEPL, "DeepL"), verb="translated_by"))
        await interaction.edit_original_response(view=card.build(),
                                                 allowed_mentions=NO_MENTIONS)


class WelcomeDmExtrasPersistence(BaseView):
    """Marker: registers the Translate button's dynamic item at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(WelcomeDmTranslateButton)


PERSISTENT_VIEWS: List[type] = [WelcomeDmExtrasPersistence]
DYNAMIC_ITEM_CASES: list = [(WelcomeDmTranslateButton, (1394001780148535387,))]
