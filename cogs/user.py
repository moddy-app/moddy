"""
User command for Moddy
Display detailed information about a Discord user
"""

import discord
from discord import app_commands, ui
from cogs.error_handler import BaseView
from discord.ext import commands
from typing import Optional
import aiohttp
from datetime import datetime
from config import COLORS
from utils.emojis import (
    EMOJIS, DISCORD_BADGES, MODDY_BADGES, AUTO_MODDY_BADGES,
    MINI_VERIFIED, CERTIF_BADGE, USER as USER_ICON, BANNER as BANNER_ICON, TEXT,
    VERIFIED_ORG, DOCS_VERIFIED_URL,
    get_user_verification_badge, format_verification_badge,
)
from utils.i18n import i18n

def _format_org_names(orgs: list, locale: str) -> str:
    """Format a list of org names into a readable string with locale-aware joining."""
    if not orgs:
        return ""
    bold = [f"**{o}**" for o in orgs]
    sep = " et " if locale == "fr" else " and "
    if len(bold) == 1:
        return bold[0]
    if len(bold) == 2:
        return f"{bold[0]}{sep}{bold[1]}"
    return ", ".join(bold[:-1]) + sep + bold[-1]


# Discord badge support article URLs (locale-specific)
DISCORD_BADGE_URLS = {
    "fr": "https://support.discord.com/hc/fr/articles/360035962891-Le-b-a-ba-des-Badges-de-Profil",
    "en-US": "https://support.discord.com/hc/en-us/articles/360035962891-Profile-Badges-101"
}


