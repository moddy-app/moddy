"""
Security & dev utility tools: ``/pwned``, ``/password``, ``/uuid``, ``/hash``,
``/jwt`` and ``/color``. See docs/features/TOOLS_SECURITY.md and the shared
contract in docs/features/TOOLS.md.

* ``/pwned``, ``/password``, ``/uuid`` and ``/jwt`` are always ephemeral and
  have no ``incognito`` option: they deal with secrets.
* ``/pwned`` and ``/jwt`` take their input only from a Modal V2, never from a
  slash option, because ``cogs/command_logger.py`` logs option values.
* Everything is computed on Moddy except the HIBP range lookup of ``/pwned``,
  which only ever receives the first five characters of a SHA-1.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, List, Optional

import discord
from discord import app_commands, ui
from discord.ext import commands

from cogs.error_handler import BaseModal
from services.public_api import PublicAPIError
from tools import color as colors
from tools import generators as gen
from tools import hashing as hs
from tools import jwt_decode as jwtd
from utils import tool_card as tc
from utils import tools_security_views as views
from utils.i18n import i18n, t
from utils.incognito import add_incognito_option, get_incognito_setting

logger = logging.getLogger("moddy.tools_security")

#: Largest file /hash accepts (Discord's free upload limit is 10 MB for most
#: members; 25 MB covers boosted servers and keeps memory bounded).
MAX_HASH_FILE_BYTES = 25 * 1024 * 1024
MAX_HASH_TEXT = 4000
PWNED_PER_MINUTE = 8
HIBP_TIMEOUT = 10.0

# Hashing a big file is CPU work: never on the loop, never more than two at once.
_CPU = asyncio.Semaphore(2)


def api_error(exc: PublicAPIError, tool: str) -> tc.ToolError:
    """Map a :class:`PublicAPIError` to the ``ToolError`` code of the same name."""
    code = exc.code if exc.code in ("timeout", "network", "rate_limited") else "unavailable"
    return tc.ToolError(code, tool=tool)


async def _send_error(interaction: discord.Interaction, exc: tc.ToolError, locale: str) -> None:
    """Expected failure as an ephemeral card (first response or follow-up)."""
    await tc.respond(interaction, view=tc.tool_error_card(exc, locale), ephemeral=True)


# --------------------------------------------------------------------------- #
# Modals (V2)
# --------------------------------------------------------------------------- #


class PwnedModal(BaseModal):
    """The only place a password is typed: never a slash option, never logged."""

    def __init__(self, cog: "ToolsSecurity", locale: str):
        super().__init__(title=t("tools.pwned.modal_title", locale=locale)[:45])
        self.bot = cog.bot
        self.cog = cog
        self.add_item(ui.TextDisplay(t("tools.pwned.modal_help", locale=locale)))
        self.password = ui.Label(
            text=t("tools.pwned.input_label", locale=locale)[:45],
            component=ui.TextInput(style=discord.TextStyle.short, required=True,
                                   min_length=1, max_length=256))
        self.add_item(self.password)

    async def on_submit(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        try:
            self.cog.check_pwned_rate(interaction.user.id)
            await interaction.response.send_message(view=tc.loading_card(locale), ephemeral=True)
            try:
                count = await self.cog.lookup_pwned(self.password.component.value or "")
            except tc.ToolError as exc:
                await interaction.edit_original_response(view=tc.tool_error_card(exc, locale))
                return
        except tc.ToolError as exc:
            await _send_error(interaction, exc, locale)
            return
        await interaction.edit_original_response(view=views.build_pwned_card(count, locale))


class JwtModal(BaseModal):
    def __init__(self, cog: "ToolsSecurity", locale: str):
        super().__init__(title=t("tools.jwt.modal_title", locale=locale)[:45])
        self.bot = cog.bot
        self.add_item(ui.TextDisplay(t("tools.jwt.modal_help", locale=locale)))
        self.token = ui.Label(
            text=t("tools.jwt.input_label", locale=locale)[:45],
            component=ui.TextInput(style=discord.TextStyle.paragraph, required=True,
                                   min_length=1, max_length=jwtd.MAX_TOKEN_LENGTH))
        self.add_item(self.token)

    async def on_submit(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        try:
            decoded = jwtd.decode(self.token.component.value or "")
        except jwtd.JwtError as exc:
            await _send_error(interaction, tc.ToolError(exc.code, tool="jwt"), locale)
            return
        view, files = views.build_jwt_card(decoded, locale)
        await tc.respond(interaction, view=view, ephemeral=True, files=files)


class HashModal(BaseModal):
    """Text and/or file, plus an optional hash to compare with."""

    def __init__(self, cog: "ToolsSecurity", locale: str, *, ephemeral: bool,
                 compare: Optional[str] = None):
        super().__init__(title=t("tools.hash.modal_title", locale=locale)[:45])
        self.bot = cog.bot
        self.cog = cog
        self.ephemeral = ephemeral
        self.add_item(ui.TextDisplay(t("tools.hash.modal_help", locale=locale)))
        self.text = ui.Label(
            text=t("tools.hash.text_label", locale=locale)[:45],
            component=ui.TextInput(
                style=discord.TextStyle.paragraph, required=False, max_length=MAX_HASH_TEXT,
                placeholder=t("tools.hash.text_placeholder", locale=locale)[:100]))
        self.file = ui.Label(
            text=t("tools.hash.file_label", locale=locale)[:45],
            description=t("tools.hash.file_description", locale=locale)[:100],
            component=ui.FileUpload(required=False, min_values=0, max_values=1))
        self.compare = ui.Label(
            text=t("tools.hash.compare_label", locale=locale)[:45],
            component=ui.TextInput(
                style=discord.TextStyle.short, required=False, max_length=256,
                default=(compare or None),
                placeholder=t("tools.hash.compare_placeholder", locale=locale)[:100]))
        for item in (self.text, self.file, self.compare):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        files = list(self.file.component.values or [])
        await self.cog.run_hash(
            interaction, text=self.text.component.value or None,
            attachment=files[0] if files else None,
            compare=self.compare.component.value or None, ephemeral=self.ephemeral)


# --------------------------------------------------------------------------- #
# Cog
# --------------------------------------------------------------------------- #


class ToolsSecurity(commands.Cog):
    """/pwned /password /uuid /hash /jwt /color"""

    def __init__(self, bot):
        self.bot = bot
        self._pwned_usage: Dict[int, List[float]] = {}

    # ------------------------------------------------------------------ #
    # /pwned
    # ------------------------------------------------------------------ #

    @app_commands.command(name="pwned",
                          description="Check if a password has appeared in a data breach")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def pwned_command(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        await interaction.response.send_modal(PwnedModal(self, locale))

    def check_pwned_rate(self, user_id: int) -> None:
        """At most :data:`PWNED_PER_MINUTE` lookups a minute per member."""
        now = time.monotonic()
        stamps = [s for s in self._pwned_usage.get(user_id, []) if now - s < 60]
        if len(stamps) >= PWNED_PER_MINUTE:
            wait = int(60 - (now - stamps[0])) + 1
            self._pwned_usage[user_id] = stamps
            raise tc.ToolError("cooldown", tool="pwned", retry=f"{wait} s")
        stamps.append(now)
        self._pwned_usage[user_id] = stamps
        if len(self._pwned_usage) > 2048:  # keep the dict small
            self._pwned_usage = {u: s for u, s in self._pwned_usage.items()
                                 if s and now - s[-1] < 60}

    async def lookup_pwned(self, password: str) -> int:
        """Times ``password`` appears in known breaches (k-anonymity range query).

        Only the 5-character SHA-1 prefix leaves Moddy; the password itself is
        hashed here, never stored, cached or logged.
        """
        api = getattr(self.bot, "public_api", None)
        if api is None:
            raise tc.ToolError("unavailable", tool="pwned")
        prefix, suffix = hs.sha1_parts(password)
        try:
            response = await api.request(
                "GET", hs.HIBP_RANGE_URL + prefix, headers={"Add-Padding": "true"},
                timeout=HIBP_TIMEOUT, max_bytes=2 * 1024 * 1024)
        except PublicAPIError as exc:
            raise api_error(exc, "pwned") from None
        return hs.parse_hibp_range(response.text(), suffix)

    # ------------------------------------------------------------------ #
    # /password
    # ------------------------------------------------------------------ #

    @app_commands.command(name="password",
                          description="Generate a secure random password or passphrase")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        length="Length (8-128). Passphrase: about 1 word per 4 characters, 4 to 10 words",
        type="Kind of password (default: strong)")
    @app_commands.choices(type=[
        app_commands.Choice(name="Strong", value=gen.KIND_STRONG),
        app_commands.Choice(name="No look-alikes", value=gen.KIND_CLEAR),
        app_commands.Choice(name="Passphrase", value=gen.KIND_PASSPHRASE),
    ])
    async def password_command(self, interaction: discord.Interaction,
                               length: app_commands.Range[int, gen.MIN_LENGTH, gen.MAX_LENGTH] = gen.DEFAULT_LENGTH,
                               type: Optional[app_commands.Choice[str]] = None):
        locale = i18n.get_user_locale(interaction)
        kind = type.value if type else gen.KIND_STRONG
        view = views.build_password_card(gen.generate_password(kind, length), kind, length, locale)
        await tc.respond(interaction, view=view, ephemeral=True)

    # ------------------------------------------------------------------ #
    # /uuid
    # ------------------------------------------------------------------ #

    @app_commands.command(name="uuid", description="Generate random UUIDs (v4 or v7)")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(version="UUID version (default: v4)",
                           count="How many to generate (1-10, default 1)")
    @app_commands.choices(version=[
        app_commands.Choice(name="v4 (random)", value="v4"),
        app_commands.Choice(name="v7 (time-ordered)", value="v7"),
    ])
    async def uuid_command(self, interaction: discord.Interaction,
                           version: Optional[app_commands.Choice[str]] = None,
                           count: app_commands.Range[int, gen.MIN_COUNT, gen.MAX_COUNT] = 1):
        locale = i18n.get_user_locale(interaction)
        chosen = version.value if version else "v4"
        view = views.build_uuid_card(gen.generate_uuids(chosen, count), chosen, locale)
        await tc.respond(interaction, view=view, ephemeral=True)

    # ------------------------------------------------------------------ #
    # /hash
    # ------------------------------------------------------------------ #

    @app_commands.command(name="hash",
                          description="Compute MD5, SHA-1, SHA-256 and SHA-512 of a text or file")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        text="Text to hash (leave text and file empty to open a form)",
        file="File to hash (max 25 MB)",
        compare="A known hash to compare with",
        incognito="Make response visible only to you")
    @add_incognito_option()
    async def hash_command(self, interaction: discord.Interaction,
                           text: Optional[app_commands.Range[str, 1, MAX_HASH_TEXT]] = None,
                           file: Optional[discord.Attachment] = None,
                           compare: Optional[app_commands.Range[str, 1, 256]] = None,
                           incognito: Optional[bool] = None):
        ephemeral = get_incognito_setting(interaction)
        if text is None and file is None:
            locale = i18n.get_user_locale(interaction)
            await interaction.response.send_modal(
                HashModal(self, locale, ephemeral=ephemeral, compare=compare))
            return
        await self.run_hash(interaction, text=text, attachment=file, compare=compare,
                            ephemeral=ephemeral)

    async def run_hash(self, interaction: discord.Interaction, *, text: Optional[str],
                       attachment: Optional[discord.Attachment], compare: Optional[str],
                       ephemeral: bool) -> None:
        locale = i18n.get_user_locale(interaction)
        requester = None if ephemeral else interaction.user.id
        loading = False
        try:
            if text and attachment:
                raise tc.ToolError("both", tool="hash")
            if not text and attachment is None:
                raise tc.ToolError("empty_input", tool="hash")

            if attachment is not None:
                if attachment.size > MAX_HASH_FILE_BYTES:
                    raise tc.ToolError("too_large", tool="hash",
                                       max=tc.format_bytes(MAX_HASH_FILE_BYTES, locale))
                await interaction.response.send_message(view=tc.loading_card(locale),
                                                        ephemeral=ephemeral)
                loading = True
                async with _CPU:
                    try:
                        data = await attachment.read()
                    except (discord.HTTPException, discord.NotFound):
                        raise tc.ToolError("download_failed", tool="hash") from None
                    digests = await asyncio.to_thread(hs.hash_bytes, data)
                subject = t("tools.hash.subject_file", locale=locale, name=attachment.filename,
                            size=tc.format_bytes(len(data), locale))
                del data
                is_text = False
            else:
                digests = hs.hash_text(text)
                subject = t("tools.hash.subject_text", locale=locale, count=len(text))
                is_text = True
        except tc.ToolError as exc:
            card = tc.tool_error_card(exc, locale)
            if loading:
                await interaction.edit_original_response(view=card)
            else:
                await tc.respond(interaction, view=card, ephemeral=True)
            return

        match = hs.match_digest(digests, compare) if compare else None
        view = views.build_hash_card(digests, subject=subject, is_text=is_text, locale=locale,
                                     match=match, compared=bool(compare), requester_id=requester)
        if loading:
            await interaction.edit_original_response(view=view, allowed_mentions=tc.NO_MENTIONS)
        else:
            await tc.respond(interaction, view=view, ephemeral=ephemeral)

    # ------------------------------------------------------------------ #
    # /jwt
    # ------------------------------------------------------------------ #

    @app_commands.command(name="jwt", description="Decode a JSON Web Token (header and payload)")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def jwt_command(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        await interaction.response.send_modal(JwtModal(self, locale))

    # ------------------------------------------------------------------ #
    # /color
    # ------------------------------------------------------------------ #

    @app_commands.command(name="color", description="Convert a colour and check its contrast")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        color="HEX, rgb(), hsl(), CSS name, Discord number or random",
        incognito="Make response visible only to you")
    @add_incognito_option()
    async def color_command(self, interaction: discord.Interaction,
                            color: app_commands.Range[str, 1, 64],
                            incognito: Optional[bool] = None):
        locale = i18n.get_user_locale(interaction)
        ephemeral = get_incognito_setting(interaction)
        try:
            parsed = colors.parse(color)
        except colors.ColorError as exc:
            await _send_error(interaction, tc.ToolError(exc.code, tool="color",
                                                        examples=colors.EXAMPLES), locale)
            return
        await interaction.response.defer(ephemeral=ephemeral)
        view, files = await views.build_color_message(
            parsed.rgb, locale, alpha=parsed.alpha,
            requester_id=None if ephemeral else interaction.user.id)
        await tc.respond(interaction, view=view, ephemeral=ephemeral, files=files)


async def setup(bot):
    await bot.add_cog(ToolsSecurity(bot))
