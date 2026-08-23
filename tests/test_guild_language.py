"""Server language — the one setting every module reads.

Covers the two halves of ``utils/guild_language.py``: the pure rules (which
Discord locale maps onto a language Moddy speaks, and what "automatic" means)
and the stored setting (read once, cached, invalidated on write).
"""

import asyncio
import types

import pytest

from utils import guild_language as gl


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

def _guild(guild_id=1, *, community=False, preferred="en-US"):
    return types.SimpleNamespace(
        id=guild_id,
        features=["COMMUNITY"] if community else [],
        preferred_locale=preferred,
    )


class FakeDB:
    """Just enough of ModdyDatabase for the setting to round-trip."""

    def __init__(self, settings=None):
        self.settings = settings or {}
        self.reads = 0
        self.writes = []

    async def get_guild(self, guild_id):
        self.reads += 1
        return {"guild_id": guild_id, "attributes": {},
                "data": {"settings": self.settings}}

    async def update_guild_data(self, guild_id, path, value):
        self.writes.append((guild_id, path, value))


class FakeBot:
    def __init__(self, db=None, guild=None):
        self.db = db
        self._guild = guild

    def get_guild(self, guild_id):
        if self._guild is not None and self._guild.id == guild_id:
            return self._guild
        return None


@pytest.fixture(autouse=True)
def _clean_cache():
    gl.invalidate_guild_language()
    yield
    gl.invalidate_guild_language()


# --------------------------------------------------------------------------- #
# Pure rules
# --------------------------------------------------------------------------- #

def test_a_discord_locale_maps_onto_a_language_moddy_speaks():
    assert gl.match_supported_locale("fr") == "fr"
    assert gl.match_supported_locale("en-GB") == "en-US"   # same language
    assert gl.match_supported_locale("es-419") == "es-ES"
    assert gl.match_supported_locale("pt-PT") == "pt-BR"


def test_an_untranslated_language_matches_nothing():
    """Better English than a half-translated Japanese."""
    assert gl.match_supported_locale("ja") is None
    assert gl.match_supported_locale("") is None
    assert gl.match_supported_locale(None) is None


def test_automatic_only_trusts_a_community_server():
    """Outside Community, preferred_locale is an account default nobody picked."""
    assert gl.auto_locale(_guild(community=True, preferred="fr")) == "fr"
    assert gl.auto_locale(_guild(community=False, preferred="fr")) == "en-US"
    assert gl.auto_locale(None) == "en-US"


def test_automatic_falls_back_when_the_server_speaks_something_untranslated():
    assert gl.auto_locale(_guild(community=True, preferred="ja")) == "en-US"


def test_an_explicit_choice_wins_over_the_server_language():
    guild = _guild(community=True, preferred="fr")
    assert gl.resolve_locale(guild, "de") == "de"
    assert gl.resolve_locale(guild, gl.AUTO) == "fr"


def test_a_stored_value_that_means_nothing_reads_as_automatic():
    assert gl.normalize_language_setting("klingon") == gl.AUTO
    assert gl.normalize_language_setting(None) == gl.AUTO
    assert gl.normalize_language_setting("AUTO") == gl.AUTO
    assert gl.normalize_language_setting("en-GB") == "en-US"


# --------------------------------------------------------------------------- #
# Stored setting
# --------------------------------------------------------------------------- #

def test_the_setting_is_read_once_and_then_cached():
    db = FakeDB({"language": "de"})
    bot = FakeBot(db, _guild())

    assert asyncio.run(gl.guild_locale(bot, 1)) == "de"
    assert asyncio.run(gl.guild_locale(bot, 1)) == "de"
    assert db.reads == 1


def test_saving_writes_the_setting_and_refreshes_the_cache():
    db = FakeDB({"language": "de"})
    bot = FakeBot(db, _guild())
    assert asyncio.run(gl.guild_locale(bot, 1)) == "de"

    asyncio.run(gl.set_language_setting(bot, 1, "fr"))

    assert db.writes == [(1, gl.SETTINGS_PATH, "fr")]
    assert asyncio.run(gl.guild_locale(bot, 1)) == "fr"


def test_an_unsupported_saved_value_is_stored_as_automatic():
    db = FakeDB()
    bot = FakeBot(db, _guild(community=True, preferred="fr"))

    assert asyncio.run(gl.set_language_setting(bot, 1, "klingon")) == gl.AUTO
    assert asyncio.run(gl.guild_locale(bot, 1)) == "fr"


def test_a_database_failure_degrades_to_automatic():
    class BrokenDB(FakeDB):
        async def get_guild(self, guild_id):
            raise RuntimeError("no database")

    bot = FakeBot(BrokenDB(), _guild(community=True, preferred="de"))
    assert asyncio.run(gl.guild_locale(bot, 1)) == "de"


def test_invalidation_forces_a_re_read():
    db = FakeDB({"language": "de"})
    bot = FakeBot(db, _guild())
    asyncio.run(gl.guild_locale(bot, 1))

    db.settings["language"] = "fr"          # the dashboard wrote it directly
    gl.invalidate_guild_language(1)

    assert asyncio.run(gl.guild_locale(bot, 1)) == "fr"
    assert db.reads == 2


# --------------------------------------------------------------------------- #
# Sync hot path
# --------------------------------------------------------------------------- #

def test_the_sync_reader_uses_the_cache_when_it_is_warm():
    db = FakeDB({"language": "de"})
    guild = _guild(community=True, preferred="fr")
    bot = FakeBot(db, guild)
    asyncio.run(gl.guild_locale(bot, guild))

    assert gl.guild_locale_cached(bot, guild) == "de"


def test_the_sync_reader_answers_automatically_on_a_cold_cache():
    """A cold cache must never block: worst case, the automatic language."""
    bot = FakeBot(FakeDB({"language": "de"}), _guild(community=True, preferred="fr"))
    assert gl.guild_locale_cached(bot, bot._guild) == "fr"


def test_the_sync_reader_warms_the_cache_for_the_next_call():
    async def scenario():
        db = FakeDB({"language": "de"})
        guild = _guild(community=True, preferred="fr")
        bot = FakeBot(db, guild)

        assert gl.guild_locale_cached(bot, guild) == "fr"   # cold
        await asyncio.sleep(0)                              # let the fill run
        await asyncio.sleep(0)
        return gl.guild_locale_cached(bot, guild)

    assert asyncio.run(scenario()) == "de"
