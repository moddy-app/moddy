"""
Announcement verification — authenticate support-server announcements.

Every message posted in one of the support server's announcement channels
(``ANNOUNCEMENT_VERIFICATION_CHANNEL_IDS``) gets a reply holding an
*Authenticate* button that only Managers and Developers of the Moddy team may
press. Pressing it records the announcement as authentic (with a hash of its
text) and translates it once into every language Moddy speaks.

Discord relays a published announcement into every server that follows the
channel, as a webhook message flagged ``IS_CROSSPOST`` whose reference points
back at the original. Moddy answers each such copy of an authenticated
announcement with a confirmation card and a *Translate* button that shows the
stored translation in the reader's own language. A copy that arrives before
the announcement is authenticated is remembered and answered when it is.

See docs/features/ANNOUNCEMENT_VERIFICATION.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from typing import Optional

import discord
from discord import ui
from discord.ext import commands

from cogs.announcement_translation import (
    AnnouncementTranslation, _MAX_RENDERED, _MAX_SOURCE, sanitize_mentions,
)
from cogs.error_handler import BaseView
from config import ANNOUNCEMENT_VERIFICATION_CHANNEL_IDS, COLORS, MODDY_TEAM_GUILD_ID
from utils.emojis import TRANSLATE, VERIFIED, WARNING
from utils.i18n import i18n, t

logger = logging.getLogger('moddy.announcement_verification')

_VERIFY_CID = "moddy:annver:verify"
_TRANSLATE_CID = "moddy:annver:translate"
NO_MENTIONS = discord.AllowedMentions.none()

# Pause between two replies when a backlog of copies is answered at once: the
# library handles rate limits, this only keeps a large fan-out from bursting.
_BACKLOG_PAUSE = 0.25


def content_hash(text: Optional[str]) -> str:
    """Fingerprint of an announcement's text, compared on every relayed copy."""
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()


def relayed_source_id(message: discord.Message) -> Optional[int]:
    """Id of the support-server announcement ``message`` is a relayed copy of.

    Only Discord sets ``IS_CROSSPOST``: a webhook posting by itself cannot, so
    the flag plus a reference to a watched channel of the support server is
    what tells a real relay from a look-alike.
    """
    if not message.flags.is_crossposted or message.webhook_id is None:
        return None
    ref = message.reference
    if ref is None or ref.message_id is None:
        return None
    if ref.guild_id != MODDY_TEAM_GUILD_ID:
        return None
    if ref.channel_id not in ANNOUNCEMENT_VERIFICATION_CHANNEL_IDS:
        return None
    return ref.message_id


def language_code(locale: str) -> str:
    """``en-US`` → ``en``: the key the stored translations use."""
    return (locale or "").split("-")[0].lower()


async def can_verify(bot, user_id: int) -> bool:
    """Managers and Developers of the Moddy team, and nobody else."""
    from utils.staff_permissions import StaffRole, staff_permissions
    if staff_permissions is None:
        return False
    roles = await staff_permissions.get_user_roles(user_id)
    return StaffRole.MANAGER in roles or StaffRole.DEV in roles


def _guarded(callback):
    """Funnel a dynamic-item callback error to the central handler (no live view)."""
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as e:  # noqa: BLE001
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, e, self.__class__.__name__)
    return wrapper


# --------------------------------------------------------------------------- #
# Cards
# --------------------------------------------------------------------------- #

def build_verify_card(message_id: int, locale: str, *, edited: bool = False) -> BaseView:
    """Support-server reply to a fresh (or edited) announcement."""
    view = BaseView()
    container = ui.Container(accent_colour=COLORS["warning"] if edited else COLORS["primary"])
    container.add_item(ui.TextDisplay(
        f"### {VERIFIED} {t('announcement_verification.verify.title', locale=locale)}"))
    if edited:
        container.add_item(ui.TextDisplay(
            f"{WARNING} {t('announcement_verification.verify.edited', locale=locale)}"))
    container.add_item(ui.TextDisplay(
        t("announcement_verification.verify.description", locale=locale)))
    row = ui.ActionRow()
    row.add_item(VerifyAnnouncementButton(message_id, label=t(
        "announcement_verification.verify.button", locale=locale)))
    container.add_item(row)
    view.add_item(container)
    return view


