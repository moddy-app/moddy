"""Tests for the global sanction workflow: groups, notices, countdown, Redis.

Covers what a suspended user may still reach, how a group's level is derived,
how targets are parsed, and the apply / halt / lift / execute service paths
against stubbed DB + Redis.

    pytest tests/test_global_sanction_flow.py -q
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from utils import global_sanctions as gs
from utils.global_sanctions import GlobalLevel


# ------------------------------------------------- what a suspension spares

def test_a_suspended_user_keeps_the_informational_commands():
    for name in ("mycases", "moddy", "ping"):
        assert gs.is_command_allowed_when_suspended(name)


def test_a_suspended_user_loses_everything_else():
    for name in ("config", "translate", "reminder", "mod case create"):
        assert not gs.is_command_allowed_when_suspended(name)


def test_a_subcommand_is_gated_on_its_root():
    # "/mod global apply" is staff work, not an information command.
    assert not gs.is_command_allowed_when_suspended("mod global apply")


def test_no_command_name_is_not_allowed():
    assert not gs.is_command_allowed_when_suspended(None)
    assert not gs.is_command_allowed_when_suspended("")


def test_appeal_buttons_survive_a_suspension():
    assert gs.is_component_allowed_when_suspended(
        "moddy:apl:new:t:11111111-1111-1111-1111-111111111111:"
        "22222222-2222-2222-2222-222222222222")


def test_the_personal_cases_browser_survives_but_not_the_guild_one():
    assert gs.is_component_allowed_when_suspended("moddy:cases:browser:filters:user")
    assert not gs.is_component_allowed_when_suspended("moddy:cases:browser:filters:server")


def test_the_moddy_panel_survives():
    assert gs.is_component_allowed_when_suspended("moddy:moddy:main:attribution")


def test_other_components_are_blocked():
    assert not gs.is_component_allowed_when_suspended("moddy:config:main:module_select")
    assert not gs.is_component_allowed_when_suspended(None)


# ------------------------------------------------------------ presentation

def test_each_level_has_its_own_accent():
    accents = {gs.level_accent(l) for l in
               (GlobalLevel.WARN, GlobalLevel.LIMITED, GlobalLevel.SUSPENDED)}
    assert len(accents) == 3


def test_levels_have_custom_emojis():
    for level in (GlobalLevel.WARN, GlobalLevel.LIMITED, GlobalLevel.SUSPENDED):
        assert gs.level_emoji(level).startswith("<:")


# ------------------------------------------------------------ target parsing

def test_guild_ids_accept_commas_spaces_and_duplicates():
    from staff.commands.mod.global_sanction._shared import parse_guild_ids
    assert parse_guild_ids("111, 222 333\n111") == [111, 222, 333]


def test_no_guilds_parses_to_nothing():
    from staff.commands.mod.global_sanction._shared import parse_guild_ids
    assert parse_guild_ids(None) == []
    assert parse_guild_ids("   ") == []


def test_group_level_is_the_highest_active_one():
    from staff.commands.mod.global_sanction._shared import group_level
    cases = [
        {"sanctions": [{"action": "restrict", "status": "active"}]},
        {"sanctions": [{"action": "ban", "status": "active"}]},
    ]
    assert group_level(cases) is GlobalLevel.SUSPENDED


def test_group_level_falls_back_to_history_once_everything_is_revoked():
    from staff.commands.mod.global_sanction._shared import group_level
    cases = [{"sanctions": [{"action": "restrict", "status": "revoked"}]}]
    assert group_level(cases) is GlobalLevel.LIMITED


# --------------------------------------------------------------------- stubs

class _StubRedis:
    def __init__(self):
        self.published = []

    async def publish(self, channel, payload):
        self.published.append((channel, json.loads(payload)))


class _StubDB:
    def __init__(self):
        self.cases = []
        self.enforcements = {}
        self.events = []
        self.revoked = []
        self._ref = 0

    async def create_case(self, **kwargs):
        self._ref += 1
        row = {"id": uuid.uuid4(), "reference": f"REF{self._ref:03d}", **kwargs}
        self.cases.append(row)
        return {"id": row["id"], "reference": row["reference"]}

    async def create_enforcement(self, **kwargs):
        row = {"status": "pending", "notified": False, **kwargs}
        self.enforcements[str(kwargs["group_id"])] = row
        return row

    async def mark_enforcement_notified(self, group_id):
        self.enforcements[str(group_id)]["notified"] = True

    async def halt_enforcement(self, group_id, *, by_id=None, reason=None):
        row = self.enforcements.get(str(group_id))
        if not row or row["status"] != "pending":
            return None
        row.update(status="halted", halted_by=str(by_id), halted_reason=reason)
        return row

    async def cancel_enforcement(self, group_id):
        row = self.enforcements.get(str(group_id))
        if not row or row["status"] not in ("pending", "halted"):
            return None
        row["status"] = "cancelled"
        return row

    async def list_group_cases(self, group_id):
        return [
            {**c, "sanctions": [{"id": uuid.uuid4(), "action": "ban", "status": "active",
                                 "expires_at": None}]}
            for c in self.cases if str(c.get("group_id")) == str(group_id)
        ]

    async def revoke_sanction(self, sanction_id, by_type, by_id):
        self.revoked.append(str(sanction_id))
        return True

    async def add_event(self, case_id, event_type, **kwargs):
        self.events.append((str(case_id), event_type, kwargs.get("payload")))
        return uuid.uuid4()

    async def claim_due_enforcements(self, limit=25):
        due = [r for r in self.enforcements.values()
               if r["status"] == "pending" and r["deadline"] <= datetime.now(timezone.utc)]
        for row in due:
            row["status"] = "executed"
        return due


class _StubBot:
    def __init__(self, db=None, redis=None):
        self.db = db
        self.redis = redis
        self.left = []

    def get_guild(self, guild_id):
        return None

    def get_user(self, user_id):
        return None

    async def fetch_user(self, user_id):
        raise RuntimeError("no DMs in tests")


@pytest.fixture
def service():
    from services.global_sanction_service import GlobalSanctionService
    db, redis = _StubDB(), _StubRedis()
    bot = _StubBot(db, redis)
    gs.invalidate(bot)
    return GlobalSanctionService(bot), db, redis


# ---------------------------------------------------------------- apply flow

@pytest.mark.asyncio
async def test_apply_opens_one_case_per_target_in_one_group(service):
    svc, db, _ = service
    result = await svc.apply(
        level=GlobalLevel.SUSPENDED, reason="Raid", issuer_id=1,
        user_id=10, guild_ids=[20, 30], notify=False,
    )
    assert len(result["cases"]) == 3
    group_ids = {str(c["group_id"]) for c in db.cases}
    assert group_ids == {result["group_id"]}


@pytest.mark.asyncio
async def test_apply_uses_the_action_matching_the_level(service):
    svc, db, _ = service
    await svc.apply(level=GlobalLevel.LIMITED, reason="x", issuer_id=1,
                    user_id=10, notify=False)
    assert db.cases[0]["action"] == "restrict"


@pytest.mark.asyncio
async def test_apply_needs_at_least_one_target(service):
    svc, _, _ = service
    with pytest.raises(ValueError):
        await svc.apply(level=GlobalLevel.WARN, reason="x", issuer_id=1, notify=False)


@pytest.mark.asyncio
async def test_a_warning_schedules_no_countdown(service):
    svc, _, _ = service
    result = await svc.apply(level=GlobalLevel.WARN, reason="x", issuer_id=1,
                             user_id=10, notify=False)
    assert result["enforcement"] is None


@pytest.mark.asyncio
async def test_a_suspension_schedules_a_countdown(service):
    from services.global_sanction_service import ENFORCEMENT_GRACE_HOURS
    svc, _, _ = service
    result = await svc.apply(level=GlobalLevel.SUSPENDED, reason="x", issuer_id=1,
                             user_id=10, notify=False)
    deadline = result["enforcement"]["deadline"]
    expected = datetime.now(timezone.utc) + timedelta(hours=ENFORCEMENT_GRACE_HOURS)
    assert abs((deadline - expected).total_seconds()) < 60


@pytest.mark.asyncio
async def test_a_zero_grace_period_schedules_no_countdown(service):
    svc, _, _ = service
    result = await svc.apply(level=GlobalLevel.SUSPENDED, reason="x", issuer_id=1,
                             user_id=10, grace_hours=0, notify=False)
    assert result["enforcement"] is None


@pytest.mark.asyncio
async def test_a_guild_only_sanction_still_schedules_a_countdown(service):
    # No user in the group, but Moddy must still leave the suspended server —
    # the countdown is what makes that happen.
    svc, _, _ = service
    result = await svc.apply(level=GlobalLevel.SUSPENDED, reason="x", issuer_id=1,
                             guild_ids=[20], notify=False)
    assert result["enforcement"] is not None
    assert result["enforcement"]["subject_type"] == "discord_guild"
    assert result["enforcement"]["subject_id"] == 20


@pytest.mark.asyncio
async def test_apply_notifies_the_backend(service):
    svc, _, redis = service
    await svc.apply(level=GlobalLevel.LIMITED, reason="x", issuer_id=1,
                    user_id=10, guild_ids=[20], notify=False)
    channel, event = redis.published[-1]
    assert channel == "moddy:sanctions"
    assert event["type"] == "global_sanction_applied"
    assert event["level"] == "limited"
    assert len(event["targets"]) == 2


@pytest.mark.asyncio
async def test_apply_drops_the_cached_level_of_every_target(service):
    svc, db, _ = service
    bot = svc.bot
    # Warm the cache with a stale "no sanction" answer.
    bot._global_sanction_cache = {
        (gs.SUBJECT_USER, "10"): (GlobalLevel.NONE, 1e18),
        (gs.SUBJECT_GUILD, "20"): (GlobalLevel.NONE, 1e18),
    }
    await svc.apply(level=GlobalLevel.SUSPENDED, reason="x", issuer_id=1,
                    user_id=10, guild_ids=[20], notify=False)
    assert bot._global_sanction_cache == {}


# ----------------------------------------------------------------- halt flow

@pytest.mark.asyncio
async def test_halt_stops_the_countdown_and_tells_the_backend(service):
    svc, _, redis = service
    result = await svc.apply(level=GlobalLevel.SUSPENDED, reason="x", issuer_id=1,
                             user_id=10, notify=False)
    row = await svc.halt(result["group_id"], by_id=99, reason="Appeal #12", notify=False)
    assert row["status"] == "halted"
    assert redis.published[-1][1]["type"] == "enforcement_halted"
    assert redis.published[-1][1]["halted_by"] == "99"


@pytest.mark.asyncio
async def test_halting_twice_reports_nothing_to_stop(service):
    svc, _, _ = service
    result = await svc.apply(level=GlobalLevel.SUSPENDED, reason="x", issuer_id=1,
                             user_id=10, notify=False)
    await svc.halt(result["group_id"], by_id=99, notify=False)
    assert await svc.halt(result["group_id"], by_id=99, notify=False) is None


# ----------------------------------------------------------------- lift flow

@pytest.mark.asyncio
async def test_lift_revokes_every_sanction_of_the_group(service):
    svc, db, _ = service
    result = await svc.apply(level=GlobalLevel.SUSPENDED, reason="x", issuer_id=1,
                             user_id=10, guild_ids=[20], notify=False)
    lifted = await svc.lift(result["group_id"], by_id=99, reason="Appeal accepted")
    assert lifted["revoked"] == 2
    assert lifted["cancelled"] is True


@pytest.mark.asyncio
async def test_lift_notifies_the_backend(service):
    svc, _, redis = service
    result = await svc.apply(level=GlobalLevel.SUSPENDED, reason="x", issuer_id=1,
                             user_id=10, notify=False)
    await svc.lift(result["group_id"], by_id=99)
    assert redis.published[-1][1]["type"] == "global_sanction_lifted"


# -------------------------------------------------------------- execute flow

@pytest.mark.asyncio
async def test_a_due_countdown_executes_and_asks_the_backend_to_cancel_billing(service):
    svc, db, redis = service
    result = await svc.apply(level=GlobalLevel.SUSPENDED, reason="x", issuer_id=1,
                             user_id=10, guild_ids=[20], notify=False)
    row = db.enforcements[result["group_id"]]
    row["deadline"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    row["premium"] = True

    assert await svc.run_due() == 1
    event = redis.published[-1][1]
    assert event["type"] == "enforcement_executed"
    assert event["cancel_subscription"] is True


@pytest.mark.asyncio
async def test_a_halted_countdown_never_executes(service):
    svc, db, _ = service
    result = await svc.apply(level=GlobalLevel.SUSPENDED, reason="x", issuer_id=1,
                             user_id=10, notify=False)
    await svc.halt(result["group_id"], by_id=99, notify=False)
    db.enforcements[result["group_id"]]["deadline"] = (
        datetime.now(timezone.utc) - timedelta(hours=1))
    assert await svc.run_due() == 0


@pytest.mark.asyncio
async def test_nothing_due_is_a_no_op(service):
    svc, _, redis = service
    await svc.apply(level=GlobalLevel.SUSPENDED, reason="x", issuer_id=1,
                    user_id=10, notify=False)
    before = len(redis.published)
    assert await svc.run_due() == 0
    assert len(redis.published) == before


# ------------------------------------------------------- the guild-join gate

def _decide(guild="none", owner="none", inviter=None):
    return gs.decide_join_refusal(
        guild_level=GlobalLevel(guild),
        owner_level=GlobalLevel(owner),
        inviter_level=GlobalLevel(inviter) if inviter is not None else None,
    )


def test_a_clean_join_is_allowed():
    assert _decide() is None


def test_a_suspended_owner_blocks_the_join():
    assert _decide(owner="suspended") == "owner"


def test_a_limited_owner_blocks_the_join_too():
    # A limitation freezes growth, and a new server is growth.
    assert _decide(owner="limited") == "owner"


def test_a_warned_owner_does_not_block_the_join():
    assert _decide(owner="warn") is None


def test_a_sanctioned_inviter_blocks_the_join():
    assert _decide(inviter="limited") == "inviter"
    assert _decide(inviter="suspended") == "inviter"


def test_a_warned_inviter_does_not_block_the_join():
    assert _decide(inviter="warn") is None


def test_an_unknown_inviter_does_not_block_a_clean_join():
    # The audit log may be unreadable — that must not refuse a legitimate join.
    assert _decide(inviter=None) is None


def test_a_suspended_server_blocks_the_join():
    assert _decide(guild="suspended") == "guild"


def test_a_limited_server_keeps_moddy():
    # A limitation reduces service; it does not evict the bot.
    assert _decide(guild="limited") is None


def test_the_server_is_reported_before_the_people():
    # When everything is sanctioned at once, the server is the root cause.
    assert _decide(guild="suspended", owner="suspended", inviter="limited") == "guild"


def test_the_owner_is_reported_before_the_inviter():
    assert _decide(owner="limited", inviter="suspended") == "owner"
