"""
Saved Messages Library for Moddy
Allows users to save messages to a personal library via context menu
"""

import re
import discord
from discord import app_commands, ui
from discord.ext import commands
from discord.ui import Container, TextDisplay, Separator
from discord import SeparatorSpacing
from cogs.error_handler import BaseModal, BaseView
from typing import Optional, List, Dict, Any
from datetime import datetime
import logging
import json
import io

from utils.components_v2 import create_error_message
from utils.i18n import i18n, t
from utils.interaction_response import safe_defer
from utils.incognito import get_incognito_setting

logger = logging.getLogger('moddy.saved_messages')

# --------------------------------------------------------------------------- #
# custom_id templates
#
#   moddy:svm:manage:<action>:<owner_id>:<page>                 (list screen)
#   moddy:svm:manage:<action>:<owner_id>:<saved_id>:<page>      (detail screen)
#
# `page` is always the page the list should show if the user backs out to it
# — the target for prev/next is baked directly into the button (no reliance
# on self.page from a live view). `owner_id` is re-checked on every click.
# \d{1,20} (not a tighter snowflake range): a bare shell built with
# owner_id=0 must still match its own template (see
# docs/PERSISTENT_VIEWS.md Migration log, Step 6).
# --------------------------------------------------------------------------- #

_CID_LIST_TEMPLATE = r"moddy:svm:manage:(?P<action>view|prev|next|pageinfo):(?P<owner>\d{1,20}):(?P<page>\d{1,6})"
_CID_DETAIL_TEMPLATE = (
    r"moddy:svm:manage:(?P<action>back|edit_note|export|delete):"
    r"(?P<owner>\d{1,20}):(?P<msgid>\d{1,10}):(?P<page>\d{1,6})"
)


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


async def _reject_if_not_owner(interaction: discord.Interaction, owner_id: int) -> bool:
    """Return True (and answer ephemerally) if the clicker is not the owner."""
    if interaction.user.id == owner_id:
        return False
    locale = i18n.get_user_locale(interaction)
    await interaction.response.send_message(
        view=create_error_message(
            t("errors.not_your_message.title", locale=locale),
            t("errors.not_your_message.description", locale=locale),
        ),
        ephemeral=True,
    )
    return True


def message_to_raw_data(message: discord.Message) -> Dict:
    """Convertit un message Discord en données JSON brutes"""
    return {
        "channelId": str(message.channel.id) if message.channel else None,
        "guildId": str(message.guild.id) if message.guild else None,
        "id": str(message.id),
        "createdTimestamp": int(message.created_at.timestamp() * 1000),
        "type": message.type.value,
        "system": message.is_system(),
        "content": message.content,
        "authorId": str(message.author.id),
        "pinned": message.pinned,
        "tts": message.tts,
        "nonce": message.nonce,
        "embeds": [e.to_dict() for e in message.embeds],
        "components": [c.to_dict() for c in message.components] if hasattr(message, 'components') else [],
        "attachments": [
            {
                "id": str(a.id),
                "filename": a.filename,
                "size": a.size,
                "url": a.url,
                "proxyUrl": a.proxy_url,
                "contentType": a.content_type
            } for a in message.attachments
        ],
        "stickers": [
            {
                "id": str(s.id),
                "name": s.name,
                "formatType": s.format.value
            } for s in message.stickers
        ],
        "position": None,
        "roleSubscriptionData": None,
        "resolved": None,
        "editedTimestamp": int(message.edited_at.timestamp() * 1000) if message.edited_at else None,
        "mentions": {
            "everyone": message.mention_everyone,
            "users": [str(u.id) for u in message.mentions],
            "roles": [str(r.id) for r in message.role_mentions],
            "crosspostedChannels": [],
            "repliedUser": str(message.reference.resolved.author.id) if message.reference and hasattr(message.reference, 'resolved') and message.reference.resolved else None,
            "members": None,
            "channels": []
        },
        "webhookId": None,
        "groupActivityApplicationId": None,
        "applicationId": str(message.application_id) if message.application_id else None,
        "activity": None,
        "flags": message.flags.value,
        "reference": {
            "messageId": str(message.reference.message_id),
            "channelId": str(message.reference.channel_id),
            "guildId": str(message.reference.guild_id) if message.reference.guild_id else None
        } if message.reference else None,
        "interactionMetadata": None,
        "interaction": None,
        "poll": None,
        "messageSnapshots": [],
        "call": None,
        "cleanContent": message.clean_content
    }


