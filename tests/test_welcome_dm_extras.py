"""Welcome DM extras (Moddy HQ only): Translate button + Website / Docs links."""

import asyncio
import json
import re
from types import SimpleNamespace

import discord
from discord import ui

from modules.welcome_dm import build_welcome_view
from utils.welcome_dm_views import (
    DOCS_URL, WEBSITE_URL, WELCOME_EXTRAS_GUILDS, WelcomeDmTranslateButton, welcome_text_of,
)

HQ = 1394001780148535387


class _Guild:
    def __init__(self, gid):
        self.id, self.name, self.member_count = gid, "Moddy HQ", 10
        self.icon = None


class _Member:
    id = 5
    mention = "<@5>"
    name = display_name = global_name = "Jules"
    display_avatar = SimpleNamespace(url="https://x/a.png")
    created_at = discord.utils.utcnow()


def _rows(view):
    return [c for c in view.children if isinstance(c, ui.ActionRow)]


def test_hq_welcome_dm_gets_translate_and_links():
    view, _ = build_welcome_view({"message": "Bienvenue !"}, _Member(), _Guild(HQ), "fr")
    assert isinstance(view.children[0], ui.Container)
    (row,) = _rows(view)
    items = list(row.children)
    assert items[0].custom_id == f"moddy:welcome_dm:translate:{HQ}"
    assert items[0].item.style is discord.ButtonStyle.primary
    assert [b.url for b in items[1:]] == [WEBSITE_URL, DOCS_URL]


def test_other_servers_unchanged():
    view, _ = build_welcome_view({"message": "Hi"}, _Member(), _Guild(42), "en-US")
    assert not _rows(view)
    assert 42 not in WELCOME_EXTRAS_GUILDS


def test_text_reread_from_the_dm_skips_the_attribution_line():
    msg = SimpleNamespace(components=[SimpleNamespace(children=[
        SimpleNamespace(content="Bienvenue sur **Moddy HQ** !"),
        SimpleNamespace(content="-# Sent by [**Moddy HQ**](x) (`1`)"),
    ])])
    assert welcome_text_of(msg) == "Bienvenue sur **Moddy HQ** !"
    assert welcome_text_of(None) == ""


def test_dynamic_item_round_trip():
    item = WelcomeDmTranslateButton(HQ)
    m = re.fullmatch(WelcomeDmTranslateButton.__discord_ui_compiled_template__,
                     item.item.custom_id)
    rebuilt = asyncio.run(WelcomeDmTranslateButton.from_custom_id(None, item.item, m))
    assert rebuilt.guild_id == HQ


def test_extras_strings_in_every_locale():
    for loc in ("fr", "en-US", "es-ES", "pt-BR", "de"):
        extras = json.load(open(f"locales/{loc}.json"))["modules"]["welcome_dm"]["extras"]
        assert set(extras) == {"translate", "website", "docs", "loading", "card_title"}
