"""
Translate command for Moddy
Uses the DeepL API via the centralized gateway.
"""

import discord
from discord import app_commands, ui
from cogs.error_handler import BaseView
from discord.ext import commands
from typing import Optional
import re
from datetime import datetime, timedelta
import asyncio

from utils.embeds import ModdyEmbed, ModdyResponse, ModdyColors
from utils.incognito import add_incognito_option, get_incognito_setting
from config import COLORS
from utils.i18n import i18n


class TranslateView(BaseView):
    """View to re-translate into another language using Components V2"""

    def __init__(self, bot, original_text: str, translated_text: str, from_lang: str, current_to_lang: str, locale: str, author: discord.User):
        super().__init__(timeout=120)
        self.bot = bot
        self.original_text = original_text
        self.translated_text = translated_text
        self.from_lang = from_lang
        self.current_to_lang = current_to_lang
        self.locale = locale
        self.author = author

        # Create the container
        self.build_view()

    def build_view(self):
        """Builds the Components V2 view"""
        # Clear existing items
        self.clear_items()

        # Create main container
        container = ui.Container()

        # Add title at the top
        view_title = i18n.get("commands.translate.view.title", locale=self.locale)
        container.add_item(ui.TextDisplay(view_title))

        # Get translator cog for helper functions
        translator = self.bot.get_cog("Translate")
        if translator:
            from_flag = translator.get_language_flag(self.from_lang)
            to_flag = translator.get_language_flag(self.current_to_lang)
            from_name = translator.get_language_name(self.from_lang, self.locale)
            to_name = translator.get_language_name(self.current_to_lang, self.locale)

            # Create title with flags and language names (without translate emoji)
            title = f"``{from_flag} {from_name}`` → ``{to_flag} {to_name}``"

            # Add translation result display
            deepl_text = i18n.get("commands.translate.deepl_attribution", locale=self.locale)
            translation_display = f"{title}\n```\n{self.translated_text}\n```\n-# {deepl_text}"
            container.add_item(ui.TextDisplay(translation_display))

        # Add container to view
        self.add_item(container)

        # Create language select menu outside the container
        select = self.create_select()

        # Create action row for the select (outside container)
        select_row = ui.ActionRow()
        select_row.add_item(select)
        self.add_item(select_row)

    def create_select(self):
        """Creates the language selection menu"""
        options = []

        # Available DeepL languages (most common)
        languages = {
            "EN-US": ("🇺🇸", "English (US)", "Anglais (US)"),
            "EN-GB": ("🇬🇧", "English (UK)", "Anglais (UK)"),
            "FR": ("🇫🇷", "Français", "Français"),
            "DE": ("🇩🇪", "Deutsch", "Allemand"),
            "ES": ("🇪🇸", "Español", "Espagnol"),
            "IT": ("🇮🇹", "Italiano", "Italien"),
            "PT-PT": ("🇵🇹", "Português", "Portugais"),
            "PT-BR": ("🇧🇷", "Português (BR)", "Portugais (BR)"),
            "NL": ("🇳🇱", "Nederlands", "Néerlandais"),
            "PL": ("🇵🇱", "Polski", "Polonais"),
            "RU": ("🇷🇺", "Русский", "Russe"),
            "JA": ("🇯🇵", "日本語", "Japonais"),
            "ZH": ("🇨🇳", "中文", "Chinois"),
            "KO": ("🇰🇷", "한국어", "Coréen"),
            "TR": ("🇹🇷", "Türkçe", "Turc"),
            "SV": ("🇸🇪", "Svenska", "Suédois"),
            "DA": ("🇩🇰", "Dansk", "Danois"),
            "NO": ("🇳🇴", "Norsk", "Norvégien"),
            "FI": ("🇫🇮", "Suomi", "Finnois"),
            "EL": ("🇬🇷", "Ελληνικά", "Grec"),
            "CS": ("🇨🇿", "Čeština", "Tchèque"),
            "RO": ("🇷🇴", "Română", "Roumain"),
            "HU": ("🇭🇺", "Magyar", "Hongrois"),
            "UK": ("🇺🇦", "Українська", "Ukrainien"),
            "BG": ("🇧🇬", "Български", "Bulgare")
        }

        for code, (emoji, name, name_fr) in languages.items():
            # Do not include the current language
            if code != self.current_to_lang:
                # Use French names for French locale, English names for others
                label = name_fr if self.locale == "fr" else name
                options.append(discord.SelectOption(
                    label=label,
                    value=code,
                    emoji=emoji
                ))

        # Limit to 25 options (Discord limit)
        options = options[:25]

        placeholder = i18n.get("commands.translate.view.placeholder", locale=self.locale)

        select = ui.Select(
            placeholder=placeholder,
            options=options,
            min_values=1,
            max_values=1
        )
        select.callback = self.translate_callback

        return select

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Checks that it's the author using the menu"""
        if interaction.user != self.author:
            locale = i18n.get_user_locale(interaction)
            error_msg = i18n.get("commands.translate.view.author_only", locale=locale)
            await interaction.response.send_message(error_msg, ephemeral=True)
            return False
        return True

    async def translate_callback(self, interaction: discord.Interaction):
        """Callback to re-translate the text"""
        new_lang = interaction.data['values'][0]

        await interaction.response.defer()

        translator = self.bot.get_cog("Translate")
        if translator:
            result = await translator.translate_via_gateway(
                self.original_text, new_lang, user_id=interaction.user.id
            )
            if result:
                self.current_to_lang = new_lang
                self.translated_text = result["text"]
                self.build_view()
                await interaction.edit_original_response(view=self)
            else:
                error_msg = i18n.get("common.error", locale=self.locale)
                await interaction.followup.send(error_msg, ephemeral=True)


