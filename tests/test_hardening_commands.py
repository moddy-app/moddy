"""Hardening: commands that act on Discord objects with hostile permissions."""
import asyncio
import types
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs import moderation_commands as mc


def _guild(perms: discord.Permissions, owner_id=1):
    me = types.SimpleNamespace(guild_permissions=perms, top_role=10)
    return types.SimpleNamespace(me=me, owner_id=owner_id, id=5)


class TestBotPermissionCheck:
    def test_permissions_zero_gets_precise_card(self):
        g = _guild(discord.Permissions.none())
        for action in ("ban", "kick", "mute"):
            assert mc._bot_permission_check(g, action, "en-US") is not None

    def test_only_the_asked_permission_is_enough(self):
        assert mc._bot_permission_check(_guild(discord.Permissions(ban_members=True)), "ban", "en-US") is None
        assert mc._bot_permission_check(_guild(discord.Permissions(kick_members=True)), "kick", "en-US") is None
        assert mc._bot_permission_check(_guild(discord.Permissions(moderate_members=True)), "mute", "en-US") is None
        # the wrong permission is not enough
        assert mc._bot_permission_check(_guild(discord.Permissions(kick_members=True)), "ban", "en-US") is not None

    def test_administrator_passes_and_warn_needs_nothing(self):
        assert mc._bot_permission_check(_guild(discord.Permissions.all()), "ban", "en-US") is None
        assert mc._bot_permission_check(_guild(discord.Permissions.none()), "warn", "en-US") is None

    def test_guild_me_missing_defers_to_discord(self):
        g = types.SimpleNamespace(me=None)
        assert mc._bot_permission_check(g, "ban", "en-US") is None

    def test_message_names_permission_and_server_wide_place(self):
        from utils.i18n import t
        for loc in ("fr", "en-US", "es-ES", "pt-BR", "de"):
            txt = t("commands.moderation.errors.bot_missing_permission", locale=loc, permission="Ban Members")
            assert "Ban Members" in txt


class TestTimeoutOnAdmin:
    def test_mute_of_administrator_is_refused_upfront(self):
        guild = types.SimpleNamespace(
            owner_id=1, me=types.SimpleNamespace(top_role=10))
        target = MagicMock(spec=discord.Member)
        target.id = 3
        target.top_role = 1
        target.guild_permissions = discord.Permissions(administrator=True)
        mod = types.SimpleNamespace(id=2, top_role=5)
        assert mc._hierarchy_check(guild, mod, target, "mute", "en-US") is not None
        # a ban of the same member is not blocked by this rule
        assert mc._hierarchy_check(guild, mod, target, "ban", "en-US") is None


class TestBrocoliNoSendPermission:
    def test_forbidden_on_loading_card_is_silent(self):
        from cogs.brocoli_chat import BrocoliChat

        cog = BrocoliChat.__new__(BrocoliChat)
        resp = MagicMock(status=403, reason="Forbidden")
        channel = MagicMock()
        channel.id = 9
        channel.send = AsyncMock(side_effect=discord.Forbidden(resp, "Missing Permissions"))
        message = MagicMock()
        message.guild.id = 1
        message.author.id = 2
        message.channel = channel
        asyncio.run(cog._run_turn(message, {}, "en-US"))  # must not raise
        channel.send.assert_awaited_once()
