"""Bump prospects: detection, one card per server, follow-up, card, i18n.

See docs/features/BUMP_PROSPECTS.md.
"""

import datetime
import json
import re
import string

import pytest

import config
from bumpreminder import BUMP_BOTS, bot_by_key
from bumpreminder.prospects import (
    REMINDER_BOTS, WATCHED_IDS, is_watched, prospect_directories,
)
from services import bump_prospect_service as svc
from utils import bump_prospect_views as views

DISBOARD = bot_by_key("disboard").app_id
FRENCHGG = bot_by_key("frenchgg").app_id
REMINDER = next(iter(REMINDER_BOTS))


# --------------------------------------------------------------------------- #
# Pure detection
# --------------------------------------------------------------------------- #

def test_every_directory_and_reminder_is_watched():
    for spec in BUMP_BOTS:
        assert is_watched(spec.app_id)
    for app_id in REMINDER_BOTS:
        assert is_watched(app_id)
    assert not is_watched(1234)
    # One gateway query covers them all.
    assert len(WATCHED_IDS) <= 100


def test_no_directory_is_no_prospect():
    assert prospect_directories([]) is None
    assert prospect_directories([1234]) is None


def test_a_directory_without_reminder_is_a_prospect():
    found = prospect_directories([FRENCHGG, DISBOARD])
    # Registry order, not input order.
    assert [spec.key for spec in found] == ["disboard", "frenchgg"]


@pytest.mark.parametrize("reminder", sorted(REMINDER_BOTS))
def test_a_reminder_bot_covers_the_server(reminder):
    assert prospect_directories([DISBOARD, reminder]) is None


def test_the_three_requested_reminder_bots_are_listed():
    assert REMINDER_BOTS == {478321260481478677, 1441619898756501574, 735147814878969968}


# --------------------------------------------------------------------------- #
# Service, against fakes
# --------------------------------------------------------------------------- #

class FakeMessage:
    _next = 1000

    def __init__(self):
        FakeMessage._next += 1
        self.id = FakeMessage._next
        self.edits = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)


class FakeChannel:
    def __init__(self, channel_id):
        self.id = channel_id
        self.sent = []
        self.messages = {}

    async def send(self, **kwargs):
        message = FakeMessage()
        self.sent.append(kwargs)
        self.messages[message.id] = message
        return message

    async def fetch_message(self, message_id):
        return self.messages[message_id]


class FakeGuild:
    def __init__(self, guild_id, member_ids, name="Bumpers"):
        self.id = guild_id
        self.name = name
        self.unavailable = False
        self.chunked = True
        self.owner_id = 42
        self.member_count = 1234
        self._members = set(member_ids)

    def get_member(self, user_id):
        return object() if user_id in self._members else None


class FakeDB:
    def __init__(self):
        self.rows = {}

    async def get_bump_prospect(self, guild_id):
        row = self.rows.get(guild_id)
        return dict(row) if row else None

    async def known_bump_prospect_guilds(self):
        return set(self.rows)

    async def list_active_bump_prospects(self):
        return [dict(r) for r in self.rows.values() if r["status"] in ("open", "claimed")]

    async def create_bump_prospect(self, guild_id, guild_name, directories):
        if guild_id in self.rows:
            return None
        self.rows[guild_id] = {
            "guild_id": guild_id, "guild_name": guild_name,
            "directories": list(directories), "status": "open",
            "claimed_by": None, "claimed_at": None, "closed_by": None,
            "closed_at": None, "channel_id": None, "message_id": None,
            "created_at": datetime.datetime.now(datetime.timezone.utc),
            "updated_at": None,
        }
        return dict(self.rows[guild_id])

    async def set_bump_prospect_card(self, guild_id, channel_id, message_id):
        self.rows[guild_id].update(channel_id=channel_id, message_id=message_id)

    async def settle_bump_prospect(self, guild_id, status):
        row = self.rows.get(guild_id)
        if not row or row["status"] not in ("open", "claimed"):
            return None
        row["status"] = status
        return dict(row)

    async def claim_bump_prospect(self, guild_id, staff_id):
        row = self.rows.get(guild_id)
        if not row or row["status"] != "open":
            return None
        row.update(status="claimed", claimed_by=staff_id)
        return dict(row)