def build_verified_card(user_id: int, locale: str, *, translated: bool) -> BaseView:
    """The verify card once the announcement is authenticated (no button)."""
    view = BaseView()
    container = ui.Container(accent_colour=COLORS["success"])
    container.add_item(ui.TextDisplay(
        f"### {VERIFIED} {t('announcement_verification.verified.title', locale=locale)}"))
    container.add_item(ui.TextDisplay(t(
        "announcement_verification.verified.description", locale=locale,
        user=f"<@{user_id}>", time=f"<t:{int(time.time())}:R>")))
    if not translated:
        container.add_item(ui.TextDisplay(
            f"-# {t('announcement_verification.verified.no_translation', locale=locale)}"))
    view.add_item(container)
    return view


def build_confirmation_card(source_id: int, locale: str, *, translatable: bool) -> BaseView:
    """The reply under a relayed copy, in the following server's language."""
    view = BaseView()
    container = ui.Container(accent_colour=COLORS["success"])
    container.add_item(ui.TextDisplay(
        f"### {VERIFIED} {t('announcement_verification.confirmation.title', locale=locale)}"))
    container.add_item(ui.TextDisplay(
        t("announcement_verification.confirmation.description", locale=locale)))
    if translatable:
        row = ui.ActionRow()
        row.add_item(TranslateAnnouncementButton(source_id, label=t(
            "announcement_verification.confirmation.translate", locale=locale)))
        container.add_item(row)
    view.add_item(container)
    return view


def _notice(text: str, *, accent: int = COLORS["primary"]) -> ui.LayoutView:
    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=accent)
    container.add_item(ui.TextDisplay(text))
    view.add_item(container)
    return view


# --------------------------------------------------------------------------- #
# Buttons (DynamicItems, persistent)
# --------------------------------------------------------------------------- #

class VerifyAnnouncementButton(
    ui.DynamicItem[ui.Button],
    template=rf"{_VERIFY_CID}:(?P<mid>\d{{15,25}})",
):
    """Authenticate one announcement. Auth: Moddy team Managers and Developers."""

    def __init__(self, message_id: int, label: str = "Authenticate"):
        super().__init__(ui.Button(
            label=label[:80],
            style=discord.ButtonStyle.success,
            emoji=discord.PartialEmoji.from_str(VERIFIED),
            custom_id=f"{_VERIFY_CID}:{message_id}",
        ))
        self.message_id = message_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["mid"]))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        from utils.components_v2 import create_error_message

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)

        if not await can_verify(bot, interaction.user.id):
            await interaction.response.send_message(
                view=create_error_message(
                    t("announcement_verification.error.forbidden_title", locale=locale),
                    t("announcement_verification.error.forbidden", locale=locale)),
                ephemeral=True)
            return

        # Translating takes a few DeepL calls: acknowledge first.
        await interaction.response.defer()

        try:
            source = await interaction.channel.fetch_message(self.message_id)
        except discord.NotFound:
            await interaction.followup.send(
                view=create_error_message(
                    t("announcement_verification.error.title", locale=locale),
                    t("announcement_verification.error.deleted", locale=locale)),
                ephemeral=True)
            return

        translated = await authenticate(bot, source, verified_by=interaction.user.id)

        card_locale = await _server_locale(bot, interaction.guild)
        await interaction.edit_original_response(
            view=build_verified_card(interaction.user.id, card_locale, translated=translated),
            allowed_mentions=NO_MENTIONS)

        cog = bot.get_cog("AnnouncementVerification")
        if cog is not None:
            cog.spawn_backlog(source.id)


