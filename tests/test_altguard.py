"""Tests for the AltGuard verification gate.

Nothing here talks to Discord, Redis or Postgres: the pieces under test are the
ones that decide *what* to do — parse a verdict, hold a member back, move them
across the gate, refuse to replay a message twice.

    pytest tests/test_altguard.py -q
"""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from db.repositories.altguard import (
    SOURCE_SERVICE, STATUS_BLOCKED, STATUS_PENDING, STATUS_VERIFIED,
    VERDICT_TO_STATUS,
)
from modules.altguard import AltGuardModule, DEFAULT_PANEL_LOCALE
from services.altguard_client import (
    MEMBERSHIP_ACTIVE, MEMBERSHIP_CHANNEL, AltGuardClient, parse_verdict,
)


# --------------------------------------------------------------- fixtures

class FakeRole:
    def __init__(self, role_id, name="role"):
        self.id = role_id
        self.name = name

    def __repr__(self):
        return f"<FakeRole {self.name}>"


class FakeMember:
    """Just enough of ``discord.Member`` for the role plumbing."""

    def __init__(self, member_id=42, roles=(), bot=False):
        self.id = member_id
        self.bot = bot
        self.roles = list(roles)
        self.added = []
        self.removed = []
        self.dms = []

    async def send(self, *, view=None, **kwargs):
        self.dms.append(view)

    async def add_roles(self, *roles, reason=None):
        self.added.extend(roles)
        self.roles.extend(r for r in roles if r not in self.roles)

    async def remove_roles(self, *roles, reason=None):
        self.removed.extend(roles)
        self.roles = [r for r in self.roles if r not in roles]


class FakeGuild:
    def __init__(self, guild_id=1, roles=(), name="Test Server"):
        self.id = guild_id
        self.name = name
        self._roles = {role.id: role for role in roles}
        self.channels = {}
        self.members = {}

    def get_member(self, user_id):
        return self.members.get(user_id)

    def get_role(self, role_id):
        return self._roles.get(role_id)

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)


class FakeDB:
    """In-memory stand-in for the AltGuard repository methods."""

    def __init__(self):
        self.members = {}
        self.verifications = {}

    async def get_altguard_member(self, guild_id, user_id):
        return self.members.get((guild_id, user_id))

    async def is_altguard_verified(self, guild_id, user_id):
        row = self.members.get((guild_id, user_id))
        return bool(row and row["status"] == STATUS_VERIFIED)

    async def set_altguard_member_status(self, guild_id, user_id, status, *,
                                         source=None, decided_by=None,
                                         verification_id=None):
        self.members[(guild_id, user_id)] = {
            "status": status, "source": source,
            "decided_by": decided_by, "verification_id": verification_id,
        }

    async def record_altguard_verification(self, *, verification_id, **kwargs):
        # Mirrors the repository: False means "already seen".
        if verification_id in self.verifications:
            return False
        self.verifications[verification_id] = kwargs
        return True

    async def update_guild_data(self, guild_id, path, value):
        return None


class FakeBot:
    def __init__(self, guild=None, db=None):
        self._guild = guild
        self.db = db
        self.module_manager = None
        from notifications import NotificationService
        self.notifications = NotificationService(self)

    def get_guild(self, guild_id):
        return self._guild if self._guild and self._guild.id == guild_id else None


def build_module(*, unverified=None, verified=None, db=None, guild=None):
    unverified = unverified if unverified is not None else FakeRole(10, "unverified")
    verified = verified if verified is not None else FakeRole(11, "verified")
    roles = [role for role in (unverified, verified) if role]
    guild = guild or FakeGuild(1, roles)
    bot = FakeBot(guild=guild, db=db if db is not None else FakeDB())
    module = AltGuardModule(bot, guild.id)
    asyncio.run(module.load_config({
        "channel_id": 100,
        "unverified_role_id": unverified.id if unverified else None,
        "verified_role_id": verified.id if verified else None,
        "log_channel_id": None,
        "panel_locale": "fr",
        "message_id": None,
    }))
    return module


# ----------------------------------------------------------- verdict parsing

