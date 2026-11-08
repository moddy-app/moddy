"""The AltGuard mini-bot (minibots/bots/altguard.py) and the pieces it needs
from Moddy: the members intent without a member cache, departures without a
cache, the verdict subscription, the standalone /config screen and the Auto
Role hand-off between the two processes.

    pytest tests/test_altguard_minibot.py -q
"""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from db.repositories.altguard import SOURCE_SERVICE, STATUS_VERIFIED
from minibots import status as mstatus
from minibots.ownership import APP_KEY, MODDY_APP

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ("fr", "en-US", "es-ES", "pt-BR", "de")
APP = "altguard_bot"
GUILD = 1

GATE = {"channel_id": 100, "unverified_role_id": 10, "verified_role_id": 11,
        "log_channel_id": None, "message_id": None}


# --------------------------------------------------------------------------- #
# Spec
# --------------------------------------------------------------------------- #

class TestSpec:
    def test_declared_and_valid(self):
        from minibots.spec import app_display_name, get_spec
        spec = get_spec(APP)
        assert spec is not None and spec.name == "AltGuard"
        assert app_display_name(APP) == "AltGuard"
        assert spec.modules == ("altguard",) and spec.token_env == "MINIBOT_ALTGUARD_TOKEN"
        # Joins (members), bans (moderation), invite baseline (invites).
        assert {"members", "moderation", "invites"} <= set(spec.intents)
        assert "presences" not in spec.intents and "message_content" not in spec.intents
        assert spec.web_surfaces == {"altguard_verification"}
        import importlib
        for path in (*spec.persistent_views, spec.welcome_builder):
            module, _, attr = path.partition(":")
            assert getattr(importlib.import_module(module), attr)

    def test_invite_link_asks_exactly_what_the_gate_uses(self):
        from minibots.builtin import invite_permissions
        from minibots.spec import get_spec
        from modules.altguard import AltGuardModule
        client = SimpleNamespace(
            spec=get_spec(APP),
            module_manager=SimpleNamespace(registered_modules={"altguard": AltGuardModule}))
        perms = discord.Permissions(invite_permissions(client))
        assert perms.manage_roles and perms.view_channel and perms.send_messages
        assert perms.manage_guild  # inviter attribution (optional extra)
        assert perms.view_audit_log  # kick vs leave, installer welcome
        assert not perms.administrator  # rule #12

    def test_the_client_gets_member_events_but_no_member_cache(self):
        from minibots.client import MiniBotClient
        from minibots.spec import get_spec
        client = MiniBotClient(get_spec(APP), SimpleNamespace(db=None, redis=None, tech_logger=None))
        assert client.intents.members and client.intents.bans and client.intents.invites
        assert not client.intents.presences and not client.intents.message_content
        assert client._connection.member_cache_flags.value == 0
        assert client._connection._chunk_guilds is False
        assert client.module_manager.app == APP


def test_the_bot_imports_stay_light():
    code = (
        "import sys, os; os.environ['MODDY_SERVICE']='minibots';"
        "import minibots.client, minibots.bots.altguard, modules.altguard,"
        " modules.configs.altguard_config, utils.altguard_views;"
        "heavy=[m for m in ('bot','onnxruntime','PIL','c2pa','numpy','openai',"
        "'fastapi','uvicorn','cogs.automod','cogs.tickets') if m in sys.modules];"
        "print('HEAVY=' + ','.join(heavy))"
    )
    env = {k: v for k, v in __import__("os").environ.items() if k != "DISCORD_TOKEN"}
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                         text=True, env=env, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "HEAVY="


# --------------------------------------------------------------------------- #
# Redis contract: minibots:moddy events
# --------------------------------------------------------------------------- #

