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
    OPERATOR_BOOLEAN_EQUAL,
    STORE_PATH,
    TEAM_ROLE_NAME,
    LinkResult,
    _as_int,
    build_requirement,
    configuration_contains,
    link_team_role,
    merge_configuration,
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
# Binding the role to the linked-role requirement
# --------------------------------------------------------------------------- #
class FakeHTTP:
    """Answers the two role-connection routes with whatever the test wants."""

    #: The real schema: two boolean keys, only one of which may ever be used.
    METADATA = [{"key": "team", "type": 7}, {"key": "premium", "type": 7}]

    def __init__(self, current=None, on_write=None, metadata=None):
        self.metadata = self.METADATA if metadata is None else metadata
        self.current = current if current is not None else []
        self.on_write = on_write
        self.written = None
        self.calls = []

    async def request(self, route, **kwargs):
        self.calls.append((route.method, route.path))
        if route.path.endswith("/role-connections/metadata"):
            return self.metadata
        if route.method == "GET":
            return self.current
        if self.on_write:
            raise self.on_write
        self.written = kwargs.get("json")
        return self.written


def make_role(guild_id=1, role_id=2):
    return SimpleNamespace(id=role_id, guild=SimpleNamespace(id=guild_id))


_app_ids = iter(range(1000, 9999))


def make_linking_bot(http, application_id=42):
    return SimpleNamespace(application_id=application_id, http=http)


class TestRoleBinding:
    def test_requirement_shape(self):
        req = build_requirement(42, "team")
        assert req["connection_type"] == "application"
        assert req["application_id"] == "42"   # a snowflake travels as a string
        assert req["connection_metadata_field"] == "team"
        assert req["operator"] == OPERATOR_BOOLEAN_EQUAL
        assert req["value"] == "1"

    def test_a_server_requirement_is_never_dropped(self):
        """The PUT replaces the whole configuration — ours is an extra OR branch."""
        theirs = [[{"connection_type": "steam", "connection_metadata_field": None,
                    "operator": None, "value": None}]]
        merged = merge_configuration(theirs, build_requirement(42, "team"))
        assert merged[0] == theirs[0]
        assert len(merged) == 2

    def test_merging_twice_adds_nothing(self):
        req = build_requirement(42, "team")
        once = merge_configuration([], req)
        assert merge_configuration(once, req) == once

    def test_receive_only_fields_do_not_hide_our_requirement(self):
        """A configuration read back carries extra fields; == would miss it."""
        req = build_requirement(42, "team")
        from_discord = [[dict(req, name="Moddy Team", description="…", result=True)]]
        assert configuration_contains(from_discord, req)

    def test_merge_does_not_mutate_the_original(self):
        theirs = [[{"connection_type": "steam"}]]
        merge_configuration(theirs, build_requirement(42, "team"))
        assert theirs == [[{"connection_type": "steam"}]]

    def test_binding_writes_the_requirement(self):
        http = FakeHTTP()
        result = run(link_team_role(make_linking_bot(http), make_role()))
        assert result == LinkResult.LINKED_NOW
        assert http.written == [[build_requirement(42, "team")]]

    def test_an_existing_binding_is_not_rewritten(self):
        http = FakeHTTP(current=[[build_requirement(42, "team")]])
        result = run(link_team_role(make_linking_bot(http), make_role()))
        assert result == LinkResult.ALREADY_LINKED
        assert http.written is None

    @pytest.mark.parametrize("status,expected", [
        (403, LinkResult.FORBIDDEN),
        (404, LinkResult.UNSUPPORTED),
        (405, LinkResult.UNSUPPORTED),
        (400, LinkResult.FAILED),
        (500, LinkResult.FAILED),
    ])
    def test_discord_refusing_is_an_answer_not_a_crash(self, status, expected):
        """`/team role` must always be able to fall back on the manual steps."""
        import discord

        response = SimpleNamespace(status=status, reason="nope")
        error = (discord.Forbidden(response, "no") if status == 403
                 else discord.HTTPException(response, "no"))
        http = FakeHTTP(on_write=error)
        assert run(link_team_role(make_linking_bot(http), make_role())) == expected

    def test_premium_is_never_mistaken_for_team(self):
        """Binding the role to `premium` would hand it to every subscriber."""
        http = FakeHTTP(metadata=[{"key": "premium", "type": 7}])
        bot = make_linking_bot(http, application_id=next(_app_ids))
        assert run(link_team_role(bot, make_role())) == LinkResult.NO_METADATA
        assert http.written is None

    def test_discord_s_own_answer_reaches_the_logs(self, caplog):
        """The route is undocumented: its body is the only thing that will ever
        explain a refusal, so it must never be swallowed."""
        import logging

        import discord

        response = SimpleNamespace(status=403, reason="Forbidden")
        error = discord.Forbidden(response, {
            "code": 50013, "message": "Missing Permissions",
        })
        http = FakeHTTP(on_write=error)
        with caplog.at_level(logging.ERROR, logger="moddy.moddy_team_role"):
            run(link_team_role(make_linking_bot(http, application_id=next(_app_ids)),
                               make_role(guild_id=7, role_id=8)))

        logged = "\n".join(r.getMessage() for r in caplog.records)
        assert "403" in logged
        assert "50013" in logged                 # Discord's own error code
        assert "Missing Permissions" in logged   # the body, verbatim
        assert "role 8" in logged and "guild 7" in logged
        assert "connection_metadata_field" in logged  # what we actually sent

    def test_no_application_id_is_not_a_crash(self):
        bot = SimpleNamespace(application_id=None, http=FakeHTTP())
        assert run(link_team_role(bot, make_role())) == LinkResult.FAILED

    def test_every_failure_has_something_to_say(self):
        """Each non-DONE outcome must have its `auto_*` line in every locale."""
        outcomes = [v for k, v in vars(LinkResult).items()
                    if k.isupper() and isinstance(v, str) and v not in LinkResult.DONE]
        for locale in ("fr", "en-US", "es-ES", "pt-BR", "de"):
            with open(f"locales/{locale}.json", encoding="utf-8") as fh:
                role = json.load(fh)["staff"]["team"]["role"]
            for outcome in outcomes:
                assert f"auto_{outcome}" in role, (locale, outcome)


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
