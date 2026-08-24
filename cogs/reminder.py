"""
Reminder system for Moddy
Uses database persistence and tasks.loop for scheduling
"""
import asyncio
import re
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from zoneinfo import ZoneInfo

import discord
from discord import app_commands, ui
from discord.ext import commands, tasks
from discord.ui import LayoutView, Container, TextDisplay, Separator
from discord import SeparatorSpacing
from cogs.error_handler import BaseView

from utils.i18n import i18n, t
from utils.components_v2 import create_error_message
from cogs.error_handler import BaseModal

logger = logging.getLogger('moddy.reminder')

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
    "tr": "Europe/Istanbul",
    "sv-SE": "Europe/Stockholm",
    "da": "Europe/Copenhagen",
    "no": "Europe/Oslo",
    "fi": "Europe/Helsinki",
    "el": "Europe/Athens",
    "cs": "Europe/Prague",
    "ro": "Europe/Bucharest",
    "hu": "Europe/Budapest",
    "uk": "Europe/Kiev",
    "bg": "Europe/Sofia",
    "hi": "Asia/Kolkata",
    "th": "Asia/Bangkok",
    "vi": "Asia/Ho_Chi_Minh",
    "id": "Asia/Jakarta",
    "lt": "Europe/Vilnius",
    "hr": "Europe/Zagreb",
}

# Common timezones for preferences
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


def get_default_timezone(locale: str) -> str:
    """Get default timezone based on Discord locale"""
    locale_str = str(locale)
    # Try exact match first
    if locale_str in LOCALE_TO_TIMEZONE:
        return LOCALE_TO_TIMEZONE[locale_str]
    # Try base language (e.g., "fr" from "fr-FR")
    base_lang = locale_str.split("-")[0]
    if base_lang in LOCALE_TO_TIMEZONE:
        return LOCALE_TO_TIMEZONE[base_lang]
    # Default to UTC
    return "UTC"


async def get_user_timezone(bot, user_id: int, locale: str = None) -> ZoneInfo:
    """Get user's timezone from preferences or default from locale"""
    user_data = await bot.db.get_user(user_id)
    user_tz_str = user_data.get('data', {}).get('reminder_timezone')

    if user_tz_str:
        try:
            return ZoneInfo(user_tz_str)
        except Exception:
            pass

    # Use locale-based default
    if locale:
        default_tz = get_default_timezone(locale)
        return ZoneInfo(default_tz)

    return ZoneInfo("UTC")


def parse_time_string(time_str: str, user_tz: ZoneInfo) -> Optional[datetime]:
    """Parse a time string into a datetime object in UTC

    Supports formats:
    - Relative: 1h, 30m, 2d, 1h30m, 2d3h
    - Absolute: 15:30, 3pm, 15h30
    - Date + time: 25/12 15:30, 25/12/2024 15:30
    - Natural: tomorrow 3pm, demain 15h
    """
    time_str = time_str.strip().lower()
    now = datetime.now(user_tz)

    # Try relative time first (1h, 30m, 2d, 1h30m, etc.)
    relative_pattern = r'^(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?$'
    match = re.match(relative_pattern, time_str.replace(' ', ''))
    if match and any(match.groups()):
        days = int(match.group(1) or 0)
        hours = int(match.group(2) or 0)
        minutes = int(match.group(3) or 0)

        if days == 0 and hours == 0 and minutes == 0:
            return None

        result = now + timedelta(days=days, hours=hours, minutes=minutes)
        return result.astimezone(ZoneInfo('UTC'))

    # Try "tomorrow" or "demain"
    tomorrow_match = re.match(r'^(tomorrow|demain)\s*(.*)$', time_str)
    if tomorrow_match:
        time_part = tomorrow_match.group(2).strip()
        target_date = now.date() + timedelta(days=1)

        if time_part:
            parsed_time = parse_time_only(time_part)
            if parsed_time:
                result = datetime.combine(target_date, parsed_time, tzinfo=user_tz)
                return result.astimezone(ZoneInfo('UTC'))
        else:
            # Default to 9:00 AM
            result = datetime.combine(target_date, datetime.strptime("09:00", "%H:%M").time(), tzinfo=user_tz)
            return result.astimezone(ZoneInfo('UTC'))

    # Try date + time: DD/MM HH:MM or DD/MM/YYYY HH:MM
    date_time_pattern = r'^(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\s+(.+)$'
    match = re.match(date_time_pattern, time_str)
    if match:
        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3)) if match.group(3) else now.year
        time_part = match.group(4)

        parsed_time = parse_time_only(time_part)
        if parsed_time:
            try:
                target_date = datetime(year, month, day, tzinfo=user_tz)
                result = datetime.combine(target_date.date(), parsed_time, tzinfo=user_tz)
                # If the date is in the past this year, try next year
                if result < now and not match.group(3):
                    result = result.replace(year=year + 1)
                return result.astimezone(ZoneInfo('UTC'))
            except ValueError:
                return None

    # Try time only (today or tomorrow if past)
    parsed_time = parse_time_only(time_str)
    if parsed_time:
        result = datetime.combine(now.date(), parsed_time, tzinfo=user_tz)
        # If the time is in the past, assume tomorrow
        if result <= now:
            result = result + timedelta(days=1)
        return result.astimezone(ZoneInfo('UTC'))

    return None


