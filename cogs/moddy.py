"""
About Moddy command
Displays information about the Moddy bot
"""

import discord
from discord import app_commands, ui
from discord.ext import commands
from typing import Optional
from datetime import datetime

from cogs.error_handler import BaseView
from utils.i18n import i18n, t
from utils.emojis import EMOJIS


# Namespaced custom_id constants for persistent dispatch.
# Format: moddy:<cog>:<view>:<action>
_CID_MAIN_ATTRIBUTION = "moddy:moddy:main:attribution"
_CID_MAIN_WE_SUPPORT = "moddy:moddy:main:we_support"
_CID_ATTRIBUTION_BACK = "moddy:moddy:attribution:back"
_CID_WE_SUPPORT_BACK = "moddy:moddy:we_support:back"


class AttributionView(BaseView):
    """View to display attributions.

    Persistent: yes. Auth: public (informational command).
    """

    __persistent__ = True

    def __init__(self, bot=None, locale: str = "en-US", user_id: Optional[int] = None):
        super().__init__()
        self.bot = bot
        self.locale = locale
        self.user_id = user_id
        self.build_view()

    def build_view(self):
        """Build the attributions view."""
        self.clear_items()

        container = ui.Container()

        # Title
        container.add_item(ui.TextDisplay(
            f"### <:attribution:1451293906175262871> {t('commands.moddy.attribution.title', locale=self.locale)}"
        ))

        # Description
        container.add_item(ui.TextDisplay(
            t('commands.moddy.attribution.description', locale=self.locale)
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Google Fonts Icons
        container.add_item(ui.TextDisplay(
            f"<:googlefonts:1451300521548972163> [@**Google Fonts** | Icons](https://fonts.google.com/icons)\n"
            f"-# {t('commands.moddy.attribution.google_fonts', locale=self.locale)}"
        ))

        # Discord.py
        container.add_item(ui.TextDisplay(
            f"<:python:1451298515199332372> [@**Rapptz** | Discord.py](https://github.com/Rapptz/discord.py)\n"
            f"-# {t('commands.moddy.attribution.discordpy', locale=self.locale)}"
        ))

        # Discord Userdoccers
        container.add_item(ui.TextDisplay(
            f"<:discorduserdoccers:1451303689602994196> [@**Discord Userdoccers** | Documentation](https://docs.discord.food/intro)\n"
            f"-# {t('commands.moddy.attribution.userdoccers', locale=self.locale)}"
        ))

        # Railway
        container.add_item(ui.TextDisplay(
            f"<:railway:1451333311199838218> [@**Railway** | Host](https://railway.app)\n"
            f"-# {t('commands.moddy.attribution.railway', locale=self.locale)}"
        ))

        self.add_item(container)

        # Back button (persistent — stable custom_id)
        back_row = ui.ActionRow()
        back_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str("<:back:1401600847733067806>"),
            label=t('commands.moddy.buttons.back', locale=self.locale),
            style=discord.ButtonStyle.secondary,
            custom_id=_CID_ATTRIBUTION_BACK,
        )
        back_btn.callback = self.on_back
        back_row.add_item(back_btn)
        self.add_item(back_row)

    async def on_back(self, interaction: discord.Interaction):
        """Handle back button click — re-derives state from interaction."""
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        main_view = ModdyMainView(bot, locale, interaction.user.id)
        await interaction.response.edit_message(view=main_view)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: public — anyone who can see the message can click."""
        bot.add_view(cls())


class WeSupportView(BaseView):
    """View to display projects we support.

    Persistent: yes. Auth: public (informational command).
    """

    __persistent__ = True

    def __init__(self, bot=None, locale: str = "en-US", user_id: Optional[int] = None):
        super().__init__()
        self.bot = bot
        self.locale = locale
        self.user_id = user_id
        self.build_view()

    def build_view(self):
        """Build the we support view."""
        self.clear_items()

        container = ui.Container()

        # Title
        container.add_item(ui.TextDisplay(
            f"### <:favorite:1451293904329769081> {t('commands.moddy.we_support.title', locale=self.locale)}"
        ))

        # Description
        container.add_item(ui.TextDisplay(
            t('commands.moddy.we_support.description', locale=self.locale)
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Empty message for now (user will add projects later)
        container.add_item(ui.TextDisplay(
            f"-# {t('commands.moddy.we_support.empty', locale=self.locale)}"
        ))

        self.add_item(container)

        # Back button (persistent — stable custom_id)
        back_row = ui.ActionRow()
        back_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str("<:back:1401600847733067806>"),
            label=t('commands.moddy.buttons.back', locale=self.locale),
            style=discord.ButtonStyle.secondary,
            custom_id=_CID_WE_SUPPORT_BACK,
        )
        back_btn.callback = self.on_back
        back_row.add_item(back_btn)
        self.add_item(back_row)

    async def on_back(self, interaction: discord.Interaction):
        """Handle back button click — re-derives state from interaction."""
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        main_view = ModdyMainView(bot, locale, interaction.user.id)
        await interaction.response.edit_message(view=main_view)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: public — anyone who can see the message can click."""
        bot.add_view(cls())


class ModdyMainView(BaseView):
    """Main view for the /moddy command.

    Persistent: yes. Auth: public (informational command).

    When ``bot`` is ``None`` the view is in "shell" mode — only the layout
    structure needed for persistent dispatch is built, and any content that
    requires runtime bot state (guild count, user count, version) is
    skipped. The shell is what gets registered via ``bot.add_view`` and
    never rendered to the user.
    """

    __persistent__ = True

    def __init__(self, bot=None, locale: str = "en-US", user_id: Optional[int] = None):
        super().__init__()
        self.bot = bot
        self.locale = locale
        self.user_id = user_id
        self.build_view()

    def build_view(self):
        """Build the main about view."""
        self.clear_items()

        container = ui.Container()

        # Title
        container.add_item(ui.TextDisplay(
            f"### <:moddy:1451280939412881508> {t('commands.moddy.title', locale=self.locale)}"
        ))

        # Bio description
        container.add_item(ui.TextDisplay(
            t('commands.moddy.bio', locale=self.locale)
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Bot Information section — only when we have a live bot reference.
        # In shell mode (bot is None, used for persistent registration) we
        # skip this since it requires runtime state.
        if self.bot is not None:
            version = self.bot.version or "Unknown"
            server_count = len(self.bot.guilds)
            user_count = len(self.bot.users)

            container.add_item(ui.TextDisplay(
                f"**{t('commands.moddy.bot_info.title', locale=self.locale)}**\n"
                f"> **{t('commands.moddy.bot_info.version', locale=self.locale)}:** `{version}`\n"
                f"> **{t('commands.moddy.bot_info.servers', locale=self.locale)}:** `{server_count:,}`\n"
                f"> **{t('commands.moddy.bot_info.users', locale=self.locale)}:** `{user_count:,}`"
            ))

            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Links section
        container.add_item(ui.TextDisplay(
            f"**{t('commands.moddy.links.title', locale=self.locale)}**\n"
            f"> [Website](https://moddy.app/)\n"
            f"> [Support](https://moddy.app/support/)\n"
            f"> [Documentation](https://docs.moddy.app/)\n"
            f"> [Moddy Max](https://moddy.app/max/)\n"
            f"> [Service Status](https://moddy.app/status/)\n"
            f"> [Github](https://moddy.app/redirect?url=https://github.com/juthing/MODDY)\n"
            f"> [Legal Notices](https://moddy.app/)"
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Footer
        current_year = datetime.now().year
        container.add_item(ui.TextDisplay(
            f"-# © {current_year} **Moddy**. {t('commands.moddy.footer.rights', locale=self.locale)}\n"
            f"-# {t('commands.moddy.footer.disclaimer', locale=self.locale)}"
        ))

        self.add_item(container)

        # Action buttons (persistent — stable custom_ids)
        buttons_row = ui.ActionRow()

        attribution_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str("<:attribution:1451293906175262871>"),
            label=t('commands.moddy.buttons.attribution', locale=self.locale),
            style=discord.ButtonStyle.secondary,
            custom_id=_CID_MAIN_ATTRIBUTION,
        )
        attribution_btn.callback = self.on_attribution
        buttons_row.add_item(attribution_btn)

        we_support_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str("<:favorite:1451293904329769081>"),
            label=t('commands.moddy.buttons.we_support', locale=self.locale),
            style=discord.ButtonStyle.secondary,
            custom_id=_CID_MAIN_WE_SUPPORT,
        )
        we_support_btn.callback = self.on_we_support
        buttons_row.add_item(we_support_btn)

        self.add_item(buttons_row)

    async def on_attribution(self, interaction: discord.Interaction):
        """Handle attribution button click — re-derives state from interaction."""
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        attribution_view = AttributionView(bot, locale, interaction.user.id)
        await interaction.response.edit_message(view=attribution_view)

    async def on_we_support(self, interaction: discord.Interaction):
        """Handle we support button click — re-derives state from interaction."""
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        we_support_view = WeSupportView(bot, locale, interaction.user.id)
        await interaction.response.edit_message(view=we_support_view)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: public — anyone who can see the message can click."""
        bot.add_view(cls())


class Moddy(commands.Cog):
    """About Moddy command system"""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="moddy",
        description="Learn more about Moddy"
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        incognito="Make response visible only to you"
    )
    async def moddy_command(
        self,
        interaction: discord.Interaction,
        incognito: Optional[bool] = None
    ):
        """Display information about Moddy"""
        # Get the ephemeral mode
        if incognito is None and self.bot.db:
            try:
                user_pref = await self.bot.db.get_attribute('user', interaction.user.id, 'DEFAULT_INCOGNITO')
                ephemeral = True if user_pref is None else user_pref
            except:
                ephemeral = True
        else:
            ephemeral = incognito if incognito is not None else True

        # Get the user's locale
        locale = i18n.get_user_locale(interaction)

        # Create the main view
        view = ModdyMainView(self.bot, locale, interaction.user.id)

        # Send response
        await interaction.response.send_message(view=view, ephemeral=ephemeral)


async def setup(bot):
    await bot.add_cog(Moddy(bot))
