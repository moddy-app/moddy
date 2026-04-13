"""
Commande /subscription — affiche le statut Premium de l'utilisateur.
Les données sont lues directement depuis la base de données partagée.
Les détails complets (factures, période de renouvellement) sont sur le dashboard.
"""

import discord
from discord import app_commands, ui
from discord.ext import commands
import logging

from cogs.error_handler import BaseView
from utils.i18n import t
from utils.emojis import PREMIUM, WARNING, GREEN_STATUS, RED_STATUS, INFO, ERROR

logger = logging.getLogger('moddy.cogs.subscription')


class SubscriptionView(BaseView):
    """Vue Components V2 pour le statut d'abonnement."""

    def __init__(self, bot, user_id: int, is_premium: bool, stripe_customer_id: str | None):
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = user_id
        self.is_premium = is_premium
        self.stripe_customer_id = stripe_customer_id
        self._build_view()

    def _build_view(self):
        self.clear_items()
        container = ui.Container()

        container.add_item(ui.TextDisplay(f"### {PREMIUM} Your Subscription"))

        if self.is_premium:
            container.add_item(ui.TextDisplay(
                f"{GREEN_STATUS} **Active Subscription**\n"
                "-# Your account has an active Moddy Premium subscription."
            ))
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(ui.TextDisplay(
                "For billing details, invoices and renewal date, visit your dashboard."
            ))
        else:
            container.add_item(ui.TextDisplay(
                f"{RED_STATUS} **No Active Subscription**\n"
                "-# You don't have an active Moddy Premium subscription."
            ))
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(ui.TextDisplay(
                "**Subscribe to Moddy Premium** to unlock exclusive features!\n"
                "-# Visit our website to get started."
            ))

        self.add_item(container)

        row = ui.ActionRow()
        row.add_item(ui.Button(
            label="Dashboard",
            url="https://moddy.app/dashboard",
            style=discord.ButtonStyle.link,
        ))
        self.add_item(row)


class Subscription(commands.Cog):
    """Commandes liées aux abonnements Premium."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="subscription",
        description="View your Moddy Premium subscription status"
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def subscription(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        is_premium = False
        stripe_customer_id = None

        if self.bot.db:
            try:
                is_premium = await self.bot.db.has_attribute('user', interaction.user.id, 'PREMIUM')
                user_data = await self.bot.db.get_user(interaction.user.id)
                stripe_customer_id = user_data.get('stripe_customer_id') if user_data else None
            except Exception as e:
                logger.error(f"DB error for user {interaction.user.id}: {e}", exc_info=True)
                await interaction.followup.send(
                    view=_error_view(),
                    ephemeral=True,
                )
                return

        view = SubscriptionView(self.bot, interaction.user.id, is_premium, stripe_customer_id)
        await interaction.followup.send(view=view, ephemeral=True)
        logger.info(f"Subscription status shown for user {interaction.user.id} (premium={is_premium})")


def _error_view():
    """Vue d'erreur simple."""
    from utils.components_v2 import create_error_message
    return create_error_message(
        "Error",
        "Unable to retrieve your subscription status. Please try again later.",
    )


async def setup(bot):
    await bot.add_cog(Subscription(bot))
    logger.info("Subscription cog loaded")