def _payload(**overrides):
    payload = {
        "verification_id": "uuid-1",
        "guild_id": 123456789,
        "discord_user_id": 987654321,
        "verdict": "passed",
        "score": 62,
        "reasons": ["cookie_match", "gpu_match"],
        "enforced": True,
    }
    payload.update(overrides)
    return payload


def test_parse_verdict_accepts_the_documented_payload():
    parsed = parse_verdict(_payload())
    assert parsed == {
        "enforced_missing": False,
        "verification_id": "uuid-1",
        "guild_id": 123456789,
        "user_id": 987654321,
        "verdict": "passed",
        "score": 62,
        "reasons": ["cookie_match", "gpu_match"],
        "matches": [],
        "enforced": True,
    }


def test_parse_verdict_reads_matches():
    payload = _payload(verdict="blocked", matches=[
        {"discord_user_id": 456789123, "score": 62, "reasons": ["cookie_match", "gpu_match"]},
    ])
    parsed = parse_verdict(payload)
    assert parsed["matches"] == [
        {"user_id": 456789123, "score": 62, "reasons": ["cookie_match", "gpu_match"]},
    ]


def test_parse_verdict_defaults_matches_when_absent():
    """A bot deployed ahead of the service must not choke on a missing field."""
    assert parse_verdict(_payload())["matches"] == []


def test_parse_verdict_drops_malformed_matches():
    payload = _payload(matches=[
        {"discord_user_id": "not-an-id"},
        "not-a-dict",
        {"discord_user_id": 111, "score": "n/a", "reasons": "not-a-list"},
    ])
    parsed = parse_verdict(payload)
    assert parsed["matches"] == [{"user_id": 111, "score": None, "reasons": []}]


def test_parse_verdict_defaults_to_shadow_mode():
    """A payload without ``enforced`` must log, never sanction."""
    parsed = parse_verdict(_payload(enforced=None))
    assert parsed["enforced"] is False

    payload = _payload()
    del payload["enforced"]
    assert parse_verdict(payload)["enforced"] is False


@pytest.mark.parametrize("overrides", [
    {"verification_id": None},          # no idempotency key
    {"verification_id": ""},
    {"verdict": "maybe"},               # not one of passed/flagged/blocked
    {"verdict": None},
    {"guild_id": "not-an-id"},
    {"discord_user_id": None},
])
def test_parse_verdict_rejects_unusable_payloads(overrides):
    assert parse_verdict(_payload(**overrides)) is None


def test_parse_verdict_tolerates_soft_fields():
    parsed = parse_verdict(_payload(score="not-a-number", reasons="cookie_match"))
    assert parsed["score"] is None
    assert parsed["reasons"] == []

    # Ids arrive as strings on the wire; they must come back as ints.
    parsed = parse_verdict(_payload(guild_id="123", discord_user_id="456"))
    assert (parsed["guild_id"], parsed["user_id"]) == (123, 456)


# ------------------------------------------------------- membership publishing

class FakeRedis:
    def __init__(self):
        self.published = []

    async def publish(self, channel, payload):
        self.published.append((channel, payload))


def test_publish_membership_matches_the_service_contract():
    redis = FakeRedis()
    client = AltGuardClient(SimpleNamespace(redis=redis))
    moment = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)

    assert asyncio.run(client.publish_membership(
        guild_id=123, discord_user_id=456,
        state=MEMBERSHIP_ACTIVE, occurred_at=moment,
    )) is True

    channel, raw = redis.published[0]
    assert channel == MEMBERSHIP_CHANNEL
    assert json.loads(raw) == {
        "event": "membership",
        "guild_id": 123,
        "discord_user_id": 456,
        "state": "active",
        "occurred_at": "2026-08-18T10:00:00Z",
    }


def test_publish_membership_refuses_an_unknown_state():
    client = AltGuardClient(SimpleNamespace(redis=FakeRedis()))
    with pytest.raises(ValueError):
        asyncio.run(client.publish_membership(
            guild_id=1, discord_user_id=2, state="vanished"))


def test_publish_membership_without_redis_is_not_an_error():
    client = AltGuardClient(SimpleNamespace(redis=None))
    assert asyncio.run(client.publish_membership(
        guild_id=1, discord_user_id=2, state=MEMBERSHIP_ACTIVE)) is False


