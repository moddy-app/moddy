"""
Commande /subscription pour afficher les informations d'abonnement Stripe d'un utilisateur.
Utilise l'API interne pour récupérer les données depuis le backend.
"""

import discord
from discord import app_commands, ui
from discord.ext import commands
import logging
from datetime import datetime

from services import get_backend_client, BackendClientError
from cogs.error_handler import BaseView
from utils.i18n import t
from utils.emojis import PREMIUM, WARNING, GREEN_STATUS, RED_STATUS, YELLOW_STATUS, INFO, ERROR

logger = logging.getLogger('moddy.cogs.subscription')


class SubscriptionView(BaseView):
    """Vue pour afficher les informations d'abonnement avec Components V2"""

    def __init__(self, bot, user_id: int, locale: str, subscription_data: dict):
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = user_id
        self.locale = locale
        self.subscription_data = subscription_data
        self._build_view()

    def _build_view(self):
        """Construit l'interface avec les informations d'abonnement"""
        self.clear_items()

        container = ui.Container()

        # Titre avec emoji premium
        container.add_item(ui.TextDisplay(
            f"### {PREMIUM} Your Subscription"
        ))

        if not self.subscription_data.get("has_subscription"):
            # Pas d'abonnement actif
            container.add_item(ui.TextDisplay(
                "You don't have an active subscription yet."
            ))
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(ui.TextDisplay(
                "**Subscribe to Moddy Max** to unlock premium features!\n"
                "-# Visit our website to subscribe and get access to exclusive benefits."
            ))
        else:
            # Abonnement actif - afficher les détails
            sub = self.subscription_data["subscription"]

            # Description
            container.add_item(ui.TextDisplay(
                "Here are your subscription details."
            ))

            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

            # Status avec emoji
            status_emoji = self._get_status_emoji(sub["status"])
            container.add_item(ui.TextDisplay(
                f"**Status**\n"
                f"-# {status_emoji} {sub['status'].capitalize()}"
            ))

            # Type d'abonnement
            subscription_type = "Yearly" if sub["subscription_type"] == "yearly" else "Monthly"
            container.add_item(ui.TextDisplay(
                f"**Plan Type**\n"
                f"-# {subscription_type}"
            ))

            # Prix
            amount_euros = sub["amount"] / 100
            container.add_item(ui.TextDisplay(
                f"**Price**\n"
                f"-# {amount_euros}€ / {sub['subscription_type']}"
            ))

            # Date de renouvellement
            renewal_date = self._format_date(sub["current_period_end"])
            container.add_item(ui.TextDisplay(
                f"**Next Renewal**\n"
                f"-# {renewal_date}"
            ))

            # Si annulation programmée
            if sub.get("cancel_at_period_end"):
                container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
                container.add_item(ui.TextDisplay(
                    f"{WARNING} **Cancellation Scheduled**\n"
                    f"-# Your subscription will end on {renewal_date}"
                ))

        self.add_item(container)

    def _get_status_emoji(self, status: str) -> str:
        """Retourne l'emoji correspondant au statut"""
        status_emojis = {
            "active": GREEN_STATUS,
            "canceled": RED_STATUS,
            "trialing": YELLOW_STATUS,
            "past_due": WARNING,
            "incomplete": YELLOW_STATUS,
            "unpaid": RED_STATUS,
        }
        return status_emojis.get(status, INFO)

    def _format_date(self, iso_date: str) -> str:
        """Formate une date ISO 8601 en format lisible"""
        try:
            dt = datetime.fromisoformat(iso_date.replace('Z', '+00:00'))
            # Format: December 25, 2024
            return dt.strftime("%B %d, %Y")
        except Exception as e:
            logger.error(f"Error formatting date {iso_date}: {e}")
            return iso_date


class Subscription(commands.Cog):
    """Commandes liées aux abonnements Stripe"""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="subscription",
        description="View your Moddy subscription status and details"
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def subscription(self, interaction: discord.Interaction):
        """
        Affiche les informations d'abonnement de l'utilisateur.

        Cette commande:
        - Récupère les données d'abonnement depuis le backend
        - Affiche le statut, type, prix et dates de renouvellement
        - Indique si l'abonnement est actif ou annulé
        """
        await interaction.response.defer(ephemeral=True)

        try:
            # Récupérer les informations d'abonnement depuis le backend
            backend_client = get_backend_client()
            subscription_data = await backend_client.get_subscription_info(
                str(interaction.user.id)
            )

            # Créer la vue avec les informations
            view = SubscriptionView(
                self.bot,
                interaction.user.id,
                str(interaction.locale),
                subscription_data
            )

            await interaction.followup.send(
                view=view,
                ephemeral=True
            )

            logger.info(
                f"✅ Subscription info displayed for user {interaction.user.id} "
                f"(has_subscription: {subscription_data.get('has_subscription')})"
            )

        except BackendClientError as e:
            logger.error(f"❌ Backend error for user {interaction.user.id}: {e}", exc_info=True)
            await interaction.followup.send(
                f"{ERROR} **Error**\n"
                "Unable to retrieve your subscription information. Please try again later.",
                ephemeral=True
            )
        except Exception as e:
            logger.error(
                f"❌ Unexpected error in subscription command for user {interaction.user.id}: {e}",
                exc_info=True
            )
            await interaction.followup.send(
                f"{ERROR} **Error**\n"
                "An unexpected error occurred. Please try again later.",
                ephemeral=True
            )


async def setup(bot):
    """Charge le cog Subscription"""
    await bot.add_cog(Subscription(bot))
    logger.info("✅ Subscription cog loaded")
