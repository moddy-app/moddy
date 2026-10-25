"""Auto Role: roles given while a member wears the server tag.

    pytest tests/test_auto_role_tags.py -q
"""

import asyncio
from types import SimpleNamespace

import pytest

from utils import guild_tags

GUILD_ID = 1


# --------------------------------------------------------------- fixtures

class FakeRole:
    def __init__(self, role_id, position=1, managed=False):
        self.id = role_id
        self.position = position
        self.managed = managed
        self.name = f"r{role_id}"
        self.mention = f"<@&{role_id}>"

    def __lt__(self, other):
        return self.position < other.position

    def __ge__(self, other):
        return self.position >= other.position


class FakeMember:
    def __init__(self, member_id=42, roles=(), bot=False, primary_guild=None):
        self.id = member_id
        self.name = f"m{member_id}"
        self.bot = bot
        self.roles = list(roles)
        self.primary_guild = primary_guild
        self.added, self.removed = [], []

    async def add_roles(self, *roles, reason=None):
        self.added.extend(roles)
        self.roles.extend(r for r in roles if r not in self.roles)

    async def remove_roles(self, *roles, reason=None):
        self.removed.extend(roles)
        self.roles = [r for r in self.roles if r not in roles]


def wearing(guild_id=GUILD_ID, enabled=True):
    return SimpleNamespace(id=guild_id, identity_enabled=enabled, tag="MODY")


TAG_ROLE = FakeRole(10)
MEMBER_ROLE = FakeRole(11)
ROLES = {r.id: r for r in (TAG_ROLE, MEMBER_ROLE)}


def build_module(config, *, altguard=None, features=()):
    from modules.auto_role import AutoRoleModule

    guild = SimpleNamespace(
        id=GUILD_ID, features=list(features), get_role=ROLES.get,
        me=SimpleNamespace(top_role=FakeRole(99, position=99),
                           guild_permissions=SimpleNamespace(manage_roles=True)),
    )

    class FakeModuleManager:
        async def get_module_instance(self, guild_id, module_id):
            return altguard if module_id == "altguard" else None

    bot = SimpleNamespace(get_guild=lambda gid: guild if gid == GUILD_ID else None,
                          module_manager=FakeModuleManager(), stats=None)
    module = AutoRoleModule(bot, GUILD_ID)
    module.count = lambda *a, **k: None
    asyncio.run(module.load_config(config))
    return module, guild


@pytest.fixture(autouse=True)
def _clean_tags():
    guild_tags._TAGS.clear()
    yield
    guild_tags._TAGS.clear()


# --------------------------------------------------------------- guild_tags

def test_wears_tag_needs_this_guild_and_identity_enabled():
    assert guild_tags.wears_tag(FakeMember(primary_guild=wearing()), GUILD_ID)
    assert not guild_tags.wears_tag(FakeMember(primary_guild=wearing(guild_id=2)), GUILD_ID)
    assert not guild_tags.wears_tag(FakeMember(primary_guild=wearing(enabled=False)), GUILD_ID)
    assert not guild_tags.wears_tag(FakeMember(primary_guild=wearing(enabled=None)), GUILD_ID)
    assert not guild_tags.wears_tag(FakeMember(), GUILD_ID)


def test_guild_has_tag_from_feature_profile_and_wearers():
    guild = SimpleNamespace(id=GUILD_ID, features=[])
    assert not guild_tags.guild_has_tag(guild)
    assert not guild_tags.guild_has_tag(None)

    guild.features = ["GUILD_TAGS"]
    assert guild_tags.guild_has_tag(guild)  # can set one, nothing says otherwise

    guild_tags.remember_from_guild_payload({"id": str(GUILD_ID), "profile": None})
    assert not guild_tags.guild_has_tag(guild)  # the payload says: no tag

    guild_tags.remember_from_user_payload(GUILD_ID, {"primary_guild": {
        "identity_guild_id": str(GUILD_ID), "identity_enabled": True, "tag": "MODY"}})
    assert guild_tags.guild_has_tag(guild)
    assert guild_tags.known_tag(GUILD_ID) == "MODY"


def test_payload_without_profile_or_other_guild_teaches_nothing():
    guild_tags.remember_from_guild_payload({"id": "1", "name": "x"})
    guild_tags.remember_from_user_payload(GUILD_ID, {"primary_guild": {
        "identity_guild_id": "2", "identity_enabled": True, "tag": "OTHR"}})
    assert guild_tags.known_tag(GUILD_ID) is None


