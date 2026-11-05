"""A card that prints its own "sent by" line still gets the server's badge."""

import asyncio

import discord
from discord import ui

from notifications.render import inject_guild_badge
from notifications.models import NotificationSource
from notifications.service import NotificationService

GID = 1394001780148535387
LINE = f"-# Sent by [**Moddy HQ**](https://discord.com/channels/{GID}) (`{GID}`)"


def _card(text):
    view = ui.LayoutView(timeout=None)
    view.add_item(ui.Container(ui.TextDisplay(text)))
    return view


def _text(view):
    return view.children[0].children[0].content


def test_badge_inserted_after_the_server_link_once():
    view = _card("### Title\nbody\n" + LINE)
    assert inject_guild_badge(view, GID, "<:verified:1>") == 1
    assert _text(view).endswith(f"(https://discord.com/channels/{GID})<:verified:1> (`{GID}`)")
    assert inject_guild_badge(view, GID, "<:verified:1>") == 0  # idempotent


def test_no_badge_no_change_and_other_guild_untouched():
    view = _card(LINE)
    assert inject_guild_badge(view, GID, "") == 0
    assert inject_guild_badge(view, 42, "<:verified:1>") == 0
    assert _text(view) == LINE


class _DB:
    def __init__(self, attrs):
        self.attrs = attrs

    async def get_guild(self, guild_id):
        return {"attributes": self.attrs}


class _Bot:
    def __init__(self, attrs):
        self.db = _DB(attrs)

    def get_guild(self, guild_id):
        return None


def _build(attrs):
    svc = NotificationService.__new__(NotificationService)
    svc.bot = _Bot(attrs)
    view = _card(LINE)
    source = NotificationSource.service_guild("expirations", GID)
    out = asyncio.run(svc._build_view(None, None, source, {}, view, "en-US",
                                      attribution=False))
    return _text(out)


def test_official_server_gets_the_badge_automatically():
    text = _build({"OFFICIAL": True})
    assert f") (`{GID}`)" not in text and f"`{GID}`" in text


def test_unverified_server_unchanged():
    assert _build({}) == LINE
