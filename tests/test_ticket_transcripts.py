"""Ticket transcripts, closure detection and ratings.

Everything here is pure Python: no Discord, no gateway, no database. The
Discord objects are the small stubs below — deliberately the exact surface the
production code touches, so a stub that passes is evidence the real object
would.

    pytest tests/test_ticket_transcripts.py -q
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.tickets import (
    DEFAULT_SETTINGS,
    MAX_RETENTION_DAYS,
    PERM_STATS,
    SETTING_CLOSURE_DETECTION,
    SETTING_LOG_CHANNEL,
    SETTING_RATING,
    SETTING_RETENTION,
    SETTING_TRANSCRIPTS,
    TICKET_PERMISSIONS,
    normalize_config,
    normalize_settings,
)
from services.ticket_closure_detector import (
    CLOSURE_THRESHOLD,
    MAX_LENGTH,
    MIN_LENGTH,
    TicketClosureDetector,
    load_keywords,
)
from services.ticket_transcript_service import (
    MAX_MESSAGE_CONTENT,
    TRANSCRIPT_VERSION,
    TicketTranscriptService,
    extract_component_text,
    transcript_url,
)
from utils.compression import (
    CODEC_ZLIB,
    CODEC_ZSTD,
    UnknownCodecError,
    compress,
    decompress,
)

LOCALES = ("fr", "en-US", "es-ES", "pt-BR", "de")


# --------------------------------------------------------------------------- #
# Stubs
# --------------------------------------------------------------------------- #
def _now():
    return datetime.now(timezone.utc)


class FakeAuthor:
    def __init__(self, user_id=1, name="member", bot=False):
        self.id = user_id
        self.name = name
        self.display_name = name.title()
        self.bot = bot
        self.display_avatar = SimpleNamespace(url=f"https://cdn/{user_id}.png")


class FakeMessage:
    """Only what the exporter reads."""

    def __init__(self, message_id=1, author=None, content="", *, components=(),
                 attachments=(), embeds=(), reactions=(), reference=None,
                 edited_at=None, created_at=None, message_type=None):
        import discord
        self.id = message_id
        self.author = author or FakeAuthor()
        self.content = content
        self.components = components
        self.attachments = list(attachments)
        self.embeds = list(embeds)
        self.reactions = list(reactions)
        self.reference = reference
        self.edited_at = edited_at
        self.created_at = created_at or _now()
        self.type = message_type or discord.MessageType.default


class FakeHistoryChannel:
    """A channel whose history is a fixed list, newest first like Discord's."""

    def __init__(self, messages, channel_id=999):
        self.id = channel_id
        self._messages = list(messages)
        self.guild = None

    def history(self, *, limit=None, oldest_first=False):
        ordered = self._messages if oldest_first else list(reversed(self._messages))

        async def _iterator():
            for index, message in enumerate(ordered):
                if limit is not None and index >= limit:
                    return
                yield message

        return _iterator()


def make_ticket(**overrides):
    ticket = {
        'guild_id': 10, 'channel_id': 999, 'number': 42,
        'panel_id': 'p_a', 'category_id': 'c_1', 'owner_id': 1,
        'participants': [1, 2], 'claimed_by': None, 'close_reason': None,
        'opened_at': _now() - timedelta(hours=1), 'staff_thread_id': None,
        'status': 'open',
    }
    ticket.update(overrides)
    return ticket


# =========================================================================== #
# Compression
# =========================================================================== #
class TestCompression:
    def test_round_trip_preserves_bytes(self):
        payload = ('{"messages":[{"c":"merci beaucoup"}]}' * 50).encode()
        blob, codec = compress(payload)
        assert codec in (CODEC_ZSTD, CODEC_ZLIB)
        assert decompress(blob, codec) == payload

    def test_conversational_json_compresses_hard(self):
        """The whole point of the feature: transcripts must be cheap to keep."""
        payload = ('{"i":"1","a":7,"t":1757000000,"c":"merci beaucoup"}' * 400).encode()
        blob, _codec = compress(payload)
        assert len(blob) < len(payload) * 0.2

    def test_zlib_round_trip_when_zstandard_is_missing(self, monkeypatch):
        """A deployment without the wheel still archives, and says so."""
        import builtins
        real_import = builtins.__import__

        def no_zstandard(name, *args, **kwargs):
            if name == 'zstandard':
                raise ImportError("no zstandard here")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, '__import__', no_zstandard)
        payload = b'{"messages":[]}' * 20
        blob, codec = compress(payload)
        assert codec == CODEC_ZLIB
        monkeypatch.undo()
        # And it still reads back once the wheel is there again: the codec is
        # stored per row precisely so the two can coexist.
        assert decompress(blob, CODEC_ZLIB) == payload

    def test_an_unknown_codec_is_refused_rather_than_guessed(self):
        with pytest.raises(UnknownCodecError):
            decompress(b'xxxx', 'brotli')


