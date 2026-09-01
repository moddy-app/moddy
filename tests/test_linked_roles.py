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
from utils.moddy_team_role import (
    KINDS,
    MANAGER,
    STORE_PATH,
    TEAM,
    TEAM_ROLE_NAME,
    _as_int,
    kind_from_key,
)
from services.team_link_session import (
    CANCELLED,
    removable_roles,
    unstrippable_roles,
    DONE,
    EXPIRED,
    FAILED,
    PARTIAL,
    SESSION_PATH,
    WINDOW_SECONDS,
    WindowResult,
    _restorable,
    id_set,
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

    @pytest.mark.parametrize("custom_id", [
        "moddy:teamaccess:pick:7:3",
        "moddy:teamaccess:send:7:3",
        "moddy:teamaccess:accept:7:3",
        "moddy:teamaccess:refuse:7:3",
    ])
    def test_a_card_posted_before_the_manager_role_is_still_answerable(self, custom_id):
        """The role segment is optional in every template. A pending card
        posted last month has no third field and means the base role — the
        alternative is buttons that silently stop responding."""
        import re

        from utils.moddy_team_role import TEAM
        from utils.team_access_views import _CID_DECIDE, _CID_PICK, _CID_SEND

        matched = None
        for template in (_CID_PICK, _CID_SEND, _CID_DECIDE):
            matched = re.fullmatch(template, custom_id)
            if matched:
                break
        assert matched is not None, custom_id
        assert matched.groupdict().get("kind") is None
        # And an absent segment resolves to the base role, not to nothing.
        assert kind_from_key(matched.groupdict().get("kind")) is TEAM

    def test_the_role_travels_in_the_custom_id(self):
        import re

        from utils.team_access_views import _CID_SEND

        match = re.fullmatch(_CID_SEND, "moddy:teamaccess:send:7:3:manager")
        assert match is not None
        assert match["kind"] == "manager"
        # And nothing else is accepted there.
        assert re.fullmatch(_CID_SEND, "moddy:teamaccess:send:7:3:admin") is None

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

    def test_the_two_roles_never_share_anything(self):
        """A shared name, path or metadata key would mean one role overwriting
        the other's id — silently, and only in servers that have both."""
        for field in ("key", "name", "store_path", "metadata"):
            values = [getattr(kind, field) for kind in KINDS]
            assert len(set(values)) == len(values), field

    def test_the_metadata_keys_are_the_ones_the_backend_publishes(self):
        assert TEAM.metadata == "team"
        assert MANAGER.metadata == "manager"

    def test_the_manager_name_contains_the_team_name(self):
        """Which is exactly why the name lookup must match in full.

        A ``startswith`` (or an ``in``) would resolve the base role to
        "Moddy Team Manager" in a server that has both, and `/team access`
        would then grant the team's permissions to the manager role.
        """
        assert MANAGER.name.startswith(TEAM.name)
        assert MANAGER.name != TEAM.name

    def test_an_unknown_key_means_the_base_role(self):
        """The key travels through options and custom_ids; a typo must not
        raise in front of an administrator, and must not silently escalate."""
        assert kind_from_key("manager") is MANAGER
        assert kind_from_key("team") is TEAM
        assert kind_from_key("MANAGER") is MANAGER
        for junk in (None, "", "admin", "moddy team manager"):
            assert kind_from_key(junk) is TEAM


class TestRoleScope:
    """`t.role [guild_id] [team|manager|both]` — one role by default."""

    def test_the_default_is_the_base_role_alone(self):
        from staff.commands.team.team_role import kinds_for_scope

        assert kinds_for_scope(None) == (TEAM,)
        assert kinds_for_scope("") == (TEAM,)
        assert kinds_for_scope("team") == (TEAM,)

    def test_each_scope_selects_what_it_says(self):
        from staff.commands.team.team_role import kinds_for_scope

        assert kinds_for_scope("manager") == (MANAGER,)
        assert kinds_for_scope("both") == KINDS
        assert set(kinds_for_scope("both")) == {TEAM, MANAGER}

    def test_an_unknown_scope_does_not_widen(self):
        assert __import__(
            "staff.commands.team.team_role", fromlist=["kinds_for_scope"]
        ).kinds_for_scope("everything") == (TEAM,)

    @pytest.mark.parametrize("raw,expected", [
        ("", ("", "team")),
        ("123", ("123", "team")),
        ("manager", ("", "manager")),
        ("123 manager", ("123", "manager")),
        ("manager 123", ("123", "manager")),
        ("123 both", ("123", "both")),
    ])
    def test_the_message_form_reads_both_orders(self, raw, expected):
        """A scope is a word from a three-item list and a guild id is digits,
        so neither can be mistaken for the other."""
        from staff.commands.team.team_role import TeamRoleCommand

        parsed = TeamRoleCommand(bot=None).parse_message(raw)
        assert (parsed["guild_id"], parsed["roles"]) == expected


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
    MANAGER_ID = 98

    def _guild(self):
        return FakeGuild([
            FakeRole(1, default=True),          # @everyone
            FakeRole(2),                        # a normal role
            FakeRole(3, managed=True),          # a bot/booster role
            FakeRole(self.TEAM_ID),             # Moddy Team
            FakeRole(self.MANAGER_ID),          # Moddy Team Manager
        ])

    def test_neither_moddy_team_role_is_ever_handed_back(self):
        """Discord assigns them from the metadata; the bot must never grant one."""
        restored = _restorable(self._guild(),
                               [2, self.TEAM_ID, self.MANAGER_ID],
                               {self.TEAM_ID, self.MANAGER_ID})
        assert [r.id for r in restored] == [2]

    def test_a_window_persisted_before_the_second_role_still_restores(self):
        """A restart reads back a single ``team_role_id``, not a list. That
        staffer must still get their roles — and not the Moddy Team one."""
        restored = _restorable(self._guild(), [2, self.TEAM_ID], self.TEAM_ID)
        assert [r.id for r in restored] == [2]

    def test_managed_and_everyone_are_left_alone(self):
        """Neither was ever removed — Discord refuses — so re-adding would 403."""
        restored = _restorable(self._guild(), [1, 2, 3], {self.TEAM_ID})
        assert [r.id for r in restored] == [2]

    def test_a_deleted_role_is_simply_dropped(self):
        restored = _restorable(self._guild(), [2, 12345], {self.TEAM_ID})
        assert [r.id for r in restored] == [2]

    def test_nothing_to_restore_is_not_an_error(self):
        assert _restorable(self._guild(), None, {self.TEAM_ID}) == []

    @pytest.mark.parametrize("value,expected", [
        (None, set()),
        (7, {7}),
        ("7", {7}),
        ([7, "8"], {7, 8}),
        ([], set()),
        (["nope", None], set()),
    ])
    def test_stored_ids_are_read_in_either_shape(self, value, expected):
        assert id_set(value) == expected

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

    def test_the_window_covers_two_bindings(self):
        """It stays short — it is an escalation — but seven clicks per role
        against thirty seconds was already tight for one."""
        assert 60 <= WINDOW_SECONDS <= 120

    def test_the_window_ends_only_once_every_role_is_linked(self):
        """One role linked out of two is not a success, and resolving there
        would tear the window down with the second one still unbound."""
        session = self._session([self.TEAM_ID, self.MANAGER_ID])

        session.mark_linked(self.TEAM_ID)
        assert not session.finished.done()

        session.mark_linked(self.MANAGER_ID)
        assert session.finished.done()
        assert session.finished.result() == DONE

    def test_a_single_role_window_resolves_on_that_role(self):
        session = self._session([self.TEAM_ID])
        session.mark_linked(self.TEAM_ID)
        assert session.finished.result() == DONE

    def test_the_same_role_twice_does_not_resolve_the_other(self):
        """The gateway can repeat an event; it must not count as progress."""
        session = self._session([self.TEAM_ID, self.MANAGER_ID])
        session.mark_linked(self.TEAM_ID)
        session.mark_linked(self.TEAM_ID)
        assert not session.finished.done()
        assert session.pending == {self.MANAGER_ID}

    def test_the_outcome_reads_as_a_string(self):
        """`result == DONE` is how every caller asks; keep it working."""
        assert WindowResult(DONE) == DONE
        assert WindowResult(PARTIAL) != DONE
        assert str(WindowResult(EXPIRED)) == EXPIRED
        assert WindowResult(DONE, {1, 2}).linked_ids == {1, 2}

    def _session(self, role_ids):
        """A LinkSession with no Discord behind it — only the bookkeeping."""
        import asyncio

        from services.team_link_session import LinkSession

        # The session only needs a loop to hold its future on; nothing here
        # ever runs on it.
        asyncio.set_event_loop(asyncio.new_event_loop())
        return LinkSession(
            bot=None, guild=SimpleNamespace(id=1),
            member=SimpleNamespace(id=2),
            team_roles=[FakeRole(rid) for rid in role_ids],
        )

    def test_the_session_is_persisted_under_moddy_team(self):
        """It must land beside the role id, so one guild read finds both."""
        assert SESSION_PATH.startswith("moddy_team.")
        assert SESSION_PATH != STORE_PATH

    def test_channel_access_is_not_moderative(self):
        """`/team see` opens a channel; it does not hand out moderation.

        Anything beyond taking part in the conversation goes through
        `/team access`, which an administrator has to accept.
        """
        from staff.commands.team.see import GRANTED

        assert set(GRANTED) == {"view_channel", "read_message_history",
                                "send_messages", "send_messages_in_threads"}

    @pytest.mark.parametrize("locale", ["fr", "en-US", "es-ES", "pt-BR", "de"])
    def test_channel_access_is_translated(self, locale):
        with open(f"locales/{locale}.json", encoding="utf-8") as fh:
            see = json.load(fh)["staff"]["team"]["see"]
        for key in ("title", "granted", "revoked", "already", "scope",
                    "failed_title", "guild_only", "not_yours", "no_role",
                    "no_permission", "role_too_high", "moddy_cannot_see"):
            assert see.get(key), (locale, key)

    @pytest.mark.parametrize("locale", ["fr", "en-US", "es-ES", "pt-BR", "de"])
    def test_every_outcome_has_a_sentence(self, locale):
        """A window that ends with no explanation is a window nobody trusts."""
        with open(f"locales/{locale}.json", encoding="utf-8") as fh:
            role = json.load(fh)["staff"]["team"]["role"]
        for outcome in (DONE, PARTIAL, EXPIRED, CANCELLED, FAILED):
            assert f"window_{outcome}" in role, (locale, outcome)
        for blocker in ("not_member", "owner", "busy", "no_permission", "no_room"):
            assert f"blocked_{blocker}" in role, (locale, blocker)
        # The window can only half-contain a staffer sitting above Moddy, and
        # the card has to say so — silence there would be a false promise.
        assert "window_kept_roles" in role, locale
        # One role linked out of two is its own outcome, distinct from the
        # window that achieved nothing.
        assert role["window_partial"] != role["window_expired"], locale


# --------------------------------------------------------------------------- #
# The staff ticket category
# --------------------------------------------------------------------------- #
class TestStaffTicketCategory:
    def _category(self, role_ids=(555,)):
        from modules.tickets import normalize_category
        from services.ticket_service import STAFF_CATEGORY_ID, STAFF_NAME_FORMAT

        return normalize_category({
            'id': STAFF_CATEGORY_ID,
            'name': "Moddy ticket",
            'permissions': {str(rid): ["admin"] for rid in role_ids},
            'buttons': ["close", "participants"],
            'claim_enabled': False,
            'name_format': STAFF_NAME_FORMAT,
            'ping_staff_roles': False,
            'enabled': True,
        })

    def test_only_the_team_roles_are_granted_anything(self):
        from modules.tickets import staff_role_ids

        category = self._category()
        assert staff_role_ids(category, permission="view") == [555]
        assert staff_role_ids(category, permission="admin") == [555]

    def test_both_team_roles_are_on_a_staff_ticket(self):
        """A manager holds the base role too, so this changes nothing for them.
        What it covers is a server that only ever created the manager role,
        where granting the base role alone would open the channel to nobody."""
        from modules.tickets import staff_role_ids

        category = self._category(role_ids=(555, 556))
        assert sorted(staff_role_ids(category, permission="admin")) == [555, 556]

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
