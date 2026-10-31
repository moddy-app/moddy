"""
Bump Reminder — the first mini-bot.

It runs Moddy's ``bump_reminder`` module (same detection, same cards, same
/config panel, same table) under its own name. What it adds is all about
removing friction, because a single-feature bot is judged in its first minute:

1. **Zero setup.** Nobody has to open /config. The first successful bump the
   bot sees, on a directory the server has no reminder for, configures one in
   that very channel (directory defaults, no roles, "remind me" button) and
   arms it. The thank-you card says so in one discreet line. It happens once
   per (server, directory): a reminder the admin later deletes stays deleted.
2. **A welcome that knows the server.** Added to a server, it looks for the
   seven directories already there (a gateway lookup by id, no privileged
   intent) and DMs the person who added it: "I see DISBOARD here, nothing to
   do — bump as usual", with an optional channel picker per directory to
   activate it right now.
3. **A directory added later** (``integrations`` intent: the bot has no
   member events) gets the same one-pick offer as on Moddy
   (services/bump_suggestion_service.py) — deduplicated with Moddy's through
   the same Redis key, so a server with both bots gets one DM, not two.

The reminder rows belong to this app (``bump_reminders.app``), so Moddy's
sweeper never claims them; a transfer to Moddy reassigns them.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import discord
from discord.ext import commands

from bumpreminder import BUMP_BOTS, bot_by_app_id, detect
from cogs.bump_reminder import MODULE_ID, BumpReminder

logger = logging.getLogger("moddy.minibots.bump_reminder")

#: Directories offered a channel picker in the welcome DM (a DM stays short).
MAX_WELCOME_PICKERS = 3


def autosetup_path(app: str, bot_key: str) -> str:
    """Where the "already set up once" marker lives in ``guilds.data``."""
    return f"minibots.{app}.bump_autosetup.{bot_key}"


def already_set_up(guild_data: Dict, app: str, bot_key: str) -> bool:
    node = guild_data
    for part in autosetup_path(app, bot_key).split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return bool(node)


def wants_autosetup(stored: Optional[Dict], bot_key: str, owns: bool) -> bool:
    """Pure: may the bot configure ``bot_key`` on its own from a bump?"""
    from modules.bump_reminder import normalize_config
    from modules.module_manager import STATE_KEY, STATE_PAUSED
    if not owns:
        return False  # Moddy (or another app) runs the module here
    raw = stored or {}
    if raw.get(STATE_KEY) == STATE_PAUSED:
        return False  # a deliberate "no"
    return not any(e["bot"] == bot_key for e in normalize_config(raw)["reminders"])


async def directories_present(guild: discord.Guild) -> List:
    """The supported directories that are members of ``guild`` (one gateway
    request by ids; no member intent needed, nothing cached)."""
    ids = [spec.app_id for spec in BUMP_BOTS]
    try:
        members = await guild.query_members(user_ids=ids, limit=len(ids), cache=False)
    except Exception as exc:  # noqa: BLE001 — the welcome just stays generic
        logger.debug("Directory lookup failed in guild %s: %s", guild.id, exc)
        return []
    found = {m.id for m in members}
    return [spec for spec in BUMP_BOTS if spec.app_id in found]


def too_many_channels(guild: discord.Guild) -> bool:
    """More postable text channels than a select can list (25)."""
    from services.bump_suggestion_service import MAX_CHANNEL_OPTIONS
    me = getattr(guild, "me", None)
    if me is None:
        return False
    count = 0
    for channel in getattr(guild, "text_channels", ()):
        perms = channel.permissions_for(me)
        if perms.view_channel and perms.send_messages:
            count += 1
            if count > MAX_CHANNEL_OPTIONS:
                return True
    return False


class BumpReminderBotCog(BumpReminder):
    """Moddy's Bump Reminder cog, plus zero-setup and the integration hook."""

    def __init__(self, bot):
        super().__init__(bot)
        self._setup_locks: Dict[int, asyncio.Lock] = {}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        author = message.author
        if not author.bot or message.guild is None:
            return
        spec = bot_by_app_id(author.id)
        if spec is None:
            return
        try:
            module = await self.bot.module_manager.get_module_instance(message.guild.id, MODULE_ID)
            if module is not None and module.enabled and module.entry_for(message.channel.id, spec.key):
                found = await module.on_message(message, spec)
                if found is not None:
                    await self._on_bump(message, module, found["hit"], found["entry"])
                return
            await self._auto_setup(message, spec)
        except Exception as exc:  # noqa: BLE001
            logger.error("Bump handling failed in guild %s: %s", message.guild.id, exc, exc_info=True)

    async def _auto_setup(self, message: discord.Message, spec) -> bool:
        """Configure the reminder from the first successful bump. True when done."""
        from config import command_label
        from modules.bump_reminder import DEFAULT_PING_MODE, new_entry, normalize_config
        from utils.guild_language import guild_locale
        from utils.i18n import t

        guild, channel = message.guild, message.channel
        if not isinstance(channel, discord.TextChannel):
            return False
        perms = channel.permissions_for(guild.me)
        if not (perms.view_channel and perms.send_messages):
            return False

        lock = self._setup_locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            manager = self.bot.module_manager
            stored = await manager.get_module_config(guild.id, MODULE_ID)
            if not wants_autosetup(stored, spec.key, manager.owns(stored)):
                return False
            guild_data = (await self.bot.db.get_guild(guild.id)).get("data", {})
            if already_set_up(guild_data, self.app, spec.key):
                return False  # set up once, then removed by the admin: respected
            hit = detect(message, spec.default_interval)
            if hit is None:
                return False  # only a *successful* bump sets anything up

            reminders = normalize_config(stored)["reminders"]
            reminders.append(new_entry(
                spec.key, channel_id=channel.id, role_ids=[], ping_mode=DEFAULT_PING_MODE,
                interval=spec.default_interval, created_by=None))
            ok, error = await manager.save_module_config(
                guild.id, MODULE_ID, {**(stored or {}), "reminders": reminders})
            if not ok:
                logger.info("Auto-setup refused in guild %s (%s): %s", guild.id, spec.key, error)
                return False
            await self.bot.db.update_guild_data(
                guild.id, autosetup_path(self.app, spec.key),
                datetime.now(timezone.utc).isoformat())

        module = await manager.get_module_instance(guild.id, MODULE_ID)
        entry = module.entry_for(channel.id, spec.key) if module else None
        if entry is None:
            return False
        locale = await guild_locale(self.bot, guild)
        note = t("minibots.bots.bump_reminder.auto_setup_note", locale=locale,
                 config=command_label("config"))
        await self._on_bump(message, module, hit, entry, note=note)
        logger.info("Bump reminder set up automatically: guild %s, %s, channel %s",
                    guild.id, spec.key, channel.id)
        return True

    @commands.Cog.listener()
    async def on_integration_create(self, integration):
        """A directory added after the bot: same one-pick offer as Moddy's."""
        application = getattr(integration, "application", None)
        user = getattr(application, "user", None)
        app_id = getattr(user, "id", None) or getattr(application, "id", None)
        spec = bot_by_app_id(app_id) if app_id else None
        guild = getattr(integration, "guild", None)
        if spec is None or guild is None:
            return
        try:
            await self.suggestions._maybe_suggest(guild, spec)
        except Exception as exc:  # noqa: BLE001
            logger.error("Bump offer failed in guild %s: %s", guild.id, exc, exc_info=True)


