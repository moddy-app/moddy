"""Tickets must open without Administrator.

Discord rejects (50013) a channel create/edit whose overwrites allow or deny a
permission the bot does not hold. These tests pin the clamping that prevents it.

    pytest tests/test_ticket_permissions.py -q
"""

import discord
import pytest

from modules.tickets import PERM_ADMIN, PERM_CLOSE, PERM_VIEW
from services.ticket_service import (
    TicketError,
    TicketService,
    bot_permissions_for,
    clamp_overwrite,
    clamp_overwrites,
    destination_blockers,
    grantable_permissions,
    missing_core_permissions,
)
from tests.test_tickets import FakeGuild, FakeMember, FakeRole, make_category

CORE = dict(view_channel=True, send_messages=True, manage_channels=True,
            manage_roles=True)


def perms(**kw):
    return discord.Permissions(**kw)


def all_bits(overwrites):
    """Every permission name touched (allowed or denied) by any overwrite."""
    touched = set()
    for ow in overwrites.values():
        for name, value in ow:
            if value is not None:
                touched.add(name)
    return touched


class TestClamp:
    def test_administrator_is_left_alone(self):
        ow = discord.PermissionOverwrite(manage_messages=True, view_channel=False)
        assert clamp_overwrite(ow, perms(administrator=True)) is ow

    def test_unheld_bits_are_dropped_allow_and_deny(self):
        ow = discord.PermissionOverwrite(
            manage_messages=True, add_reactions=False, send_messages=True)
        out = clamp_overwrite(ow, perms(send_messages=True))
        assert out.send_messages is True
        assert out.manage_messages is None
        assert out.add_reactions is None

    def test_whole_map_is_clamped(self):
        role = FakeRole(1, "r")
        out = clamp_overwrites(
            {role: discord.PermissionOverwrite(manage_threads=True, view_channel=True)},
            perms(view_channel=True))
        assert out[role].manage_threads is None and out[role].view_channel is True


class TestMissing:
    def test_lists_missing_core(self):
        assert missing_core_permissions(perms(view_channel=True, send_messages=True)) \
            == ['manage_channels', 'manage_roles']

    def test_admin_misses_nothing(self):
        assert missing_core_permissions(perms(administrator=True)) == []

    def test_minimum_set_is_enough(self):
        assert missing_core_permissions(perms(**CORE)) == []


class _Parent:
    name = "Support"

    def __init__(self, p):
        self._p = p

    def permissions_for(self, _member):
        return self._p


class TestSafeOverwrites:
    def setup_method(self):
        self.service = TicketService(bot=None)
        self.support = FakeRole(10, "Support")
        self.lead = FakeRole(11, "Lead")
        self.owner = FakeMember(7, name="opener")
        self.guild = FakeGuild(roles=[self.support, self.lead], members=[self.owner])
        self.category = make_category(permissions={
            "10": [PERM_VIEW, PERM_CLOSE], "11": [PERM_ADMIN]})
        self.ticket = {'owner_id': 7, 'status': 'open', 'escalated': False,
                       'participants': [], 'participant_roles': []}
        # The bot's guild-level permissions: everything, so each test only
        # constrains the category side unless it says otherwise.
        everything = discord.Permissions.all()
        everything.administrator = False
        self.guild.me.guild_permissions = everything

    def test_minimum_bot_never_asks_for_more_than_it_has(self):
        have = perms(**CORE, read_message_history=True)
        parent = _Parent(have)
        out = self.service.safe_overwrites(
            self.guild, self.category, self.ticket, parent=parent)
        allowed = {n for n, v in have}
        for name in all_bits(out):
            assert getattr(have, name), name
        assert 'manage_messages' not in all_bits(out)
        assert 'manage_threads' not in all_bits(out)
        assert 'create_private_threads' not in all_bits(out)
        assert out[self.guild.default_role].view_channel is False
        assert out[self.guild.me].view_channel is True
        assert out[self.owner].attach_files is None  # not held -> not touched
        assert allowed  # sanity

    def test_extra_perms_are_kept_when_held(self):
        have = perms(**CORE, manage_messages=True, read_message_history=True)
        out = self.service.safe_overwrites(
            self.guild, self.category, self.ticket, parent=_Parent(have))
        assert out[self.support].manage_messages is True
        assert out[self.guild.me].manage_messages is True

    def test_missing_core_names_the_permissions(self):
        with pytest.raises(TicketError) as exc:
            self.service.safe_overwrites(
                self.guild, self.category, self.ticket,
                parent=_Parent(perms(view_channel=True, send_messages=True)))
        # With a destination category the message names it: server-wide
        # permissions cannot fix what the category's overwrites take away.
        assert exc.value.key == 'modules.tickets.errors.missing_bot_permissions_category'
        assert 'category' in exc.value.params
        assert 'manage_channels' in exc.value.params['permissions']
        # manage_roles is read at guild level (held here), never in the category.
        assert 'manage_roles' not in exc.value.params['permissions']

    def test_no_category_uses_guild_permissions(self):
        self.guild.me.guild_permissions = perms(**CORE)
        assert bot_permissions_for(self.guild, None).manage_channels

    def test_error_message_translates(self):
        msg = TicketError('modules.tickets.errors.missing_bot_permissions',
                          permissions='`manage_roles`').message('en-US')
        assert '`manage_roles`' in msg