def parse_time_only(time_str: str) -> Optional[datetime]:
    """Parse time-only strings like 15:30, 3pm, 15h30"""
    time_str = time_str.strip().lower()

    # Try HH:MM format
    match = re.match(r'^(\d{1,2}):(\d{2})$', time_str)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return datetime.strptime(f"{hour:02d}:{minute:02d}", "%H:%M").time()

    # Try HHhMM format (French)
    match = re.match(r'^(\d{1,2})h(\d{2})?$', time_str)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return datetime.strptime(f"{hour:02d}:{minute:02d}", "%H:%M").time()

    # Try 12-hour format (3pm, 3:30pm)
    match = re.match(r'^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$', time_str)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        period = match.group(3)

        if hour == 12:
            hour = 0 if period == 'am' else 12
        elif period == 'pm':
            hour += 12

        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return datetime.strptime(f"{hour:02d}:{minute:02d}", "%H:%M").time()

    return None


def format_discord_timestamp(dt: datetime, style: str = "R") -> str:
    """Format a datetime as a Discord timestamp

    Styles:
    - R: Relative (in 2 hours, 3 days ago)
    - F: Full date and time
    - f: Short date and time
    - D: Date only
    - T: Time only
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo('UTC'))
    timestamp = int(dt.timestamp())
    return f"<t:{timestamp}:{style}>"


def format_datetime_for_user(dt: datetime, user_tz: ZoneInfo, locale: str = "en") -> str:
    """Format a datetime for display to user in their timezone"""
    local_dt = dt.astimezone(user_tz)
    if str(locale).startswith("fr"):
        return local_dt.strftime("%d/%m/%Y %H:%M")
    return local_dt.strftime("%m/%d/%Y %I:%M %p")


# --------------------------------------------------------------------------- #
# custom_id template
#
#   moddy:rem:manage:<action>:<owner_id>
#
# `owner_id` is the user the card was rendered FOR. It is compared against
# interaction.user.id on every click. A Discord snowflake is public
# information already visible in the message, so encoding it leaks nothing.
#
# \d{1,20} rather than a tighter \d{17,20} snowflake range: a bare shell
# (owner_id defaulting to 0) must still match its own template — see
# docs/PERSISTENT_VIEWS.md Migration log, Step 6.
# --------------------------------------------------------------------------- #

_REM_ACTIONS = "add|edit|delete|history|back"
_CID_REM_TEMPLATE = rf"moddy:rem:manage:(?P<action>{_REM_ACTIONS}):(?P<owner>\d{{1,20}})"


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


async def _refresh_manage_card(bot, owner_id: int, locale: str,
                                channel_id: Optional[int], message_id: Optional[int],
                                *, show_history: bool = False):
    """Rebuild the /reminders manage card in place from fresh DB state.

    Keyed on (channel_id, message_id) rather than a live view or interaction
    object, so it works the same whether the card was just opened or the bot
    restarted in between (docs/PERSISTENT_VIEWS.md Appendix B.1.b: replace a
    parent_view reference with a rebuild-from-DB helper).
    """
    if not channel_id or not message_id:
        return
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception:
            return
    reminders = await bot.db.get_user_reminders(owner_id)
    user_tz = await get_user_timezone(bot, owner_id, locale)
    past = await bot.db.get_user_past_reminders(owner_id) if show_history else []
    view = RemindersManageView(
        bot, owner_id, locale, reminders=reminders, user_tz=user_tz,
        show_history=show_history, past_reminders=past,
    )
    try:
        await channel.get_partial_message(message_id).edit(view=view)
    except Exception:
        pass


class ReminderAddModal(BaseModal):
    """Modal for adding a new reminder"""

    def __init__(self, locale: str, bot, channel_id: int = None, guild_id: int = None,
                 owner_id: int = None, card_channel_id: int = None, card_message_id: int = None):
        super().__init__(title=t("commands.reminder.modals.add_title", locale=locale))
        self.locale = locale
        self.bot = bot
        self.channel_id = channel_id
        self.guild_id = guild_id
        self.owner_id = owner_id
        self.card_channel_id = card_channel_id
        self.card_message_id = card_message_id

        self.message_input = ui.TextInput(
            label=t("commands.reminder.modals.add_message_label", locale=locale),
            placeholder=t("commands.reminder.modals.add_message_placeholder", locale=locale),
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=True
        )
        self.add_item(self.message_input)

        self.time_input = ui.TextInput(
            label=t("commands.reminder.modals.add_time_label", locale=locale),
            placeholder=t("commands.reminder.modals.add_time_placeholder", locale=locale),
            style=discord.TextStyle.short,
            max_length=50,
            required=True
        )
        self.add_item(self.time_input)

    async def on_submit(self, interaction: discord.Interaction):
        message = self.message_input.value
        time_str = self.time_input.value

        # Get user timezone (auto-detected or from preferences)
        user_tz = await get_user_timezone(self.bot, interaction.user.id, str(interaction.locale))
        remind_at = parse_time_string(time_str, user_tz)

        if not remind_at:
            await interaction.response.send_message(
                t("commands.reminder.errors.invalid_time", interaction),
                ephemeral=True
            )
            return

        if remind_at <= datetime.now(ZoneInfo('UTC')):
            await interaction.response.send_message(
                t("commands.reminder.errors.past_time", interaction),
                ephemeral=True
            )
            return

        # Check max reminders
        existing = await self.bot.db.get_user_reminders(interaction.user.id)
        if len(existing) >= 50:
            await interaction.response.send_message(
                t("commands.reminder.errors.max_reminders", interaction),
                ephemeral=True
            )
            return

        # Create reminder
        reminder_id = await self.bot.db.create_reminder(
            user_id=interaction.user.id,
            message=message,
            remind_at=remind_at,
            guild_id=self.guild_id,
            channel_id=self.channel_id,
            send_in_channel=self.channel_id is not None
        )

        # Simple confirmation message
        await interaction.response.send_message(
            t("commands.reminder.success.created", interaction, id=reminder_id),
            ephemeral=True
        )

        if self.owner_id is not None:
            await _refresh_manage_card(
                self.bot, self.owner_id, self.locale, self.card_channel_id, self.card_message_id,
            )


class ReminderEditModal(BaseModal):
    """Modal for editing a reminder"""

    def __init__(self, locale: str, bot, reminder: Dict, owner_id: int = None,
                 card_channel_id: int = None, card_message_id: int = None):
        super().__init__(title=t("commands.reminder.modals.edit_title", locale=locale))
        self.locale = locale
        self.bot = bot
        self.reminder = reminder
        self.owner_id = owner_id
        self.card_channel_id = card_channel_id
        self.card_message_id = card_message_id

        self.message_input = ui.TextInput(
            label=t("commands.reminder.modals.edit_message_label", locale=locale),
            default=reminder['message'],
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=True
        )
        self.add_item(self.message_input)

        self.time_input = ui.TextInput(
            label=t("commands.reminder.modals.edit_time_label", locale=locale),
            placeholder=t("commands.reminder.modals.add_time_placeholder", locale=locale),
            style=discord.TextStyle.short,
            max_length=50,
            required=False
        )
        self.add_item(self.time_input)

    async def on_submit(self, interaction: discord.Interaction):
        message = self.message_input.value
        time_str = self.time_input.value.strip() if self.time_input.value else None

        new_remind_at = None
        if time_str:
            user_tz = await get_user_timezone(self.bot, interaction.user.id, str(interaction.locale))
            new_remind_at = parse_time_string(time_str, user_tz)

            if not new_remind_at:
                await interaction.response.send_message(
                    t("commands.reminder.errors.invalid_time", interaction),
                    ephemeral=True
                )
                return

            if new_remind_at <= datetime.now(ZoneInfo('UTC')):
                await interaction.response.send_message(
                    t("commands.reminder.errors.past_time", interaction),
                    ephemeral=True
                )
                return

        # Update reminder
        await self.bot.db.update_reminder(
            self.reminder['id'],
            interaction.user.id,
            message=message,
            remind_at=new_remind_at
        )

        # Simple confirmation message
        await interaction.response.send_message(
            t("commands.reminder.success.edited", interaction, id=self.reminder['id']),
            ephemeral=True
        )

        if self.owner_id is not None:
            await _refresh_manage_card(
                self.bot, self.owner_id, self.locale, self.card_channel_id, self.card_message_id,
            )


class ReminderSelectForEdit(ui.Select):
    """Select menu for choosing a reminder to edit"""

    def __init__(self, reminders: List[Dict], locale: str, bot, owner_id: int = None,
                 card_channel_id: int = None, card_message_id: int = None):
        self.reminders_map = {str(r['id']): r for r in reminders}
        self.locale = locale
        self.bot = bot
        self.owner_id = owner_id
        self.card_channel_id = card_channel_id
        self.card_message_id = card_message_id

        options = []
        for reminder in reminders[:25]:
            label = f"{reminder['message'][:50]}"
            if len(reminder['message']) > 50:
                label += "..."
            options.append(discord.SelectOption(
                label=label,
                value=str(reminder['id']),
                description=f"#{reminder['id']}"
            ))

        super().__init__(
            placeholder=t("commands.reminder.modals.select_reminder_edit", locale=locale),
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        reminder = self.reminders_map.get(self.values[0])
        if reminder:
            modal = ReminderEditModal(
                self.locale, self.bot, reminder,
                owner_id=self.owner_id, card_channel_id=self.card_channel_id,
                card_message_id=self.card_message_id,
            )
            await interaction.response.send_modal(modal)


class ReminderSelectForDelete(ui.Select):
    """Select menu for choosing a reminder to delete"""

    def __init__(self, reminders: List[Dict], locale: str, bot, owner_id: int = None,
                 card_channel_id: int = None, card_message_id: int = None):
        self.reminders_map = {str(r['id']): r for r in reminders}
        self.locale = locale
        self.bot = bot
        self.owner_id = owner_id
        self.card_channel_id = card_channel_id
        self.card_message_id = card_message_id

        options = []
        for reminder in reminders[:25]:
            label = f"{reminder['message'][:50]}"
            if len(reminder['message']) > 50:
                label += "..."
            options.append(discord.SelectOption(
                label=label,
                value=str(reminder['id']),
                description=f"#{reminder['id']}"
            ))

        super().__init__(
            placeholder=t("commands.reminder.modals.select_reminder_delete", locale=locale),
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        reminder_id = int(self.values[0])
        await self.bot.db.delete_reminder(reminder_id, interaction.user.id)

        # Simple confirmation message
        await interaction.response.send_message(
            t("commands.reminder.success.deleted", interaction, id=reminder_id),
            ephemeral=True
        )

        if self.owner_id is not None:
            await _refresh_manage_card(
                self.bot, self.owner_id, self.locale, self.card_channel_id, self.card_message_id,
            )


class ReminderManageButton(ui.DynamicItem[ui.Button], template=_CID_REM_TEMPLATE):
    """One button on the /reminders manage card. Auth: owner only.

    The owner id is encoded in the custom_id so a restarted bot can still
    tell the card's owner from a passer-by clicking someone else's buttons.
    """

    _STYLE = {
        "add":     discord.ButtonStyle.success,
        "edit":    discord.ButtonStyle.primary,
        "delete":  discord.ButtonStyle.danger,
        "history": discord.ButtonStyle.secondary,
        "back":    discord.ButtonStyle.secondary,
    }
    _EMOJI = {
        "add":     "<:add:1519791773235413022>",
        "edit":    "<:edit:1519795936568676383>",
        "delete":  "<:delete:1519795753164210447>",
        "history": "<:history:1519796822963392755>",
        "back":    "<:back:1519795556665397431>",
    }
    _LABEL_KEY = {
        "add":     "commands.reminder.buttons.add",
        "edit":    "commands.reminder.buttons.edit",
        "delete":  "commands.reminder.buttons.delete",
        "history": "commands.reminder.buttons.history",
        "back":    "commands.reminder.buttons.back",
    }

    def __init__(self, action: str, owner_id: int, *,
                 locale: str = "en-US", disabled: bool = False):
        super().__init__(
            ui.Button(
                label=t(self._LABEL_KEY[action], locale=locale)[:80],
                style=self._STYLE[action],
                emoji=discord.PartialEmoji.from_str(self._EMOJI[action]),
                custom_id=f"moddy:rem:manage:{action}:{owner_id}",
                disabled=disabled,
            )
        )
        self.action = action
        self.owner_id = owner_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction,
                              item: ui.Button, match: "re.Match"):
        """Rebuild the item from the clicked custom_id.

        Runs on EVERY click once the item is registered — the live instance is
        not reused. Kept cheap and side-effect free: no DB, no API calls.
        Locale is deliberately re-derived here, never encoded in the id.
        """
        return cls(
            match["action"],
            int(match["owner"]),
            locale=i18n.get_user_locale(interaction),
        )

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if await _reject_if_not_owner(interaction, self.owner_id):
            return

        # Re-derive EVERYTHING from the interaction — there is no live view.
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        owner_id = interaction.user.id
        channel_id = interaction.channel_id
        message_id = interaction.message.id if interaction.message else None

        if self.action == "add":
            await interaction.response.send_modal(
                ReminderAddModal(
                    locale, bot, owner_id=owner_id,
                    card_channel_id=channel_id, card_message_id=message_id,
                )
            )
            return

        if self.action in ("edit", "delete"):
            reminders = await bot.db.get_user_reminders(owner_id)
            if not reminders:
                return
            select_view = LayoutView()
            container = Container()
            select_cls = ReminderSelectForEdit if self.action == "edit" else ReminderSelectForDelete
            key = "commands.reminder.modals.select_reminder_edit" if self.action == "edit" \
                else "commands.reminder.modals.select_reminder_delete"
            container.add_item(TextDisplay(t(key, locale=locale)))
            row = discord.ui.ActionRow()
            row.add_item(select_cls(
                reminders, locale, bot, owner_id=owner_id,
                card_channel_id=channel_id, card_message_id=message_id,
            ))
            container.add_item(row)
            select_view.add_item(container)
            await interaction.response.send_message(view=select_view, ephemeral=True)
            return

        # "history" and "back" both just re-render the card in place.
        view = await RemindersManageView.build_for(
            bot, owner_id, locale, show_history=(self.action == "history")
        )
        await interaction.response.edit_message(view=view)


class RemindersManageView(BaseView):
    """/reminders manage card. Persistent: yes (via ReminderManageButton).

    Auth: owner only — enforced per-button by the encoded owner_id, NOT by
    interaction_check (which cannot work on a restarted shell).
    """

    __persistent__ = True

    def __init__(self, bot=None, user_id: Optional[int] = None, locale: str = "en-US",
                 reminders: Optional[List[Dict]] = None, user_tz: Optional[ZoneInfo] = None,
                 show_history: bool = False, past_reminders: Optional[List[Dict]] = None):
        super().__init__()  # timeout=None
        self.bot = bot
        self.user_id = user_id
        self.locale = locale
        self.user_tz = user_tz or ZoneInfo("UTC")
        self.show_history = show_history
        self.reminders = reminders or []
        self.past_reminders = past_reminders or []

        self._build_view()

    @classmethod
    async def build_for(cls, bot, user_id: int, locale: str, *,
                         show_history: bool = False) -> "RemindersManageView":
        """Fetch fresh state and return a rendered view. The ONLY constructor
        callers should use for a real card — it guarantees the data is not a
        stale snapshot."""
        reminders = await bot.db.get_user_reminders(user_id)
        past = await bot.db.get_user_past_reminders(user_id) if show_history else []
        user_tz = await get_user_timezone(bot, user_id, locale)
        return cls(bot, user_id, locale, reminders, user_tz, show_history, past)

    def _build_view(self):
        # Clear existing items
        self.clear_items()

        container = Container()

        if self.show_history:
            # History view
            container.add_item(TextDisplay(t("commands.reminder.manage.history_title", locale=self.locale)))

            if not self.past_reminders:
                container.add_item(TextDisplay(t("commands.reminder.manage.history_empty", locale=self.locale)))
            else:
                for reminder in self.past_reminders[:15]:
                    sent_at = reminder.get('sent_at')
                    if sent_at:
                        if sent_at.tzinfo is None:
                            sent_at = sent_at.replace(tzinfo=ZoneInfo('UTC'))
                        time_str = format_discord_timestamp(sent_at, "R")
                    else:
                        time_str = "N/A"

                    if reminder.get('failed'):
                        item_text = t("commands.reminder.manage.history_item_failed", locale=self.locale,
                            message=reminder['message'][:100], time=time_str, id=reminder['id'])
                    else:
                        item_text = t("commands.reminder.manage.history_item", locale=self.locale,
                            message=reminder['message'][:100], time=time_str, id=reminder['id'])
                    container.add_item(TextDisplay(item_text))

                container.add_item(Separator(spacing=SeparatorSpacing.small))
                container.add_item(TextDisplay(t("commands.reminder.manage.history_footer", locale=self.locale,
                    count=len(self.past_reminders))))

            # Back button
            back_row = discord.ui.ActionRow()
            back_row.add_item(ReminderManageButton("back", self.user_id or 0, locale=self.locale))
            container.add_item(back_row)
        else:
            # Main reminders view
            container.add_item(TextDisplay(t("commands.reminder.manage.title", locale=self.locale)))

            if not self.reminders:
                container.add_item(TextDisplay(t("commands.reminder.manage.no_reminders", locale=self.locale)))
            else:
                for reminder in self.reminders[:15]:
                    remind_at = reminder['remind_at']
                    if remind_at.tzinfo is None:
                        remind_at = remind_at.replace(tzinfo=ZoneInfo('UTC'))

                    relative = format_discord_timestamp(remind_at, "R")
                    time_str = format_datetime_for_user(remind_at, self.user_tz, self.locale)

                    # New format: [subject] | #[ID] \n -# [relative] • [exact time]
                    if reminder.get('send_in_channel') and reminder.get('channel_id'):
                        item_text = t("commands.reminder.manage.reminder_item_channel", locale=self.locale,
                            message=reminder['message'][:100], relative=relative,
                            time=time_str, channel_id=reminder['channel_id'], id=reminder['id'])
                    else:
                        item_text = t("commands.reminder.manage.reminder_item", locale=self.locale,
                            message=reminder['message'][:100], relative=relative, time=time_str, id=reminder['id'])
                    container.add_item(TextDisplay(item_text))

            # Get user timezone name for footer
            tz_name = TIMEZONE_NAMES.get(str(self.user_tz), str(self.user_tz))
            container.add_item(Separator(spacing=SeparatorSpacing.small))
            container.add_item(TextDisplay(t("commands.reminder.manage.footer", locale=self.locale,
                count=len(self.reminders), timezone=tz_name)))

            # Action buttons row 1
            btn_row1 = discord.ui.ActionRow()
            btn_row1.add_item(ReminderManageButton("add", self.user_id or 0, locale=self.locale))
            btn_row1.add_item(ReminderManageButton(
                "edit", self.user_id or 0, locale=self.locale, disabled=not self.reminders))
            btn_row1.add_item(ReminderManageButton(
                "delete", self.user_id or 0, locale=self.locale, disabled=not self.reminders))
            btn_row1.add_item(ReminderManageButton("history", self.user_id or 0, locale=self.locale))
            container.add_item(btn_row1)

        self.add_item(container)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: owner only — owner_id is encoded in each button's
        custom_id and compared to interaction.user.id on click."""
        bot.add_dynamic_items(ReminderManageButton)


