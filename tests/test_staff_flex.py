"""The staff flex card and the Flex button `/user` adds in a DM.

    pytest tests/test_staff_flex.py -q
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from utils import staff_flex
from utils.errors import NotYours


def _interaction(user_id=7, dm=False, private=False):
    interaction = MagicMock()
    interaction.user = SimpleNamespace(id=user_id, mention=f"<@{user_id}>")
    interaction.locale = "en-US"
    interaction.context = SimpleNamespace(dm_channel=dm, private_channel=private)
    interaction.response.send_message = AsyncMock()
    return interaction


@pytest.fixture
def roles(monkeypatch):
    state = SimpleNamespace(value=[SimpleNamespace(value="Support")])

    async def fake(user_id):
        return state.value

    monkeypatch.setattr(staff_flex, "staff_roles", fake)
    return state


@pytest.mark.parametrize("dm,private,expected", [
    (True, False, True), (False, True, True), (False, False, False),
])
def test_private_context(dm, private, expected):
    assert staff_flex.is_private_context(_interaction(dm=dm, private=private)) is expected


async def test_button_posts_the_card_publicly(roles):
    interaction = _interaction()
    await staff_flex.FlexSendButton(7).callback(interaction)
    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is False
    assert kwargs["view"] is not None


async def test_button_refuses_someone_else(roles, monkeypatch):
    import cogs.error_handler as eh

    reported = AsyncMock()
    monkeypatch.setattr(eh, "report_component_error", reported)
    interaction = _interaction(user_id=8)
    await staff_flex.FlexSendButton(7).callback(interaction)
    interaction.response.send_message.assert_not_awaited()
    assert isinstance(reported.await_args.args[1], NotYours)


async def test_button_refuses_a_former_staffer(roles):
    roles.value = []
    interaction = _interaction()
    await staff_flex.FlexSendButton(7).callback(interaction)
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_card_is_none_for_non_staff(roles):
    roles.value = []
    assert await staff_flex.build_flex_view(SimpleNamespace(id=1, mention="x"), "en-US") is None


@pytest.mark.parametrize("show", [True, False])
def test_user_view_carries_the_button_only_when_asked(show):
    from cogs.user import UserInfoView

    view = UserInfoView(
        user_data={"id": "7", "username": "me", "global_name": "Me"},
        bot_data=None, moddy_attributes={}, locale="en-US", author_id=7,
        bot=None, show_flex=show,
    )
    ids = [getattr(i, "custom_id", None) for i in view.walk_children()]
    assert ("moddy:user:flex:send:7" in ids) is show


@pytest.mark.parametrize("locale", ["fr", "en-US", "es-ES", "pt-BR", "de"])
def test_label_is_translated(locale):
    import json

    with open(f"locales/{locale}.json", encoding="utf-8") as fh:
        assert json.load(fh)["commands"]["user"]["buttons"]["flex"]
