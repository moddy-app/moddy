"""
User preferences command for Moddy
Allows users to customize their experience
"""
import re
import discord
from discord import app_commands, ui
from discord.ext import commands
from discord.ui import Container, TextDisplay, Separator
from discord import SeparatorSpacing
from typing import Optional

from cogs.error_handler import BaseView
from utils.components_v2 import create_error_message
from utils.i18n import i18n, t

# --------------------------------------------------------------------------- #
# custom_id templates
#
#   moddy:pref:manage:<action>:<owner_id>
#
# Owner-only: interaction_check cannot work on a restarted shell (self.user_id
# is None), so ownership is encoded per-item and re-checked on every click
# instead (Appendix B.2 of docs/PERSISTENT_VIEWS.md).
# --------------------------------------------------------------------------- #

#   \d{1,20} (not the tighter \d{17,20} snowflake range) so a bare shell
#   built with the default owner_id=0 (see PreferencesView.__init__) still
#   matches its own template — required for test_shell_constructs.
_CID_BTN_TEMPLATE = r"moddy:pref:manage:(?P<action>timezone|back):(?P<owner>\d{1,20})"
_CID_SELECT_TEMPLATE = r"moddy:pref:manage:tz_select:(?P<owner>\d{1,20})"


def _guarded(callback):
    """Route DynamicItem callback errors to the central error handler.

    A DynamicItem dispatched via ``bot.add_dynamic_items`` has no live
    ``BaseView``, so ``BaseView.on_error`` never fires. Copied from
    ``utils/appeal_views.py``.
    """
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as e:  # noqa: BLE001 — funnel everything to the handler
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, e, self.__class__.__name__)
    return wrapper


async def _reject_if_not_owner(interaction: discord.Interaction, owner_id: int) -> bool:
    """Return True (and answer ephemerally) if the clicker is not the owner."""
    if interaction.user.id == owner_id:
        return False
    locale = i18n.get_user_locale(interaction)
    await interaction.response.send_message(
        view=create_error_message(
            t("errors.not_your_message.title", locale=locale),
            t("errors.not_your_message.description", locale=locale),
        ),
        ephemeral=True,
    )
    return True

# Common timezones for selection
TIMEZONE_OPTIONS = [
    ("Europe/London", "London (GMT/BST)"),
    ("Europe/Paris", "Paris, Berlin, Rome (CET)"),
    ("Europe/Athens", "Athens, Helsinki (EET)"),
    ("Europe/Moscow", "Moscow (MSK)"),
    ("America/New_York", "New York (EST/EDT)"),
    ("America/Chicago", "Chicago (CST/CDT)"),
    ("America/Denver", "Denver (MST/MDT)"),
    ("America/Los_Angeles", "Los Angeles (PST/PDT)"),
    ("America/Sao_Paulo", "Sao Paulo (BRT)"),
    ("America/Mexico_City", "Mexico City (CST)"),
    ("Asia/Tokyo", "Tokyo (JST)"),
    ("Asia/Shanghai", "Shanghai, Beijing (CST)"),
    ("Asia/Seoul", "Seoul (KST)"),
    ("Asia/Singapore", "Singapore (SGT)"),
    ("Asia/Dubai", "Dubai (GST)"),
    ("Asia/Kolkata", "Mumbai, Delhi (IST)"),
    ("Australia/Sydney", "Sydney (AEST/AEDT)"),
    ("Pacific/Auckland", "Auckland (NZST/NZDT)"),
    ("UTC", "UTC"),
]

TIMEZONE_NAMES = {tz_id: name for tz_id, name in TIMEZONE_OPTIONS}