class TranslateAnnouncementButton(
    ui.DynamicItem[ui.Button],
    template=rf"{_TRANSLATE_CID}:(?P<mid>\d{{15,25}})",
):
    """Show an authenticated announcement in the reader's language. Auth: public."""

    def __init__(self, message_id: int, label: str = "Translate"):
        super().__init__(ui.Button(
            label=label[:80],
            style=discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(TRANSLATE),
            custom_id=f"{_TRANSLATE_CID}:{message_id}",
        ))
        self.message_id = message_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["mid"]))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        from utils.components_v2 import create_error_message

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        # Public button in thousands of servers: ack first, the read can wait.
        await interaction.response.defer(ephemeral=True, thinking=False)

        row = None
        if getattr(bot, "db", None):
            row = await bot.db.get_announcement_translations(self.message_id)
        code = language_code(locale)

        if row and row.get("source_lang") == code:
            await interaction.followup.send(view=_notice(
                t("announcement_verification.translate.same_language", locale=locale)),
                ephemeral=True)
            return

        text = (row or {}).get("translations", {}).get(code)
        if not text:
            await interaction.followup.send(
                view=create_error_message(
                    t("announcement_translation.error.title", locale=locale),
                    t("announcement_translation.error.missing", locale=locale)),
                ephemeral=True)
            return

        await interaction.followup.send(view=_notice(text[:_MAX_RENDERED]), ephemeral=True)


class AnnouncementVerificationView(BaseView):
    """Registration shell for the two buttons above (both are DynamicItems)."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth: the verify button re-checks Manager/Dev on every click; the
        translate button is public (it only shows a text the reader can see)."""
        bot.add_dynamic_items(VerifyAnnouncementButton, TranslateAnnouncementButton)


# --------------------------------------------------------------------------- #
# Authentication and replies
# --------------------------------------------------------------------------- #

async def _server_locale(bot, guild) -> str:
    from utils.guild_language import guild_locale
    return await guild_locale(bot, guild)


async def authenticate(bot, source: discord.Message, *, verified_by: int) -> bool:
    """Record ``source`` as authentic and store its translations.

    Returns whether translations are available for its relayed copies.
    """
    content = (source.content or "").strip()
    translated = False
    gateway = getattr(bot, "gateway", None)
    if content and gateway and gateway.deepl_available():
        text = sanitize_mentions(content, source.guild)[:_MAX_SOURCE]
        translations, source_code = await AnnouncementTranslation(bot)._translate_all(
            text, user_id=verified_by)
        if translations:
            await bot.db.save_announcement_translations(
                source.id, guild_id=source.guild.id, channel_id=source.channel.id,
                source_lang=source_code, translations=translations)
            translated = True
    elif content:
        logger.warning("DeepL unavailable — announcement %s authenticated untranslated",
                       source.id)

    await bot.db.save_announcement_verification(
        source.id, guild_id=source.guild.id, channel_id=source.channel.id,
        content_hash=content_hash(source.content), verified_by=verified_by)
    logger.info("Announcement %s authenticated by %s", source.id, verified_by)
    return translated


async def answer_copy(bot, channel, message_id: int, source_id: int) -> bool:
    """Reply to one relayed copy with the confirmation card."""
    row = await bot.db.get_announcement_translations(source_id)
    translatable = bool(row and row.get("translations"))
    locale = await _server_locale(bot, getattr(channel, "guild", None))
    view = build_confirmation_card(source_id, locale, translatable=translatable)
    reference = discord.MessageReference(
        message_id=message_id, channel_id=channel.id,
        guild_id=getattr(getattr(channel, "guild", None), "id", None),
        fail_if_not_exists=False)
    try:
        await channel.send(view=view, reference=reference, allowed_mentions=NO_MENTIONS)
        return True
    except discord.HTTPException as exc:
        # Missing permissions in a server's updates channel is common and final.
        logger.info("Confirmation under copy %s (channel %s) failed: %s",
                    message_id, channel.id, exc)
        return False
    finally:
        await bot.db.mark_announcement_crosspost_answered(message_id)


# --------------------------------------------------------------------------- #
# Cog
# --------------------------------------------------------------------------- #