class AddNoteModal(BaseModal):
    """Modal for adding a note to a saved message"""

    def __init__(self, locale: str, bot, message: discord.Message):
        super().__init__(title=t("commands.saved_messages.modals.add_note_title", locale=locale))
        self.locale = locale
        self.bot = bot
        self.message = message

        self.note_input = ui.TextInput(
            label=t("commands.saved_messages.modals.note_label", locale=locale),
            placeholder=t("commands.saved_messages.modals.note_placeholder", locale=locale),
            style=discord.TextStyle.paragraph,
            max_length=500,
            required=False
        )
        self.add_item(self.note_input)

    async def on_submit(self, interaction: discord.Interaction):
        # Saving the message is a database round-trip: acknowledge first.
        await safe_defer(interaction, ephemeral=True)

        note = self.note_input.value if self.note_input.value else None

        # Préparer les données du message
        attachments = []
        for attachment in self.message.attachments:
            attachments.append({
                'url': attachment.url,
                'filename': attachment.filename,
                'size': attachment.size,
                'content_type': attachment.content_type
            })

        embeds = []
        for embed in self.message.embeds:
            embeds.append(embed.to_dict())

        # Préparer les données brutes du message
        raw_message_data = message_to_raw_data(self.message)

        # Sauvegarder le message - les erreurs imprévues seront gérées par le système global
        saved_id = await self.bot.db.save_message(
            user_id=interaction.user.id,
            message_id=self.message.id,
            channel_id=self.message.channel.id,
            guild_id=self.message.guild.id if self.message.guild else None,
            author_id=self.message.author.id,
            author_username=str(self.message.author),
            content=self.message.content or "",
            attachments=attachments,
            embeds=embeds,
            created_at=self.message.created_at,
            message_url=self.message.jump_url,
            raw_message_data=raw_message_data,
            note=note
        )

        success_msg = t("commands.saved_messages.success.saved", interaction, id=saved_id)
        await interaction.followup.send(success_msg, ephemeral=True)


async def _refresh_library_card(bot, owner_id: int, locale: str,
                                 channel_id: Optional[int], message_id: Optional[int], *,
                                 show_detail: bool = False, detail_id: Optional[int] = None,
                                 page: int = 0):
    """Rebuild the /library card in place from fresh DB state.

    Keyed on (channel_id, message_id) rather than a live view, so it works
    the same whether the card's original view object is still alive or the
    bot restarted in between (docs/PERSISTENT_VIEWS.md Appendix B.1.b).
    """
    if not channel_id or not message_id:
        return
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception:
            return

    if show_detail and detail_id:
        detail_msg = await bot.db.get_saved_message(detail_id, owner_id)
        view = SavedMessagesLibraryView(
            bot, owner_id, [], locale, page=page, show_detail=True, detail_msg=detail_msg,
        )
    else:
        offset = page * 10
        messages = await bot.db.get_saved_messages(owner_id, limit=10, offset=offset)
        total_count = await bot.db.count_saved_messages(owner_id)
        view = SavedMessagesLibraryView(
            bot, owner_id, messages, locale, page=page, total_count=total_count,
        )

    try:
        await channel.get_partial_message(message_id).edit(view=view)
    except Exception:
        pass


