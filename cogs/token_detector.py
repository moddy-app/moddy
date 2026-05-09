"""
Token Detector — automatically detects Discord tokens posted in messages,
validates them against the Discord API, and alerts the affected user or bot
owner via DM with action buttons.

Security design:
- Tokens are NEVER stored in the database.
- Tokens are held in process memory, encrypted with Fernet (TOKEN_DETECTOR_KEY env var).
- Each alert payload is keyed by a 10-char random hex string embedded in button custom_ids.
- Cache entries expire after 24 h. After invalidation the token field is cleared but the
  rest of the payload is kept so Message Info and Delete still work.
- After a bot restart the cache is empty; buttons show a graceful "unavailable" message.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import secrets
import time
from typing import Optional

import aiohttp
import discord
from discord import ui
from discord.ext import commands

import traceback

from cogs.error_handler import BaseView, ErrorView, capture_error_to_sentry
from utils.emojis import (
    WARNING, ERROR, DONE, INFO, DELETE, LOGOUT, SETTINGS,
)

logger = logging.getLogger("moddy.token_detector")

DISCORD_API = "https://discord.com/api/v10"


# =============================================================================
# ERROR ROUTING — unexpected exceptions in DynamicItem callbacks
# =============================================================================

async def _route_error(
    interaction: discord.Interaction,
    error: Exception,
    context: str,
) -> None:
    """Route an unexpected exception to the centralized ErrorTracker cog."""
    compact_tb = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    ).replace("\n", " ⮐ ")
    logger.error(f"Unexpected error in TokenDetector/{context}: {compact_tb}")

    bot = interaction.client
    error_tracker = bot.get_cog("ErrorTracker") if bot else None

    if error_tracker:
        error_code = error_tracker.generate_error_code(error)
        error_details = error_tracker.format_error_details(error)
        error_details.update({
            "command": f"TokenDetector:{context}",
            "user": f"{interaction.user} ({interaction.user.id})",
            "guild": (
                f"{interaction.guild.name} ({interaction.guild.id})"
                if interaction.guild else "DM"
            ),
            "channel": (
                f"#{interaction.channel.name}"
                if hasattr(interaction.channel, "name") else "DM"
            ),
        })

        sentry_id = capture_error_to_sentry(error, {
            "error_type": "DynamicItem Error",
            "error_code": error_code,
            "context": context,
            "user_id": interaction.user.id if interaction.user else None,
            "guild_id": interaction.guild.id if interaction.guild else None,
        })
        if sentry_id:
            error_details["sentry_event_id"] = sentry_id

        error_tracker.store_error(error_code, error_details)
        await error_tracker.store_error_db(error_code, error_details)
        await error_tracker.send_error_log(error_code, error_details, is_fatal=False)

        error_view = ErrorView(error_code)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(view=error_view, ephemeral=True)
            else:
                await interaction.response.send_message(view=error_view, ephemeral=True)
        except Exception:
            pass
    else:
        try:
            msg = "An unexpected error occurred and has been logged."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass


class _ErrorRoutingMixin:
    """Mixin that routes unhandled DynamicItem callback exceptions to ErrorTracker."""

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await _route_error(interaction, error, type(self).__name__)

# Left-border accent colour for the alert container (deep calm red, not flashy)
_ALERT_COLOUR = discord.Colour(0xE74C3C)

# =============================================================================
# TOKEN REGEX
# =============================================================================

# Matches user/bot tokens (3-part) and MFA tokens. Intentionally broad to catch
# format variations Discord has shipped over the years.
_TOKEN_RE = re.compile(
    r"\b(mfa\.[\w-]{60,}|[\w-]{20,30}\.[\w-]{6,10}\.[\w-]{20,50})\b"
)

# =============================================================================
# IN-MEMORY ENCRYPTED CACHE
# =============================================================================
#
# Payload schema (all fields are stored encrypted):
# {
#   "token": str,              # cleared after invalidation
#   "email": str | None,
#   "masked_content": str,
#   "msg_id": int,
#   "channel_id": int,
#   "channel_name": str,
#   "guild_id": int,
#   "guild_name": str,
#   "author_id": int,
#   "author_name": str,
#   "timestamp": int,
#   # bot-token-only:
#   "bot_id": int | None,
#   "bot_name": str | None,
#   # state (mutated in-place during button interactions):
#   "state": {
#     "deleted": bool,
#     "invalidated": bool,
#     "pw_reset_sent": bool,
#   },
#   # set after the DM is sent:
#   "dm_message_id": int | None,
#   "dm_channel_id": int | None,
# }

_TOKEN_CACHE: dict[str, dict] = {}
_CACHE_TTL = 86_400  # 24 h

_fernet = None


def _init_fernet() -> None:
    global _fernet
    try:
        from cryptography.fernet import Fernet  # type: ignore[import]

        raw = os.environ.get("TOKEN_DETECTOR_KEY", "")
        if raw:
            _fernet = Fernet(raw.encode() if isinstance(raw, str) else raw)
            logger.info("Token detector: using TOKEN_DETECTOR_KEY for encryption.")
        else:
            key = Fernet.generate_key()
            _fernet = Fernet(key)
            logger.warning(
                "TOKEN_DETECTOR_KEY env var not set — using ephemeral Fernet key. "
                "Cached alert data will be lost on bot restart."
            )
    except ImportError:
        logger.error(
            "cryptography library not installed — token cache encryption disabled. "
            "Run: pip install cryptography"
        )
        _fernet = None


def _enc(data: bytes) -> bytes:
    return _fernet.encrypt(data) if _fernet else data


def _dec(data: bytes) -> bytes:
    return _fernet.decrypt(data) if _fernet else data


def _purge_expired() -> None:
    now = time.time()
    for k in [k for k, v in _TOKEN_CACHE.items() if now > v["exp"]]:
        del _TOKEN_CACHE[k]


def cache_alert(payload: dict) -> str:
    """Encrypt *payload* and store it; return a 10-char hex cache key."""
    _purge_expired()
    ck = secrets.token_hex(5)
    _TOKEN_CACHE[ck] = {
        "d": _enc(json.dumps(payload).encode()),
        "exp": time.time() + _CACHE_TTL,
    }
    return ck


def peek_alert(ck: str) -> Optional[dict]:
    """Retrieve cached payload without consuming it."""
    entry = _TOKEN_CACHE.get(ck)
    if not entry or time.time() > entry["exp"]:
        _TOKEN_CACHE.pop(ck, None)
        return None
    try:
        return json.loads(_dec(entry["d"]).decode())
    except Exception:
        _TOKEN_CACHE.pop(ck, None)
        return None


def update_alert(ck: str, new_data: dict) -> bool:
    """Overwrite the encrypted payload in-place. Returns False if key not found."""
    entry = _TOKEN_CACHE.get(ck)
    if not entry or time.time() > entry["exp"]:
        return False
    try:
        entry["d"] = _enc(json.dumps(new_data).encode())
        return True
    except Exception:
        return False


# =============================================================================
# DISCORD API HELPERS
# =============================================================================

async def _api_get(
    session: aiohttp.ClientSession,
    path: str,
    token: Optional[str] = None,
    bot_token: bool = True,
) -> Optional[dict]:
    headers: dict = {}
    if token:
        headers["Authorization"] = f"Bot {token}" if bot_token else token
    try:
        async with session.get(
            f"{DISCORD_API}{path}",
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            return await r.json() if r.status == 200 else None
    except Exception:
        return None


async def _api_post(
    session: aiohttp.ClientSession,
    path: str,
    body: dict,
    token: Optional[str] = None,
    bot_token: bool = False,
) -> tuple[int, Optional[dict]]:
    """Returns (http_status, json_body_or_none)."""
    headers: dict = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bot {token}" if bot_token else token
    try:
        async with session.post(
            f"{DISCORD_API}{path}",
            headers=headers,
            json=body,
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            try:
                body_data = await r.json()
            except Exception:
                body_data = None
            return r.status, body_data
    except Exception:
        return 0, None


async def _delete_message_api(
    session: aiohttp.ClientSession,
    token: str,
    channel_id: int,
    message_id: int,
) -> bool:
    """Delete a message using a user token as authorization."""
    try:
        async with session.delete(
            f"{DISCORD_API}/channels/{channel_id}/messages/{message_id}",
            headers={"Authorization": token},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            return r.status == 204
    except Exception:
        return False


async def _open_dm_via_user_token(
    session: aiohttp.ClientSession,
    user_token: str,
    bot_id: int,
) -> Optional[int]:
    """Use a user's own token to open a DM channel with the bot. Returns channel_id."""
    status, data = await _api_post(
        session,
        "/users/@me/channels",
        body={"recipient_id": str(bot_id)},
        token=user_token,
        bot_token=False,
    )
    if status in (200, 201) and data and "id" in data:
        return int(data["id"])
    return None