# Mapping of Discord locales to default timezones
LOCALE_TO_TIMEZONE = {
    "en-US": "America/New_York",
    "en-GB": "Europe/London",
    "fr": "Europe/Paris",
    "de": "Europe/Berlin",
    "es-ES": "Europe/Madrid",
    "es-419": "America/Mexico_City",
    "pt-BR": "America/Sao_Paulo",
    "it": "Europe/Rome",
    "nl": "Europe/Amsterdam",
    "pl": "Europe/Warsaw",
    "ru": "Europe/Moscow",
    "ja": "Asia/Tokyo",
    "zh-CN": "Asia/Shanghai",
    "zh-TW": "Asia/Taipei",
    "ko": "Asia/Seoul",
}


def get_default_timezone(locale: str) -> str:
    """Get default timezone based on Discord locale"""
    locale_str = str(locale)
    if locale_str in LOCALE_TO_TIMEZONE:
        return LOCALE_TO_TIMEZONE[locale_str]
    base_lang = locale_str.split("-")[0]
    if base_lang in LOCALE_TO_TIMEZONE:
        return LOCALE_TO_TIMEZONE[base_lang]
    return "UTC"


class TimezoneSelect(ui.DynamicItem[ui.Select], template=_CID_SELECT_TEMPLATE):
    """Timezone select menu on the /preferences timezone page. Auth: owner only."""

    def __init__(self, owner_id: int, *, locale: str = "en-US", current_tz: Optional[str] = None):
        options = [
            discord.SelectOption(
                label=name[:100],
                value=tz_id,
                description=tz_id,
                default=(tz_id == current_tz),
            )
            for tz_id, name in TIMEZONE_OPTIONS
        ]
        super().__init__(
            ui.Select(
                placeholder=t("commands.preferences.timezone.placeholder", locale=locale),
                options=options,
                custom_id=f"moddy:pref:manage:tz_select:{owner_id}",
            )
        )
        self.owner_id = owner_id
        self.locale = locale

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Select, match: re.Match):
        return cls(int(match["owner"]), locale=i18n.get_user_locale(interaction))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if await _reject_if_not_owner(interaction, self.owner_id):
            return

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        selected_tz = self.item.values[0]

        await bot.db.update_user_data(interaction.user.id, "reminder_timezone", selected_tz)

        await interaction.response.send_message(
            t("commands.preferences.timezone.success", locale=locale,
              timezone=TIMEZONE_NAMES.get(selected_tz, selected_tz)),
            ephemeral=True,
        )


class PreferencesManageButton(ui.DynamicItem[ui.Button], template=_CID_BTN_TEMPLATE):
    """A button on the /preferences card. Auth: owner only."""

    _STYLE = {
        "timezone": discord.ButtonStyle.primary,
        "back": discord.ButtonStyle.secondary,
    }
    _EMOJI = {
        "timezone": "<:time:1519798202990330087>",
        "back": "<:back:1519795556665397431>",
    }
    _LABEL_KEY = {
        "timezone": "commands.preferences.buttons.manage_timezone",
        "back": "commands.preferences.buttons.back",
    }

    def __init__(self, action: str, owner_id: int, *, locale: str = "en-US"):
        super().__init__(
            ui.Button(
                label=t(self._LABEL_KEY[action], locale=locale)[:80],
                style=self._STYLE[action],
                emoji=discord.PartialEmoji.from_str(self._EMOJI[action]),
                custom_id=f"moddy:pref:manage:{action}:{owner_id}",
            )
        )
        self.action = action
        self.owner_id = owner_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match: re.Match):
        return cls(match["action"], int(match["owner"]), locale=i18n.get_user_locale(interaction))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if await _reject_if_not_owner(interaction, self.owner_id):
            return

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        user_data = await bot.db.get_user(interaction.user.id)
        page = "timezone" if self.action == "timezone" else "home"
        view = PreferencesView(bot, interaction.user.id, locale, user_data, page=page)
        await interaction.response.edit_message(view=view)


