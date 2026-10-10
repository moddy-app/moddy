"""Automod member profiles — avatar / banner / names (docs/AUTOMOD_AI.md "Profiles").

No network, no Discord, no DB: every collaborator is a small fake, in the style of
tests/test_automod_images.py.
"""

from __future__ import annotations

import io
import json
import types
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

from automod import constants as ac
from automod.schemas import (
    Decision, ORIGINE_PROFIL_AVATAR, ORIGINE_PROFIL_BANNIERE, ORIGINE_PROFIL_PSEUDO,
)
from modules.automod_ai import (
    AutomodModule, FEATURE_CLASSES, FEATURE_EXTRA_KEYS, ProfileFeature,
)
from services.appeal_service import AppealService
from services.automod_image_service import AutomodImageService
from services.automod_label_service import (
    AutomodLabelService, KIND_PROFIL_AVATAR_CHOQUANT, KIND_PROFIL_AVATAR_NSFW,
    KIND_PROFIL_PSEUDO, build_details, label_kind,
)
from services.automod_profile_service import (
    AutomodProfileService, name_cache_key, normalize_name,
)

NOW = datetime.now(timezone.utc)
ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

def _png(color=(120, 40, 40)) -> bytes:
    img = Image.new("RGB", (256, 256), color)
    for x in range(0, 256, 16):
        for y in range(0, 256, 32):
            img.putpixel((x, y), (255 - color[0], 200, 10))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class FakeAsset:
    def __init__(self, key: str, data: bytes):
        self.key = key
        self.url = f"https://cdn.example/{key}.png"
        self._data = data
        self.reads = 0

    def replace(self, **kwargs):
        return self

    async def read(self):
        self.reads += 1
        return self._data


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, redis):
        self.redis, self.ops = redis, []

    def incr(self, key):
        self.ops.append(("incr", key))

    def expire(self, key, ttl):
        self.ops.append(("expire", key))

    async def execute(self):
        out = []
        for op, key in self.ops:
            if op == "incr":
                self.redis.store[key] = int(self.redis.store.get(key, 0)) + 1
                out.append(self.redis.store[key])
            else:
                out.append(True)
        return out


class FakeVision:
    def __init__(self, adult="UNLIKELY", violence="UNLIKELY"):
        self.calls = []
        self.likelihoods = {"adult": adult, "racy": "UNLIKELY", "violence": violence,
                            "medical": "UNLIKELY", "spoof": "UNLIKELY"}

    async def safe_search(self, data, **kwargs):
        self.calls.append(kwargs)
        lk = dict(self.likelihoods)
        return types.SimpleNamespace(as_dict=lambda: lk)


class FakeDB:
    def __init__(self):
        self.holds = {}
        self.events = []
        self.revoked = []
        self.labels = []

    async def get_profile_hold(self, guild_id, user_id, kind):
        return self.holds.get((guild_id, user_id, kind))

    async def list_member_profile_holds(self, guild_id, user_id):
        return [h for (g, u, _), h in self.holds.items() if (g, u) == (guild_id, user_id)]

    async def upsert_profile_hold(self, guild_id, user_id, kind, **kw):
        self.holds[(guild_id, user_id, kind)] = {
            "guild_id": guild_id, "user_id": user_id, "kind": kind,
            "categorie": kw.get("categorie"), "asset_key": kw.get("asset_key"),
            "case_id": kw.get("case_id"), "sanction_id": kw.get("sanction_id"),
            "timeout_until": kw.get("timeout_until"),
        }

    async def delete_profile_hold(self, guild_id, user_id, kind):
        return self.holds.pop((guild_id, user_id, kind), None) is not None

    async def revoke_sanction(self, sanction_id, by_type, by_id):
        self.revoked.append(sanction_id)
        return True

    async def add_event(self, case_id, etype, **kw):
        self.events.append((case_id, etype, kw))

    async def list_member_sanctions(self, guild_id, user_id, since=None):
        return []

    async def list_image_hashes(self):
        return []


class FakeCases:
    def __init__(self):
        self.recorded = []

    async def record_sanction(self, source, **kw):
        self.recorded.append(kw)
        return {"id": uuid.uuid4(), "reference": f"REF{len(self.recorded)}",
                "sanction_id": uuid.uuid4()}


class FakeNotifications:
    def __init__(self):
        self.sent = []

    async def send_dm(self, user, **kw):
        self.sent.append((user, kw))


class FakeChannel:
    def __init__(self):
        self.id = 77
        self.sent = []

    def permissions_for(self, me):
        return types.SimpleNamespace(send_messages=True)

    async def send(self, **kw):
        self.sent.append(kw)
        return types.SimpleNamespace(id=500 + len(self.sent), channel=self)


def _make_text_channel(monkeypatch):
    import discord
    ch = FakeChannel()
    monkeypatch.setattr(discord, "TextChannel", FakeChannel)
    return ch


class FakeEngine:
    def __init__(self, decision=None, only=None):
        self.calls = []
        self.decision = decision
        #: Predicate on the judged text: names it rejects are not routed at all.
        self.only = only or (lambda text: True)

    async def analyze(self, target, **kwargs):
        self.calls.append((target, kwargs))
        if self.decision is None or not self.only(target.content):
            return None
        d = dataclass_copy(self.decision)
        d.origine = target.origine
        d.contenu_juge = target.content
        d.message_id = target.id
        return d


def dataclass_copy(decision):
    import copy
    return copy.deepcopy(decision)


def _is_bad(text: str) -> bool:
    return "bad" in text.lower()