def _decode_user_id(token: str) -> Optional[int]:
    """Try to base64-decode the first token segment into a Discord snowflake."""
    try:
        part = token.split(".")[0]
        padded = part + "=" * (-len(part) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8", errors="ignore")
        uid = int(decoded)
        if 10_000_000_000_000_000 <= uid <= 9_999_999_999_999_999_999:
            return uid
    except Exception:
        pass
    return None


def _redact(text: str, token: str) -> str:
    return text.replace(token, "`[TOKEN REDACTED]`")


# =============================================================================
# HELPERS — EDIT ORIGINAL DM TO UPDATE BUTTON STATES
# =============================================================================

async def _refresh_dm(bot: commands.Bot, data: dict, ck: str, is_bot_alert: bool) -> None:
    """Edit the original DM message to reflect updated button disabled states."""
    dm_msg_id = data.get("dm_message_id")
    dm_ch_id = data.get("dm_channel_id")
    if not (dm_msg_id and dm_ch_id):
        return
    try:
        channel = bot.get_channel(dm_ch_id) or await bot.fetch_channel(dm_ch_id)
        msg = await channel.fetch_message(dm_msg_id)
        if is_bot_alert:
            new_view = _build_bot_alert_view(
                ck=ck,
                msg_id=data["msg_id"],
                channel_id=data["channel_id"],
                bot_name=data.get("bot_name", ""),
                bot_id=data.get("bot_id", 0),
                guild_name=data["guild_name"],
                channel_name=data["channel_name"],
                author_id=data["author_id"],
                author_name=data["author_name"],
                timestamp=data["timestamp"],
                state=data["state"],
            )
        else:
            new_view = _build_user_alert_view(
                ck=ck,
                msg_id=data["msg_id"],
                channel_id=data["channel_id"],
                guild_name=data["guild_name"],
                channel_name=data["channel_name"],
                author_id=data["author_id"],
                author_name=data["author_name"],
                timestamp=data["timestamp"],
                state=data["state"],
            )
        await msg.edit(view=new_view)
    except Exception as exc:
        logger.debug(f"Could not refresh DM message {dm_msg_id}: {exc}")


# =============================================================================
# SHARED VIEWS
# =============================================================================

def _expired_view() -> BaseView:
    """Shown when the cache entry for a button's ck is gone (restart / TTL)."""
    view = BaseView()
    c = ui.Container(accent_colour=_ALERT_COLOUR)
    c.add_item(ui.TextDisplay(
        f"### {ERROR} Action No Longer Available\n"
        "The data for this alert has expired — either the bot restarted or the "
        "24-hour window elapsed.\n\n"
        "If your account is still at risk, please review your security settings at "
        "<https://discord.com/settings/account>."
    ))
    view.add_item(c)
    return view


def _already_done_view(action: str) -> BaseView:
    view = BaseView()
    c = ui.Container()
    c.add_item(ui.TextDisplay(
        f"### {INFO} Already Done\n"
        f"This action ({action}) has already been performed."
    ))
    view.add_item(c)
    return view


# =============================================================================
# DYNAMIC ITEM — USER ALERT: MESSAGE INFO
# =============================================================================

class UserDetailsButton(
    _ErrorRoutingMixin,
    ui.DynamicItem[ui.Button],
    template=r"moddy:td:user:details:(?P<ck>[0-9a-f]{10}):(?P<mid>\d+):(?P<cid>\d+)",
):
    """Shows server / channel / author details of where the token was posted."""

    def __init__(self, ck: str, mid: int, cid: int, disabled: bool = False) -> None:
        super().__init__(
            ui.Button(
                label="Message Info",
                style=discord.ButtonStyle.secondary,
                emoji=discord.PartialEmoji.from_str(INFO),
                custom_id=f"moddy:td:user:details:{ck}:{mid}:{cid}",
                disabled=disabled,
            )
        )
        self.ck, self.mid, self.cid = ck, mid, cid

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match.group("ck"), int(match.group("mid")), int(match.group("cid")))

    async def callback(self, interaction: discord.Interaction) -> None:
        data = peek_alert(self.ck)
        if data is None:
            await interaction.response.send_message(view=_expired_view(), ephemeral=True)
            return

        ts = data.get("timestamp", 0)
        masked = data.get("masked_content", "*Content unavailable*")
        body = (
            f"**Server:** `{data.get('guild_name', '?')}` (`{data.get('guild_id', '?')}`)\n"
            f"**Channel:** `#{data.get('channel_name', '?')}` (`{data.get('channel_id', '?')}`)\n"
            f"**Message ID:** `{self.mid}`\n"
            f"**Sent by:** `{data.get('author_name', '?')}` (`{data.get('author_id', '?')}`)\n"
            f"**At:** <t:{ts}:F>\n\n"
            f"**Message content:**\n>>> {masked}"
        )
        view = BaseView()
        c = ui.Container()
        c.add_item(ui.TextDisplay(f"### {INFO} Message Details"))
        c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        c.add_item(ui.TextDisplay(body))
        view.add_item(c)
        await interaction.response.send_message(view=view, ephemeral=True)


