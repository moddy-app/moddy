"""
Saved Messages Library for Moddy
Allows users to save messages to a personal library via context menu
"""

import discord
from discord import app_commands, ui
from discord.ext import commands
from discord.ui import LayoutView, Container, TextDisplay, Separator
from discord import SeparatorSpacing
from typing import Optional, List, Dict, Any
from datetime import datetime
import logging
import json
import io

from utils.i18n import t
from utils.incognito import get_incognito_setting

logger = logging.getLogger('moddy.saved_messages')


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


class AddNoteModal(ui.Modal):
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
        await interaction.response.send_message(success_msg, ephemeral=True)


class EditNoteModal(ui.Modal):
    """Modal for editing a note on a saved message"""

    def __init__(self, locale: str, bot, saved_msg: Dict, parent_view=None):
        super().__init__(title=t("commands.saved_messages.modals.edit_note_title", locale=locale))
        self.locale = locale
        self.bot = bot
        self.saved_msg = saved_msg
        self.parent_view = parent_view

        self.note_input = ui.TextInput(
            label=t("commands.saved_messages.modals.note_label", locale=locale),
            default=saved_msg.get('note', '') or '',
            style=discord.TextStyle.paragraph,
            max_length=500,
            required=False
        )
        self.add_item(self.note_input)

    async def on_submit(self, interaction: discord.Interaction):
        note = self.note_input.value if self.note_input.value else None

        # Les erreurs imprévues seront gérées par le système global
        await self.bot.db.update_saved_message_note(
            self.saved_msg['id'],
            interaction.user.id,
            note
        )

        success_msg = t("commands.saved_messages.success.note_updated", interaction)
        await interaction.response.send_message(success_msg, ephemeral=True)

        # Refresh parent view if it exists
        if self.parent_view:
            await self.parent_view.refresh(interaction, show_detail=True, detail_id=self.saved_msg['id'])


class ViewMessageModal(ui.Modal):
    """Modal for entering a message ID to view"""

    def __init__(self, locale: str, parent_view):
        super().__init__(title=t("commands.saved_messages.modals.view_title", locale=locale))
        self.locale = locale
        self.parent_view = parent_view

        self.id_input = ui.TextInput(
            label=t("commands.saved_messages.modals.id_label", locale=locale),
            placeholder="1, 2, 3...",
            style=discord.TextStyle.short,
            max_length=10,
            required=True
        )
        self.add_item(self.id_input)

    async def on_submit(self, interaction: discord.Interaction):
        # Gestion de l'erreur attendue (ID invalide)
        try:
            msg_id = int(self.id_input.value.strip().replace('#', ''))
        except ValueError:
            error_msg = t("commands.saved_messages.errors.invalid_id", interaction)
            await interaction.response.send_message(error_msg, ephemeral=True)
            return

        # Les erreurs imprévues seront gérées par le système global
        saved_msg = await self.parent_view.bot.db.get_saved_message(msg_id, interaction.user.id)

        if not saved_msg:
            error_msg = t("commands.saved_messages.errors.not_found", interaction)
            await interaction.response.send_message(error_msg, ephemeral=True)
            return

        # Refresh parent view to show detail
        await self.parent_view.refresh(interaction, show_detail=True, detail_id=msg_id)