class TestModdyEvents:
    def test_round_trip(self):
        raw = mstatus.moddy_event(mstatus.EVT_ALTGUARD_GATE_OPENED, app=APP,
                                  guild_id="1", user_id="42")
        event = mstatus.parse_moddy_event(json.loads(raw))
        assert event["type"] == "altguard_gate_opened" and event["app"] == APP
        assert (event["guild_id"], event["user_id"]) == (1, 42)

    @pytest.mark.parametrize("data", [
        None, "x", {"type": "release", "guild_id": 1, "user_id": 2},
        {"type": "altguard_gate_opened", "guild_id": "a", "user_id": 2},
        {"type": "altguard_gate_opened", "guild_id": 1},
        {"type": "altguard_gate_opened", "guild_id": 0, "user_id": 2},
    ])
    def test_rejects_malformed(self, data):
        assert mstatus.parse_moddy_event(data) is None

    def test_unknown_event_type_refused(self):
        with pytest.raises(ValueError):
            mstatus.moddy_event("grant_admin", guild_id=1)


# --------------------------------------------------------------------------- #
# A gate run by another app, seen from Moddy's Auto Role
# --------------------------------------------------------------------------- #

def test_gate_configured():
    from modules.altguard import gate_configured
    assert gate_configured(GATE)
    assert gate_configured({**GATE, APP_KEY: APP})
    assert not gate_configured({**GATE, "verified_role_id": None})
    assert not gate_configured({**GATE, "state": "paused"})
    assert not gate_configured(None) and not gate_configured({})


class _GateDB:
    def __init__(self, altguard=None, verified=()):
        self.altguard = altguard
        self.verified = set(verified)
        self.fail = False

    async def get_guild(self, guild_id):
        if self.fail:
            raise RuntimeError("database down")
        modules = {"altguard": self.altguard} if self.altguard is not None else {}
        return {"data": {"modules": modules}, "attributes": {}}

    async def is_altguard_verified(self, guild_id, user_id):
        return user_id in self.verified


def _moddy_auto_role(db):
    """Moddy's Auto Role with a real ModuleManager (AltGuard registered)."""
    from modules.altguard import AltGuardModule
    from modules.auto_role import AutoRoleModule
    from modules.module_manager import ModuleManager
    bot = SimpleNamespace(db=db, module_slash_commands={}, get_guild=lambda gid: None,
                          resync_module_commands=AsyncMock())
    manager = ModuleManager(bot, app=MODDY_APP)
    manager.register_module(AltGuardModule)
    manager.register_module(AutoRoleModule)
    bot.module_manager = manager
    return AutoRoleModule(bot, GUILD)


def _member(member_id=42, bot=False):
    return SimpleNamespace(id=member_id, bot=bot, roles=[])


class TestForeignGate:
    async def test_holds_back_until_the_member_passes(self):
        db = _GateDB({**GATE, APP_KEY: APP})
        auto_role = _moddy_auto_role(db)
        assert await auto_role._altguard_holds_back(_member()) is True
        db.verified.add(42)
        assert await auto_role._altguard_holds_back(_member()) is False

    async def test_bots_are_never_held_back(self):
        auto_role = _moddy_auto_role(_GateDB({**GATE, APP_KEY: APP}))
        assert await auto_role._altguard_holds_back(_member(bot=True)) is False

    @pytest.mark.parametrize("config", [
        None,                                            # no AltGuard at all
        {**GATE, APP_KEY: APP, "verified_role_id": None},  # incomplete gate
        {**GATE, APP_KEY: APP, "state": "paused"},       # paused there
    ])
    async def test_no_gate_no_hold(self, config):
        auto_role = _moddy_auto_role(_GateDB(config))
        assert await auto_role._altguard_holds_back(_member()) is False

    async def test_fails_closed(self):
        db = _GateDB({**GATE, APP_KEY: APP})
        auto_role = _moddy_auto_role(db)
        db.fail = True
        auto_role.bot.module_manager.active_modules.clear()
        auto_role.bot.module_manager.get_module_config = AsyncMock(
            side_effect=RuntimeError("database down"))
        assert await auto_role._altguard_holds_back(_member()) is True


# --------------------------------------------------------------------------- #
# Hand-off: the mini-bot opens the gate, Moddy hands out the auto roles
# --------------------------------------------------------------------------- #

def _gate_module(app):
    from modules.altguard import AltGuardModule
    redis = SimpleNamespace(publish=AsyncMock())

    async def no_instance(guild_id, module_id):
        return None

    bot = SimpleNamespace(
        db=None, redis=redis, get_guild=lambda gid: None,
        module_manager=SimpleNamespace(app=app, get_module_instance=no_instance))
    return AltGuardModule(bot, GUILD), redis


