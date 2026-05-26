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
from utils.emojis import PREMIUM, GREEN_STATUS, RED_STATUS, DONE
from utils.i18n import t
from utils.subscription import get_subscription

logger = logging.getLogger('moddy.cogs.subscription')


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

        container.add_item(ui.TextDisplay(
            t('commands.subscription.title', locale=self.locale,
              premium=PREMIUM)
        ))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        if self.sub and self.sub.get('is_active'):
            tier = self.sub.get('tier') or 'Moddy Max'
            container.add_item(ui.TextDisplay(
                t('commands.subscription.active', locale=self.locale,
                  green=GREEN_STATUS, tier=tier)
            ))

            expires_at = self.sub.get('expires_at')
            if expires_at:
                ts = int(expires_at.astimezone(timezone.utc).timestamp())
                container.add_item(ui.TextDisplay(
                    t('commands.subscription.expires', locale=self.locale,
                      date=f"<t:{ts}:D>")
                ))

            if self.sub.get('stripe_customer_id'):
                container.add_item(ui.TextDisplay(
                    t('commands.subscription.stripe_linked', locale=self.locale,
                      done=DONE)
                ))
        else:
            container.add_item(ui.TextDisplay(
                t('commands.subscription.inactive', locale=self.locale,
                  red=RED_STATUS)
            ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        count = len(self.servers)
        container.add_item(ui.TextDisplay(
            t('commands.subscription.servers_title', locale=self.locale,
              count=count)
        ))

        if self.servers:
            lines = "\n".join(
                t('commands.subscription.server_entry', locale=self.locale,
                  server_id=s['server_id'])
                for s in self.servers
            )
            container.add_item(ui.TextDisplay(lines))
        else:
            container.add_item(ui.TextDisplay(
                t('commands.subscription.servers_empty', locale=self.locale)
            ))

        self.add_item(container)

        row = ui.ActionRow()
        row.add_item(ui.Button(
            label=t('commands.subscription.manage_button', locale=self.locale),
            url="https://dashboard.moddy.app/billing",
            style=discord.ButtonStyle.link,
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
            if self.bot.db:
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