# ------------------------------------------------------------ configuration

def test_module_needs_channel_and_both_roles_to_be_enabled():
    module = build_module()
    assert module.enabled is True

    for missing in ("channel_id", "unverified_role_id", "verified_role_id"):
        config = {
            "channel_id": 100, "unverified_role_id": 10,
            "verified_role_id": 11, "panel_locale": "fr",
        }
        config[missing] = None
        asyncio.run(module.load_config(config))
        assert module.enabled is False, missing


def test_unsupported_panel_language_falls_back():
    module = build_module()
    asyncio.run(module.load_config({
        "channel_id": 100, "unverified_role_id": 10, "verified_role_id": 11,
        "panel_locale": "kl",
    }))
    assert module.panel_locale == DEFAULT_PANEL_LOCALE


# -------------------------------------------------------------- gate roles

def test_gate_roles_open_and_close():
    unverified, verified = FakeRole(10, "unverified"), FakeRole(11, "verified")
    module = build_module(unverified=unverified, verified=verified)
    member = FakeMember(roles=[unverified])

    asyncio.run(module._apply_gate_roles(member, verified=True, reason="test"))
    assert member.added == [verified]
    assert member.removed == [unverified]

    asyncio.run(module._apply_gate_roles(member, verified=False, reason="test"))
    assert member.added[-1] is unverified
    assert member.removed[-1] is verified


def test_gate_roles_are_idempotent():
    """Replaying the same decision must not touch the member again."""
    unverified, verified = FakeRole(10), FakeRole(11)
    module = build_module(unverified=unverified, verified=verified)
    member = FakeMember(roles=[verified])

    asyncio.run(module._apply_gate_roles(member, verified=True, reason="test"))
    assert member.added == [] and member.removed == []


def test_join_gates_a_human_and_records_the_pending_state():
    db = FakeDB()
    module = build_module(db=db)
    member = FakeMember(member_id=42)

    asyncio.run(module.on_member_join(member))

    assert db.members[(1, 42)]["status"] == STATUS_PENDING
    assert db.members[(1, 42)]["source"] == SOURCE_SERVICE
    assert [role.id for role in member.added] == [10]


def test_join_of_an_already_verified_member_skips_the_gate():
    db = FakeDB()
    asyncio.run(db.set_altguard_member_status(1, 42, STATUS_VERIFIED, source=SOURCE_SERVICE))
    module = build_module(db=db)
    member = FakeMember(member_id=42)

    asyncio.run(module.on_member_join(member))
    assert [role.id for role in member.added] == [11]


def test_bots_are_never_gated():
    db = FakeDB()
    module = build_module(db=db)
    member = FakeMember(member_id=7, bot=True)

    asyncio.run(module.on_member_join(member))
    assert member.added == [] and db.members == {}


# ----------------------------------------------------------------- verdicts

def test_enforced_pass_opens_the_gate_and_runs_auto_role():
    db = FakeDB()
    unverified, verified = FakeRole(10, "unverified"), FakeRole(11, "verified")
    module = build_module(unverified=unverified, verified=verified, db=db)
    member = FakeMember(member_id=42, roles=[unverified])
    module.bot._guild.get_member = lambda user_id, _m=member: _m if user_id == _m.id else None

    handed_over = []
    module._run_auto_role = lambda m: handed_over.append(m) or asyncio.sleep(0)

    asyncio.run(module.apply_verdict({
        "verification_id": "uuid-1", "guild_id": 1, "user_id": 42,
        "verdict": "passed", "score": 3, "reasons": [], "enforced": True,
    }))

    assert db.members[(1, 42)]["status"] == VERDICT_TO_STATUS["passed"] == STATUS_VERIFIED
    assert [role.id for role in member.added] == [11]
    assert handed_over == [member]


