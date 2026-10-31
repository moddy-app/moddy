"""Cards for the Bump Reminder module.

Two messages, and the whole design of the feature is in the difference between
them.

**The thank-you** goes out the instant a bump lands. It names the person, says
when the next window opens, and — depending on the server's setting — offers
them a button to be pinged when it does. It renders their mention but does not
notify them: they typed the command one second ago and are looking straight at
the channel; buzzing them for their own action would be noise.

**The reminder** goes out when that window opens. Here the mention *is* the
message, so it sits in a text display at the top level of the view, above and
outside the container — visible, permanent, and actually notifying. Which roles
and which people it may notify is stated explicitly at send time rather than
left to Discord's defaults, so a stale config can never ping something the
server did not choose.

See docs/features/BUMP_REMINDER.md.
"""

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import discord
from discord import ui

from bumpreminder import BumpBot, bot_by_key, format_interval
from cogs.error_handler import BaseView
from config import COLORS
from utils import i18n
from utils.components_v2 import create_success_message
from utils.emojis import DONE, ROCKET_LAUNCH, TIME
from utils.i18n import t
from utils.errors import MissingPermissions, NotFound, NotYours

logger = logging.getLogger('moddy.bump_views')

ACCENT = COLORS["primary"]

# Nothing on the thank-you card notifies anyone: the bumper is reading the
# channel they just typed in, and the roles are for the reminder an hour later.
NO_MENTIONS = discord.AllowedMentions.none()

_CID_PREFIX = "moddy:bump:optin"

# A reminder that fires well after it was due — the bot was down, or a sweep was
# delayed — says so, rather than silently pretending it is on time.
LATE_AFTER = 300


def _guarded(callback):
    """Route a dynamic-item callback error to the central handler (no live view)."""
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as e:  # noqa: BLE001
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, e, self.__class__.__name__)
    return wrapper


async def card_locale(interaction: discord.Interaction) -> str:
    """Language of a **public** bump card — the server's, not the clicker's."""
    from utils.transcription_views import bounded_guild_locale

    if interaction.guild is None:
        return i18n.get_locale(interaction)
    return await bounded_guild_locale(interaction)


# --------------------------------------------------------------------------- #
# The "ping me next time" button
# --------------------------------------------------------------------------- #
class BumpOptInButton(
    ui.DynamicItem[ui.Button],
    template=rf"{_CID_PREFIX}:(?P<bot>[a-z]{{1,16}}):(?P<user>\d{{1,20}})",
):
    """Lets the person who just bumped ask to be mentioned by the reminder.

    Only on the thank-you card, only when the server picked the ``button`` ping
    mode, and only for the bumper: their id is baked into the custom_id, so
    authorisation is re-read from the click itself and a card left in a channel
    for a month is exactly as safe as a fresh one.

    It toggles. Somebody who armed it by reflex and changed their mind clicks
    again — a reminder nobody asked for is the thing this module exists to
    avoid, and that includes its own ping.

    ``\\d{1,20}`` rather than ``\\d{17,20}``: the bare shell the persistence
    tests build uses zeros, and it has to match its own template.
    """

    def __init__(self, bot_key: str, user_id: int, *,
                 locale: str = "en-US", armed: bool = False):
        super().__init__(
            ui.Button(
                label=t("modules.bump_reminder.card.optin", locale=locale)[:80],
                style=discord.ButtonStyle.success if armed else discord.ButtonStyle.secondary,
                emoji=discord.PartialEmoji.from_str(TIME),
                custom_id=f"{_CID_PREFIX}:{bot_key}:{user_id}",
            )
        )
        self.bot_key = bot_key
        self.user_id = user_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction,
                             item: ui.Button, match: re.Match):
        return cls(match["bot"], int(match["user"]))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_locale(interaction)

        if interaction.user.id != self.user_id:
            raise NotYours()

        await interaction.response.defer()  # ack: DB reads/writes below
        bot = interaction.client
        # Read the live row rather than the card: the card may be describing a
        # bump that a later one has already replaced.
        states = await bot.db.get_guild_bump_states(interaction.guild_id)
        state = states.get(self.bot_key)
        if not state or state.get("bumper_id") != self.user_id or state.get("sent"):
            await interaction.followup.send(
                t("modules.bump_reminder.card.optin_expired", locale=locale),
                ephemeral=True,
            )
            return

        armed = not bool(state.get("opt_in"))
        await bot.db.set_bump_opt_in(interaction.guild_id, self.bot_key,
                                     self.user_id, armed)

        public_locale = await card_locale(interaction)
        spec = bot_by_key(self.bot_key)
        # The card is rebuilt from scratch on every click, so it has to restate
        # everything the original one was given — the server's name included.
        view = build_thanks_card(
            spec, self.user_id, state["due_at"],
            locale=public_locale, ping_mode="button", armed=armed,
            guild_name=interaction.guild.name if interaction.guild else "",
        )
        await interaction.edit_original_response(view=view, allowed_mentions=NO_MENTIONS)
        await interaction.followup.send(
            view=create_success_message(
                t(
                    "modules.bump_reminder.card.optin_on_title" if armed
                    else "modules.bump_reminder.card.optin_off_title",
                    locale=locale,
                ),
                t(
                    "modules.bump_reminder.card.optin_armed" if armed
                    else "modules.bump_reminder.card.optin_disarmed",
                    locale=locale,
                ),
            ),
            ephemeral=True,
        )