def _name_decision(sanctionnable=True, categorie="insulte"):
    return Decision(
        message_id="x", auteur_id="1", sanctionnable=sanctionnable, actions=[],
        categorie=categorie, gravite="moyenne", raison="ai reason",
        explication="ai explication", confiance="high", signal_source="regex",
        score_detecteur=0.9, citation="bad", decideur="nano")


class FakeGuild:
    def __init__(self, gid=1, *, nick_perm=True, moderate_perm=True):
        self.id = gid
        self.name = f"Guild{gid}"
        self.owner_id = 9999
        self.owner = types.SimpleNamespace(id=9999)
        self.me = types.SimpleNamespace(
            top_role=100,
            guild_permissions=types.SimpleNamespace(
                manage_nicknames=nick_perm, moderate_members=moderate_perm,
                ban_members=True, manage_messages=True),
        )
        self.channel = FakeChannel()

    def get_channel(self, cid):
        return self.channel

    def get_member(self, uid):
        return None


class FakeMember:
    def __init__(self, guild, *, uid=5, avatar=None, guild_avatar=None, nick=None,
                 global_name="Alice", name="alice", bot=False, roles=(), mod=False,
                 account_days=3, tenure_days=0.01, banner=None):
        self.id = uid
        self.guild = guild
        self.avatar = avatar
        self.guild_avatar = guild_avatar
        self.nick = nick
        self.global_name = global_name
        self.name = name
        self.bot = bot
        self.roles = list(roles)
        self.top_role = 10
        self.guild_permissions = types.SimpleNamespace(manage_messages=mod)
        self.created_at = NOW - timedelta(days=account_days)
        self.joined_at = NOW - timedelta(days=tenure_days)
        self.timed_out_until = None
        self.timeouts = []
        self.edits = []
        self.banner = banner
        self.timeout_error = None

    @property
    def display_avatar(self):
        return self.guild_avatar or self.avatar

    @property
    def display_name(self):
        return self.nick or self.global_name or self.name

    async def timeout(self, duration, *, reason=None):
        self.timeouts.append((duration, reason))
        self.timed_out_until = (NOW + duration) if duration else None

    async def edit(self, **kw):
        self.edits.append(kw)
        if "nick" in kw:
            self.nick = kw["nick"]

    def __eq__(self, other):
        return other is self

    def __hash__(self):
        return id(self)


class Env:
    """One bot + the pieces every test wants, with a module per guild."""

    def __init__(self, monkeypatch, *, vision=None, redis=True, usage=0):
        self.monkeypatch = monkeypatch
        self.vision = vision or FakeVision()
        self.fetched = []
        gw = types.SimpleNamespace(
            vision=self.vision, google_vision_available=lambda: True,
            config=types.SimpleNamespace(model_rate_limits={}),
        )

        async def rl_usage(provider, model):
            return [{"rule": "rpmo", "used": usage, "limit": 1000}]
        gw.rate_limit_usage = rl_usage
        self.bot = types.SimpleNamespace(
            redis=FakeRedis() if redis else None, db=FakeDB(), stats=None, gateway=gw,
            user=types.SimpleNamespace(id=999, name="Moddy"),
            cases=FakeCases(), notifications=FakeNotifications(),
            module_manager=None,
        )
        self.bot.automod_images = AutomodImageService(self.bot)
        self.bot.automod_profiles = AutomodProfileService(self.bot)
        self.bot._automod_engine = FakeEngine()
        self.bot.fetch_user = self._fetch_user
        self.banner_assets = {}
        monkeypatch.setattr("utils.global_sanctions.is_limited",
                            self._not_limited)
        self.channel = None

    async def _fetch_user(self, uid):
        self.fetched.append(uid)
        return types.SimpleNamespace(banner=self.banner_assets.get(uid))

    @staticmethod
    async def _not_limited(bot, **kw):
        return False

    def module(self, guild, *, features=None, sanctions=None, extra=None):
        mod = AutomodModule(self.bot, guild.id)
        mod.guild_locale = lambda g: "en-US"
        self.bot.get_guild = lambda gid, _g=guild: _g
        cfg = {
            "enabled": True, "notify_channel_id": guild.channel.id,
            "ignore_moderators": True, "severity": 3, "max_action": "ban",
            "sanctions": sanctions or {},
            "features": {"profile": {"enabled": True, "avatar": True, "banner": True,
                                     "pseudo": True, **(features or {})}},
            **(extra or {}),
        }
        return mod, cfg


async def _module(env, guild, **kw):
    mod, cfg = env.module(guild, **kw)
    assert await mod.load_config(cfg)
    import discord
    env.monkeypatch.setattr(discord, "TextChannel", FakeChannel)
    return mod


def _nsfw_member(guild, key="a_bad", data=None, **kw):
    asset = FakeAsset(key, data or _png())
    member = FakeMember(guild, avatar=asset, **kw)
    return member, asset


# --------------------------------------------------------------------------- #
# Config / registration
# --------------------------------------------------------------------------- #

async def test_profile_feature_is_registered_with_the_documented_shape():
    assert FEATURE_CLASSES["profile"] is ProfileFeature
    assert FEATURE_EXTRA_KEYS["profile"] == {"avatar": True, "banner": True, "pseudo": True}
    module = AutomodModule(types.SimpleNamespace(), 1)
    assert module.get_default_config()["features"]["profile"] == {
        "enabled": False, "exempt_roles": [], "exempt_channels": [],
        "avatar": True, "banner": True, "pseudo": True}
    assert "manage_nicknames" in AutomodModule.REQUIRED_BOT_PERMISSIONS


