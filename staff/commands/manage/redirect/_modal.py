"""Modal V2 for creating / editing a redirect link."""

import random
import string

import discord
from discord import ui

from staff.framework import design
from utils.i18n import t
from cogs.error_handler import BaseModal


def random_path(length: int = 6) -> str:
    return "/" + "".join(random.choices(string.ascii_letters + string.digits, k=length))


class RedirectModal(BaseModal):
    """Create or edit a redirect link (Modal V2)."""

    def __init__(self, bot, locale: str, redirect_id=None, prefill=None):
        super().__init__(title=t("staff.manage.redirect.modal_edit" if redirect_id else "staff.manage.redirect.modal_add", locale=locale)[:45])
        self.bot = bot
        self.locale = locale
        self.redirect_id = redirect_id

        self.domain_input = ui.TextInput(default=(prefill["domain"] if prefill else "moddy.app"), max_length=100)
        self.path_input = ui.TextInput(default=(prefill["path"] if prefill else random_path()), max_length=100)
        self.target_input = ui.TextInput(default=(prefill["target"] if prefill else ""), max_length=500)
        self.description_input = ui.TextInput(default=(prefill["description"] if prefill else ""), required=False, max_length=200)

        self.add_item(ui.Label(text=t("staff.manage.redirect.domain", locale=locale)[:45], component=self.domain_input))
        self.add_item(ui.Label(text=t("staff.manage.redirect.path", locale=locale)[:45], component=self.path_input))
        self.add_item(ui.Label(text=t("staff.manage.redirect.target", locale=locale)[:45], component=self.target_input))
        self.add_item(ui.Label(text=t("staff.manage.redirect.description", locale=locale)[:45], component=self.description_input))

    async def on_submit(self, interaction: discord.Interaction):
        domain = self.domain_input.value.strip()
        path = self.path_input.value.strip()
        target = self.target_input.value.strip()
        description = self.description_input.value.strip()

        if self.redirect_id is not None:
            row = await self.bot.db.update_redirect(self.redirect_id, domain, path, target, description)
            title = t("staff.manage.redirect.updated", locale=self.locale)
        else:
            row = await self.bot.db.add_redirect(domain, path, target, description, interaction.user.id)
            title = t("staff.manage.redirect.added", locale=self.locale)

        await interaction.response.send_message(view=design.success(
            title,
            f"**ID:** `{row['id']}`\n**`{row['domain']}{row['path']}`** → `{row['target']}`\n-# {row['description']}",
        ), ephemeral=True)