# =========================================================================== #
# The exporter
# =========================================================================== #
class TestComponentText:
    def test_a_components_v2_card_is_not_archived_blank(self):
        """Moddy's own cards carry no `content` — all their text is components."""
        inner = SimpleNamespace(content="### Ticket closed")
        body = SimpleNamespace(content="Reason: solved")
        container = SimpleNamespace(children=[inner, body])
        assert extract_component_text([container]) == "### Ticket closed\nReason: solved"

    def test_it_walks_sections_and_accessories_too(self):
        accessory = SimpleNamespace(content="accessory")
        section = SimpleNamespace(components=[SimpleNamespace(content="section")],
                                  accessory=accessory)
        assert extract_component_text([section]) == "section\naccessory"

    def test_no_components_is_an_empty_string_not_an_error(self):
        assert extract_component_text(None) == ""
        assert extract_component_text([]) == ""


class TestExport:
    @pytest.fixture
    def service(self):
        return TicketTranscriptService(SimpleNamespace(db=None, stats=None))

    async def test_messages_come_back_oldest_first(self, service):
        author = FakeAuthor()
        base = _now()
        messages = [
            FakeMessage(1, author, "first", created_at=base),
            FakeMessage(2, author, "second", created_at=base + timedelta(minutes=1)),
            FakeMessage(3, author, "third", created_at=base + timedelta(minutes=2)),
        ]
        body, authors, truncated = await service.build_payload(
            FakeHistoryChannel(messages))
        assert [m['c'] for m in body['messages']] == ["first", "second", "third"]
        assert body['v'] == TRANSCRIPT_VERSION
        assert truncated is False
        assert list(authors) == [author.id]

    async def test_authors_are_stored_once_not_per_message(self, service):
        author = FakeAuthor(7, "jules")
        messages = [FakeMessage(i, author, f"line {i}") for i in range(20)]
        body, authors, _ = await service.build_payload(FakeHistoryChannel(messages))
        assert len(authors) == 1
        assert all(m['a'] == 7 for m in body['messages'])
        # The author's name appears nowhere in the body itself.
        assert all('jules' not in str(m) for m in body['messages'])

    async def test_beyond_the_cap_the_end_is_kept_and_flagged(self, service, monkeypatch):
        monkeypatch.setattr(
            "services.ticket_transcript_service.MAX_TRANSCRIPT_MESSAGES", 5)
        author = FakeAuthor()
        base = _now()
        messages = [FakeMessage(i, author, f"line {i}",
                                created_at=base + timedelta(minutes=i))
                    for i in range(12)]
        body, _authors, truncated = await service.build_payload(
            FakeHistoryChannel(messages))
        assert truncated is True
        assert len(body['messages']) == 5
        # The conclusion survives, the first day does not — that is the trade.
        assert body['messages'][-1]['c'] == "line 11"

    async def test_long_messages_are_truncated_not_stored_whole(self, service):
        message = FakeMessage(1, FakeAuthor(), "x" * (MAX_MESSAGE_CONTENT + 500))
        body, _a, _t = await service.build_payload(FakeHistoryChannel([message]))
        assert len(body['messages'][0]['c']) == MAX_MESSAGE_CONTENT

    async def test_empty_fields_are_omitted_entirely(self, service):
        body, _a, _t = await service.build_payload(
            FakeHistoryChannel([FakeMessage(1, FakeAuthor(), "hello")]))
        entry = body['messages'][0]
        assert set(entry) == {'i', 'a', 't', 'c'}

    async def test_attachments_are_referenced_never_downloaded(self, service):
        attachment = SimpleNamespace(filename="a.png", url="https://cdn/a.png",
                                     size=1234, content_type="image/png")
        message = FakeMessage(1, FakeAuthor(), "", attachments=[attachment])
        body, _a, _t = await service.build_payload(FakeHistoryChannel([message]))
        assert body['messages'][0]['f'] == [
            {'n': "a.png", 'u': "https://cdn/a.png", 's': 1234, 'ct': "image/png"}]

    async def test_the_staff_thread_is_kept_under_its_own_key(self, service):
        """Staff-only content must never merge into the member's transcript."""
        public = FakeHistoryChannel([FakeMessage(1, FakeAuthor(), "hello")])
        staff = FakeHistoryChannel([FakeMessage(2, FakeAuthor(2, "mod"), "internal")],
                                   channel_id=1000)
        body, authors, _ = await service.build_payload(public, staff_thread=staff)
        assert [m['c'] for m in body['messages']] == ["hello"]
        assert [m['c'] for m in body['staff_thread']['messages']] == ["internal"]
        assert set(authors) == {1, 2}

    async def test_no_staff_thread_means_no_key_at_all(self, service):
        body, _a, _t = await service.build_payload(
            FakeHistoryChannel([FakeMessage(1, FakeAuthor(), "hi")]))
        assert 'staff_thread' not in body


