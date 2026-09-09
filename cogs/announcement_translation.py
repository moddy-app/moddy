"""
Announcement translation — support server only.

Every message posted in one of the support server's announcement channels
(``ANNOUNCEMENT_TRANSLATION_CHANNEL_IDS``) is translated through DeepL into all
five languages Moddy speaks **once, at post time**, and stored in
``announcement_translations``. Moddy then replies with a bare container holding
one flag button per language; clicking a flag reads the stored translation back
and shows it ephemerally, as plain text, with nothing around it.

Translating up front rather than on click is the whole design: an announcement
read by a thousand people costs five DeepL calls, not a thousand.

See docs/ANNOUNCEMENT_TRANSLATION.md.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, Optional

import discord
from discord import ui
from discord.ext import commands

from cogs.error_handler import BaseView
from config import ANNOUNCEMENT_TRANSLATION_CHANNEL_IDS, MODDY_TEAM_GUILD_ID
from utils.i18n import i18n, t

logger = logging.getLogger('moddy.announcement_translation')

# The languages Moddy speaks, in button order. Each entry is
# code → (DeepL target language, flag, label written in that language).
# The label is deliberately *not* i18n'd: a button offering German is labelled
# "Deutsch" whoever is looking at it, which is the only way a reader who does
# not speak the announcement's language can find their own.
LANGUAGES: Dict[str, tuple] = {
    "fr": ("FR", "🇫🇷", "Français"),
    "en": ("EN-US", "🇺🇸", "English"),
    "es": ("ES", "🇪🇸", "Español"),
    "pt": ("PT-BR", "🇧🇷", "Português"),
    "de": ("DE", "🇩🇪", "Deutsch"),
}

# DeepL reports the source language as a bare code (FR, EN, PT…); this maps it
# back to our button codes so the announcement's own language is stored as the
# original text instead of being sent on a pointless round trip.
_SOURCE_TO_CODE = {
    "FR": "fr", "EN": "en", "ES": "es", "PT": "pt", "DE": "de",
}

_CID_PREFIX = "moddy:anntr:lang"

# Discord caps a message at 4000 characters and a TextDisplay at 4000 too; a
# translation can come back slightly longer than its source, so both ends are
# bounded.
_MAX_SOURCE = 3500
_MAX_RENDERED = 3900


def _guarded(callback):
    """Funnel a dynamic-item callback error to the central handler (no live view)."""
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as e:  # noqa: BLE001
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, e, self.__class__.__name__)
    return wrapper


def sanitize_mentions(text: str, guild: Optional[discord.Guild]) -> str:
    """Turn mentions into plain text so DeepL sees words, not raw ids."""
    text = text.replace('@everyone', '@​everyone')
    text = text.replace('@here', '@​here')

    def replace_user(match: re.Match) -> str:
        if guild:
            member = guild.get_member(int(match.group(1)))
            if member:
                return f"@{member.display_name}"
        return "@user"

    def replace_role(match: re.Match) -> str:
        if guild:
            role = guild.get_role(int(match.group(1)))
            if role:
                return f"@{role.name}"
        return "@role"

    text = re.sub(r'<@!?(\d+)>', replace_user, text)
    text = re.sub(r'<@&(\d+)>', replace_role, text)
    return text


# --------------------------------------------------------------------------- #
# The language button (DynamicItem, persistent)
# --------------------------------------------------------------------------- #

class AnnouncementLanguageButton(
    ui.DynamicItem[ui.Button],
    template=rf"{_CID_PREFIX}:(?P<code>fr|en|es|pt|de):(?P<mid>\d{{15,25}})",
):
    """One flag button under an announcement. Auth: public (read-only)."""

    def __init__(self, code: str, message_id: int):
        _, flag, label = LANGUAGES[code]
        super().__init__(
            ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                emoji=discord.PartialEmoji.from_str(flag),
                custom_id=f"{_CID_PREFIX}:{code}:{message_id}",
            )
        )
        self.code = code
        self.message_id = message_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["code"], int(match["mid"]))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        # Everything is re-derived from the click: the custom_id names the
        # announcement, the row names the text. `self` holds nothing else.
        from utils.components_v2 import create_error_message

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)

        row = None
        if getattr(bot, "db", None):
            row = await bot.db.get_announcement_translations(self.message_id)
        text = (row or {}).get("translations", {}).get(self.code)

        if not text:
            await interaction.response.send_message(
                view=create_error_message(
                    t("announcement_translation.error.title", locale=locale),
                    t("announcement_translation.error.missing", locale=locale)),
                ephemeral=True)
            return

        view = ui.LayoutView(timeout=None)
        container = ui.Container()
        container.add_item(ui.TextDisplay(text[:_MAX_RENDERED]))
        view.add_item(container)
        await interaction.response.send_message(view=view, ephemeral=True)


class AnnouncementTranslationView(BaseView):
    """The buttons-only card replying to an announcement. Auth: public."""

    __persistent__ = True

    def __init__(self, message_id: int = 0, codes=None):
        super().__init__()
        self.message_id = message_id
        self.codes = list(codes or [])
        self.build_view()

    def build_view(self):
        self.clear_items()
        if not self.message_id or not self.codes:
            # Shell instance (registration only): the buttons are DynamicItems,
            # registered by class, so an empty shell is all that is needed.
            return
        container = ui.Container()
        row = ui.ActionRow()
        for code in self.codes:
            row.add_item(AnnouncementLanguageButton(code, self.message_id))
        container.add_item(row)
        self.add_item(container)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: public — anyone who can read the announcement may click."""
        bot.add_dynamic_items(AnnouncementLanguageButton)


