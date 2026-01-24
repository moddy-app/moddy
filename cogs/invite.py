"""
Invite lookup command for Moddy
Displays information about Discord invites (Server, Group DM, Friend)
"""

import discord
from discord import app_commands, ui
from cogs.error_handler import BaseView
from discord.ext import commands
from typing import Optional, Dict, Any
import aiohttp
import json
import io
from datetime import datetime

from utils.i18n import i18n, t
from config import EMOJIS


class InviteView(BaseView):
    """View to display invite information using Components V2"""

    def __init__(self, invite_data: Dict[str, Any], locale: str):
        super().__init__(timeout=180)
        self.invite_data = invite_data
        self.locale = locale

        # Build the view
        self.build_view()

    def build_view(self):
        """Builds the Components V2 view for invite information"""
        self.clear_items()

        container = ui.Container()

        # Get invite type
        invite_type = self.invite_data.get('type', 0)

        # Type 0: Guild (Server)
        if invite_type == 0:
            self._build_guild_invite_info(container)
            self.add_item(container)
            self._add_guild_buttons()
        # Type 1: Group DM
        elif invite_type == 1:
            self._build_group_dm_invite(container)
            self.add_item(container)
            self._add_raw_data_button()
        # Type 2: Friend
        elif invite_type == 2:
            self._build_friend_invite(container)
            self.add_item(container)
            self._add_raw_data_button()
        else:
            # Unknown type
            container.add_item(ui.TextDisplay(
                t("commands.invite.errors.unknown_type", locale=self.locale, type=invite_type)
            ))
            self.add_item(container)

    def _add_guild_buttons(self):
        """Add buttons for guild invite (outside container, below)"""
        button_row = ui.ActionRow()
        server_info_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str("<:server:1464693264773939319>"),
            label=t('commands.invite.view.guild.show_server_info', locale=self.locale),
            style=discord.ButtonStyle.primary
        )
        server_info_btn.callback = self.on_show_server_info
        button_row.add_item(server_info_btn)

        # TEMP: Raw data button for debugging
        raw_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str("<:code:1401610523803652196>"),
            label="Raw Data",
            style=discord.ButtonStyle.secondary
        )
        raw_btn.callback = self.on_show_raw_data
        button_row.add_item(raw_btn)

        self.add_item(button_row)

    def _add_raw_data_button(self):
        """Add raw data button only (for non-guild invites)"""
        button_row = ui.ActionRow()
        raw_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str("<:code:1401610523803652196>"),
            label="Raw Data",
            style=discord.ButtonStyle.secondary
        )
        raw_btn.callback = self.on_show_raw_data
        button_row.add_item(raw_btn)
        self.add_item(button_row)

    def _build_guild_invite_info(self, container: ui.Container):
        """Build view for guild invite - shows invite info only"""
        guild = self.invite_data.get('guild', {})
        inviter = self.invite_data.get('inviter')
        channel = self.invite_data.get('channel', {})
        code = self.invite_data.get('code', 'Unknown')
        invite_id = self.invite_data.get('id')

        # Title
        container.add_item(ui.TextDisplay(
            f"### <:search:1443752796460552232> {t('commands.invite.view.guild.title', locale=self.locale)}"
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Build info list (most important first)
        info_lines = []

        # Invite code (most important)
        info_lines.append(f"**{t('commands.invite.view.guild.invite_code', locale=self.locale)}:** `{code}`")

        # Invite ID
        if invite_id:
            info_lines.append(f"**{t('commands.invite.view.guild.invite_id', locale=self.locale)}:** `{invite_id}`")

        # Channel destination
        channel_name = channel.get('name', 'Unknown')
        channel_id = channel.get('id')
        channel_type = channel.get('type', 0)
        channel_info = f"#{channel_name} (`{self._get_channel_type_name(channel_type)}`)"
        if channel_id:
            channel_info += f"\n-# ID: `{channel_id}`"
        info_lines.append(f"**{t('commands.invite.view.guild.channel', locale=self.locale)}:** {channel_info}")

        # Inviter info (if available)
        if inviter:
            inviter_username = inviter.get('username', 'Unknown')
            inviter_global_name = inviter.get('global_name')
            inviter_id = inviter.get('id', 'Unknown')

            if inviter_global_name:
                inviter_display = f"{inviter_global_name} (@{inviter_username})"
            else:
                inviter_display = inviter_username

            info_lines.append(f"**{t('commands.invite.view.guild.inviter', locale=self.locale)}:** {inviter_display}\n-# ID: `{inviter_id}`")

        # Expiration (if available)
        expires_at = self.invite_data.get('expires_at')
        if expires_at:
            try:
                expires_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                expires_ts = int(expires_dt.timestamp())
                info_lines.append(f"**{t('commands.invite.view.guild.expires', locale=self.locale)}:** <t:{expires_ts}:F> (<t:{expires_ts}:R>)")
            except:
                pass

        # Add all info as a single text block
        container.add_item(ui.TextDisplay("\n".join(info_lines)))

    async def on_show_server_info(self, interaction: discord.Interaction):
        """Show server information view"""
        server_view = ServerInfoView(self.invite_data, self.locale)
        await interaction.response.edit_message(view=server_view)

    async def on_show_raw_data(self, interaction: discord.Interaction):
        """TEMP: Show raw API response data"""
        raw_json = json.dumps(self.invite_data, indent=2, ensure_ascii=False)

        # Send as file (safer for any size)
        file = discord.File(
            fp=io.BytesIO(raw_json.encode('utf-8')),
            filename="invite_raw_data.json"
        )
        await interaction.response.send_message("Raw API response:", file=file, ephemeral=True)

    def _build_group_dm_invite(self, container: ui.Container):
        """Build view for group DM invite"""
        channel = self.invite_data.get('channel', {})
        inviter = self.invite_data.get('inviter')
        code = self.invite_data.get('code', 'Unknown')

        # Title
        container.add_item(ui.TextDisplay(
            f"### <:message:1443749710073696286> {t('commands.invite.view.group_dm.title', locale=self.locale)}"
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Build info list
        info_lines = []

        # Invite code
        info_lines.append(f"**{t('commands.invite.view.group_dm.invite_code', locale=self.locale)}:** `{code}`")

        # Group name and ID
        channel_name = channel.get('name') if channel else None
        channel_id = channel.get('id') if channel else None
        if channel_name:
            info_lines.append(f"**{t('commands.invite.view.group_dm.name', locale=self.locale)}:** {channel_name}")
        if channel_id:
            info_lines.append(f"**{t('commands.invite.view.group_dm.id', locale=self.locale)}:** `{channel_id}`")

        # Members count (prefer approximate_member_count)
        member_count = self.invite_data.get('approximate_member_count')
        if member_count is None and channel:
            recipients = channel.get('recipients', [])
            if recipients:
                member_count = len(recipients)
        if member_count is not None:
            info_lines.append(f"**{t('commands.invite.view.group_dm.members', locale=self.locale)}:** `{member_count}`")

        # Expiration (if available)
        expires_at = self.invite_data.get('expires_at')
        if expires_at:
            try:
                expires_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                expires_ts = int(expires_dt.timestamp())
                info_lines.append(f"**{t('commands.invite.view.guild.expires', locale=self.locale)}:** <t:{expires_ts}:F> (<t:{expires_ts}:R>)")
            except:
                pass

        container.add_item(ui.TextDisplay("\n".join(info_lines)))

        # Inviter section (separate block)
        if inviter:
            self._add_inviter_info(container, inviter)

    def _build_friend_invite(self, container: ui.Container):
        """Build view for friend invite"""
        inviter = self.invite_data.get('inviter')
        code = self.invite_data.get('code', 'Unknown')

        # Title
        container.add_item(ui.TextDisplay(
            f"### <:user:1398729712204779571> {t('commands.invite.view.friend.title', locale=self.locale)}"
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Build info list
        info_lines = []

        # Invite code first
        info_lines.append(f"**{t('commands.invite.view.friend.invite_code', locale=self.locale)}:** `{code}`")

        # Expiration (if available)
        expires_at = self.invite_data.get('expires_at')
        if expires_at:
            try:
                expires_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                expires_ts = int(expires_dt.timestamp())
                info_lines.append(f"**{t('commands.invite.view.guild.expires', locale=self.locale)}:** <t:{expires_ts}:F> (<t:{expires_ts}:R>)")
            except:
                pass

        container.add_item(ui.TextDisplay("\n".join(info_lines)))

        # Inviter section (separate block with all details)
        if inviter:
            self._add_inviter_info(container, inviter)

    def _add_inviter_info(self, container: ui.Container, inviter: Dict[str, Any]):
        """Add inviter information block to container"""
        info_lines = []

        inviter_username = inviter.get('username', 'Unknown')
        inviter_discriminator = inviter.get('discriminator', '0')
        inviter_id = inviter.get('id', 'Unknown')
        inviter_global_name = inviter.get('global_name')

        # Display name and username
        if inviter_global_name:
            info_lines.append(f"**{t('commands.invite.view.friend.display_name', locale=self.locale)}:** {inviter_global_name}")
            info_lines.append(f"**{t('commands.invite.view.friend.username', locale=self.locale)}:** @{inviter_username}")
        else:
            if inviter_discriminator != '0':
                username_display = f"{inviter_username}#{inviter_discriminator}"
            else:
                username_display = f"@{inviter_username}"
            info_lines.append(f"**{t('commands.invite.view.friend.username', locale=self.locale)}:** {username_display}")

        # User ID
        info_lines.append(f"**{t('commands.invite.view.friend.id', locale=self.locale)}:** `{inviter_id}`")

        # Public flags (badges)
        public_flags = inviter.get('public_flags', 0)
        if public_flags > 0:
            badges = self._get_user_badges(public_flags)
            if badges:
                info_lines.append(f"**Badges:** {badges}")

        # Banner color
        banner_color = inviter.get('banner_color')
        accent_color = inviter.get('accent_color')
        if banner_color:
            info_lines.append(f"**Banner Color:** `{banner_color}`")
        elif accent_color:
            hex_color = f"#{accent_color:06x}"
            info_lines.append(f"**Accent Color:** `{hex_color}`")

        # Avatar decoration
        avatar_decoration = inviter.get('avatar_decoration_data')
        if avatar_decoration:
            info_lines.append(f"**Avatar Decoration:** `{avatar_decoration.get('sku_id', 'Unknown')}`")

        # Collectibles (nameplate)
        collectibles = inviter.get('collectibles', {})
        nameplate = collectibles.get('nameplate')
        if nameplate:
            nameplate_label = nameplate.get('label', 'Unknown')
            # Clean up the label
            clean_label = nameplate_label.replace('COLLECTIBLES_NAMEPLATES_', '').replace('_A11Y', '').replace('_', ' ').title()
            info_lines.append(f"**Nameplate:** `{clean_label}` ({nameplate.get('palette', 'default')})")

        container.add_item(ui.TextDisplay("\n".join(info_lines)))

        # Image links (avatar, banner)
        image_links = []
        avatar_hash = inviter.get('avatar')
        banner_hash = inviter.get('banner')

        if avatar_hash:
            ext = "gif" if avatar_hash.startswith("a_") else "png"
            avatar_url = f"https://cdn.discordapp.com/avatars/{inviter_id}/{avatar_hash}.{ext}?size=512"
            image_links.append(f"[Avatar]({avatar_url})")

        if banner_hash:
            ext = "gif" if banner_hash.startswith("a_") else "png"
            banner_url = f"https://cdn.discordapp.com/banners/{inviter_id}/{banner_hash}.{ext}?size=512"
            image_links.append(f"[Banner]({banner_url})")

        if image_links:
            container.add_item(ui.TextDisplay(
                f"**{t('commands.invite.view.guild.images', locale=self.locale)}:** {' | '.join(image_links)}"
            ))

    def _get_channel_type_name(self, channel_type: int) -> str:
        """Get human-readable channel type name"""
        channel_types = {
            0: "Text",
            1: "DM",
            2: "Voice",
            3: "Group DM",
            4: "Category",
            5: "Announcement",
            10: "News Thread",
            11: "Public Thread",
            12: "Private Thread",
            13: "Stage Voice",
            14: "Directory",
            15: "Forum",
            16: "Media"
        }
        return channel_types.get(channel_type, f"Unknown ({channel_type})")

    def _get_user_badges(self, flags: int) -> str:
        """Get user badges from public flags"""
        badges = []
        flag_names = {
            1 << 0: "Discord Staff",
            1 << 1: "Partner",
            1 << 2: "HypeSquad Events",
            1 << 3: "Bug Hunter Level 1",
            1 << 6: "HypeSquad Bravery",
            1 << 7: "HypeSquad Brilliance",
            1 << 8: "HypeSquad Balance",
            1 << 9: "Early Supporter",
            1 << 14: "Bug Hunter Level 2",
            1 << 17: "Verified Bot Developer",
            1 << 18: "Discord Certified Moderator",
            1 << 22: "Active Developer"
        }
        for flag, name in flag_names.items():
            if flags & flag:
                badges.append(f"`{name}`")
        return ", ".join(badges) if badges else ""


class ServerInfoView(BaseView):
    """View to display server information for guild invites"""

    def __init__(self, invite_data: Dict[str, Any], locale: str):
        super().__init__(timeout=180)
        self.invite_data = invite_data
        self.locale = locale

        # Build the view
        self.build_view()

    def build_view(self):
        """Builds the Components V2 view for server information"""
        self.clear_items()

        container = ui.Container()
        guild = self.invite_data.get('guild', {})
        profile = self.invite_data.get('profile', {})
        guild_id = guild.get('id', 'Unknown')

        # Title
        container.add_item(ui.TextDisplay(
            f"### <:server:1464693264773939319> {t('commands.invite.view.server_info.title', locale=self.locale)}"
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Build info list (most important first)
        info_lines = []

        # Guild name and ID (most important)
        guild_name = guild.get('name', 'Unknown')
        info_lines.append(f"**{t('commands.invite.view.guild.name', locale=self.locale)}:** {guild_name}")
        info_lines.append(f"**{t('commands.invite.view.guild.id', locale=self.locale)}:** `{guild_id}`")

        # Vanity URL
        vanity_url = guild.get('vanity_url_code')
        if vanity_url:
            info_lines.append(f"**Vanity URL:** discord.gg/{vanity_url}")

        # Member counts (use profile data or approximate)
        member_count = profile.get('member_count') or self.invite_data.get('approximate_member_count')
        online_count = profile.get('online_count') or self.invite_data.get('approximate_presence_count')
        if member_count is not None:
            info_lines.append(f"**{t('commands.invite.view.guild.members', locale=self.locale)}:** `{member_count:,}` ({t('commands.invite.view.guild.online', locale=self.locale)}: `{online_count:,}`)")

        # Boost info
        premium_tier = guild.get('premium_tier', 0)
        premium_count = guild.get('premium_subscription_count', 0)
        if premium_tier > 0 or premium_count > 0:
            boost_info = f"Level `{premium_tier}` (`{premium_count}` boosts)"
            info_lines.append(f"**{t('commands.invite.view.guild.boost', locale=self.locale)}:** {boost_info}")

        # Verification level
        verification_level = guild.get('verification_level')
        if verification_level is not None:
            info_lines.append(f"**{t('commands.invite.view.guild.verification', locale=self.locale)}:** `{self._get_verification_level(verification_level)}`")

        container.add_item(ui.TextDisplay("\n".join(info_lines)))

        # Guild description (if available, separate block)
        description = guild.get('description') or profile.get('description')
        if description:
            container.add_item(ui.TextDisplay(
                f"**{t('commands.invite.view.guild.description', locale=self.locale)}:**\n-# {description}"
            ))

        # Guild features (if available)
        features = guild.get('features', [])
        if features and len(features) > 0:
            features_display = ', '.join(f"`{f}`" for f in features[:8])
            if len(features) > 8:
                features_display += f" +{len(features) - 8}"
            container.add_item(ui.TextDisplay(
                f"**{t('commands.invite.view.guild.features', locale=self.locale)}:**\n-# {features_display}"
            ))

        # NSFW warning (if applicable)
        nsfw_level = guild.get('nsfw_level', 0)
        is_nsfw = guild.get('nsfw', False)
        if nsfw_level > 0 or is_nsfw:
            container.add_item(ui.TextDisplay(
                f"<:warning:1446108410092195902> **{t('commands.invite.view.guild.nsfw', locale=self.locale)}** (Level: `{nsfw_level}`)"
            ))

        # Server images (icon, banner, splash) as links
        image_links = []
        icon_hash = guild.get('icon') or profile.get('icon_hash')
        banner_hash = guild.get('banner') or profile.get('banner_hash')
        splash_hash = guild.get('splash')

        if icon_hash:
            ext = "gif" if icon_hash.startswith("a_") else "png"
            icon_url = f"https://cdn.discordapp.com/icons/{guild_id}/{icon_hash}.{ext}?size=512"
            image_links.append(f"[Icon]({icon_url})")

        if banner_hash:
            ext = "gif" if banner_hash.startswith("a_") else "png"
            banner_url = f"https://cdn.discordapp.com/banners/{guild_id}/{banner_hash}.{ext}?size=512"
            image_links.append(f"[Banner]({banner_url})")

        if splash_hash:
            splash_url = f"https://cdn.discordapp.com/splashes/{guild_id}/{splash_hash}.png?size=512"
            image_links.append(f"[Splash]({splash_url})")

        if image_links:
            container.add_item(ui.TextDisplay(
                f"**{t('commands.invite.view.guild.images', locale=self.locale)}:** {' | '.join(image_links)}"
            ))

        self.add_item(container)

        # Add back button (outside container, below)
        button_row = ui.ActionRow()
        back_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str("<:back:1401600847733067806>"),
            label=t('commands.invite.view.server_info.back', locale=self.locale),
            style=discord.ButtonStyle.secondary
        )
        back_btn.callback = self.on_back
        button_row.add_item(back_btn)
        self.add_item(button_row)

    async def on_back(self, interaction: discord.Interaction):
        """Go back to invite info view"""
        invite_view = InviteView(self.invite_data, self.locale)
        await interaction.response.edit_message(view=invite_view)

    def _get_verification_level(self, level: int) -> str:
        """Get verification level name"""
        levels = {
            0: "None",
            1: "Low",
            2: "Medium",
            3: "High",
            4: "Very High"
        }
        return levels.get(level, f"Unknown ({level})")


class Invite(commands.Cog):
    """Invite lookup command system"""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="invite",
        description="Lookup information about a Discord invite"
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        invite_code="The Discord invite code (e.g., 'discord' or 'https://discord.gg/discord')",
        incognito="Make response visible only to you"
    )
    async def invite_command(
        self,
        interaction: discord.Interaction,
        invite_code: str,
        incognito: Optional[bool] = None
    ):
        """Lookup a Discord invite"""
        # Get the ephemeral mode
        if incognito is None and self.bot.db:
            try:
                user_pref = await self.bot.db.get_attribute('user', interaction.user.id, 'DEFAULT_INCOGNITO')
                ephemeral = True if user_pref is None else user_pref
            except:
                ephemeral = True
        else:
            ephemeral = incognito if incognito is not None else True

        # Get the user's locale
        locale = i18n.get_user_locale(interaction)

        # Extract invite code from URL if needed
        code = self._extract_invite_code(invite_code)

        # Send loading message
        loading_msg = t("commands.invite.loading", locale=locale)
        await interaction.response.send_message(loading_msg, ephemeral=ephemeral)

        # Fetch invite data from Discord API
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "User-Agent": "DiscordBot (Moddy, 1.0)"
                }

                # Get invite data with counts and expiration
                url = f"https://discord.com/api/v10/invites/{code}?with_counts=true&with_expiration=true"

                async with session.get(url, headers=headers) as resp:
                    if resp.status == 404:
                        error_msg = t("commands.invite.errors.not_found", locale=locale, code=code)
                        await interaction.edit_original_response(content=error_msg)
                        return
                    elif resp.status == 429:
                        error_msg = t("commands.invite.errors.rate_limit", locale=locale)
                        await interaction.edit_original_response(content=error_msg)
                        return
                    elif resp.status != 200:
                        error_msg = t("commands.invite.errors.api_error", locale=locale, status=resp.status)
                        await interaction.edit_original_response(content=error_msg)
                        return

                    invite_data = await resp.json()

        except aiohttp.ClientError as e:
            error_msg = t("commands.invite.errors.network_error", locale=locale)
            await interaction.edit_original_response(content=error_msg)
            return
        except Exception as e:
            error_msg = t("commands.invite.errors.generic", locale=locale, error=str(e))
            await interaction.edit_original_response(content=error_msg)
            return

        # Create the view with invite data
        view = InviteView(invite_data, locale)

        # Send response with Components V2
        await interaction.edit_original_response(
            content=None,
            view=view
        )

    def _extract_invite_code(self, invite_input: str) -> str:
        """Extract invite code from various formats"""
        # Remove whitespace
        invite_input = invite_input.strip()

        # If it's a full URL, extract the code
        if 'discord.gg/' in invite_input:
            code = invite_input.split('discord.gg/')[-1]
        elif 'discordapp.com/invite/' in invite_input:
            code = invite_input.split('discordapp.com/invite/')[-1]
        elif 'discord.com/invite/' in invite_input:
            code = invite_input.split('discord.com/invite/')[-1]
        else:
            # Assume it's just the code
            code = invite_input

        # Remove query parameters and fragments
        code = code.split('?')[0].split('#')[0]

        return code


async def setup(bot):
    await bot.add_cog(Invite(bot))
