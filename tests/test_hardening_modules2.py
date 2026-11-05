"""Hostile-permission hardening: tickets edit paths, server logs, AltGuard, automod.

    pytest tests/test_hardening_modules2.py -q
"""

import asyncio
import types
from types import SimpleNamespace

import discord
import pytest

from modules.tickets import PERM_ADMIN, PERM_CLOSE, PERM_VIEW
from services.ticket_service import (
    TicketError, TicketService, category_label, editable_permissions,
    missing_sync_permissions,
)
from tests.test_tickets import FakeGuild, FakeMember, FakeRole, make_category


def perms(**kw):
    return discord.Permissions(**kw)


def forbidden(code=50013, status=403):
    resp = SimpleNamespace(status=status, reason="Forbidden")
    return discord.Forbidden(resp, {"code": code, "message": "Missing Permissions"})


def not_found():
    return discord.NotFound(SimpleNamespace(status=404, reason="Not Found"),
                            {"code": 10015, "message": "Unknown Webhook"})


class FakeTicketChannel:
    """A ticket text channel; permissions_for returns what the test configured."""

    def __init__(self, guild, local, category_perms=None, fail=()):
        self.id = 555
        self.name = "ticket-0001"
        self.guild = guild
        self.category = (SimpleNamespace(id=100, name="TEST", flags=SimpleNamespace(value=0),
                                         permissions_for=lambda _m: category_perms)
                         if category_perms is not None else None)
        self._local = local
        self.edits = []
        self._fail = list(fail)

    def permissions_for(self, _member):
        return self._local

    async def edit(self, **kwargs):
        self.edits.append(kwargs)
        if self._fail:
            raise self._fail.pop(0)


def _service_and_guild():
    service = TicketService(bot=None)

    async def no_members(guild, ticket):
        return {}
    service.resolve_ticket_members = no_members
    support = FakeRole(10, "Support")
    owner = FakeMember(7, name="opener")
    guild = FakeGuild(roles=[support], members=[owner])
    category = make_category(permissions={"10": [PERM_VIEW, PERM_CLOSE]})
    ticket = {'owner_id': 7, 'status': 'open', 'escalated': False,
              'participants': [], 'participant_roles': []}
    return service, guild, category, ticket