# --------------------------------------------------------------- module

def test_tag_roles_alone_enable_the_module():
    module, _ = build_module({"tag_roles": [10]})
    assert module.enabled


def test_validate_accepts_tag_roles_only():
    module, _ = build_module({"tag_roles": [10]})
    ok, error = asyncio.run(module.validate_config({"tag_roles": [10]}))
    assert ok, error
    ok, _ = asyncio.run(module.validate_config({"tag_roles": [404]}))
    assert not ok


def test_sync_adds_then_removes_the_tag_roles():
    module, _ = build_module({"tag_roles": [10]})
    member = FakeMember(primary_guild=wearing())
    asyncio.run(module.sync_tag_roles(member))
    assert member.added == [TAG_ROLE]

    asyncio.run(module.sync_tag_roles(member))  # our own update: no-op
    assert member.added == [TAG_ROLE]

    member.primary_guild = wearing(enabled=False)
    asyncio.run(module.sync_tag_roles(member))
    assert member.removed == [TAG_ROLE] and TAG_ROLE not in member.roles


def test_sync_leaves_other_roles_and_bots_alone():
    module, _ = build_module({"tag_roles": [10], "member_roles": [11]})
    member = FakeMember(roles=[MEMBER_ROLE])
    asyncio.run(module.sync_tag_roles(member))
    assert member.removed == [] and member.roles == [MEMBER_ROLE]

    bot = FakeMember(bot=True, primary_guild=wearing())
    asyncio.run(module.sync_tag_roles(bot))
    assert bot.added == []


def test_sync_waits_for_altguard():
    class Gate:
        enabled = True

        async def is_verified(self, user_id):
            return False

    module, _ = build_module({"tag_roles": [10]}, altguard=Gate())
    member = FakeMember(primary_guild=wearing())
    asyncio.run(module.sync_tag_roles(member))
    assert member.added == []


def test_join_while_wearing_the_tag_gets_both_lists():
    module, _ = build_module({"tag_roles": [10], "member_roles": [11]})
    member = FakeMember(primary_guild=wearing())
    asyncio.run(module.apply_roles(member))
    assert member.added == [MEMBER_ROLE, TAG_ROLE]

    other = FakeMember(member_id=43)
    asyncio.run(module.apply_roles(other))
    assert other.added == [MEMBER_ROLE]


# --------------------------------------------------------------- config panel

def _select_ids(view):
    from discord import ui
    return [c.custom_id for c in view.walk_children() if isinstance(c, ui.RoleSelect)]


def _view(features, config=None):
    from modules.configs.auto_role_config import AutoRoleConfigView, _CID_TAG_ROLES
    _, guild = build_module(config or {"member_roles": [11]}, features=features)
    guild.get_role = lambda role_id: None  # RoleSelect defaults need real discord.Role
    bot = SimpleNamespace(get_guild=lambda gid: guild)
    return AutoRoleConfigView(bot, GUILD_ID, 2, "en-US", config), _CID_TAG_ROLES


def test_panel_hides_the_tag_section_without_a_tag():
    view, cid = _view([])
    assert cid not in _select_ids(view)


def test_panel_shows_the_tag_section_with_a_tag():
    guild_tags.remember(GUILD_ID, "MODY")
    view, cid = _view([], {"tag_roles": [10]})
    assert cid in _select_ids(view)
    assert view.has_existing_config


def test_registration_shell_always_carries_the_tag_select():
    from modules.configs.auto_role_config import AutoRoleConfigView, _CID_TAG_ROLES
    assert _CID_TAG_ROLES in _select_ids(AutoRoleConfigView())


# --------------------------------------------------------------- gateway hook

def test_parser_wrapper_learns_and_restores():
    from cogs.server_tag_events import ServerTagEvents

    seen = []
    parsers = {"GUILD_UPDATE": lambda data: seen.append(data)}
    bot = SimpleNamespace(_connection=SimpleNamespace(parsers=parsers), module_manager=None)
    cog = ServerTagEvents(bot)
    original = parsers["GUILD_UPDATE"]
    cog._install_parsers()
    assert parsers["GUILD_UPDATE"] is not original

    payload = {"id": str(GUILD_ID), "profile": {"tag": "MODY", "badge": "abc"}}
    parsers["GUILD_UPDATE"](payload)
    assert seen == [payload]
    assert guild_tags.known_tag(GUILD_ID) == "MODY"

    cog._remove_parsers()
    assert parsers["GUILD_UPDATE"] is original