async def test_profile_alone_makes_the_module_run_and_validates(monkeypatch):
    env = Env(monkeypatch)
    guild = FakeGuild()
    mod = await _module(env, guild)
    assert mod.enabled and mod.profile_active()
    assert mod._features["profile"].ON_MESSAGE is False
    assert not mod._features["content"].enabled
    # No alert channel: automod does not run, profile included.
    mod2 = AutomodModule(env.bot, 1)
    await mod2.load_config({"enabled": True, "features": {"profile": {"enabled": True}}})
    assert not mod2.enabled
    # validate_config accepts the feature.
    env.bot.get_guild = lambda gid: types.SimpleNamespace(
        get_channel=lambda cid: None)
    ok, err = await mod.validate_config({"features": {"profile": {"enabled": True}}})
    assert ok, err


async def test_on_message_skips_the_profile_feature(monkeypatch):
    env = Env(monkeypatch)
    guild = FakeGuild()
    mod = await _module(env, guild)
    calls = []

    async def boom(message):
        calls.append(1)
        return []
    mod._features["profile"].process = boom
    import discord
    monkeypatch.setattr(discord, "Member", FakeMember)
    author = FakeMember(guild, mod=False)
    message = types.SimpleNamespace(
        author=author, guild=guild, webhook_id=None, content="hi",
        channel=types.SimpleNamespace(id=1, parent_id=None),
        mentions=[], reference=None, type=discord.MessageType.default)
    await mod.on_message(message)
    assert calls == []


# --------------------------------------------------------------------------- #
# Avatars
# --------------------------------------------------------------------------- #

async def test_default_avatar_is_skipped(monkeypatch):
    env = Env(monkeypatch)
    guild = FakeGuild()
    mod = await _module(env, guild)
    member = FakeMember(guild)       # no avatar, no guild avatar
    await mod.screen_member(member, reason="join", scope={"avatar"})
    assert env.vision.calls == [] and member.timeouts == []
    assert env.bot.cases.recorded == []


async def test_nsfw_avatar_applies_a_hold(monkeypatch):
    env = Env(monkeypatch, vision=FakeVision(adult="VERY_LIKELY"))
    guild = FakeGuild()
    mod = await _module(env, guild)
    member, asset = _nsfw_member(guild)
    await mod.screen_member(member, reason="join", scope={"avatar"})

    # SafeSearch ran on the PROFILE budget, accounted separately.
    assert len(env.vision.calls) == 1
    assert env.vision.calls[0]["budget_model"] == "safe_search_profile"
    assert env.vision.calls[0]["call_type"] == ac.CALL_TYPE_PROFILE_SAFESEARCH
    # Timeout for 28 days, hold stored, case = MUTE, DM sent, alert posted.
    assert member.timeouts and member.timeouts[0][0] == timedelta(days=28)
    hold = env.bot.db.holds[(1, 5, "avatar")]
    assert hold["categorie"] == "profil_avatar_nsfw" and hold["asset_key"] == "a_bad"
    assert hold["case_id"] is not None and hold["sanction_id"] is not None
    (case,) = env.bot.cases.recorded
    assert case["action"].value == "mute"
    assert env.bot.notifications.sent, "the member is told through bot.notifications"
    assert guild.channel.sent, "the alert card was posted"
    # Evidence: profile origin, no message fields.
    ev = [e for e in env.bot.db.events if e[2].get("payload", {}).get("source") == "automod"]
    payload = ev[0][2]["payload"]
    assert payload["origine"] == "profil_avatar" and payload["extrait"] == asset.url
    assert "message_id" not in payload and "jump_url" not in payload


async def test_cached_avatar_costs_no_download_and_no_call(monkeypatch):
    env = Env(monkeypatch, vision=FakeVision(adult="VERY_LIKELY"))
    guild = FakeGuild()
    mod = await _module(env, guild)
    asset = FakeAsset("a_seen", _png())
    # An avatar already judged somewhere: only the Redis record exists.
    from automod.image_hash import prepare_image
    img = prepare_image(asset._data)
    env.bot.redis.store["automod:avatar:a_seen"] = json.dumps({
        "p": img.phash_hex, "d": img.dhash_hex,
        "ss": {"adult": "UNLIKELY", "racy": "UNLIKELY", "violence": "UNLIKELY"}})
    member = FakeMember(guild, avatar=asset)
    await mod.screen_member(member, scope={"avatar"})
    assert asset.reads == 0 and env.vision.calls == []
    assert member.timeouts == []        # clean verdict


async def test_same_avatar_in_two_guilds_is_one_safesearch_call(monkeypatch):
    env = Env(monkeypatch, vision=FakeVision(adult="VERY_LIKELY"))
    g1, g2 = FakeGuild(1), FakeGuild(2)
    mod1 = await _module(env, g1)
    mod2 = await _module(env, g2)
    asset = FakeAsset("a_shared", _png())
    m1 = FakeMember(g1, avatar=asset)
    m2 = FakeMember(g2, avatar=asset)
    await mod1.screen_member(m1, scope={"avatar"})
    await mod2.screen_member(m2, scope={"avatar"})
    assert len(env.vision.calls) == 1 and asset.reads == 1
    # ... and the verdict was applied in both guilds.
    assert m1.timeouts and m2.timeouts


async def test_budget_refused_sanctions_nothing(monkeypatch):
    env = Env(monkeypatch, vision=FakeVision(adult="VERY_LIKELY"), usage=1000)
    guild = FakeGuild()
    mod = await _module(env, guild)
    member, _ = _nsfw_member(guild)
    await mod.screen_member(member, scope={"avatar"})
    assert env.vision.calls == [] and member.timeouts == []
    assert env.bot.cases.recorded == [] and env.bot.db.holds == {}


