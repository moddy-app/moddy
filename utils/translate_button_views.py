"""
Views of the translate button module — see docs/features/TRANSLATE_BUTTON.md.

* the **prompt** Moddy posts under a message in a foreign language: one dim
  line (``<flag> Language detected``) and a *Translate* button;
* the persistent **button** itself (a ``DynamicItem`` carrying the channel and
  the first / last message it stands for, so it survives restarts with no
  stored state);
* the **translation card** shown privately to whoever clicks, in their language.

List each persistent class in ``PERSISTENT_VIEWS`` and each DynamicItem in
``DYNAMIC_ITEM_CASES`` (class, sample constructor args): both are picked up by
utils/tool_views_registry.py.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

import discord
from discord import ui

from cogs.error_handler import BaseView
from modules.configs.translate_button_config import TranslateButtonConfigView
from tools import langdetect as ld
from utils.emojis import DEEPL, TRANSLATE
from utils.errors import build_error_card
from utils.i18n import i18n, t
from utils.tool_card import (
    ACCENT_INFO, NO_MENTIONS, ToolCard, button_emoji, guarded, loading_card, source_line,
    truncate,
)

logger = logging.getLogger("moddy.translate_button_views")

_CID_PREFIX = "moddy:autotr:tr"

#: Translated text shown on one card (Components V2 allows 4000 per message).
CARD_TEXT_BUDGET = 3300


def _tm(key: str, locale: str, **kwargs) -> str:
    return t(f"modules.translate_button.{key}", locale=locale, **kwargs)


def language_name(code: str, locale: str) -> str:
    """Name of a language in ``locale``; the upper-cased code when unknown."""
    base = ld.base_language(code)
    name = i18n.get(f"modules.translate_button.languages.{base}", locale=locale)
    return base.upper() if name.startswith("[") else name


def language_label(code: str, locale: str) -> str:
    """``<flag> Name`` — the flag is dropped when the language has none."""
    flag = ld.language_flag(code)
    name = language_name(code, locale)
    return f"{flag} {name}" if flag else name


# --------------------------------------------------------------------------- #
# The prompt
# --------------------------------------------------------------------------- #

def build_prompt_view(channel_id: int, first_id: int, last_id: int, language: str,
                      locale: str = "en-US") -> "TranslatePromptView":
    """The prompt for the messages ``first_id`` .. ``last_id`` of one member.

    Read by the whole channel, so ``locale`` is the SERVER language.
    """
    return TranslatePromptView(channel_id, first_id, last_id, language, locale)


class TranslatePromptView(BaseView):
    """``-# 🇫🇷 French detected`` + the Translate button, in one small card.

    Persistence lives on the button (a ``DynamicItem`` registered by
    :class:`TranslateButtonPersistence`); this view holds no state.
    """

    def __init__(self, channel_id: int = 0, first_id: int = 0, last_id: int = 0,
                 language: str = "en", locale: str = "en-US"):
        super().__init__()  # timeout=None
        line = _tm("prompt.detected", locale,
                   language=language_label(language, locale))
        container = ui.Container(accent_colour=discord.Colour(ACCENT_INFO))
        container.add_item(ui.Section(
            ui.TextDisplay(f"-# {line}"),
            accessory=TranslateButton(channel_id, first_id, last_id, locale),
        ))
        self.add_item(container)


class TranslateButton(
    ui.DynamicItem[ui.Button],
    template=rf"{_CID_PREFIX}:(?P<channel_id>\d+):(?P<first_id>\d+):(?P<last_id>\d+)",
):
    """Translates the prompt's message(s) for whoever clicks, privately.

    Public by design: any member who can see the message may ask for its
    translation, into **their own** language (the card is ephemeral). The cost
    side is guarded by a per-user click limit and the gateway's DeepL quotas.
    """

    def __init__(self, channel_id: int, first_id: int, last_id: int, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=_tm("prompt.button", locale)[:80],
            style=discord.ButtonStyle.secondary,
            emoji=button_emoji(TRANSLATE),
            custom_id=f"{_CID_PREFIX}:{channel_id}:{first_id}:{last_id}",
        ))
        self.channel_id = channel_id
        self.first_id = first_id
        self.last_id = last_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button,
                             match: re.Match):
        return cls(int(match["channel_id"]), int(match["first_id"]), int(match["last_id"]))

    @guarded
    async def callback(self, interaction: discord.Interaction):
        from services.translate_button_service import TranslateError

        locale = i18n.get_user_locale(interaction)
        service = getattr(interaction.client, "translate_button", None)
        if (service is None or interaction.guild_id is None
                or interaction.channel_id != self.channel_id):
            await self._fail(interaction, "unavailable", locale)
            return
        if not service.available():
            await self._fail(interaction, "unavailable", locale)
            return
        if not service.allow_click(interaction.user.id):
            await self._fail(interaction, "rate_limited", locale)
            return

        # Answer first: the DeepL call can take a second or two.
        await interaction.response.send_message(
            view=loading_card(locale, _tm("loading", locale)), ephemeral=True)
        try:
            result = await service.translate_messages(
                interaction, interaction.channel, self.first_id, self.last_id)
        except TranslateError as exc:
            await interaction.edit_original_response(view=render_error_card(exc.code, locale))
            return
        await interaction.edit_original_response(
            view=render_translation_card(result, locale), allowed_mentions=NO_MENTIONS)

    @staticmethod
    async def _fail(interaction: discord.Interaction, code: str, locale: str) -> None:
        await interaction.response.send_message(
            view=render_error_card(code, locale), ephemeral=True)


class TranslateButtonPersistence(BaseView):
    """Marker view: registers the translate button's dynamic item at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(TranslateButton)


# --------------------------------------------------------------------------- #
# Cards
# --------------------------------------------------------------------------- #

def quote(text: str) -> str:
    """``text`` as a Discord block quote (every line, empty ones included)."""
    return "\n".join(f"> {line}" if line else ">" for line in (text or "").splitlines()) or ">"


def render_translation_card(result, locale: str) -> ui.LayoutView:
    """The private translation: who wrote it, the text, and where it comes from.

    ``result`` is a ``services.translate_button_service.TranslationResult``.
    """
    items = result.items
    sources = {ld.base_language(i.source) for i in items if i.source}
    target = language_label(result.target, locale)
    subtitle = None
    if len(sources) == 1:
        subtitle = f"-# {language_label(next(iter(sources)), locale)} → {target}"
    else:
        subtitle = f"-# → {target}"

    card = ToolCard(title=_tm("card.title", locale), icon=TRANSLATE, subtitle=subtitle)
    first = items[0]
    header = f"<@{result.author_id}>"
    if first.jump_url:
        header += f" · [{_tm('card.jump', locale)}]({first.jump_url})"
    card.text(header)

    per_item = max(200, CARD_TEXT_BUDGET // max(1, len(items)))
    for item in items:
        text, _ = truncate(item.text, per_item)
        card.text(quote(text))
    card.footer(source_line(locale, (DEEPL, "DeepL"), verb="translated_by"))
    return card.build()


def render_error_card(code: str, locale: str) -> ui.LayoutView:
    """Private card for an expected failure (``errors.<code>``)."""
    description = i18n.get(f"modules.translate_button.errors.{code}", locale=locale)
    if description.startswith("["):
        description = _tm("errors.failed", locale)
    return build_error_card(_tm("errors.title", locale), description)


PERSISTENT_VIEWS: List[type] = [TranslateButtonPersistence, TranslateButtonConfigView]
DYNAMIC_ITEM_CASES: list = [
    (TranslateButton, (123456789012345678, 987654321098765432, 987654321098765433)),
]