class FakeModules:
    def __init__(self):
        self.states = {}

    async def get_module_state(self, guild_id, module_id):
        assert module_id == "bump_reminder"
        return self.states.get(guild_id)


class FakeBot:
    def __init__(self, guilds):
        self.db = FakeDB()
        self.module_manager = FakeModules()
        self.channel = FakeChannel(config.MODDY_BUMP_PROSPECT_CHANNEL_ID)
        self.guilds = list(guilds)

    def get_guild(self, guild_id):
        return next((g for g in self.guilds if g.id == guild_id), None)

    def get_channel(self, channel_id):
        return self.channel if channel_id == self.channel.id else None


class FakeUser:
    id = 77


@pytest.fixture
def bot():
    return FakeBot([FakeGuild(1, [DISBOARD])])


@pytest.fixture
def service(bot):
    return svc.BumpProspectService(bot)


def test_the_channel_defaults_to_the_requested_one():
    assert config.MODDY_BUMP_PROSPECT_CHANNEL_ID == 1564692382107041894


async def test_a_new_prospect_gets_exactly_one_card(bot, service):
    guild = bot.guilds[0]
    assert await service.scan_guild(guild) == svc.OUTCOME_NEW
    assert len(bot.channel.sent) == 1
    row = bot.db.rows[guild.id]
    assert row["directories"] == ["disboard"]
    assert row["message_id"] is not None
    # Rescanning never posts a second card.
    assert await service.scan_guild(guild) == svc.OUTCOME_KNOWN
    assert len(bot.channel.sent) == 1


async def test_a_reminder_bot_means_no_card(bot, service):
    guild = FakeGuild(2, [DISBOARD, REMINDER])
    assert await service.scan_guild(guild) == svc.OUTCOME_NONE
    assert not bot.channel.sent and not bot.db.rows


@pytest.mark.parametrize("state", ["active", "simulation"])
async def test_moddys_running_module_means_no_card(bot, service, state):
    bot.module_manager.states[1] = state
    assert await service.scan_guild(bot.guilds[0]) == svc.OUTCOME_NONE
    assert not bot.channel.sent


async def test_a_paused_module_still_makes_a_prospect(bot, service):
    bot.module_manager.states[1] = "paused"
    assert await service.scan_guild(bot.guilds[0]) == svc.OUTCOME_NEW


async def test_the_team_guild_is_never_a_prospect(bot, service):
    guild = FakeGuild(config.MODDY_TEAM_GUILD_ID, [DISBOARD])
    assert await service.scan_guild(guild) == svc.OUTCOME_SKIPPED


async def test_enabling_the_module_converts_the_open_card(bot, service):
    await service.scan_guild(bot.guilds[0])
    bot.module_manager.states[1] = "active"
    assert await service.scan_guild(bot.guilds[0]) == svc.OUTCOME_CONVERTED
    assert bot.db.rows[1]["status"] == "converted"
    message = next(iter(bot.channel.messages.values()))
    assert message.edits, "the card is refreshed in place"


async def test_follow_up_settles_converted_and_left(bot, service):
    other = FakeGuild(3, [FRENCHGG])
    bot.guilds.append(other)
    await service.scan_guild(bot.guilds[0])
    await service.scan_guild(other)
    bot.module_manager.states[1] = "active"
    bot.guilds.remove(other)
    await service.follow_up()
    assert bot.db.rows[1]["status"] == "converted"
    assert bot.db.rows[3]["status"] == "left"


async def test_a_closed_row_is_not_settled(bot, service):
    await service.scan_guild(bot.guilds[0])
    bot.db.rows[1]["status"] = "closed"
    bot.module_manager.states[1] = "active"
    assert await service.scan_guild(bot.guilds[0]) == svc.OUTCOME_KNOWN
    assert bot.db.rows[1]["status"] == "closed"


async def test_claim_refreshes_the_card(bot, service):
    await service.scan_guild(bot.guilds[0])
    assert await service.claim(1, FakeUser()) is not None
    assert bot.db.rows[1]["claimed_by"] == 77
    # Second claim loses.
    assert await service.claim(1, FakeUser()) is None


def test_enqueue_dedupes_and_prioritises(service):
    assert service.enqueue([1, 2, 3]) == 3
    assert service.enqueue([2, 3]) == 0
    service.enqueue([9], priority=True)
    assert list(service._queue) == [9, 1, 2, 3]


