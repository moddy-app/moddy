"""
AltGuard — Moddy's anti multi-account gate as its own bot.

It runs Moddy's ``altguard`` module unchanged (same panel, same consent modal,
same ``/config`` screen, same tables, same service contract). What this file
adds is the wiring Moddy does elsewhere (``cogs/module_events.py``,
``bot._listen_pubsub``), adapted to a client that caches no member:

1. **Joins** are handed to the module first (the gate role is applied), then
   reported to the service (``altguard:membership``), like on Moddy.
2. **Departures** arrive as ``on_raw_member_remove``: discord.py only fires
   ``on_member_remove`` for a cached member, and this client caches none.
3. **Verdicts**: the bot listens to ``altguard:verdict`` itself. Moddy hears
   the same messages; each process only acts where it owns the module
   (``guilds.data.modules.altguard.app``), and the module check comes before
   the idempotency insert, so a verdict is never swallowed by the wrong one.
4. **Auto Role** stays a Moddy module. When the gate opens here, Moddy is told
   on ``minibots:moddy`` (``modules/altguard.py::_run_auto_role``), and Moddy's
   Auto Role holds its roles back meanwhile (``modules/auto_role.py``).

See docs/features/MINIBOTS.md §11 and docs/features/ALTGUARD.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import discord
from discord.ext import commands

from cogs.altguard import AltGuard
from services.altguard_client import VERDICT_CHANNEL, AltGuardClient
from utils.background import spawn

logger = logging.getLogger("moddy.minibots.altguard")

MODULE_ID = "altguard"

#: Reconnection backoff of the verdict subscription (seconds).
VERDICT_BACKOFF_START = 5.0
VERDICT_BACKOFF_MAX = 60.0


def decode_verdict_message(raw) -> Optional[dict]:
    """A Pub/Sub payload as a dict, or None when it is not JSON (never raises)."""
    try:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode()
        data = json.loads(raw)
    except (TypeError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


class AltGuardBotCog(AltGuard, name="AltGuard"):
    """Moddy's AltGuard cog, plus the joins, departures and verdicts Moddy
    wires elsewhere. Keeps the cog name ``AltGuard``: the module reaches the
    invite tracker and the membership resync through ``get_cog("AltGuard")``."""

    def __init__(self, bot):
        super().__init__(bot)
        self._verdict_task: Optional[asyncio.Task] = None

    async def cog_load(self) -> None:
        # Created from the client's own task, so the verdicts it applies send
        # with this bot's emoji ids and name (minibots/emojis.py, branding.py).
        self._verdict_task = spawn(self._listen_verdicts(), name="minibots:altguard-verdicts")

    async def cog_unload(self):
        await super().cog_unload()
        if self._verdict_task is not None:
            self._verdict_task.cancel()
        client = getattr(self.bot, "altguard", None)
        if client is not None:
            await client.close()

    # ------------------------------------------------------------ verdicts

    async def _listen_verdicts(self) -> None:
        """Subscribe to ``altguard:verdict`` for as long as the client lives.

        Waits for the gateway first: a verdict applied before the guild cache
        exists would be recorded as seen and then fail to find the member.
        """
        await self.bot.wait_until_ready()
        delay = VERDICT_BACKOFF_START
        while not self.bot.is_closed():
            redis = getattr(self.bot, "redis", None)
            if redis is None:
                # Redis down at boot: the runtime restarts every client once
                # it is back (minibots/runtime.py), with a fresh subscription.
                logger.warning("AltGuard bot: no Redis, verdicts cannot be received")
                return
            pubsub = None
            try:
                pubsub = redis.pubsub()
                await pubsub.subscribe(VERDICT_CHANNEL)
                logger.info("AltGuard bot: subscribed to %s", VERDICT_CHANNEL)
                delay = VERDICT_BACKOFF_START
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    data = decode_verdict_message(message.get("data"))
                    if data is None:
                        logger.warning("AltGuard bot: verdict is not JSON — ignored")
                        continue
                    # One slow verdict (role calls, DM, retry) never holds
                    # the next ones back.
                    spawn(self._handle(data), name="minibots:altguard-verdict")
                logger.warning("AltGuard bot: verdict stream ended, resubscribing")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — the subscription outlives errors
                logger.error("AltGuard bot: verdict subscription error: %s", exc)
            finally:
                if pubsub is not None:
                    try:
                        await pubsub.aclose()
                    except Exception:  # noqa: BLE001
                        pass
            await asyncio.sleep(delay)
            delay = min(delay * 2, VERDICT_BACKOFF_MAX)

    async def _handle(self, data: dict) -> None:
        try:
            await self.handle_verdict(data)
        except Exception as exc:  # noqa: BLE001
            logger.error("AltGuard bot: verdict handling failed: %s", exc, exc_info=True)

    # -------------------------------------------------------- membership

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Gate first (what Moddy's module_events does), then report."""
        if member.bot:
            return
        module = await self._module(member.guild.id)
        if module is None:
            return
        try:
            await module.on_member_join(member)
        except Exception as exc:  # noqa: BLE001 — the report below still goes out
            logger.error("AltGuard bot: gate failed for %s in guild %s: %s",
                         member.id, member.guild.id, exc, exc_info=True)
        await AltGuard.on_member_join(self, member)

    @commands.Cog.listener()
    async def on_raw_member_remove(self, payload: discord.RawMemberRemoveEvent):
        """Departures without a member cache (``on_member_remove`` never fires)."""
        user = payload.user
        if isinstance(user, discord.Member):
            return  # it was cached after all: on_member_remove reported it
        if getattr(user, "bot", False):
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        state, occurred_at = await self._classify_departure(guild, user.id)
        if state is None:
            return
        await self._publish(guild.id, user.id, state, occurred_at)


# --------------------------------------------------------------------------- #
# Welcome DM
# --------------------------------------------------------------------------- #

async def build_welcome(client, guild: discord.Guild, locale: str):
    """``(content, variables, view)`` of the DM sent to whoever added the bot.

    The gate needs three things before it can run, and the one people miss is
    the role order: the bot's role must sit above both gate roles. The DM says
    so up front instead of letting the first save fail.
    """
    from config import MODDY_INVITE_URL, SUPPORT_URL, command_label
    from minibots.builtin import client_accent
    from notifications.models import NotificationContent
    # The bot's shield (as in its /config; Moddy shows the fingerprint). The
    # animated AltGuard face is kept for the panel and what the member reads.
    from utils.emojis import SHIELD
    from utils.i18n import t

    stored = await client.module_manager.get_module_config(guild.id, MODULE_ID)
    body_key = "body" if client.module_manager.owns(stored) else "elsewhere"
    variables = {"name": client.spec.name, "server": guild.name,
                 "config": command_label("config")}
    content = NotificationContent(
        title=t("minibots.bots.altguard.welcome.title", locale=locale, name="{name}"),
        body=t(f"minibots.bots.altguard.welcome.{body_key}", locale=locale,
               name="{name}", server="{server}", config="{config}"),
        icon=SHIELD, accent_color=client_accent(client),
        links=[{"label": t("minibots.links.get_moddy", locale=locale), "url": MODDY_INVITE_URL},
               {"label": t("modules.config.main.links.support", locale=locale), "url": SUPPORT_URL}],
        template_id=f"minibots.altguard.welcome.{body_key}",
    )
    return content, variables, None


async def setup(client) -> None:
    # What the panel's consent modal and the cog call (HTTP + Redis).
    client.altguard = AltGuardClient(client)
    await client.add_cog(AltGuardBotCog(client))