class TestCapture:
    """`capture` is the one that must never break a closure."""

    def _bot(self, db):
        return SimpleNamespace(db=db, stats=None, get_guild=lambda _id: None)

    async def test_a_history_failure_returns_none_rather_than_raising(self):
        import discord
        db = MagicMock()
        service = TicketTranscriptService(self._bot(db))
        channel = MagicMock()
        channel.id = 999
        channel.guild = None

        def boom(**_kwargs):
            raise discord.Forbidden(MagicMock(status=403), "nope")

        channel.history = boom
        assert await service.capture(channel, make_ticket(), {}, 5) is None
        db.create_ticket_transcript.assert_not_called()

    async def test_a_storage_failure_returns_none_rather_than_raising(self):
        db = MagicMock()
        db.create_ticket_transcript = AsyncMock(side_effect=RuntimeError("pg down"))
        service = TicketTranscriptService(self._bot(db))
        channel = FakeHistoryChannel([FakeMessage(1, FakeAuthor(), "hi")])
        assert await service.capture(channel, make_ticket(), {'name': "S"}, 5) is None

    async def test_a_successful_capture_stores_compressed_bytes(self):
        stored = {}

        async def create(**kwargs):
            stored.update(kwargs)
            return {'id': 1, 'key': 'abc', **kwargs}

        db = MagicMock()
        db.create_ticket_transcript = create
        service = TicketTranscriptService(self._bot(db))
        channel = FakeHistoryChannel(
            [FakeMessage(i, FakeAuthor(), "merci beaucoup") for i in range(30)])

        row = await service.capture(channel, make_ticket(), {'name': "Support"}, 5)
        assert row['key'] == 'abc'
        assert stored['message_count'] == 30
        assert stored['category_name'] == "Support"
        assert stored['closed_by'] == 5
        assert len(stored['payload']) < stored['payload_size']
        assert decompress(stored['payload'], stored['codec'])

    async def test_without_a_database_nothing_is_attempted(self):
        service = TicketTranscriptService(SimpleNamespace(db=None))
        assert await service.capture(MagicMock(), make_ticket(), {}, 5) is None


def test_the_transcript_url_points_at_the_dashboard():
    assert transcript_url("abc-123").endswith("/transcripts/abc-123")