# =============================================================================
# DYNAMIC ITEM — USER ALERT: INVALIDATE TOKEN (step 1 — confirmation prompt)
# =============================================================================

class UserInvalidateButton(
    _ErrorRoutingMixin,
    ui.DynamicItem[ui.Button],
    template=r"moddy:td:user:invalidate:(?P<ck>[0-9a-f]{10})",
):
    """Shows a confirmation before revoking the session token."""

    def __init__(self, ck: str, disabled: bool = False) -> None:
        super().__init__(
            ui.Button(
                label="Invalidate Token",
                style=discord.ButtonStyle.danger,
                emoji=discord.PartialEmoji.from_str(LOGOUT),
                custom_id=f"moddy:td:user:invalidate:{ck}",
                disabled=disabled,
            )
        )
        self.ck = ck

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match.group("ck"))

    async def callback(self, interaction: discord.Interaction) -> None:
        data = peek_alert(self.ck)
        if data is None:
            await interaction.response.send_message(view=_expired_view(), ephemeral=True)
            return
        if data.get("state", {}).get("invalidated"):
            await interaction.response.send_message(
                view=_already_done_view("token invalidation"), ephemeral=True
            )
            return

        view = BaseView()
        c = ui.Container()
        c.add_item(ui.TextDisplay(
            f"### {WARNING} Confirm Token Invalidation\n"
            "This will invalidate the session token that was exposed. "
            "**Only the session linked to this specific token will be ended** — "
            "your other active sessions (other browsers or devices) will remain open.\n\n"
            "-# This action cannot be undone. You will need to log in again on the affected session."
        ))
        view.add_item(c)

        row = ui.ActionRow()
        row.add_item(UserConfirmInvalidateButton(self.ck))
        row.add_item(UserCancelButton())
        view.add_item(row)

        await interaction.response.send_message(view=view, ephemeral=True)