def test_shadow_mode_changes_nothing():
    """``enforced=False``: log only — no role, no state, no auto role."""
    db = FakeDB()
    unverified = FakeRole(10, "unverified")
    module = build_module(unverified=unverified, db=db)
    member = FakeMember(member_id=42, roles=[unverified])
    module.bot._guild.get_member = lambda user_id, _m=member: _m if user_id == _m.id else None
    module._run_auto_role = lambda m: pytest.fail("auto role ran in shadow mode")

    asyncio.run(module.apply_verdict({
        "verification_id": "uuid-2", "guild_id": 1, "user_id": 42,
        "verdict": "blocked", "score": 91, "reasons": ["cookie_match"], "enforced": False,
    }))

    assert db.members == {}
    assert member.added == [] and member.removed == []


def test_blocked_member_stays_behind_the_gate():
    db = FakeDB()
    unverified, verified = FakeRole(10, "unverified"), FakeRole(11, "verified")
    module = build_module(unverified=unverified, verified=verified, db=db)
    member = FakeMember(member_id=42, roles=[verified])
    module.bot._guild.get_member = lambda user_id, _m=member: _m if user_id == _m.id else None

    asyncio.run(module.apply_verdict({
        "verification_id": "uuid-3", "guild_id": 1, "user_id": 42,
        "verdict": "blocked", "score": 91, "reasons": ["cookie_match"], "enforced": True,
    }))

    assert db.members[(1, 42)]["status"] == STATUS_BLOCKED
    assert [role.id for role in member.added] == [10]
    assert [role.id for role in member.removed] == [11]


def test_a_replayed_verdict_is_recorded_once():
    db = FakeDB()
    first = asyncio.run(db.record_altguard_verification(
        verification_id="uuid-1", guild_id=1, user_id=42, verdict="passed",
        score=1, reasons=[], enforced=True))
    second = asyncio.run(db.record_altguard_verification(
        verification_id="uuid-1", guild_id=1, user_id=42, verdict="passed",
        score=1, reasons=[], enforced=True))
    assert (first, second) == (True, False)


# -------------------------------------------------------- auto role hand-off

def _auto_role_module(altguard_module, *, lookup_error=False):
    from modules.auto_role import AutoRoleModule

    class FakeModuleManager:
        async def get_module_instance(self, guild_id, module_id):
            if lookup_error:
                raise RuntimeError("database down")
            return altguard_module if module_id == "altguard" else None

    bot = FakeBot(db=FakeDB())
    bot.module_manager = FakeModuleManager()
    return AutoRoleModule(bot, 1)


def test_auto_role_holds_back_an_unverified_human():
    altguard = build_module()
    auto_role = _auto_role_module(altguard)
    assert asyncio.run(auto_role._altguard_holds_back(FakeMember(member_id=42))) is True


def test_auto_role_runs_for_a_verified_human():
    db = FakeDB()
    asyncio.run(db.set_altguard_member_status(1, 42, STATUS_VERIFIED, source=SOURCE_SERVICE))
    altguard = build_module(db=db)
    auto_role = _auto_role_module(altguard)
    assert asyncio.run(auto_role._altguard_holds_back(FakeMember(member_id=42))) is False


def test_auto_role_is_never_held_back_for_a_bot():
    altguard = build_module()
    auto_role = _auto_role_module(altguard)
    assert asyncio.run(auto_role._altguard_holds_back(FakeMember(member_id=7, bot=True))) is False


def test_auto_role_runs_when_altguard_is_off():
    altguard = build_module()
    altguard.enabled = False
    auto_role = _auto_role_module(altguard)
    assert asyncio.run(auto_role._altguard_holds_back(FakeMember(member_id=42))) is False


def test_auto_role_fails_closed_when_the_lookup_breaks():
    auto_role = _auto_role_module(build_module(), lookup_error=True)
    assert asyncio.run(auto_role._altguard_holds_back(FakeMember(member_id=42))) is True


# ------------------------------------------------------------------- cards

def test_the_link_card_never_leaks_the_score():
    """Score and signals belong to the log channel, not to the member."""
    from utils.altguard_views import build_link_card, build_log_card

    link = build_link_card("en-US", url="https://discord.com/oauth2/authorize?x=1",
                           expires_at="2026-08-18T10:10:00Z")
    rendered = json.dumps(link.to_components())
    assert "62" not in rendered and "cookie_match" not in rendered

    log = build_log_card(locale="en-US", kind="verdict", user_id=42, verdict="blocked",
                         score=62, reasons=["cookie_match"], enforced=True,
                         verification_id="uuid-1")
    rendered = json.dumps(log.to_components())
    assert "62" in rendered and "cookie_match" in rendered and "uuid-1" in rendered