class EditNoteModal(BaseModal):
    """Modal for editing a note on a saved message"""

    def __init__(self, locale: str, bot, saved_msg: Dict, owner_id: int,
                 card_channel_id: Optional[int] = None, card_message_id: Optional[int] = None,
                 page: int = 0):
        super().__init__(title=t("commands.saved_messages.modals.edit_note_title", locale=locale))
        self.locale = locale
        self.bot = bot
        self.saved_msg = saved_msg
        self.owner_id = owner_id
        self.card_channel_id = card_channel_id
        self.card_message_id = card_message_id
        self.page = page

        self.note_input = ui.TextInput(
            label=t("commands.saved_messages.modals.note_label", locale=locale),
            default=saved_msg.get('note', '') or '',
            style=discord.TextStyle.paragraph,
            max_length=500,
            required=False
        )
        self.add_item(self.note_input)

    async def on_submit(self, interaction: discord.Interaction):
        # The note update is a database round-trip: acknowledge first.
        await safe_defer(interaction, ephemeral=True)

        note = self.note_input.value if self.note_input.value else None

        # Les erreurs imprévues seront gérées par le système global
        await self.bot.db.update_saved_message_note(
            self.saved_msg['id'],
            interaction.user.id,
            note
        )

        success_msg = t("commands.saved_messages.success.note_updated", interaction)
        await interaction.followup.send(success_msg, ephemeral=True)

        await _refresh_library_card(
            self.bot, self.owner_id, self.locale, self.card_channel_id, self.card_message_id,
            show_detail=True, detail_id=self.saved_msg['id'], page=self.page,
        )


class ViewMessageModal(BaseModal):
    """Modal for entering a message ID to view"""

    def __init__(self, locale: str, bot, owner_id: int,
                 card_channel_id: Optional[int] = None, card_message_id: Optional[int] = None,
                 page: int = 0):
        super().__init__(title=t("commands.saved_messages.modals.view_title", locale=locale))
        self.locale = locale
        self.bot = bot
        self.owner_id = owner_id
        self.card_channel_id = card_channel_id
        self.card_message_id = card_message_id
        self.page = page

        self.id_input = ui.TextInput(
            label=t("commands.saved_messages.modals.id_label", locale=locale),
            placeholder="1, 2, 3...",
            style=discord.TextStyle.short,
            max_length=10,
            required=True
        )
        self.add_item(self.id_input)

    async def on_submit(self, interaction: discord.Interaction):
        # The lookup below is a database round-trip: acknowledge first.
        await safe_defer(interaction, ephemeral=True)

        # Gestion de l'erreur attendue (ID invalide)
        try:
            msg_id = int(self.id_input.value.strip().replace('#', ''))
        except ValueError:
            error_msg = t("commands.saved_messages.errors.invalid_id", interaction)
            await interaction.followup.send(error_msg, ephemeral=True)
            return

        # Les erreurs imprévues seront gérées par le système global
        saved_msg = await self.bot.db.get_saved_message(msg_id, interaction.user.id)

        if not saved_msg:
            error_msg = t("commands.saved_messages.errors.not_found", interaction)
            await interaction.followup.send(error_msg, ephemeral=True)
            return

        await _refresh_library_card(
            self.bot, self.owner_id, self.locale, self.card_channel_id, self.card_message_id,
            show_detail=True, detail_id=msg_id, page=self.page,
        )


