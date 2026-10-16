"""Expected errors: card builder, exceptions, mapping, handler routing, i18n."""
import asyncio
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands
from discord.ext import commands

from utils import errors as E
from utils.components_v2 import create_error_message, create_warning_message

try:  # the error handler imports sentry_sdk at module load; stub it when absent
    import sentry_sdk  # noqa: F401
except ImportError:
    sys.modules["sentry_sdk"] = types.ModuleType("sentry_sdk")

LOCALES = ["fr", "en-US", "es-ES", "pt-BR", "de"]


def _texts(view):
    out = []

    def walk(item):
        if isinstance(item, discord.ui.TextDisplay):
            out.append(item.content)
        for child in getattr(item, "children", []) or []:
            walk(child)

    for item in view.children:
        walk(item)
    return out


def _container(view):
    return next(c for c in view.children if isinstance(c, discord.ui.Container))


def test_severity_defaults_and_never_green():
    for sev in E.Severity:
        view = E.build_error_card("T", "D", severity=sev)
        assert _container(view).accent_colour.value != 0x57F287
    assert _container(E.build_error_card("T", "D", severity="info")).accent_colour.value == 0x5865F2


def test_custom_icon_replaces_default_never_two():
    view = E.build_error_card("Title", "Desc", severity="warning", icon="<:x:1>")
    title = _texts(view)[0]
    assert title == "### <:x:1> Title"
    assert E.EMOJIS["warning"] not in title
    assert _texts(E.build_error_card("Title", "D", icon=""))[0] == "### Title"


def test_card_structure_fields_hint_buttons():
    btn = discord.ui.Button(label="Docs", url="https://moddy.app", style=discord.ButtonStyle.link)
    view = E.build_error_card("T", "D", hint="h", fields=[{"name": "n", "value": "v"}],
                              accent=0x123456, buttons=[btn])
    texts = _texts(view)
    assert texts[0].startswith("### ") and "**n**\nv" in texts and "-# h" in texts
    assert _container(view).accent_colour.value == 0x123456
    assert any(isinstance(c, discord.ui.ActionRow) for c in view.children)


def test_legacy_helpers_delegate():
    err = create_error_message("Oops", "Bad", fields=[{"name": "a", "value": "b"}])
    assert _texts(err)[0] == f"### {E.EMOJIS['error']} Oops"
    warn = create_warning_message("Careful", "Hmm")
    assert _texts(warn)[0] == f"### {E.EMOJIS['warning']} Careful"
    assert _container(warn).accent_colour.value != _container(err).accent_colour.value


@pytest.mark.parametrize("locale", LOCALES)
def test_every_exception_renders_in_every_locale(locale):
    errs = [
        E.ModdyError(), E.MissingPermissions(["manage_roles"]),
        E.BotMissingPermissions(["ban_members"]), E.InvalidInput(), E.InvalidInput("duration"),
        E.NotFound(), E.NotFound("that role"), E.Cooldown(75), E.ModuleDisabled(),
        E.ModuleDisabled("Tickets"), E.PremiumRequired(), E.PremiumRequired("Export"),
        E.NotAllowed(), E.NotYours(), E.GuildOnly(), E.Unavailable(), E.MissingArgument("user"),
        E.ModdyError(key="unresolved_argument", value="x"),
    ]
    for err in errs:
        texts = _texts(err.render(locale))
        assert len(texts) >= 2
        assert not any(t.startswith("[") or "[errors." in t for t in texts), (err, texts)
        assert "{" not in "".join(texts)
        assert len(texts[0].split()) <= 8  # icon + short title


def test_locales_have_same_expected_keys():
    keysets = []
    for loc in LOCALES:
        data = json.loads((Path(__file__).parent.parent / "locales" / f"{loc}.json").read_text("utf-8"))
        keysets.append({k: set(v) for k, v in data["errors"]["expected"].items()})
    assert all(k == keysets[0] for k in keysets)


def test_format_retry():
    assert E.format_retry(0.2) == "1s" and E.format_retry(75) == "1m 15s" and E.format_retry(3600) == "1h"


def test_mapping_of_builtin_errors():
    mp = E.to_expected_error
    assert isinstance(mp(app_commands.MissingPermissions(["ban_members"])), E.MissingPermissions)
    assert isinstance(mp(app_commands.BotMissingPermissions(["ban_members"])), E.BotMissingPermissions)
    assert isinstance(mp(commands.MissingPermissions(["ban_members"])), E.MissingPermissions)
    cd = app_commands.Cooldown(1, 10)
    assert isinstance(mp(app_commands.CommandOnCooldown(cd, 4.2)), E.Cooldown)
    assert isinstance(mp(app_commands.NoPrivateMessage()), E.GuildOnly)
    assert isinstance(mp(app_commands.CheckFailure()), E.NotAllowed)
    assert mp(app_commands.CheckFailure()).only_if_unanswered
    assert mp(ValueError("boom")) is None
    wrapped = app_commands.CommandInvokeError(MagicMock(), E.NotFound())
    assert isinstance(mp(wrapped), E.NotFound)


def _interaction(done=False):
    it = MagicMock(spec=discord.Interaction)
    it.response = MagicMock()
    it.response.is_done.return_value = done
    it.response.send_message = AsyncMock()
    it.followup = MagicMock()
    it.followup.send = AsyncMock()
    it.created_at = discord.utils.utcnow()
    it.locale = "en-US"
    it.user = SimpleNamespace(id=1)
    it.id = 5
    it.client = None
    it.extras = {}
    return it


def test_handler_renders_expected_without_reporting(monkeypatch):
    asyncio.run(_test_handler_renders_expected_without_reporting(monkeypatch))


async def _test_handler_renders_expected_without_reporting(monkeypatch):
    from cogs import error_handler as eh
    sentry = MagicMock()
    monkeypatch.setattr(eh, "capture_error_to_sentry", sentry)
    it = _interaction()
    code = await eh.report_interaction_error(
        it, E.NotFound("that role"), command="x", error_type="UI Error")
    assert code is None
    sentry.assert_not_called()
    kwargs = it.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True and "that role" in " ".join(_texts(kwargs["view"]))


def test_handler_uses_followup_when_already_responded():
    asyncio.run(_test_handler_uses_followup_when_already_responded())


async def _test_handler_uses_followup_when_already_responded():
    from cogs import error_handler as eh
    it = _interaction(done=True)
    await eh.report_expected_error(it, E.Cooldown(3))
    it.response.send_message.assert_not_awaited()
    it.followup.send.assert_awaited()


def test_failed_check_after_reply_stays_silent():
    asyncio.run(_test_failed_check_after_reply_stays_silent())


async def _test_failed_check_after_reply_stays_silent():
    from cogs import error_handler as eh
    it = _interaction(done=True)
    assert await eh.report_expected_error(it, app_commands.CheckFailure()) == "silent"
    it.followup.send.assert_not_awaited()


def test_unexpected_error_still_takes_the_full_path(monkeypatch):
    asyncio.run(_test_unexpected_error_still_takes_the_full_path(monkeypatch))


async def _test_unexpected_error_still_takes_the_full_path(monkeypatch):
    from cogs import error_handler as eh
    sentry = MagicMock(return_value=None)
    monkeypatch.setattr(eh, "capture_error_to_sentry", sentry)
    it = _interaction()
    code = await eh.report_interaction_error(
        it, ValueError("boom"), command="x", error_type="UI Error")
    assert code == "UNTRACKED"
    sentry.assert_called_once()