class TestHandOff:
    async def test_minibot_tells_moddy(self):
        module, redis = _gate_module(APP)
        await module._run_auto_role(_member())
        redis.publish.assert_awaited_once()
        channel, raw = redis.publish.await_args.args
        assert channel == mstatus.MODDY_CONTROL_CHANNEL
        event = mstatus.parse_moddy_event(json.loads(raw))
        assert (event["guild_id"], event["user_id"], event["app"]) == (GUILD, 42, APP)

    async def test_moddy_never_publishes_to_itself(self):
        module, redis = _gate_module(MODDY_APP)
        await module._run_auto_role(_member())
        redis.publish.assert_not_awaited()

    async def test_a_redis_failure_is_contained(self):
        module, redis = _gate_module(APP)
        redis.publish.side_effect = ConnectionError("redis down")
        await module._run_auto_role(_member())  # never raises

    def _moddy(self, *, verified, auto_role_enabled=True):
        member = SimpleNamespace(id=42)
        auto_role = SimpleNamespace(enabled=auto_role_enabled, apply_roles=AsyncMock())
        guild = SimpleNamespace(id=GUILD, get_member=lambda uid: member)

        async def instance(guild_id, module_id):
            return auto_role if module_id == "auto_role" else None

        fake = SimpleNamespace(
            get_guild=lambda gid: guild if gid == GUILD else None,
            module_manager=SimpleNamespace(get_module_instance=instance),
            db=SimpleNamespace(is_altguard_verified=AsyncMock(return_value=verified)))
        return fake, auto_role

    def _event(self, **overrides):
        return {"type": "altguard_gate_opened", "app": APP, "guild_id": str(GUILD),
                "user_id": "42", **overrides}

    async def test_moddy_grants_once_the_database_agrees(self):
        from bot import ModdyBot
        fake, auto_role = self._moddy(verified=True)
        await ModdyBot._handle_altguard_gate_opened(fake, self._event())
        auto_role.apply_roles.assert_awaited_once()

    async def test_an_unsigned_message_cannot_open_a_closed_gate(self):
        from bot import ModdyBot
        fake, auto_role = self._moddy(verified=False)
        await ModdyBot._handle_altguard_gate_opened(fake, self._event())
        auto_role.apply_roles.assert_not_awaited()

    @pytest.mark.parametrize("overrides", [{"guild_id": "999"}, {"user_id": "x"}])
    async def test_other_guilds_and_garbage_are_ignored(self, overrides):
        from bot import ModdyBot
        fake, auto_role = self._moddy(verified=True)
        await ModdyBot._handle_altguard_gate_opened(fake, self._event(**overrides))
        auto_role.apply_roles.assert_not_awaited()

    async def test_nothing_without_auto_role(self):
        from bot import ModdyBot
        fake, auto_role = self._moddy(verified=True, auto_role_enabled=False)
        await ModdyBot._handle_altguard_gate_opened(fake, self._event())
        auto_role.apply_roles.assert_not_awaited()


# --------------------------------------------------------------------------- #
# The cog: joins, departures, verdicts
# --------------------------------------------------------------------------- #

def _cog(module=None, *, audit_log=False):
    from minibots.bots.altguard import AltGuardBotCog
    cog = AltGuardBotCog.__new__(AltGuardBotCog)  # no loop / task at construction
    me = SimpleNamespace(guild_permissions=discord.Permissions(view_audit_log=audit_log))
    guild = SimpleNamespace(id=GUILD, me=me)
    cog.bot = SimpleNamespace(get_guild=lambda gid: guild if gid == GUILD else None,
                              altguard=SimpleNamespace(publish_membership=AsyncMock()))
    cog._module = AsyncMock(return_value=module)
    return cog, guild