class SavedMessagesLibraryView(BaseView):
    """/library card. Persistent: yes (via DynamicItems).

    Auth: owner only — enforced per-item by the encoded owner_id, NOT by
    interaction_check (which cannot work on a restarted shell).
    """

    __persistent__ = True

    def __init__(self, bot=None, user_id: Optional[int] = None, messages: Optional[List[Dict]] = None,
                 locale: str = "en-US", page: int = 0, total_count: int = 0, show_detail: bool = False,
                 detail_msg: Optional[Dict] = None):
        super().__init__()  # timeout=None
        self.bot = bot
        self.user_id = user_id
        self.messages = messages or []
        self.locale = locale
        self.page = page
        self.total_count = total_count
        self.show_detail = show_detail
        self.detail_msg = detail_msg
        self._build_view()

    def _build_view(self):
        self.clear_items()

        container = Container()

        if self.show_detail and self.detail_msg:
            # Vue détaillée d'un message
            container.add_item(TextDisplay(t('commands.saved_messages.detail.title', locale=self.locale)))

            # === MODDY INFO ===

            # MODDY ID
            moddy_id_label = t('commands.saved_messages.detail.moddy_id', locale=self.locale)
            container.add_item(TextDisplay(f"> **{moddy_id_label}** : `{self.detail_msg['id']}`"))

            # Date d'enregistrement
            saved_date_label = t('commands.saved_messages.detail.saved_date', locale=self.locale)
            saved_ts = f"<t:{int(self.detail_msg['saved_at'].timestamp())}:F>"
            container.add_item(TextDisplay(f"> **{saved_date_label}** : {saved_ts}"))

            # Note
            note_label = t('commands.saved_messages.detail.note', locale=self.locale)
            none_text = t('commands.saved_messages.detail.none', locale=self.locale)
            if self.detail_msg.get('note'):
                container.add_item(TextDisplay(f"> **{note_label}** : {self.detail_msg['note']}"))
            else:
                container.add_item(TextDisplay(f"> **{note_label}** : {none_text}"))

            # Séparateur
            container.add_item(Separator(spacing=SeparatorSpacing.small))

            # === DISCORD MESSAGE INFO ===

            # Message ID
            message_id_label = t('commands.saved_messages.detail.message_id', locale=self.locale)
            container.add_item(TextDisplay(f"> **{message_id_label}** : `{self.detail_msg['message_id']}`"))

            # Channel ID
            if self.detail_msg.get('channel_id'):
                channel_id_label = t('commands.saved_messages.detail.channel_id', locale=self.locale)
                container.add_item(TextDisplay(f"> **{channel_id_label}** : `{self.detail_msg['channel_id']}`"))

            # Guild ID (Server ID)
            if self.detail_msg.get('guild_id'):
                guild_id_label = t('commands.saved_messages.detail.guild_id', locale=self.locale)
                container.add_item(TextDisplay(f"> **{guild_id_label}** : `{self.detail_msg['guild_id']}`"))

            # Author ID
            author_id_label = t('commands.saved_messages.detail.author_id', locale=self.locale)
            container.add_item(TextDisplay(f"> **{author_id_label}** : `{self.detail_msg['author_id']}`"))

            # Username
            if self.detail_msg.get('author_username'):
                username_label = t('commands.saved_messages.detail.username', locale=self.locale)
                container.add_item(TextDisplay(f"> **{username_label}** : `{self.detail_msg['author_username']}`"))

            # Author Mention
            author_mention_label = t('commands.saved_messages.detail.author_mention', locale=self.locale)
            container.add_item(TextDisplay(f"> **{author_mention_label}** : <@{self.detail_msg['author_id']}>"))

            # Message Send Date
            send_date_label = t('commands.saved_messages.detail.send_date', locale=self.locale)
            created_ts = f"<t:{int(self.detail_msg['created_at'].timestamp())}:F>"
            container.add_item(TextDisplay(f"> **{send_date_label}** : {created_ts}"))

            # Content
            content_label = t('commands.saved_messages.detail.content', locale=self.locale)
            if self.detail_msg['content']:
                content_preview = self.detail_msg['content'][:1800]
                if len(self.detail_msg['content']) > 1800:
                    content_preview += "..."
                container.add_item(TextDisplay(f"> **{content_label}** :\n```\n{content_preview}\n```"))
            else:
                container.add_item(TextDisplay(f"> **{content_label}** : {none_text}"))

            # Attachments
            attachments_label = t('commands.saved_messages.detail.attachments', locale=self.locale)
            attach_count = len(self.detail_msg.get('attachments', []))
            if attach_count > 0:
                attachments_info = []
                for i, attach in enumerate(self.detail_msg.get('attachments', [])[:5], 1):
                    attachments_info.append(f"  {i}. {attach.get('filename', 'unknown')} ({attach.get('content_type', 'unknown')})")
                attachments_text = "\n".join(attachments_info)
                container.add_item(TextDisplay(f"> **{attachments_label}** : {attach_count}\n{attachments_text}"))
            else:
                container.add_item(TextDisplay(f"> **{attachments_label}** : 0"))

            # Embeds
            embeds_label = t('commands.saved_messages.detail.embeds', locale=self.locale)
            embeds_count = len(self.detail_msg.get('embeds', []))
            if embeds_count > 0:
                container.add_item(TextDisplay(f"> **{embeds_label}** : {embeds_count}"))
            else:
                container.add_item(TextDisplay(f"> **{embeds_label}** : 0"))

            # Message URL
            if self.detail_msg.get('message_url'):
                message_url_label = t('commands.saved_messages.detail.message_url', locale=self.locale)
                jump_to_msg = t('commands.saved_messages.detail.jump_to_message', locale=self.locale)
                container.add_item(TextDisplay(f"> **{message_url_label}** : [{jump_to_msg}]({self.detail_msg['message_url']})"))

            self.add_item(container)

            # Boutons d'action
            btn_row = ui.ActionRow()
            detail_id = self.detail_msg['id']
            btn_row.add_item(SavedMessagesDetailButton("back", self.user_id or 0, detail_id, self.page, locale=self.locale))
            btn_row.add_item(SavedMessagesDetailButton("edit_note", self.user_id or 0, detail_id, self.page, locale=self.locale))
            btn_row.add_item(SavedMessagesDetailButton("export", self.user_id or 0, detail_id, self.page, locale=self.locale))
            btn_row.add_item(SavedMessagesDetailButton("delete", self.user_id or 0, detail_id, self.page, locale=self.locale))
            container.add_item(btn_row)
        else:
            # Liste des messages
            title = f"{t('commands.saved_messages.library.title', locale=self.locale, count=self.total_count)}"
            container.add_item(TextDisplay(title))

            if not self.messages:
                container.add_item(TextDisplay(t("commands.saved_messages.library.empty", locale=self.locale)))
            else:
                # Afficher les messages
                for msg in self.messages[:10]:
                    saved_ts = f"<t:{int(msg['saved_at'].timestamp())}:R>"

                    # Format: **#ID** • <@author_id> • Saved {relative_time}
                    msg_line = f"**#{msg['id']}** • <@{msg['author_id']}> • {saved_ts}"

                    # Ajouter la note si présente
                    if msg.get('note'):
                        note_preview = msg['note'][:80]
                        if len(msg['note']) > 80:
                            note_preview += "..."
                        msg_line += f"\n-# <:note:1519790932663468184> {note_preview}"
                    else:
                        msg_line += f"\n-# ID Discord: `{msg['message_id']}`"

                    container.add_item(TextDisplay(msg_line))

                container.add_item(Separator(spacing=SeparatorSpacing.small))

                # Bouton pour sélectionner un message
                view_row = ui.ActionRow()
                view_row.add_item(SavedMessagesListButton("view", self.user_id or 0, self.page, locale=self.locale))
                container.add_item(view_row)

                # Navigation buttons
                total_pages = (self.total_count + 9) // 10
                if total_pages > 1:
                    nav_row = ui.ActionRow()
                    nav_row.add_item(SavedMessagesListButton(
                        "prev", self.user_id or 0, max(self.page - 1, 0), locale=self.locale,
                        disabled=self.page == 0,
                    ))
                    nav_row.add_item(SavedMessagesListButton(
                        "pageinfo", self.user_id or 0, self.page, locale=self.locale,
                        label=t("commands.saved_messages.library.page_label", locale=self.locale,
                                page=self.page + 1, total=total_pages),
                        disabled=True,
                    ))
                    nav_row.add_item(SavedMessagesListButton(
                        "next", self.user_id or 0, self.page + 1, locale=self.locale,
                        disabled=(self.page + 1) * 10 >= self.total_count,
                    ))
                    container.add_item(nav_row)

            self.add_item(container)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: owner only — owner_id is encoded in each item's
        custom_id and compared to interaction.user.id on click."""
        bot.add_dynamic_items(SavedMessagesListButton, SavedMessagesDetailButton)


class SavedMessagesListButton(ui.DynamicItem[ui.Button], template=_CID_LIST_TEMPLATE):
    """A button on the /library list screen. Auth: owner only."""

    _STYLE = {
        "view":     discord.ButtonStyle.primary,
        "prev":     discord.ButtonStyle.secondary,
        "next":     discord.ButtonStyle.secondary,
        "pageinfo": discord.ButtonStyle.secondary,
    }
    _EMOJI = {
        "view": "<:search:1519790418290675822>",
        "prev": "<:back:1519795556665397431>",
        "next": "<:next:1519791619526754354>",
    }
    _LABEL_KEY = {
        "view": "commands.saved_messages.buttons.view_message",
    }

    def __init__(self, action: str, owner_id: int, page: int, *,
                 locale: str = "en-US", label: Optional[str] = None, disabled: bool = False):
        kwargs = {
            "style": self._STYLE[action],
            "custom_id": f"moddy:svm:manage:{action}:{owner_id}:{page}",
            "disabled": disabled,
        }
        if action in self._EMOJI:
            kwargs["emoji"] = discord.PartialEmoji.from_str(self._EMOJI[action])
        if label is not None:
            kwargs["label"] = label
        elif action in self._LABEL_KEY:
            kwargs["label"] = t(self._LABEL_KEY[action], locale=locale)[:80]
        super().__init__(ui.Button(**kwargs))
        self.action = action
        self.owner_id = owner_id
        self.page = page

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match: re.Match):
        return cls(match["action"], int(match["owner"]), int(match["page"]),
                   locale=i18n.get_user_locale(interaction))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if await _reject_if_not_owner(interaction, self.owner_id):
            return

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        owner_id = interaction.user.id
        channel_id = interaction.channel_id
        message_id = interaction.message.id if interaction.message else None

        if self.action == "pageinfo":
            return

        if self.action == "view":
            await interaction.response.send_modal(
                ViewMessageModal(locale, bot, owner_id,
                                  card_channel_id=channel_id, card_message_id=message_id, page=self.page)
            )
            return

        # prev / next: self.page already IS the target page (baked in at render time).
        offset = self.page * 10
        messages = await bot.db.get_saved_messages(owner_id, limit=10, offset=offset)
        total_count = await bot.db.count_saved_messages(owner_id)
        view = SavedMessagesLibraryView(bot, owner_id, messages, locale, page=self.page, total_count=total_count)
        await interaction.response.edit_message(view=view)


class SavedMessagesDetailButton(ui.DynamicItem[ui.Button], template=_CID_DETAIL_TEMPLATE):
    """A button on the /library detail screen. Auth: owner only."""

    _STYLE = {
        "back":      discord.ButtonStyle.secondary,
        "edit_note": discord.ButtonStyle.primary,
        "export":    discord.ButtonStyle.secondary,
        "delete":    discord.ButtonStyle.danger,
    }
    _EMOJI = {
        "back":      "<:back:1519795556665397431>",
        "edit_note": "<:edit:1519795936568676383>",
        "export":    "<:data_object:1519795407453159474>",
        "delete":    "<:delete:1519795753164210447>",
    }
    _LABEL_KEY = {
        "back":      "commands.saved_messages.buttons.back",
        "edit_note": "commands.saved_messages.buttons.edit_note",
        "export":    "commands.saved_messages.buttons.export_json",
        "delete":    "commands.saved_messages.buttons.delete",
    }

    def __init__(self, action: str, owner_id: int, saved_id: int, page: int, *, locale: str = "en-US"):
        super().__init__(
            ui.Button(
                label=t(self._LABEL_KEY[action], locale=locale)[:80],
                style=self._STYLE[action],
                emoji=discord.PartialEmoji.from_str(self._EMOJI[action]),
                custom_id=f"moddy:svm:manage:{action}:{owner_id}:{saved_id}:{page}",
            )
        )
        self.action = action
        self.owner_id = owner_id
        self.saved_id = saved_id
        self.page = page

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match: re.Match):
        return cls(match["action"], int(match["owner"]), int(match["msgid"]), int(match["page"]),
                   locale=i18n.get_user_locale(interaction))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if await _reject_if_not_owner(interaction, self.owner_id):
            return

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        owner_id = interaction.user.id
        channel_id = interaction.channel_id
        message_id = interaction.message.id if interaction.message else None

        if self.action == "back":
            offset = self.page * 10
            messages = await bot.db.get_saved_messages(owner_id, limit=10, offset=offset)
            total_count = await bot.db.count_saved_messages(owner_id)
            view = SavedMessagesLibraryView(bot, owner_id, messages, locale, page=self.page, total_count=total_count)
            await interaction.response.edit_message(view=view)
            return

        detail_msg = await bot.db.get_saved_message(self.saved_id, owner_id)
        if not detail_msg:
            return

        if self.action == "edit_note":
            await interaction.response.send_modal(
                EditNoteModal(locale, bot, detail_msg, owner_id,
                              card_channel_id=channel_id, card_message_id=message_id, page=self.page)
            )
            return

        if self.action == "export":
            if detail_msg.get('raw_message_data'):
                json_data = json.dumps(detail_msg['raw_message_data'], indent=2, ensure_ascii=False)
                file = discord.File(
                    io.BytesIO(json_data.encode('utf-8')),
                    filename=f"message_{detail_msg['id']}_raw_data.json"
                )
                await interaction.response.send_message(
                    content=t("commands.saved_messages.success.exported", locale=locale),
                    file=file,
                    ephemeral=True,
                )
            return

        if self.action == "delete":
            success = await bot.db.delete_saved_message(detail_msg['id'], owner_id)
            if success:
                await interaction.response.send_message(
                    t("commands.saved_messages.success.deleted", locale=locale),
                    ephemeral=True,
                )
                # Return the card (same message the delete button lived on) to the list.
                await _refresh_library_card(
                    bot, owner_id, locale, channel_id, message_id,
                    show_detail=False, page=self.page,
                )
            else:
                await interaction.response.send_message(t("common.error", locale=locale), ephemeral=True)


class SavedMessages(commands.Cog):
    """Saved messages library system"""

    def __init__(self, bot):
        self.bot = bot

        # Create context menu command
        self.save_message_menu = app_commands.ContextMenu(
            name="Save Message",
            callback=self.save_message_context_menu
        )
        self.bot.tree.add_command(self.save_message_menu)

    async def save_message_context_menu(self, interaction: discord.Interaction, message: discord.Message):
        """Context menu to save a message"""
        locale = str(interaction.locale)

        # Check if user already has too many saved messages
        count = await self.bot.db.count_saved_messages(interaction.user.id)
        if count >= 500:
            error_msg = t("commands.saved_messages.errors.max_messages", interaction)
            await interaction.response.send_message(error_msg, ephemeral=True)
            return

        # Show modal to add optional note
        modal = AddNoteModal(locale, self.bot, message)
        await interaction.response.send_modal(modal)

    @app_commands.command(
        name="library",
        description="View your saved messages library"
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        incognito="Make response visible only to you"
    )
    async def library_command(
        self,
        interaction: discord.Interaction,
        incognito: Optional[bool] = None
    ):
        """View saved messages library"""
        # Handle incognito setting
        if incognito is None and self.bot.db:
            try:
                user_pref = await self.bot.db.get_attribute('user', interaction.user.id, 'DEFAULT_INCOGNITO')
                ephemeral = True if user_pref is None else user_pref
            except:
                ephemeral = True
        else:
            ephemeral = incognito if incognito is not None else True

        # Get saved messages
        messages = await self.bot.db.get_saved_messages(interaction.user.id, limit=10, offset=0)
        total_count = await self.bot.db.count_saved_messages(interaction.user.id)

        # Create view
        view = SavedMessagesLibraryView(
            self.bot,
            interaction.user.id,
            messages,
            i18n.get_user_locale(interaction),
            page=0,
            total_count=total_count,
        )

        await interaction.response.send_message(view=view, ephemeral=ephemeral)

    async def cog_unload(self):
        """Remove context menu when cog is unloaded"""
        self.bot.tree.remove_command(self.save_message_menu.name, type=self.save_message_menu.type)


async def setup(bot):
    await bot.add_cog(SavedMessages(bot))