class PreferencesView(BaseView):
    """Main preferences view. Persistent: yes (via DynamicItem).

    Auth: owner only — enforced per-item by the encoded owner_id, NOT by
    interaction_check (which cannot work on a restarted shell).
    """

    __persistent__ = True

    def __init__(self, bot=None, user_id: Optional[int] = None, locale: str = "en-US",
                 user_data: Optional[dict] = None, page: str = "home"):
        super().__init__()  # timeout=None
        self.bot = bot
        self.user_id = user_id
        self.locale = locale
        self.user_data = user_data or {}
        self.current_page = page

        self._build_view()

    def _build_view(self):
        # Clear existing items
        self.clear_items()

        # Build appropriate page based on current_page
        if self.current_page == "timezone":
            self._build_timezone_page()
        else:
            self._build_home_page()

    def _build_home_page(self):
        """Build the home page with all preference options"""
        container = Container()

        # Title and description
        container.add_item(TextDisplay(t("commands.preferences.title", locale=self.locale)))
        container.add_item(TextDisplay(t("commands.preferences.main_description", locale=self.locale)))

        # Current timezone display
        current_tz = self.user_data.get('data', {}).get('reminder_timezone')
        if current_tz:
            tz_display = TIMEZONE_NAMES.get(current_tz, current_tz)
        else:
            default_tz = get_default_timezone(self.locale)
            tz_display = f"{TIMEZONE_NAMES.get(default_tz, default_tz)} ({t('commands.preferences.timezone.auto_detected', locale=self.locale)})"

        container.add_item(TextDisplay(t("commands.preferences.timezone.current", locale=self.locale, timezone=tz_display)))

        container.add_item(Separator(spacing=SeparatorSpacing.small))

        # Manage timezone button
        btn_row = discord.ui.ActionRow()
        btn_row.add_item(PreferencesManageButton("timezone", self.user_id or 0, locale=self.locale))
        container.add_item(btn_row)

        # Footer
        container.add_item(TextDisplay(t("commands.preferences.footer", locale=self.locale)))

        self.add_item(container)

    def _build_timezone_page(self):
        """Build the timezone settings page with back button"""
        container = Container()

        # Title and description
        container.add_item(TextDisplay(t("commands.preferences.timezone.title", locale=self.locale)))
        container.add_item(TextDisplay(t("commands.preferences.timezone.label", locale=self.locale)))

        # Timezone select
        current_tz = self.user_data.get('data', {}).get('reminder_timezone')
        select_row = discord.ui.ActionRow()
        select_row.add_item(TimezoneSelect(self.user_id or 0, locale=self.locale, current_tz=current_tz))
        container.add_item(select_row)

        container.add_item(Separator(spacing=SeparatorSpacing.small))

        # Back button
        back_btn_row = discord.ui.ActionRow()
        back_btn_row.add_item(PreferencesManageButton("back", self.user_id or 0, locale=self.locale))
        container.add_item(back_btn_row)

        self.add_item(container)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: owner only — owner_id is encoded in each item's
        custom_id and compared to interaction.user.id on click."""
        bot.add_dynamic_items(PreferencesManageButton, TimezoneSelect)


class Preferences(commands.Cog):
    """User preferences management"""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="preferences",
        description="Manage your personal preferences"
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        incognito="Make response visible only to you"
    )
    async def preferences(
        self,
        interaction: discord.Interaction,
        incognito: Optional[bool] = None
    ):
        """Open preferences menu"""
        # Handle incognito setting
        if incognito is None and self.bot.db:
            try:
                user_pref = await self.bot.db.get_attribute('user', interaction.user.id, 'DEFAULT_INCOGNITO')
                ephemeral = True if user_pref is None else user_pref
            except:
                ephemeral = True
        else:
            ephemeral = incognito if incognito is not None else True

        # Get user data
        user_data = await self.bot.db.get_user(interaction.user.id)

        # Create preferences view
        view = PreferencesView(
            self.bot,
            interaction.user.id,
            i18n.get_user_locale(interaction),
            user_data,
        )

        await interaction.response.send_message(view=view, ephemeral=ephemeral)


async def setup(bot):
    await bot.add_cog(Preferences(bot))