async def test_guild_avatar_overrides_the_global_one(monkeypatch):
    env = Env(monkeypatch, vision=FakeVision(adult="UNLIKELY"))
    guild = FakeGuild()
    mod = await _module(env, guild)
    glob, local = FakeAsset("g_old", _png()), FakeAsset("g_guild", _png((10, 90, 10)))
    member = FakeMember(guild, avatar=glob, guild_avatar=local)
    await mod.screen_member(member, scope={"avatar"})
    assert local.reads == 1 and glob.reads == 0


async def test_profile_fixed_lifts_the_hold(monkeypatch):
    env = Env(monkeypatch, vision=FakeVision(adult="UNLIKELY"))
    guild = FakeGuild()
    mod = await _module(env, guild)
    member = FakeMember(guild, avatar=FakeAsset("a_clean", _png((0, 90, 10))))
    until = NOW + timedelta(days=20)
    member.timed_out_until = until
    case_id, sanction_id = uuid.uuid4(), uuid.uuid4()
    await env.bot.db.upsert_profile_hold(
        1, 5, "avatar", categorie="profil_avatar_nsfw", asset_key="a_bad",
        case_id=case_id, sanction_id=sanction_id, timeout_until=until)
    await mod.screen_member(member, scope={"avatar"})
    assert member.timeouts == [(None, "[Automod] Profile fixed")] or \
        member.timeouts[0][0] is None
    assert (1, 5, "avatar") not in env.bot.db.holds
    assert env.bot.db.revoked == [sanction_id]
    assert any(e[2].get("payload", {}).get("kind") == "profil_corrige"
               for e in env.bot.db.events)
    assert guild.channel.sent, "a short 'profile fixed' line is posted"


async def test_removed_avatar_also_lifts_the_hold(monkeypatch):
    env = Env(monkeypatch)
    guild = FakeGuild()
    mod = await _module(env, guild)
    member = FakeMember(guild)       # back to the default avatar
    member.timed_out_until = NOW + timedelta(days=5)
    await env.bot.db.upsert_profile_hold(
        1, 5, "avatar", asset_key="a_bad", sanction_id=uuid.uuid4(),
        timeout_until=member.timed_out_until)
    await mod.screen_member(member, scope={"avatar"})
    assert (1, 5, "avatar") not in env.bot.db.holds and member.timed_out_until is None


async def test_rejoin_with_a_hold_is_timed_out_again(monkeypatch):
    env = Env(monkeypatch, vision=FakeVision(adult="VERY_LIKELY"))
    guild = FakeGuild()
    mod = await _module(env, guild)
    member, asset = _nsfw_member(guild)
    await mod.screen_member(member, scope={"avatar"})
    assert len(member.timeouts) == 1
    # They leave and come back: the Discord timeout is gone, the hold row is not.
    member.timed_out_until = None
    cases_before = len(env.bot.cases.recorded)
    await mod.screen_member(member, reason="join", scope={"avatar"})
    assert len(member.timeouts) == 2
    assert len(env.bot.cases.recorded) == cases_before, "no second case"
    assert len(env.vision.calls) == 1


async def test_same_flagged_avatar_is_not_sanctioned_twice(monkeypatch):
    env = Env(monkeypatch, vision=FakeVision(adult="VERY_LIKELY"))
    guild = FakeGuild()
    mod = await _module(env, guild, sanctions={
        "profil_avatar_nsfw": {"action_profil": "alert"}})
    member, _ = _nsfw_member(guild)
    await mod.screen_member(member, scope={"avatar"})
    await mod.screen_member(member, scope={"avatar"})
    assert len(guild.channel.sent) == 1


async def test_alert_action_applies_nothing(monkeypatch):
    env = Env(monkeypatch, vision=FakeVision(adult="VERY_LIKELY"))
    guild = FakeGuild()
    mod = await _module(env, guild, sanctions={
        "profil_avatar_nsfw": {"action_profil": "alert"}})
    member, _ = _nsfw_member(guild)
    await mod.screen_member(member, scope={"avatar"})
    assert guild.channel.sent and member.timeouts == []
    assert env.bot.cases.recorded == [] and env.bot.notifications.sent == []
    assert env.bot.db.holds == {}


async def test_bareme_action_uses_the_cran_ladder(monkeypatch):
    env = Env(monkeypatch, vision=FakeVision(adult="VERY_LIKELY"))
    guild = FakeGuild()
    mod = await _module(env, guild, sanctions={
        "profil_avatar_nsfw": {"action_profil": "bareme"}})
    member, _ = _nsfw_member(guild)
    await mod.screen_member(member, scope={"avatar"})
    (case,) = env.bot.cases.recorded
    assert case["action"].value in ("warn", "mute", "ban")
    assert env.bot.db.holds == {}, "a ladder mute is not a profile hold"


async def test_hold_degrades_to_a_warn_under_a_warn_ceiling(monkeypatch):
    env = Env(monkeypatch, vision=FakeVision(adult="VERY_LIKELY"))
    guild = FakeGuild()
    mod = await _module(env, guild, extra={"max_action": "warn"})
    member, _ = _nsfw_member(guild)
    await mod.screen_member(member, scope={"avatar"})
    (case,) = env.bot.cases.recorded
    assert case["action"].value == "warn" and member.timeouts == []