# --------------------------------------------------------------------------- #
# Tickets: edit paths under the restrictive TEST category
# --------------------------------------------------------------------------- #
class TestSyncUnderRestrictiveCategory:
    """@everyone denies manage_channels/manage_roles on the category; Moddy holds
    manage_channels + manage_roles server-wide only."""

    def setup_method(self):
        self.service, self.guild, self.category, self.ticket = _service_and_guild()
        self.guild.me.guild_permissions = perms(
            view_channel=True, send_messages=True, manage_channels=True,
            manage_roles=True, read_message_history=True)
        # In the category the bot lost manage_channels and manage_roles ...
        self.cat_perms = perms(view_channel=True, send_messages=True,
                               read_message_history=True)
        # ... but the ticket channel carries the bot's own overwrite.
        self.local = perms(view_channel=True, send_messages=True, manage_channels=True,
                           read_message_history=True)

    @pytest.mark.asyncio
    async def test_close_claim_escalate_sync_works_without_category_manage_channels(self):
        channel = FakeTicketChannel(self.guild, self.local, self.cat_perms)
        assert await self.service.sync_permissions(channel, self.category, self.ticket)
        assert len(channel.edits) == 1
        sent = channel.edits[0]["overwrites"]
        assert sent[self.guild.default_role].view_channel is False

    def test_missing_sync_reads_the_channel_not_the_category(self):
        channel = FakeTicketChannel(self.guild, self.local, self.cat_perms)
        assert missing_sync_permissions(self.guild, channel) == []

    def test_manage_roles_in_the_channel_overwrite_is_enough(self):
        self.guild.me.guild_permissions = perms(view_channel=True, manage_channels=True)
        channel = FakeTicketChannel(
            self.guild, perms(view_channel=True, manage_roles=True), self.cat_perms)
        assert missing_sync_permissions(self.guild, channel) == []

    def test_no_manage_roles_anywhere_is_reported(self):
        self.guild.me.guild_permissions = perms(view_channel=True, manage_channels=True)
        channel = FakeTicketChannel(self.guild, self.local, self.cat_perms)
        assert missing_sync_permissions(self.guild, channel) == ['manage_roles']

    @pytest.mark.asyncio
    async def test_missing_manage_roles_returns_false_without_calling_discord(self):
        self.guild.me.guild_permissions = perms(view_channel=True)
        channel = FakeTicketChannel(self.guild, perms(view_channel=True))
        assert await self.service.sync_permissions(channel, self.category, self.ticket) is False
        assert channel.edits == []

    def test_union_then_intersection(self):
        # manage_messages only in the category: allowed by the union, not by the
        # strict fallback.
        self.guild.me.guild_permissions = perms(manage_roles=True)
        channel = FakeTicketChannel(self.guild, self.local,
                                    perms(manage_messages=True))
        assert editable_permissions(self.guild, channel).manage_messages
        assert not editable_permissions(self.guild, channel, strict=True).manage_messages

    @pytest.mark.asyncio
    async def test_forbidden_retries_with_the_strict_clamp_then_succeeds(self):
        self.guild.me.guild_permissions = perms(
            view_channel=True, manage_roles=True, manage_channels=True)
        cat = perms(view_channel=True, manage_messages=True)
        channel = FakeTicketChannel(self.guild, self.local, cat, fail=[forbidden()])
        assert await self.service.sync_permissions(channel, self.category, self.ticket)
        assert len(channel.edits) == 2

    @pytest.mark.asyncio
    async def test_persistent_forbidden_never_raises(self):
        channel = FakeTicketChannel(self.guild, self.local, self.cat_perms,
                                    fail=[forbidden(), forbidden()])
        assert await self.service.sync_permissions(channel, self.category, self.ticket) is False

    @pytest.mark.asyncio
    async def test_deleted_channel_is_not_an_error(self):
        channel = FakeTicketChannel(self.guild, self.local, self.cat_perms,
                                    fail=[discord.NotFound(
                                        SimpleNamespace(status=404, reason="x"),
                                        {"code": 10003, "message": "Unknown Channel"})])
        assert await self.service.sync_permissions(channel, self.category, self.ticket) is False

    @pytest.mark.asyncio
    async def test_administrator_bypasses(self):
        self.guild.me.guild_permissions = perms(administrator=True)
        channel = FakeTicketChannel(self.guild, perms(), self.cat_perms)
        assert await self.service.sync_permissions(channel, self.category, self.ticket)


class FakeDB:
    def __init__(self):
        self.calls = []

    async def set_ticket_category(self, channel_id, panel_id, category_id):
        self.calls.append((channel_id, panel_id, category_id))

    async def get_ticket_by_channel(self, channel_id):
        return {'owner_id': 7, 'status': 'open', 'escalated': False, 'participants': [],
                'participant_roles': [], 'panel_id': 'p2', 'category_id': 'c2',
                'number': 1, 'claimed_by': None}


class TestMove:
    def setup_method(self):
        self.service, self.guild, self.category, self.ticket = _service_and_guild()
        self.guild.me.guild_permissions = perms(
            view_channel=True, send_messages=True, manage_channels=True, manage_roles=True)
        self.parent = SimpleNamespace(id=200, name="Dest", flags=SimpleNamespace(value=0),
                                      permissions_for=lambda _m: perms(
                                          view_channel=True, manage_channels=True))
        self.db = FakeDB()
        self.service.bot = SimpleNamespace(db=self.db)
        self.target = dict(make_category(name="Dest"), id='c2', discord_category_id=200)
        self.panel = {'id': 'p2'}
        self.actor = FakeMember(1)

    def _wire(self, channel):
        service = self.service
        ticket = {'owner_id': 7, 'status': 'open', 'escalated': False, 'participants': [],
                  'participant_roles': [], 'panel_id': 'p1', 'category_id': 'c1', 'number': 1}

        async def resolve(ch):
            return ticket, {'id': 'p1'}, self.category

        async def get_module(gid):
            return SimpleNamespace(panels=[dict(self.panel, categories=[self.target])])

        service.resolve = resolve
        service.get_module = get_module
        service.require = lambda *a, **k: None
        self.guild.get_channel = lambda cid: self.parent
        self.guild.id = 1
        import services.ticket_service as ts
        self._orig = ts.locate_category
        ts.locate_category = lambda cfg, p, c: ({'id': 'p2'}, self.target)

    def teardown_method(self):
        import services.ticket_service as ts
        if hasattr(self, "_orig"):
            ts.locate_category = self._orig

    @pytest.mark.asyncio
    async def test_channel_without_manage_channels_gets_a_precise_error(self, monkeypatch):
        channel = FakeTicketChannel(self.guild, perms(view_channel=True))
        self._wire(channel)
        monkeypatch.setattr(discord, "CategoryChannel", type(self.parent), raising=False)
        with pytest.raises(TicketError) as exc:
            await self.service.move_ticket(channel, self.actor, 'p2', 'c2')
        assert exc.value.key == 'modules.tickets.errors.cannot_move_channel'
        assert self.db.calls == []  # nothing written before the refusal

    @pytest.mark.asyncio
    async def test_refused_move_rolls_the_category_back(self, monkeypatch):
        channel = FakeTicketChannel(self.guild, perms(view_channel=True, manage_channels=True),
                                    fail=[forbidden()])
        self._wire(channel)
        monkeypatch.setattr(discord, "CategoryChannel", type(self.parent), raising=False)
        with pytest.raises(TicketError) as exc:
            await self.service.move_ticket(channel, self.actor, 'p2', 'c2')
        assert exc.value.key == 'modules.tickets.errors.cannot_move_channel'
        assert self.db.calls[0] == (555, 'p2', 'c2')
        assert self.db.calls[-1] == (555, 'p1', 'c1')