# =============================================================================
# DYNAMIC ITEM — USER ALERT: INVALIDATE TOKEN (step 2 — execute)
# =============================================================================

class UserConfirmInvalidateButton(
    _ErrorRoutingMixin,
    ui.DynamicItem[ui.Button],
    template=r"moddy:td:user:confirm:(?P<ck>[0-9a-f]{10})",
):
    """Calls POST /auth/logout with the user's token, then clears it from cache."""

    def __init__(self, ck: str) -> None:
        super().__init__(
            ui.Button(
                label="Invalidate this session",
                style=discord.ButtonStyle.danger,
                emoji=discord.PartialEmoji.from_str(LOGOUT),
                custom_id=f"moddy:td:user:confirm:{ck}",
            )
        )
        self.ck = ck

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match.group("ck"))

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        bot = interaction.client
        data = peek_alert(self.ck)
        if data is None:
            await interaction.followup.send(view=_expired_view(), ephemeral=True)
            return

        token = data.get("token", "")
        success = False
        if token:
            async with aiohttp.ClientSession() as session:
                status, _ = await _api_post(
                    session,
                    "/auth/logout",
                    body={"provider": None, "voip_provider": None},
                    token=token,
                    bot_token=False,
                )
                success = status in (200, 204)

        view = BaseView()
        c = ui.Container()
        if success:
            c.add_item(ui.TextDisplay(
                f"### {DONE} Session Token Invalidated\n"
                "The exposed session token has been successfully revoked. "
                "The session it was linked to is now closed.\n\n"
                "**Recommended next steps:**\n"
                "- Check [your active sessions](https://discord.com/settings/sessions) "
                "and close any you don't recognise\n"
                "- Consider enabling Two-Factor Authentication (2FA) if you haven't already\n"
                "- If you suspect your account was accessed, change your password"
            ))
        elif not token:
            c.add_item(ui.TextDisplay(
                f"### {ERROR} Token Already Cleared\n"
                "The token was already removed from our system. "
                "If you still need to end your sessions, please change your password at "
                "<https://discord.com/settings/account>."
            ))
        else:
            c.add_item(ui.TextDisplay(
                f"### {ERROR} Invalidation Failed\n"
                "Discord returned an unexpected error when trying to invalidate the token "
                "(HTTP error from Discord's API).\n\n"
                "To manually end all sessions, change your password at "
                "<https://discord.com/settings/account>."
            ))
        view.add_item(c)
        await interaction.followup.send(view=view, ephemeral=True)

        # Clear token from cache, mark invalidated — other buttons (info, delete) still work
        data["token"] = ""
        data["state"]["invalidated"] = True
        # pw_reset_sent also disabled because we have no token left for the email
        data["state"]["pw_reset_sent"] = True
        update_alert(self.ck, data)

        # Refresh original DM so Invalidate + Reset Password become visually disabled
        await _refresh_dm(bot, data, self.ck, is_bot_alert=False)


# =============================================================================
# DYNAMIC ITEM — USER ALERT: CANCEL
# =============================================================================