async def test_dry_run_posts_a_simulation_and_applies_nothing(monkeypatch):
    env = Env(monkeypatch, vision=FakeVision(adult="VERY_LIKELY"))
    guild = FakeGuild()
    mod = await _module(env, guild, extra={"dry_run": True})
    member, _ = _nsfw_member(guild)
    await mod.screen_member(member, scope={"avatar"})
    assert member.timeouts == [] and env.bot.cases.recorded == []
    assert env.bot.db.holds == {} and env.bot.notifications.sent == []
    assert len(guild.channel.sent) == 1
    # The simulation badge is on the card.
    assert "SIMULATION" in _card_text(guild.channel.sent[0]["view"])


def _card_text(view) -> str:
    out = []

    def walk(item):
        content = getattr(item, "content", None)
        if content:
            out.append(content)
        for child in getattr(item, "children", []) or []:
            walk(child)
    for child in view.children:
        walk(child)
    return "\n".join(out)


async def test_a_failed_timeout_is_not_announced_as_a_hold(monkeypatch):
    env = Env(monkeypatch, vision=FakeVision(adult="VERY_LIKELY"))
    guild = FakeGuild(moderate_perm=False)
    mod = await _module(env, guild)
    member, _ = _nsfw_member(guild)
    await mod.screen_member(member, scope={"avatar"})
    assert member.timeouts == [] and env.bot.db.holds == {}
    assert env.bot.notifications.sent == []
    assert "Moddy could not apply the timeout" in _card_text(guild.channel.sent[0]["view"])


# --------------------------------------------------------------------------- #
# Skips
# --------------------------------------------------------------------------- #

async def test_bots_moderators_owner_and_exempt_roles_are_skipped(monkeypatch):
    env = Env(monkeypatch, vision=FakeVision(adult="VERY_LIKELY"))
    guild = FakeGuild()
    mod = await _module(env, guild, features={"exempt_roles": [42]})
    skipped = [
        _nsfw_member(guild, key="a_1", bot=True)[0],
        _nsfw_member(guild, key="a_2", mod=True)[0],
        _nsfw_member(guild, key="a_3", uid=9999)[0],
        _nsfw_member(guild, key="a_4", roles=[types.SimpleNamespace(id=42)])[0],
    ]
    for member in skipped:
        await mod.screen_member(member, scope={"avatar"})
        assert member.timeouts == []
    assert env.vision.calls == []


async def test_globally_limited_guild_is_left_alone(monkeypatch):
    env = Env(monkeypatch, vision=FakeVision(adult="VERY_LIKELY"))
    guild = FakeGuild()
    mod = await _module(env, guild)

    async def limited(bot, **kw):
        return True
    monkeypatch.setattr("utils.global_sanctions.is_limited", limited)
    member, _ = _nsfw_member(guild)
    await mod.screen_member(member, scope={"avatar"})
    assert env.vision.calls == [] and member.timeouts == []


async def test_disabled_sanction_type_applies_nothing(monkeypatch):
    env = Env(monkeypatch, vision=FakeVision(adult="VERY_LIKELY"))
    guild = FakeGuild()
    mod = await _module(env, guild, sanctions={"profil_avatar_nsfw": {"enabled": False}})
    member, _ = _nsfw_member(guild)
    await mod.screen_member(member, scope={"avatar"})
    assert member.timeouts == [] and guild.channel.sent == []


# --------------------------------------------------------------------------- #
# Names
# --------------------------------------------------------------------------- #

async def test_flagged_name_is_reset_and_warned(monkeypatch):
    env = Env(monkeypatch)
    env.bot._automod_engine = FakeEngine(_name_decision(), only=_is_bad)
    guild = FakeGuild()
    mod = await _module(env, guild)
    member = FakeMember(guild, global_name="BadWord", name="alice")
    await mod.screen_member(member, scope={"pseudo"})
    assert member.edits == [{"nick": "Moderated name",
                             "reason": member.edits[0]["reason"]}]
    (case,) = env.bot.cases.recorded
    assert case["action"].value == "warn"
    # The funnel was called with the profile origin and no context.
    target, kwargs = env.bot._automod_engine.calls[0]
    assert target.origine == ORIGINE_PROFIL_PSEUDO
    assert await kwargs["fetch_context"](5) == []
    ev = [e for e in env.bot.db.events if e[2].get("payload", {}).get("source") == "automod"]
    payload = ev[0][2]["payload"]
    assert payload["categorie"] == "profil_pseudo" and payload["categorie_nano"] == "insulte"
    assert payload["origine"] == "profil_pseudo" and payload["extrait"] == "BadWord"
    assert env.bot.notifications.sent


async def test_rename_failure_falls_back_to_a_warn(monkeypatch):
    env = Env(monkeypatch)
    env.bot._automod_engine = FakeEngine(_name_decision(), only=_is_bad)
    guild = FakeGuild(nick_perm=False)
    mod = await _module(env, guild)
    member = FakeMember(guild, global_name="BadWord")
    await mod.screen_member(member, scope={"pseudo"})
    assert member.edits == []
    (case,) = env.bot.cases.recorded
    assert case["action"].value == "warn"
    assert "could not reset the nickname" in _card_text(guild.channel.sent[0]["view"])


async def test_a_name_that_is_not_displayed_is_a_warn_only(monkeypatch):
    env = Env(monkeypatch)

    class OnlyUsername(FakeEngine):
        async def analyze(self, target, **kw):
            self.calls.append((target, kw))
            if target.content != "badname":
                return None
            return await super().analyze(target, **kw)
    env.bot._automod_engine = OnlyUsername(_name_decision())
    guild = FakeGuild()
    mod = await _module(env, guild)
    member = FakeMember(guild, global_name="Alice", name="badname")
    await mod.screen_member(member, scope={"pseudo"})
    assert member.edits == []
    assert len(env.bot.cases.recorded) == 1