def test_the_log_card_shows_matches():
    """The audit log is the reason `matches` exists: a moderator undoing a
    kick needs to see which account it matched with."""
    from utils.altguard_views import build_log_card

    log = build_log_card(locale="en-US", kind="verdict", user_id=42, verdict="blocked",
                         score=62, reasons=["cookie_match"],
                         matches=[{"user_id": 43, "score": 62,
                                  "reasons": ["cookie_match", "gpu_match"]}],
                         enforced=True, verification_id="uuid-1")
    rendered = json.dumps(log.to_components())
    assert "43" in rendered and "gpu_match" in rendered


def test_the_log_card_omits_matches_on_a_pass():
    from utils.altguard_views import build_log_card

    log = build_log_card(locale="en-US", kind="verdict", user_id=42, verdict="passed",
                         score=3, reasons=[], matches=[], enforced=True,
                         verification_id="uuid-1")
    rendered = json.dumps(log.to_components())
    assert "Linked accounts" not in rendered


def test_the_consent_modal_requires_both_boxes():
    from utils.altguard_views import AltGuardConsentModal

    modal = AltGuardConsentModal(locale="en-US")
    group = modal.agreement.component
    assert group.required is True
    assert group.min_values == 2 and len(group.options) == 2


# ---------------------------------------------- member DM + already verified

def test_a_verdict_is_announced_to_the_member():
    """The member cannot read the log channel: the DM is their only feedback."""
    db = FakeDB()
    unverified, verified = FakeRole(10, "unverified"), FakeRole(11, "verified")
    module = build_module(unverified=unverified, verified=verified, db=db)
    member = FakeMember(member_id=42, roles=[unverified])
    module.bot._guild.get_member = lambda user_id, _m=member: _m if user_id == _m.id else None
    module._run_auto_role = lambda m: asyncio.sleep(0)

    asyncio.run(module.apply_verdict({
        "verification_id": "uuid-dm", "guild_id": 1, "user_id": 42,
        "verdict": "passed", "score": 3, "reasons": [], "enforced": True,
    }))

    # Rendered in the guild's panel language (the fixture's is "fr"): a DM
    # carries no interaction locale, so the member's own language is unknown.
    assert len(member.dms) == 1
    assert "Vérification validée" in json.dumps(member.dms[0].to_components(),
                                                ensure_ascii=False)


def test_the_member_dm_never_carries_the_score():
    from utils.altguard_views import build_member_dm

    for kind in ("passed", "flagged", "blocked"):
        card = build_member_dm(locale="en-US", kind=kind, guild_name="Test")
        rendered = json.dumps(card.to_components())
        assert "cookie_match" not in rendered and "62" not in rendered

    assert build_member_dm(locale="en-US", kind="unknown-kind") is None


def test_shadow_mode_does_not_dm_the_member():
    """Nothing was applied, so there is nothing to announce."""
    db = FakeDB()
    module = build_module(unverified=FakeRole(10, "unverified"), db=db)
    member = FakeMember(member_id=42)
    module.bot._guild.get_member = lambda user_id, _m=member: _m if user_id == _m.id else None

    asyncio.run(module.apply_verdict({
        "verification_id": "uuid-shadow-dm", "guild_id": 1, "user_id": 42,
        "verdict": "blocked", "score": 9, "reasons": [], "enforced": False,
    }))

    assert member.dms == []


def test_a_missing_enforced_field_is_flagged_as_a_service_defect():
    """A service that forgets the field must not look like deliberate shadow mode."""
    from services.altguard_client import parse_verdict

    parsed = parse_verdict({
        "verification_id": "uuid-1", "guild_id": 1, "discord_user_id": 42,
        "verdict": "passed",
    })
    assert parsed["enforced"] is False
    assert parsed["enforced_missing"] is True

    explicit = parse_verdict({
        "verification_id": "uuid-2", "guild_id": 1, "discord_user_id": 42,
        "verdict": "passed", "enforced": False,
    })
    assert explicit["enforced_missing"] is False