class UserCancelButton(
    _ErrorRoutingMixin,
    ui.DynamicItem[ui.Button],
    template=r"moddy:td:user:cancel",
):
    """Dismisses the ephemeral confirmation without taking action."""

    def __init__(self) -> None:
        super().__init__(
            ui.Button(
                label="Cancel",
                style=discord.ButtonStyle.secondary,
                custom_id="moddy:td:user:cancel",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        view = BaseView()
        c = ui.Container()
        c.add_item(ui.TextDisplay(
            f"### {DONE} Cancelled\n"
            "No action was taken. Your security alert is still active above."
        ))
        view.add_item(c)
        await interaction.response.edit_message(view=view)


# =============================================================================
# DYNAMIC ITEM — USER ALERT: DELETE MESSAGE
# =============================================================================

class UserDeleteMsgButton(
    _ErrorRoutingMixin,
    ui.DynamicItem[ui.Button],
    template=r"moddy:td:user:delete:(?P<ck>[0-9a-f]{10}):(?P<mid>\d+):(?P<cid>\d+)",
):
    """Deletes the original message. Tries bot permissions first, then user token."""

    def __init__(self, ck: str, mid: int, cid: int, disabled: bool = False) -> None:
        super().__init__(
            ui.Button(
                label="Delete Message",
                style=discord.ButtonStyle.danger,
                emoji=discord.PartialEmoji.from_str(DELETE),
                custom_id=f"moddy:td:user:delete:{ck}:{mid}:{cid}",
                disabled=disabled,
            )
        )
        self.ck, self.mid, self.cid = ck, mid, cid

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match.group("ck"), int(match.group("mid")), int(match.group("cid")))

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        bot = interaction.client
        data = peek_alert(self.ck)
        if data is None:
            await interaction.followup.send(view=_expired_view(), ephemeral=True)
            return
        if data.get("state", {}).get("deleted"):
            await interaction.followup.send(
                view=_already_done_view("message deletion"), ephemeral=True
            )
            return

        deleted = False

        # Attempt 1: delete via bot (requires Manage Messages in that channel)
        try:
            channel = bot.get_channel(self.cid) or await bot.fetch_channel(self.cid)
            msg = await channel.fetch_message(self.mid)
            await msg.delete()
            deleted = True
        except discord.NotFound:
            deleted = True  # Message already gone — treat as success
        except discord.Forbidden:
            pass
        except Exception as exc:
            logger.debug(f"Bot delete attempt failed: {exc}")

        # Attempt 2: delete via the user's token
        if not deleted:
            token = data.get("token", "")
            if token:
                async with aiohttp.ClientSession() as session:
                    deleted = await _delete_message_api(session, token, self.cid, self.mid)

        view = BaseView()
        c = ui.Container()
        if deleted:
            c.add_item(ui.TextDisplay(
                f"### {DONE} Message Deleted\n"
                "The message containing your session token has been removed."
            ))
            data["state"]["deleted"] = True
            update_alert(self.ck, data)
            await _refresh_dm(bot, data, self.ck, is_bot_alert=False)
        else:
            c.add_item(ui.TextDisplay(
                f"### {ERROR} Could Not Delete\n"
                "Moddy does not have **Manage Messages** permission in that channel, "
                "and the user token attempt also failed.\n\n"
                "Please delete the message manually if you have access to the channel."
            ))
        view.add_item(c)
        await interaction.followup.send(view=view, ephemeral=True)


# =============================================================================
# DYNAMIC ITEM — USER ALERT: CHANGE PASSWORD
# =============================================================================

class UserResetPwButton(
    _ErrorRoutingMixin,
    ui.DynamicItem[ui.Button],
    template=r"moddy:td:user:resetpw:(?P<ck>[0-9a-f]{10})",
):
    """Sends a password-reset email via POST /auth/forgot."""

    def __init__(self, ck: str, disabled: bool = False) -> None:
        super().__init__(
            ui.Button(
                label="Change Password",
                style=discord.ButtonStyle.primary,
                emoji=discord.PartialEmoji.from_str(SETTINGS),
                custom_id=f"moddy:td:user:resetpw:{ck}",
                disabled=disabled,
            )
        )
        self.ck = ck

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match.group("ck"))

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        bot = interaction.client
        data = peek_alert(self.ck)
        if data is None:
            await interaction.followup.send(view=_expired_view(), ephemeral=True)
            return
        if data.get("state", {}).get("pw_reset_sent"):
            await interaction.followup.send(
                view=_already_done_view("password reset"), ephemeral=True
            )
            return

        email = data.get("email")
        view = BaseView()
        c = ui.Container()

        if not email:
            # No email in scope — direct user to settings
            c.add_item(ui.TextDisplay(
                f"### {ERROR} Email Address Unavailable\n"
                "We couldn't retrieve your email address from the session token "
                "(it may not include the email scope).\n\n"
                "Please reset your password manually at "
                "<https://discord.com/settings/account>."
            ))
        else:
            async with aiohttp.ClientSession() as session:
                status, resp_data = await _api_post(
                    session,
                    "/auth/forgot",
                    body={"login": email},
                )

            if status == 200:
                method = (resp_data or {}).get("method", "password_reset")
                if method == "one_time_login":
                    c.add_item(ui.TextDisplay(
                        f"### {DONE} Reset Email Sent\n"
                        f"A password reset link (with a one-time login option) has been sent "
                        f"to **{email}**. Follow the instructions in the email to set a new password.\n\n"
                        "-# Resetting your password will invalidate all active sessions."
                    ))
                else:
                    c.add_item(ui.TextDisplay(
                        f"### {DONE} Reset Email Sent\n"
                        f"A password reset link has been sent to **{email}**. "
                        "Click the link in the email to choose a new password.\n\n"
                        "-# Resetting your password will invalidate all active sessions."
                    ))
                data["state"]["pw_reset_sent"] = True
                update_alert(self.ck, data)
                await _refresh_dm(bot, data, self.ck, is_bot_alert=False)

            elif status == 400 and (resp_data or {}).get("code") == 70007:
                # Phone-number account, SMS sent
                c.add_item(ui.TextDisplay(
                    f"### {DONE} Verification Code Sent\n"
                    "A verification code has been sent to your registered phone number. "
                    "Use it to reset your password in the Discord app."
                ))
                data["state"]["pw_reset_sent"] = True
                update_alert(self.ck, data)
                await _refresh_dm(bot, data, self.ck, is_bot_alert=False)

            elif status == 400 and (resp_data or {}).get("code") == 70009:
                # Phone ineligible, email fallback
                c.add_item(ui.TextDisplay(
                    f"### {DONE} Reset Email Sent (Phone Ineligible)\n"
                    f"Your phone number is not eligible for phone-based reset. "
                    f"A password reset link has been sent to **{email}** instead."
                ))
                data["state"]["pw_reset_sent"] = True
                update_alert(self.ck, data)
                await _refresh_dm(bot, data, self.ck, is_bot_alert=False)

            else:
                # Unexpected — raise so _ErrorRoutingMixin.on_error handles it
                raise RuntimeError(
                    f"Unexpected Discord API response for POST /auth/forgot: "
                    f"HTTP {status}, body={resp_data!r}"
                )

        view.add_item(c)
        await interaction.followup.send(view=view, ephemeral=True)


# =============================================================================
# DYNAMIC ITEM — BOT ALERT: MESSAGE INFO
# =============================================================================