class AnnouncementVerification(commands.Cog):
    """Authenticate support-server announcements and confirm their relayed copies."""

    def __init__(self, bot):
        self.bot = bot
        self._backlogs: set = set()

    def _is_source_channel(self, guild_id: Optional[int], channel_id: int) -> bool:
        return guild_id == MODDY_TEAM_GUILD_ID and channel_id in ANNOUNCEMENT_VERIFICATION_CHANNEL_IDS

    def spawn_backlog(self, source_id: int) -> None:
        task = asyncio.create_task(self.answer_backlog(source_id))
        self._backlogs.add(task)
        task.add_done_callback(self._backlogs.discard)

    async def answer_backlog(self, source_id: int) -> int:
        """Answer the copies that arrived before the announcement was authenticated."""
        try:
            rows = await self.bot.db.get_unanswered_announcement_crossposts(source_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("Reading copies of announcement %s failed: %s", source_id, exc)
            return 0
        answered = 0
        for row in rows:
            channel = self.bot.get_channel(row["channel_id"])
            if channel is None:
                await self.bot.db.mark_announcement_crosspost_answered(row["message_id"])
                continue
            if await answer_copy(self.bot, channel, row["message_id"], source_id):
                answered += 1
            await asyncio.sleep(_BACKLOG_PAUSE)
        if rows:
            logger.info("Announcement %s: %s/%s waiting copies answered",
                        source_id, answered, len(rows))
        return answered

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not getattr(self.bot, "db", None):
            return
        source_id = relayed_source_id(message)
        if source_id is not None:
            await self._on_copy(message, source_id)
            return
        if not message.guild or not self._is_source_channel(message.guild.id, message.channel.id):
            return
        # Moddy's own cards land in this channel too; never answer them.
        if self.bot.user and message.author.id == self.bot.user.id:
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        await self._post_verify_card(message)

    async def _post_verify_card(self, message: discord.Message, *, edited: bool = False) -> None:
        locale = await _server_locale(self.bot, message.guild)
        try:
            await message.reply(view=build_verify_card(message.id, locale, edited=edited),
                                mention_author=False, allowed_mentions=NO_MENTIONS)
        except discord.HTTPException as exc:
            logger.error("Posting the verify card for %s failed: %s", message.id, exc)

    async def _on_copy(self, message: discord.Message, source_id: int) -> None:
        inserted = await self.bot.db.record_announcement_crosspost(
            message.id, source_message_id=source_id,
            guild_id=message.guild.id if message.guild else 0,
            channel_id=message.channel.id)
        if not inserted:
            return
        verification = await self.bot.db.get_announcement_verification(source_id)
        if verification is None:
            return  # answered by the backlog once a Manager/Dev authenticates it
        if content_hash(message.content) != verification["content_hash"]:
            logger.warning("Copy %s of announcement %s differs from the authenticated text",
                           message.id, source_id)
            return
        await answer_copy(self.bot, message.channel, message.id, source_id)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        """An edited announcement loses its authentication and is offered again."""
        if not getattr(self.bot, "db", None):
            return
        if not self._is_source_channel(payload.guild_id, payload.channel_id):
            return
        if "content" not in payload.data:
            return  # embed unfurl or flag change, not a text edit
        verification = await self.bot.db.get_announcement_verification(payload.message_id)
        if verification is None:
            return
        if content_hash(payload.data.get("content")) == verification["content_hash"]:
            return
        await self.bot.db.delete_announcement_verification(payload.message_id)
        logger.warning("Announcement %s edited after authentication — authentication withdrawn",
                       payload.message_id)
        message = getattr(payload, "message", None) or payload.cached_message
        if message is None:
            channel = self.bot.get_channel(payload.channel_id)
            try:
                message = await channel.fetch_message(payload.message_id)
            except (discord.HTTPException, AttributeError):
                return
        await self._post_verify_card(message, edited=True)


async def setup(bot):
    await bot.add_cog(AnnouncementVerification(bot))