async def test_startup_only_queues_unknown_servers(bot, service):
    bot.guilds.append(FakeGuild(5, []))
    await service.scan_guild(bot.guilds[0])
    assert await service.enqueue_unknown() == 1
    assert list(service._queue) == [5]


def test_manual_scan_queues_every_server_and_tracks_a_run(bot, service):
    bot.guilds.append(FakeGuild(5, []))
    assert service.scan_all(requested_by=77) == 2
    assert service.manual_run_active
    assert service.pending == 2


# --------------------------------------------------------------------------- #
# Card
# --------------------------------------------------------------------------- #

def _row(status, **overrides):
    row = {
        "guild_id": 123456789012345678, "guild_name": "Bumpers",
        "directories": ["disboard", "frenchgg"], "status": status,
        "claimed_by": None, "closed_by": None,
        "created_at": datetime.datetime(2026, 10, 27, tzinfo=datetime.timezone.utc),
    }
    row.update(overrides)
    return row


def _custom_ids(view):
    ids = []
    for row in view.children:
        for child in getattr(row, "children", []):
            item = getattr(child, "item", child)
            if getattr(item, "custom_id", None):
                ids.append(item.custom_id)
    return ids


@pytest.mark.parametrize("status, actions", [
    ("open", ["invite", "claim", "close"]),
    ("claimed", ["invite", "claim", "close"]),
    ("closed", ["reopen"]),
    ("converted", []),
    ("left", []),
])
def test_card_buttons_follow_the_status(status, actions):
    view = views.build_prospect_card(prospect=_row(status, claimed_by=7, closed_by=8))
    assert [cid.split(":")[2] for cid in _custom_ids(view)] == actions


def test_card_renders_without_the_guild():
    view = views.build_prospect_card(prospect=_row("left", guild_name=None, created_at=None))
    assert view.children


def test_directories_line_names_each_directory():
    line = views.directories_line(["disboard", "retired"])
    assert "DISBOARD" in line and "`retired`" in line


@pytest.mark.parametrize("item, cls", [
    (views.ProspectInviteButton(123456789012345678), views.ProspectInviteButton),
    (views.ProspectClaimButton(123456789012345678), views.ProspectClaimButton),
    (views.ProspectClaimButton(123456789012345678, claimed=True), views.ProspectClaimButton),
    (views.ProspectCloseButton(123456789012345678), views.ProspectCloseButton),
    (views.ProspectReopenButton(123456789012345678), views.ProspectReopenButton),
])
def test_custom_ids_match_their_template(item, cls):
    assert cls.__discord_ui_compiled_template__.fullmatch(item.item.custom_id)


def test_scan_summary_renders():
    view = views.build_scan_summary(
        {"scanned": 10, "new": 2, "converted": 1, "skipped": 0, "requested_by": 7},
        duration=600)
    assert view.children


# --------------------------------------------------------------------------- #
# i18n
# --------------------------------------------------------------------------- #

LOCALES = ["fr", "en-US", "es-ES", "pt-BR", "de"]


def _flatten(block, prefix=""):
    out = {}
    for key, value in block.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, path + "."))
        else:
            out[path] = value
    return out


def _block(locale):
    with open(f"locales/{locale}.json", encoding="utf-8") as fh:
        return _flatten(json.load(fh)["bump_prospects"])


def _fields(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


@pytest.mark.parametrize("locale", LOCALES[1:])
def test_locales_stay_in_step(locale):
    reference = _block("fr")
    block = _block(locale)
    assert block.keys() == reference.keys()
    for key, text in block.items():
        assert _fields(text) == _fields(reference[key]), (locale, key)


@pytest.mark.parametrize("locale", LOCALES)
def test_button_labels_fit(locale):
    for key, text in _block(locale).items():
        if key.startswith("buttons."):
            assert len(text) <= 80, (locale, key)


def test_staff_node_exists():
    from utils.staff_role_permissions import COMMUNICATION_PERMISSIONS, get_permission_label
    assert views.STAFF_NODE in COMMUNICATION_PERMISSIONS
    assert get_permission_label(views.STAFF_NODE) != views.STAFF_NODE


def test_the_rescan_command_is_registered():
    from staff.commands.dev.bumpscan import BumpScanCommand
    assert BumpScanCommand.name == "bumpscan"
    assert re.fullmatch(r"[a-z]+", BumpScanCommand.name)