# =========================================================================== #
# Closure detection
# =========================================================================== #
class TestClosurePrefilter:
    @pytest.fixture
    def detector(self):
        return TicketClosureDetector(SimpleNamespace())

    @pytest.mark.parametrize("message", [
        "merci beaucoup ça marche",          # fr
        "thanks a lot, that worked",         # en
        "muchas gracias, ya está",           # es
        "obrigado, tudo certo",              # pt
        "vielen dank, alles gut",            # de
    ])
    def test_every_supported_language_gets_through(self, detector, message):
        assert detector.looks_like_closure(message) is True

    @pytest.mark.parametrize("message", [
        "comment je fais ça merci ?",        # a question is not a goodbye
        "merci <@123> tu peux regarder",     # addressed to somebody
        "/ticket close merci",               # a command
        "ok",                                # too short
        "merci " + "x" * MAX_LENGTH,         # too long
        "je n'arrive toujours pas a me co",  # no closing root at all
        "",
        None,
    ])
    def test_everything_else_is_rejected_for_free(self, detector, message):
        assert detector.looks_like_closure(message) is False

    def test_the_bounds_are_the_ones_advertised(self, detector):
        assert detector.looks_like_closure("merci" + "!" * (MIN_LENGTH - 6)) is False
        assert detector.looks_like_closure("merci beaucoup") is True

    def test_accents_and_case_do_not_matter(self, detector):
        assert detector.looks_like_closure("C'EST RÉSOLU, MERCI") is True

    def test_the_keyword_table_covers_the_five_languages(self):
        import json
        from services.ticket_closure_detector import _REFERENCES_PATH
        with open(_REFERENCES_PATH, encoding='utf-8') as f:
            data = json.load(f)
        assert set(data['keywords']) == {'fr', 'en', 'es', 'pt', 'de'}
        assert all(len(roots) >= 10 for roots in data['keywords'].values())
        assert len(load_keywords()) >= 80

    def test_the_reference_corpus_covers_them_too(self):
        from services.ticket_closure_detector import _ClosureReferences
        texts, categories = _ClosureReferences.load_reference_texts()
        assert len(texts) >= 30
        assert set(categories) == {'resolution'}
        for marker in ("merci", "thank", "gracias", "obrigado", "danke"):
            assert any(marker in text for text in texts), marker


class TestClosureScoring:
    def test_the_threshold_is_stricter_than_automod(self):
        """A false positive here is visible to the whole channel."""
        from automod.constants import SEUIL_EMBEDDING
        assert CLOSURE_THRESHOLD > SEUIL_EMBEDDING

    async def test_no_gateway_means_no_score_and_no_crash(self):
        detector = TicketClosureDetector(SimpleNamespace(gateway=None))
        assert await detector.score("merci beaucoup") is None

    async def test_a_rejected_message_never_reaches_the_gateway(self):
        """The prefilter is what keeps this feature from costing anything."""
        embed = AsyncMock()
        detector = TicketClosureDetector(
            SimpleNamespace(gateway=SimpleNamespace(ai=SimpleNamespace(embed=embed))))
        for message in ("what is going on here ?", "ok", "<@1> please look"):
            assert detector.looks_like_closure(message) is False
        embed.assert_not_awaited()


class TestClosureState:
    @pytest.fixture
    def detector(self):
        return TicketClosureDetector(SimpleNamespace())

    async def test_a_young_ticket_is_left_alone(self, detector):
        ticket = make_ticket(opened_at=_now())
        message = SimpleNamespace(channel=SimpleNamespace(id=1), content="merci")
        for _ in range(5):
            await detector.consider(message, ticket)
        assert detector._state(1).timer is None

    async def test_a_quiet_ticket_needs_a_conversation_first(self, detector):
        ticket = make_ticket()
        message = SimpleNamespace(channel=SimpleNamespace(id=2), content="merci")
        await detector.consider(message, ticket)
        assert detector._state(2).timer is None       # 1 message is not a conversation

    async def test_dismissing_holds_the_suggestion_off(self, detector):
        ticket = make_ticket()
        message = SimpleNamespace(channel=SimpleNamespace(id=3), content="merci")
        detector.note_dismissed(3)
        for _ in range(5):
            await detector.consider(message, ticket)
        assert detector._state(3).timer is None

    def test_forgetting_a_channel_cancels_its_pending_look(self, detector):
        timer = MagicMock()
        detector._state(4).timer = timer
        detector.forget(4)
        timer.cancel.assert_called_once()
        assert 4 not in detector._states


