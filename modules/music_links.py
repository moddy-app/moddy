"""
Music links module — a Spotify link in a message gets the same song on the
other platforms, right under it.

Someone posts ``open.spotify.com/track/...``; half the channel uses Deezer,
Apple Music or YouTube Music. Moddy answers the message, discreetly (silent,
no ping, no embed), with one dim line — ``♪ Title — Artist`` — and link buttons
to the platforms where it found the same song.

Silence is the default: bots and webhooks are ignored, a playlist has no
equivalent elsewhere, a failed lookup posts nothing, and a card whose only
button would be a YouTube Music *search* is not worth a reply (see
``tools.music.worth_posting``).

**Default-enabled** (``DEFAULT_ENABLED``): runs in every server that never
touched it. A server pauses it from /config -> Manage, picks the platforms to
show and ignores channels on the module screen.

``on_message`` never waits for the network: it hands the work to
``bot.music_links.spawn`` (bounded in time and in number). The resolution
itself lives in ``services/music_links_service.py`` and is shared with
``/music``. See docs/features/MUSIC_LINKS.md.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

import discord

from modules.module_manager import ModuleBase
from tools import music as mu
from utils.emojis import MUSIC_NOTE

logger = logging.getLogger("moddy.modules.music_links")

PLATFORMS = mu.PLATFORMS

# Discord caps a select at 25 values — the ignored channel list follows.
MAX_IGNORED_CHANNELS = 25

NO_MENTIONS = discord.AllowedMentions.none()


def normalize_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Stored config -> a complete, valid one (unknown values dropped).

    No platform (or none valid) means all of them: a module that shows no
    button would be pointless, so an empty list can only be a stale value.
    """
    config = dict(config or {})
    raw = config.get("platforms")
    platforms = [p for p in PLATFORMS if isinstance(raw, list) and p in raw] or list(PLATFORMS)
    channels: List[int] = []
    raw_channels = config.get("ignored_channel_ids") or []
    if isinstance(raw_channels, list):
        for value in raw_channels:
            if str(value).isdigit() and int(value) not in channels:
                channels.append(int(value))
    return {**config, "platforms": platforms,
            "ignored_channel_ids": channels[:MAX_IGNORED_CHANNELS]}


class MusicLinksModule(ModuleBase):
    """Answers Spotify links with the same item on other platforms."""

    MODULE_ID = "music_links"
    MODULE_NAME = "Music links"
    MODULE_DESCRIPTION = "Ajoute Deezer, Apple Music et YouTube Music sous les liens Spotify"
    # Empty until the music_note artwork is uploaded: /config renders it as "no icon".
    MODULE_EMOJI = MUSIC_NOTE
    MODULE_ORDER = 106
    DEFAULT_ENABLED = True
    # Replies in the channel the link was posted in: the baseline (view, send,
    # history, external emojis) covers it, nothing module-specific.
    REQUIRED_BOT_PERMISSIONS: List[str] = []

    def __init__(self, bot, guild_id: int):
        super().__init__(bot, guild_id)
        self.platforms: List[str] = list(PLATFORMS)
        self.ignored_channel_ids: List[int] = []

    # ----------------------------------------------------------------- #
    # Configuration
    # ----------------------------------------------------------------- #

    def get_default_config(self) -> Dict[str, Any]:
        return {"platforms": list(PLATFORMS), "ignored_channel_ids": []}

    async def load_config(self, config_data: Dict[str, Any]) -> bool:
        try:
            config = normalize_config(config_data)
            self.config = config
            self.platforms = config["platforms"]
            self.ignored_channel_ids = config["ignored_channel_ids"]
            # Pause / resume is the generic module state (apply_state).
            self.enabled = True
            return True
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error loading music_links config: {e}")
            return False

    async def validate_config(self, config_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        platforms = config_data.get("platforms")
        if (not isinstance(platforms, list) or not platforms
                or any(p not in PLATFORMS for p in platforms)):
            return False, "Plateformes invalides"
        channels = config_data.get("ignored_channel_ids") or []
        if not isinstance(channels, list) or not all(str(c).isdigit() for c in channels):
            return False, "La liste des salons est invalide"
        if len(channels) > MAX_IGNORED_CHANNELS:
            return False, f"Maximum {MAX_IGNORED_CHANNELS} salons"
        return True, None

    # ----------------------------------------------------------------- #
    # Runtime
    # ----------------------------------------------------------------- #

    def handles_channel(self, channel) -> bool:
        """False in an ignored channel (a thread follows its parent)."""
        ids = {getattr(channel, "id", None), getattr(channel, "parent_id", None)}
        return not (ids & set(self.ignored_channel_ids))

    async def on_message(self, message: discord.Message) -> None:
        """Cheap checks, then hand over to the background: never waits on the network."""
        if (not self.enabled or message.guild is None or message.author.bot
                or getattr(message, "webhook_id", None)):
            return
        if not mu.has_spotify_hint(message.content):
            return
        if not self.handles_channel(message.channel):
            return
        me = message.guild.me
        if me is not None and not message.channel.permissions_for(me).send_messages:
            return
        service = getattr(self.bot, "music_links", None)
        if service is None:
            return
        service.spawn(self._reply(service, message))

    async def _reply(self, service, message: discord.Message) -> None:
        refs = await service.refs_from_text(message.content)
        if not refs:
            return
        infos = [i for i in await asyncio.gather(
            *(service.resolve_quiet(ref, source="auto") for ref in refs)) if i is not None]
        if not infos:
            return

        from utils.guild_language import guild_locale
        from utils.music_links_views import build_reply_view
        # Read by the whole channel: the server language.
        locale = await guild_locale(self.bot, message.guild)
        view = build_reply_view(infos, self.platforms, locale)
        if view is None:
            return
        try:
            await message.reply(view=view, mention_author=False, silent=True,
                                allowed_mentions=NO_MENTIONS)
        except discord.Forbidden:
            logger.debug(f"No permission to reply to a Spotify link in guild {self.guild_id}")
            return
        except discord.HTTPException as e:
            # Includes the original message being deleted meanwhile.
            logger.debug(f"Could not reply to a Spotify link in guild {self.guild_id}: {e}")
            return
        for info in infos:
            if mu.worth_posting(info, self.platforms):
                service.record("posted", kind=info.kind)
                self.count("posted")