class TestCog:
    def test_keeps_the_cog_name_the_module_looks_up(self):
        from minibots.bots.altguard import AltGuardBotCog
        assert AltGuardBotCog.__cog_name__ == "AltGuard"
        listeners = {name for name, _ in AltGuardBotCog.__cog_listeners__}
        assert {"on_member_join", "on_raw_member_remove", "on_member_ban",
                "on_guild_channel_create", "on_invite_create"} <= listeners

    async def test_a_join_is_gated_then_reported(self):
        order = []
        module = SimpleNamespace(on_member_join=AsyncMock(side_effect=lambda m: order.append("gate")))
        cog, guild = _cog(module)
        cog.bot.altguard.publish_membership.side_effect = lambda **kw: order.append(kw["state"])
        member = SimpleNamespace(id=42, bot=False, guild=guild, joined_at=None)
        await cog.on_member_join(member)
        assert order == ["gate", "active"]

    async def test_a_join_where_the_gate_is_off_does_nothing(self):
        cog, guild = _cog(None)
        await cog.on_member_join(SimpleNamespace(id=42, bot=False, guild=guild, joined_at=None))
        cog.bot.altguard.publish_membership.assert_not_awaited()

    async def test_a_departure_without_cache_is_reported(self):
        cog, _ = _cog(SimpleNamespace())
        user = SimpleNamespace(id=42, bot=False)
        await cog.on_raw_member_remove(SimpleNamespace(guild_id=GUILD, user=user))
        kwargs = cog.bot.altguard.publish_membership.await_args.kwargs
        assert (kwargs["guild_id"], kwargs["discord_user_id"], kwargs["state"]) == (GUILD, 42, "left")

    async def test_a_cached_departure_is_left_to_on_member_remove(self):
        cog, _ = _cog(SimpleNamespace())
        member = MagicMock(spec=discord.Member)
        await cog.on_raw_member_remove(SimpleNamespace(guild_id=GUILD, user=member))
        cog.bot.altguard.publish_membership.assert_not_awaited()

    async def test_bots_leaving_are_not_reported(self):
        cog, _ = _cog(SimpleNamespace())
        await cog.on_raw_member_remove(SimpleNamespace(
            guild_id=GUILD, user=SimpleNamespace(id=7, bot=True)))
        cog.bot.altguard.publish_membership.assert_not_awaited()

    @pytest.mark.parametrize("raw, expected", [
        (b'{"verdict": "passed"}', {"verdict": "passed"}),
        ('{"a": 1}', {"a": 1}),
        ("not json", None), ("[1, 2]", None), (None, None),
    ])
    def test_verdict_decoding(self, raw, expected):
        from minibots.bots.altguard import decode_verdict_message
        assert decode_verdict_message(raw) == expected

    async def test_moddy_ignores_a_verdict_for_a_gate_it_does_not_run(self):
        """Both processes hear every verdict: the module check comes BEFORE the
        idempotency insert, or the wrong process would swallow it."""
        from cogs.altguard import AltGuard
        cog = AltGuard.__new__(AltGuard)
        db = SimpleNamespace(record_altguard_verification=AsyncMock(return_value=True))
        cog.bot = SimpleNamespace(db=db)
        cog._module = AsyncMock(return_value=None)  # owned by the AltGuard bot
        await cog.handle_verdict({"verification_id": "v1", "guild_id": GUILD,
                                  "discord_user_id": 42, "verdict": "passed",
                                  "score": 0, "reasons": [], "enforced": True})
        db.record_altguard_verification.assert_not_awaited()


# --------------------------------------------------------------------------- #
# /config: the module screen IS /config in the AltGuard app
# --------------------------------------------------------------------------- #

def _ids(view):
    found = []

    def walk(item):
        cid = getattr(item, "custom_id", None)
        if cid:
            found.append(cid)
        for child in getattr(item, "children", []) or []:
            walk(child)
        inner = getattr(item, "item", None)  # DynamicItem
        if inner is not None:
            walk(inner)
    for child in view.children:
        walk(child)
    return found


def _config_view(standalone, **kwargs):
    from minibots import branding
    from modules.configs.altguard_config import AltGuardConfigView
    token = branding._standalone.set("altguard" if standalone else None)
    try:
        bot = kwargs.pop("bot", SimpleNamespace(get_channel=lambda cid: None,
                                                get_guild=lambda gid: None))
        return AltGuardConfigView(bot, GUILD, 5, "en-US", **kwargs)
    finally:
        branding._standalone.reset(token)