class Reminder(commands.Cog):
    """Reminder system for Moddy"""

    def __init__(self, bot):
        self.bot = bot
        self.check_reminders.start()

    def cog_unload(self):
        self.check_reminders.cancel()

    @tasks.loop(seconds=30)
    async def check_reminders(self):
        """Check for due reminders and send them"""
        if not self.bot.db or not self.bot.db.pool:
            return

        try:
            pending = await self.bot.db.get_pending_reminders()

            for reminder in pending:
                await self.send_reminder(reminder)
        except Exception as e:
            logger.error(f"Error checking reminders: {e}")

    @check_reminders.before_loop
    async def before_check_reminders(self):
        """Wait for bot to be ready before starting the loop"""
        await self.bot.wait_until_ready()

        # On startup, send any missed reminders
        logger.info("Checking for missed reminders...")
        await asyncio.sleep(5)  # Give DB time to initialize

        if self.bot.db and self.bot.db.pool:
            try:
                pending = await self.bot.db.get_pending_reminders()
                logger.info(f"Found {len(pending)} missed reminders to send")

                for reminder in pending:
                    await self.send_reminder(reminder, is_late=True)
            except Exception as e:
                logger.error(f"Error sending missed reminders: {e}")

    async def send_reminder(self, reminder: Dict, is_late: bool = False):
        """Send a reminder to the user"""
        user_id = reminder['user_id']
        channel_id = reminder.get('channel_id')
        guild_id = reminder.get('guild_id')
        send_in_channel = reminder.get('send_in_channel', False)

        # Get user
        try:
            user = await self.bot.fetch_user(user_id)
        except discord.NotFound:
            await self.bot.db.mark_reminder_sent(reminder['id'], failed=True)
            return

        # Build the reminder message - NO content field, everything in container
        view = LayoutView()
        container = Container()

        # Add mention inside the container for channel reminders
        if send_in_channel and channel_id and guild_id:
            container.add_item(TextDisplay(f"<@{user_id}>"))

        container.add_item(TextDisplay(t("commands.reminder.notification.title", locale="en")))
        container.add_item(TextDisplay(f"> {reminder['message']}"))

        if is_late:
            container.add_item(TextDisplay(t("commands.reminder.notification.late_notice", locale="en")))

        created_at = reminder.get('created_at')
        if created_at:
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=ZoneInfo('UTC'))
            container.add_item(TextDisplay(f"-# {t('commands.reminder.notification.footer', locale='en', time=format_discord_timestamp(created_at, 'R'))}"))

        view.add_item(container)

        sent = False

        # Try to send in channel if requested
        if send_in_channel and channel_id and guild_id:
            try:
                guild = self.bot.get_guild(guild_id)
                if guild:
                    # Check if user is still in the guild
                    member = guild.get_member(user_id)
                    if member:
                        channel = guild.get_channel(channel_id)
                        if channel and channel.permissions_for(guild.me).send_messages:
                            # Send without content - mention is in the container
                            await channel.send(view=view)
                            sent = True
            except Exception as e:
                logger.warning(f"Could not send reminder to channel: {e}")

        # Fallback to DM
        if not sent:
            try:
                # For DMs, rebuild without the mention
                view_dm = LayoutView()
                container_dm = Container()
                container_dm.add_item(TextDisplay(t("commands.reminder.notification.title", locale="en")))
                container_dm.add_item(TextDisplay(f"> {reminder['message']}"))

                if is_late:
                    container_dm.add_item(TextDisplay(t("commands.reminder.notification.late_notice", locale="en")))

                if created_at:
                    container_dm.add_item(TextDisplay(f"-# {t('commands.reminder.notification.footer', locale='en', time=format_discord_timestamp(created_at, 'R'))}"))

                view_dm.add_item(container_dm)
                # Through the notification system: the text is the user's own
                # reminder, sent by a Moddy service — attributed, not reportable.
                from notifications.models import NotificationContent, NotificationSource
                from utils.emojis import TIME
                result = await self.bot.notifications.send_dm(
                    user,
                    content=NotificationContent(
                        title=t("commands.reminder.notification.title", locale="en"),
                        body="> {message}",
                        icon=TIME,
                        template_id="reminder.notification",
                    ),
                    source=NotificationSource.service("reminder"),
                    variables={"message": reminder['message']},
                    view=view_dm,
                    locale="en-US",
                )
                sent = result.delivered
                if result.forbidden:
                    logger.warning(f"Could not DM user {user_id}")
            except discord.Forbidden:
                logger.warning(f"Could not DM user {user_id}")
                sent = False

        # Mark as sent
        await self.bot.db.mark_reminder_sent(reminder['id'], failed=not sent)

    @app_commands.command(
        name="reminder-add",
        description="Add a new reminder"
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        message="What should I remind you?",
        time="When to remind (e.g. 1h30m, 2d, tomorrow 3pm)",
        send_here="Send reminder in this channel instead of DM",
        incognito="Make response visible only to you"
    )
    async def reminder_add(
        self,
        interaction: discord.Interaction,
        message: str,
        time: str,
        send_here: Optional[bool] = False,
        incognito: Optional[bool] = None
    ):
        """Add a new reminder"""
        # Handle incognito setting
        if incognito is None and self.bot.db:
            try:
                user_pref = await self.bot.db.get_attribute('user', interaction.user.id, 'DEFAULT_INCOGNITO')
                ephemeral = True if user_pref is None else user_pref
            except:
                ephemeral = True
        else:
            ephemeral = incognito if incognito is not None else True

        # Validate message length
        if len(message) > 1000:
            await interaction.response.send_message(
                t("commands.reminder.errors.too_long", interaction),
                ephemeral=True
            )
            return

        # Get user timezone (auto-detected or from preferences)
        user_tz = await get_user_timezone(self.bot, interaction.user.id, str(interaction.locale))

        # Channel info for "send here" option
        channel_id = interaction.channel_id if send_here else None
        guild_id = interaction.guild_id if send_here else None

        remind_at = parse_time_string(time, user_tz)

        if not remind_at:
            await interaction.response.send_message(
                t("commands.reminder.errors.invalid_time", interaction),
                ephemeral=True
            )
            return

        if remind_at <= datetime.now(ZoneInfo('UTC')):
            await interaction.response.send_message(
                t("commands.reminder.errors.past_time", interaction),
                ephemeral=True
            )
            return

        # Check max reminders
        existing = await self.bot.db.get_user_reminders(interaction.user.id)
        if len(existing) >= 50:
            await interaction.response.send_message(
                t("commands.reminder.errors.max_reminders", interaction),
                ephemeral=True
            )
            return

        # Create reminder
        reminder_id = await self.bot.db.create_reminder(
            user_id=interaction.user.id,
            message=message,
            remind_at=remind_at,
            guild_id=guild_id,
            channel_id=channel_id,
            send_in_channel=send_here
        )

        # Simple confirmation message
        await interaction.response.send_message(
            t("commands.reminder.success.created", interaction, id=reminder_id),
            ephemeral=ephemeral
        )

    @app_commands.command(
        name="reminders",
        description="Manage your reminders"
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        incognito="Make response visible only to you"
    )
    async def reminders(
        self,
        interaction: discord.Interaction,
        incognito: Optional[bool] = None
    ):
        """Manage reminders"""
        # Handle incognito setting
        if incognito is None and self.bot.db:
            try:
                user_pref = await self.bot.db.get_attribute('user', interaction.user.id, 'DEFAULT_INCOGNITO')
                ephemeral = True if user_pref is None else user_pref
            except:
                ephemeral = True
        else:
            ephemeral = incognito if incognito is not None else True

        # Get user timezone (auto-detected or from preferences)
        user_tz = await get_user_timezone(self.bot, interaction.user.id, str(interaction.locale))

        # Get user's reminders
        reminders = await self.bot.db.get_user_reminders(interaction.user.id)

        # Create management view
        view = RemindersManageView(
            self.bot,
            interaction.user.id,
            i18n.get_user_locale(interaction),
            reminders,
            user_tz,
        )

        await interaction.response.send_message(view=view, ephemeral=ephemeral)


async def setup(bot):
    await bot.add_cog(Reminder(bot))