# --------------------------------------------------------------------------- #
# Cog
# --------------------------------------------------------------------------- #

class AnnouncementTranslation(commands.Cog):
    """Translates support-server announcements once and offers them per language."""

    def __init__(self, bot):
        self.bot = bot

    def _is_announcement_channel(self, message: discord.Message) -> bool:
        if not message.guild or message.guild.id != MODDY_TEAM_GUILD_ID:
            return False
        return message.channel.id in ANNOUNCEMENT_TRANSLATION_CHANNEL_IDS

    async def _translate_all(
        self, text: str, *, user_id: int
    ) -> tuple[Dict[str, str], Optional[str]]:
        """Translate one announcement into every language but its own.

        DeepL reports the detected source language with every translation, so
        the announcement's own language is skipped as soon as it is known — and
        dropped at the end whatever the order the calls happened in. An
        announcement written in English therefore ends up with no English entry,
        hence no English button: offering to translate a message into the
        language it is already written in is noise.

        The final ``pop`` is what makes this order-independent. Skipping ahead of
        the call only works for a language the loop has not reached yet; the one
        translated *before* the source was known (the first call, always) has to
        be removed afterwards. A result that comes back identical to the source
        is dropped too, which catches the announcements whose language DeepL
        detects wrongly or not at all.
        """
        from gateway import QuotaTarget

        translations: Dict[str, str] = {}
        source_code: Optional[str] = None

        for code, (target, _flag, _label) in LANGUAGES.items():
            if source_code == code:
                continue
            try:
                result = await self.bot.gateway.translation.translate(
                    text,
                    target,
                    quota=[QuotaTarget.user(user_id, "translation")],
                    call_type="translation",
                    metadata={"user_id": user_id, "feature": "announcement"},
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("Announcement translation to %s failed: %s", target, exc)
                continue

            if not result or not result.get("text"):
                continue
            # A "translation" identical to the announcement is the announcement:
            # DeepL was handed a text already in that language and gave it back.
            # The button would show the message the reader is looking at, so it
            # is dropped — this also covers a detection DeepL got wrong or did
            # not report, which language detection on a short text does.
            if result["text"].strip().casefold() != text.strip().casefold():
                translations[code] = result["text"]

            if source_code is None:
                detected = (result.get("detected_source_language") or "").upper()
                source_code = _SOURCE_TO_CODE.get(detected.split("-")[0])
                logger.debug("Announcement source detected as %r -> %r",
                             detected, source_code)

        # The announcement's own language never gets a button, including when it
        # is the language the first call happened to translate into.
        if source_code:
            translations.pop(source_code, None)

        return translations, source_code

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not self._is_announcement_channel(message):
            return
        content = (message.content or "").strip()
        if not content:
            return

        gateway = getattr(self.bot, "gateway", None)
        if not gateway or not gateway.deepl_available():
            logger.warning("DeepL unavailable — announcement %s not translated",
                           message.id)
            return

        source = sanitize_mentions(content, message.guild)[:_MAX_SOURCE]
        translations, source_code = await self._translate_all(
            source, user_id=message.author.id)
        if not translations:
            logger.error("No translation produced for announcement %s", message.id)
            return

        if getattr(self.bot, "db", None):
            try:
                await self.bot.db.save_announcement_translations(
                    message.id,
                    guild_id=message.guild.id,
                    channel_id=message.channel.id,
                    source_lang=source_code,
                    translations=translations,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("Storing announcement %s failed: %s", message.id, exc)
                return

        view = AnnouncementTranslationView(message.id, list(translations.keys()))
        try:
            await message.reply(view=view, mention_author=False)
        except discord.HTTPException as exc:
            logger.error("Posting translation buttons for %s failed: %s",
                         message.id, exc)


async def setup(bot):
    await bot.add_cog(AnnouncementTranslation(bot))
