"""
What every mini-bot gets without writing a line: ``/config``, ``/about``, the
welcome DM to whoever adds it, error handling, persistent views and the
command sync.

- ``/config`` opens the module directly when the bot runs a single one (the
  usual case), otherwise the regular /config main screen restricted to its
  modules. Same screens, same saves as Moddy's — the module manager stamps the
  config with the mini-bot's ``app``.
- ``/about`` presents the bot, says it is made by the Moddy team and points to
  Moddy for everything else.
- The welcome DM goes through the notification system (rule #11), like
  Moddy's own install welcome.

Every text is i18n (``minibots.*``), with the bot's name as a variable.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import logging
from pathlib import Path
from typing import List

import discord
from discord import app_commands, ui

from minibots.ownership import config_app

logger = logging.getLogger("moddy.minibots")

#: Accent of the mini-bots' own cards (Moddy blue).
ACCENT = 0x3661FF

#: How long a welcome DM waits for the first emoji copy (first start only).
WELCOME_EMOJI_WAIT = 600

#: Persistent views every mini-bot registers (its modules add their own
#: through ``MiniBotSpec.persistent_views``).
COMMON_PERSISTENT_VIEWS = (
    "cogs.config:ConfigMainView",
    "modules.configs.server_settings_config:ServerSettingsConfigView",
    "modules.configs._common:ModuleStatePersistence",
    "utils.minibot_transfer:MinibotTransferPersistence",
    "utils.notification_views:NotificationsPersistence",
)


def _load(path: str):
    module, _, attr = path.partition(":")
    return getattr(importlib.import_module(module), attr)


def module_names(client, locale: str) -> str:
    names = []
    for module_id in client.spec.modules:
        cls = client.module_manager.registered_modules.get(module_id)
        names.append(f"`{cls.MODULE_NAME if cls else module_id}`")
    return ", ".join(names)


#: Asked on top of the modules' own needs: reading who added the bot (its
#: welcome DM goes to them, not to the owner by default).
EXTRA_INVITE_PERMISSIONS = ("view_audit_log",)


def invite_permissions(client) -> int:
    """Exactly what the bot's modules need (never Administrator, rule #12)."""
    perms = discord.Permissions.none()
    names = set(EXTRA_INVITE_PERMISSIONS)
    for cls in client.module_manager.registered_modules.values():
        names.update(cls.required_permissions())
    for name in names:
        if hasattr(perms, name):
            setattr(perms, name, True)
    return perms.value


def invite_url(client) -> str:
    """Add-to-server link: bot + slash commands, with the right permissions."""
    app_id = client.spec.application_id or (client.user.id if client.user else 0)
    return (f"https://discord.com/oauth2/authorize?client_id={app_id}"
            f"&scope=bot+applications.commands&permissions={invite_permissions(client)}")


# --------------------------------------------------------------------------- #
# /config
# --------------------------------------------------------------------------- #

async def build_config_home(client, interaction: discord.Interaction):
    """The screen /config opens (None when the bot lacks permissions — the
    check already answered)."""
    from cogs.config import ConfigMainView, build_module_config_view
    from modules.configs._common import check_bot_perms
    from utils.i18n import i18n
    from utils.minibot_transfer import build_external_module_view

    locale = i18n.get_user_locale(interaction)
    manager = client.module_manager
    guild_id = interaction.guild_id
    if len(client.spec.modules) != 1:
        return await ConfigMainView.create(client, guild_id, interaction.user.id, locale)

    module_id = client.spec.modules[0]
    module_class = manager.registered_modules.get(module_id)
    config = await manager.get_module_config(guild_id, module_id)
    if not manager.owns(config):
        return build_external_module_view(client, guild_id, locale, module_id,
                                          config_app(config), viewer_app=manager.app)
    if module_class is not None:
        try:
            needs = module_class.channel_requirements(config or {})
        except Exception:  # noqa: BLE001 — malformed config: guild-level check still runs
            needs = {}
        if not await check_bot_perms(interaction, module_class.required_permissions(),
                                     module_name=module_class.MODULE_NAME, followup=True,
                                     channel_requirements=needs):
            return None
    return await build_module_config_view(client, guild_id, interaction.user.id, locale,
                                          module_id, config)