def test_the_verified_role_alone_proves_a_member_is_through_the_gate():
    """A hand-granted role has no DB row; it must still skip the verification."""
    verified = FakeRole(11, "verified")
    module = build_module(unverified=FakeRole(10, "unverified"), verified=verified)

    assert module.has_verified_role(FakeMember(member_id=42, roles=[verified])) is True
    assert module.has_verified_role(FakeMember(member_id=43, roles=[])) is False


# ------------------------------------------------- pushed config (dashboard)

class RecordingModule(AltGuardModule):
    """AltGuard with the three Discord-side effects recorded instead of done."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = []
        self.panel_posted = SimpleNamespace(id=555)

    async def refresh_panel(self):
        self.calls.append("refresh_panel")
        return self.panel_posted

    async def delete_panel(self, *, persist=True):
        self.calls.append(f"delete_panel(persist={persist})")

    async def sync_channel_permissions(self):
        self.calls.append("sync_channel_permissions")
        return {"updated": 3, "failed": 0, "skipped": 1}

    async def _resync_membership(self):
        self.calls.append("resync_membership")


def _pushed_module(**config_overrides):
    guild = FakeGuild(1, [FakeRole(10, "unverified"), FakeRole(11, "verified")])
    module = RecordingModule(FakeBot(guild=guild, db=FakeDB()), guild.id)
    config = {
        "channel_id": 100, "unverified_role_id": 10, "verified_role_id": 11,
        "log_channel_id": None, "panel_locale": "fr", "message_id": 999,
    }
    config.update(config_overrides)
    asyncio.run(module.load_config(config))
    return module


def test_a_pushed_config_reposts_the_panel_and_closes_the_channels():
    """A dashboard save must end in the same state as a /config save."""
    module = _pushed_module()

    recap = asyncio.run(module.on_external_config_change("updated"))

    assert module.calls == [
        "refresh_panel", "sync_channel_permissions", "resync_membership",
    ]
    assert recap["panel"] == "posted"
    # Snowflakes cross to the dashboard as strings — JSON would round a 64-bit id.
    assert recap["panel_message_id"] == "555"
    assert recap["permissions"] == {"updated": 3, "failed": 0, "skipped": 1}


def test_a_pushed_deletion_takes_the_panel_down_without_writing_back():
    """Persisting here would recreate the config that was just deleted."""
    module = _pushed_module()

    recap = asyncio.run(module.on_external_config_change("deleted"))

    assert module.calls == ["delete_panel(persist=False)"]
    assert recap == {"panel": "deleted"}


def test_a_pushed_incomplete_config_removes_the_panel():
    """Without a channel there is no gate, so the button must not stay up."""
    module = _pushed_module(channel_id=None)
    assert module.enabled is False

    recap = asyncio.run(module.on_external_config_change("updated"))

    assert module.calls == ["delete_panel(persist=True)"]
    assert recap == {"panel": "deleted", "enabled": False}


def test_delete_panel_without_persist_leaves_the_database_alone():
    """The real delete_panel, not the recording stub: nothing may be written."""
    writes = []

    class WatchingDB(FakeDB):
        async def update_guild_data(self, guild_id, path, value):
            writes.append((path, value))

    module = build_module(db=WatchingDB())
    module.message_id = 999

    asyncio.run(module.delete_panel(persist=False))
    assert writes == []
    assert module.message_id is None

    module.message_id = 999
    asyncio.run(module.delete_panel(persist=True))
    assert writes and writes[0][0] == "modules.altguard"


def test_the_log_card_names_a_missing_enforced_field():
    """A service defect must not read as deliberate shadow mode."""
    from utils.altguard_views import build_log_card

    missing = json.dumps(build_log_card(
        locale="en-US", kind="verdict", user_id=42, verdict="passed", score=1,
        reasons=[], enforced=False, enforced_missing=True, verification_id="uuid-1",
    ).to_components())
    assert "did not send" in missing

    deliberate = json.dumps(build_log_card(
        locale="en-US", kind="verdict", user_id=42, verdict="passed", score=1,
        reasons=[], enforced=False, enforced_missing=False, verification_id="uuid-2",
    ).to_components())
    assert "did not send" not in deliberate