class UserInfoView(BaseView):
    """View for displaying user information using Components V2"""

    def __init__(self, user_data: dict, bot_data: Optional[dict], moddy_attributes: dict, locale: str, author_id: int, bot, user_verification_data: dict = None):
        super().__init__(timeout=180)
        self.user_data = user_data
        self.bot_data = bot_data
        self.moddy_attributes = moddy_attributes
        self.locale = locale
        self.author_id = author_id
        self.bot = bot
        self.user_verification_data = user_verification_data or {}

        # Build the view
        self.build_view()

    def build_view(self):
        """Builds the Components V2 view with user information"""
        # Clear existing items
        self.clear_items()

        # Create main container
        container = ui.Container()

        # Get user info
        user_id = self.user_data.get("id", "Unknown")
        username = self.user_data.get("username", "Unknown")
        discriminator = self.user_data.get("discriminator", "0")
        global_name = self.user_data.get("global_name", username)
        is_bot = self.user_data.get("bot", False)

        # Determine verification badge (3-tier system)
        verification_badge, org_names, tier = get_user_verification_badge(
            self.user_data, self.moddy_attributes, self.user_verification_data
        )
        badge_link = format_verification_badge(verification_badge)

        # Build title — badge is a hyperlink appended after the bold name
        title_name = username if is_bot else (self.user_data.get("global_name") or username)
        title = i18n.get(
            "commands.user.view.title",
            locale=self.locale,
            name=title_name,
            badge=badge_link
        )

        # Build avatar thumbnail URL
        avatar_hash = self.user_data.get("avatar")
        if avatar_hash:
            ext = "gif" if avatar_hash.startswith("a_") else "png"
            avatar_thumbnail_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.{ext}?size=256"
        else:
            default_index = (int(user_id) >> 22) % 6
            avatar_thumbnail_url = f"https://cdn.discordapp.com/embed/avatars/{default_index}.png"

        container.add_item(ui.Section(
            ui.TextDisplay(title),
            accessory=ui.Thumbnail(media=avatar_thumbnail_url)
        ))

        # Build info block with quotes
        info_lines = []

        # Display name (only for non-bots)
        if not is_bot:
            display_name_label = i18n.get("commands.user.view.display_name", locale=self.locale)
            info_lines.append(f"> **{display_name_label}:** `{global_name}`")

        # Username
        username_label = i18n.get("commands.user.view.username", locale=self.locale)
        if discriminator != "0":
            info_lines.append(f"> **{username_label}:** `{username}#{discriminator}`")
        else:
            info_lines.append(f"> **{username_label}:** `@{username}`")

        # ID
        id_label = i18n.get("commands.user.view.id", locale=self.locale)
        info_lines.append(f"> **{id_label}:** `{user_id}`")

        # Discord badges
        discord_badges = self._get_discord_badges()
        if discord_badges:
            badges_str = "".join(discord_badges)  # No spaces between Discord badges
            # Get locale-specific badge URL
            badge_url = DISCORD_BADGE_URLS.get(self.locale, DISCORD_BADGE_URLS.get("en-US"))
            badges_label = i18n.get("commands.user.view.badges", locale=self.locale)
            info_lines.append(f"> **{badges_label}:** [{badges_str}]({badge_url})")

        # Moddy badges (avec -# pour griser)
        moddy_badges = self._get_moddy_badges()
        if moddy_badges:
            badges_str = " ".join(moddy_badges)
            moddy_badges_label = i18n.get("commands.user.view.moddy_badges", locale=self.locale)
            info_lines.append(f"-# > **{moddy_badges_label}:** {badges_str}")

        # Account creation date
        try:
            snowflake_id = int(user_id)
            timestamp = ((snowflake_id >> 22) + 1420070400000) // 1000
            created_label = i18n.get("commands.user.view.created", locale=self.locale)
            info_lines.append(f"> **{created_label}:** <t:{timestamp}:R>")
        except:
            pass

        # Banner color
        banner_color = self.user_data.get("banner_color")
        if banner_color:
            banner_color_label = i18n.get("commands.user.view.banner_color", locale=self.locale)
            info_lines.append(f"> **{banner_color_label}:** `{banner_color}`")

        # Clan
        clan = self.user_data.get("clan")
        if clan:
            clan_tag = clan.get("tag", "")
            if clan_tag:
                clan_label = i18n.get("commands.user.view.clan", locale=self.locale)
                info_lines.append(f"> **{clan_label}:** `{clan_tag}`")

        # Avatar decoration
        avatar_decoration = self.user_data.get("avatar_decoration_data")
        if avatar_decoration:
            sku_id = avatar_decoration.get("sku_id")
            if sku_id:
                avatar_decoration_label = i18n.get("commands.user.view.avatar_decoration", locale=self.locale)
                info_lines.append(f"> **{avatar_decoration_label}:** [`{sku_id}`](https://discord.com/shop#itemSkuId={sku_id})")

        # Profile decoration
        collectibles = self.user_data.get("collectibles")
        if collectibles and isinstance(collectibles, dict):
            nameplate = collectibles.get("nameplate")
            if nameplate:
                nameplate_sku = nameplate.get("sku_id")
                if nameplate_sku:
                    nameplate_label = i18n.get("commands.user.view.nameplate", locale=self.locale)
                    info_lines.append(f"> **{nameplate_label}:** [`{nameplate_sku}`](https://discord.com/shop#itemSkuId={nameplate_sku})")

        # Bot status
        bot_emoji = EMOJIS.get("done") if is_bot else EMOJIS.get("undone")
        bot_label = i18n.get("commands.user.view.bot", locale=self.locale)
        info_lines.append(f"> **{bot_label}:** {bot_emoji}")

        # Spammer detection (bit 20 of flags)
        flags = self.user_data.get("flags", 0)
        is_spammer = bool(flags & (1 << 20))
        spammer_emoji = EMOJIS.get("done") if is_spammer else EMOJIS.get("undone")
        spammer_label = i18n.get("commands.user.view.spammer", locale=self.locale)
        info_lines.append(f"> **{spammer_label}:** {spammer_emoji}")

        # Provisional account detection (bit 23 of flags)
        is_provisional = bool(flags & (1 << 23))
        provisional_emoji = EMOJIS.get("done") if is_provisional else EMOJIS.get("undone")
        provisional_label = i18n.get("commands.user.view.provisional_account", locale=self.locale)
        info_lines.append(f"> **{provisional_label}:** {provisional_emoji}")

        # Add all info lines to container
        container.add_item(ui.TextDisplay("\n".join(info_lines)))

        # Add affiliation / verification notices
        notices = []
        show_learn_more = False

        if tier == "org_member":
            auto_orgs = [o for o in org_names if o in ("Discord", "Moddy Team")]
            custom_orgs = [o for o in org_names if o not in ("Discord", "Moddy Team")]

            for org in auto_orgs:
                if org == "Discord":
                    text = i18n.get("commands.user.view.discord_employee_notice", locale=self.locale)
                else:
                    text = i18n.get("commands.user.view.moddy_team_notice", locale=self.locale)
                notices.append(f"-# {MINI_VERIFIED} {text}")

            if custom_orgs:
                formatted = _format_org_names(custom_orgs, self.locale)
                text = i18n.get("commands.user.view.verified_org_member_notice", locale=self.locale, org_name=formatted)
                date_attr = self._get_badge_date("VERIFIED_ORG_MEMBER")
                if date_attr:
                    date_text = i18n.get("commands.user.view.verified_date", locale=self.locale, date=f"<t:{date_attr}:D>")
                    notices.append(f"-# {MINI_VERIFIED} {text} • {date_text}")
                else:
                    notices.append(f"-# {MINI_VERIFIED} {text}")
                show_learn_more = True
            elif not auto_orgs:
                # VERIFIED_ORG_MEMBER set but no org configured
                text = i18n.get("commands.user.view.verified_org_member_no_org_notice", locale=self.locale)
                date_attr = self._get_badge_date("VERIFIED_ORG_MEMBER")
                if date_attr:
                    date_text = i18n.get("commands.user.view.verified_date", locale=self.locale, date=f"<t:{date_attr}:D>")
                    notices.append(f"-# {MINI_VERIFIED} {text} • {date_text}")
                else:
                    notices.append(f"-# {MINI_VERIFIED} {text}")
                show_learn_more = True

        elif tier == "verified_org":
            text = i18n.get("commands.user.view.verified_org_notice", locale=self.locale)
            date_attr = self._get_badge_date("VERIFIED_ORG")
            if date_attr:
                date_text = i18n.get("commands.user.view.verified_date", locale=self.locale, date=f"<t:{date_attr}:D>")
                notices.append(f"-# {MINI_VERIFIED} {text} • {date_text}")
            else:
                notices.append(f"-# {MINI_VERIFIED} {text}")
            show_learn_more = True

        elif tier == "verified":
            date_attr = self._get_badge_date("VERIFIED")
            if date_attr:
                date_text = i18n.get("commands.user.view.verified_date", locale=self.locale, date=f"<t:{date_attr}:D>")
                notices.append(f"-# {MINI_VERIFIED} {date_text}")
            show_learn_more = True

        if show_learn_more:
            learn_more_text = i18n.get("commands.user.view.verified_learn_more", locale=self.locale)
            notices.append(f"-# [{learn_more_text}]({DOCS_VERIFIED_URL})")

        if notices:
            container.add_item(ui.TextDisplay("\n".join(notices)))

        # Add container to view
        self.add_item(container)

        # Add action buttons
        self._add_buttons()

    def _get_badge_date(self, attr_key: str):
        """Return the verification date for a badge, checking data.verification first then legacy attributes."""
        date = (self.user_verification_data.get(attr_key) or {}).get("date")
        if date is not None:
            return str(date)
        return self.moddy_attributes.get(f"{attr_key}_DATE")

    def _get_discord_badges(self) -> list:
        """Get Discord badges for the user"""
        badges = []
        flags = self.user_data.get("public_flags", 0)

        # Check each flag
        if flags & (1 << 0):  # Staff
            badges.append(DISCORD_BADGES.get("staff", ""))
        if flags & (1 << 1):  # Partner
            badges.append(DISCORD_BADGES.get("partner", ""))
        if flags & (1 << 2):  # HypeSquad Events
            badges.append(DISCORD_BADGES.get("hypesquad", ""))
        if flags & (1 << 3):  # Bug Hunter Level 1
            badges.append(DISCORD_BADGES.get("bug_hunter_level_1", ""))
        if flags & (1 << 6):  # House Bravery
            badges.append(DISCORD_BADGES.get("hypesquad_bravery", ""))
        if flags & (1 << 7):  # House Brilliance
            badges.append(DISCORD_BADGES.get("hypesquad_brilliance", ""))
        if flags & (1 << 8):  # House Balance
            badges.append(DISCORD_BADGES.get("hypesquad_balance", ""))
        if flags & (1 << 9):  # Early Supporter
            badges.append(DISCORD_BADGES.get("early_supporter", ""))
        if flags & (1 << 14):  # Bug Hunter Level 2
            badges.append(DISCORD_BADGES.get("bug_hunter_level_2", ""))
        if flags & (1 << 17):  # Verified Bot Developer
            badges.append(DISCORD_BADGES.get("verified_bot_developer", ""))
        if flags & (1 << 22):  # Active Developer
            badges.append(DISCORD_BADGES.get("active_developer", ""))

        return [b for b in badges if b]

    def _get_moddy_badges(self) -> list:
        """Get Moddy badges based on user attributes"""
        badges = []

        # First check auto-assigned badges (TEAM, SUPPORT, VERIFIED)
        for attr_name, badge_emoji in AUTO_MODDY_BADGES.items():
            if self.moddy_attributes.get(attr_name):
                badges.append(badge_emoji)

        # Then check regular badges (but skip if already added via auto-badges)
        for attr_name, badge_emoji in MODDY_BADGES.items():
            if self.moddy_attributes.get(attr_name):
                # Skip MODDYTEAM if TEAM was already added
                if attr_name == "MODDYTEAM" and self.moddy_attributes.get("TEAM"):
                    continue
                # Skip SUPPORTAGENT if SUPPORT was already added
                if attr_name == "SUPPORTAGENT" and self.moddy_attributes.get("SUPPORT"):
                    continue
                # Skip CERTIF if VERIFIED was already added
                if attr_name == "CERTIF" and self.moddy_attributes.get("VERIFIED"):
                    continue
                badges.append(badge_emoji)

        # Check if user should have verified emoji (add Certif badge at the end)
        public_flags = self.user_data.get("public_flags", 0)
        is_discord_staff = bool(public_flags & (1 << 0))
        is_verified_attr = self.moddy_attributes.get("VERIFIED", False)
        is_team_attr = self.moddy_attributes.get("TEAM", False)
        should_show_verified = is_discord_staff or is_verified_attr or is_team_attr

        # Add Certif badge at the end if user has verified emoji and badge not already present
        certif_badge = CERTIF_BADGE
        if should_show_verified and certif_badge not in badges:
            badges.append(certif_badge)

        return badges

    async def _get_server_info(self, guild_id: str) -> dict:
        """Try to get server information from Discord API using widget, preview, or invite"""
        result = {}

        async with aiohttp.ClientSession() as session:
            # Try 1: Guild Widget (public, no auth needed)
            try:
                async with session.get(f"https://discord.com/api/v10/guilds/{guild_id}/widget.json") as resp:
                    if resp.status == 200:
                        widget_data = await resp.json()
                        result["name"] = widget_data.get("name")
                        if "instant_invite" in widget_data and widget_data["instant_invite"]:
                            # Extract invite code from full URL
                            invite_url = widget_data["instant_invite"]
                            if "discord.gg/" in invite_url:
                                result["invite_code"] = invite_url.split("discord.gg/")[-1]
                        return result
            except:
                pass

            # Try 2: Guild Preview (requires bot token, works for Discovery servers)
            try:
                headers = {
                    "Authorization": f"Bot {self.bot.http.token}",
                    "User-Agent": "DiscordBot (Moddy, 1.0)"
                }
                async with session.get(f"https://discord.com/api/v10/guilds/{guild_id}/preview", headers=headers) as resp:
                    if resp.status == 200:
                        preview_data = await resp.json()
                        result["name"] = preview_data.get("name")
                        return result
            except:
                pass

        return result

    def _add_buttons(self):
        """Add action buttons to the view"""
        button_row = ui.ActionRow()

        # Bot Info button (only if user is a bot)
        if self.bot_data:
            bot_info_btn = ui.Button(
                label=i18n.get("commands.user.buttons.bot_info", locale=self.locale),
                style=discord.ButtonStyle.primary,
                emoji="<:extension:1439692401760272435>",
                custom_id="bot_info"
            )
            bot_info_btn.callback = self.on_bot_info_click
            button_row.add_item(bot_info_btn)

        # Avatar button
        avatar_btn = ui.Button(
            label=i18n.get("commands.user.buttons.avatar", locale=self.locale),
            style=discord.ButtonStyle.secondary,
            emoji="<:face:1439042029198770289>",
            custom_id="avatar"
        )
        avatar_btn.callback = self.on_avatar_click
        button_row.add_item(avatar_btn)

        # Banner button
        if self.user_data.get("banner"):
            banner_btn = ui.Button(
                label=i18n.get("commands.user.buttons.banner", locale=self.locale),
                style=discord.ButtonStyle.secondary,
                emoji="<:banner:1439659080472989726>",
                custom_id="banner"
            )
            banner_btn.callback = self.on_banner_click
            button_row.add_item(banner_btn)

        # Description button (only if bot with description)
        if self.bot_data:
            description = self.bot_data.get("description", "")
            if description:
                desc_btn = ui.Button(
                    label=i18n.get("commands.user.buttons.description", locale=self.locale),
                    style=discord.ButtonStyle.secondary,
                    emoji="<:text:1439692405317046372>",
                    custom_id="description"
                )
                desc_btn.callback = self.on_description_click
                button_row.add_item(desc_btn)

            # Add bot button (only if user is a bot) - positioned at the right
            add_bot_btn = ui.Button(
                label=i18n.get("commands.user.buttons.add_bot", locale=self.locale),
                style=discord.ButtonStyle.link,
                url=f"https://discord.com/oauth2/authorize?client_id={self.user_data.get('id')}"
            )
            button_row.add_item(add_bot_btn)

        self.add_item(button_row)

    async def on_bot_info_click(self, interaction: discord.Interaction):
        """Handle Bot Info button click"""
        # Check if the user is the author
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                i18n.get("commands.user.errors.author_only", locale=self.locale),
                ephemeral=True
            )
            return

        # Create bot info view with Components V2
        bot_view = ui.LayoutView()
        bot_container = ui.Container()

        # Title
        bot_name = self.bot_data.get("name", "Unknown")
        title = f"### <:extension:1439692401760272435> Bot Information - **{bot_name}**"
        bot_container.add_item(ui.TextDisplay(title))

        # Build info lines
        info_lines = []

        # Bot ID
        info_lines.append(f"> **Bot ID:** `{self.user_data.get('id', 'Unknown')}`")

        # Server count
        if "approximate_guild_count" in self.bot_data:
            server_count = f"{self.bot_data['approximate_guild_count']:,}"
            info_lines.append(f"> **Server Count:** `{server_count}`")

        # Is public
        is_public = self.bot_data.get("bot_public", False)
        public_emoji = EMOJIS.get("done") if is_public else EMOJIS.get("undone")
        info_lines.append(f"> **Public Bot:** {public_emoji}")

        # Is verified
        is_verified = self.bot_data.get("is_verified", False)
        verified_emoji = EMOJIS.get("done") if is_verified else EMOJIS.get("undone")
        info_lines.append(f"> **Verified Bot:** {verified_emoji}")

        # Support server - try to get server info from Discord API
        guild_id = self.bot_data.get("guild_id")
        if guild_id:
            # Try to fetch guild widget or preview info
            server_info = await self._get_server_info(guild_id)
            if server_info:
                server_name = server_info.get("name", "")
                invite_code = server_info.get("invite_code", "")
                if invite_code:
                    info_lines.append(f"> **Support Server:** [{server_name}](https://discord.gg/{invite_code})" if server_name else f"> **Support Server:** https://discord.gg/{invite_code}")
                else:
                    info_lines.append(f"> **Support Server:** {server_name} (ID: `{guild_id}`)" if server_name else f"> **Support Server ID:** `{guild_id}`")
            else:
                info_lines.append(f"> **Support Server ID:** `{guild_id}`")

        # HTTP interactions
        hook = self.bot_data.get("hook", False)
        http_emoji = EMOJIS.get("done") if hook else EMOJIS.get("undone")
        info_lines.append(f"> **HTTP Interactions:** {http_emoji}")

        # Global commands (check integration types)
        integration_config = self.bot_data.get("integration_types_config", {})
        has_global = "1" in integration_config  # User install
        global_emoji = EMOJIS.get("done") if has_global else EMOJIS.get("undone")
        info_lines.append(f"> **Global Commands:** {global_emoji}")

        # Intents (based on flags)
        flags = self.bot_data.get("flags", 0)
        intents = []
        if flags & (1 << 12):  # GATEWAY_PRESENCE
            intents.append("Presence")
        if flags & (1 << 14):  # GATEWAY_GUILD_MEMBERS
            intents.append("Guild Members")
        if flags & (1 << 18):  # GATEWAY_MESSAGE_CONTENT
            intents.append("Message Content")

        if intents:
            intents_text = ", ".join(intents)
            info_lines.append(f"> **Intents:** `{intents_text}`")

        # Terms of Service and Privacy Policy
        tos = self.bot_data.get("terms_of_service_url")
        if tos:
            info_lines.append(f"> **Terms of Service:** {tos}")

        privacy = self.bot_data.get("privacy_policy_url")
        if privacy:
            info_lines.append(f"> **Privacy Policy:** {privacy}")

        # Tags
        tags = self.bot_data.get("tags", [])
        if tags:
            tags_str = ", ".join([f"`{tag}`" for tag in tags[:5]])
            info_lines.append(f"> **Tags:** {tags_str}")

        # Add all info to container
        bot_container.add_item(ui.TextDisplay("\n".join(info_lines)))

        bot_view.add_item(bot_container)

        await interaction.response.send_message(view=bot_view, ephemeral=True)

    async def on_description_click(self, interaction: discord.Interaction):
        """Handle Description button click"""
        # Check if the user is the author
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                i18n.get("commands.user.errors.author_only", locale=self.locale),
                ephemeral=True
            )
            return

        # Get bot description
        description = self.bot_data.get("description", "")
        if not description:
            await interaction.response.send_message(
                i18n.get("commands.user.errors.no_description", locale=self.locale),
                ephemeral=True
            )
            return

        # Create description view
        desc_view = ui.LayoutView()
        desc_container = ui.Container()
        desc_container.add_item(ui.TextDisplay(f"### {TEXT} Description"))
        desc_container.add_item(ui.TextDisplay(f"```{description}```"))
        desc_view.add_item(desc_container)

        await interaction.response.send_message(view=desc_view, ephemeral=True)

    async def on_avatar_click(self, interaction: discord.Interaction):
        """Handle Avatar button click"""
        # Check if the user is the author
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                i18n.get("commands.user.errors.author_only", locale=self.locale),
                ephemeral=True
            )
            return

        user_id = self.user_data.get("id")
        avatar_hash = self.user_data.get("avatar")

        if not avatar_hash:
            await interaction.response.send_message(
                i18n.get("commands.user.errors.no_avatar", locale=self.locale),
                ephemeral=True
            )
            return

        # Build avatar URL
        extension = "gif" if avatar_hash.startswith("a_") else "png"
        avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.{extension}?size=1024"

        # Create Components V2 view with MediaGallery
        badge, _, _ = get_user_verification_badge(self.user_data, self.moddy_attributes, self.user_verification_data)
        badge_link = format_verification_badge(badge)
        display_name = self.user_data.get("global_name") or self.user_data.get("username", "Unknown")
        avatar_title = i18n.get(
            "commands.avatar.view.title",
            locale=self.locale,
            name=display_name,
            badge=badge_link
        )
        avatar_view = ui.LayoutView()
        avatar_container = ui.Container(
            ui.TextDisplay(avatar_title),
            ui.MediaGallery(
                discord.MediaGalleryItem(media=avatar_url)
            ),
            ui.TextDisplay(f"**Download:** [256]({avatar_url}?size=256) • [512]({avatar_url}?size=512) • [1024]({avatar_url}?size=1024) • [2048]({avatar_url}?size=2048)")
        )

        avatar_view.add_item(avatar_container)
        await interaction.response.send_message(view=avatar_view, ephemeral=True)

    async def on_banner_click(self, interaction: discord.Interaction):
        """Handle Banner button click"""
        # Check if the user is the author
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                i18n.get("commands.user.errors.author_only", locale=self.locale),
                ephemeral=True
            )
            return

        user_id = self.user_data.get("id")
        banner_hash = self.user_data.get("banner")

        if not banner_hash:
            await interaction.response.send_message(
                i18n.get("commands.user.errors.no_banner", locale=self.locale),
                ephemeral=True
            )
            return

        # Build banner URL
        extension = "gif" if banner_hash.startswith("a_") else "png"
        banner_url = f"https://cdn.discordapp.com/banners/{user_id}/{banner_hash}.{extension}?size=1024"

        # Create Components V2 view with MediaGallery
        badge, _, _ = get_user_verification_badge(self.user_data, self.moddy_attributes, self.user_verification_data)
        badge_link = format_verification_badge(badge)
        display_name = self.user_data.get("global_name") or self.user_data.get("username", "Unknown")
        banner_title_text = i18n.get(
            "commands.banner.view.title",
            locale=self.locale,
            name=display_name,
            badge=badge_link
        )
        banner_view = ui.LayoutView()
        banner_container = ui.Container(
            ui.TextDisplay(f"### {BANNER_ICON} {banner_title_text}"),
            ui.MediaGallery(
                discord.MediaGalleryItem(media=banner_url)
            ),
            ui.TextDisplay(f"**Download:** [256]({banner_url}?size=256) • [512]({banner_url}?size=512) • [1024]({banner_url}?size=1024) • [2048]({banner_url}?size=2048)")
        )

        banner_view.add_item(banner_container)
        await interaction.response.send_message(view=banner_view, ephemeral=True)