def test_hidden_category_is_named_by_mention():
    hidden = SimpleNamespace(id=42, name="___hidden___",
                             flags=SimpleNamespace(value=1 << 17))
    visible = SimpleNamespace(id=43, name="Support", flags=SimpleNamespace(value=0))
    assert category_label(hidden) == "<#42>"
    assert category_label(visible) == "Support"


@pytest.mark.parametrize("locale", ["fr", "en-US", "es-ES", "pt-BR", "de"])
def test_cannot_move_message_in_every_locale(locale):
    from utils.i18n import t
    text = t('modules.tickets.errors.cannot_move_channel', locale=locale, category="X")
    assert "X" in text and "cannot_move_channel" not in text


# --------------------------------------------------------------------------- #
# Server logs
# --------------------------------------------------------------------------- #
from serverlogs.dispatcher import LogDispatcher, NEGATIVE_WEBHOOK_TTL  # noqa: E402
from serverlogs.renderer import fmt_channel  # noqa: E402


class _LogChannel:
    def __init__(self, local, send_exc=None):
        self.id = 9
        self.flags = SimpleNamespace(value=0)
        self.guild = SimpleNamespace(me=object())
        self._local = local
        self.sent = []
        self._send_exc = send_exc

    def permissions_for(self, _m):
        return self._local

    async def send(self, **kw):
        if self._send_exc:
            raise self._send_exc
        self.sent.append(kw)


def test_hidden_channel_never_renders_its_placeholder_name():
    hidden = SimpleNamespace(id=5, name="___hidden___", flags=SimpleNamespace(value=1 << 17))
    assert fmt_channel(hidden) == "<#5>"
    flagless = SimpleNamespace(id=6, name="___hidden___")
    assert fmt_channel(flagless) == "<#6>"
    assert "#general" in fmt_channel(SimpleNamespace(id=7, name="general"))


@pytest.mark.asyncio
async def test_fallback_skips_a_channel_the_bot_cannot_write_in():
    dispatcher = LogDispatcher(SimpleNamespace(user=None))
    channel = _LogChannel(perms(view_channel=True))  # no send / embed
    await dispatcher._fallback_send(channel, [discord.Embed(title="x")], [])
    assert channel.sent == []


@pytest.mark.asyncio
async def test_fallback_drops_attachments_without_attach_files():
    dispatcher = LogDispatcher(SimpleNamespace(user=None))
    channel = _LogChannel(perms(view_channel=True, send_messages=True, embed_links=True))
    import io
    await dispatcher._fallback_send(channel, [discord.Embed(title="x")],
                                    [discord.File(io.BytesIO(b"a"), filename="a.txt")])
    assert channel.sent and channel.sent[0]["files"] is discord.utils.MISSING


@pytest.mark.asyncio
async def test_obfuscated_log_channel_is_not_written_to():
    dispatcher = LogDispatcher(SimpleNamespace(user=None))
    channel = _LogChannel(perms(view_channel=True, send_messages=True, embed_links=True))
    channel.flags = SimpleNamespace(value=1 << 17)
    await dispatcher._fallback_send(channel, [discord.Embed(title="x")], [])
    assert channel.sent == []


