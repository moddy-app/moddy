"""
/staff subscription-info <user> — staff-only view of a user's subscription.
Shows all subscription fields including stripe_customer_id and server linked timestamps.
"""

import logging
from datetime import timezone

import discord
from discord import app_commands, ui
from discord.ext import commands

from cogs.error_handler import BaseView
from utils.components_v2 import create_error_message
from utils.emojis import GREEN_STATUS, RED_STATUS, STAFF
from utils.subscription import get_subscription

logger = logging.getLogger('moddy.cogs.staff_subscription')


class StaffSubscriptionView(BaseView):
    """Components V2 view for the staff subscription-info command."""

    def __init__(self, bot, target: discord.User, sub: dict | None, servers: list):
        super().__init__(timeout=300)
        self.bot = bot
        self.target = target
        self.sub = sub
        self.servers = servers
        self._build()

    def _build(self):
        self.clear_items()
        container = ui.Container()

        container.add_item(ui.TextDisplay(
            f"### {STAFF} Informations d'abonnement\n"
            f"Utilisateur : **{self.target.display_name}** (`{self.target.id}`)"
        ))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        if self.sub and self.sub.get('is_active'):
            tier = self.sub.get('tier') or 'Moddy Max'
            container.add_item(ui.TextDisplay(
                f"{GREEN_STATUS} **Abonnement actif** — tier : `{tier}`"
            ))
        else:
            container.add_item(ui.TextDisplay(
                f"{RED_STATUS} **Aucun abonnement actif**"
            ))

        expires_at = self.sub.get('expires_at') if self.sub else None
        if expires_at:
            ts = int(expires_at.astimezone(timezone.utc).timestamp())
            container.add_item(ui.TextDisplay(
                f"**Expire le :** `{expires_at.strftime('%Y-%m-%d %H:%M UTC')}` (<t:{ts}:R>)"
            ))
        else:
            container.add_item(ui.TextDisplay("**Expire le :** `-`"))

        stripe_id = self.sub.get('stripe_customer_id') if self.sub else None
        if stripe_id:
            container.add_item(ui.TextDisplay(
                f"**Stripe customer ID :** `{stripe_id}`"
            ))
        else:
            container.add_item(ui.TextDisplay("**Stripe customer ID :** `-`"))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        count = len(self.servers)
        container.add_item(ui.TextDisplay(f"**Serveurs liés :** `{count}/5`"))

        if self.servers:
            lines = []
            for s in self.servers:
                added = s['added_at']
                ts = int(added.astimezone(timezone.utc).timestamp()) if added else 0
                lines.append(f"- `{s['server_id']}` — ajouté <t:{ts}:D>")
            container.add_item(ui.TextDisplay("\n".join(lines)))
        else:
            container.add_item(ui.TextDisplay("-# Aucun serveur lié."))

        self.add_item(container)


class StaffSubscription(commands.Cog):
    """Staff subscription-info slash command."""

    staff_group = app_commands.Group(
        name="staff",
        description="Staff-only commands",
    )

    def __init__(self, bot):
        self.bot = bot

    async def _is_staff(self, user_id: int) -> bool:
        if self.bot.is_developer(user_id):
            return True
        if not self.bot.db:
            return False
        perms = await self.bot.db.get_staff_permissions(user_id)
        return bool(perms and perms.get('roles'))

    @staff_group.command(
        name="subscription-info",
        description="[Staff] Affiche les informations d'abonnement d'un utilisateur",
    )
    @app_commands.describe(user="L'utilisateur dont afficher l'abonnement")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def subscription_info(
        self,
        interaction: discord.Interaction,
        user: discord.User,
    ):
        if not await self._is_staff(interaction.user.id):
            await interaction.response.send_message(
                view=create_error_message(
                    "Accès refusé",
                    "Cette commande est réservée au staff de Moddy.",
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            sub = await get_subscription(self.bot, user.id)
            servers = []
            if self.bot.db:
                servers = await self.bot.db.get_subscription_servers(user.id)
        except Exception as e:
            logger.error(
                f"Staff subscription fetch error for {user.id} "
                f"by {interaction.user.id}: {e}",
                exc_info=True,
            )
            await interaction.followup.send(
                view=create_error_message(
                    "Erreur",
                    "Impossible de récupérer les données d'abonnement.",
                ),
                ephemeral=True,
            )
            return

        view = StaffSubscriptionView(self.bot, user, sub, servers)
        await interaction.followup.send(view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(StaffSubscription(bot))
    logger.info("StaffSubscription cog loaded")
