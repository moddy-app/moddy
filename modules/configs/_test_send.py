"""
"Send a test" button shared by every module whose main job is to post a message.

A server that sets up a welcome message, a welcome DM, a bump reminder or a
social notification wants to see it for real before a member joins, a directory
cools down or a creator posts. This button sends the configured message once,
right now, with the person who clicked standing in for the member / bumper /
post author.

Where it appears:
  - on the confirmation message shown right after an entry is created;
  - on the screen that manages an existing entry.

One ``DynamicItem`` serves every module: the custom_id carries the module id and
the entry's own id (``moddy:cfg:test:<module>:<ref>``), so the button keeps
working after a restart and on an ephemeral confirmation that no panel tracks.
The sending itself lives next to each module's real rendering code
(``send_test`` in the module file), registered in :data:`_SENDERS` and imported
lazily, so this file stays light and a mini-bot never imports a module it does
not run.

Guard rails, identical for every module:
  - Manage Server (or the staff ``t.config`` key), re-checked on every click;
  - one test per entry every :data:`COOLDOWN` seconds, so the button cannot be
    used to flood a channel or someone's DMs;
  - a discreet line under the message says it is a test and who sent it;
  - tests never notify a role or ``@everyone``: role tags are rendered so the
    layout is exact, but ``allowed_mentions`` drops them. Only the tester can be
    pinged.
"""

from __future__ import annotations

import importlib
import logging
import re
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import discord
from discord import ui

from cogs.error_handler import BaseView
from modules.configs._common import ack_thinking, check_guild_perms
from utils.emojis import DONE, SEND, WARNING
from utils.i18n import i18n, t

logger = logging.getLogger('moddy.modules.test_send')

_CID_PREFIX = "moddy:cfg:test"

#: Seconds between two tests of the same entry.
COOLDOWN = 10.0

#: module id -> "python.module:function". Each function has the signature
#: ``async def send_test(bot, guild, member, ref, locale) -> TestResult``.
_SENDERS: Dict[str, str] = {
    "welcome_channel": "modules.welcome_channel:send_test",
    "welcome_dm": "modules.welcome_dm:send_test",
    "bump_reminder": "modules.bump_reminder:send_test",
    "social_notifications": "modules.social_notifications:send_test",
}

# (guild_id, module_id, ref) -> monotonic time of the last test.
_last_test: Dict[Tuple[int, str, str], float] = {}


# --------------------------------------------------------------------------- #
# Result + shared rendering helper (used by the module senders)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TestResult:
    """What happened to one test.

    ``status`` is one of ``sent_channel``, ``sent_dm``, ``not_found``,
    ``channel_missing``, ``no_permission``, ``dm_closed``, ``failed`` — each
    one an i18n key under ``modules.config.test.result``.
    """

    __test__ = False  # not a pytest test class, despite the name

    status: str
    channel_id: Optional[int] = None


def add_test_marker(view: ui.LayoutView, user: discord.abc.User, locale: str) -> None:
    """Append the "this is a test" line under a rendered message.

    Outside the module's own container so the message itself stays exactly
    what members will get.
    """
    view.add_item(ui.TextDisplay(
        f"-# {t('modules.config.test.marker', locale=locale, user=user.mention)}"
    ))


def supports_test(module_id: str) -> bool:
    return module_id in _SENDERS