@pytest.mark.asyncio
async def test_deleted_webhook_is_recreated_and_the_batch_retried():
    dispatcher = LogDispatcher(SimpleNamespace(user=None))
    sent = []

    class Hook:
        def __init__(self, dead):
            self.dead = dead

        async def send(self, **kw):
            if self.dead:
                raise not_found()
            sent.append(kw)

    hooks = [Hook(True), Hook(False)]

    async def get(channel):
        hook = hooks.pop(0) if hooks else None
        return hook, None

    dispatcher._get_webhook = get
    channel = _LogChannel(perms())
    await dispatcher._send(channel, [discord.Embed(title="a")], [])
    assert len(sent) == 1 and channel.sent == []


@pytest.mark.asyncio
async def test_oversized_batch_is_split_instead_of_lost():
    dispatcher = LogDispatcher(SimpleNamespace(user=None))
    sent = []

    class Hook:
        async def send(self, embeds, **kw):
            if len(embeds) > 1:
                raise discord.HTTPException(
                    SimpleNamespace(status=400, reason="Bad Request"),
                    {"code": 50035, "message": "too large"})
            sent.append(embeds)

    async def get(channel):
        return Hook(), None

    dispatcher._get_webhook = get
    await dispatcher._send(_LogChannel(perms()), [discord.Embed(title="a"),
                                                  discord.Embed(title="b")], [])
    assert len(sent) == 2


@pytest.mark.asyncio
async def test_missing_webhook_permission_is_retried_after_the_ttl(monkeypatch):
    dispatcher = LogDispatcher(SimpleNamespace(user=None))
    calls = []

    async def resolve(channel):
        calls.append(1)
        return None

    dispatcher._resolve_webhook = resolve
    channel = _LogChannel(perms())
    await dispatcher._get_webhook(channel)
    await dispatcher._get_webhook(channel)
    assert len(calls) == 1  # negative result cached
    dispatcher._negative_until[channel.id] -= NEGATIVE_WEBHOOK_TTL + 1
    await dispatcher._get_webhook(channel)
    assert len(calls) == 2  # but not forever: the admin may have fixed it


@pytest.mark.asyncio
async def test_webhook_limit_falls_back_without_raising():
    dispatcher = LogDispatcher(SimpleNamespace(user=SimpleNamespace(display_avatar=None)))
    guild = SimpleNamespace(me=object())

    class Chan(_LogChannel):
        async def webhooks(self):
            return []

        async def create_webhook(self, **kw):
            raise discord.HTTPException(
                SimpleNamespace(status=400, reason="x"),
                {"code": 30007, "message": "Maximum number of webhooks reached (15)"})

    chan = Chan(perms(manage_webhooks=True, view_channel=True))
    chan.guild = guild
    assert await dispatcher._resolve_webhook(chan) is None


@pytest.mark.asyncio
async def test_executor_does_not_wait_without_view_audit_log():
    from serverlogs.service import LogService
    me = SimpleNamespace(guild_permissions=perms(view_channel=True))
    guild = SimpleNamespace(id=1, me=me)
    service = LogService(SimpleNamespace(module_manager=None))
    loop = asyncio.get_running_loop()
    start = loop.time()
    who, reason = await service.executor(guild, discord.AuditLogAction.kick, 5, wait=5)
    assert (who, reason) == (None, None)
    assert loop.time() - start < 0.5


def test_logs_module_needs_nothing_server_wide():
    from modules.logs import LogsModule
    assert "manage_webhooks" not in LogsModule.required_permissions()
    reqs = LogsModule.channel_requirements(
        {"categories": {"server": {"channel_ids": [1]}}})
    assert set(reqs[1]) >= {"view_channel", "send_messages", "embed_links"}


# --------------------------------------------------------------------------- #
# AltGuard
# --------------------------------------------------------------------------- #
class _Role:
    def __init__(self, rid, position, managed=False):
        self.id, self.position, self.managed = rid, position, managed

    def is_default(self):
        return False

    def __ge__(self, other):
        return self.position >= other.position

    def __gt__(self, other):
        return self.position > other.position


class _GateMember:
    id = 77

    def __init__(self, fail_add=False):
        self.roles = []
        self.added, self.removed = [], []
        self.fail_add = fail_add

    async def add_roles(self, *roles, reason=None):
        if self.fail_add:
            raise forbidden()
        self.added += roles

    async def remove_roles(self, *roles, reason=None):
        self.removed += roles