async def test_the_same_bad_name_is_not_warned_twice(monkeypatch):
    env = Env(monkeypatch)
    env.bot._automod_engine = FakeEngine(_name_decision(), only=_is_bad)
    guild = FakeGuild()
    mod = await _module(env, guild)
    member = FakeMember(guild, global_name="BadWord")
    await mod.screen_member(member, scope={"pseudo"})
    # The nickname reset hides it; the next event still sees the bad global name.
    await mod.screen_member(member, scope={"pseudo"})
    assert len(env.bot.cases.recorded) == 1


async def test_name_verdicts_are_cached_per_normalized_name(monkeypatch):
    env = Env(monkeypatch)
    engine = FakeEngine(_name_decision(sanctionnable=False))
    env.bot._automod_engine = engine
    svc = env.bot.automod_profiles
    kw = dict(user_id=5, guild_id=1, guild_name="G", rules="", severity=3,
              response_language="English")
    await svc.judge_name("Some  Name", **kw)
    second = await svc.judge_name("some name", **kw)
    assert len(engine.calls) == 1 and second.fresh is False
    assert name_cache_key("Some  Name") == name_cache_key("some name")
    assert name_cache_key("some name") in env.bot.redis.store
    # A stricter server does not trust another server's "clean".
    await svc.judge_name("some name", **{**kw, "severity": 5})
    assert len(engine.calls) == 2


async def test_clean_names_cost_nothing_and_sanction_nothing(monkeypatch):
    env = Env(monkeypatch)
    guild = FakeGuild()
    mod = await _module(env, guild)
    member = FakeMember(guild)
    await mod.screen_member(member, scope={"pseudo"})
    assert env.bot.cases.recorded == [] and member.edits == []


async def test_pseudo_prompt_block_is_only_for_the_pseudo_origin():
    from automod.nano import origin_prompt_block, PROFIL_PSEUDO_PROMPT_BLOCK
    from automod.schemas import TargetMessage
    pseudo = TargetMessage(id="1", author_id="2", content="x", origine=ORIGINE_PROFIL_PSEUDO)
    plain = TargetMessage(id="1", author_id="2", content="x")
    assert origin_prompt_block(pseudo) == PROFIL_PSEUDO_PROMPT_BLOCK
    assert "impersonation" in PROFIL_PSEUDO_PROMPT_BLOCK.lower()
    assert origin_prompt_block(plain) == ""


# --------------------------------------------------------------------------- #
# Banners
# --------------------------------------------------------------------------- #

async def test_banner_is_fetched_once_per_ten_minutes(monkeypatch):
    env = Env(monkeypatch, vision=FakeVision(adult="VERY_LIKELY"))
    guild = FakeGuild()
    mod = await _module(env, guild)
    env.banner_assets[5] = FakeAsset("b_bad", _png((20, 20, 90)))
    member = FakeMember(guild)
    await mod.screen_member(member, scope={"banner"})
    await mod.screen_member(member, scope={"banner"})
    assert env.fetched == [5], "one REST fetch per user per window"
    assert env.bot.db.holds[(1, 5, "banner")]["asset_key"] == "b_bad"


async def test_banner_screening_can_be_switched_off(monkeypatch):
    env = Env(monkeypatch)
    guild = FakeGuild()
    mod = await _module(env, guild, features={"banner": False})
    await mod.screen_member(FakeMember(guild), scope={"banner"})
    assert env.fetched == []


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #

async def test_member_events_schedule_the_right_checks(monkeypatch):
    env = Env(monkeypatch)
    guild = FakeGuild()
    mod = await _module(env, guild)
    seen = []

    def spawn(member, reason, scope):
        seen.append((reason, set(scope)))
    mod._spawn_screen = spawn
    m = FakeMember(guild)
    await mod.on_member_join(m)
    assert seen[-1] == ("join", {"avatar", "banner", "pseudo"})
    before = types.SimpleNamespace(nick=None, guild_avatar=None)
    await mod.on_member_update(before, types.SimpleNamespace(nick=None, guild_avatar=None))
    assert len(seen) == 1, "nothing relevant changed"
    await mod.on_member_update(before, types.SimpleNamespace(nick="x", guild_avatar=None))
    assert seen[-1] == ("update", {"pseudo", "banner"})
    ub = types.SimpleNamespace(name="a", global_name="A", avatar=None)
    ua = types.SimpleNamespace(name="a", global_name="A", avatar="new")
    await mod.on_user_update(ub, ua, m)
    assert seen[-1] == ("update", {"avatar", "banner"})


async def test_screening_runs_as_a_bounded_background_task(monkeypatch):
    env = Env(monkeypatch)
    svc = env.bot.automod_profiles
    done = []

    async def job():
        done.append(1)
    assert svc.spawn(job()) is True
    import asyncio
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert done == [1]
    monkeypatch.setattr(ac, "PROFILE_QUEUE_MAX", 0)
    assert svc.spawn(job()) is False


async def test_a_failing_background_screening_is_logged_not_raised(monkeypatch, caplog):
    env = Env(monkeypatch)
    svc = env.bot.automod_profiles

    async def boom():
        raise RuntimeError("kaboom")
    import asyncio
    with caplog.at_level("ERROR"):
        svc.spawn(boom())
        for _ in range(3):
            await asyncio.sleep(0)
    assert "kaboom" in caplog.text


# --------------------------------------------------------------------------- #
# Gateway budget
# --------------------------------------------------------------------------- #