class User(commands.Cog):
    """User information command"""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="user",
        description="Display detailed information about a Discord user"
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        user="The user to lookup",
        incognito="Make response visible only to you"
    )
    async def user_command(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        incognito: Optional[bool] = None
    ):
        """Display user information"""

        # === BLOC INCOGNITO ===
        if incognito is None and self.bot.db:
            try:
                user_pref = await self.bot.db.get_attribute('user', interaction.user.id, 'DEFAULT_INCOGNITO')
                ephemeral = True if user_pref is None else user_pref
            except:
                ephemeral = True
        else:
            ephemeral = incognito if incognito is not None else True

        # Get locale
        locale = i18n.get_user_locale(interaction)

        user_id = str(user.id)

        # Send loading message
        loading_msg = i18n.get("commands.user.loading", locale=locale)
        await interaction.response.send_message(loading_msg, ephemeral=ephemeral)

        # Fetch user data from Discord API
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bot {self.bot.http.token}",
                "User-Agent": "DiscordBot (Moddy, 1.0)"
            }

            # Get user data
            async with session.get(f"https://discord.com/api/v10/users/{user_id}", headers=headers) as resp:
                if resp.status != 200:
                    error_msg = i18n.get("commands.user.errors.not_found", locale=locale)
                    await interaction.edit_original_response(content=error_msg)
                    return

                user_data = await resp.json()

            # Check if user is a bot
            bot_data = None
            if user_data.get("bot"):
                # Get bot/application data
                async with session.get(f"https://discord.com/api/v10/applications/{user_id}/rpc", headers=headers) as resp:
                    if resp.status == 200:
                        bot_data = await resp.json()

        # Get Moddy attributes and verification data for the user
        moddy_attributes = {}
        user_verification_data = {}
        if self.bot.db:
            try:
                user_db_data = await self.bot.db.get_user(int(user_id))
                if user_db_data:
                    moddy_attributes = user_db_data.get("attributes", {})
                    user_verification_data = (user_db_data.get("data") or {}).get("verification") or {}
            except Exception:
                # If user not in DB, that's okay
                pass

        # Create the view
        view = UserInfoView(
            user_data=user_data,
            bot_data=bot_data,
            moddy_attributes=moddy_attributes,
            locale=locale,
            author_id=interaction.user.id,
            bot=self.bot,
            user_verification_data=user_verification_data
        )

        # Update the message with the view
        await interaction.edit_original_response(content=None, view=view)


async def setup(bot):
    await bot.add_cog(User(bot))