def _gate_module(my_position=10, admin=False, manage_roles=True):
    from modules.altguard import AltGuardModule
    me = SimpleNamespace(
        top_role=_Role(0, my_position),
        guild_permissions=perms(administrator=admin, manage_roles=manage_roles))
    guild = SimpleNamespace(me=me, get_role=lambda rid: None)
    bot = SimpleNamespace(get_guild=lambda gid: guild, db=None)
    module = AltGuardModule(bot, 1)
    return module


@pytest.mark.asyncio
async def test_unassignable_verified_role_never_keeps_a_passed_member_locked():
    module = _gate_module(my_position=5)
    member = _GateMember()
    unverified, verified = _Role(1, 3), _Role(2, 9)  # verified is above Moddy
    ok_remove = await module._change_roles(member, remove=[unverified], reason="r")
    ok_add = await module._change_roles(member, add=[verified], reason="r")
    assert ok_remove is True and member.removed == [unverified]
    assert ok_add is False and member.added == []


@pytest.mark.asyncio
async def test_managed_role_and_missing_manage_roles_are_reported_not_raised():
    module = _gate_module(my_position=50)
    member = _GateMember()
    assert await module._change_roles(member, add=[_Role(3, 1, managed=True)], reason="r") is False
    module = _gate_module(manage_roles=False)
    assert await module._change_roles(member, add=[_Role(4, 1)], reason="r") is False


@pytest.mark.asyncio
async def test_discord_403_and_member_gone_are_contained():
    module = _gate_module()
    member = _GateMember(fail_add=True)
    assert await module._change_roles(member, add=[_Role(5, 1)], reason="r") is False


def test_gate_overwrite_drops_flags_the_bot_lacks_but_keeps_view_channel():
    module = _gate_module()
    channel = SimpleNamespace(permissions_for=lambda _m: perms(view_channel=True))
    ow = discord.PermissionOverwrite(view_channel=False, create_private_threads=False,
                                     send_messages=False)
    clamped = module._clamp_overwrite(channel, ow)
    assert clamped.view_channel is False
    assert clamped.create_private_threads is None


@pytest.mark.asyncio
async def test_altguard_service_timeout_is_an_altguard_error(monkeypatch):
    import services.altguard_client as ac

    class Boom:
        def post(self, *a, **k):
            raise asyncio.TimeoutError()

    client = ac.AltGuardClient(SimpleNamespace())
    monkeypatch.setattr(ac, "ALTGUARD_BOT_TOKEN", "t")
    monkeypatch.setattr(ac, "ALTGUARD_API_URL", "http://x")

    async def sess():
        return Boom()

    client._get_session = sess
    with pytest.raises(ac.AltGuardError) as exc:
        await client._post("/p", {})
    assert exc.value.code == "network_error"


# --------------------------------------------------------------------------- #
# Automod actions
# --------------------------------------------------------------------------- #
def test_two_factor_403_is_a_permission_gap_not_a_hierarchy_problem():
    from automod import actions as act
    from modules.automod_ai import AutomodModule
    assert AutomodModule._forbidden_code(forbidden(code=60003), act.FAIL_HIERARCHY) \
        == act.FAIL_PERMISSION
    assert AutomodModule._forbidden_code(forbidden(code=50013), act.FAIL_HIERARCHY) \
        == act.FAIL_HIERARCHY


@pytest.mark.asyncio
async def test_timeout_on_an_administrator_is_not_attempted():
    from automod import actions as act
    from modules.automod_ai import AutomodModule

    module = AutomodModule.__new__(AutomodModule)
    calls = []
    member = SimpleNamespace(id=3, guild_permissions=perms(administrator=True),
                             top_role=_Role(1, 1))

    async def edit(**kw):
        calls.append(kw)

    member.edit = edit
    module._blocker = lambda *a, **k: None
    decision = SimpleNamespace(duree_secondes=3600, duree_heures=1, gravite=1,
                               raison="r", categorie="x")
    module._duration_td = lambda d: None
    module._audit_reason = lambda *a, **k: "r"
    module._mark_moddy_initiated = lambda *a, **k: None
    applied, failed = await module._apply_discord_sanction(
        SimpleNamespace(me=object()), member, decision, None, ["mute"])
    assert applied == [] and failed == {"mute": act.FAIL_HIERARCHY}
    assert calls == []