class BotDetailsButton(
    _ErrorRoutingMixin,
    ui.DynamicItem[ui.Button],
    template=r"moddy:td:bot:details:(?P<ck>[0-9a-f]{10}):(?P<mid>\d+):(?P<cid>\d+)",
):
    """Shows where and by whom the bot token was posted."""

    def __init__(self, ck: str, mid: int, cid: int, disabled: bool = False) -> None:
        super().__init__(
            ui.Button(
                label="Message Info",
                style=discord.ButtonStyle.secondary,
                emoji=discord.PartialEmoji.from_str(INFO),
                custom_id=f"moddy:td:bot:details:{ck}:{mid}:{cid}",
                disabled=disabled,
            )
        )
        self.ck, self.mid, self.cid = ck, mid, cid

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match.group("ck"), int(match.group("mid")), int(match.group("cid")))

    async def callback(self, interaction: discord.Interaction) -> None:
        data = peek_alert(self.ck)
        if data is None:
            await interaction.response.send_message(view=_expired_view(), ephemeral=True)
            return

        ts = data.get("timestamp", 0)
        masked = data.get("masked_content", "*Content unavailable*")
        body = (
            f"**Server:** `{data.get('guild_name', '?')}` (`{data.get('guild_id', '?')}`)\n"
            f"**Channel:** `#{data.get('channel_name', '?')}` (`{data.get('channel_id', '?')}`)\n"
            f"**Message ID:** `{self.mid}`\n"
            f"**Sent by:** `{data.get('author_name', '?')}` (`{data.get('author_id', '?')}`)\n"
            f"**At:** <t:{ts}:F>\n\n"
            f"**Message content:**\n>>> {masked}"
        )
        view = BaseView()
        c = ui.Container()
        c.add_item(ui.TextDisplay(f"### {INFO} Message Details"))
        c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        c.add_item(ui.TextDisplay(body))
        view.add_item(c)
        await interaction.response.send_message(view=view, ephemeral=True)


# =============================================================================
# DYNAMIC ITEM — BOT ALERT: DELETE MESSAGE
# =============================================================================

class BotDeleteMsgButton(
    _ErrorRoutingMixin,
    ui.DynamicItem[ui.Button],
    template=r"moddy:td:bot:delete:(?P<ck>[0-9a-f]{10}):(?P<mid>\d+):(?P<cid>\d+)",
):
    """Deletes the message that contained the bot token (via bot permissions only)."""

    def __init__(self, ck: str, mid: int, cid: int, disabled: bool = False) -> None:
        super().__init__(
            ui.Button(
                label="Delete Message",
                style=discord.ButtonStyle.danger,
                emoji=discord.PartialEmoji.from_str(DELETE),
                custom_id=f"moddy:td:bot:delete:{ck}:{mid}:{cid}",
                disabled=disabled,
            )
        )
        self.ck, self.mid, self.cid = ck, mid, cid

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match.group("ck"), int(match.group("mid")), int(match.group("cid")))

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        bot = interaction.client
        data = peek_alert(self.ck)
        if data is None:
            await interaction.followup.send(view=_expired_view(), ephemeral=True)
            return
        if data.get("state", {}).get("deleted"):
            await interaction.followup.send(
                view=_already_done_view("message deletion"), ephemeral=True
            )
            return

        deleted = False
        try:
            channel = bot.get_channel(self.cid) or await bot.fetch_channel(self.cid)
            msg = await channel.fetch_message(self.mid)
            await msg.delete()
            deleted = True
        except discord.NotFound:
            deleted = True
        except discord.Forbidden:
            pass
        except Exception as exc:
            logger.debug(f"Bot alert — bot delete attempt failed: {exc}")

        view = BaseView()
        c = ui.Container()
        if deleted:
            c.add_item(ui.TextDisplay(
                f"### {DONE} Message Deleted\n"
                "The message containing the bot token has been removed. "
                "Make sure to regenerate your token in the Developer Portal."
            ))
            data["state"]["deleted"] = True
            update_alert(self.ck, data)
            await _refresh_dm(bot, data, self.ck, is_bot_alert=True)
        else:
            c.add_item(ui.TextDisplay(
                f"### {ERROR} Could Not Delete\n"
                "Moddy does not have **Manage Messages** permission in that channel.\n\n"
                "Please delete the message manually. "
                "Regardless, **regenerate your bot token immediately** in the "
                "[Developer Portal](https://discord.com/developers/applications)."
            ))
        view.add_item(c)
        await interaction.followup.send(view=view, ephemeral=True)


# =============================================================================
# VIEW BUILDERS
# =============================================================================

