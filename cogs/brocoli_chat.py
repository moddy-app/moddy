"""Brocoli in a Discord channel.

Brocoli is the backend's AI assistant for configuration and support. The
dashboard already talks to it; this cog gives it a second front end: a channel
where an administrator configures their server by writing sentences.

The bot is a **client**, not a second Brocoli. The agent loop, the tools, the
history and every write stay in ``website-backend`` — this cog carries messages
and renders events. Anything that looks like a decision here (which tools exist,
whether an action needs confirming, what a diff contains) is the backend's, on
purpose: two implementations of that logic would drift, and the one in Discord
would be the one nobody audits.

Where the guard rails are
-------------------------
- **Identity** is asserted per request and signed (``utils/brocoli_signature``).
  The bot says who is typing; the backend decides what that person may do.
- **Genre** is fixed backend-side to ``guild_config``: billing and authority
  tools are unreachable from a channel, even for staff.
- **Confirmation** is the backend's ``permission_request`` event rendered as a
  card, never a sentence Brocoli writes. A ``critical`` action is confirmed even
  in ``auto`` mode, and that is not ours to override.
- **Registration** is limited to ``BROCOLI_GUILD_IDS``. Empty = the cog does not
  load at all, so the command cannot leak to every server.

See ``docs/BROCOLI_CHANNEL.md``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import discord
from discord import app_commands, ui
from discord.ext import commands

from config import BOT_ASSERT_SECRET, BROCOLI_API_URL, BROCOLI_GUILD_IDS, COLORS
from services.brocoli_client import BrocoliClient, BrocoliError
from utils.brocoli_views import (
    answer_card,
    confirmation_card,
    decided_card,
    notice_card,
)
from utils.components_v2 import create_error_message
from utils.emojis import SETTINGS
from utils.guild_language import guild_locale
from utils.i18n import t

logger = logging.getLogger('moddy.brocoli')

# Where the channel id and the live conversation are remembered, under
# `guilds.data`. Survives restarts without a migration.
DATA_PATH = "brocoli"

# Default channel name. Deliberately close to `moddy-updates`, the channel the
# bot already creates on join, so the two read as one family.
CHANNEL_NAME = "moddy-chat"

# Minimum delay between two edits of the answer card while a turn streams.
# Discord rate-limits edits per channel; editing on every text_delta would burn
# the bucket and make the card lag further behind than this throttle does.
EDIT_INTERVAL = 1.5

# Longest message accepted from a member. The backend has its own limits; this
# one keeps an accidental paste out of the quota.
MAX_INPUT = 2000


class BrocoliChat(commands.Cog):
    """A channel where Brocoli configures the server on request."""

    def __init__(self, bot):
        self.bot = bot
        self.client = BrocoliClient(BROCOLI_API_URL, BOT_ASSERT_SECRET)
        # One turn at a time per channel. The backend answers 409 on a second
        # concurrent turn; holding the lock here turns that race into a queue
        # rather than an error the member has to read.
        self._locks: dict[int, asyncio.Lock] = {}

    async def cog_unload(self) -> None:
        await self.client.close()

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    async def _state(self, guild_id: int) -> dict:
        if not self.bot.db:
            return {}
        guild = await self.bot.db.get_guild(guild_id)
        return (guild.get("data") or {}).get(DATA_PATH) or {}

    async def _save(self, guild_id: int, state: dict) -> None:
        if self.bot.db:
            await self.bot.db.update_guild_data(guild_id, DATA_PATH, state)

    def _lock(self, channel_id: int) -> asyncio.Lock:
        return self._locks.setdefault(channel_id, asyncio.Lock())

    # ------------------------------------------------------------------
    # Command
    # ------------------------------------------------------------------

    @app_commands.command(
        name="brocoli",
        description="Open a channel where Moddy's assistant configures this server",
    )
    @app_commands.guilds(*[discord.Object(id=gid) for gid in BROCOLI_GUILD_IDS])
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def brocoli(self, interaction: discord.Interaction):
        """Create (or point to) the Brocoli channel."""
        locale = str(interaction.locale) if interaction.locale else "en-US"
        guild = interaction.guild

        state = await self._state(guild.id)
        existing = guild.get_channel(int(state.get("channel_id", 0) or 0))
        if existing is not None:
            await interaction.response.send_message(
                view=notice_card("exists", locale, channel=existing.mention),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Administrators only: the conversation genre is `guild_config`, which
        # the backend refuses to anyone without admin rights. A channel open to
        # everyone would be a wall of 403s.
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, embed_links=True
            ),
        }

        try:
            channel = await guild.create_text_channel(
                CHANNEL_NAME,
                overwrites=overwrites,
                topic=t("brocoli.channel_topic", locale=locale),
                reason=t("brocoli.channel_reason", locale=locale),
            )
        except discord.Forbidden:
            await interaction.followup.send(
                view=create_error_message(
                    t("brocoli.error.no_permission_title", locale=locale),
                    t("brocoli.error.no_permission", locale=locale),
                ),
                ephemeral=True,
            )
            return

        await self._save(guild.id, {"channel_id": str(channel.id)})
        await channel.send(view=self._welcome_card(await guild_locale(self.bot, guild)))
        await interaction.followup.send(
            view=notice_card("created", locale, channel=channel.mention),
            ephemeral=True,
        )

    def _welcome_card(self, locale: str) -> ui.LayoutView:
        from cogs.error_handler import BaseView

        view = BaseView()
        container = ui.Container(accent_colour=COLORS["primary"])
        container.add_item(
            ui.TextDisplay(f"### {SETTINGS} {t('brocoli.welcome.title', locale=locale)}")
        )
        container.add_item(ui.TextDisplay(t("brocoli.welcome.body", locale=locale)))
        container.add_item(
            ui.TextDisplay(f"-# {t('brocoli.welcome.footer', locale=locale)}")
        )
        view.add_item(container)
        return view

    # ------------------------------------------------------------------
    # Conversation
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if message.guild.id not in BROCOLI_GUILD_IDS:
            return

        state = await self._state(message.guild.id)
        if str(message.channel.id) != str(state.get("channel_id") or ""):
            return
        if not message.content or message.content.startswith(("//", "#")):
            # An easy way to talk in the channel without paying for a turn.
            return

        locale = await guild_locale(self.bot, message.guild)

        lock = self._lock(message.channel.id)
        if lock.locked():
            await message.reply(view=notice_card("busy", locale), silent=True)
            return

        async with lock:
            await self._run_turn(message, state, locale)

    async def _run_turn(self, message: discord.Message, state: dict, locale: str) -> None:
        guild_id = message.guild.id
        user_id = message.author.id

        try:
            conversation_id = await self._conversation(state, guild_id, user_id)
        except BrocoliError as exc:
            await message.reply(view=notice_card(exc.code, locale), silent=True)
            return

        async with message.channel.typing():
            try:
                stream = self.client.send_message(
                    conversation_id, user_id, guild_id, message.content[:MAX_INPUT]
                )
                await self._render(stream, message.channel, conversation_id, locale)
            except BrocoliError as exc:
                logger.warning("[Brocoli] turn failed (%s): %s", exc.code, exc.detail)
                await message.reply(view=notice_card(exc.code, locale), silent=True)

    async def _conversation(self, state: dict, guild_id: int, user_id: int) -> str:
        """The live conversation for this channel, opening one if needed.

        One conversation per channel, not per member: the channel is a single
        thread of work on a single server, and splitting it per person would
        make Brocoli forget what was just configured.
        """
        conversation_id = state.get("conversation_id")
        if conversation_id:
            return conversation_id

        conversation = await self.client.open_conversation(user_id, guild_id)
        conversation_id = conversation["id"]
        state["conversation_id"] = conversation_id
        await self._save(guild_id, state)
        return conversation_id

    async def _render(
        self,
        stream,
        channel: discord.abc.Messageable,
        conversation_id: str,
        locale: str,
    ) -> None:
        """Turn the event stream into one message that updates in place.

        A Discord message cannot stream token by token — editing on every
        ``text_delta`` would hit the per-channel edit bucket in seconds. The
        card is therefore edited on a throttle and finalised once, which is
        also what makes the "thinking" state readable rather than flickering.
        """
        card: Optional[discord.Message] = None
        buffer: list[str] = []
        tool: Optional[str] = None
        last_edit = 0.0

        async def paint(*, thinking: bool, force: bool = False) -> None:
            nonlocal card, last_edit
            now = time.monotonic()
            if not force and now - last_edit < EDIT_INTERVAL:
                return
            last_edit = now
            view = answer_card(
                "".join(buffer), locale=locale, thinking=thinking, tool=tool
            )
            if card is None:
                card = await channel.send(view=view)
            else:
                await card.edit(view=view)

        async for event in stream:
            name, data = event.get("event"), event.get("data") or {}

            if name == "text_delta":
                buffer.append(data.get("delta", ""))
                await paint(thinking=True)

            elif name == "tool_call":
                tool = _tool_label(data.get("name", ""), locale)
                await paint(thinking=True)

            elif name == "tool_result":
                tool = None

            elif name == "permission_request":
                # Finalise whatever Brocoli said first, then ask separately: the
                # question must be a card with buttons, never a sentence in the
                # middle of a paragraph.
                await paint(thinking=False, force=True)
                await channel.send(
                    view=confirmation_card(data, conversation_id, locale=locale)
                )

            elif name == "error":
                logger.warning("[Brocoli] stream error: %s", data.get("code"))
                await channel.send(view=notice_card("unavailable", locale))

            elif name == "run_end":
                tool = None
                if data.get("status") == "max_iterations":
                    buffer.append("\n\n" + t("brocoli.max_iterations", locale=locale))
                if buffer or card is not None:
                    await paint(thinking=False, force=True)

    # ------------------------------------------------------------------
    # Confirmation
    # ------------------------------------------------------------------

    async def handle_decision(
        self,
        interaction: discord.Interaction,
        *,
        conversation_id: str,
        action_id: str,
        approve: bool,
    ) -> None:
        """Send a decision and stream the resumed turn.

        Called from :class:`utils.brocoli_views.DecisionButton`, which
        reconstructs itself from the ``custom_id`` on every click — so nothing
        here may rely on state that was in memory when the card was posted.
        """
        # The cards land in the channel, where the whole server reads them, so
        # they follow the server language rather than the clicker's client.
        locale = await guild_locale(self.bot, interaction.guild)
        await interaction.response.defer()

        try:
            stream = self.client.decide(
                conversation_id,
                action_id,
                interaction.user.id,
                interaction.guild_id,
                approve,
            )
            # Replace the question with its outcome before the answer arrives:
            # leaving live buttons under a decision already taken invites a
            # second click that can only fail.
            await interaction.message.edit(
                view=decided_card(approve, "", locale)
            )
            await self._render(stream, interaction.channel, conversation_id, locale)
        except BrocoliError as exc:
            logger.warning("[Brocoli] decision failed (%s): %s", exc.code, exc.detail)
            await interaction.followup.send(
                view=notice_card(exc.code, locale), ephemeral=True
            )


# Tools we have a human sentence for. Anything outside this set — a tool added
# backend-side after this cog shipped — falls back to a generic line rather than
# rendering `[brocoli.tools.x]`: i18n returns the key when it is missing, and a
# bracketed key in a member's channel reads as a bug.
_NAMED_TOOLS = frozenset({
    "search_documentation", "read_documentation_page", "read_internal_guide",
    "get_guild_overview", "list_channels", "list_roles", "lookup_member",
    "get_bot_capabilities", "list_modules", "list_module_catalogue",
    "get_module_config", "describe_module_config", "get_module_schema",
    "validate_module_config", "get_guild_language", "set_module_config",
    "disable_module", "set_guild_language",
})


def _tool_label(name: str, locale: str) -> str:
    """What Brocoli is doing right now, in the member's language."""
    key = f"brocoli.tools.{name}" if name in _NAMED_TOOLS else "brocoli.tools.generic"
    return t(key, locale=locale)


async def setup(bot):
    if not BROCOLI_GUILD_IDS:
        # No allowlist, no command. Loading the cog with an empty
        # `app_commands.guilds()` would register `/brocoli` globally — on every
        # server Moddy is in — which is the opposite of the intent.
        logger.info("[Brocoli] BROCOLI_GUILD_IDS is empty, channel feature disabled")
        return
    await bot.add_cog(BrocoliChat(bot))
