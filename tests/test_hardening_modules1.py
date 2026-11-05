"""Hostile-permission / chaos tests for the server modules (agent C1).

Fake guilds reproduce what Top.gg reviewers do: Moddy invited with
permissions=0 (only what @everyone has), then given ONE permission at a time,
in channels whose overwrites deny it (the reference bug: tickets checked
manage_roles in the category while Discord checks it at guild level).

    pytest tests/test_hardening_modules1.py -q
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from modules import interserver as inter_mod
from modules.adaptive_slowmode import AdaptiveSlowmodeModule, ChannelState, FAILURE_BACKOFF
from modules.ai_detector import AiDetectorModule
from modules.auto_restore_roles import AutoRestoreRolesModule
from modules.auto_role import AutoRoleModule
from modules.interserver import InterServerModule
from modules.music_links import MusicLinksModule
from modules.starboard import StarboardModule
from modules.translate_button import TranslateButtonModule
from modules.voice_transcription import VoiceTranscriptionModule
from modules.welcome_channel import WelcomeChannelModule

BOT_ID = 4242

# What @everyone has by default: the "invited with permissions=0" bot.
EVERYONE = dict(view_channel=True, send_messages=True, embed_links=True,
                attach_files=True, read_message_history=True, add_reactions=True,
                use_external_emojis=True, send_messages_in_threads=True)


def perms(**kw):
    return discord.Permissions(**kw)


def channel(cls=discord.TextChannel, cid=10, held=None, guild=None):
    """A channel whose effective permissions for the bot are exactly ``held``."""
    ch = MagicMock(spec=cls)
    ch.id = cid
    ch.flags = None
    ch.mention = f"<#{cid}>"
    ch.guild = guild
    ch.permissions_for = lambda member, _p=perms(**(held or {})): _p
    ch.send = AsyncMock()
    ch.fetch_message = AsyncMock()
    ch.edit = AsyncMock()
    ch.slowmode_delay = 0
    ch.topic = None
    return ch


def guild_with(me_perms=None, channels=(), roles=(), top_role=None, gid=1):
    me = SimpleNamespace(
        id=BOT_ID, guild_permissions=perms(**(me_perms or {})),
        top_role=top_role if top_role is not None else FakeRole(500, 50),
    )
    g = SimpleNamespace(
        id=gid, name="Fake", me=me, features=[], member_count=3,
        get_channel=lambda cid: next((c for c in channels if c.id == cid), None),
        get_channel_or_thread=lambda cid: next((c for c in channels if c.id == cid), None),
        get_role=lambda rid: next((r for r in roles if r.id == rid), None),
    )
    for c in channels:
        c.guild = g
    return g


class FakeRole:
    def __init__(self, rid, position, managed=False):
        self.id, self.position, self.managed = rid, position, managed
        self.name = f"role{rid}"
        self.mention = f"<@&{rid}>"

    def __lt__(self, other):
        return self.position < other.position

    def __ge__(self, other):
        return self.position >= other.position

    def __eq__(self, other):
        return isinstance(other, FakeRole) and self.id == other.id

    def __hash__(self):
        return hash(self.id)


def bot_with(guild, **extra):
    guilds = {guild.id: guild}
    bot = SimpleNamespace(
        user=SimpleNamespace(id=BOT_ID),
        get_guild=lambda gid: guilds.get(gid),
        guilds=list(guilds.values()),
        db=MagicMock(),
        stats=None,
        **extra,
    )
    return bot


def forbidden():
    resp = SimpleNamespace(status=403, reason="Forbidden")
    return discord.Forbidden(resp, "Missing Permissions")


def not_found(code=0):
    resp = SimpleNamespace(status=404, reason="Not Found")
    return discord.NotFound(resp, {"code": code, "message": "Unknown"})


# --------------------------------------------------------------------------- #
# Declarations: the right permission, in the right place
# --------------------------------------------------------------------------- #
class TestDeclaredPermissions:
    def test_interserver_needs_nothing_guild_wide(self):
        """manage_webhooks / manage_channels / manage_messages are checked in
        the channel by Discord: a channel-only grant must be enough."""
        assert InterServerModule.REQUIRED_BOT_PERMISSIONS == []
        assert not InterServerModule.required_permissions()
        req = InterServerModule.channel_requirements({"channel_id": 55})
        assert req == {55: ["view_channel", "manage_webhooks"]}

    def test_adaptive_slowmode_manage_channels_is_per_channel(self):
        assert "manage_channels" not in AdaptiveSlowmodeModule.required_permissions()
        req = AdaptiveSlowmodeModule.channel_requirements({"channels": {"7": {}, "8": {}}})
        assert req == {7: ["view_channel", "manage_channels"],
                       8: ["view_channel", "manage_channels"]}

    def test_starboard_needs_embed_links_and_history_in_its_channel(self):
        req = StarboardModule.channel_requirements({"channel_id": 9})
        assert set(req[9]) >= {"view_channel", "send_messages", "embed_links",
                               "read_message_history"}

    def test_auto_restore_roles_embed_links_only_for_its_log_channel(self):
        assert "embed_links" not in AutoRestoreRolesModule.required_permissions()
        assert "manage_roles" in AutoRestoreRolesModule.required_permissions()
        req = AutoRestoreRolesModule.channel_requirements({"log_channel_id": 3})
        assert "embed_links" in req[3]
        assert AutoRestoreRolesModule.channel_requirements({}) == {}

    def test_voice_transcription_replies_need_history(self):
        req = VoiceTranscriptionModule.channel_requirements({"channel_ids": [4]})
        assert "read_message_history" in req[4]

    def test_auto_role_is_guild_level_only(self):
        assert AutoRoleModule.required_permissions() == ["manage_roles"]


# --------------------------------------------------------------------------- #
# Inter-server
# --------------------------------------------------------------------------- #
def interserver_setup(destinations, monkeypatch):
    """A source module + destination channels, each with its own webhook mock."""
    inter_mod._WEBHOOKS.clear()
    inter_mod._BLOCKED_UNTIL.clear()
    src_guild = guild_with(me_perms=EVERYONE, gid=1)
    guilds = [src_guild]
    webhooks = {}
    for index, (cid, held, send_exc) in enumerate(destinations, start=2):
        g = guild_with(me_perms=EVERYONE, gid=index)
        ch = channel(cid=cid, held=held, guild=g)
        wh = SimpleNamespace(
            user=SimpleNamespace(id=BOT_ID), name="Moddy Inter-Server",
            send=AsyncMock(side_effect=send_exc,
                           return_value=SimpleNamespace(id=cid * 100, add_reaction=AsyncMock())),
            delete_message=AsyncMock())
        ch.webhooks = AsyncMock(return_value=[wh])
        ch.create_webhook = AsyncMock(return_value=wh)
        g.get_channel = lambda _cid, _ch=ch: _ch
        webhooks[cid] = (ch, wh)
        guilds.append(g)
    target = SimpleNamespace(show_server_name=True, show_avatar=True, allowed_mentions=False)
    manager = SimpleNamespace(get_module_instance=AsyncMock(return_value=target))
    bot = SimpleNamespace(
        user=SimpleNamespace(id=BOT_ID, display_avatar=SimpleNamespace(url="u")),
        guilds=guilds, module_manager=manager,
        db=SimpleNamespace(add_relayed_message=AsyncMock(),
                           get_interserver_message_by_original=AsyncMock(return_value=None)),
    )
    monkeypatch.setattr(inter_mod, "get_or_fetch_member", AsyncMock(return_value=None))
    module = InterServerModule(bot, 1)
    module.channel_id = 99
    return module, webhooks, src_guild


def fake_message(guild, attachments=()):
    return SimpleNamespace(
        guild=guild, channel=SimpleNamespace(id=99), reference=None, embeds=[],
        attachments=list(attachments), content="hello",
        author=SimpleNamespace(id=5, display_name="Al", name="al",
                               display_avatar=SimpleNamespace(url="http://a")),
    )


GOOD = dict(EVERYONE, manage_webhooks=True)


class TestInterserver:
    async def test_message_with_attachment_is_relayed(self, monkeypatch):
        """Used to raise UnboundLocalError: no message with a file was ever relayed."""
        module, hooks, src = interserver_setup([(20, GOOD, None)], monkeypatch)
        att = SimpleNamespace(filename="a.png", url="http://x/a.png")
        count = await module._relay_message(
            fake_message(src, [att]), [hooks[20][0]], "ABCD1234", "hello", False)
        assert count == 1
        content = hooks[20][1].send.await_args.kwargs["content"]
        assert "a.png" in content and "ABCD1234" in content

    async def test_broken_destination_does_not_stop_the_others(self, monkeypatch):
        module, hooks, src = interserver_setup(
            [(20, GOOD, forbidden()), (21, GOOD, None), (22, GOOD, None)], monkeypatch)
        count = await module._relay_message(
            fake_message(src), [hooks[c][0] for c in (20, 21, 22)], "ID", "hello", False)
        assert count == 2
        assert hooks[21][1].send.await_count == 1 and hooks[22][1].send.await_count == 1

    async def test_failed_destination_backs_off_instead_of_retrying(self, monkeypatch):
        module, hooks, src = interserver_setup([(20, GOOD, forbidden())], monkeypatch)
        ch = hooks[20][0]
        await module._relay_message(fake_message(src), [ch], "A", "hi", False)
        await module._relay_message(fake_message(src), [ch], "B", "hi", False)
        assert hooks[20][1].send.await_count == 1  # second message: skipped, no API call

    async def test_webhook_is_cached_between_messages(self, monkeypatch):
        module, hooks, src = interserver_setup([(20, GOOD, None)], monkeypatch)
        ch = hooks[20][0]
        for i in range(3):
            await module._relay_message(fake_message(src), [ch], f"I{i}", "hi", False)
        assert ch.webhooks.await_count == 1

    async def test_destination_without_manage_webhooks_is_skipped_not_called(self, monkeypatch):
        module, hooks, src = interserver_setup([(20, EVERYONE, None)], monkeypatch)
        count = await module._relay_message(
            fake_message(src), [hooks[20][0]], "A", "hi", False)
        assert count == 0
        hooks[20][0].webhooks.assert_not_awaited()
        hooks[20][1].send.assert_not_awaited()

    async def test_destinations_exclude_hidden_and_locked_channels(self, monkeypatch):
        module, hooks, src = interserver_setup(
            [(20, GOOD, None), (21, dict(GOOD, view_channel=False), None),
             (22, EVERYONE, None)], monkeypatch)
        # Every guild "has" the module enabled with the same type/channel.
        for g in module.bot.guilds:
            pass
        target = SimpleNamespace(enabled=True, channel_id=1, interserver_type="english",
                                 show_server_name=True, show_avatar=True,
                                 allowed_mentions=False)
        module.bot.module_manager.get_module_instance = AsyncMock(return_value=target)
        found = await module._get_all_interserver_channels()
        assert [c.id for c in found] == [20]

    async def test_one_guild_failing_does_not_break_destination_listing(self, monkeypatch):
        module, hooks, src = interserver_setup([(20, GOOD, None), (21, GOOD, None)], monkeypatch)
        good = SimpleNamespace(enabled=True, channel_id=1, interserver_type="english")
        calls = {"n": 0}

        async def flaky(gid, mid):
            calls["n"] += 1
            if gid == 2:
                raise RuntimeError("db hiccup")
            return good
        module.bot.module_manager.get_module_instance = flaky
        found = await module._get_all_interserver_channels()
        assert [c.id for c in found] == [21]

    async def test_cosmetic_reaction_failure_never_aborts_a_delivered_relay(self, monkeypatch):
        module, hooks, src = interserver_setup([(20, GOOD, None)], monkeypatch)
        sent = hooks[20][1].send.return_value
        sent.add_reaction = AsyncMock(side_effect=forbidden())
        count = await module._relay_message(
            fake_message(src), [hooks[20][0]], "A", "hi", True)
        assert count == 1

    async def test_db_failure_after_delivery_still_counts(self, monkeypatch):
        module, hooks, src = interserver_setup([(20, GOOD, None)], monkeypatch)
        module.bot.db.add_relayed_message = AsyncMock(side_effect=RuntimeError("pg down"))
        count = await module._relay_message(
            fake_message(src), [hooks[20][0]], "A", "hi", False)
        assert count == 1

    async def test_deleted_webhook_is_recreated_once(self, monkeypatch):
        module, hooks, src = interserver_setup([(20, GOOD, None)], monkeypatch)
        ch, wh = hooks[20]
        wh.send = AsyncMock(side_effect=[not_found(10015),
                                         SimpleNamespace(id=1, add_reaction=AsyncMock())])
        count = await module._relay_message(fake_message(src), [ch], "A", "hi", False)
        assert count == 1 and wh.send.await_count == 2

    async def test_setup_slowmode_without_manage_channels_makes_no_call(self, monkeypatch):
        module, hooks, src = interserver_setup([], monkeypatch)
        ch = channel(cid=99, held=GOOD, guild=src)
        src.get_channel = lambda cid: ch
        module.channel_id = 99
        await module._setup_slowmode()
        ch.edit.assert_not_awaited()

    async def test_delete_copy_uses_the_webhook_without_any_permission(self, monkeypatch):
        module, hooks, src = interserver_setup([(20, GOOD, None)], monkeypatch)
        ch, wh = hooks[20]
        await module.delete_relayed_copy(ch, 777)
        wh.delete_message.assert_awaited_once_with(777)
        ch.fetch_message.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Adaptive slowmode
# --------------------------------------------------------------------------- #
class TestAdaptiveSlowmode:
    def make(self, held):
        g = guild_with(me_perms=EVERYONE)
        ch = channel(cid=7, held=held, guild=g)
        bot = bot_with(g)
        module = AdaptiveSlowmodeModule(bot, g.id)
        state = ChannelState()
        return module, ch, state

    async def test_no_manage_channels_in_channel_skips_and_backs_off(self):
        module, ch, state = self.make(EVERYONE)
        await module._apply_slowmode(ch, 10, state, 1, True, 1000.0)
        ch.edit.assert_not_awaited()
        assert state.blocked_until == 1000.0 + FAILURE_BACKOFF

    async def test_manage_channels_granted_only_on_the_channel_is_enough(self):
        module, ch, state = self.make(dict(EVERYONE, manage_channels=True))
        await module._apply_slowmode(ch, 10, state, 1, True, 1000.0)
        ch.edit.assert_awaited_once()
        assert state.current_level == 1

    async def test_forbidden_edit_backs_off(self):
        module, ch, state = self.make(dict(EVERYONE, manage_channels=True))
        ch.edit = AsyncMock(side_effect=forbidden())  # e.g. 2FA-protected server
        await module._apply_slowmode(ch, 10, state, 1, True, 1000.0)
        assert state.blocked_until > 1000.0 and state.current_level == 0

    async def test_hidden_channel_is_never_touched(self):
        module, ch, state = self.make(dict(EVERYONE, view_channel=False, manage_channels=True))
        await module._apply_slowmode(ch, 10, state, 1, True, 1000.0)
        ch.edit.assert_not_awaited()

    async def test_one_failing_channel_does_not_stop_the_others(self):
        g = guild_with(me_perms=EVERYONE)
        chans = [channel(cid=1, held=EVERYONE, guild=g), channel(cid=2, held=EVERYONE, guild=g)]
        g.get_channel = lambda cid: next(c for c in chans if c.id == cid)
        module = AdaptiveSlowmodeModule(bot_with(g), g.id)
        seen = []

        async def check(guild, ch_id, cfg, now):
            seen.append(ch_id)
            if ch_id == 1:
                raise RuntimeError("boom")
        module._check_channel = check
        module.channels_config = {1: {}, 2: {}}
        await module._check_all_channels()
        assert seen == [1, 2]

    async def test_malformed_stored_config_is_survived(self):
        g = guild_with(me_perms=EVERYONE)
        module = AdaptiveSlowmodeModule(bot_with(g), g.id)
        ok = await module.load_config({"channels": {"12": {"min_delay": 0}, "abc": {}, "13": "x"}})
        assert ok and set(module.channels_config) == {12, 13}
        # a non-numeric delay is ignored by the monitor, never raises
        ch = channel(cid=12, held=EVERYONE, guild=g)
        g.get_channel = lambda cid: ch
        module.channels_config = {12: {"min_delay": "x", "max_delay": None}}
        await module._check_all_channels()


# --------------------------------------------------------------------------- #
# Starboard
# --------------------------------------------------------------------------- #
class TestStarboard:
    def make(self, held):
        g = guild_with(me_perms=EVERYONE)
        sb = channel(cid=50, held=held, guild=g)
        g.get_channel = lambda cid: sb if cid == 50 else None
        sb.send = AsyncMock(return_value=SimpleNamespace(id=900))
        bot = bot_with(g)
        module = StarboardModule(bot, g.id)
        module.channel_id = 50
        module.enabled = True
        module._build_embed = AsyncMock(return_value=discord.Embed(description="x"))
        module._build_view = AsyncMock(return_value=None)
        return module, sb, g

    def message(self, mid=1):
        return SimpleNamespace(id=mid, channel=SimpleNamespace(id=2))

    async def test_without_embed_links_nothing_is_posted(self):
        module, sb, g = self.make(dict(EVERYONE, embed_links=False))
        await module._update_starboard(self.message(), 5)
        sb.send.assert_not_awaited()
        assert module._reported  # logged once

    async def test_hidden_starboard_channel_is_skipped_quietly(self):
        module, sb, g = self.make(dict(EVERYONE, view_channel=False))
        await module._update_starboard(self.message(), 5)
        sb.send.assert_not_awaited()

    async def test_deleted_starboard_channel_is_a_clean_skip(self):
        module, sb, g = self.make(EVERYONE)
        g.get_channel = lambda cid: None
        await module._update_starboard(self.message(), 5)  # no raise

    async def test_burst_of_reactions_posts_one_entry(self):
        import asyncio
        module, sb, g = self.make(EVERYONE)

        async def slow_send(**kw):
            await asyncio.sleep(0.01)
            return SimpleNamespace(id=900)
        sb.send = AsyncMock(side_effect=slow_send)
        await asyncio.gather(*(module._update_starboard(self.message(), 5 + i) for i in range(4)))
        assert sb.send.await_count == 1

    async def test_forbidden_is_reported_once(self, caplog):
        module, sb, g = self.make(EVERYONE)
        sb.send = AsyncMock(side_effect=forbidden())
        for i in range(3):
            await module._update_starboard(self.message(i + 1), 5)
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1

    async def test_source_channel_without_history_is_not_fetched(self):
        module, sb, g = self.make(EVERYONE)
        src = channel(cid=2, held=dict(EVERYONE, read_message_history=False), guild=g)
        g.get_channel = lambda cid: src if cid == 2 else sb
        payload = SimpleNamespace(emoji=discord.PartialEmoji(name="⭐"), channel_id=2,
                                  message_id=3)
        await module.on_reaction_add(payload)
        src.fetch_message.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Roles
# --------------------------------------------------------------------------- #
class TestAutoRole:
    def setup_method(self):
        self.top = FakeRole(500, 50)
        self.roles = [FakeRole(101, 10), FakeRole(102, 20, managed=True), FakeRole(103, 60),
                      FakeRole(104, 30)]

    def member(self, bot=False):
        m = MagicMock()
        m.id, m.bot, m.roles = 77, bot, []
        m.primary_guild = None
        m.add_roles = AsyncMock()
        m.remove_roles = AsyncMock()
        return m

    def module(self, me_perms=None, member_roles=(101, 102, 103, 104, 999)):
        g = guild_with(me_perms=me_perms if me_perms is not None else {"manage_roles": True},
                       roles=self.roles, top_role=self.top)
        module = AutoRoleModule(bot_with(g), g.id)
        module.member_roles = list(member_roles)
        module.enabled = True
        module._altguard_holds_back = AsyncMock(return_value=False)
        return module, g

    async def test_only_assignable_roles_are_sent(self):
        """One managed / above-the-bot / deleted role used to fail the whole
        request: the member then got none of the valid ones."""
        module, g = self.module()
        m = self.member()
        await module.apply_roles(m)
        granted = [c.args[0].id for c in m.add_roles.await_args_list]
        assert granted == [101, 104]
        assert all(c.kwargs["atomic"] is False for c in m.add_roles.await_args_list)

    async def test_without_manage_roles_nothing_is_attempted(self):
        module, g = self.module(me_perms={})
        m = self.member()
        await module.apply_roles(m)
        m.add_roles.assert_not_awaited()

    async def test_role_deleted_mid_operation_keeps_the_others(self):
        module, g = self.module()
        m = self.member()
        m.add_roles = AsyncMock(side_effect=[not_found(10011), None])
        await module.apply_roles(m)
        assert m.add_roles.await_count == 2

    async def test_member_left_mid_operation_is_silent(self, caplog):
        module, g = self.module()
        m = self.member()
        m.add_roles = AsyncMock(side_effect=not_found(10007))
        await module.apply_roles(m)
        assert not [r for r in caplog.records if r.levelname == "ERROR"]

    async def test_forbidden_one_role_does_not_block_the_next(self):
        module, g = self.module()
        m = self.member()
        m.add_roles = AsyncMock(side_effect=[forbidden(), None])
        await module.apply_roles(m)
        assert m.add_roles.await_count == 2

    async def test_guild_without_member_object_for_the_bot(self):
        module, g = self.module()
        g.me = None
        await module.apply_roles(self.member())  # no AttributeError

    async def test_timed_out_bot_skips(self):
        # discord.py strips manage_roles from guild_permissions of a timed-out bot
        module, g = self.module(me_perms={})
        m = self.member()
        await module.apply_roles(m)
        m.add_roles.assert_not_awaited()


class TestAutoRestoreRoles:
    def setup_method(self):
        self.top = FakeRole(500, 50)
        self.roles = [FakeRole(101, 10), FakeRole(102, 20, managed=True), FakeRole(103, 60)]

    def build(self, log_held=None, me_perms=None):
        log = channel(cid=30, held=log_held if log_held is not None else EVERYONE)
        g = guild_with(me_perms=me_perms if me_perms is not None else {"manage_roles": True},
                       channels=[log], roles=self.roles, top_role=self.top)
        bot = bot_with(g)
        bot.db = SimpleNamespace(
            get_saved_roles=AsyncMock(return_value={"roles": [101, 102, 103, 404]}),
            delete_saved_roles=AsyncMock(), save_user_roles=AsyncMock(return_value=True))
        module = AutoRestoreRolesModule(bot, g.id)
        module.enabled = True
        module.mode = "all"
        module.log_channel_id = 30
        member = MagicMock()
        member.id, member.mention = 8, "<@8>"
        member.add_roles = AsyncMock()
        member.display_avatar = SimpleNamespace(url="http://a")
        return module, member, log, bot

    async def test_restores_only_what_discord_will_accept(self):
        module, member, log, bot = self.build()
        await module.on_member_join(member)
        assert [c.args[0].id for c in member.add_roles.await_args_list] == [101]
        bot.db.delete_saved_roles.assert_awaited_once()

    async def test_without_manage_roles_saved_roles_are_kept(self):
        module, member, log, bot = self.build(me_perms={})
        await module.on_member_join(member)
        member.add_roles.assert_not_awaited()
        bot.db.delete_saved_roles.assert_not_awaited()

    async def test_locked_log_channel_is_skipped_without_error(self, caplog):
        module, member, log, bot = self.build(log_held=dict(EVERYONE, send_messages=False))
        await module.on_member_join(member)
        log.send.assert_not_awaited()
        assert not [r for r in caplog.records if r.levelname == "ERROR"]

    async def test_log_without_embed_links_is_not_sent(self):
        module, member, log, bot = self.build(log_held=dict(EVERYONE, embed_links=False))
        await module._send_log_saved(member, [self.roles[0]])
        log.send.assert_not_awaited()

    async def test_log_forbidden_race_is_debug_only(self, caplog):
        module, member, log, bot = self.build()
        log.send = AsyncMock(side_effect=forbidden())
        await module._send_log_restored(member, [self.roles[0]])
        assert not [r for r in caplog.records if r.levelname in ("ERROR", "WARNING")]


# --------------------------------------------------------------------------- #
# Welcome channel
# --------------------------------------------------------------------------- #
class TestWelcomeChannel:
    async def test_locked_channel_skipped_other_entry_still_sent(self):
        locked = channel(cid=1, held=dict(EVERYONE, send_messages=False))
        open_ch = channel(cid=2, held=EVERYONE)
        g = guild_with(me_perms=EVERYONE, channels=[locked, open_ch])
        module = WelcomeChannelModule(bot_with(g), g.id)
        module.enabled = True
        module.messages = [{"id": "a", "channel_id": 1, "message": "hi {user}"},
                           {"id": "b", "channel_id": 2, "message": "hi {user}"}]
        member = SimpleNamespace(id=9, mention="<@9>", display_name="x", name="x",
                                 joined_at=None)
        await module.on_member_join(member)
        locked.send.assert_not_awaited()
        open_ch.send.assert_awaited_once()


# --------------------------------------------------------------------------- #
# Default-enabled / automatic features: silence under hostile permissions
# --------------------------------------------------------------------------- #
def text_message(ch, g, content="https://open.spotify.com/track/abc"):
    msg = MagicMock()
    msg.guild, msg.channel, msg.content = g, ch, content
    msg.author = SimpleNamespace(bot=False, id=3)
    msg.webhook_id = None
    msg.reply = AsyncMock()
    return msg


class TestAutomaticFeaturesDegradeSilently:
    @pytest.mark.parametrize("held", [
        {},                                               # permissions=0, nothing at all
        dict(EVERYONE, send_messages=False),              # Muted-style deny
        dict(EVERYONE, read_message_history=False),       # reply would be error 160002
        dict(EVERYONE, view_channel=False),               # hidden / obfuscated
    ])
    async def test_music_links_does_not_spawn(self, held):
        ch = channel(cid=2, held=held)
        g = guild_with(me_perms=EVERYONE, channels=[ch])
        service = SimpleNamespace(spawn=MagicMock())
        module = MusicLinksModule(bot_with(g, music_links=service), g.id)
        module.enabled = True
        await module.on_message(text_message(ch, g))
        service.spawn.assert_not_called()

    async def test_music_links_works_with_exactly_the_needed_permissions(self):
        ch = channel(cid=2, held=dict(view_channel=True, send_messages=True,
                                      read_message_history=True))
        g = guild_with(me_perms=EVERYONE, channels=[ch])
        service = SimpleNamespace(spawn=MagicMock())
        module = MusicLinksModule(bot_with(g, music_links=service), g.id)
        module.enabled = True
        module._reply = MagicMock()
        await module.on_message(text_message(ch, g))
        service.spawn.assert_called_once()

    async def test_music_links_in_a_thread_uses_thread_send_permission(self):
        denied = channel(cls=discord.Thread, cid=3, held=dict(
            EVERYONE, send_messages=True, send_messages_in_threads=False))
        g = guild_with(me_perms=EVERYONE, channels=[denied])
        service = SimpleNamespace(spawn=MagicMock())
        module = MusicLinksModule(bot_with(g, music_links=service), g.id)
        module.enabled = True
        denied.parent_id = None
        await module.on_message(text_message(denied, g))
        service.spawn.assert_not_called()

    async def test_ai_detector_does_not_even_download_when_it_cannot_reply(self):
        ch = channel(cid=2, held=dict(EVERYONE, read_message_history=False))
        g = guild_with(me_perms=EVERYONE, channels=[ch])
        service = SimpleNamespace(candidate_images=MagicMock(return_value=[object()]),
                                  analyze_message=AsyncMock())
        module = AiDetectorModule(bot_with(g, ai_detector=service), g.id)
        module.enabled = True
        msg = text_message(ch, g)
        msg.author.bot = False
        await module.on_message(msg)
        service.analyze_message.assert_not_awaited()

    async def test_ai_detector_reply_to_deleted_message_is_silent(self, caplog):
        ch = channel(cid=2, held=EVERYONE)
        g = guild_with(me_perms=EVERYONE, channels=[ch])
        result = SimpleNamespace(kind="ai_generated")
        service = SimpleNamespace(
            candidate_images=MagicMock(return_value=[object()]),
            analyze_message=AsyncMock(return_value=(SimpleNamespace(id=1), result)),
            record=MagicMock())
        bot = bot_with(g, ai_detector=service)
        module = AiDetectorModule(bot, g.id)
        module.enabled = True
        msg = text_message(ch, g)
        resp = SimpleNamespace(status=400, reason="Bad Request")
        msg.reply = AsyncMock(side_effect=discord.HTTPException(resp, "Unknown message"))
        import utils.ai_detector_views as views
        orig = views.build_verdict_view
        views.build_verdict_view = lambda *a, **k: object()
        try:
            from unittest.mock import patch
            with patch("utils.guild_language.guild_locale", AsyncMock(return_value="en-US")):
                await module.on_message(msg)
        finally:
            views.build_verdict_view = orig
        assert not [r for r in caplog.records if r.levelname in ("WARNING", "ERROR")]

    async def test_translate_button_does_not_offer_without_history(self):
        ch = channel(cid=2, held=dict(EVERYONE, read_message_history=False))
        g = guild_with(me_perms=EVERYONE, channels=[ch])
        service = MagicMock()
        service.available.return_value = True
        service.detect = AsyncMock(return_value=SimpleNamespace(language="ja"))
        service.known_languages = AsyncMock(return_value=set())
        service.offer = AsyncMock(return_value=True)
        module = TranslateButtonModule(bot_with(g, translate_button=service), g.id)
        module.enabled = True
        msg = text_message(ch, g, content="これは日本語のメッセージです、よろしくお願いします")
        msg.type = discord.MessageType.default
        from unittest.mock import patch
        with patch("utils.guild_language.guild_locale", AsyncMock(return_value="en-US")):
            await module.on_message(msg)
        service.offer.assert_not_awaited()

    async def test_voice_transcription_does_not_reply_without_history(self):
        ch = channel(cid=2, held=dict(EVERYONE, read_message_history=False))
        g = guild_with(me_perms=EVERYONE, channels=[ch])
        service = MagicMock()
        service.available.return_value = True
        module = VoiceTranscriptionModule(bot_with(g, transcription=service), g.id)
        module.enabled = True
        msg = text_message(ch, g)
        import services.transcription_service as ts
        from unittest.mock import patch
        with patch.object(ts, "is_voice_message", lambda m: True):
            await module.on_message(msg)
        msg.reply.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Social notifications dispatch / bump reminder fan-out / listeners
# --------------------------------------------------------------------------- #
class TestSocialDispatch:
    async def test_locked_and_forbidden_channels_do_not_stop_the_rest(self, monkeypatch):
        from cogs import social_notifications as cog_mod
        locked = channel(cid=1, held=dict(EVERYONE, send_messages=False))
        failing = channel(cid=2, held=EVERYONE)
        failing.send = AsyncMock(side_effect=forbidden())
        fine = channel(cid=3, held=EVERYONE)
        g = guild_with(me_perms=EVERYONE, channels=[locked, failing, fine])
        bot = bot_with(g)
        bot.db = SimpleNamespace(get_target_followers=AsyncMock(return_value=[
            {"guild_id": 1, "channel_id": c} for c in (1, 2, 99, 3)]))
        monkeypatch.setattr(cog_mod, "is_guild_paused", AsyncMock(return_value=False))
        monkeypatch.setattr(cog_mod, "guild_locale", AsyncMock(return_value="en-US"))
        monkeypatch.setattr(cog_mod, "build_notification_view",
                            lambda *a, **k: (object(), discord.AllowedMentions.none()))
        cog = cog_mod.SocialNotifications.__new__(cog_mod.SocialNotifications)
        cog.bot, cog._warned = bot, set()
        await cog._dispatch_event({"platform": "rss", "target_id": "t"})
        locked.send.assert_not_awaited()
        failing.send.assert_awaited_once()
        fine.send.assert_awaited_once()

    async def test_locale_failure_in_one_guild_is_contained(self, monkeypatch):
        from cogs import social_notifications as cog_mod
        a = channel(cid=1, held=EVERYONE)
        b = channel(cid=2, held=EVERYONE)
        g = guild_with(me_perms=EVERYONE, channels=[a, b])
        bot = bot_with(g)
        bot.db = SimpleNamespace(get_target_followers=AsyncMock(return_value=[
            {"guild_id": 1, "channel_id": 1}, {"guild_id": 1, "channel_id": 2}]))
        monkeypatch.setattr(cog_mod, "is_guild_paused", AsyncMock(return_value=False))
        monkeypatch.setattr(cog_mod, "guild_locale",
                            AsyncMock(side_effect=[RuntimeError("pg"), "en-US"]))
        monkeypatch.setattr(cog_mod, "build_notification_view",
                            lambda *a, **k: (object(), discord.AllowedMentions.none()))
        cog = cog_mod.SocialNotifications.__new__(cog_mod.SocialNotifications)
        cog.bot, cog._warned = bot, set()
        await cog._dispatch_event({"platform": "rss", "target_id": "t"})
        b.send.assert_awaited_once()


class TestBumpFanOut:
    async def test_one_broken_channel_does_not_cost_the_others_their_reminder(self):
        from cogs.bump_reminder import BumpReminder
        g = guild_with(me_perms=EVERYONE)
        cog = BumpReminder.__new__(BumpReminder)
        cog.bot = bot_with(g)
        module = SimpleNamespace(enabled=True, entries_for_bot=lambda key: [
            {"channel_id": 1}, {"channel_id": 2}])
        cog.bot.module_manager = SimpleNamespace(get_module_instance=AsyncMock(return_value=module))
        calls = []

        async def post(guild, spec, entry, *a, **k):
            calls.append(entry["channel_id"])
            if entry["channel_id"] == 1:
                raise RuntimeError("deleted")
        cog._post = post
        from unittest.mock import patch
        with patch("utils.guild_language.guild_locale", AsyncMock(return_value="en-US")):
            await cog._fire({"guild_id": 1, "bot_key": "disboard",
                             "due_at": __import__("datetime").datetime.now(
                                 __import__("datetime").timezone.utc)})
        assert calls == [1, 2]

    async def test_post_in_denied_channel_makes_no_send(self):
        from cogs.bump_reminder import BumpReminder
        ch = channel(cid=1, held=dict(EVERYONE, send_messages=False))
        g = guild_with(me_perms=EVERYONE, channels=[ch])
        cog = BumpReminder.__new__(BumpReminder)
        cog.bot = bot_with(g)
        cog.bot.notifications = SimpleNamespace(send_channel=AsyncMock())
        await cog._post(g, SimpleNamespace(key="disboard"), {"channel_id": 1, "ping_mode": "off",
                                                            "role_ids": []},
                        {}, "en-US", bumper=None, bumped_at=None, late_by=0)
        cog.bot.notifications.send_channel.assert_not_awaited()


class TestServerTagListener:
    def test_sync_scheduling_never_raises_in_the_gateway_loop(self):
        from cogs.server_tag_events import ServerTagEvents
        cog = ServerTagEvents(SimpleNamespace(module_manager=None, _connection=None))
        # a payload that breaks _schedule_sync internally must be swallowed
        cog._schedule_sync = MagicMock(side_effect=RuntimeError("bad payload"))
        calls = []
        wrapper = cog._make_parser("GUILD_MEMBER_UPDATE", lambda d: calls.append(d) or "ok")
        assert wrapper({"guild_id": 1, "user": {"id": 2, "primary_guild": {}}}) == "ok"
        assert calls


class TestModuleEventsDelete:
    async def test_deleting_a_message_outside_interserver_makes_no_db_lookup(self):
        from cogs.module_events import ModuleEvents
        db = SimpleNamespace(get_interserver_message_by_original=AsyncMock())
        manager = SimpleNamespace(get_module_instance=AsyncMock(return_value=None))
        cog = ModuleEvents(SimpleNamespace(module_manager=manager, db=db))
        msg = SimpleNamespace(author=SimpleNamespace(bot=False), guild=SimpleNamespace(id=1),
                              channel=SimpleNamespace(id=5), id=9)
        await cog.on_message_delete(msg)
        db.get_interserver_message_by_original.assert_not_awaited()
