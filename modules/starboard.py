"""
Module Starboard - Système de tableau d'honneur pour messages populaires
"""

import re
import asyncio
import logging
from typing import Dict, Any, Optional

import aiohttp
import discord

from cogs.error_handler import BaseView
from modules.module_manager import ModuleBase
from utils.emojis import STAR, is_standard_discord_emoji
from utils.i18n import t, i18n

logger = logging.getLogger('moddy.modules.starboard')

# Gold accent used for the starboard embed's side bar, matching the star theme.
STARBOARD_EMBED_COLOR = 0xFFAC33

_IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.webp')
_IMAGE_URL_RE = re.compile(
    r'https?://\S+?\.(?:png|jpe?g|gif|webp)(?:\?\S*)?',
    re.IGNORECASE,
)
_URL_RE = re.compile(r'https?://\S+', re.IGNORECASE)
_META_TAG_RE = re.compile(r'<meta\b[^>]*>', re.IGNORECASE)
_META_ATTR_RE = re.compile(r'''([a-zA-Z][\w:-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')''')
_OG_IMAGE_PROPS = ('og:image:secure_url', 'og:image', 'twitter:image', 'twitter:image:src')
_LINK_PREVIEW_TIMEOUT = aiohttp.ClientTimeout(total=5)
_LINK_PREVIEW_MAX_BYTES = 300_000
# Many sites (Tenor, Klipy, Giphy, ...) special-case Discord's own crawler UA
# to serve pre-rendered OpenGraph tags for rich link unfurls, even when the
# page itself is a JS-rendered SPA for regular browsers. Mimicking it here
# gets us the same treatment.
_LINK_PREVIEW_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)",
    "Accept": "text/html,application/xhtml+xml",
}


def _extract_meta_content(html: str, props: tuple) -> Optional[str]:
    """
    Find the `content` of the first `<meta>` tag whose `property`/`name`
    matches one of `props` (checked in order), regardless of attribute
    order or quoting. A small hand-rolled parser instead of a regex per
    property, since attribute order in the wild is inconsistent
    (`content` before `property` is common).
    """
    best_by_prop: Dict[str, str] = {}
    for tag_match in _META_TAG_RE.finditer(html):
        tag = tag_match.group(0)
        attrs = {}
        for attr_match in _META_ATTR_RE.finditer(tag):
            key = attr_match.group(1).lower()
            value = attr_match.group(2) if attr_match.group(2) is not None else attr_match.group(3)
            attrs[key] = value

        prop = attrs.get('property') or attrs.get('name')
        content = attrs.get('content')
        if prop and content and prop.lower() in props and prop.lower() not in best_by_prop:
            best_by_prop[prop.lower()] = content

    for prop in props:
        if prop in best_by_prop:
            return best_by_prop[prop]
    return None


def _report_dynamic_item_error(interaction: discord.Interaction, error: Exception, source: str):
    """Route a DynamicItem callback error to the central handler (fire-and-forget)."""
    from cogs.error_handler import report_component_error
    asyncio.create_task(report_component_error(interaction, error, source))


