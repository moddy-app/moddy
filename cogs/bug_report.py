"""
/bug-report — tell the Moddy team something is broken.

Moddy is in beta: the fastest way to hear about a bug is to let anyone report
one from wherever they hit it, in two fields, without leaving Discord. The
report lands as a card in the team's bug channel (``MODDY_BUG_REPORT_CHANNEL_ID``)
where a staffer can claim it, answer it, and close it — the answer reaches the
reporter as a DM through the notification system, and they can answer back.

The command is global (usable in servers and in DMs) precisely because a bug is
often "Moddy did not answer me anywhere".

See docs/SUPPORT_REQUESTS.md.
"""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands, ui
from discord.ext import commands

from cogs.error_handler import BaseModal
from db.repositories.support_requests import KIND_BUG
from utils.components_v2 import create_error_message

from utils.i18n import i18n, t
from utils.support_request_views import build_receipt, shorten

logger = logging.getLogger("moddy.cogs.bug_report")


class BugReportModal(BaseModal):
    """The report itself — Modal V2 (docs/MODALS_V2.md).

    Three fields is the most a user will fill in when annoyed: what happened,
    how to reproduce it, and where. ``where`` is prefilled from the interaction
    when there is a server to name, so the common case is two fields.
    """

    def __init__(self, *, locale: str = "en-US", guild: Optional[discord.Guild] = None):
        super().__init__(title=shorten(t("support.bug.modal.title", locale=locale), 45))
        self.locale = locale
        self.guild = guild

        self.add_item(ui.TextDisplay(t("support.bug.modal.intro", locale=locale)))

        self.summary = ui.Label(
            text=shorten(t("support.bug.modal.summary.label", locale=locale), 45),
            description=shorten(
                t("support.bug.modal.summary.description", locale=locale), 100),
            component=ui.TextInput(
                style=discord.TextStyle.short, max_length=150, required=True,
                placeholder=shorten(
                    t("support.bug.modal.summary.placeholder", locale=locale), 100),
            ),
        )
        self.description = ui.Label(
            text=shorten(t("support.bug.modal.description.label", locale=locale), 45),
            description=shorten(
                t("support.bug.modal.description.description", locale=locale), 100),
            component=ui.TextInput(
                style=discord.TextStyle.paragraph, max_length=2000, required=True,
                placeholder=shorten(
                    t("support.bug.modal.description.placeholder", locale=locale), 100),
            ),
        )
        self.steps = ui.Label(
            text=shorten(t("support.bug.modal.steps.label", locale=locale), 45),
            description=shorten(
                t("support.bug.modal.steps.description", locale=locale), 100),
            component=ui.TextInput(
                style=discord.TextStyle.paragraph, max_length=1000, required=False,
                placeholder=shorten(
                    t("support.bug.modal.steps.placeholder", locale=locale), 100),
            ),
        )
        self.add_item(self.summary)
        self.add_item(self.description)
        self.add_item(self.steps)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        service = getattr(interaction.client, "support_requests", None)
        if service is None:
            await interaction.followup.send(view=create_error_message(
                t("support.errors.unavailable.title", locale=self.locale),
                t("support.errors.unavailable.description", locale=self.locale),
            ), ephemeral=True)
            return

        request = await service.open_request(
            kind=KIND_BUG,
            user=interaction.user,
            guild=self.guild,
            locale=self.locale,
            subject=(self.summary.component.value or "").strip(),
            body=(self.description.component.value or "").strip(),
            details={
                "steps": (self.steps.component.value or "").strip(),
                # Where the click came from: a bug in a DM and the same bug in
                # a server rarely have the same cause.
                "context": ("dm" if interaction.guild is None
                            else f"guild {interaction.guild_id}"),
            },
        )
        if request is None:
            await interaction.followup.send(view=create_error_message(
                t("support.errors.unavailable.title", locale=self.locale),
                t("support.errors.unavailable.description", locale=self.locale),
            ), ephemeral=True)
            return

        await interaction.followup.send(
            view=build_receipt(kind=KIND_BUG, request=request, locale=self.locale),
            ephemeral=True)


class BugReport(commands.Cog):
    """The /bug-report command."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="bug-report", description="Report a Moddy bug to the team")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def bug_report(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)

        service = getattr(self.bot, "support_requests", None)
        if service is None:
            await interaction.response.send_message(view=create_error_message(
                t("support.errors.unavailable.title", locale=locale),
                t("support.errors.unavailable.description", locale=locale),
            ), ephemeral=True)
            return

        if await service.is_rate_limited(interaction.user.id, KIND_BUG):
            await interaction.response.send_message(view=create_error_message(
                t("support.errors.rate_limited.title", locale=locale),
                t("support.errors.rate_limited.description", locale=locale),
            ), ephemeral=True)
            return

        await interaction.response.send_modal(
            BugReportModal(locale=locale, guild=interaction.guild))


async def setup(bot):
    await bot.add_cog(BugReport(bot))
