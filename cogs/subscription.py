"""
/subscription command — displays the user's subscription status.
Reads from Redis cache (sub:user:{id}) with DB fallback.
The bot never modifies subscription data.
"""

import logging
from datetime import timezone

import discord
from discord import app_commands, ui
from discord.ext import commands

from cogs.error_handler import BaseView
from utils.components_v2 import create_error_message
from utils.emojis import PREMIUM
from utils.i18n import t
from utils.subscription import get_subscription

logger = logging.getLogger('moddy.cogs.subscription')

MANAGE_URL = "https://dashboard.moddy.app/billing"
SELECT_URL = "https://dashboard.moddy.app/select-premium-servers"
SUPPORT_URL = "https://moddy.app/support"


class SubscriptionView(BaseView):
    """Components V2 view for the /subscription command."""

    def __init__(self, bot, user_id: int, sub: dict | None, servers: list, locale: str):
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = user_id
        self.sub = sub
        self.servers = servers
        self.locale = locale
        self._build()

    def _build(self):
        self.clear_items()
        container = ui.Container()

        container.add_item(ui.TextDisplay(f"### {PREMIUM} Your Subscription"))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        is_active = self.sub and self.sub.get('is_active')

        if is_active:
            interval = self.sub.get('subscription_interval')
            if interval == 'year':
                interval_label = 'Annual'
            elif interval == 'month':
                interval_label = 'Monthly'
            else:
                interval_label = None

            lines = [
                f"* **Plan:** Max",
            ]
            if interval_label:
                lines.append(f"* **Type:** {interval_label}")

            expires_at = self.sub.get('expires_at')
            if expires_at:
                ts = int(expires_at.astimezone(timezone.utc).timestamp())
                lines.append(f"* **Expires:** <t:{ts}:R>")

            stripe_id = self.sub.get('stripe_customer_id')
            if stripe_id:
                lines.append(f"* **Stripe Customer ID:** `{stripe_id}`")

            container.add_item(ui.TextDisplay("\n".join(lines)))

            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

            count = len(self.servers)
            container.add_item(ui.TextDisplay(f"**Linked servers:** `{count}/5`"))

            if self.servers:
                lines_srv = "\n".join(f"- `{s['server_id']}`" for s in self.servers)
                container.add_item(ui.TextDisplay(lines_srv))
            else:
                container.add_item(ui.TextDisplay(
                    "-# No servers are linked to your subscription."
                ))
        else:
            container.add_item(ui.TextDisplay(
                "-# You don't have an active Moddy subscription.\n"
                "-# Visit [moddy.app](https://moddy.app) to learn more about Moddy Max."
            ))

        self.add_item(container)

        row = ui.ActionRow()
        row.add_item(ui.Button(
            url=MANAGE_URL,
            style=discord.ButtonStyle.link,
            label="Manage subscription",
        ))
        if is_active:
            row.add_item(ui.Button(
                url=SELECT_URL,
                style=discord.ButtonStyle.link,
                label="Select servers",
            ))
        row.add_item(ui.Button(
            url=SUPPORT_URL,
            style=discord.ButtonStyle.link,
            label="Support",
        ))
        self.add_item(row)


class Subscription(commands.Cog):
    """Subscription status command."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="subscription",
        description="View your Moddy subscription status",
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def subscription(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        locale = str(interaction.locale)
        user_id = interaction.user.id

        try:
            sub = await get_subscription(self.bot, user_id)
            servers = []
            if self.bot.db and sub and sub.get('is_active'):
                servers = await self.bot.db.get_subscription_servers(user_id)
        except Exception as e:
            logger.error(f"Subscription fetch error for {user_id}: {e}", exc_info=True)
            await interaction.followup.send(
                view=create_error_message(
                    "Error",
                    t('commands.subscription.error', locale=locale),
                ),
                ephemeral=True,
            )
            return

        view = SubscriptionView(self.bot, user_id, sub, servers, locale)
        await interaction.followup.send(view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Subscription(bot))
    logger.info("Subscription cog loaded")
