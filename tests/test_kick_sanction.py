"""Kick as a first-class case sanction: enum, source, labels, DM, audit sync."""

from __future__ import annotations

import types

import discord
import pytest

from cogs.case_sync import CaseSync
from services.case_service import SOURCES
from utils import emojis
from utils.appeal_views import NON_APPEALABLE_ACTIONS, build_sanction_dm_view
from utils.cases_views import SANCTION_PERMISSION
from utils.moderation_cases import (
    TEMPORARY_ACTIONS, CaseType, SanctionAction, get_action_emoji,
    get_available_actions,
)
from services.expiration_notifier import NOTIFIABLE_ACTIONS
from utils.i18n import t

U = "00000000-0000-0000-0000-000000000000"
LOCALES = ("fr", "en-US", "es-ES", "pt-BR", "de")


def test_kick_is_a_guild_sanction_only():
    assert SanctionAction("kick") is SanctionAction.KICK
    assert SanctionAction.KICK in SOURCES["guild"].actions
    assert SanctionAction.KICK in get_available_actions(CaseType.GUILD)
    assert SanctionAction.KICK not in get_available_actions(CaseType.GLOBAL)
    assert SOURCES["global"].actions and SanctionAction.KICK not in SOURCES["global"].actions


def test_kick_never_expires_and_needs_kick_members():
    assert SanctionAction.KICK not in TEMPORARY_ACTIONS
    assert "kick" not in NOTIFIABLE_ACTIONS
    assert SANCTION_PERMISSION[SanctionAction.KICK] == "kick_members"


def test_kick_icon_and_accent():
    assert get_action_emoji(SanctionAction.KICK) == emojis.LOGOUT
    assert emojis.get_sanction_dm_emoji("kick") == emojis.LOGOUT
    assert emojis.get_sanction_accent("kick") == emojis.get_sanction_accent("ban")


@pytest.mark.parametrize("locale", LOCALES)
def test_kick_label_in_every_locale(locale):
    for key in ("commands.cases.action.kick", "staff.mod.case.action.kick"):
        assert not t(key, locale=locale).startswith("["), (key, locale)


def test_kick_dm_has_no_appeal_buttons_nor_expiry():
    assert "kick" in NON_APPEALABLE_ACTIONS
    kw = dict(locale="en-US", guild_name="G", case_ref="ABC123", reason="r",
              explication="e", case_id=U, sanction_id=U, guild_id=1)
    kick, _ = build_sanction_dm_view(action="kick", **kw)
    ban, _ = build_sanction_dm_view(action="ban", **kw)
    assert not any(isinstance(i, discord.ui.ActionRow) for i in kick.children)
    assert any(isinstance(i, discord.ui.ActionRow) for i in ban.children)
    expires = t("modules.automod_ai.dm.expires", locale="en-US")

    def text(v):
        return " ".join(getattr(i, "content", "") or "" for i in v.walk_children())
    assert expires not in text(kick) and expires in text(ban)


class _Cases:
    def __init__(self):
        self.recorded = []

    async def record_sanction(self, source, **kw):
        self.recorded.append((source, kw))


@pytest.mark.asyncio
async def test_case_sync_records_a_manual_kick_but_not_ours():
    bot = types.SimpleNamespace(cases=_Cases(), _moddy_initiated_sanctions={})
    cog = CaseSync(bot)
    guild = types.SimpleNamespace(id=9)
    target = types.SimpleNamespace(id=42, bot=False)
    entry = types.SimpleNamespace(
        guild=guild, action=discord.AuditLogAction.kick, target=target,
        user=types.SimpleNamespace(id=1, bot=False), reason="spam")
    await cog.on_audit_log_entry_create(entry)
    assert [k["action"] for _, k in bot.cases.recorded] == ["kick"]

    bot.cases.recorded.clear()
    cog._is_moddy_initiated = lambda *a: True
    await cog.on_audit_log_entry_create(entry)
    assert bot.cases.recorded == []
