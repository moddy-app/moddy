"""Hardening of /config: permissions checked where Discord checks them, hostile
stored data, one failing module never breaking the others.

    pytest tests/test_hardening_config.py -q

Fake guilds implement Discord's documented permission algorithm
(docs/guides/DISCORD_PERMISSIONS.md section 1) with real discord.Permissions /
PermissionOverwrite objects, so the Top.gg review setups can be reproduced.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from modules.module_manager import ModuleBase, ModuleManager
from utils.bot_permissions import (
    CHANNEL_SCOPED_PERMISSIONS,
    GUILD_SCOPED_PERMISSIONS,
    analyze_requirements,
    channel_validation_error,
    explicit_channel_deny,
    format_gaps,
    invite_permissions_for,
    role_assignment_error,
    role_assignment_problem,
    split_by_scope,
)

EVERYONE_TOPGG = dict(view_channel=True, send_messages=True, embed_links=True,
                      attach_files=True, read_message_history=True, add_reactions=True,
                      external_emojis=True, connect=True, speak=True)


class FRole:
    def __init__(self, rid, position, perms=None, managed=False, name="r"):
        self.id, self.position, self.managed, self.name = rid, position, managed, name
        self.permissions = discord.Permissions(**(perms or {}))
        self.mention = f"<@&{rid}>"

    def __ge__(self, other):
        return self.position >= other.position

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        return isinstance(other, FRole) and other.id == self.id


class FMember:
    def __init__(self, mid, roles, timed_out=False):
        self.id, self.roles, self._timed_out = mid, roles, timed_out
        self.top_role = max(roles, key=lambda r: r.position)

    def is_timed_out(self):
        return self._timed_out

    @property
    def guild_permissions(self):
        total = discord.Permissions.none()
        for r in self.roles:
            total.value |= r.permissions.value
        if total.administrator:
            return discord.Permissions.all()
        if self._timed_out:
            return discord.Permissions(view_channel=True, read_message_history=True)
        return total


class FChannel:
    def __init__(self, guild, cid, overwrites=None, obfuscated=False, name="chan"):
        self.guild, self.id, self.name = guild, cid, name
        self.overwrites = overwrites or {}
        self.mention = f"<#{cid}>"
        self.flags = SimpleNamespace(value=(1 << 17) if obfuscated else 0)

    def permissions_for(self, member):
        base = member.guild_permissions
        if base.administrator:
            return base
        value = base.value
        ow = self.overwrites

        def apply(target_ids, kind):
            nonlocal value
            for target, o in ow.items():
                if target.id in target_ids:
                    allow, deny = o.pair()
                    value = (value & ~deny.value | allow.value) if kind == "both" else value

        # everyone, then roles (deny all then allow all), then member
        ev = [t for t in ow if t.id == self.guild.id]
        for t_ in ev:
            a, d = ow[t_].pair()
            value = (value & ~d.value) | a.value
        role_ids = {r.id for r in member.roles if r.id != self.guild.id}
        deny = allow = 0
        for t_, o in ow.items():
            if t_.id in role_ids:
                a, d = o.pair()
                deny |= d.value
                allow |= a.value
        value = (value & ~deny) | allow
        for t_, o in ow.items():
            if t_.id == member.id:
                a, d = o.pair()
                value = (value & ~d.value) | a.value
        perms = discord.Permissions(value)
        if member._timed_out:
            perms.value &= discord.Permissions(view_channel=True, read_message_history=True).value
        return perms


class FGuild:
    def __init__(self, bot_perms=None, extra_roles=(), timed_out=False, channels=()):
        self.id = 1
        self.default_role = FRole(1, 0, EVERYONE_TOPGG, name="@everyone")
        self.bot_role = FRole(50, 5, bot_perms or {}, name="Moddy")
        self.me = FMember(99, [self.default_role, self.bot_role, *extra_roles], timed_out)
        self._channels = {}
        for c in channels:
            self.add(c)
        self.roles_by_id = {1: self.default_role, 50: self.bot_role}

    def add(self, channel):
        self._channels[channel.id] = channel
        return channel

    @property
    def channels(self):
        return list(self._channels.values())

    def get_channel_or_thread(self, cid):
        return self._channels.get(cid)

    def get_role(self, rid):
        return self.roles_by_id.get(rid)


def ow(**kw):
    return discord.PermissionOverwrite(**kw)


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #

def test_scopes_are_disjoint_and_valid():
    assert not CHANNEL_SCOPED_PERMISSIONS & GUILD_SCOPED_PERMISSIONS
    for key in CHANNEL_SCOPED_PERMISSIONS | GUILD_SCOPED_PERMISSIONS:
        assert hasattr(discord.Permissions, key), key
    guild_keys, chan_keys = split_by_scope(
        ["send_messages", "manage_roles", "manage_webhooks", "kick_members", "send_messages"])
    assert guild_keys == ["manage_roles", "kick_members"]
    assert chan_keys == ["send_messages", "manage_webhooks"]


def test_every_module_permission_is_classified_somewhere_sensible():
    from tests.test_bot_permissions import MODULES
    for cls in MODULES.values():
        guild_keys, chan_keys = split_by_scope(cls.required_permissions())
        assert set(guild_keys) | set(chan_keys) == set(cls.required_permissions())
        for key in ("manage_roles", "kick_members", "ban_members", "moderate_members"):
            assert key not in chan_keys


# --------------------------------------------------------------------------- #
# Top.gg review setups
# --------------------------------------------------------------------------- #

REQ = ["view_channel", "send_messages", "read_message_history", "use_external_emojis",
       "manage_webhooks"]


class TestTopggSetups:
    def test_everyone_perms_only_and_channel_grants_webhooks(self):
        """permissions=0 invite, then 'Allow Manage Webhooks' only in the log channel."""
        guild = FGuild()
        guild.add(FChannel(guild, 10, {guild.bot_role: ow(manage_webhooks=True)}))
        gaps = analyze_requirements(guild, REQ, {10: REQ})
        assert not gaps.any

    def test_everyone_perms_only_reports_webhooks_where_to_grant(self):
        guild = FGuild()
        guild.add(FChannel(guild, 10))
        gaps = analyze_requirements(guild, REQ, {10: REQ})
        assert gaps.channels == {10: ["manage_webhooks"]}
        text = format_gaps(gaps, "en-US")
        assert "<#10>" in text and "Manage webhooks" in text
        assert "explicitly denies" not in text

    def test_everyone_role_cannot_send_but_channel_allows(self):
        guild = FGuild()
        guild.default_role.permissions = discord.Permissions(view_channel=True)
        guild.add(FChannel(guild, 10, {guild.bot_role: ow(
            send_messages=True, read_message_history=True, external_emojis=True)}))
        gaps = analyze_requirements(
            guild, ["view_channel", "send_messages", "read_message_history",
                    "use_external_emojis"], {10: ["view_channel", "send_messages"]})
        assert not gaps.any, gaps

    def test_logging_category_denies_webhooks_to_everyone(self):
        """Logging category: @everyone denied manage_webhooks. Server-wide grant
        would be overridden: the message must say to edit the channel."""
        guild = FGuild(bot_perms=dict(manage_webhooks=True))
        guild.add(FChannel(guild, 10, {guild.default_role: ow(manage_webhooks=False)}))
        gaps = analyze_requirements(guild, REQ, {10: REQ})
        assert gaps.channels == {10: ["manage_webhooks"]}
        assert gaps.denied == {10: ["manage_webhooks"]}
        text = format_gaps(gaps, "en-US")
        assert "explicitly denies" in text and "Permissions" in text

    def test_role_overwrite_allow_beats_everyone_deny(self):
        guild = FGuild()
        guild.add(FChannel(guild, 10, {guild.default_role: ow(manage_webhooks=False),
                                       guild.bot_role: ow(manage_webhooks=True)}))
        assert not analyze_requirements(guild, REQ, {10: REQ}).any

    def test_logging_category_hides_channel_from_everyone(self):
        guild = FGuild()
        guild.add(FChannel(guild, 10, {guild.default_role: ow(view_channel=False)}))
        gaps = analyze_requirements(guild, REQ, {10: REQ})
        assert gaps.hidden == [10]
        assert gaps.channels[10][0] == "view_channel"
        assert "View channel" in format_gaps(gaps, "en-US")

    def test_manage_roles_is_guild_level_even_if_channel_allows_it(self):
        """'Allow Manage Roles' on a channel does not give Add Role: guild level."""
        guild = FGuild()
        guild.add(FChannel(guild, 10, {guild.bot_role: ow(manage_roles=True)}))
        gaps = analyze_requirements(guild, ["manage_roles"], {10: ["view_channel"]})
        assert gaps.guild == ["manage_roles"] and gaps.blocking
        text = format_gaps(gaps, "en-US")
        assert "Manage roles" in text and "Roles" in text

    def test_allow_manage_roles_role_fixes_it(self):
        guild = FGuild(bot_perms=dict(manage_roles=True))
        assert not analyze_requirements(guild, ["manage_roles"], {}).any

    def test_manage_roles_named_manage_permissions_on_a_channel(self):
        guild = FGuild()
        guild.add(FChannel(guild, 10))
        gaps = analyze_requirements(guild, [], {10: ["view_channel", "manage_roles"]})
        assert "Manage permissions" in format_gaps(gaps, "en-US")


# --------------------------------------------------------------------------- #
# Fallback when no channel is configured, edge cases
# --------------------------------------------------------------------------- #

def test_no_channel_configured_accepts_a_grant_on_any_channel():
    guild = FGuild()
    guild.add(FChannel(guild, 5, {guild.bot_role: ow(manage_webhooks=True)}))
    assert not analyze_requirements(guild, REQ, {}).any


def test_no_channel_configured_nowhere_held_says_server_wide_or_channel():
    guild = FGuild()
    guild.add(FChannel(guild, 5))
    gaps = analyze_requirements(guild, REQ, {})
    assert gaps.anywhere == ["manage_webhooks"] and gaps.blocking
    assert "server-wide" in format_gaps(gaps, "en-US")


def test_configured_channel_deleted_is_reported_not_blocking_not_crashing():
    guild = FGuild()
    guild.add(FChannel(guild, 5, {guild.bot_role: ow(manage_webhooks=True)}))
    gaps = analyze_requirements(guild, REQ, {777: REQ})
    assert gaps.gone == [777] and not gaps.blocking
    assert "<#777>" in format_gaps(gaps, "en-US")


def test_obfuscated_channel_never_shows_the_placeholder_name():
    guild = FGuild()
    guild.add(FChannel(guild, 10, {guild.default_role: ow(view_channel=False)},
                       obfuscated=True, name="___hidden___"))
    gaps = analyze_requirements(guild, REQ, {10: REQ})
    assert gaps.hidden == [10]
    text = format_gaps(gaps, "en-US")
    assert "___hidden___" not in text and "<#10>" in text
    assert explicit_channel_deny(guild.get_channel_or_thread(10), guild.me, "view_channel") is False
    msg = channel_validation_error(guild.get_channel_or_thread(10), guild, REQ, "en-US")
    assert "___hidden___" not in msg and "cannot see <#10>" in msg


def test_bot_timed_out_reports_only_the_timeout():
    guild = FGuild(bot_perms=dict(manage_roles=True), timed_out=True)
    gaps = analyze_requirements(guild, REQ + ["manage_roles"], {10: REQ})
    assert gaps.timed_out and gaps.blocking and not gaps.guild
    assert "timed out" in format_gaps(gaps, "en-US")


def test_malformed_requirements_do_not_crash():
    guild = FGuild()
    gaps = analyze_requirements(guild, REQ, {"abc": REQ, None: REQ, "12": REQ})
    assert gaps.gone == [12]


def test_invite_permissions_never_administrator_and_only_missing():
    perms = invite_permissions_for(["administrator", "manage_webhooks", "nonsense",
                                    "manage_permissions", "kick_members"])
    assert perms.manage_webhooks and perms.kick_members
    assert not perms.administrator and not perms.send_messages
    guild = FGuild()
    guild.add(FChannel(guild, 10))
    gaps = analyze_requirements(guild, REQ + ["manage_roles"], {10: REQ})
    assert set(gaps.invite_keys()) == {"manage_roles", "manage_webhooks"}


# --------------------------------------------------------------------------- #
# check_bot_perms end to end
# --------------------------------------------------------------------------- #

def _inter(guild, done=True):
    return SimpleNamespace(
        guild=guild, client=SimpleNamespace(user=SimpleNamespace(id=42)),
        response=SimpleNamespace(is_done=lambda: done, send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()), locale="en-US",
        user=SimpleNamespace(id=1), data={})


async def test_check_bot_perms_channel_only_gap_warns_but_opens_panel():
    from modules.configs._common import check_bot_perms
    guild = FGuild()
    guild.add(FChannel(guild, 10))
    inter = _inter(guild)
    ok = await check_bot_perms(inter, REQ, module_name="X", followup=True,
                               channel_requirements={10: REQ})
    assert ok is True  # the panel is where the channel is changed
    inter.followup.send.assert_awaited_once()


async def test_check_bot_perms_guild_gap_blocks_with_scoped_invite():
    from modules.configs._common import check_bot_perms
    guild = FGuild()
    guild.add(FChannel(guild, 10, {guild.bot_role: ow(manage_webhooks=True)}))
    inter = _inter(guild)
    ok = await check_bot_perms(inter, REQ + ["manage_roles"], module_name="X",
                               followup=True, channel_requirements={10: REQ})
    assert ok is False
    view = inter.followup.send.await_args.kwargs["view"]
    link = next(c for c in view.walk_children() if getattr(c, "url", None))
    value = int(link.url.split("permissions=")[1].split("&")[0])
    assert value == discord.Permissions(manage_roles=True).value


async def test_check_bot_perms_bot_not_in_guild_answers_cleanly():
    from modules.configs._common import check_bot_perms
    guild = SimpleNamespace(me=None, id=5)
    inter = _inter(guild, done=False)
    assert await check_bot_perms(inter, REQ) is False
    inter.response.send_message.assert_awaited_once()


async def test_check_bot_perms_timed_out_has_no_invite_button():
    from modules.configs._common import check_bot_perms
    guild = FGuild(timed_out=True)
    inter = _inter(guild)
    assert await check_bot_perms(inter, REQ, followup=True) is False
    view = inter.followup.send.await_args.kwargs["view"]
    assert not any(getattr(c, "url", None) for c in view.walk_children())


# --------------------------------------------------------------------------- #
# Role hierarchy
# --------------------------------------------------------------------------- #

def test_role_assignment_problems():
    guild = FGuild()
    high = FRole(60, 9, name="Admin")
    managed = FRole(61, 1, managed=True)
    low = FRole(62, 2)
    guild.roles_by_id.update({60: high, 61: managed, 62: low})
    assert role_assignment_problem(guild, low) is None
    assert role_assignment_problem(guild, 60) == "above"
    assert role_assignment_problem(guild, 61) == "managed"
    assert role_assignment_problem(guild, 1) == "everyone"
    assert role_assignment_problem(guild, 999) == "missing"
    msg = role_assignment_error(guild, [high, managed], "en-US")
    assert "<@&60>" in msg and "Server Settings" in msg and "<@&50>" in msg
    assert role_assignment_error(guild, [low], "en-US") is None


async def test_refuse_unassignable_roles_tells_the_admin():
    from modules.configs._common import refuse_unassignable_roles
    guild = FGuild()
    guild.roles_by_id[60] = FRole(60, 9)
    inter = _inter(guild)
    assert await refuse_unassignable_roles(inter, [60]) is True
    inter.followup.send.assert_awaited_once()
    assert await refuse_unassignable_roles(_inter(guild), []) is False


def test_channel_label_never_leaks_placeholder():
    from modules.configs._common import channel_label
    guild = FGuild()
    guild.add(FChannel(guild, 10, obfuscated=True, name="___hidden___"))
    guild.add(FChannel(guild, 11, name="general"))
    bot = SimpleNamespace(get_channel=lambda cid: guild.get_channel_or_thread(cid))
    assert channel_label(bot, 10) == "#10"
    assert channel_label(bot, "11") == "#general"
    assert channel_label(bot, 999) == "#999"
    assert channel_label(bot, "garbage") == "#garbage"
    assert channel_label(None, None) == "#"


# --------------------------------------------------------------------------- #
# Module manager resilience
# --------------------------------------------------------------------------- #

class BoomModule(ModuleBase):
    MODULE_ID = "boom"
    MODULE_NAME = "Boom"
    MODULE_DESCRIPTION = "d"
    SENDS_MESSAGES = False

    async def load_config(self, config_data):
        raise RuntimeError("bad stored config")

    async def validate_config(self, config_data):
        return True, None

    def get_default_config(self):
        return {}


class OkModule(ModuleBase):
    MODULE_ID = "fine"
    MODULE_NAME = "Fine"
    MODULE_DESCRIPTION = "d"
    SENDS_MESSAGES = False

    async def load_config(self, config_data):
        self.enabled = True
        return True

    async def validate_config(self, config_data):
        return True, None

    def get_default_config(self):
        return {}


class FakeDB:
    def __init__(self, row, fail=False):
        self.row, self.fail = row, fail

    async def get_guild(self, gid):
        if self.fail:
            raise ConnectionError("db down")
        return self.row

    async def update_guild_data(self, gid, path, value):
        if self.fail:
            raise ConnectionError("db down")


def make_mgr(row, fail=False):
    bot = SimpleNamespace(db=FakeDB(row, fail), module_slash_commands={})
    mgr = ModuleManager(bot)
    mgr.register_module(BoomModule)
    mgr.register_module(OkModule)
    return mgr


async def test_one_module_failing_to_load_does_not_stop_the_others():
    mgr = make_mgr({"data": {"modules": {"boom": {"x": 1}, "fine": {"y": 2}}}})
    await mgr.load_guild_modules(1)
    assert "fine" in mgr.active_modules[1] and "boom" not in mgr.active_modules[1]


@pytest.mark.parametrize("row", [
    None, {}, {"data": None}, {"data": []}, {"data": {"modules": "oops"}},
    {"data": {"modules": {"fine": "not a dict", "boom": 5}}},
    {"data": {"modules": {"fine": None}}},
])
async def test_malformed_stored_rows_load_without_exploding(row):
    mgr = make_mgr(row)
    await mgr.load_guild_modules(1)
    statuses = await mgr.get_module_statuses(1)
    assert set(statuses) == {"boom", "fine"}
    assert await mgr.get_module_config(1, "fine") in (None, {})
    assert await mgr.get_foreign_modules(1) == {}


async def test_db_outage_on_save_is_a_clean_message_not_the_exception():
    mgr = make_mgr({"data": {}}, fail=True)
    ok, error = await mgr.save_module_config(1, "fine", {"a": 1})
    assert ok is False
    assert "db down" not in error and "ConnectionError" not in error
    assert "try again" in error.lower()


async def test_outage_never_looks_like_an_empty_config_for_writers():
    mgr = make_mgr({"data": {}}, fail=True)
    assert await mgr.get_module_config(1, "fine") is None
    with pytest.raises(ConnectionError):
        await mgr.get_module_config(1, "fine", raise_errors=True)


async def test_save_with_a_non_dict_config_is_a_clean_failure():
    mgr = make_mgr({"data": {"modules": {}}})
    ok, error = await mgr.save_module_config(1, "fine", "garbage")
    assert ok is False and "Traceback" not in error


# --------------------------------------------------------------------------- #
# i18n
# --------------------------------------------------------------------------- #

NEW_KEYS = [
    ("modules", "config", "errors", "missing_bot_perms", k)
    for k in ("description", "hint_guild", "hint_anywhere", "hint_channel",
              "hint_denied", "hidden", "gone", "timed_out")
] + [("modules", "config", "errors", "role_problem", k)
     for k in ("above", "managed", "everyone", "missing")] + [
    ("modules", "config", "errors", "save_failed"),
    ("modules", "config", "errors", "unavailable"),
]


@pytest.mark.parametrize("locale", ["fr", "en-US", "es-ES", "pt-BR", "de"])
def test_new_strings_exist_in_every_locale(locale):
    data = json.loads((Path(__file__).parent.parent / "locales" / f"{locale}.json")
                      .read_text(encoding="utf8"))
    for path in NEW_KEYS:
        node = data
        for part in path:
            node = node[part]
        assert isinstance(node, str) and node.strip()
    placeholders = {"hint_denied": ("{channel}", "{permissions}"), "hidden": ("{channel}",),
                    "gone": ("{channel}",)}
    m = data["modules"]["config"]["errors"]["missing_bot_perms"]
    for key, needed in placeholders.items():
        for ph in needed:
            assert ph in m[key], (locale, key)
    assert "{roles}" in data["modules"]["config"]["errors"]["role_problem"]["above"]
