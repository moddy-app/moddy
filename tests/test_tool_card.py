"""Shared tool card (utils/tool_card.py) and the public API client basics."""

import types

import discord
import pytest

from utils import tool_card as tc
from utils.i18n import i18n

LOCALES = ("fr", "en-US", "es-ES", "pt-BR", "de")


def _texts(view):
    return [c.content for c in view.walk_children() if isinstance(c, discord.ui.TextDisplay)]


def test_heading_drops_empty_icon():
    assert tc.heading("", "Hash") == "### Hash"
    assert tc.heading("<:tag:1>", "Hash") == "### <:tag:1> Hash"
    assert tc.button_emoji("") is None
    assert tc.button_emoji("<:tag:1519800727231266858>").id == 1519800727231266858


def test_card_anatomy_and_footer():
    card = tc.ToolCard(title="Hash", icon="")
    card.code("abc").details([("SHA-256", "ff"), None, "free line"])
    card.footer(tc.source_line("en-US", ("", "Have I Been Pwned"), "Pwned Passwords", verb="checked_by"), None, "x")
    view = card.build()
    texts = _texts(view)
    assert texts[0] == "### Hash"
    assert texts[1].startswith("```\nabc")
    assert "**SHA-256** · `ff`" in texts[2] and "free line" in texts[2]
    assert texts[-1] == "-# Checked by **Have I Been Pwned** & **Pwned Passwords** · x"


def test_code_block_and_inline_escaping():
    assert "```" not in tc.code_block("a```b")[3:-3]
    assert tc.inline("a`b") == "`aˋb`"
    assert tc.inline("") == "`—`"


def test_truncate_and_fit_or_attach():
    text, cut = tc.truncate("x" * 50, 10)
    assert cut and len(text) == 10
    block, file = tc.fit_or_attach("short", limit=100, filename="a.txt")
    assert file is None
    block, file = tc.fit_or_attach("y" * 500, limit=100, filename="a.txt")
    assert file is not None and file.filename == "a.txt"


def test_number_and_size_formats():
    assert tc.format_number(12345, "en-US") == "12,345"
    assert tc.format_number(12345, "fr") == "12 345"
    assert tc.format_number(12345, "de") == "12.345"
    assert tc.format_bytes(2048, "en-US") == "2.0 KB"
    assert tc.format_bytes(2048, "fr") == "2,0 KB"
    assert tc.progress_bar(6, 12) == "██████░░░░░░"


def test_error_card_falls_back_to_common():
    view = tc.tool_error_card(tc.ToolError("timeout", tool="nonexistent"), "fr")
    texts = "\n".join(_texts(view))
    assert i18n.get("tools.common.errors.timeout", locale="fr") in texts
    unknown = "\n".join(_texts(tc.tool_error_card("no_such_code", "en-US")))
    assert i18n.get("tools.common.errors.failed", locale="en-US") in unknown


@pytest.mark.parametrize("locale", LOCALES)
def test_common_keys_exist_in_every_locale(locale):
    en = i18n._translations["en-US"]["tools"]["common"]
    loc = i18n._translations[locale]["tools"]["common"]

    def keys(d, prefix=""):
        out = set()
        for k, v in d.items():
            out |= keys(v, f"{prefix}{k}.") if isinstance(v, dict) else {prefix + k}
        return out

    assert keys(en) == keys(loc)


async def test_state_roundtrip_without_redis():
    bot = types.SimpleNamespace(redis=None)
    token = await tc.save_state(bot, "regex", {"p": "a+"})
    assert len(token) == 10
    assert await tc.load_state(bot, "regex", token) == {"p": "a+"}
    assert await tc.load_state(bot, "regex", "missing123") is None


def test_public_api_cache_key_is_stable():
    from services.public_api import PublicAPI
    a = PublicAPI.cache_key("ns", "https://x", {"q": 1})
    assert a == PublicAPI.cache_key("ns", "https://x", {"q": 1})
    assert a != PublicAPI.cache_key("ns", "https://x", {"q": 2})