class TestGrantableOnCreate:
    """Discord checks a channel *create* against guild-level permissions."""

    def setup_method(self):
        self.service = TicketService(bot=None)
        self.support = FakeRole(10, "Support")
        self.owner = FakeMember(7, name="opener")
        self.guild = FakeGuild(roles=[self.support], members=[self.owner])
        self.category = make_category(permissions={"10": [PERM_VIEW, PERM_CLOSE]})
        self.ticket = {'owner_id': 7, 'status': 'open', 'escalated': False,
                       'participants': [], 'participant_roles': []}

    def test_category_only_permission_is_not_handed_out(self):
        # The role lacks manage_messages; only the category grants it. Passing
        # it on to staff is what made Discord answer 50013 without Administrator.
        self.guild.me.guild_permissions = perms(**CORE, read_message_history=True)
        parent = _Parent(perms(**CORE, read_message_history=True, manage_messages=True))
        out = self.service.safe_overwrites(
            self.guild, self.category, self.ticket, parent=parent)
        assert 'manage_messages' not in all_bits(out)
        for name in all_bits(out):
            assert getattr(self.guild.me.guild_permissions, name), name

    def test_category_deny_still_wins(self):
        self.guild.me.guild_permissions = perms(**CORE, manage_messages=True)
        parent = _Parent(perms(**CORE))
        assert not grantable_permissions(self.guild, parent).manage_messages

    def test_administrator_keeps_everything(self):
        self.guild.me.guild_permissions = perms(administrator=True)
        assert grantable_permissions(self.guild, _Parent(perms())).administrator

    def test_no_category_is_the_guild_level(self):
        self.guild.me.guild_permissions = perms(**CORE, manage_messages=True)
        assert grantable_permissions(self.guild, None).manage_messages


class TestDestinationCategory:
    """A category hiding itself from @everyone blocks tickets (Discord: 50013)."""

    def setup_method(self):
        self.guild = FakeGuild(roles=[], members=[])
        everything = discord.Permissions.all()
        everything.administrator = False
        self.guild.me.guild_permissions = everything

    def test_hidden_category_is_reported(self):
        # Moddy's role holds everything but Administrator; the category strips
        # view_channel. This is the Top.gg review case.
        in_cat = discord.Permissions.all()
        in_cat.administrator = False
        in_cat.view_channel = False
        assert destination_blockers(self.guild, _Parent(in_cat)) == ['view_channel']

    def test_open_category_is_fine(self):
        assert destination_blockers(self.guild, _Parent(perms(**CORE))) == []

    def test_no_category_is_fine(self):
        assert destination_blockers(self.guild, None) == []


@pytest.mark.parametrize("locale", ["fr", "en-US", "es-ES", "pt-BR", "de"])
def test_category_messages_never_point_server_wide(locale):
    """The old "(or server-wide)" hint sent a reviewer the wrong way."""
    from utils.i18n import t
    text = t('modules.tickets.errors.missing_bot_permissions_category', locale=locale,
             permissions='`manage_roles`', category='Support')
    assert '`manage_roles`' in text and 'Support' in text
    text = t('modules.tickets.errors.cannot_create_channel_category', locale=locale,
             category='Support')
    assert 'Support' in text
    text = t('modules.tickets.category.destination_blocked', locale=locale,
             permissions='`view_channel`')
    assert '`view_channel`' in text


class TestTopggReviewCategory:
    """The Top.gg review server's ticket category, as read from Discord.

    @everyone is denied manage_channels + manage_roles on the category, Moddy's
    "Allow Manage Channels" role gets manage_channels back there, nothing gives
    manage_roles back. Server-wide, Moddy holds both. Discord accepted the
    create with overwrites (verified live); Moddy used to refuse it.
    """

    def setup_method(self):
        self.service = TicketService(bot=None)
        self.owner = FakeMember(7, name="opener")
        self.guild = FakeGuild(roles=[], members=[self.owner])
        self.guild.me.guild_permissions = perms(**CORE, read_message_history=True)
        self.parent = _Parent(perms(view_channel=True, send_messages=True,
                                    manage_channels=True, read_message_history=True))
        self.ticket = {'owner_id': 7, 'status': 'open', 'escalated': False,
                       'participants': [], 'participant_roles': []}

    def test_ticket_opens_without_manage_roles_in_the_category(self):
        out = self.service.safe_overwrites(
            self.guild, make_category(), self.ticket, parent=self.parent)
        assert out[self.guild.default_role].view_channel is False
        assert out[self.guild.me].view_channel is True

    def test_config_does_not_warn(self):
        assert destination_blockers(self.guild, self.parent) == []

    def test_still_needs_manage_roles_server_wide(self):
        self.guild.me.guild_permissions = perms(view_channel=True, send_messages=True,
                                                manage_channels=True)
        # Not the category's fault: the server-level message, no /config
        # category warning (the module permission check reports it there).
        assert destination_blockers(self.guild, self.parent) == []
        with pytest.raises(TicketError) as exc:
            self.service.safe_overwrites(
                self.guild, make_category(), self.ticket, parent=self.parent)
        assert exc.value.key == 'modules.tickets.errors.missing_bot_permissions'
        assert 'manage_roles' in exc.value.params['permissions']