class _StarboardReactorsButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"moddy:starboard:reactors:(?P<channel_id>\d+):(?P<message_id>\d+):(?P<emoji>.+)",
):
    """
    Persistent button showing the reaction count. Clicking it lists (ephemeral)
    who reacted with the configured emoji.

    This lives on the same message as `_StarboardCardView`'s link button, next
    to a real `discord.Embed` — see the module docstring for why that message
    cannot use `BaseView`. Its callback is guarded with
    `report_component_error` instead of a `BaseView.on_error`, matching the
    pattern already used by other persistent `DynamicItem`s that ship without
    a live view (see `utils/appeal_views.py`).
    """

    def __init__(self, channel_id: int, message_id: int, emoji: str, count: int):
        super().__init__(
            discord.ui.Button(
                style=discord.ButtonStyle.secondary,
                label=str(count),
                emoji=emoji,
                custom_id=f"moddy:starboard:reactors:{channel_id}:{message_id}:{emoji}",
            )
        )
        self.channel_id = channel_id
        self.message_id = message_id
        self.emoji = emoji

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match):
        return cls(
            channel_id=int(match["channel_id"]),
            message_id=int(match["message_id"]),
            emoji=match["emoji"],
            count=0,  # Placeholder — the already-sent button keeps its rendered label.
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            await self._show_reactors(interaction)
        except Exception as e:
            _report_dynamic_item_error(interaction, e, self.__class__.__name__)

    async def _show_reactors(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        locale = i18n.get_user_locale(interaction)

        channel = interaction.guild.get_channel(self.channel_id) if interaction.guild else None
        if channel is None:
            await interaction.followup.send(
                t('modules.starboard.message.reactors.not_found', locale=locale), ephemeral=True
            )
            return

        try:
            message = await channel.fetch_message(self.message_id)
        except (discord.NotFound, discord.Forbidden):
            await interaction.followup.send(
                t('modules.starboard.message.reactors.not_found', locale=locale), ephemeral=True
            )
            return

        reaction = discord.utils.find(
            lambda r: not r.is_custom_emoji() and str(r.emoji) == self.emoji, message.reactions
        )
        if reaction is None:
            await interaction.followup.send(
                t('modules.starboard.message.reactors.empty', locale=locale), ephemeral=True
            )
            return

        users = [user async for user in reaction.users() if not user.bot]
        if not users:
            await interaction.followup.send(
                t('modules.starboard.message.reactors.empty', locale=locale), ephemeral=True
            )
            return

        max_shown = 40
        lines = [f"- {user.mention}" for user in users[:max_shown]]
        if len(users) > max_shown:
            lines.append(t(
                'modules.starboard.message.reactors.more', locale=locale,
                count=f"`{len(users) - max_shown}`",
            ))

        title = t(
            'modules.starboard.message.reactors.title', locale=locale,
            emoji=self.emoji, count=f"`{len(users)}`",
        )
        await interaction.followup.send(
            f"{title}\n" + "\n".join(lines),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class StarboardCardPersistence(BaseView):
    """Marker view used only to register the starboard card's dynamic items at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(_StarboardReactorsButton)


class _StarboardCardView(discord.ui.View):
    """
    Plain (non-Components-V2) view holding the reactors button and the
    "jump to message" link button.

    This intentionally does NOT inherit from `BaseView`: `BaseView` is a
    `ui.LayoutView` (Components V2), and Discord rejects any message that
    combines the IS_COMPONENTS_V2 flag with a classic `discord.Embed` —
    which the starboard card needs (see module docstring). The jump button
    is a link with no callback (nothing to guard or persist); the reactors
    button is a persistent `DynamicItem` whose errors are separately routed
    to `report_component_error` — see `_StarboardReactorsButton`. So this
    view itself needs neither error-handler wrapping nor registration.
    """

    def __init__(self, channel_id: int, message_id: int, emoji: str, count: int,
                 jump_url: str, jump_label: str):
        super().__init__(timeout=None)
        self.add_item(_StarboardReactorsButton(channel_id, message_id, emoji, count))
        self.add_item(discord.ui.Button(
            style=discord.ButtonStyle.link,
            label=jump_label,
            url=jump_url,
        ))


async def _fetch_link_preview_image(url: str) -> Optional[str]:
    """
    Best-effort OpenGraph/Twitter-card image lookup for a bare link that
    isn't itself a direct image URL (e.g. a GIF site's share page). Used as
    a fallback so starred messages that are "just a link" still show a
    preview, matching what Discord's own link unfurling would show.
    """
    try:
        async with aiohttp.ClientSession(timeout=_LINK_PREVIEW_TIMEOUT) as session:
            async with session.get(url, headers=_LINK_PREVIEW_HEADERS) as response:
                if response.status != 200:
                    logger.debug(f"Starboard link preview: {url} returned HTTP {response.status}")
                    return None
                content_type = response.headers.get("Content-Type", "")
                if "html" not in content_type:
                    logger.debug(f"Starboard link preview: {url} is not HTML ({content_type!r})")
                    return None
                raw = await response.content.read(_LINK_PREVIEW_MAX_BYTES)
    except Exception as e:
        logger.debug(f"Starboard link preview fetch failed for {url}: {e}")
        return None

    html = raw.decode("utf-8", errors="ignore")
    candidate = _extract_meta_content(html, _OG_IMAGE_PROPS)
    if not candidate:
        logger.debug(f"Starboard link preview: no og:image/twitter:image meta tag found for {url}")
        return None

    if candidate.startswith("//"):
        candidate = f"https:{candidate}"
    elif candidate.startswith("/"):
        from urllib.parse import urljoin
        candidate = urljoin(url, candidate)

    return candidate


class StarboardModule(ModuleBase):
    """
    Module de starboard (tableau d'honneur)
    Forward automatiquement les messages qui reçoivent un nombre X de réactions
    (un émoji standard Discord, configurable) dans un salon dédié, avec un
    compteur de réactions et un lien vers le message d'origine.

    CLAUDE.md exceptions (both explicit, scoped to this one card):
    - The starred message is rendered as a real `discord.Embed` (not
      Components V2), matching the Starboard-app look the feature is styled
      after (colored side bar, footer avatar/name, native timestamp). See
      `_build_embed()` / `_StarboardCardView` for the implementation notes.
    - Rule #7 (verification badge next to a displayed username) is
      deliberately NOT applied on this card — no badge is shown here, by
      request.
    """

    MODULE_ID = "starboard"
    MODULE_NAME = "Starboard"
    MODULE_DESCRIPTION = "Tableau d'honneur des messages populaires"
    MODULE_EMOJI = STAR

    def __init__(self, bot, guild_id: int):
        super().__init__(bot, guild_id)

        # Channel configuration
        self.channel_id: Optional[int] = None

        # Starboard configuration
        self.reaction_count: int = 5  # Number of reactions required
        self.emoji: str = "⭐"  # Standard Discord unicode emoji that triggers the starboard

        # Track sent starboard messages to update them in real-time.
        # Format: {original_message_id: starboard_message_id}
        self.starboard_messages: Dict[int, int] = {}

    async def load_config(self, config_data: Dict[str, Any]) -> bool:
        """Load configuration from DB"""
        try:
            self.config = config_data

            # Channel configuration
            self.channel_id = config_data.get('channel_id')

            # Starboard configuration
            self.reaction_count = config_data.get('reaction_count', 5)
            self.emoji = config_data.get('emoji', "⭐")

            # Module is enabled if channel is configured
            self.enabled = self.channel_id is not None

            return True
        except Exception as e:
            logger.error(f"Error loading starboard config: {e}")
            return False

    async def validate_config(self, config_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate configuration"""
        # Channel ID is required
        if not config_data.get('channel_id'):
            return False, "Un salon est requis pour le starboard"

        # Verify channel exists and bot has permissions
        try:
            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                return False, "Serveur introuvable"

            channel = guild.get_channel(config_data['channel_id'])
            if not channel:
                return False, "Salon introuvable"

            if not isinstance(channel, discord.TextChannel):
                return False, "Le salon doit être un salon textuel"

            # Check permissions
            perms = channel.permissions_for(guild.me)
            if not perms.send_messages:
                return False, f"Je n'ai pas la permission d'envoyer des messages dans {channel.mention}"

        except Exception as e:
            return False, f"Erreur de validation du salon : {str(e)}"

        # Validate reaction count
        reaction_count = config_data.get('reaction_count', 5)
        if not isinstance(reaction_count, int) or reaction_count < 1 or reaction_count > 100:
            return False, "Le nombre de réactions doit être entre 1 et 100"

        # Validate the reaction emoji: only standard Discord emojis are accepted,
        # never a custom/guild emoji.
        emoji = config_data.get('emoji', "⭐")
        if not isinstance(emoji, str) or not emoji:
            return False, "Un émoji de réaction est requis"

        partial_emoji = discord.PartialEmoji.from_str(emoji)
        if partial_emoji.is_custom_emoji():
            return False, "Seuls les émojis standards de Discord sont autorisés (pas d'émoji personnalisé)"

        if not is_standard_discord_emoji(emoji):
            return False, "Émoji invalide, veuillez choisir un émoji standard de Discord"

        return True, None

    def get_default_config(self) -> Dict[str, Any]:
        """Return default configuration"""
        return {
            'channel_id': None,
            'reaction_count': 5,
            'emoji': "⭐"
        }

    async def on_reaction_add(self, payload: discord.RawReactionActionEvent):
        """
        Called when a reaction is added to a message
        Checks if the message should be added to starboard
        """
        if not self.enabled or not self.channel_id:
            return

        # Only standard Discord emojis can trigger the starboard, and only the
        # one configured for this server.
        if payload.emoji.is_custom_emoji() or str(payload.emoji) != self.emoji:
            return

        try:
            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                return

            # Get the channel where the reaction was added
            channel = guild.get_channel(payload.channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                return

            # Don't track reactions in the starboard channel itself
            if payload.channel_id == self.channel_id:
                return

            # Get the message
            try:
                message = await channel.fetch_message(payload.message_id)
            except discord.NotFound:
                logger.warning(f"Message {payload.message_id} not found")
                return

            # Count reactions matching the configured emoji
            star_count = self._count_reactions(message)

            # Check if we should send/update starboard entry
            if star_count >= self.reaction_count:
                await self._update_starboard(message, star_count)

        except discord.Forbidden:
            logger.warning(f"Missing permissions for starboard in guild {self.guild_id}")
        except Exception as e:
            logger.error(f"Error processing starboard reaction: {e}", exc_info=True)

    async def on_reaction_remove(self, payload: discord.RawReactionActionEvent):
        """
        Called when a reaction is removed from a message
        Updates the starboard message with the new count
        """
        if not self.enabled or not self.channel_id:
            return

        if payload.emoji.is_custom_emoji() or str(payload.emoji) != self.emoji:
            return

        # Check if this message has a starboard entry
        if payload.message_id not in self.starboard_messages:
            return

        try:
            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                return

            # Get the channel where the reaction was removed
            channel = guild.get_channel(payload.channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                return

            # Get the message
            try:
                message = await channel.fetch_message(payload.message_id)
            except discord.NotFound:
                return

            star_count = self._count_reactions(message)

            # Update or remove starboard entry
            if star_count >= self.reaction_count:
                await self._update_starboard(message, star_count)
            else:
                # Remove from starboard if below threshold
                await self._remove_starboard(message)

        except Exception as e:
            logger.error(f"Error updating starboard on reaction remove: {e}", exc_info=True)

    def _count_reactions(self, message: discord.Message) -> int:
        """Count how many times the configured (standard) emoji was used on this message"""
        for reaction in message.reactions:
            if not reaction.is_custom_emoji() and str(reaction.emoji) == self.emoji:
                return reaction.count
        return 0

    async def _get_locale(self) -> str:
        """Locale used for the starboard message's static UI strings (jump button)"""
        try:
            guild = self.bot.get_guild(self.guild_id)
            return str(guild.preferred_locale) if guild and guild.preferred_locale else 'en-US'
        except Exception:
            return 'en-US'

    async def _find_image_url(self, message: discord.Message) -> Optional[str]:
        """
        Find an image to show on the starboard embed:
        1. The first image attachment, if there is one.
        2. Otherwise, the first bare image URL found in the message content.
        3. Otherwise, if the message content contains any link, try fetching
           its OpenGraph/Twitter-card image (e.g. a GIF site's share page
           that isn't itself a direct image URL).
        """
        for attachment in message.attachments:
            content_type = attachment.content_type or ""
            if content_type.startswith("image/"):
                return attachment.url
            if attachment.filename.lower().endswith(_IMAGE_EXTENSIONS):
                return attachment.url

        if not message.content:
            return None

        match = _IMAGE_URL_RE.search(message.content)
        if match:
            return match.group(0)

        url_match = _URL_RE.search(message.content)
        if url_match:
            return await _fetch_link_preview_image(url_match.group(0))

        return None

    async def _build_embed(self, message: discord.Message) -> discord.Embed:
        """
        Build the starred message's embed: original text (mentions never
        ping — Discord never notifies from mentions inside an embed, only
        from the plain message content), image if any, the author's name in
        the footer, and the original message's native timestamp.

        By explicit request, this card does NOT show the verification badge
        (rule #7 is intentionally not applied here — see the module
        docstring's CLAUDE.md exception) and shows the author's name only
        once, in the footer, rather than duplicating it in an author header.
        """
        embed = discord.Embed(
            description=message.content or "",
            color=STARBOARD_EMBED_COLOR,
            timestamp=message.created_at,
        )
        embed.set_footer(
            text=message.author.display_name,
            icon_url=message.author.display_avatar.url,
        )

        image_url = await self._find_image_url(message)
        if image_url:
            embed.set_image(url=image_url)

        return embed

    async def _build_view(self, message: discord.Message, star_count: int) -> _StarboardCardView:
        """Build the reactors-count button + "jump to message" link button (see `_StarboardCardView`)"""
        locale = await self._get_locale()
        return _StarboardCardView(
            channel_id=message.channel.id,
            message_id=message.id,
            emoji=self.emoji,
            count=star_count,
            jump_url=message.jump_url,
            jump_label=t('modules.starboard.message.jump_button', locale=locale),
        )

    async def _update_starboard(self, message: discord.Message, star_count: int):
        """
        Update or create a starboard entry for a message
        """
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            return

        starboard_channel = guild.get_channel(self.channel_id)
        if not starboard_channel or not isinstance(starboard_channel, discord.TextChannel):
            logger.warning(f"Starboard channel {self.channel_id} not found or not a text channel")
            return

        # Check if we already have a starboard entry for this message
        if message.id in self.starboard_messages:
            # Only the reactors-count button needs updating
            try:
                starboard_msg_id = self.starboard_messages[message.id]
                starboard_msg = await starboard_channel.fetch_message(starboard_msg_id)
                view = await self._build_view(message, star_count)
                await starboard_msg.edit(view=view)
                logger.info(f"Updated starboard message for {message.id} (stars: {star_count})")
            except discord.NotFound:
                # Entry was deleted, recreate it
                del self.starboard_messages[message.id]
                await self._create_starboard_message(starboard_channel, message, star_count)
        else:
            # Create new starboard entry
            await self._create_starboard_message(starboard_channel, message, star_count)

    async def _create_starboard_message(self, channel: discord.TextChannel,
                                         original_message: discord.Message, star_count: int):
        """
        Post a starboard entry: the starred message rendered as an embed
        (see CLAUDE.md exception note on the class docstring), a reactors
        button, and a jump-to-message link button. `AllowedMentions.none()`
        and the fact that mentions inside embeds never notify anyone
        together ensure nothing in the starred message can ping.
        """
        try:
            embed = await self._build_embed(original_message)
            view = await self._build_view(original_message, star_count)

            starboard_msg = await channel.send(
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )

            # Track it for future updates/removal
            self.starboard_messages[original_message.id] = starboard_msg.id

            logger.info(f"Created starboard entry for {original_message.id}")
        except discord.Forbidden:
            logger.warning(f"Missing permissions to send starboard message in guild {self.guild_id}")
        except discord.HTTPException as e:
            logger.error(f"Error sending starboard message for {original_message.id}: {e}")
        except Exception as e:
            logger.error(f"Error creating starboard message: {e}", exc_info=True)

    async def _remove_starboard(self, message: discord.Message):
        """Remove a starboard entry"""
        if message.id not in self.starboard_messages:
            return

        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            return

        starboard_channel = guild.get_channel(self.channel_id)
        if not starboard_channel or not isinstance(starboard_channel, discord.TextChannel):
            return

        msg_id = self.starboard_messages[message.id]
        try:
            starboard_msg = await starboard_channel.fetch_message(msg_id)
            await starboard_msg.delete()
        except discord.NotFound:
            pass
        except Exception as e:
            logger.error(f"Error removing starboard message {msg_id}: {e}", exc_info=True)

        del self.starboard_messages[message.id]
        logger.info(f"Removed starboard entry for {message.id}")
