"""Linked roles — the `moddy:staff` publication, the Moddy Team role, `/team access`.

Everything here is pure Python: no gateway, no database, no Discord. The three
things worth pinning down are the ones a mistake would be silent about:

- the staff event goes out **after** the write, carries an id and never raises;
- the permission bitfield that travels in a custom_id cannot smuggle
  `administrator` back in;
- a staff ticket's category grants `admin` to the Moddy Team role and to
  nothing else.

    pytest tests/test_linked_roles.py -q
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from services.staff_events import (
    EVENT_RANKED,
    EVENT_UNRANKED,
    EVENT_UPDATED,
    STAFF_CHANNEL,
    notify_staff_change,
)
from utils.moddy_team_role import STORE_PATH, TEAM_ROLE_NAME, _as_int
from services.team_link_session import (
    CANCELLED,
    removable_roles,
    unstrippable_roles,
    DONE,
    EXPIRED,
    FAILED,
    SESSION_PATH,
    WINDOW_SECONDS,
    _restorable,
)
from utils.team_access_views import (
    ACCESS_PERMISSIONS,
    keys_to_value,
    permission_label,
    value_to_keys,
)


# --------------------------------------------------------------------------- #
# Stubs
# --------------------------------------------------------------------------- #
class FakeRedis:
    def __init__(self, fail=False):
        self.published = []
        self.fail = fail

    async def publish(self, channel, payload):
        if self.fail:
            raise RuntimeError("redis is down")
        self.published.append((channel, json.loads(payload)))


def make_bot(redis=None):
    return SimpleNamespace(redis=redis)


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# The moddy:staff publication
# --------------------------------------------------------------------------- #
class TestStaffEvents:
    def test_payload_shape(self):
        redis = FakeRedis()
        assert run(notify_staff_change(make_bot(redis), 42,
                                       event=EVENT_RANKED, roles=["Moderator"]))
        channel, payload = redis.published[0]
        assert channel == STAFF_CHANNEL
        assert payload == {"type": "staff_ranked", "user_id": "42",
                           "roles": ["Moderator"]}

    def test_user_id_is_a_string(self):
        """JSON has no 64-bit integer — a snowflake goes out as text."""
        redis = FakeRedis()
        run(notify_staff_change(make_bot(redis), 1164597199594852395,
                                event=EVENT_UPDATED))
        assert redis.published[0][1]["user_id"] == "1164597199594852395"

    def test_roles_omitted_when_not_given(self):
        """A destitution says who left, not what they used to hold."""
        redis = FakeRedis()
        run(notify_staff_change(make_bot(redis), 7, event=EVENT_UNRANKED))
        assert "roles" not in redis.published[0][1]

    def test_redis_failure_is_swallowed(self):
        """A promotion must never fail because Redis hiccuped."""
        assert run(notify_staff_change(make_bot(FakeRedis(fail=True)), 7,
                                       event=EVENT_UNRANKED)) is False

    def test_no_redis_is_not_an_error(self):
        assert run(notify_staff_change(make_bot(None), 7, event=EVENT_UNRANKED)) is False

    def test_every_write_site_publishes(self):
        """The five places that write staff_permissions all notify the backend.

        Forgetting one is invisible in production until somebody keeps a badge
        they should have lost, so it is asserted from the source itself.
        """
        for path in (
            "staff/commands/manage/rank.py",
            "staff/commands/manage/unrank.py",
            "staff/commands/manage/staff.py",
        ):
            with open(path, encoding="utf-8") as fh:
                assert "notify_staff_change" in fh.read(), path

    def test_publication_follows_the_write(self):
        """Publishing first would have the backend re-read the old state."""
        with open("staff/commands/manage/unrank.py", encoding="utf-8") as fh:
            source = fh.read()
        assert (source.index("await bot.db.remove_staff_permissions")
                < source.index("await notify_staff_change"))


# --------------------------------------------------------------------------- #
# The permission catalogue
# --------------------------------------------------------------------------- #
class TestAccessCatalogue:
    def test_fits_one_select(self):
        assert 1 <= len(ACCESS_PERMISSIONS) <= 25
        assert len(set(ACCESS_PERMISSIONS)) == len(ACCESS_PERMISSIONS)

    def test_administrator_is_not_requestable(self):
        assert "administrator" not in ACCESS_PERMISSIONS

    def test_round_trip(self):
        keys = ["manage_messages", "ban_members", "view_channel"]
        assert set(value_to_keys(keys_to_value(keys))) == set(keys)

    def test_catalogue_order_is_preserved(self):
        value = keys_to_value(["ban_members", "view_channel"])
        assert value_to_keys(value) == ["view_channel", "ban_members"]

    def test_a_forged_bitfield_cannot_smuggle_administrator(self):
        """The bitfield travels in a custom_id; it is re-filtered on the way out."""
        import discord

        forged = discord.Permissions.none()
        forged.administrator = True
        forged.ban_members = True
        assert value_to_keys(forged.value) == ["ban_members"]

    def test_unknown_keys_are_dropped_on_the_way_in(self):
        assert keys_to_value(["administrator", "manage_messages"]) == \
            keys_to_value(["manage_messages"])

    @pytest.mark.parametrize("locale", ["fr", "en-US", "es-ES", "pt-BR", "de"])
    def test_every_permission_is_translated(self, locale):
        for key in ACCESS_PERMISSIONS:
            label = permission_label(key, locale)
            assert label and not label.startswith("modules.logs"), (locale, key)
            assert len(label) <= 100, (locale, key)


# --------------------------------------------------------------------------- #
# The Moddy Team role helpers
# --------------------------------------------------------------------------- #
class TestTeamRole:
    def test_stored_snowflake_may_be_text(self):
        assert _as_int("1234567890") == 1234567890
        assert _as_int(1234567890) == 1234567890
        assert _as_int(None) is None
        assert _as_int("not an id") is None

    def test_store_path_is_namespaced(self):
        assert STORE_PATH == "moddy_team.role_id"

    def test_role_name(self):
        assert TEAM_ROLE_NAME == "Moddy Team"


# --------------------------------------------------------------------------- #
# The thirty-second linking window
# --------------------------------------------------------------------------- #
class FakeRole:
    def __init__(self, rid, *, managed=False, default=False, position=0):
        self.id = rid
        self.managed = managed
        self.position = position
        self._default = default

    def is_default(self):
        return self._default

    def __lt__(self, other):
        return self.position < other.position


class FakeGuildWithMe:
    """A guild whose ``me`` sits at a known height in the hierarchy."""

    def __init__(self, bot_top):
        self.me = SimpleNamespace(top_role=FakeRole(999, position=bot_top))


class FakeGuild:
    def __init__(self, roles):
        self._roles = {r.id: r for r in roles}

    def get_role(self, rid):
        return self._roles.get(rid)


class TestLinkingWindow:
    TEAM_ID = 99

    def _guild(self):
        return FakeGuild([
            FakeRole(1, default=True),          # @everyone
            FakeRole(2),                        # a normal role
            FakeRole(3, managed=True),          # a bot/booster role
            FakeRole(self.TEAM_ID),             # Moddy Team
        ])

    def test_the_moddy_team_role_is_never_handed_back(self):
        """Discord assigns it from the metadata; the bot must never grant it."""
        restored = _restorable(self._guild(), [2, self.TEAM_ID], self.TEAM_ID)
        assert [r.id for r in restored] == [2]

    def test_managed_and_everyone_are_left_alone(self):
        """Neither was ever removed — Discord refuses — so re-adding would 403."""
        restored = _restorable(self._guild(), [1, 2, 3], self.TEAM_ID)
        assert [r.id for r in restored] == [2]

    def test_a_deleted_role_is_simply_dropped(self):
        restored = _restorable(self._guild(), [2, 12345], self.TEAM_ID)
        assert [r.id for r in restored] == [2]

    def test_nothing_to_restore_is_not_an_error(self):
        assert _restorable(self._guild(), None, self.TEAM_ID) == []

    def test_roles_above_moddy_are_left_in_place(self):
        """Discord refuses to touch them; the window runs anyway, half-open."""
        guild = FakeGuildWithMe(bot_top=10)
        member = SimpleNamespace(roles=[
            FakeRole(1, default=True), FakeRole(2, position=5),
            FakeRole(3, managed=True, position=6), FakeRole(4, position=20),
        ])
        assert [r.id for r in removable_roles(guild, member)] == [2]
        assert [r.id for r in unstrippable_roles(guild, member)] == [4]

    def test_nothing_stays_when_everything_is_below_moddy(self):
        guild = FakeGuildWithMe(bot_top=10)
        member = SimpleNamespace(roles=[FakeRole(1, default=True), FakeRole(2, position=5)])
        assert unstrippable_roles(guild, member) == []

    def test_the_window_is_thirty_seconds(self):
        assert WINDOW_SECONDS == 30

    def test_the_session_is_persisted_under_moddy_team(self):
        """It must land beside the role id, so one guild read finds both."""
        assert SESSION_PATH.startswith("moddy_team.")
        assert SESSION_PATH != STORE_PATH

    @pytest.mark.parametrize("locale", ["fr", "en-US", "es-ES", "pt-BR", "de"])
    def test_every_outcome_has_a_sentence(self, locale):
        """A window that ends with no explanation is a window nobody trusts."""
        with open(f"locales/{locale}.json", encoding="utf-8") as fh:
            role = json.load(fh)["staff"]["team"]["role"]
        for outcome in (DONE, EXPIRED, CANCELLED, FAILED):
            assert f"window_{outcome}" in role, (locale, outcome)
        for blocker in ("not_member", "owner", "busy", "no_permission", "no_room"):
            assert f"blocked_{blocker}" in role, (locale, blocker)
        # The window can only half-contain a staffer sitting above Moddy, and
        # the card has to say so — silence there would be a false promise.
        assert "window_partial" in role, locale


# --------------------------------------------------------------------------- #
# The staff ticket category
# --------------------------------------------------------------------------- #
class TestStaffTicketCategory:
    def _category(self, role_id=555):
        from modules.tickets import normalize_category
        from services.ticket_service import STAFF_CATEGORY_ID, STAFF_NAME_FORMAT

        return normalize_category({
            'id': STAFF_CATEGORY_ID,
            'name': "Moddy ticket",
            'permissions': {str(role_id): ["admin"]},
            'buttons': ["close", "participants"],
            'claim_enabled': False,
            'name_format': STAFF_NAME_FORMAT,
            'ping_staff_roles': False,
            'enabled': True,
        })

    def test_only_the_team_role_is_granted_anything(self):
        from modules.tickets import staff_role_ids

        category = self._category()
        assert staff_role_ids(category, permission="view") == [555]
        assert staff_role_ids(category, permission="admin") == [555]

    def test_no_status_dot_without_claiming(self):
        from modules.tickets import ticket_status_dot

        ticket = {'status': 'open', 'escalated': False, 'claimed_by': None}
        assert ticket_status_dot(self._category(), ticket) is None

    def test_channel_name(self):
        from modules.tickets import render_channel_name

        name = render_channel_name(self._category(),
                                   member=SimpleNamespace(name="jules",
                                                          display_name="Jules"),
                                   number=3)
        assert name == "moddy-0003"

    def test_a_guild_administrator_keeps_everything(self):
        """Locking a server's owners out of a channel in their own server would
        be absurd — and Discord would not honour it anyway."""
        from modules.tickets import TICKET_PERMISSIONS, member_permissions

        admin = SimpleNamespace(
            id=1, roles=[],
            guild_permissions=SimpleNamespace(administrator=True))
        assert member_permissions(admin, self._category(), None) == set(TICKET_PERMISSIONS)


# --------------------------------------------------------------------------- #
# i18n
# --------------------------------------------------------------------------- #
class TestLocales:
    LOCALES = ["fr", "en-US", "es-ES", "pt-BR", "de"]

    def _team(self, locale):
        with open(f"locales/{locale}.json", encoding="utf-8") as fh:
            return json.load(fh)["staff"]["team"]

    @pytest.mark.parametrize("block", ["role", "access", "ticket"])
    def test_the_five_locales_stay_in_step(self, block):
        reference = set(self._team("fr")[block])
        for locale in self.LOCALES[1:]:
            assert set(self._team(locale)[block]) == reference, locale

    @pytest.mark.parametrize("locale", LOCALES)
    def test_placeholders_match_across_locales(self, locale):
        """A missing {role} is a KeyError in front of an administrator."""
        import re

        for block in ("role", "access", "ticket"):
            french = self._team("fr")[block]
            other = self._team(locale)[block]
            for key, value in french.items():
                assert (set(re.findall(r"\{(\w+)\}", value))
                        == set(re.findall(r"\{(\w+)\}", other[key]))), (locale, block, key)