# =========================================================================== #
# Settings
# =========================================================================== #
class TestSettings:
    def test_a_config_written_before_these_existed_gets_the_defaults(self):
        config = normalize_config({'panels': [{'name': "P"}]})
        assert config['settings'] == DEFAULT_SETTINGS

    def test_closure_detection_is_off_by_default_because_it_costs_quota(self):
        assert DEFAULT_SETTINGS[SETTING_CLOSURE_DETECTION] is False
        assert DEFAULT_SETTINGS[SETTING_TRANSCRIPTS] is True
        assert DEFAULT_SETTINGS[SETTING_RATING] is True

    def test_the_three_switches_are_independent(self):
        settings = normalize_settings({
            SETTING_TRANSCRIPTS: False,
            SETTING_CLOSURE_DETECTION: True,
            SETTING_RATING: False,
        })
        assert settings[SETTING_TRANSCRIPTS] is False
        assert settings[SETTING_CLOSURE_DETECTION] is True
        assert settings[SETTING_RATING] is False

    def test_retention_is_clamped_and_defaults_to_forever(self):
        assert normalize_settings({})[SETTING_RETENTION] == 0
        assert normalize_settings({SETTING_RETENTION: -5})[SETTING_RETENTION] == 0
        assert normalize_settings(
            {SETTING_RETENTION: 99999})[SETTING_RETENTION] == MAX_RETENTION_DAYS

    def test_settings_written_flat_by_an_older_dashboard_still_load(self):
        config = normalize_config({'panels': [], SETTING_TRANSCRIPTS: False,
                                   SETTING_LOG_CHANNEL: "123"})
        assert config['settings'][SETTING_TRANSCRIPTS] is False
        assert config['settings'][SETTING_LOG_CHANNEL] == 123

    def test_stats_is_a_real_permission(self):
        assert PERM_STATS in TICKET_PERMISSIONS


# =========================================================================== #
# The suggestion card — suggesting is not granting
# =========================================================================== #
class TestClosureSuggestionAuthorisation:
    """The requirement in one place: the card offers, the service decides.

    Whatever the card says, the only thing that can close a ticket is
    ``close_ticket``, and the only thing that reaches it is somebody holding
    ``close``. A member without it who clicks the very same button gets a
    close *request* instead.
    """

    def _interaction(self, user, channel_id=999):
        interaction = MagicMock()
        interaction.user = user
        interaction.channel = SimpleNamespace(id=channel_id)
        interaction.client = MagicMock()
        interaction.response.is_done.return_value = False
        interaction.response.defer = AsyncMock()
        interaction.response.send_message = AsyncMock()
        interaction.response.edit_message = AsyncMock()
        interaction.followup.send = AsyncMock()
        return interaction

    def _service(self):
        service = MagicMock()
        service.close_ticket = AsyncMock()
        service.request_close = AsyncMock()
        return service

    @pytest.fixture
    def patched(self, monkeypatch):
        """Wire the view's two lookups to stubs, keeping its real logic."""
        def install(service, ticket, category, permissions):
            async def fake_resolve(_interaction):
                return service, ticket, {}, category
            monkeypatch.setattr("utils.ticket_views._resolve", fake_resolve)
            monkeypatch.setattr("utils.ticket_views.member_permissions",
                                lambda *_a, **_k: set(permissions))
            monkeypatch.setattr("utils.ticket_views.send_success", AsyncMock())
            monkeypatch.setattr("utils.ticket_views.send_error", AsyncMock())
        return install

    async def test_a_member_without_close_gets_a_request_never_a_closure(self, patched):
        from modules.tickets import PERM_VIEW
        from utils.ticket_views import TicketClosureSuggestionView

        service = self._service()
        ticket = make_ticket(owner_id=1)
        patched(service, ticket, {}, {PERM_VIEW})

        member = SimpleNamespace(id=1)
        await TicketClosureSuggestionView("fr").on_resolve(self._interaction(member))

        service.close_ticket.assert_not_awaited()
        service.request_close.assert_awaited_once()

    async def test_a_staffer_with_close_closes_it(self, patched):
        from modules.tickets import PERM_CLOSE, PERM_VIEW
        from utils.ticket_views import TicketClosureSuggestionView

        service = self._service()
        ticket = make_ticket(owner_id=1)
        patched(service, ticket, {}, {PERM_VIEW, PERM_CLOSE})

        staffer = SimpleNamespace(id=99)     # not the opener: no rating prompt
        await TicketClosureSuggestionView("fr").on_resolve(self._interaction(staffer))

        service.close_ticket.assert_awaited_once()
        service.request_close.assert_not_awaited()

    async def test_somebody_with_no_permission_at_all_gets_neither(self, patched):
        from utils.ticket_views import TicketClosureSuggestionView

        service = self._service()
        patched(service, make_ticket(), {}, set())

        await TicketClosureSuggestionView("fr").on_resolve(
            self._interaction(SimpleNamespace(id=5)))

        service.close_ticket.assert_not_awaited()
        service.request_close.assert_not_awaited()

    async def test_dismissing_needs_at_least_view(self, patched):
        from utils.ticket_views import TicketClosureSuggestionView

        service = self._service()
        patched(service, make_ticket(), {}, set())
        interaction = self._interaction(SimpleNamespace(id=5))
        await TicketClosureSuggestionView("fr").on_dismiss(interaction)
        interaction.response.edit_message.assert_not_awaited()

    @pytest.mark.parametrize("locale", LOCALES)
    def test_the_card_is_translated_everywhere(self, locale):
        from utils.ticket_views import build_closure_suggestion
        view = build_closure_suggestion(locale=locale)
        assert view is not None