def make_config_command(client) -> app_commands.Command:
    @app_commands.command(name="config", description="Configure server modules")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def config_command(interaction: discord.Interaction):
        from modules.configs._common import check_guild_perms
        if not await check_guild_perms(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        view = await build_config_home(client, interaction)
        if view is not None:
            await interaction.followup.send(view=view, ephemeral=True)
    return config_command


# --------------------------------------------------------------------------- #
# /about
# --------------------------------------------------------------------------- #

def build_about_view(client, locale: str):
    from cogs.error_handler import BaseView
    from config import DOCS_URL, MODDY_INVITE_URL, SUPPORT_URL
    from utils.emojis import INFO, MODDY
    from utils.i18n import t

    name = client.spec.name
    view = BaseView()
    container = ui.Container(accent_colour=discord.Colour(ACCENT))
    container.add_item(ui.TextDisplay(f"### {INFO} {t('minibots.about.title', locale=locale, name=name)}"))
    container.add_item(ui.TextDisplay(t("minibots.about.body", locale=locale, name=name,
                                        modules=module_names(client, locale))))
    container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    container.add_item(ui.TextDisplay(f"{MODDY} {t('minibots.about.moddy', locale=locale, name=name)}"))
    container.add_item(ui.TextDisplay(
        f"-# {t('minibots.about.stats', locale=locale, guilds=f'`{len(client.guilds)}`', latency=f'`{round(client.latency * 1000) if client.latency == client.latency else 0}ms`')}"))
    view.add_item(container)
    row = ui.ActionRow()
    row.add_item(ui.Button(label=t("minibots.links.add_bot", locale=locale, name=name)[:80],
                           style=discord.ButtonStyle.link, url=invite_url(client)))
    row.add_item(ui.Button(label=t("minibots.links.get_moddy", locale=locale),
                           style=discord.ButtonStyle.link, url=MODDY_INVITE_URL))
    row.add_item(ui.Button(label=t("modules.config.main.links.support", locale=locale),
                           style=discord.ButtonStyle.link, url=SUPPORT_URL))
    row.add_item(ui.Button(label=t("modules.config.main.links.docs", locale=locale),
                           style=discord.ButtonStyle.link, url=DOCS_URL))
    view.add_item(row)
    return view


def make_about_command(client) -> app_commands.Command:
    @app_commands.command(name="about", description="About this bot")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def about_command(interaction: discord.Interaction):
        from utils.i18n import i18n
        await interaction.response.send_message(
            view=build_about_view(client, i18n.get_user_locale(interaction)), ephemeral=True)
    return about_command


# --------------------------------------------------------------------------- #
# Welcome DM
# --------------------------------------------------------------------------- #

def welcome_content(client, locale: str):
    import config
    from notifications.models import NotificationContent
    from utils.emojis import MODDY_SQUARE_MIN
    from utils.i18n import t
    return NotificationContent(
        title=t("minibots.welcome.title", locale=locale, name="{name}"),
        body=t("minibots.welcome.body", locale=locale, name="{name}", server="{server}",
               modules="{modules}", config=config.command_label("config"),
               about=config.command_label("about")),
        icon=MODDY_SQUARE_MIN,
        accent_color=ACCENT,
        links=[{"label": t("minibots.links.get_moddy", locale=locale), "url": config.MODDY_INVITE_URL},
               {"label": t("modules.config.main.links.support", locale=locale), "url": config.SUPPORT_URL}],
        template_id="minibots.welcome",
    )


async def send_welcome(client, guild: discord.Guild) -> bool:
    """DM whoever added the bot. Never raises."""
    try:
        from notifications.models import ContentAuthor, NotificationSource
        from utils.guild_language import guild_locale
        from utils.install_welcome import resolve_installer

        # On a bot's very first start the icons are still being copied: wait
        # for them (bounded) so the first impression is not raw ":emoji:" text.
        ready = getattr(client, "emojis_ready", None)
        if ready is not None and not ready.is_set():
            try:
                await asyncio.wait_for(ready.wait(), timeout=WELCOME_EMOJI_WAIT)
            except asyncio.TimeoutError:
                pass

        installer = await resolve_installer(client, guild)
        if installer is None or getattr(installer, "bot", False):
            return False
        locale = await guild_locale(client, guild)
        variables = {"name": client.spec.name, "server": guild.name,
                     "modules": module_names(client, locale)}
        content, view = welcome_content(client, locale), None
        if client.spec.welcome_builder:
            built = await _load(client.spec.welcome_builder)(client, guild, locale)
            if built is not None:
                content, variables, view = built
        result = await client.notifications.send_dm(
            installer, content=content, variables=variables, view=view,
            # "Sent by <bot name>": the Moddy service label, rebranded by
            # minibots/branding.py to the bot's own name. Users never see
            # the internal word "mini-bot".
            source=NotificationSource.service("moddy", author=ContentAuthor.MODDY),
            locale=locale,
        )
        return bool(result.delivered)
    except Exception as exc:  # noqa: BLE001 — a welcome is never worth a failed join
        logger.error("Mini-bot %s: welcome DM failed for guild %s: %s",
                     client.app_id, guild.id, exc, exc_info=True)
        return False


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #

async def setup(client) -> None:
    """Called from ``MiniBotClient.setup_hook``."""
    from cogs.error_handler import ErrorTracker
    await client.add_cog(ErrorTracker(client))

    client.tree.add_command(make_config_command(client))
    client.tree.add_command(make_about_command(client))

    for path in (*COMMON_PERSISTENT_VIEWS, *client.spec.persistent_views):
        try:
            cls = _load(path)
            if getattr(cls, "__persistent__", False):
                cls.register_persistent(client)
        except Exception as exc:  # noqa: BLE001 — one view never blocks the bot
            logger.error("Mini-bot %s: persistent view %s not registered: %s",
                         client.app_id, path, exc)


def command_hash(client) -> str:
    """Fingerprint of the command tree + the command localizations."""
    payload = [cmd.to_dict(client.tree) for cmd in client.tree.get_commands()]
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode())
    for path in sorted((Path(__file__).resolve().parent.parent / "locales" / "commands").glob("*.json")):
        digest.update(path.read_bytes())
    return digest.hexdigest()


async def sync_commands(client) -> bool:
    """Publish the global commands, only when they changed since the last sync."""
    key = f"minibots:cmdhash:{client.app_id}"
    fingerprint = command_hash(client)
    redis = client.redis
    try:
        if redis is not None and await redis.get(key) == fingerprint:
            return False
        await client.tree.sync()
        if redis is not None:
            await redis.set(key, fingerprint)
        logger.info("Mini-bot %s: commands synced", client.app_id)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("Mini-bot %s: command sync failed: %s", client.app_id, exc)
        return False