class BumpReminderPersistence(BaseView):
    """Marker view: registers the opt-in button's dynamic item at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        # Auth model: the bumper's id is in the custom_id and re-checked on
        # every click, so no guild or owner context needs storing anywhere.
        bot.add_dynamic_items(BumpOptInButton)
        # Auth model: the activation picker carries the server and directory in
        # its custom_id; Manage Server is re-checked on that server per click.
        bot.add_dynamic_items(BumpSuggestChannelSelect)


# --------------------------------------------------------------------------- #
# Cards
# --------------------------------------------------------------------------- #
def build_thanks_card(spec: BumpBot, bumper_id: Optional[int], due_at,
                      *, locale: str, ping_mode: str, guild_name: str,
                      armed: bool = False) -> ui.LayoutView:
    """The card posted right after a successful bump."""
    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=discord.Colour(ACCENT))

    if bumper_id:
        heading = t("modules.bump_reminder.card.thanks_title",
                    locale=locale, user=f"<@{bumper_id}>")
    else:
        heading = t("modules.bump_reminder.card.thanks_title_anonymous", locale=locale)
    container.add_item(ui.TextDisplay(f"### {ROCKET_LAUNCH} {heading}"))

    container.add_item(ui.TextDisplay(
        t("modules.bump_reminder.card.thanks_body",
          locale=locale, emoji=spec.emoji, name=spec.name,
          server=guild_name,
          timestamp=f"<t:{int(due_at.timestamp())}:R>")
    ))

    if ping_mode == "button" and bumper_id:
        row = ui.ActionRow()
        row.add_item(BumpOptInButton(spec.key, bumper_id, locale=locale, armed=armed))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(row)

    view.add_item(container)
    return view


def build_reminder_card(spec: BumpBot, *, locale: str,
                        role_ids: Sequence[int], bumper_id: Optional[int],
                        mention_bumper: bool, bumped_at: Optional[datetime],
                        guild_name: str, late_by: int = 0) -> ui.LayoutView:
    """The card posted when the command becomes available again.

    The mentions ride in a text display added to the **view**, above the
    container — the one place a Components V2 message can carry a real ping,
    since ``content=`` is rejected outright alongside a ``LayoutView``. Which of
    them actually notifies is decided by the ``allowed_mentions`` the caller
    passes, never by what this renders.
    """
    view = ui.LayoutView(timeout=None)

    mentions = [f"<@&{role_id}>" for role_id in role_ids]
    if mention_bumper and bumper_id:
        mentions.append(f"<@{bumper_id}>")
    if mentions:
        view.add_item(ui.TextDisplay(" ".join(mentions)))

    container = ui.Container(accent_colour=discord.Colour(ACCENT))
    container.add_item(ui.TextDisplay(
        f"### {ROCKET_LAUNCH} {t('modules.bump_reminder.card.reminder_title', locale=locale)}"
    ))
    container.add_item(ui.TextDisplay(
        t("modules.bump_reminder.card.reminder_body",
          locale=locale, emoji=spec.emoji, name=spec.name, command=spec.command,
          server=guild_name)
    ))

    footnotes = []
    if bumper_id and bumped_at is not None:
        footnotes.append(t("modules.bump_reminder.card.reminder_last",
                           locale=locale, user=f"<@{bumper_id}>",
                           timestamp=f"<t:{int(bumped_at.timestamp())}:R>"))
    if late_by >= LATE_AFTER:
        footnotes.append(t("modules.bump_reminder.card.reminder_late", locale=locale))
    if footnotes:
        container.add_item(ui.TextDisplay("\n".join(f"-# {line}" for line in footnotes)))

    view.add_item(container)
    return view


def reminder_mentions(guild: discord.Guild, role_ids: Sequence[int],
                      bumper: Optional[discord.Member],
                      mention_bumper: bool) -> Tuple[List[discord.Role], discord.AllowedMentions]:
    """Resolve who the reminder is allowed to notify.

    Returns the surviving roles alongside an ``AllowedMentions`` listing those
    exact objects. Never ``roles=True``: a role deleted since the config was
    written simply drops out, and the bumper is only ever in the list when the
    server's ping mode put them there — which is what keeps the "last bumped by"
    line in the card informative without it becoming a ping of its own.
    """
    roles = [role for role in (guild.get_role(rid) for rid in role_ids) if role is not None]
    users = [bumper] if (mention_bumper and bumper is not None) else []
    allowed = discord.AllowedMentions(everyone=False, roles=roles, users=users)
    return roles, allowed


def unnotifiable_roles(roles: Sequence[discord.Role],
                       can_mention_everyone: bool) -> List[discord.Role]:
    """The roles Discord will render but refuse to notify.

    A role that is **not mentionable** only pings when the sender may mention
    every role in that channel. Discord enforces that by dropping the mention
    from the delivered message — no error, no rejected request, and the tag
    still renders in blue. So a reminder can look perfect in the channel and
    notify nobody, which is indistinguishable from a working one unless
    something says otherwise. That is what this list is for.
    """
    if can_mention_everyone:
        return []
    return [role for role in roles if not role.mentionable]


# --------------------------------------------------------------------------- #
# The activation offer (services/bump_suggestion_service.py)
# --------------------------------------------------------------------------- #
_SUGGEST_PREFIX = "moddy:bump:suggest"


def suggestion_content(spec: BumpBot, locale: str):
    """Uniform payload behind the offer DM. ``{server}`` stays a placeholder."""
    from notifications.models import NotificationContent

    return NotificationContent(
        title=t("modules.bump_reminder.suggest.title", locale=locale),
        body=t("modules.bump_reminder.suggest.body", locale=locale,
               emoji=spec.emoji, name=spec.name, server="{server}",
               command=spec.command_hint,
               interval=format_interval(spec.default_interval)),
        icon=ROCKET_LAUNCH,
        accent_color=ACCENT,
        template_id=f"bump_reminder.suggest.{spec.key}",
    )


def build_suggestion_card(spec: BumpBot, guild: discord.Guild,
                          channels: Sequence[Any], *, locale: str) -> BaseView:
    """The DM offering to activate the reminder for a directory just added.

    The picker *is* the activation: choosing a channel creates the reminder
    there with the directory's defaults. Without any channel Moddy can post in,
    the card only points at ``/config``.
    """
    from config import command_label

    view = BaseView()
    container = ui.Container(accent_colour=discord.Colour(ACCENT))
    container.add_item(ui.TextDisplay(
        f"### {ROCKET_LAUNCH} {t('modules.bump_reminder.suggest.title', locale=locale)}"))
    container.add_item(ui.TextDisplay(
        t("modules.bump_reminder.suggest.body", locale=locale,
          emoji=spec.emoji, name=spec.name, server=guild.name,
          command=spec.command_hint,
          interval=format_interval(spec.default_interval))))

    if channels:
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(
            t("modules.bump_reminder.suggest.pick", locale=locale)))
        row = ui.ActionRow()
        row.add_item(BumpSuggestChannelSelect(
            guild.id, spec.key, locale=locale,
            options=[discord.SelectOption(label=f"#{c.name}"[:100], value=str(c.id))
                     for c in channels]))
        container.add_item(row)
        container.add_item(ui.TextDisplay("-# " + t(
            "modules.bump_reminder.suggest.footer", locale=locale,
            config=command_label("config"))))
    else:
        container.add_item(ui.TextDisplay(t(
            "modules.bump_reminder.suggest.no_channel", locale=locale,
            config=command_label("config"))))

    view.add_item(container)
    return view


def build_suggestion_done_card(spec: BumpBot, guild_name: str, channel_id: int,
                               *, locale: str) -> BaseView:
    """What the offer becomes once accepted: no picker left to click twice."""
    from config import command_label

    view = BaseView()
    container = ui.Container(accent_colour=discord.Colour(COLORS["success"]))
    container.add_item(ui.TextDisplay(
        f"### {DONE} {t('modules.bump_reminder.suggest.done_title', locale=locale)}"))
    container.add_item(ui.TextDisplay(t(
        "modules.bump_reminder.suggest.done_body", locale=locale,
        channel=f"<#{channel_id}>", emoji=spec.emoji, name=spec.name,
        server=guild_name, config=command_label("config"))))
    view.add_item(container)
    return view


class BumpSuggestChannelSelect(
    ui.DynamicItem[ui.Select],
    template=rf"{_SUGGEST_PREFIX}:(?P<guild>\d{{1,20}}):(?P<bot>[a-z]{{1,16}})",
):
    """Channel picker on the offer DM — picking one activates the reminder.

    It lives in a DM, so nothing about the server comes from the interaction:
    the server and directory are in the custom_id, and Manage Server is checked
    on that server for whoever clicks, every time.
    """

    def __init__(self, guild_id: int, bot_key: str, *, locale: str = "en-US",
                 options: Optional[List[discord.SelectOption]] = None):
        super().__init__(ui.Select(
            placeholder=t("modules.bump_reminder.suggest.placeholder", locale=locale)[:150],
            options=options or [discord.SelectOption(label="-", value="0")],
            min_values=1, max_values=1,
            custom_id=f"{_SUGGEST_PREFIX}:{guild_id}:{bot_key}",
        ))
        self.guild_id = guild_id
        self.bot_key = bot_key

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction,
                             item: ui.Select, match: re.Match):
        return cls(int(match["guild"]), match["bot"])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        from modules.bump_reminder import DEFAULT_PING_MODE, new_entry, normalize_config
        from utils.members import get_or_fetch_member

        locale = i18n.get_locale(interaction)
        bot = interaction.client
        spec = bot_by_key(self.bot_key)
        guild = bot.get_guild(self.guild_id)
        if spec is None or guild is None:
            raise NotFound(description=t("modules.bump_reminder.suggest.guild_gone", locale=locale))

        member = await get_or_fetch_member(guild, interaction.user.id)
        if member is None or not member.guild_permissions.manage_guild:
            raise MissingPermissions(["manage_guild"])

        values = interaction.data.get("values") or []
        channel = guild.get_channel(int(values[0])) if values and values[0].isdigit() else None
        if not isinstance(channel, discord.TextChannel):
            raise NotFound(description=t("modules.bump_reminder.suggest.channel_gone", locale=locale))

        await interaction.response.defer()  # ack: DB read + write below

        stored = await bot.module_manager.get_module_config(guild.id, "bump_reminder")
        reminders = normalize_config(stored)["reminders"]
        existing = next((r for r in reminders if r["bot"] == spec.key), None)
        if existing is not None:
            # Already done — from /config, the dashboard or another click.
            await interaction.edit_original_response(view=build_suggestion_done_card(
                spec, guild.name, existing["channel_id"], locale=locale))
            return

        reminders.append(new_entry(
            spec.key, channel_id=channel.id, role_ids=[],
            ping_mode=DEFAULT_PING_MODE, interval=spec.default_interval,
            created_by=interaction.user.id,
        ))
        success, error = await bot.module_manager.save_module_config(
            guild.id, "bump_reminder", normalize_config({"reminders": reminders}),
            actor_id=interaction.user.id,
        )
        if not success:
            await interaction.followup.send(
                t("modules.config.save.error", locale=locale, error=error or ""),
                ephemeral=True)
            return

        logger.info("Bump reminder activated from the offer: guild %s, %s, channel %s, by %s",
                    guild.id, spec.key, channel.id, interaction.user.id)
        await interaction.edit_original_response(view=build_suggestion_done_card(
            spec, guild.name, channel.id, locale=locale))