class TestStandaloneConfig:
    def test_moddy_keeps_back_and_trail(self):
        view = _config_view(False, current_config=GATE)
        assert "moddy:altguard:config:back" in _ids(view)
        assert "moddy:config:language" not in _ids(view)
        assert view.children[0].children[0].content.startswith("-# ")

    def test_altguard_app_has_no_back_and_reaches_the_language(self):
        view = _config_view(True, current_config=GATE)
        ids = _ids(view)
        assert "moddy:altguard:config:back" not in ids
        assert "moddy:config:language" in ids
        assert not view.children[0].children[0].content.startswith("-# ")

    def test_the_shell_still_registers_every_button(self):
        view = _config_view(True, bot=None)
        ids = _ids(view)
        for cid in ("moddy:altguard:config:back", "moddy:altguard:config:save",
                    "moddy:altguard:config:cancel"):
            assert cid in ids


# --------------------------------------------------------------------------- #
# Welcome DM + i18n
# --------------------------------------------------------------------------- #

class TestWelcome:
    async def _build(self, stored):
        from minibots.bots.altguard import build_welcome
        from minibots.spec import get_spec
        from modules.module_manager import ModuleManager
        bot = SimpleNamespace(db=None)
        manager = ModuleManager(bot, app=APP)
        manager.get_module_config = AsyncMock(return_value=stored)
        client = SimpleNamespace(spec=get_spec(APP), module_manager=manager)
        guild = SimpleNamespace(id=GUILD, name="Test")
        return await build_welcome(client, guild, "en-US")

    async def test_explains_the_three_things_the_gate_needs(self):
        content, variables, view = await self._build(None)
        assert content.template_id == "minibots.altguard.welcome.body"
        assert "{config}" in content.body and variables["name"] == "AltGuard"
        assert content.accent_color == 0x2EB84A and view is None
        from utils.emojis import SHIELD
        assert content.icon == SHIELD

    async def test_steps_aside_where_moddy_runs_the_gate(self):
        content, _, _ = await self._build(dict(GATE))  # no app key: Moddy's
        assert content.template_id == "minibots.altguard.welcome.elsewhere"


@pytest.mark.parametrize("locale", LOCALES)
def test_i18n(locale):
    data = json.loads((ROOT / "locales" / f"{locale}.json").read_text(encoding="utf-8"))
    welcome = data["minibots"]["bots"]["altguard"]["welcome"]
    for key in ("title", "body", "elsewhere"):
        assert isinstance(welcome[key], str) and welcome[key], key
    assert "{name}" in welcome["title"]
    assert "{config}" in welcome["body"] and "{server}" in welcome["body"]
    assert "mini-bot" not in json.dumps(welcome).lower()


def test_the_bot_paints_its_cards_in_its_own_colour():
    """#2EB84A on the AltGuard bot, the panel colour under Moddy."""
    from minibots.spec import get_spec
    from utils.altguard_views import PANEL_ACCENT, build_link_card, build_verification_panel, panel_accent
    bot = SimpleNamespace(spec=get_spec(APP))
    assert panel_accent(bot).value == 0x2EB84A
    assert panel_accent(SimpleNamespace()) == PANEL_ACCENT
    panel = build_verification_panel("en-US", accent=panel_accent(bot))
    assert panel.children[0].accent_colour.value == 0x2EB84A
    assert build_verification_panel("en-US").children[0].accent_colour == PANEL_ACCENT
    card = build_link_card("en-US", url="https://verify.moddy.app/x", accent=panel_accent(bot))
    assert card.children[0].accent_colour.value == 0x2EB84A


def test_config_title_icon_shield_on_the_bot_fingerprint_on_moddy():
    from utils.emojis import FINGERPRINT, SHIELD
    from minibots.spec import get_spec
    on_bot = _config_view(True, current_config=GATE, bot=SimpleNamespace(
        spec=get_spec(APP), get_channel=lambda cid: None, get_guild=lambda gid: None))
    on_moddy = _config_view(False, current_config=GATE)
    title = lambda view: next(i.content for i in view.children[0].children
                              if getattr(i, "content", "").startswith("### "))
    assert title(on_bot).startswith(f"### {SHIELD} ")
    assert title(on_moddy).startswith(f"### {FINGERPRINT} ")

