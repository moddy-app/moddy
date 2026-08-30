"""
Voice transcription UI — the loading card, the result card and the persistent
"Transcribe" button posted under voice messages.

Design notes
------------
The cards are deliberately bare: a title line, the transcription itself as
plain text (no code fence — the point is to read it like a message), and one
dim meta line. Nothing in a transcription can ping: every send/edit goes out
with ``AllowedMentions.none()``.

Persistence
-----------
The button is a :class:`discord.ui.DynamicItem` whose ``custom_id`` encodes the
voice message it transcribes, so it keeps working after a restart with no
in-memory state (registered by :class:`TranscriptionPersistence`, see
``utils/persistent_views.py``). Everything the callback needs is re-derived from
the interaction and from the encoded ids.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional, Tuple

import discord
from discord import ui

from cogs.error_handler import BaseView
from config import COLORS
# Generic text-to-file helper, shared with the automod cards.
from utils.automod_render import make_text_file
from utils.emojis import DOWNLOAD, ROBOT_WORKING, TIME, VOICE_CHAT
from utils.i18n import i18n, t
from utils.interaction_response import safe_defer

logger = logging.getLogger("moddy.transcription_views")

# Blurple accent — a transcription is informational, not a moderation event.
ACCENT = COLORS["primary"]

# Nothing a transcription contains may notify anyone.
NO_MENTIONS = discord.AllowedMentions.none()

_CID_PREFIX = "moddy:vtr:go"

# Name of the .txt carrying the full text of a transcription too long to fit in
# a card. Referenced by the card as `attachment://<this>`, so both must match.
TRANSCRIPT_FILE_NAME = "transcription.txt"


def _guarded(callback):
    """Route a dynamic-item callback error to the central handler (no live view)."""
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as e:  # noqa: BLE001
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, e, self.__class__.__name__)
    return wrapper


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #

async def card_locale(interaction: discord.Interaction) -> str:
    """Language of a **public** transcription card.

    The card stays in the channel for everyone to read, so it speaks the
    server language (``/config`` -> Server settings) rather than the clicker's
    — the same rule the module uses when it posts the button. In DMs there is
    no server, so the reader's own language wins. Ephemeral errors always use
    the clicker's language.
    """
    from utils.guild_language import guild_locale

    if interaction.guild is None:
        return i18n.get_user_locale(interaction)
    return await guild_locale(interaction.client, interaction.guild)


def format_duration(seconds: float) -> str:
    """``74.2`` → ``1:14``. Hours are spelled out when the audio is that long."""
    total = int(round(seconds or 0))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


# Whisper reports the detected language either as an English name ("french")
# or as an ISO code ("fr"); both are mapped onto the locale codes of the shared
# `languages.*` catalogue so the card shows the name in the reader's language.
# Anything missing here still renders, just capitalised in English.
_LANGUAGE_CODES = {
    "english": "en-US", "en": "en-US",
    "french": "fr", "français": "fr",
    "german": "de",
    "spanish": "es-ES", "es": "es-ES",
    "italian": "it",
    "portuguese": "pt-BR", "pt": "pt-BR",
    "russian": "ru",
    "polish": "pl",
    "dutch": "nl",
    "japanese": "ja",
    "chinese": "zh-CN", "zh": "zh-CN",
    "korean": "ko",
    "turkish": "tr",
    "swedish": "sv-SE", "sv": "sv-SE",
    "danish": "da",
    "norwegian": "no",
    "finnish": "fi",
    "czech": "cs",
    "greek": "el",
    "bulgarian": "bg",
    "ukrainian": "uk",
    "hindi": "hi",
    "romanian": "ro",
    "croatian": "hr",
    "hungarian": "hu",
    "thai": "th",
    "vietnamese": "vi",
    "lithuanian": "lt",
}


def format_language(raw: Optional[str], locale: str) -> Optional[str]:
    """Human-readable language name for whatever the model reported."""
    if not raw:
        return None

    value = str(raw).strip()
    code = _LANGUAGE_CODES.get(value.lower(), value)
    translated = i18n.get(f"languages.{code}", locale=locale)
    if not translated.startswith("["):
        return translated

    if len(value) <= 3:
        return value.upper()
    return value[:1].upper() + value[1:]


# --------------------------------------------------------------------------- #
# Cards
# --------------------------------------------------------------------------- #

def render_loading_card(locale: str) -> ui.LayoutView:
    """The "working on it" state, shown while the model runs."""
    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=discord.Colour(ACCENT))
    container.add_item(ui.TextDisplay(
        f"{ROBOT_WORKING} **{t('transcription.loading', locale=locale)}**"
    ))
    view.add_item(container)
    return view


def render_transcription_card(
    *,
    text: str,
    locale: str,
    duration: float = 0.0,
    language: Optional[str] = None,
    requester_id: Optional[int] = None,
    truncated: bool = False,
) -> ui.LayoutView:
    """The finished transcription."""
    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=discord.Colour(ACCENT))

    container.add_item(ui.TextDisplay(
        f"### {VOICE_CHAT} {t('transcription.title', locale=locale)}"
    ))
    container.add_item(ui.TextDisplay(text))

    if truncated:
        # The card keeps what fits; the whole thing travels as a .txt right
        # underneath, so a 20-minute recording never loses a sentence.
        container.add_item(ui.TextDisplay(
            f"-# {DOWNLOAD} {t('transcription.truncated', locale=locale)}"
        ))
        container.add_item(ui.File(f"attachment://{TRANSCRIPT_FILE_NAME}"))

    meta = [f"{TIME} `{format_duration(duration)}`"]
    language_name = format_language(language, locale)
    if language_name:
        meta.append(f"`{language_name}`")
    if requester_id:
        meta.append(t('transcription.requested_by', locale=locale,
                      user=f"<@{requester_id}>"))
    container.add_item(ui.TextDisplay(f"-# {' • '.join(meta)}"))

    view.add_item(container)
    return view


def build_transcription_message(
    result,
    *,
    locale: str,
    requester_id: Optional[int] = None,
) -> Tuple[ui.LayoutView, List[discord.File]]:
    """Card + attachments for a finished transcription.

    The single place the three call sites (context menu, button, automatic
    mode) go through, so the .txt fallback can never be wired on one path and
    forgotten on another. ``result`` is a
    ``services.transcription_service.TranscriptionResult``.
    """
    view = render_transcription_card(
        text=result.text,
        locale=locale,
        duration=result.duration,
        language=result.language,
        requester_id=requester_id,
        truncated=result.truncated,
    )
    files: List[discord.File] = []
    if result.truncated:
        files.append(make_text_file(result.full_text or result.text, TRANSCRIPT_FILE_NAME))
    return view, files


def render_error_card(code: str, locale: str, **params) -> ui.LayoutView:
    """Error card for a typed ``services.transcription_service.ErrorCode``."""
    from utils.components_v2 import create_error_message

    formatted = {k: f"`{v}`" for k, v in params.items()}
    description = i18n.get(f"transcription.errors.{code}", locale=locale, **formatted)
    if description.startswith("["):
        description = t("transcription.errors.failed", locale=locale)
    return create_error_message(t("transcription.error_title", locale=locale), description)


# --------------------------------------------------------------------------- #
# Persistent "Transcribe" button
# --------------------------------------------------------------------------- #

class TranscribeButton(
    ui.DynamicItem[ui.Button],
    template=rf"{_CID_PREFIX}:(?P<channel_id>\d+):(?P<message_id>\d+)",
):
    """Turns a voice message into text, in place, for everyone to read.

    Public by design: any member who can see the voice message may ask for its
    transcription — that is the whole point of the feature (accessibility, and
    reading a voice note in a meeting). The cost side is guarded by the
    gateway's provider rate limits, not by who clicks.
    """

    def __init__(self, channel_id: int, message_id: int, locale: str = "en-US"):
        super().__init__(
            ui.Button(
                label=t("transcription.button", locale=locale)[:80],
                style=discord.ButtonStyle.secondary,
                emoji=discord.PartialEmoji.from_str(VOICE_CHAT),
                custom_id=f"{_CID_PREFIX}:{channel_id}:{message_id}",
            )
        )
        self.channel_id = channel_id
        self.message_id = message_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button,
                             match: re.Match):
        return cls(int(match["channel_id"]), int(match["message_id"]))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        from services.transcription_service import ErrorCode, TranscriptionError

        bot = interaction.client
        # The server-language lookup is the first await of the callback and can
        # reach the database — acknowledge before it, then edit.
        await safe_defer(interaction, thinking=False)
        public_locale = await card_locale(interaction)

        # Swap the button for the loading state straight away: the model call
        # takes seconds, and a button that looks idle invites a second click.
        await interaction.edit_original_response(view=render_loading_card(public_locale))

        message = await self._fetch_message(bot)
        if message is None:
            await self._restore(interaction, ErrorCode.NO_AUDIO)
            return

        service = getattr(bot, "transcription", None)
        if service is None:
            await self._restore(interaction, ErrorCode.UNAVAILABLE)
            return

        try:
            result = await service.transcribe_message(
                message, requester_id=interaction.user.id
            )
        except TranscriptionError as exc:
            await self._restore(interaction, exc.code, **exc.params)
            return

        view, files = build_transcription_message(
            result, locale=public_locale, requester_id=interaction.user.id
        )
        await interaction.edit_original_response(
            view=view, attachments=files, allowed_mentions=NO_MENTIONS,
        )

    async def _fetch_message(self, bot) -> Optional[discord.Message]:
        channel = bot.get_channel(self.channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(self.channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return None
        try:
            return await channel.fetch_message(self.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    async def _restore(self, interaction: discord.Interaction, code: str, **params) -> None:
        """Put the button back and explain the failure to the clicker alone."""
        await interaction.edit_original_response(
            view=TranscribePromptView(
                self.channel_id, self.message_id, await card_locale(interaction)
            )
        )
        await interaction.followup.send(
            view=render_error_card(code, i18n.get_user_locale(interaction), **params),
            ephemeral=True,
        )


class TranscribePromptView(BaseView):
    """The one-button message posted under a voice message.

    Persistence lives on the button itself (a ``DynamicItem`` registered by
    :class:`TranscriptionPersistence`), exactly like the starboard card's
    reactors button — this view holds no state of its own and is never
    registered as a shell.
    """

    def __init__(self, channel_id: int, message_id: int, locale: str = "en-US"):
        super().__init__()  # timeout=None
        row = ui.ActionRow()
        row.add_item(TranscribeButton(channel_id, message_id, locale))
        self.add_item(row)


class TranscriptionPersistence(BaseView):
    """Marker view: registers the transcribe button's dynamic item at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(TranscribeButton)
