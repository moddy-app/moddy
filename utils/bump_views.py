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

See docs/BUMP_REMINDER.md.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import discord
from discord import ui

from bumpreminder import BumpBot, bot_by_key, format_interval
from cogs.error_handler import BaseView
from config import COLORS
from utils import i18n
from utils.components_v2 import create_success_message
from utils.emojis import ROCKET_LAUNCH, TIME
from utils.i18n import t

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
    from utils.guild_language import guild_locale

    if interaction.guild is None:
        return i18n.get_locale(interaction)
    return await guild_locale(interaction.client, interaction.guild)


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
            from utils.components_v2 import create_error_message
            await interaction.response.send_message(
                view=create_error_message(
                    t("errors.not_your_message.title", locale=locale),
                    t("errors.not_your_message.description", locale=locale),
                ),
                ephemeral=True,
            )
            return

        bot = interaction.client
        # Read the live row rather than the card: the card may be describing a
        # bump that a later one has already replaced.
        states = await bot.db.get_guild_bump_states(interaction.guild_id)
        state = states.get(self.bot_key)
        if not state or state.get("bumper_id") != self.user_id or state.get("sent"):
            await interaction.response.send_message(
                t("modules.bump_reminder.card.optin_expired", locale=locale),
                ephemeral=True,
            )
            return

        armed = not bool(state.get("opt_in"))
        await bot.db.set_bump_opt_in(interaction.guild_id, self.bot_key,
                                     self.user_id, armed)

        public_locale = await card_locale(interaction)
        spec = bot_by_key(self.bot_key)
        view = build_thanks_card(
            spec, self.user_id, state["due_at"],
            locale=public_locale, ping_mode="button", armed=armed,
        )
        await interaction.response.edit_message(view=view, allowed_mentions=NO_MENTIONS)
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


# --------------------------------------------------------------------------- #
# Cards
# --------------------------------------------------------------------------- #
def build_thanks_card(spec: BumpBot, bumper_id: Optional[int], due_at,
                      *, locale: str, ping_mode: str,
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
                        mention_bumper: bool, elapsed: Optional[int],
                        late_by: int = 0) -> ui.LayoutView:
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
          locale=locale, emoji=spec.emoji, name=spec.name, command=spec.command)
    ))

    footnotes = []
    if bumper_id and elapsed is not None:
        footnotes.append(t("modules.bump_reminder.card.reminder_last",
                           locale=locale, user=f"<@{bumper_id}>",
                           duration=format_interval(elapsed)))
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