# --------------------------------------------------------------------------- #
# Welcome DM
# --------------------------------------------------------------------------- #

async def build_welcome(client, guild: discord.Guild, locale: str):
    """``(content, variables, view)`` of the DM sent to whoever added the bot."""
    from config import MODDY_INVITE_URL, SUPPORT_URL, command_label
    from cogs.error_handler import BaseView
    from discord import ui
    from minibots.builtin import ACCENT
    from notifications.models import NotificationContent
    from services.bump_suggestion_service import candidate_channels, should_suggest
    from utils.bump_views import BumpSuggestChannelSelect
    from utils.emojis import ROCKET_LAUNCH
    from utils.i18n import t

    present = await directories_present(guild)
    stored = await client.module_manager.get_module_config(guild.id, MODULE_ID)
    owned = client.module_manager.owns(stored)
    names = ", ".join(f"{s.emoji} **{s.name}**" for s in present)
    body_key = "present" if present else "none"
    if not owned:
        body_key = "elsewhere"
    variables = {"name": client.spec.name, "server": guild.name, "directories": names,
                 "config": command_label("config")}
    content = NotificationContent(
        title=t("minibots.bots.bump_reminder.welcome.title", locale=locale, name="{name}"),
        body=t(f"minibots.bots.bump_reminder.welcome.{body_key}", locale=locale,
               name="{name}", server="{server}", directories="{directories}",
               config="{config}"),
        icon=ROCKET_LAUNCH, accent_color=ACCENT,
        links=[{"label": t("minibots.links.get_moddy", locale=locale), "url": MODDY_INVITE_URL},
               {"label": t("modules.config.main.links.support", locale=locale), "url": SUPPORT_URL}],
        template_id=f"minibots.bump_reminder.welcome.{body_key}",
    )
    rendered = content.render(variables)

    view = BaseView()
    container = ui.Container(accent_colour=discord.Colour(ACCENT))
    container.add_item(ui.TextDisplay(f"### {ROCKET_LAUNCH} {rendered.title}"))
    container.add_item(ui.TextDisplay(rendered.body))

    channels = candidate_channels(guild) if owned else []
    pickers = [s for s in present if should_suggest(stored, s.key)][:MAX_WELCOME_PICKERS]
    if pickers and too_many_channels(guild):
        # A select holds 25 options: with more channels the right one may not
        # be in the list. /config has a real channel picker (search included).
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(t(
            "minibots.bots.bump_reminder.welcome.pick_config", locale=locale,
            config=command_label("config"))))
    elif channels and pickers:
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(
            t("minibots.bots.bump_reminder.welcome.pick", locale=locale)))
        options = [discord.SelectOption(label=f"#{c.name}"[:100], value=str(c.id)) for c in channels]
        for spec in pickers:
            container.add_item(ui.TextDisplay(f"{spec.emoji} **{spec.name}**"))
            container.add_item(ui.ActionRow(BumpSuggestChannelSelect(
                guild.id, spec.key, locale=locale, options=options)))
    view.add_item(container)
    row = ui.ActionRow()
    row.add_item(ui.Button(label=t("minibots.links.get_moddy", locale=locale),
                           style=discord.ButtonStyle.link, url=MODDY_INVITE_URL))
    row.add_item(ui.Button(label=t("modules.config.main.links.support", locale=locale),
                           style=discord.ButtonStyle.link, url=SUPPORT_URL))
    view.add_item(row)
    return content, variables, view


async def setup(client) -> None:
    await client.add_cog(BumpReminderBotCog(client))