# --------------------------------------------------------------------------- #
# The button
# --------------------------------------------------------------------------- #
class TestSendButton(
    ui.DynamicItem[ui.Button],
    template=rf"{_CID_PREFIX}:(?P<module>[a-z0-9_]{{1,40}}):(?P<ref>[A-Za-z0-9_\-]{{1,40}})",
):
    """Sends one entry of a module as a test.

    Auth: Manage Server, re-checked on every click. The guild comes from the
    interaction; the module and the entry from the custom_id, and the entry is
    re-read from the database on every click (a deleted entry answers
    "not found" instead of sending something stale).
    """

    __test__ = False  # not a pytest test class, despite the name

    def __init__(self, module_id: str, ref: str, *, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=t('modules.config.test.button', locale=locale),
            emoji=discord.PartialEmoji.from_str(SEND),
            style=discord.ButtonStyle.secondary,
            custom_id=f"{_CID_PREFIX}:{module_id}:{ref}",
        ))
        self.module_id = module_id
        self.ref = ref

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button,
                             match: "re.Match"):
        return cls(match["module"], match["ref"])

    async def callback(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return
        locale = i18n.get_user_locale(interaction)

        # Cooldown first: answering it costs nothing and needs no defer.
        key = (interaction.guild_id or 0, self.module_id, self.ref)
        now = time.monotonic()
        last = _last_test.get(key)
        if last is not None and now - last < COOLDOWN:
            retry = int(COOLDOWN - (now - last)) + 1
            await interaction.response.send_message(
                f"{WARNING} {t('modules.config.test.cooldown', locale=locale, seconds=retry)}",
                ephemeral=True,
            )
            return

        # A new ephemeral answer rather than an edit: the panel (or the
        # confirmation) the button sits on must stay as it is.
        await ack_thinking(interaction)

        _last_test[key] = now
        if len(_last_test) > 5000:
            cutoff = now - COOLDOWN
            for k in [k for k, v in _last_test.items() if v < cutoff]:
                _last_test.pop(k, None)

        result = await self._send(interaction)
        await interaction.followup.send(_result_text(result, locale), ephemeral=True)

    async def _send(self, interaction: discord.Interaction) -> TestResult:
        bot = interaction.client
        target = _SENDERS.get(self.module_id)
        guild = bot.get_guild(interaction.guild_id) if interaction.guild_id else None
        if target is None or guild is None:
            return TestResult("not_found")

        # The tester stands in for the member. A staffer working through
        # t.config may not be a member of that server: their user object still
        # carries everything a placeholder needs.
        member = guild.get_member(interaction.user.id) or interaction.user

        from utils.guild_language import guild_locale
        try:
            server_locale = await guild_locale(bot, guild)
        except Exception:  # noqa: BLE001 - a language lookup never blocks a test
            server_locale = "en-US"

        module_path, _, func_name = target.partition(":")
        try:
            sender = getattr(importlib.import_module(module_path), func_name)
            return await sender(bot, guild, member, self.ref, server_locale)
        except Exception as e:  # noqa: BLE001 - report, never crash the button
            logger.error(
                f"Test send failed for {self.module_id} entry {self.ref} "
                f"(guild {guild.id}): {e}", exc_info=True)
            return TestResult("failed")


def _result_text(result: TestResult, locale: str) -> str:
    channel = f"<#{result.channel_id}>" if result.channel_id else ""
    text = t(f"modules.config.test.result.{result.status}", locale=locale, channel=channel)
    icon = DONE if result.status in ("sent_channel", "sent_dm") else WARNING
    return f"{icon} {text}"


def test_button_row(module_id: str, ref: Optional[str], locale: str) -> Optional[ui.ActionRow]:
    """An action row holding just the test button, or ``None`` without an entry."""
    if not ref or not supports_test(module_id):
        return None
    return ui.ActionRow(TestSendButton(module_id, str(ref), locale=locale))


def confirmation_view(module_id: str, ref: Optional[str], locale: str, text: str,
                      *, accent: Optional[int] = None) -> ui.LayoutView:
    """The ephemeral "created" confirmation, with the test button under it.

    A plain :class:`ui.LayoutView` — its only interactive child is the
    persistent :class:`TestSendButton`, registered by :class:`TestSendPersistence`.
    """
    view = ui.LayoutView(timeout=None)
    container = ui.Container(
        accent_colour=discord.Colour(accent) if accent is not None else None)
    # Some success strings already open with the icon: never show it twice.
    container.add_item(ui.TextDisplay(text if text.startswith(DONE) else f"{DONE} {text}"))
    row = test_button_row(module_id, ref, locale)
    if row is not None:
        container.add_item(ui.TextDisplay(
            f"-# {t('modules.config.test.hint', locale=locale)}"))
        container.add_item(row)
    view.add_item(container)
    return view


class TestSendPersistence(BaseView):
    """Marker view: registers :class:`TestSendButton` at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(TestSendButton)
