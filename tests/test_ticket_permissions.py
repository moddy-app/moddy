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
        assert exc.value.key == 'modules.tickets.errors.missing_bot_permissions'
        assert 'manage_roles' in exc.value.params['permissions']
        assert 'manage_channels' in exc.value.params['permissions']

    def test_no_category_uses_guild_permissions(self):
        self.guild.me.guild_permissions = perms(**CORE)
        assert bot_permissions_for(self.guild, None).manage_channels

    def test_error_message_translates(self):
        msg = TicketError('modules.tickets.errors.missing_bot_permissions',
                          permissions='`manage_roles`').message('en-US')
        assert '`manage_roles`' in msg