def _build_user_alert_view(
    ck: str,
    msg_id: int,
    channel_id: int,
    guild_name: str,
    channel_name: str,
    author_id: int,
    author_name: str,
    timestamp: int,
    state: Optional[dict] = None,
) -> BaseView:
    state = state or {}
    invalidated = state.get("invalidated", False)
    deleted = state.get("deleted", False)
    pw_sent = state.get("pw_reset_sent", False)

    view = BaseView()

    # ── Main container (red accent) ──────────────────────────────────────────
    c = ui.Container(accent_colour=_ALERT_COLOUR)

    c.add_item(ui.TextDisplay(
        f"### {WARNING} Your account token was just exposed"
    ))
    c.add_item(ui.TextDisplay(
        "A session token associated with your account was found in a public message. "
        "A session token grants access to your account and should be kept private — "
        "anyone who has it can use your account as if they were you."
    ))

    c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

    c.add_item(ui.TextDisplay(
        f"**Detected in**\n"
        f"`{guild_name}` › `#{channel_name}`\n"
        f"**Sent by:** `{author_name}` (`{author_id}`)\n"
        f"**At:** <t:{timestamp}:F>"
    ))
    c.add_item(ui.TextDisplay(
        "-# Sent automatically by Moddy · Your token is not stored · Take action to stay secure"
    ))

    view.add_item(c)

    # ── Action buttons (outside the container) ───────────────────────────────
    row = ui.ActionRow()
    row.add_item(UserDetailsButton(ck, msg_id, channel_id))
    row.add_item(UserInvalidateButton(ck, disabled=invalidated))
    row.add_item(UserDeleteMsgButton(ck, msg_id, channel_id, disabled=deleted))
    row.add_item(UserResetPwButton(ck, disabled=invalidated or pw_sent))
    view.add_item(row)

    # ── Utility link (outside the container) ────────────────────────────────
    link_row = ui.ActionRow()
    link_row.add_item(ui.Button(
        label="Account Security",
        style=discord.ButtonStyle.link,
        url="https://discord.com/settings/account",
    ))
    link_row.add_item(ui.Button(
        label="Learn More",
        style=discord.ButtonStyle.link,
        url="https://support.discord.com/hc/en-us/articles/218410947",
    ))
    view.add_item(link_row)

    return view


def _build_bot_alert_view(
    ck: str,
    msg_id: int,
    channel_id: int,
    bot_name: str,
    bot_id: int,
    guild_name: str,
    channel_name: str,
    author_id: int,
    author_name: str,
    timestamp: int,
    state: Optional[dict] = None,
) -> BaseView:
    state = state or {}
    deleted = state.get("deleted", False)

    view = BaseView()

    # ── Main container (red accent) ──────────────────────────────────────────
    c = ui.Container(accent_colour=_ALERT_COLOUR)

    c.add_item(ui.TextDisplay(
        f"### {WARNING} Your bot token has been compromised"
    ))
    c.add_item(ui.TextDisplay(
        f"The token for your bot **{bot_name}** (`{bot_id}`) was found in a public message. "
        "A bot token provides full programmatic access to your bot — anyone with it "
        "can act as your bot on any server it's in."
    ))

    c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

    c.add_item(ui.TextDisplay(
        f"**Detected in**\n"
        f"`{guild_name}` › `#{channel_name}`\n"
        f"**Sent by:** `{author_name}` (`{author_id}`)\n"
        f"**At:** <t:{timestamp}:F>"
    ))
    c.add_item(ui.TextDisplay(
        "-# Sent automatically by Moddy · Regenerate your token in the Developer Portal immediately"
    ))

    view.add_item(c)

    # ── Action buttons (outside the container) ───────────────────────────────
    row = ui.ActionRow()
    row.add_item(BotDetailsButton(ck, msg_id, channel_id))
    row.add_item(BotDeleteMsgButton(ck, msg_id, channel_id, disabled=deleted))
    row.add_item(ui.Button(
        label="Regenerate Token",
        style=discord.ButtonStyle.link,
        url=f"https://discord.com/developers/applications/{bot_id}/bot",
    ))
    view.add_item(row)

    return view


# =============================================================================
# DM SENDER
# =============================================================================

async def _send_dm(
    bot: commands.Bot,
    session: aiohttp.ClientSession,
    user_id: int,
    view: BaseView,
    user_token: Optional[str] = None,
) -> Optional[discord.Message]:
    """Send a DM to *user_id*. Falls back to opening DM via the user's token.
    Returns the sent Message on success, None otherwise."""

    # Attempt 1: normal bot DM
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        msg = await user.send(view=view)
        return msg
    except (discord.Forbidden, discord.HTTPException):
        pass
    except Exception as exc:
        logger.debug(f"Unexpected error sending DM to {user_id}: {exc}")

    # Attempt 2: have the user's token open the DM channel, then bot sends in it
    if user_token:
        try:
            ch_id = await _open_dm_via_user_token(session, user_token, bot.user.id)
            if ch_id:
                channel = bot.get_channel(ch_id) or await bot.fetch_channel(ch_id)
                msg = await channel.send(view=view)
                return msg
        except Exception as exc:
            logger.debug(f"Fallback DM (user token) failed for {user_id}: {exc}")

    return None


# =============================================================================
# MAIN COG
# =============================================================================

_PROCESSING: set[int] = set()