class SavedMessagesLibraryView(LayoutView):
    """Main view for browsing the saved messages library"""

    def __init__(self, bot, user_id: int, messages: List[Dict], locale: str,
                 page: int = 0, total_count: int = 0, show_detail: bool = False,
                 detail_msg: Optional[Dict] = None, original_interaction: discord.Interaction = None):
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = user_id
        self.messages = messages
        self.locale = locale
        self.page = page
        self.total_count = total_count
        self.show_detail = show_detail
        self.detail_msg = detail_msg
        self.original_interaction = original_interaction
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

            # Bouton Back
            back_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str("<:back:1519795556665397431>"),
                label=t("commands.saved_messages.buttons.back", locale=self.locale),
                style=discord.ButtonStyle.secondary,
                custom_id="back_btn"
            )
            back_btn.callback = self.back_callback
            btn_row.add_item(back_btn)

            # Bouton Edit Note
            edit_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str("<:edit:1519795936568676383>"),
                label=t("commands.saved_messages.buttons.edit_note", locale=self.locale),
                style=discord.ButtonStyle.primary,
                custom_id="edit_note_btn"
            )
            edit_btn.callback = self.edit_note_callback
            btn_row.add_item(edit_btn)

            # Bouton Export JSON
            export_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str("<:data_object:1519795407453159474>"),
                label=t("commands.saved_messages.buttons.export_json", locale=self.locale),
                style=discord.ButtonStyle.secondary,
                custom_id="export_json_btn"
            )
            export_btn.callback = self.export_json_callback
            btn_row.add_item(export_btn)

            # Bouton Delete
            delete_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str("<:delete:1519795753164210447>"),
                label=t("commands.saved_messages.buttons.delete", locale=self.locale),
                style=discord.ButtonStyle.danger,
                custom_id="delete_btn"
            )
            delete_btn.callback = self.delete_callback
            btn_row.add_item(delete_btn)

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
                view_btn = ui.Button(
                    emoji=discord.PartialEmoji.from_str("<:search:1519790418290675822>"),
                    label=t("commands.saved_messages.buttons.view_message", locale=self.locale),
                    style=discord.ButtonStyle.primary,
                    custom_id="view_msg_btn"
                )
                view_btn.callback = self.view_message_callback
                view_row.add_item(view_btn)
                container.add_item(view_row)

                # Navigation buttons
                total_pages = (self.total_count + 9) // 10
                if total_pages > 1:
                    nav_row = ui.ActionRow()

                    # Bouton Previous
                    prev_btn = ui.Button(
                        emoji=discord.PartialEmoji.from_str("<:back:1519795556665397431>"),
                        style=discord.ButtonStyle.secondary,
                        disabled=self.page == 0,
                        custom_id="prev_btn"
                    )
                    prev_btn.callback = self.prev_callback
                    nav_row.add_item(prev_btn)

                    # Bouton Page (non cliquable)
                    page_btn = ui.Button(
                        label=t("commands.saved_messages.library.page_label", locale=self.locale,
                               page=self.page + 1, total=total_pages),
                        style=discord.ButtonStyle.secondary,
                        disabled=True,
                        custom_id="page_info"
                    )
                    nav_row.add_item(page_btn)

                    # Bouton Next
                    next_btn = ui.Button(
                        emoji=discord.PartialEmoji.from_str("<:next:1519791619526754354>"),
                        style=discord.ButtonStyle.secondary,
                        disabled=(self.page + 1) * 10 >= self.total_count,
                        custom_id="next_btn"
                    )
                    next_btn.callback = self.next_callback
                    nav_row.add_item(next_btn)

                    container.add_item(nav_row)

            self.add_item(container)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                t("commands.saved_messages.errors.author_only", interaction),
                ephemeral=True
            )
            return False
        return True

    async def view_message_callback(self, interaction: discord.Interaction):
        modal = ViewMessageModal(self.locale, self)
        await interaction.response.send_modal(modal)

    async def prev_callback(self, interaction: discord.Interaction):
        if self.page > 0:
            self.page -= 1
            await self.refresh(interaction)

    async def next_callback(self, interaction: discord.Interaction):
        if (self.page + 1) * 10 < self.total_count:
            self.page += 1
            await self.refresh(interaction)

    async def back_callback(self, interaction: discord.Interaction):
        await self.refresh(interaction, show_detail=False)

    async def edit_note_callback(self, interaction: discord.Interaction):
        if self.detail_msg:
            modal = EditNoteModal(self.locale, self.bot, self.detail_msg, parent_view=self)
            await interaction.response.send_modal(modal)

    async def export_json_callback(self, interaction: discord.Interaction):
        if self.detail_msg and self.detail_msg.get('raw_message_data'):
            # Les erreurs imprévues seront gérées par le système global
            # Créer le fichier JSON
            json_data = json.dumps(self.detail_msg['raw_message_data'], indent=2, ensure_ascii=False)
            file = discord.File(
                io.BytesIO(json_data.encode('utf-8')),
                filename=f"message_{self.detail_msg['id']}_raw_data.json"
            )

            await interaction.response.send_message(
                content=t("commands.saved_messages.success.exported", interaction),
                file=file,
                ephemeral=True
            )

    async def delete_callback(self, interaction: discord.Interaction):
        if self.detail_msg:
            # Les erreurs imprévues seront gérées par le système global
            success = await self.bot.db.delete_saved_message(self.detail_msg['id'], interaction.user.id)
            if success:
                await interaction.response.send_message(
                    t("commands.saved_messages.success.deleted", interaction),
                    ephemeral=True
                )
                # Retour à la liste
                await self.refresh(interaction, show_detail=False)
            else:
                await interaction.response.send_message(
                    t("common.error", interaction),
                    ephemeral=True
                )

    async def refresh(self, interaction: discord.Interaction, show_detail: bool = False, detail_id: Optional[int] = None):
        """Refresh the view with updated data"""
        self.show_detail = show_detail

        if show_detail and detail_id:
            # Charger les détails du message
            self.detail_msg = await self.bot.db.get_saved_message(detail_id, self.user_id)
        else:
            # Recharger la liste
            offset = self.page * 10
            self.messages = await self.bot.db.get_saved_messages(self.user_id, limit=10, offset=offset)
            self.total_count = await self.bot.db.count_saved_messages(self.user_id)
            self.detail_msg = None

        self._build_view()

        # Mettre à jour le message
        if self.original_interaction:
            try:
                await interaction.response.edit_message(view=self)
            except discord.errors.InteractionResponded:
                try:
                    await self.original_interaction.edit_original_response(view=self)
                except Exception:
                    pass


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
            str(interaction.locale),
            page=0,
            total_count=total_count,
            original_interaction=interaction
        )

        await interaction.response.send_message(view=view, ephemeral=ephemeral)

    async def cog_unload(self):
        """Remove context menu when cog is unloaded"""
        self.bot.tree.remove_command(self.save_message_menu.name, type=self.save_message_menu.type)


async def setup(bot):
    await bot.add_cog(SavedMessages(bot))