class Translate(commands.Cog):
    """Translation system using DeepL (via the API gateway)."""

    def __init__(self, bot):
        self.bot = bot
        self.user_usage = {}  # in-memory per-minute rate limiter (20 req/min/user)
        self.max_uses_per_minute = 20

        # Create and add context menu command
        self.translate_message_menu = app_commands.ContextMenu(
            name="Translate",
            callback=self.translate_context_menu_callback
        )
        self.bot.tree.add_command(self.translate_message_menu)

    def get_language_flag(self, code: str) -> str:
        """Gets the flag emoji for a language code"""
        flags = {
            "EN": "🇬🇧",
            "EN-US": "🇺🇸",
            "EN-GB": "🇬🇧",
            "FR": "🇫🇷",
            "DE": "🇩🇪",
            "ES": "🇪🇸",
            "IT": "🇮🇹",
            "PT": "🇵🇹",
            "PT-PT": "🇵🇹",
            "PT-BR": "🇧🇷",
            "NL": "🇳🇱",
            "PL": "🇵🇱",
            "RU": "🇷🇺",
            "JA": "🇯🇵",
            "ZH": "🇨🇳",
            "KO": "🇰🇷",
            "TR": "🇹🇷",
            "SV": "🇸🇪",
            "DA": "🇩🇰",
            "NO": "🇳🇴",
            "FI": "🇫🇮",
            "EL": "🇬🇷",
            "CS": "🇨🇿",
            "RO": "🇷🇴",
            "HU": "🇭🇺",
            "UK": "🇺🇦",
            "BG": "🇧🇬"
        }
        return flags.get(code.upper(), "🌐")

    def get_language_name(self, code: str, locale: str) -> str:
        """Gets the name of a language using i18n"""
        # Convert DeepL code (uppercase) to i18n code (lowercase with proper format)
        # DeepL: EN-US, EN-GB, FR, DE -> i18n: en-US, en-GB, fr, de
        normalized_code = code.lower()

        # Special cases for codes without region
        if normalized_code in ['en', 'fr', 'de', 'es', 'it', 'pt', 'nl', 'pl', 'ru', 'ja', 'zh', 'ko', 'tr', 'sv', 'da', 'no', 'fi', 'el', 'cs', 'ro', 'hu', 'uk', 'bg']:
            # Map to standard codes
            code_mapping = {
                'en': 'en-US',
                'es': 'es-ES',
                'pt': 'pt-PT',
                'zh': 'zh-CN',
                'sv': 'sv-SE'
            }
            normalized_code = code_mapping.get(normalized_code, normalized_code)

        # Try to get the language name from i18n
        lang_name = i18n.get(f"languages.{normalized_code}", locale=locale)

        # If not found (returns [languages.xxx]), return the code itself
        if lang_name.startswith('['):
            return code

        return lang_name

    def sanitize_mentions(self, text: str, guild: Optional[discord.Guild]) -> str:
        """Replaces mentions with non-pinging text"""
        # Replace @everyone and @here
        text = text.replace('@everyone', '@\u200beveryone')
        text = text.replace('@here', '@\u200bhere')

        # Replace user mentions
        user_mention_pattern = r'<@!?(\d+)>'

        def replace_user_mention(match):
            user_id = int(match.group(1))
            if guild:
                member = guild.get_member(user_id)
                if member:
                    return f"@{member.display_name}"
            user = self.bot.get_user(user_id)
            if user:
                return f"@{user.name}"
            return f"@User"

        text = re.sub(user_mention_pattern, replace_user_mention, text)

        # Replace role mentions
        role_mention_pattern = r'<@&(\d+)>'

        def replace_role_mention(match):
            if guild:
                role_id = int(match.group(1))
                role = guild.get_role(role_id)
                if role:
                    return f"@{role.name}"
            return f"@Role"

        text = re.sub(role_mention_pattern, replace_role_mention, text)

        return text

    async def check_rate_limit(self, user_id: int) -> tuple[bool, int]:
        """Checks the 20 uses per minute limit for a user"""
        now = datetime.now()

        # Initialize the list for this user if it doesn't exist
        if user_id not in self.user_usage:
            self.user_usage[user_id] = []

        # Clean uses older than one minute for this user
        cutoff = now - timedelta(minutes=1)
        self.user_usage[user_id] = [timestamp for timestamp in self.user_usage[user_id] if timestamp > cutoff]

        # Clean users who haven't used the command for more than 2 minutes
        users_to_clean = []
        for uid, timestamps in self.user_usage.items():
            if uid != user_id and (not timestamps or max(timestamps) < now - timedelta(minutes=2)):
                users_to_clean.append(uid)
        for uid in users_to_clean:
            del self.user_usage[uid]

        # Check if the user can use the command
        if len(self.user_usage[user_id]) >= self.max_uses_per_minute:
            # Calculate the time until the next possible use
            oldest_use = min(self.user_usage[user_id])
            wait_time = 60 - (now - oldest_use).total_seconds()
            return False, int(wait_time)

        # Add this use for this user
        self.user_usage[user_id].append(now)
        return True, 0

    async def translate_via_gateway(
        self,
        text: str,
        target_lang: str,
        *,
        user_id: int,
    ) -> Optional[dict]:
        """Translate using the gateway. Returns {'text': ..., 'detected_source_language': ...}."""
        try:
            from gateway import QuotaTarget
            return await self.bot.gateway.translation.translate(
                text,
                target_lang,
                quota=[QuotaTarget.user(user_id, "translation")],
                call_type="translation",
                metadata={"user_id": user_id},
            )
        except Exception as exc:
            import logging
            logging.getLogger("moddy.translate").error("Gateway translate failed: %s", exc)
            return None

    def locale_to_deepl_lang(self, locale: str) -> str:
        """Converts a Discord locale to a DeepL language code"""
        # Mapping Discord locale to DeepL target language codes
        locale_mapping = {
            "en-US": "EN-US",
            "en-GB": "EN-GB",
            "fr": "FR",
            "de": "DE",
            "es-ES": "ES",
            "es-419": "ES",
            "it": "IT",
            "pt-BR": "PT-BR",
            "pt": "PT-PT",
            "nl": "NL",
            "pl": "PL",
            "ru": "RU",
            "ja": "JA",
            "zh-CN": "ZH",
            "zh-TW": "ZH",
            "ko": "KO",
            "tr": "TR",
            "sv-SE": "SV",
            "da": "DA",
            "no": "NO",
            "fi": "FI",
            "cs": "CS",
            "el": "EL",
            "bg": "BG",
            "uk": "UK",
            "ro": "RO",
            "hu": "HU"
        }

        # Try exact match first
        if locale in locale_mapping:
            return locale_mapping[locale]

        # Try base language
        base_lang = locale.split('-')[0]
        for key, value in locale_mapping.items():
            if key.startswith(base_lang):
                return value

        # Default to English US
        return "EN-US"

    async def _perform_translation(
        self,
        interaction: discord.Interaction,
        text: str,
        target_lang: Optional[str] = None,
        ephemeral: bool = False
    ):
        """Common translation logic used by both slash command and context menu"""
        # Get the user's locale from Discord
        locale = i18n.get_user_locale(interaction)

        # Check the rate limit (20 per minute per user)
        can_use, remaining = await self.check_rate_limit(interaction.user.id)
        if not can_use:
            error_msg = i18n.get("commands.translate.errors.rate_limit", locale=locale, seconds=remaining)
            error_embed = ModdyResponse.error(i18n.get("common.error", locale=locale), error_msg)
            await interaction.response.send_message(embed=error_embed, ephemeral=True)
            return

        # Check the length of the text
        if len(text) > 3000:
            error_msg = i18n.get("commands.translate.errors.too_long", locale=locale)
            error_embed = ModdyResponse.error(i18n.get("common.error", locale=locale), error_msg)
            await interaction.response.send_message(embed=error_embed, ephemeral=True)
            return

        # Sanitize mentions
        sanitized_text = self.sanitize_mentions(text, interaction.guild)

        # Determine target language
        if target_lang is None:
            # Use user's Discord locale
            target_lang = self.locale_to_deepl_lang(str(interaction.locale))

        # Check that the gateway / DeepL is available
        if not getattr(self.bot, "gateway", None) or not self.bot.gateway.deepl_available():
            error_msg = i18n.get("commands.translate.errors.api_error", locale=locale)
            error_embed = ModdyResponse.error(i18n.get("common.error", locale=locale), error_msg)
            await interaction.response.send_message(embed=error_embed, ephemeral=True)
            return

        # Loading message with animated emoji
        loading_msg = i18n.get("commands.translate.translating", locale=locale)
        await interaction.response.send_message(
            content=f"<a:loading:1395047662092550194> **{loading_msg}**",
            ephemeral=ephemeral
        )

        # Single gateway call — DeepL returns text + detected source language together
        result = await self.translate_via_gateway(
            sanitized_text, target_lang, user_id=interaction.user.id
        )

        if result:
            source_lang = result.get("detected_source_language") or "?"
            translated = result["text"]

            view = TranslateView(
                self.bot,
                sanitized_text,
                translated,
                source_lang,
                target_lang,
                locale,
                interaction.user
            )
            await interaction.edit_original_response(content=None, embed=None, view=view)
        else:
            error_msg = i18n.get("commands.translate.errors.api_error", locale=locale)
            error_embed = ModdyResponse.error(i18n.get("common.error", locale=locale), error_msg)
            await interaction.edit_original_response(embed=error_embed)

    @app_commands.command(
        name="translate",
        description="Translate text to another language"
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        text="The text to translate",
        to="Target language (optional, uses your Discord language by default)",
        incognito="Make response visible only to you"
    )
    @app_commands.choices(to=[
        app_commands.Choice(name="🇺🇸 English (US)", value="EN-US"),
        app_commands.Choice(name="🇬🇧 English (UK)", value="EN-GB"),
        app_commands.Choice(name="🇫🇷 Français", value="FR"),
        app_commands.Choice(name="🇩🇪 Deutsch", value="DE"),
        app_commands.Choice(name="🇪🇸 Español", value="ES"),
        app_commands.Choice(name="🇮🇹 Italiano", value="IT"),
        app_commands.Choice(name="🇵🇹 Português", value="PT-PT"),
        app_commands.Choice(name="🇧🇷 Português (BR)", value="PT-BR"),
        app_commands.Choice(name="🇳🇱 Nederlands", value="NL"),
        app_commands.Choice(name="🇵🇱 Polski", value="PL"),
        app_commands.Choice(name="🇷🇺 Русский", value="RU"),
        app_commands.Choice(name="🇯🇵 日本語", value="JA"),
        app_commands.Choice(name="🇨🇳 中文", value="ZH"),
        app_commands.Choice(name="🇰🇷 한국어", value="KO"),
        app_commands.Choice(name="🇹🇷 Türkçe", value="TR"),
        app_commands.Choice(name="🇸🇪 Svenska", value="SV"),
        app_commands.Choice(name="🇩🇰 Dansk", value="DA"),
        app_commands.Choice(name="🇳🇴 Norsk", value="NO"),
        app_commands.Choice(name="🇫🇮 Suomi", value="FI"),
        app_commands.Choice(name="🇬🇷 Ελληνικά", value="EL"),
        app_commands.Choice(name="🇨🇿 Čeština", value="CS"),
        app_commands.Choice(name="🇷🇴 Română", value="RO"),
        app_commands.Choice(name="🇭🇺 Magyar", value="HU"),
        app_commands.Choice(name="🇺🇦 Українська", value="UK"),
        app_commands.Choice(name="🇧🇬 Български", value="BG")
    ])
    @add_incognito_option()
    async def translate_command(
        self,
        interaction: discord.Interaction,
        text: str,
        to: Optional[app_commands.Choice[str]] = None,
        incognito: Optional[bool] = None
    ):
        """Main translation command"""
        # Get the ephemeral mode
        ephemeral = get_incognito_setting(interaction)

        # Determine target language
        target_lang = to.value if to is not None else None

        # Call the common translation logic
        await self._perform_translation(interaction, text, target_lang, ephemeral)

    async def translate_context_menu_callback(self, interaction: discord.Interaction, message: discord.Message):
        """Context menu command callback to translate a message"""
        # Check if the message has content
        if not message.content or message.content.strip() == "":
            locale = i18n.get_user_locale(interaction)
            error_msg = i18n.get("commands.translate.errors.no_content", locale=locale)
            error_embed = ModdyResponse.error(i18n.get("common.error", locale=locale), error_msg)
            await interaction.response.send_message(embed=error_embed, ephemeral=True)
            return

        # Call the common translation logic (always ephemeral for context menu)
        await self._perform_translation(interaction, message.content, target_lang=None, ephemeral=True)

    async def cog_unload(self):
        """Remove context menu when cog is unloaded"""
        self.bot.tree.remove_command(self.translate_message_menu.name, type=self.translate_message_menu.type)


async def setup(bot):
    await bot.add_cog(Translate(bot))