class TokenDetector(commands.Cog):
    """Detects Discord tokens in messages and alerts the affected user via DM."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        _init_fernet()
        self.bot.add_dynamic_items(
            UserDetailsButton,
            UserInvalidateButton,
            UserConfirmInvalidateButton,
            UserCancelButton,
            UserDeleteMsgButton,
            UserResetPwButton,
            BotDetailsButton,
            BotDeleteMsgButton,
        )
        logger.info("TokenDetector cog loaded — dynamic items registered.")

    # ------------------------------------------------------------------
    # on_message
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild or not message.content:
            return

        matches = _TOKEN_RE.findall(message.content)
        if not matches:
            return

        if message.id in _PROCESSING:
            return
        _PROCESSING.add(message.id)
        try:
            await asyncio.gather(
                *[self._handle_token(message, token) for token in set(matches)]
            )
        finally:
            _PROCESSING.discard(message.id)

    async def _handle_token(self, message: discord.Message, token: str) -> None:
        """Validate a candidate token and alert if valid."""
        async with aiohttp.ClientSession() as session:
            # Try as user token (no Bot prefix)
            me_data = await _api_get(session, "/users/@me", token=token, bot_token=False)
            if me_data and me_data.get("id"):
                await self._alert_user(session, message, token, me_data)
                return

            # Try as bot token
            bot_me = await _api_get(session, "/users/@me", token=token, bot_token=True)
            if bot_me and bot_me.get("id") and bot_me.get("bot"):
                await self._alert_bot(session, message, token, bot_me)
                return

            logger.debug(
                f"Token candidate in message {message.id} did not validate — skipping."
            )

    # ------------------------------------------------------------------
    # User token alert
    # ------------------------------------------------------------------

    async def _alert_user(
        self,
        session: aiohttp.ClientSession,
        message: discord.Message,
        token: str,
        me_data: dict,
    ) -> None:
        user_id = int(me_data["id"])
        ts = int(message.created_at.timestamp())
        guild = message.guild
        channel = message.channel

        payload = {
            "token": token,
            "email": me_data.get("email"),
            "masked_content": _redact(message.content, token),
            "msg_id": message.id,
            "channel_id": channel.id,
            "channel_name": getattr(channel, "name", str(channel.id)),
            "guild_id": guild.id,
            "guild_name": guild.name,
            "author_id": message.author.id,
            "author_name": str(message.author),
            "timestamp": ts,
            "bot_id": None,
            "bot_name": None,
            "state": {"deleted": False, "invalidated": False, "pw_reset_sent": False},
            "dm_message_id": None,
            "dm_channel_id": None,
        }
        ck = cache_alert(payload)

        view = _build_user_alert_view(
            ck=ck,
            msg_id=message.id,
            channel_id=channel.id,
            guild_name=guild.name,
            channel_name=getattr(channel, "name", str(channel.id)),
            author_id=message.author.id,
            author_name=str(message.author),
            timestamp=ts,
        )

        sent_msg = await _send_dm(self.bot, session, user_id, view, user_token=token)
        if sent_msg:
            # Store DM message coords so button callbacks can refresh button states
            payload["dm_message_id"] = sent_msg.id
            payload["dm_channel_id"] = sent_msg.channel.id
            update_alert(ck, payload)
            logger.info(
                f"User token alert sent to {user_id} "
                f"(msg {message.id}, guild {guild.id})."
            )
        else:
            logger.warning(
                f"Could not DM user {user_id} — alert not delivered "
                f"(msg {message.id}, guild {guild.id})."
            )

    # ------------------------------------------------------------------
    # Bot token alert
    # ------------------------------------------------------------------

    async def _alert_bot(
        self,
        session: aiohttp.ClientSession,
        message: discord.Message,
        token: str,
        bot_me: dict,
    ) -> None:
        bot_id = int(bot_me["id"])
        bot_name = bot_me.get("username", f"Bot {bot_id}")
        ts = int(message.created_at.timestamp())
        guild = message.guild
        channel = message.channel

        app_data = await _api_get(
            session, "/oauth2/applications/@me", token=token, bot_token=True
        )

        payload = {
            "token": token,
            "email": None,
            "masked_content": _redact(message.content, token),
            "msg_id": message.id,
            "channel_id": channel.id,
            "channel_name": getattr(channel, "name", str(channel.id)),
            "guild_id": guild.id,
            "guild_name": guild.name,
            "author_id": message.author.id,
            "author_name": str(message.author),
            "timestamp": ts,
            "bot_id": bot_id,
            "bot_name": bot_name,
            "state": {"deleted": False, "invalidated": False, "pw_reset_sent": False},
            "dm_message_id": None,
            "dm_channel_id": None,
        }
        ck = cache_alert(payload)

        view = _build_bot_alert_view(
            ck=ck,
            msg_id=message.id,
            channel_id=channel.id,
            bot_name=bot_name,
            bot_id=bot_id,
            guild_name=guild.name,
            channel_name=getattr(channel, "name", str(channel.id)),
            author_id=message.author.id,
            author_name=str(message.author),
            timestamp=ts,
        )

        targets: list[int] = []
        if app_data:
            team = app_data.get("team")
            owner = app_data.get("owner") or {}
            if team:
                owner_id = (team.get("owner_user") or {}).get("id")
                if owner_id:
                    targets.append(int(owner_id))
                for member in team.get("members", []):
                    mid = (member.get("user") or {}).get("id")
                    if mid and int(mid) not in targets:
                        targets.append(int(mid))
            elif owner.get("id"):
                targets.append(int(owner["id"]))

        for uid in targets:
            sent_msg = await _send_dm(self.bot, session, uid, view, user_token=None)
            if sent_msg:
                payload["dm_message_id"] = sent_msg.id
                payload["dm_channel_id"] = sent_msg.channel.id
                update_alert(ck, payload)
                logger.info(
                    f"Bot token alert sent to {uid} for bot {bot_id} "
                    f"(msg {message.id}, guild {guild.id})."
                )
            else:
                logger.warning(
                    f"Could not DM {uid} — bot token alert not delivered "
                    f"(msg {message.id}, guild {guild.id})."
                )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TokenDetector(bot))
