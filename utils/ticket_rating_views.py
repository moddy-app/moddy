"""
Ticket ratings — how a member rates the way their ticket was handled.

Three moments ask for a rating, and all three end in the same modal:

1. the member accepts a closure the staff offered them;
2. the member closes their own ticket;
3. the closing DM, days later, through a button.

All three open the modal on a **second** interaction, because Discord requires
``send_modal`` to be the first response to an interaction: a single click
cannot both close a ticket and open a form. The first two therefore close the
ticket and answer with an ephemeral card that carries the "leave a review"
button; the third is a button to start with. The cost is one extra click, and
the gain is that closing never depends on a member finishing a form.

The DM button is the interesting one. By the time it is clicked the ticket
channel — and its ``tickets`` row — may be long gone, so everything it needs is
resolved from ``ticket_transcripts`` instead: who opened the ticket (which is
also the authorisation check), who claimed it, who closed it. That is the same
reason transcripts do not have a foreign key on ``tickets``.

Scores are adjectives, never numbers. "3/5" means nothing consistent across
people; "Correct" does.

See docs/TICKETS.md and docs/MODALS_V2.md.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import discord
from discord import ui

from cogs.error_handler import BaseModal
from db.repositories.ticket_ratings import (
    MAX_SCORE,
    MIN_SCORE,
    TRIGGER_DM_BUTTON,
    TRIGGER_SELF_CLOSE,
)
from utils.components_v2 import create_error_message, create_success_message
from utils.emojis import STAR, TICKET
from utils.i18n import i18n, t

logger = logging.getLogger('moddy.tickets.rating')

# One per score, 5 down to 1. The i18n keys hold the adjectives.
_SCORE_KEYS = {
    5: 'modules.tickets.rating.score_5',
    4: 'modules.tickets.rating.score_4',
    3: 'modules.tickets.rating.score_3',
    2: 'modules.tickets.rating.score_2',
    1: 'modules.tickets.rating.score_1',
}

# "Nobody in particular" — a real answer, not an empty one: a member may want
# to rate the handling without pinning it on a person.
_NO_STAFF = "none"

MAX_COMMENT_LENGTH = 1000

_CID_RATE_NOW = "moddy:tickets:rate:now"


def score_label(score: int, locale: str) -> str:
    """The adjective for a score, e.g. "Correct"."""
    return t(_SCORE_KEYS.get(int(score), _SCORE_KEYS[3]), locale=locale)


def format_rating_line(rating: Dict[str, Any], locale: str) -> str:
    """A stored rating as one block of text, for a DM or a log card."""
    score = int(rating.get('score') or 0)
    stars = STAR * max(min(score, MAX_SCORE), MIN_SCORE)
    line = (f"**{t('modules.tickets.rating.given', locale=locale)}** "
            f"{stars} `{score_label(score, locale)}`")
    staff_id = rating.get('rated_staff_id')
    if staff_id:
        line += (f"\n**{t('modules.tickets.rating.staff', locale=locale)}** "
                 f"<@{staff_id}> (`{staff_id}`)")
    comment = rating.get('comment')
    if comment:
        line += f"\n**{t('modules.tickets.rating.comment', locale=locale)}**\n{comment}"
    return line


def staff_candidates(ticket: Dict[str, Any], guild: Optional[discord.Guild],
                     ) -> List[int]:
    """Who this ticket's member could sensibly be rating, best guess first.

    Whoever claimed it comes first — they are the one who took it in charge —
    then whoever closed it, then anyone else added to the ticket who is not the
    opener. Bots and the opener themselves are never candidates.
    """
    owner_id = ticket.get('owner_id')
    ordered: List[int] = []
    for uid in (ticket.get('claimed_by'), ticket.get('closed_by'),
                *(ticket.get('participants') or [])):
        if not uid or uid == owner_id or uid in ordered:
            continue
        member = guild.get_member(int(uid)) if guild else None
        if member is not None and member.bot:
            continue
        ordered.append(int(uid))
    return ordered


def default_staff_id(ticket: Dict[str, Any]) -> Optional[int]:
    """Who the modal pre-selects: the claimer, else the closer."""
    return ticket.get('claimed_by') or ticket.get('closed_by')


# =========================================================================== #
# The modal
# =========================================================================== #
class TicketRatingModal(BaseModal):
    """Rate the handling: an adjective, optionally a person, optionally a note.

    Three top-level components of the five Discord allows — and only two when
    there is nobody to attribute the rating to, in which case asking would be a
    dropdown with a single option.
    """

    def __init__(self, bot, *, transcript: Dict[str, Any],
                 candidates: Optional[List[int]] = None,
                 guild: Optional[discord.Guild] = None,
                 trigger: str = TRIGGER_DM_BUTTON,
                 locale: str = "en-US"):
        super().__init__(title=t('modules.tickets.rating.modal_title',
                                 locale=locale)[:45])
        self.bot = bot
        self.transcript = transcript
        self.trigger = trigger
        self.locale = locale

        preselected = default_staff_id(transcript)
        self.score = ui.Label(
            text=t('modules.tickets.rating.score_label', locale=locale)[:45],
            description=t('modules.tickets.rating.score_hint', locale=locale)[:100],
            component=ui.RadioGroup(
                required=True,
                options=[
                    discord.RadioGroupOption(
                        label=score_label(value, locale)[:100],
                        value=str(value),
                        default=(value == 4),
                    )
                    for value in (5, 4, 3, 2, 1)
                ],
            ),
        )
        self.add_item(self.score)

        self.staff = None
        if candidates:
            options = []
            for uid in candidates[:24]:
                member = guild.get_member(uid) if guild else None
                label = member.display_name if member else str(uid)
                options.append(discord.SelectOption(
                    label=label[:100], value=str(uid),
                    default=(uid == preselected)))
            options.append(discord.SelectOption(
                label=t('modules.tickets.rating.no_staff', locale=locale)[:100],
                value=_NO_STAFF,
                default=not any(o.default for o in options)))
            self.staff = ui.Label(
                text=t('modules.tickets.rating.staff_label', locale=locale)[:45],
                description=t('modules.tickets.rating.staff_hint', locale=locale)[:100],
                component=ui.Select(required=False, options=options),
            )
            self.add_item(self.staff)

        self.comment = ui.Label(
            text=t('modules.tickets.rating.comment_label', locale=locale)[:45],
            description=t('modules.tickets.rating.comment_hint', locale=locale)[:100],
            component=ui.TextInput(
                style=discord.TextStyle.paragraph,
                required=False,
                max_length=MAX_COMMENT_LENGTH,
            ),
        )
        self.add_item(self.comment)

    def _chosen_score(self) -> int:
        # RadioGroup exposes `.value`, not `.values` — see docs/MODALS_V2.md.
        raw = getattr(self.score.component, 'value', None)
        try:
            return max(MIN_SCORE, min(MAX_SCORE, int(raw)))
        except (TypeError, ValueError):
            return 3

    def _chosen_staff(self) -> Optional[int]:
        if self.staff is None:
            return default_staff_id(self.transcript)
        values = getattr(self.staff.component, 'values', None) or []
        if not values:
            return default_staff_id(self.transcript)
        if values[0] == _NO_STAFF:
            return None
        try:
            return int(values[0])
        except (TypeError, ValueError):
            return None

    async def on_submit(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        bot = self.bot or interaction.client
        transcript = self.transcript
        comment = (getattr(self.comment.component, 'value', None) or '').strip() or None

        rating = None
        if getattr(bot, 'db', None):
            rating = await bot.db.create_ticket_rating(
                guild_id=transcript['guild_id'],
                channel_id=transcript['channel_id'],
                transcript_id=transcript['id'],
                ticket_number=transcript['ticket_number'],
                category_id=transcript['category_id'],
                rated_staff_id=self._chosen_staff(),
                rated_by=interaction.user.id,
                score=self._chosen_score(),
                comment=comment,
                trigger=self.trigger,
            )

        if rating is None:
            # The unique constraint refused it: this closure is already rated.
            await _reply(interaction, create_error_message(
                t('modules.tickets.rating.already_title', locale=locale),
                t('modules.tickets.rating.already_description', locale=locale)))
            return

        stats = getattr(bot, 'stats', None)
        if stats is not None:
            stats.incr("ticket.rated", guild_id=transcript['guild_id'],
                       dims={"score": str(rating['score'])})

        # The ticket log card, if there is one, now shows the rating.
        service = getattr(bot, 'tickets', None)
        if service is not None:
            try:
                await service.refresh_ticket_log(transcript)
            except Exception as e:  # noqa: BLE001 - a log is never worth an error
                logger.warning(f"[Tickets] Could not refresh the log card of "
                               f"transcript {transcript['id']}: {e}")

        await _reply(interaction, create_success_message(
            t('modules.tickets.rating.thanks_title', locale=locale),
            t('modules.tickets.rating.thanks_description', locale=locale,
              score=score_label(rating['score'], locale))))


async def _reply(interaction: discord.Interaction, view) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(view=view, ephemeral=True)
    else:
        await interaction.response.send_message(view=view, ephemeral=True)


# =========================================================================== #
# The DM button
# =========================================================================== #
class TicketRateButton(
    ui.DynamicItem[ui.Button],
    template=r"moddy:tickets:rate:(?P<key>[0-9a-fA-F-]{36})",
):
    """"Leave a review", on the closing DM. Survives everything.

    A ``DynamicItem`` rather than a plain persistent button because a DM has no
    ticket channel to derive identity from: the transcript's public key travels
    in the custom_id and everything else — the guild, the opener, the staff —
    is read back from the transcript row on each click.

    Auth: the clicker must be the ticket's opener. Re-read from the database
    every time, never trusted from the button.
    """

    def __init__(self, key: str, locale: str = "en-US"):
        super().__init__(
            ui.Button(
                label=t('modules.tickets.rating.leave', locale=locale)[:80],
                style=discord.ButtonStyle.primary,
                emoji=discord.PartialEmoji.from_str(STAR),
                custom_id=f"moddy:tickets:rate:{key}",
            )
        )
        self.key = key

    @classmethod
    def build(cls, key: str, locale: str = "en-US") -> "TicketRateButton":
        """Ready to be added to a view — a DynamicItem *is* the item."""
        return cls(key, locale)

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match['key'])

    async def callback(self, interaction: discord.Interaction):
        try:
            await self._callback(interaction)
        except Exception as e:  # noqa: BLE001 - DynamicItem has no BaseView
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, e, self.__class__.__name__)

    async def _callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        bot = interaction.client
        if not getattr(bot, 'db', None):
            await _reply(interaction, create_error_message(
                t('modules.tickets.errors.title', locale=locale),
                t('modules.tickets.errors.unavailable', locale=locale)))
            return

        transcript = await bot.db.get_ticket_transcript_by_key(self.key)
        if transcript is None:
            await _reply(interaction, create_error_message(
                t('modules.tickets.errors.title', locale=locale),
                t('modules.tickets.rating.gone', locale=locale)))
            return

        # The opener, and only the opener. Read from the row, never the button.
        if interaction.user.id != transcript['owner_id']:
            await _reply(interaction, create_error_message(
                t('modules.tickets.errors.title', locale=locale),
                t('modules.tickets.errors.missing_permission', locale=locale)))
            return

        if await bot.db.get_ticket_rating(transcript['id']) is not None:
            await _reply(interaction, create_error_message(
                t('modules.tickets.rating.already_title', locale=locale),
                t('modules.tickets.rating.already_description', locale=locale)))
            return

        guild = bot.get_guild(transcript['guild_id'])
        await interaction.response.send_modal(TicketRatingModal(
            bot, transcript=transcript,
            candidates=staff_candidates(transcript, guild),
            guild=guild, trigger=TRIGGER_DM_BUTTON, locale=locale))

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: the opener, re-read from the transcript on every click."""
        bot.add_dynamic_items(cls)


