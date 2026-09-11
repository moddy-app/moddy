"""Ticket ratings — the modal, the DM button, and the statistics cards.

    pytest tests/test_ticket_ratings.py -q
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from db.repositories.ticket_ratings import (
    MAX_SCORE,
    MIN_SCORE,
    RATING_TRIGGERS,
    TRIGGER_CLOSE_REQUEST,
    TRIGGER_DM_BUTTON,
    TRIGGER_SELF_CLOSE,
)
from utils.ticket_rating_views import (
    MAX_COMMENT_LENGTH,
    TicketRateButton,
    TicketRatingModal,
    default_staff_id,
    format_rating_line,
    offer_rating,
    score_label,
    staff_candidates,
)
from utils.ticket_stats_views import (
    MIN_RATINGS_FOR_AVERAGE,
    build_staff_stats_card,
    build_stats_leaderboard_card,
)

LOCALES = ("fr", "en-US", "es-ES", "pt-BR", "de")


def _now():
    return datetime.now(timezone.utc)


# discord.py exposes the submitted values as read-only properties; these write
# the backing fields the real getters read, so the tests still go through the
# production accessors rather than around them.
def set_score(modal, value):
    modal.score.component._value = value


def set_staff(modal, values):
    modal.staff.component._values = list(values)


def set_comment(modal, value):
    modal.comment.component._value = value


def make_transcript(**overrides):
    row = {
        'id': 1, 'key': "11111111-2222-3333-4444-555555555555",
        'guild_id': 10, 'channel_id': 999, 'ticket_number': 42,
        'category_id': 'c_1', 'category_name': "Support", 'owner_id': 1,
        'participants': [1, 2, 3], 'claimed_by': 2, 'closed_by': 3,
        'close_reason': None, 'opened_at': _now() - timedelta(hours=2),
        'closed_at': _now(), 'message_count': 12,
    }
    row.update(overrides)
    return row


class FakeMember:
    def __init__(self, user_id, name="mod", bot=False):
        self.id = user_id
        self.display_name = name
        self.mention = f"<@{user_id}>"
        self.bot = bot


class FakeGuild:
    def __init__(self, members=(), guild_id=10):
        self.id = guild_id
        self._members = {m.id: m for m in members}

    def get_member(self, user_id):
        return self._members.get(user_id)


# =========================================================================== #
# Score vocabulary
# =========================================================================== #
class TestScoreLabels:
    @pytest.mark.parametrize("locale", LOCALES)
    @pytest.mark.parametrize("score", (1, 2, 3, 4, 5))
    def test_every_score_is_an_adjective_in_every_locale(self, locale, score):
        label = score_label(score, locale)
        assert label and not label.startswith("[")
        # Adjectives, never "3/5": a number means nothing consistent to people.
        assert not label.strip().isdigit()

    @pytest.mark.parametrize("locale", LOCALES)
    def test_the_five_adjectives_are_distinct(self, locale):
        labels = {score_label(score, locale) for score in range(1, 6)}
        assert len(labels) == 5

    def test_an_out_of_range_score_does_not_explode(self):
        assert score_label(99, "fr")
        assert score_label(0, "fr")


# =========================================================================== #
# Who gets rated
# =========================================================================== #
class TestStaffCandidates:
    def test_the_claimer_comes_first_then_the_closer(self):
        guild = FakeGuild([FakeMember(2), FakeMember(3), FakeMember(4)])
        assert staff_candidates(make_transcript(), guild)[:2] == [2, 3]

    def test_the_opener_is_never_a_candidate(self):
        transcript = make_transcript(claimed_by=None, closed_by=1)
        guild = FakeGuild([FakeMember(1), FakeMember(2), FakeMember(3)])
        assert 1 not in staff_candidates(transcript, guild)

    def test_bots_are_never_candidates(self):
        guild = FakeGuild([FakeMember(2, bot=True), FakeMember(3)])
        assert staff_candidates(make_transcript(), guild) == [3]

    def test_nobody_is_listed_twice(self):
        transcript = make_transcript(claimed_by=2, closed_by=2, participants=[1, 2])
        guild = FakeGuild([FakeMember(2)])
        assert staff_candidates(transcript, guild) == [2]

    def test_the_default_is_the_claimer_falling_back_to_the_closer(self):
        assert default_staff_id(make_transcript()) == 2
        assert default_staff_id(make_transcript(claimed_by=None)) == 3
        assert default_staff_id(
            make_transcript(claimed_by=None, closed_by=None)) is None


# =========================================================================== #
# The modal
# =========================================================================== #
class TestRatingModal:
    def _modal(self, **kwargs):
        guild = FakeGuild([FakeMember(2, "Alice"), FakeMember(3, "Bob")])
        defaults = dict(transcript=make_transcript(),
                        candidates=[2, 3], guild=guild, locale="fr")
        defaults.update(kwargs)
        return TicketRatingModal(None, **defaults)

    def test_it_fits_well_inside_the_five_component_budget(self):
        assert len(self._modal().children) == 3

    def test_without_candidates_the_staff_picker_is_dropped_not_left_empty(self):
        modal = self._modal(candidates=[])
        assert modal.staff is None
        assert len(modal.children) == 2

    def test_the_claimer_is_preselected(self):
        modal = self._modal()
        chosen = [o for o in modal.staff.component.options if o.default]
        assert [o.value for o in chosen] == ["2"]

    def test_nobody_in_particular_is_always_offered(self):
        values = [o.value for o in self._modal().staff.component.options]
        assert values[-1] == "none"

    def test_the_comment_is_optional_and_bounded(self):
        comment = self._modal().comment.component
        assert comment.required is False
        assert comment.max_length == MAX_COMMENT_LENGTH

    def test_a_radio_group_exposes_value_not_values(self):
        """docs/MODALS_V2.md — reading `.values` here silently returns nothing."""
        modal = self._modal()
        assert hasattr(modal.score.component, 'value')
        assert not hasattr(modal.score.component, 'values')
        set_score(modal, "5")
        assert modal._chosen_score() == 5

    @pytest.mark.parametrize("raw,expected", [
        ("5", 5), ("1", 1), ("9", MAX_SCORE), ("0", MIN_SCORE),
        (None, 3), ("nonsense", 3),
    ])
    def test_a_broken_score_falls_back_rather_than_raising(self, raw, expected):
        modal = self._modal()
        set_score(modal, raw)
        assert modal._chosen_score() == expected

    def test_an_empty_staff_selection_means_the_default_person(self):
        modal = self._modal()
        set_staff(modal, [])
        assert modal._chosen_staff() == 2

    def test_nobody_in_particular_stores_no_staff_at_all(self):
        modal = self._modal()
        set_staff(modal, ["none"])
        assert modal._chosen_staff() is None

    def test_picking_somebody_else_overrides_the_default(self):
        modal = self._modal()
        set_staff(modal, ["3"])
        assert modal._chosen_staff() == 3

    @pytest.mark.parametrize("locale", LOCALES)
    def test_it_is_translated_everywhere(self, locale):
        modal = self._modal(locale=locale)
        assert modal.title and not modal.title.startswith("[")
        assert len(modal.title) <= 45
        for label in (modal.score, modal.staff, modal.comment):
            assert len(label.text) <= 45
            assert not label.text.startswith("[")


class TestRatingSubmission:
    def _interaction(self):
        interaction = MagicMock()
        interaction.user.id = 1
        interaction.response.is_done.return_value = False
        interaction.response.send_message = AsyncMock()
        interaction.followup.send = AsyncMock()
        return interaction

    def _bot(self, created):
        db = MagicMock()
        db.create_ticket_rating = AsyncMock(return_value=created)
        return SimpleNamespace(db=db, stats=None, tickets=None), db

    async def test_a_submission_stores_the_score_and_the_trigger(self):
        bot, db = self._bot({'id': 1, 'score': 5})
        modal = TicketRatingModal(bot, transcript=make_transcript(),
                                  candidates=[2], guild=FakeGuild([FakeMember(2)]),
                                  trigger=TRIGGER_SELF_CLOSE, locale="fr")
        set_score(modal, "5")
        set_staff(modal, ["2"])
        set_comment(modal, "  parfait  ")
        await modal.on_submit(self._interaction())

        kwargs = db.create_ticket_rating.await_args.kwargs
        assert kwargs['score'] == 5
        assert kwargs['rated_staff_id'] == 2
        assert kwargs['comment'] == "parfait"
        assert kwargs['trigger'] == TRIGGER_SELF_CLOSE
        assert kwargs['transcript_id'] == 1
        assert kwargs['rated_by'] == 1

    async def test_an_empty_comment_is_stored_as_nothing_not_as_spaces(self):
        bot, db = self._bot({'id': 1, 'score': 3})
        modal = TicketRatingModal(bot, transcript=make_transcript(), locale="fr")
        set_score(modal, "3")
        set_comment(modal, "   ")
        await modal.on_submit(self._interaction())
        assert db.create_ticket_rating.await_args.kwargs['comment'] is None

    async def test_a_second_rating_on_the_same_closure_is_refused(self):
        """The unique constraint is the guard, not a check-then-insert."""
        bot, _db = self._bot(None)       # the repository returns None on conflict
        modal = TicketRatingModal(bot, transcript=make_transcript(), locale="fr")
        set_score(modal, "5")
        set_comment(modal, None)
        interaction = self._interaction()
        await modal.on_submit(interaction)
        interaction.response.send_message.assert_awaited()

    async def test_the_log_card_is_refreshed_but_never_fatally(self):
        bot, _db = self._bot({'id': 1, 'score': 4})
        bot.tickets = SimpleNamespace(
            refresh_ticket_log=AsyncMock(side_effect=RuntimeError("channel gone")))
        modal = TicketRatingModal(bot, transcript=make_transcript(), locale="fr")
        set_score(modal, "4")
        set_comment(modal, None)
        interaction = self._interaction()
        await modal.on_submit(interaction)          # must not raise
        interaction.response.send_message.assert_awaited()


# =========================================================================== #
# The DM button
# =========================================================================== #
class TestRateButton:
    def _interaction(self, user_id):
        interaction = MagicMock()
        interaction.user.id = user_id
        interaction.response.is_done.return_value = False
        interaction.response.send_message = AsyncMock()
        interaction.response.send_modal = AsyncMock()
        interaction.followup.send = AsyncMock()
        return interaction

    def _bot(self, transcript, rating=None):
        db = MagicMock()
        db.get_ticket_transcript_by_key = AsyncMock(return_value=transcript)
        db.get_ticket_rating = AsyncMock(return_value=rating)
        return SimpleNamespace(db=db, get_guild=lambda _id: FakeGuild([FakeMember(2)]))

    def test_the_custom_id_carries_the_transcript_key_not_the_channel(self):
        """A DM has no ticket channel to derive identity from."""
        key = "11111111-2222-3333-4444-555555555555"
        button = TicketRateButton.build(key, "fr")
        assert button.custom_id == f"moddy:tickets:rate:{key}"
        assert TicketRateButton.__discord_ui_compiled_template__.match(
            button.custom_id)

    async def test_only_the_opener_may_rate(self):
        button = TicketRateButton("11111111-2222-3333-4444-555555555555")
        interaction = self._interaction(user_id=77)     # not the owner
        interaction.client = self._bot(make_transcript())
        await button._callback(interaction)
        interaction.response.send_modal.assert_not_awaited()
        interaction.response.send_message.assert_awaited()

    async def test_the_opener_gets_the_modal(self):
        button = TicketRateButton("11111111-2222-3333-4444-555555555555")
        interaction = self._interaction(user_id=1)      # the owner
        interaction.client = self._bot(make_transcript())
        await button._callback(interaction)
        interaction.response.send_modal.assert_awaited()

    async def test_it_resolves_from_the_transcript_not_from_the_tickets_table(self):
        """By the time this is clicked the channel — and its row — may be gone."""
        bot = self._bot(make_transcript())
        bot.db.get_ticket_by_channel = AsyncMock(
            side_effect=AssertionError("must not touch `tickets`"))
        interaction = self._interaction(user_id=1)
        interaction.client = bot
        await TicketRateButton("11111111-2222-3333-4444-555555555555") \
            ._callback(interaction)
        bot.db.get_ticket_transcript_by_key.assert_awaited()

    async def test_a_deleted_transcript_is_explained_not_crashed_on(self):
        button = TicketRateButton("11111111-2222-3333-4444-555555555555")
        interaction = self._interaction(user_id=1)
        interaction.client = self._bot(None)
        await button._callback(interaction)
        interaction.response.send_modal.assert_not_awaited()
        interaction.response.send_message.assert_awaited()

    async def test_an_already_rated_closure_is_refused(self):
        button = TicketRateButton("11111111-2222-3333-4444-555555555555")
        interaction = self._interaction(user_id=1)
        interaction.client = self._bot(make_transcript(), rating={'id': 9})
        await button._callback(interaction)
        interaction.response.send_modal.assert_not_awaited()


# =========================================================================== #
# Offering the rating at closing time
# =========================================================================== #
class TestOfferRating:
    def _interaction(self):
        interaction = MagicMock()
        interaction.response.is_done.return_value = True
        interaction.followup.send = AsyncMock()
        return interaction

    def _bot(self, *, rating_enabled=True, transcript=None, rating=None):
        db = MagicMock()
        db.get_latest_ticket_transcript = AsyncMock(return_value=transcript)
        db.get_ticket_rating = AsyncMock(return_value=rating)
        tickets = SimpleNamespace(setting=AsyncMock(return_value=rating_enabled))
        return SimpleNamespace(db=db, tickets=tickets)

    async def test_a_server_that_switched_ratings_off_is_never_asked(self):
        bot = self._bot(rating_enabled=False, transcript=make_transcript())
        interaction = self._interaction()
        assert await offer_rating(interaction, bot, FakeGuild(),
                                  channel_id=999, trigger=TRIGGER_SELF_CLOSE,
                                  locale="fr") is False
        interaction.followup.send.assert_not_awaited()

    async def test_no_transcript_means_no_offer(self):
        """Asking for a rating we could not anchor anywhere would be worse."""
        bot = self._bot(transcript=None)
        assert await offer_rating(self._interaction(), bot, FakeGuild(),
                                  channel_id=999, trigger=TRIGGER_SELF_CLOSE,
                                  locale="fr") is False

    async def test_an_already_rated_closure_is_not_asked_again(self):
        bot = self._bot(transcript=make_transcript(), rating={'id': 1})
        assert await offer_rating(self._interaction(), bot, FakeGuild(),
                                  channel_id=999, trigger=TRIGGER_CLOSE_REQUEST,
                                  locale="fr") is False

    async def test_otherwise_the_card_is_offered(self):
        bot = self._bot(transcript=make_transcript())
        interaction = self._interaction()
        assert await offer_rating(interaction, bot, FakeGuild([FakeMember(2)]),
                                  channel_id=999, trigger=TRIGGER_SELF_CLOSE,
                                  locale="fr") is True
        interaction.followup.send.assert_awaited()

    def test_the_three_documented_triggers_are_the_stored_ones(self):
        assert set(RATING_TRIGGERS) == {
            TRIGGER_CLOSE_REQUEST, TRIGGER_SELF_CLOSE, TRIGGER_DM_BUTTON}


# =========================================================================== #
# Rendering
# =========================================================================== #
class TestRenderedRating:
    @pytest.mark.parametrize("locale", LOCALES)
    def test_a_rating_renders_score_person_and_comment(self, locale):
        line = format_rating_line(
            {'score': 4, 'rated_staff_id': 2, 'comment': "rapide et clair"}, locale)
        assert "<@2>" in line
        assert "rapide et clair" in line
        assert score_label(4, locale) in line

    def test_a_rating_attributed_to_nobody_omits_the_person(self):
        line = format_rating_line({'score': 3, 'rated_staff_id': None}, "fr")
        assert "<@" not in line


class TestStatsCards:
    def test_the_staff_card_shows_volume_average_and_reviews(self):
        summary = {'ratings': 8, 'average': 4.25, 's5': 4, 's4': 3, 's3': 1,
                   's2': 0, 's1': 0}
        recent = [{'score': 5, 'ticket_number': 42, 'created_at': _now(),
                   'comment': "impeccable"}]
        view = build_staff_stats_card(FakeMember(2, "Alice"), summary, recent,
                                      handled=11, days=30, locale="fr")
        text = str(view)
        assert view is not None and text

    def test_a_thin_sample_is_flagged_rather_than_ranked_on(self):
        summary = {'ratings': MIN_RATINGS_FOR_AVERAGE - 1, 'average': 5.0,
                   's5': 1, 's4': 0, 's3': 0, 's2': 0, 's1': 0}
        view = build_staff_stats_card(FakeMember(2), summary, [], handled=1,
                                      days=30, locale="fr")
        assert view is not None

    def test_an_empty_leaderboard_says_so_instead_of_rendering_nothing(self):
        view = build_stats_leaderboard_card(FakeGuild(), [], {}, days=30,
                                            locale="fr")
        assert view is not None

    def test_the_leaderboard_renders_every_row(self):
        rows = [{'rated_staff_id': i, 'ratings': 10 - i, 'average': 4.0,
                 'negative': 0} for i in range(1, 6)]
        view = build_stats_leaderboard_card(FakeGuild(), rows, {1: 12},
                                            days=30, locale="fr")
        assert view is not None