def test_profile_safesearch_has_its_own_fail_closed_monthly_rule(monkeypatch):
    from gateway.config import _default_model_rate_limits
    monkeypatch.delenv("GOOGLE_VISION_SAFESEARCH_PROFILE_MONTHLY", raising=False)
    rules = _default_model_rate_limits()
    profile = rules[("google_vision", "safe_search_profile")]
    messages = rules[("google_vision", "safe_search")]
    assert [r.name for r in profile] == ["rpmo"] and profile[0].fail_closed
    assert profile[0].limit == 1000 and profile[0] is not messages[0]
    monkeypatch.setenv("GOOGLE_VISION_SAFESEARCH_PROFILE_MONTHLY", "250")
    assert _default_model_rate_limits()[("google_vision", "safe_search_profile")][0].limit == 250


async def test_vision_client_accounts_on_the_profile_key_but_calls_safesearch():
    from gateway.clients.vision import VisionClient

    class Executor:
        def __init__(self):
            self.specs = []

        async def execute(self, spec):
            self.specs.append(spec)
            return {"adult": "VERY_LIKELY"}
    ex = Executor()
    client = VisionClient(ex)
    await client.safe_search(b"x", budget_model="safe_search_profile")
    await client.safe_search(b"x")
    profile, normal = ex.specs
    assert profile.operation == "safe_search" and profile.model == "safe_search_profile"
    assert normal.operation == normal.model == "safe_search"


async def test_profile_budget_is_read_from_its_own_rule(monkeypatch):
    env = Env(monkeypatch)
    seen = []

    async def usage(provider, model):
        seen.append(model)
        return [{"rule": "rpmo", "used": 0, "limit": 1000}]
    env.bot.gateway.rate_limit_usage = usage
    from automod.image_hash import prepare_image
    img = prepare_image(_png())
    await env.bot.automod_images.safesearch(img, guild_id=1, tier="prioritaire",
                                            profile=True)
    assert seen == ["safe_search_profile"]


# --------------------------------------------------------------------------- #
# Labeling queue
# --------------------------------------------------------------------------- #

def _decision(origine, categorie):
    return types.SimpleNamespace(origine=origine, categorie=categorie)


def test_label_kind_maps_profile_origins():
    assert label_kind(_decision(ORIGINE_PROFIL_AVATAR, "profil_avatar_nsfw")) \
        == KIND_PROFIL_AVATAR_NSFW
    assert label_kind(_decision(ORIGINE_PROFIL_BANNIERE, "profil_avatar_choquant")) \
        == KIND_PROFIL_AVATAR_CHOQUANT
    assert label_kind(_decision(ORIGINE_PROFIL_PSEUDO, "profil_pseudo")) \
        == KIND_PROFIL_PSEUDO
    from db.repositories.automod_learning import LABEL_KINDS
    assert {KIND_PROFIL_AVATAR_NSFW, KIND_PROFIL_AVATAR_CHOQUANT,
            KIND_PROFIL_PSEUDO} <= set(LABEL_KINDS)


def test_build_details_without_a_message_keeps_the_guild_and_the_hold():
    d = _name_decision()
    d.message_id = "profil:5:pseudo:abc"
    details = build_details(d, motif="sanction", applied=["warn"], guild_name="G",
                            case_id=uuid.uuid4(), guild_id=7, hold_kind="avatar")
    (entry,) = details["sanctions"]
    assert entry["guild_id"] == 7 and entry["message_id"] is None
    assert entry["hold_kind"] == "avatar" and entry["author_id"] == 1
    assert details["jump_url"] == ""


async def test_enqueue_a_profile_item_has_no_channel_or_message(monkeypatch):
    created = {}

    class DB:
        async def find_recent_label_item(self, key):
            return None

        async def create_label_item(self, **kw):
            created.update(kw)
            return "item-1"

        async def get_label_item(self, item_id):
            return {"id": "00000000-0000-0000-0000-000000000001", "kind": kw_kind(),
                    "motif": "sanction", "guild_id": 1, "author_id": 5, "channel_id": None,
                    "contenu": "BadWord", "details": created["details"], "occurrences": 1,
                    "verdict": None}

        async def set_label_item_card(self, *a):
            created["card"] = a

    def kw_kind():
        return created["kind"]
    channel = FakeChannel()
    bot = types.SimpleNamespace(db=DB(), redis=None, stats=None,
                                get_channel=lambda cid: channel)
    bot.automod_images = AutomodImageService(bot)
    service = AutomodLabelService(bot)
    monkeypatch.setattr(type(service), "channel_id", property(lambda self: 77))
    guild = types.SimpleNamespace(id=1, name="G")
    member = types.SimpleNamespace(id=5, guild=guild)
    d = _name_decision()
    d.origine, d.contenu_juge, d.message_id = ORIGINE_PROFIL_PSEUDO, "BadWord", "profil:5:p:1"
    await service.enqueue(decision=d, motif="sanction", member=member,
                          judged_text="BadWord", applied=["warn"], case_id=uuid.uuid4())
    assert created["kind"] == "profil_pseudo"
    assert created["channel_id"] is None and created["message_id"] is None
    assert created["guild_id"] == 1
    assert channel.sent, "the team card was posted"


