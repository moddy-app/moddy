"""Modal V2 for composing a Moddy notification sent by the team.

One screen writes the message; the command that opened it decides who receives
it. The fields map one-to-one onto
:class:`~notifications.models.NotificationContent`, so the same text renders as
a Discord panel, a mail subject + body, and a dashboard card without anyone
rewriting it per platform.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

import discord
from discord import ui

from cogs.error_handler import BaseModal
from utils.i18n import t


class NotificationComposeModal(BaseModal):
    """Compose a notification. ``on_submit_content`` receives the payload."""

    def __init__(self, *, locale: str,
                 on_content: Callable[[discord.Interaction, Dict[str, Any]], Any]):
        super().__init__(title=t("staff.com.send.modal.title", locale=locale)[:45])
        self.locale = locale
        self.on_content = on_content

        self.title_input = ui.TextInput(
            style=discord.TextStyle.short, max_length=200, required=True,
            placeholder=t("staff.com.send.modal.title_field.placeholder", locale=locale)[:100],
        )
        self.body_input = ui.TextInput(
            style=discord.TextStyle.paragraph, max_length=3000, required=True,
            placeholder=t("staff.com.send.modal.body.placeholder", locale=locale)[:100],
        )
        self.link_label_input = ui.TextInput(
            style=discord.TextStyle.short, max_length=80, required=False,
            placeholder=t("staff.com.send.modal.link_label.placeholder", locale=locale)[:100],
        )
        self.link_url_input = ui.TextInput(
            style=discord.TextStyle.short, max_length=300, required=False,
            placeholder="https://moddy.app/…",
        )

        self.add_item(ui.Label(
            text=t("staff.com.send.modal.title_field.label", locale=locale)[:45],
            component=self.title_input))
        self.add_item(ui.Label(
            text=t("staff.com.send.modal.body.label", locale=locale)[:45],
            description=t("staff.com.send.modal.body.description", locale=locale)[:100],
            component=self.body_input))
        self.add_item(ui.Label(
            text=t("staff.com.send.modal.link_label.label", locale=locale)[:45],
            component=self.link_label_input))
        self.add_item(ui.Label(
            text=t("staff.com.send.modal.link_url.label", locale=locale)[:45],
            description=t("staff.com.send.modal.link_url.description", locale=locale)[:100],
            component=self.link_url_input))

    async def on_submit(self, interaction: discord.Interaction):
        url = (self.link_url_input.value or "").strip()
        links = []
        if url.startswith("https://"):
            links.append({
                "label": (self.link_label_input.value or "").strip()
                         or t("staff.com.send.default_link_label", locale=self.locale),
                "url": url,
            })
        await self.on_content(interaction, {
            "title": (self.title_input.value or "").strip(),
            "body": (self.body_input.value or "").strip(),
            "links": links,
        })
