"""
Commande ping publique avec Components V2
"""
import asyncio
import time
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import LayoutView, Container, TextDisplay, Separator, ActionRow, Button
from discord import SeparatorSpacing

from config import COLORS
from utils.emojis import GREEN_STATUS, YELLOW_STATUS, RED_STATUS
from utils.i18n import i18n, t


class PublicPing(commands.Cog):
    """Commande ping pour tous les utilisateurs"""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="ping",
        description="Check the bot's latency and status"
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        incognito="Make response visible only to you"
    )
    async def ping_slash(self, interaction: discord.Interaction, incognito: Optional[bool] = None):
        """Commande slash /ping avec i18n automatique et Components V2"""

        # === BLOC INCOGNITO ===
        if incognito is None and self.bot.db:
            try:
                user_pref = await self.bot.db.get_attribute('user', interaction.user.id, 'DEFAULT_INCOGNITO')
                ephemeral = True if user_pref is None else user_pref
            except:
                ephemeral = True
        else:
            ephemeral = incognito if incognito is not None else True

        # Mesure du temps de début
        start = time.perf_counter()

        # Latence API Discord
        api_latency = round(self.bot.latency * 1000)

        # Déterminer la qualité de la connexion et l'emoji de statut
        if api_latency <= 150:
            status_emoji = GREEN_STATUS
            status_key = "excellent"
        elif api_latency < 300:
            status_emoji = YELLOW_STATUS
            status_key = "good"
        else:
            status_emoji = RED_STATUS
            status_key = "poor"

        # Récupérer le texte du statut traduit
        status_text = t(f"commands.ping.status.{status_key}", interaction)

        # Calculer l'uptime
        uptime_delta = datetime.now(datetime.now().astimezone().tzinfo) - self.bot.launch_time.astimezone()
        days = uptime_delta.days
        hours, remainder = divmod(uptime_delta.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        # Format de l'uptime
        uptime_parts = []
        if days > 0:
            uptime_parts.append(f"{days}d")
        if hours > 0:
            uptime_parts.append(f"{hours}h")
        if minutes > 0:
            uptime_parts.append(f"{minutes}m")
        if seconds > 0 or not uptime_parts:
            uptime_parts.append(f"{seconds}s")
        uptime_str = " ".join(uptime_parts)

        # Uptime timestamp
        uptime_timestamp = f"<t:{int(self.bot.launch_time.timestamp())}:R>"

        # Shard info
        shard_id = interaction.guild.shard_id if interaction.guild else 0
        total_shards = self.bot.shard_count if self.bot.shard_count else 1

        # Version
        version = self.bot.version or "Unknown"

        # Créer le message initial avec Components V2
        view = LayoutView()
        container = Container()

        # Header avec titre
        title = t("commands.ping.response.title", interaction)
        container.add_item(TextDisplay(f"### {title}"))

        # Separator
        container.add_item(Separator(spacing=SeparatorSpacing.small))

        # Info principale
        main_info = t(
            "commands.ping.response.main_info",
            interaction,
            status_emoji=status_emoji,
            status=status_text,
            api_latency=api_latency,
            message_latency=t("common.loading", interaction)
        )
        container.add_item(TextDisplay(main_info))

        # Détails système
        system_details = t(
            "commands.ping.response.system_details",
            interaction,
            shard_id=shard_id,
            total_shards=total_shards,
            uptime=uptime_str,
            uptime_timestamp=uptime_timestamp,
            version=version,
            guild_count=len(self.bot.guilds),
            user_count=len(self.bot.users)
        )
        container.add_item(TextDisplay(system_details))

        view.add_item(container)

        # Liens utiles (en dehors du container)
        links_row = ActionRow()

        support_btn = Button(
            label=t("commands.ping.buttons.support", interaction),
            style=discord.ButtonStyle.link,
            url="https://moddy.app/support"
        )
        links_row.add_item(support_btn)

        status_btn = Button(
            label=t("commands.ping.buttons.status", interaction),
            style=discord.ButtonStyle.link,
            url="https://moddy.app/status"
        )
        links_row.add_item(status_btn)

        view.add_item(links_row)

        # Envoyer le message initial
        await interaction.response.send_message(view=view, ephemeral=ephemeral)

        # Calculer la latence du message
        end = time.perf_counter()
        message_latency = round((end - start) * 1000)

        # Créer le message mis à jour avec la latence réelle
        view_updated = LayoutView()
        container_updated = Container()

        # Header
        container_updated.add_item(TextDisplay(f"### {title}"))
        container_updated.add_item(Separator(spacing=SeparatorSpacing.small))

        # Info principale avec latence mise à jour
        main_info_updated = t(
            "commands.ping.response.main_info",
            interaction,
            status_emoji=status_emoji,
            status=status_text,
            api_latency=api_latency,
            message_latency=f"`{message_latency}ms`"
        )
        container_updated.add_item(TextDisplay(main_info_updated))

        # Détails système
        container_updated.add_item(TextDisplay(system_details))

        view_updated.add_item(container_updated)

        # Liens (recréer les boutons en dehors du container)
        links_row_updated = ActionRow()

        support_btn_updated = Button(
            label=t("commands.ping.buttons.support", interaction),
            style=discord.ButtonStyle.link,
            url="https://moddy.app/support"
        )
        links_row_updated.add_item(support_btn_updated)

        status_btn_updated = Button(
            label=t("commands.ping.buttons.status", interaction),
            style=discord.ButtonStyle.link,
            url="https://moddy.app/status"
        )
        links_row_updated.add_item(status_btn_updated)

        view_updated.add_item(links_row_updated)

        # Modifier le message avec la latence réelle
        await interaction.edit_original_response(view=view_updated)


async def setup(bot):
    await bot.add_cog(PublicPing(bot))