async def test_team_revocation_frees_a_held_member(monkeypatch):
    deleted = []
    reversed_ = []
    case_id, sanction_id = uuid.uuid4(), uuid.uuid4()

    class DB:
        async def get_case_by_id(self, cid):
            return {"sanctions": [{"id": sanction_id, "status": "active", "action": "mute"}],
                    "case": {"reference": "R1"}}

        async def revoke_sanction(self, *a):
            return True

        async def add_event(self, *a, **k):
            pass

        async def mark_label_item_revoked(self, item_id):
            pass

        async def delete_profile_hold(self, guild_id, user_id, kind):
            deleted.append((guild_id, user_id, kind))
    bot = types.SimpleNamespace(db=DB(), get_guild=lambda gid: None)
    service = AutomodLabelService(bot)

    async def reverse(guild, subject, action, *, reason):
        reversed_.append((subject, action))
    monkeypatch.setattr("utils.sanction_reversal.reverse_discord_sanction", reverse)

    async def no_notice(*a, **k):
        pass
    monkeypatch.setattr(service, "_notify_revocation", no_notice)
    row = {"id": "i1", "revoked": False, "details": {"sanctions": [{
        "guild_id": 1, "case_id": str(case_id), "author_id": 5, "message_id": None,
        "hold_kind": "avatar"}]}}
    assert await service.revoke_bot_sanctions(row, 42) == 1
    assert deleted == [(1, 5, "avatar")] and reversed_ == [(5, "mute")]


# --------------------------------------------------------------------------- #
# Appeals
# --------------------------------------------------------------------------- #

def _profile_case(origine="profil_avatar"):
    return {"events": [
        {"type": "evidence", "payload": {
            "source": "automod", "origine": origine, "extrait": "https://cdn/x.png",
            "author_name": "Alice", "ts": 1, "categorie": "profil_avatar_nsfw",
            "gravite": "haute"}},
        {"type": "evidence", "payload": {"kind": "automod_log", "channel_id": 7,
                                         "message_id": 8}},
    ]}


def test_appeal_helpers_tolerate_a_case_without_message_fields():
    case = _profile_case()
    assert AppealService._proof(case) == {
        "text": None, "author": "Alice", "message_id": None, "ts": 1}
    assert AppealService._automod_extract(case) is None
    assert AppealService._context_text(case) is None
    assert AppealService._evidence(case).startswith("https://cdn")
    svc = AppealService.__new__(AppealService)
    assert svc._log_ref(case) == (7, 8)
    # A pseudo case shows the judged name as proof.
    named = _profile_case("profil_pseudo")
    named["events"][0]["payload"]["extrait"] = "BadWord"
    assert AppealService._proof(named)["text"] == "BadWord"


# --------------------------------------------------------------------------- #
# i18n
# --------------------------------------------------------------------------- #

LOCALES = ("fr", "en-US", "es-ES", "pt-BR", "de")


def _flatten(d, prefix=""):
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(_flatten(v, f"{prefix}{k}."))
        else:
            out[f"{prefix}{k}"] = v
    return out


def test_profile_strings_exist_in_every_locale_with_the_same_placeholders():
    import re
    data = {loc: json.loads((ROOT / "locales" / f"{loc}.json").read_text(encoding="utf-8"))
            for loc in LOCALES}
    ref = _flatten(data["en-US"]["modules"]["automod_ai"]["profile"])
    assert len(ref) > 25
    for loc in LOCALES:
        got = _flatten(data[loc]["modules"]["automod_ai"]["profile"])
        assert set(got) == set(ref), loc
        for key, text in ref.items():
            assert set(re.findall(r"\{(\w+)\}", got[key])) == \
                set(re.findall(r"\{(\w+)\}", text)), (loc, key)
        for key in ("profil_avatar_nsfw", "profil_avatar_choquant", "profil_pseudo"):
            assert data[loc]["automod_label"]["kind"][key]
        assert data[loc]["automod_label"]["name"]


def test_every_profile_i18n_key_the_module_uses_exists():
    import re
    src = (ROOT / "modules" / "automod_ai.py").read_text(encoding="utf-8")
    en = json.loads((ROOT / "locales" / "en-US.json").read_text(encoding="utf-8"))
    keys = _flatten(en["modules"]["automod_ai"]["profile"])
    literal = set(re.findall(r'modules\.automod_ai\.profile\.([a-z_.]+)"', src))
    for key in literal:
        assert key in keys, key
    for kind in ("avatar", "banner"):
        for axis in ("nsfw", "gore"):
            assert f"reason_{kind}_{axis}" in keys
    for note in ("hold_failed", "rename_failed", "not_displayed"):
        assert f"note_{note}" in keys
    for action in ("hold", "rename_warn", "alert", "bareme", "warn"):
        assert f"action.{action}" in keys
    for kind in ("avatar", "banner", "pseudo"):
        assert f"kind.{kind}" in keys


def test_normalize_name_is_stable():
    assert normalize_name("  ＢＡＤ   Name ") == "bad name"


def test_config_panel_carries_the_profile_feature_through():
    from modules.configs.automod_ai_common import normalize_cfg as _deep_default
    cfg = _deep_default({"features": {
        "content": {"exempt_roles": [3]},
        "profile": {"enabled": True, "banner": False}}})
    assert cfg["features"]["profile"] == {
        "enabled": True, "exempt_roles": [3], "exempt_channels": [],
        "avatar": True, "banner": False, "pseudo": True}
    assert _deep_default(None)["features"]["profile"]["enabled"] is False


async def test_validate_config_rejects_a_non_boolean_profile_option(monkeypatch):
    env = Env(monkeypatch)
    guild = FakeGuild()
    mod = await _module(env, guild)
    env.bot.get_guild = lambda gid: types.SimpleNamespace(get_channel=lambda c: None)
    ok, err = await mod.validate_config({"features": {"profile": {"avatar": "yes"}}})
    assert not ok and "avatar" in err