# =========================================================================== #
# Membership changes
# =========================================================================== #
class TestMembershipChanges:
    """Leaving offers a closure; coming back restores access. Neither is a
    setting, and neither closes or opens anything by itself."""

    def _service(self, tickets):
        from services.ticket_service import TicketService
        db = MagicMock()
        db.list_member_open_tickets = AsyncMock(return_value=tickets)
        bot = MagicMock()
        bot.db = db
        service = TicketService(bot)
        service.ticket_locale = AsyncMock(return_value="fr")
        return service, db

    async def test_a_departure_announces_only_in_the_tickets_they_opened(self):
        service, db = self._service([make_ticket(), make_ticket(channel_id=1000)])
        channel = MagicMock()
        channel.send = AsyncMock()
        guild = MagicMock()
        guild.id = 10
        guild.get_channel.return_value = channel

        import discord
        channel.__class__ = discord.TextChannel
        announced = await service.announce_owner_left(
            guild, SimpleNamespace(id=1, mention="<@1>", __str__=lambda _s: "u#1"))

        assert announced == 2
        assert db.list_member_open_tickets.await_args.kwargs['owned_only'] is True

    async def test_a_departure_never_closes_anything_on_its_own(self):
        service, _db = self._service([make_ticket()])
        service.close_ticket = AsyncMock()
        guild = MagicMock()
        guild.id = 10
        guild.get_channel.return_value = None      # channel gone: nothing to do
        await service.announce_owner_left(guild, SimpleNamespace(id=1))
        service.close_ticket.assert_not_awaited()

    async def test_returning_restores_access_to_open_tickets(self):
        import discord
        service, db = self._service([make_ticket(), make_ticket(channel_id=1000)])
        service.resolve = AsyncMock(return_value=(make_ticket(), {}, {'name': "S"}))
        service.sync_permissions = AsyncMock()

        channel = MagicMock()
        channel.__class__ = discord.TextChannel
        member = MagicMock()
        member.id = 1
        member.guild.id = 10
        member.guild.get_channel.return_value = channel

        assert await service.restore_member_access(member) == 2
        assert service.sync_permissions.await_count == 2
        # Both the tickets they opened and the ones they were added to.
        assert 'owned_only' not in db.list_member_open_tickets.await_args.kwargs

    async def test_a_ticket_whose_category_is_gone_is_skipped_not_fatal(self):
        import discord
        from services.ticket_service import TicketError
        service, _db = self._service([make_ticket()])
        service.resolve = AsyncMock(side_effect=TicketError('x'))
        service.sync_permissions = AsyncMock()

        channel = MagicMock()
        channel.__class__ = discord.TextChannel
        member = MagicMock()
        member.id = 1
        member.guild.get_channel.return_value = channel

        assert await service.restore_member_access(member) == 0
        service.sync_permissions.assert_not_awaited()

    @pytest.mark.parametrize("locale", LOCALES)
    def test_the_departure_card_is_translated_everywhere(self, locale):
        from utils.ticket_views import build_owner_left_card
        view = build_owner_left_card(
            SimpleNamespace(id=1, mention="<@1>", __str__=lambda _s: "u#1"),
            locale=locale)
        assert view is not None