# =========================================================================== #
# The in-channel prompt (triggers 1 and 2)
# =========================================================================== #
class TicketRatePromptView(discord.ui.LayoutView):
    """Ephemeral card offered right after a member closed their own ticket.

    Not persistent, and deliberately so: it lives inside one ephemeral reply
    and the transcript it points at is held in memory for that single click.
    The DM button is the durable path — this one is the convenience.
    """

    def __init__(self, bot, transcript: Dict[str, Any], *,
                 guild: Optional[discord.Guild] = None,
                 trigger: str = TRIGGER_SELF_CLOSE,
                 locale: str = "en-US"):
        super().__init__(timeout=None)
        self.bot = bot
        self.transcript = transcript
        self.guild = guild
        self.trigger = trigger
        self.locale = locale

        container = ui.Container(accent_colour=discord.Colour(0x57F287))
        container.add_item(ui.TextDisplay(
            f"### {TICKET} {t('modules.tickets.close.done_title', locale=locale)}"))
        container.add_item(ui.TextDisplay(
            t('modules.tickets.rating.prompt_description', locale=locale)))
        self.add_item(container)

        button = ui.Button(
            label=t('modules.tickets.rating.leave', locale=locale)[:80],
            style=discord.ButtonStyle.primary,
            emoji=discord.PartialEmoji.from_str(STAR),
            custom_id=_CID_RATE_NOW,
        )
        button.callback = self.on_rate
        row = ui.ActionRow()
        row.add_item(button)
        self.add_item(row)

    async def on_rate(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        await interaction.response.send_modal(TicketRatingModal(
            self.bot, transcript=self.transcript,
            candidates=staff_candidates(self.transcript, self.guild),
            guild=self.guild, trigger=self.trigger, locale=locale))


async def offer_rating(interaction: discord.Interaction, bot, guild, *,
                       channel_id: int, trigger: str, locale: str) -> bool:
    """Answer a just-closed ticket with the "rate it" card.

    Returns whether the card was offered; ``False`` means the caller should
    send its usual confirmation instead — the server does not collect ratings,
    there is no transcript to attach one to, or one was already left.
    """
    service = getattr(bot, 'tickets', None)
    if service is None or not getattr(bot, 'db', None) or guild is None:
        return False

    from modules.tickets import SETTING_RATING
    if not await service.setting(guild.id, SETTING_RATING):
        return False

    transcript = await bot.db.get_latest_ticket_transcript(channel_id)
    if transcript is None:
        # No archive means no stable anchor for a rating; asking for one we
        # could not store would be worse than not asking.
        return False
    if await bot.db.get_ticket_rating(transcript['id']) is not None:
        return False

    view = TicketRatePromptView(bot, transcript, guild=guild,
                                trigger=trigger, locale=locale)
    if interaction.response.is_done():
        await interaction.followup.send(view=view, ephemeral=True)
    else:
        await interaction.response.send_message(view=view, ephemeral=True)
    return True
