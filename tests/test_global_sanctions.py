"""Tests for the global (Moddy-team) sanction resolver.

Global sanctions live in the cases system — there is no attribute any more.
This suite covers the level ladder, the caching contract (including temporary
sanctions, which must never be cached past their expiry) and the user/guild
context resolution.

    pytest tests/test_global_sanctions.py -q
"""

from datetime import datetime, timedelta, timezone

import pytest

from utils import global_sanctions as gs
from utils.global_sanctions import GlobalLevel


# --------------------------------------------------------------- stubs

class _StubDB:
    """Minimal ``bot.db`` returning canned global sanctions per subject."""

    def __init__(self, sanctions=None, fail=False):
        # {(subject_type, subject_id_str): [ {action, expires_at}, ... ]}
        self.sanctions = sanctions or {}
        self.fail = fail
        self.calls = 0

    async def list_active_global_actions(self, subject_type, subject_id):
        self.calls += 1
        if self.fail:
            raise RuntimeError("db down")
        return list(self.sanctions.get((subject_type, str(subject_id)), []))


class _StubBot:
    def __init__(self, db=None):
        self.db = db


def _perm(action):
    return {"action": action, "expires_at": None}


def _temp(action, seconds):
    return {
        "action": action,
        "expires_at": datetime.now(timezone.utc) + timedelta(seconds=seconds),
    }


# ---------------------------------------------------------------- ladder

def test_actions_map_onto_the_three_levels():
    assert gs.level_from_actions(["warn"]) is GlobalLevel.WARN
    assert gs.level_from_actions(["restrict"]) is GlobalLevel.LIMITED
    assert gs.level_from_actions(["ban"]) is GlobalLevel.SUSPENDED


def test_no_sanction_means_no_level():
    assert gs.level_from_actions([]) is GlobalLevel.NONE


def test_highest_level_wins():
    assert gs.level_from_actions(["warn", "ban", "restrict"]) is GlobalLevel.SUSPENDED
    assert gs.level_from_actions(["warn", "restrict"]) is GlobalLevel.LIMITED


def test_unrelated_actions_are_ignored():
    # `mute` is a guild sanction; it is never a global level.
    assert gs.level_from_actions(["mute"]) is GlobalLevel.NONE


def test_at_least_follows_the_severity_order():
    assert gs.at_least(GlobalLevel.SUSPENDED, GlobalLevel.LIMITED)
    assert gs.at_least(GlobalLevel.LIMITED, GlobalLevel.LIMITED)
    assert not gs.at_least(GlobalLevel.WARN, GlobalLevel.LIMITED)
    assert not gs.at_least(GlobalLevel.NONE, GlobalLevel.WARN)


# ------------------------------------------------------------ resolution

@pytest.mark.asyncio
async def test_user_level_is_resolved_from_the_db():
    bot = _StubBot(_StubDB({(gs.SUBJECT_USER, "1"): [_perm("restrict")]}))
    assert await gs.get_user_level(bot, 1) is GlobalLevel.LIMITED


@pytest.mark.asyncio
async def test_guild_level_is_resolved_from_the_db():
    bot = _StubBot(_StubDB({(gs.SUBJECT_GUILD, "9"): [_perm("ban")]}))
    assert await gs.get_guild_level(bot, 9) is GlobalLevel.SUSPENDED


@pytest.mark.asyncio
async def test_a_limited_guild_limits_an_unsanctioned_user():
    bot = _StubBot(_StubDB({(gs.SUBJECT_GUILD, "9"): [_perm("restrict")]}))
    assert await gs.is_limited(bot, user_id=1, guild_id=9)
    assert not await gs.is_suspended(bot, user_id=1, guild_id=9)


@pytest.mark.asyncio
async def test_a_limited_user_stays_limited_in_a_clean_guild():
    bot = _StubBot(_StubDB({(gs.SUBJECT_USER, "1"): [_perm("restrict")]}))
    assert await gs.is_limited(bot, user_id=1, guild_id=9)


@pytest.mark.asyncio
async def test_a_warn_blocks_nothing():
    bot = _StubBot(_StubDB({(gs.SUBJECT_USER, "1"): [_perm("warn")]}))
    assert not await gs.is_limited(bot, user_id=1)
    assert not await gs.is_suspended(bot, user_id=1)


@pytest.mark.asyncio
async def test_suspension_implies_limitation():
    bot = _StubBot(_StubDB({(gs.SUBJECT_USER, "1"): [_perm("ban")]}))
    assert await gs.is_limited(bot, user_id=1)


@pytest.mark.asyncio
async def test_no_db_means_no_sanction():
    assert await gs.get_user_level(_StubBot(None), 1) is GlobalLevel.NONE


@pytest.mark.asyncio
async def test_a_db_error_fails_open():
    bot = _StubBot(_StubDB(fail=True))
    assert await gs.get_user_level(bot, 1) is GlobalLevel.NONE


# ----------------------------------------------------------------- cache

@pytest.mark.asyncio
async def test_the_level_is_cached():
    db = _StubDB({(gs.SUBJECT_USER, "1"): [_perm("ban")]})
    bot = _StubBot(db)
    await gs.get_user_level(bot, 1)
    await gs.get_user_level(bot, 1)
    assert db.calls == 1


@pytest.mark.asyncio
async def test_invalidate_drops_one_subject_only():
    db = _StubDB({
        (gs.SUBJECT_USER, "1"): [_perm("ban")],
        (gs.SUBJECT_USER, "2"): [_perm("ban")],
    })
    bot = _StubBot(db)
    await gs.get_user_level(bot, 1)
    await gs.get_user_level(bot, 2)
    gs.invalidate(bot, gs.SUBJECT_USER, 1)
    await gs.get_user_level(bot, 1)
    await gs.get_user_level(bot, 2)
    assert db.calls == 3


@pytest.mark.asyncio
async def test_invalidate_without_a_subject_clears_everything():
    db = _StubDB({(gs.SUBJECT_USER, "1"): [_perm("ban")]})
    bot = _StubBot(db)
    await gs.get_user_level(bot, 1)
    gs.invalidate(bot)
    await gs.get_user_level(bot, 1)
    assert db.calls == 2


@pytest.mark.asyncio
async def test_a_lifted_sanction_is_seen_after_invalidation():
    db = _StubDB({(gs.SUBJECT_USER, "1"): [_perm("ban")]})
    bot = _StubBot(db)
    assert await gs.is_suspended(bot, user_id=1)
    db.sanctions[(gs.SUBJECT_USER, "1")] = []
    gs.invalidate(bot, gs.SUBJECT_USER, 1)
    assert not await gs.is_suspended(bot, user_id=1)


def test_a_temporary_sanction_bounds_the_cache_ttl():
    # A sanction expiring sooner than CACHE_TTL shortens the cache entry.
    assert gs._ttl_for([_temp("restrict", 30)]) == pytest.approx(30, abs=2)


def test_a_permanent_sanction_uses_the_full_ttl():
    assert gs._ttl_for([_perm("ban")]) == gs.CACHE_TTL


def test_the_soonest_expiry_wins():
    ttl = gs._ttl_for([_temp("restrict", 90), _temp("ban", 20), _perm("warn")])
    assert ttl == pytest.approx(20, abs=2)


def test_an_expiry_beyond_the_ttl_does_not_extend_it():
    assert gs._ttl_for([_temp("ban", 10_000)]) == gs.CACHE_TTL
