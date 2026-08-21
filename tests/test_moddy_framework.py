"""Contract tests for the public Moddy framework surface."""

from moddy import Cog, app_commands, ui
from moddy.commands import Bot


def test_framework_primitives_keep_discord_py_inheritance():
    from discord.ext import commands

    assert issubclass(Bot, commands.Bot)
    assert issubclass(Cog, commands.Cog)


def test_global_command_applies_moddy_installation_contract():
    @app_commands.global_command(name="frameworktest", description="Framework test")
    async def framework_test(interaction):
        return None

    assert framework_test.name == "frameworktest"
    assert framework_test.guild_only is False
    assert framework_test.allowed_contexts.guild is True
    assert framework_test.allowed_contexts.dm_channel is True
    assert framework_test.allowed_contexts.private_channel is True
    assert framework_test.allowed_installs.guild is True
    assert framework_test.allowed_installs.user is True


def test_guild_command_is_not_global():
    @app_commands.guild_command(name="guildframeworktest", description="Guild framework test")
    async def guild_framework_test(interaction):
        return None

    assert guild_framework_test.guild_only is True


def test_message_builds_a_components_v2_layout():
    view = ui.message("Title", "Description", fields=[ui.Field("Name", "Value")])

    assert isinstance(view, ui.LayoutView)
    assert len(view.children) == 1
