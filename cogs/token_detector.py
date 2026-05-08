"""
Token Detector — automatically detects Discord tokens posted in messages,
validates them against the Discord API, and alerts the affected user or bot
owner via DM with action buttons.

Security design:
- Tokens are NEVER stored in the database.
- Tokens are held in process memory, encrypted with Fernet (TOKEN_DETECTOR_KEY env var).
- Each alert payload is keyed by a 10-char random hex string embedded in button custom_ids.
- Cache entries expire after 24 h and are consumed (deleted) on first destructive use.
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

from cogs.error_handler import BaseView
from utils.emojis import (
    WARNING, ERROR, DONE, INFO, DELETE, LOGOUT, SETTINGS, EDIT
)

logger = logging.getLogger("moddy.token_detector")

DISCORD_API = "https://discord.com/api/v10"

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


def _encrypt(data: bytes) -> bytes:
    return _fernet.encrypt(data) if _fernet else data


def _decrypt(data: bytes) -> bytes:
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
        "d": _encrypt(json.dumps(payload).encode()),
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
        return json.loads(_decrypt(entry["d"]).decode())
    except Exception:
        _TOKEN_CACHE.pop(ck, None)
        return None


def consume_alert(ck: str) -> Optional[dict]:
    """Retrieve cached payload and remove it from cache."""
    data = peek_alert(ck)
    _TOKEN_CACHE.pop(ck, None)
    return data


# =============================================================================
# DISCORD API HELPERS
# =============================================================================

async def _api_get(
    session: aiohttp.ClientSession,
    path: str,
    token: str,
    bot_token: bool = True,
) -> Optional[dict]:
    auth = f"Bot {token}" if bot_token else token
    try:
        async with session.get(
            f"{DISCORD_API}{path}",
            headers={"Authorization": auth},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            return await r.json() if r.status == 200 else None
    except Exception:
        return None


async def _api_post(
    session: aiohttp.ClientSession,
    path: str,
    token: str,
    bot_token: bool,
    body: dict,
) -> Optional[dict]:
    auth = f"Bot {token}" if bot_token else token
    try:
        async with session.post(
            f"{DISCORD_API}{path}",
            headers={"Authorization": auth, "Content-Type": "application/json"},
            json=body,
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            if r.status in (200, 201, 204):
                try:
                    return await r.json()
                except Exception:
                    return {}
            return None
    except Exception:
        return None


def _decode_user_id(token: str) -> Optional[int]:
    """Try to base64-decode the first token segment into a Discord snowflake."""
    try:
        part = token.split(".")[0]
        # URL-safe base64 — add padding
        padded = part + "=" * (-len(part) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8", errors="ignore")
        uid = int(decoded)
        # Sanity check: Discord snowflakes must fit in 64 bits and be non-trivial
        if 10_000_000_000_000_000 <= uid <= 9_999_999_999_999_999_999:
            return uid
    except Exception:
        pass
    return None


def _redact(text: str, token: str) -> str:
    return text.replace(token, "**[TOKEN REDACTED]**")


# =============================================================================
# SHARED ERROR VIEW
# =============================================================================

def _expired_view() -> BaseView:
    view = BaseView()
    c = ui.Container()
    c.add_item(ui.TextDisplay(
        f"### {ERROR} Action Unavailable\n"
        "This security alert is no longer active (the bot may have restarted or "
        "the data expired).\n"
        "Please review your Discord security settings at "
        "<https://discord.com/settings/account>."
    ))
    view.add_item(c)
    return view


# =============================================================================
# DYNAMIC ITEM — USER ALERT: MESSAGE INFO
# =============================================================================

class UserDetailsButton(
    ui.DynamicItem[ui.Button],
    template=r"moddy:td:user:details:(?P<ck>[0-9a-f]{10}):(?P<mid>\d+):(?P<cid>\d+)",
):
    """Shows where and by whom the user token was posted."""

    def __init__(self, ck: str, mid: int, cid: int) -> None:
        super().__init__(
            ui.Button(
                label="Message Info",
                style=discord.ButtonStyle.secondary,
                emoji=discord.PartialEmoji.from_str(INFO),
                custom_id=f"moddy:td:user:details:{ck}:{mid}:{cid}",
            )
        )
        self.ck, self.mid, self.cid = ck, mid, cid

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: ui.Button,
        match: re.Match,
    ) -> "UserDetailsButton":
        return cls(match.group("ck"), int(match.group("mid")), int(match.group("cid")))

    async def callback(self, interaction: discord.Interaction) -> None:
        data = peek_alert(self.ck)
        if data is None:
            await interaction.response.send_message(
                view=_expired_view(), ephemeral=True
            )
            return

        ts = data.get("timestamp", 0)
        masked = data.get("masked_content", "*unavailable*")
        body = (
            f"**Server:** `{data.get('guild_name', '?')}` (`{data.get('guild_id', '?')}`)\n"
            f"**Channel:** `#{data.get('channel_name', '?')}` (`{data.get('channel_id', '?')}`)\n"
            f"**Message ID:** `{self.mid}`\n"
            f"**Author:** `{data.get('author_name', '?')}` (`{data.get('author_id', '?')}`)\n"
            f"**Posted at:** <t:{ts}:F>\n\n"
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
# DYNAMIC ITEM — USER ALERT: INVALIDATE TOKEN (step 1 — confirm prompt)
# =============================================================================

class UserInvalidateButton(
    ui.DynamicItem[ui.Button],
    template=r"moddy:td:user:invalidate:(?P<ck>[0-9a-f]{10})",
):
    """Prompts the user to confirm before revoking their token."""

    def __init__(self, ck: str) -> None:
        super().__init__(
            ui.Button(
                label="Invalidate Token",
                style=discord.ButtonStyle.danger,
                emoji=discord.PartialEmoji.from_str(LOGOUT),
                custom_id=f"moddy:td:user:invalidate:{ck}",
            )
        )
        self.ck = ck

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: ui.Button,
        match: re.Match,
    ) -> "UserInvalidateButton":
        return cls(match.group("ck"))

    async def callback(self, interaction: discord.Interaction) -> None:
        if peek_alert(self.ck) is None:
            await interaction.response.send_message(
                view=_expired_view(), ephemeral=True
            )
            return

        view = BaseView()
        c = ui.Container()
        c.add_item(ui.TextDisplay(
            f"### {WARNING} Confirm Token Invalidation\n"
            "Invalidating your token will **log you out of all sessions on all devices** "
            "immediately. You will need to sign in again.\n\n"
            f"{WARNING} **This action is instant and cannot be undone.**"
        ))
        c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        row = ui.ActionRow()
        row.add_item(UserConfirmInvalidateButton(self.ck))
        row.add_item(UserCancelButton())
        c.add_item(row)
        view.add_item(c)

        await interaction.response.send_message(view=view, ephemeral=True)


# =============================================================================
# DYNAMIC ITEM — USER ALERT: INVALIDATE TOKEN (step 2 — execute)
# =============================================================================

class UserConfirmInvalidateButton(
    ui.DynamicItem[ui.Button],
    template=r"moddy:td:user:confirm:(?P<ck>[0-9a-f]{10})",
):
    """Calls POST /auth/logout with the user's token after confirmation."""

    def __init__(self, ck: str) -> None:
        super().__init__(
            ui.Button(
                label="Yes, log me out",
                style=discord.ButtonStyle.danger,
                emoji=discord.PartialEmoji.from_str(LOGOUT),
                custom_id=f"moddy:td:user:confirm:{ck}",
            )
        )
        self.ck = ck

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: ui.Button,
        match: re.Match,
    ) -> "UserConfirmInvalidateButton":
        return cls(match.group("ck"))

    async def callback(self, interaction: discord.Interaction) -> None:
        data = consume_alert(self.ck)
        if data is None:
            await interaction.response.edit_message(view=_expired_view())
            return

        token = data.get("token", "")
        bot = interaction.client

        async with aiohttp.ClientSession() as session:
            result = await _api_post(
                session, "/auth/logout", token, bot_token=False,
                body={"provider": None, "voip_provider": None},
            )

        view = BaseView()
        c = ui.Container()
        if result is not None:
            c.add_item(ui.TextDisplay(
                f"### {DONE} Token Invalidated\n"
                "Your token has been successfully invalidated. "
                "You have been logged out of all sessions.\n\n"
                "**Next steps:**\n"
                "- Log back into Discord\n"
                "- Enable Two-Factor Authentication (2FA) for added security\n"
                "- Consider changing your password if you haven't already"
            ))
        else:
            c.add_item(ui.TextDisplay(
                f"### {ERROR} Invalidation Failed\n"
                "We couldn't invalidate your token automatically. "
                "Please change your password immediately at "
                "<https://discord.com/settings/account> to force all sessions to end."
            ))
        view.add_item(c)
        await interaction.response.edit_message(view=view)


# =============================================================================
# DYNAMIC ITEM — USER ALERT: CANCEL (shared)
# =============================================================================

class UserCancelButton(
    ui.DynamicItem[ui.Button],
    template=r"moddy:td:user:cancel",
):
    """Dismisses the current ephemeral confirmation."""

    def __init__(self) -> None:
        super().__init__(
            ui.Button(
                label="Cancel",
                style=discord.ButtonStyle.secondary,
                custom_id="moddy:td:user:cancel",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: ui.Button,
        match: re.Match,
    ) -> "UserCancelButton":
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        view = BaseView()
        c = ui.Container()
        c.add_item(ui.TextDisplay(
            f"### {DONE} Cancelled\n"
            "No action was taken. Your original security alert is still active."
        ))
        view.add_item(c)
        await interaction.response.edit_message(view=view)


# =============================================================================
# DYNAMIC ITEM — USER ALERT: DELETE MESSAGE
# =============================================================================

class UserDeleteMsgButton(
    ui.DynamicItem[ui.Button],
    template=r"moddy:td:user:delete:(?P<ck>[0-9a-f]{10}):(?P<mid>\d+):(?P<cid>\d+)",
):
    """Deletes the message that contained the token."""

    def __init__(self, ck: str, mid: int, cid: int) -> None:
        super().__init__(
            ui.Button(
                label="Delete Message",
                style=discord.ButtonStyle.danger,
                emoji=discord.PartialEmoji.from_str(DELETE),
                custom_id=f"moddy:td:user:delete:{ck}:{mid}:{cid}",
            )
        )
        self.ck, self.mid, self.cid = ck, mid, cid

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: ui.Button,
        match: re.Match,
    ) -> "UserDeleteMsgButton":
        return cls(match.group("ck"), int(match.group("mid")), int(match.group("cid")))

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        bot = interaction.client
        deleted = False

        # First try: delete via the bot (requires Manage Messages in that channel)
        try:
            channel = bot.get_channel(self.cid) or await bot.fetch_channel(self.cid)
            msg = await channel.fetch_message(self.mid)
            await msg.delete()
            deleted = True
        except discord.Forbidden:
            pass
        except Exception:
            pass

        # Fallback: delete via user token
        if not deleted:
            data = peek_alert(self.ck)
            if data:
                token = data.get("token", "")
                async with aiohttp.ClientSession() as session:
                    result = await _delete_via_token(session, token, self.cid, self.mid)
                    deleted = result

        view = BaseView()
        c = ui.Container()
        if deleted:
            c.add_item(ui.TextDisplay(
                f"### {DONE} Message Deleted\n"
                "The message containing your token has been deleted."
            ))
        else:
            c.add_item(ui.TextDisplay(
                f"### {ERROR} Could Not Delete\n"
                "We were unable to delete the message automatically. "
                "Please delete it manually if you still have access to the channel."
            ))
        view.add_item(c)
        await interaction.followup.send(view=view, ephemeral=True)


# =============================================================================
# DYNAMIC ITEM — USER ALERT: CHANGE PASSWORD
# =============================================================================

class UserResetPwButton(
    ui.DynamicItem[ui.Button],
    template=r"moddy:td:user:resetpw:(?P<ck>[0-9a-f]{10})",
):
    """Triggers a password-reset email via POST /auth/forgot."""

    def __init__(self, ck: str) -> None:
        super().__init__(
            ui.Button(
                label="Change Password",
                style=discord.ButtonStyle.primary,
                emoji=discord.PartialEmoji.from_str(SETTINGS),
                custom_id=f"moddy:td:user:resetpw:{ck}",
            )
        )
        self.ck = ck

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: ui.Button,
        match: re.Match,
    ) -> "UserResetPwButton":
        return cls(match.group("ck"))

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        data = peek_alert(self.ck)
        if data is None:
            await interaction.followup.send(view=_expired_view(), ephemeral=True)
            return

        email = data.get("email")
        view = BaseView()
        c = ui.Container()

        if not email:
            c.add_item(ui.TextDisplay(
                f"### {ERROR} Email Unavailable\n"
                "We couldn't retrieve your email address to send a reset link. "
                "Please reset your password manually at <https://discord.com/settings/account>."
            ))
        else:
            async with aiohttp.ClientSession() as session:
                result = await _api_post(
                    session, "/auth/forgot", "", bot_token=False,
                    body={"login": email},
                )
            if result is not None:
                c.add_item(ui.TextDisplay(
                    f"### {DONE} Password Reset Email Sent\n"
                    f"A password reset link has been sent to **{email}**.\n"
                    "Follow the instructions in the email to set a new password.\n\n"
                    "-# After resetting your password all active sessions will be invalidated."
                ))
            else:
                c.add_item(ui.TextDisplay(
                    f"### {ERROR} Reset Failed\n"
                    "We couldn't send a reset email automatically. "
                    "Please visit <https://discord.com/settings/account> to change your password."
                ))

        view.add_item(c)
        await interaction.followup.send(view=view, ephemeral=True)


# =============================================================================
# DYNAMIC ITEM — BOT ALERT: MESSAGE INFO
# =============================================================================

class BotDetailsButton(
    ui.DynamicItem[ui.Button],
    template=r"moddy:td:bot:details:(?P<ck>[0-9a-f]{10}):(?P<mid>\d+):(?P<cid>\d+)",
):
    """Shows where and by whom the bot token was posted."""

    def __init__(self, ck: str, mid: int, cid: int) -> None:
        super().__init__(
            ui.Button(
                label="Message Info",
                style=discord.ButtonStyle.secondary,
                emoji=discord.PartialEmoji.from_str(INFO),
                custom_id=f"moddy:td:bot:details:{ck}:{mid}:{cid}",
            )
        )
        self.ck, self.mid, self.cid = ck, mid, cid

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: ui.Button,
        match: re.Match,
    ) -> "BotDetailsButton":
        return cls(match.group("ck"), int(match.group("mid")), int(match.group("cid")))

    async def callback(self, interaction: discord.Interaction) -> None:
        data = peek_alert(self.ck)
        if data is None:
            await interaction.response.send_message(
                view=_expired_view(), ephemeral=True
            )
            return

        ts = data.get("timestamp", 0)
        masked = data.get("masked_content", "*unavailable*")
        body = (
            f"**Server:** `{data.get('guild_name', '?')}` (`{data.get('guild_id', '?')}`)\n"
            f"**Channel:** `#{data.get('channel_name', '?')}` (`{data.get('channel_id', '?')}`)\n"
            f"**Message ID:** `{self.mid}`\n"
            f"**Author:** `{data.get('author_name', '?')}` (`{data.get('author_id', '?')}`)\n"
            f"**Posted at:** <t:{ts}:F>\n\n"
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
    ui.DynamicItem[ui.Button],
    template=r"moddy:td:bot:delete:(?P<ck>[0-9a-f]{10}):(?P<mid>\d+):(?P<cid>\d+)",
):
    """Deletes the message that contained the bot token."""

    def __init__(self, ck: str, mid: int, cid: int) -> None:
        super().__init__(
            ui.Button(
                label="Delete Message",
                style=discord.ButtonStyle.danger,
                emoji=discord.PartialEmoji.from_str(DELETE),
                custom_id=f"moddy:td:bot:delete:{ck}:{mid}:{cid}",
            )
        )
        self.ck, self.mid, self.cid = ck, mid, cid

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: ui.Button,
        match: re.Match,
    ) -> "BotDeleteMsgButton":
        return cls(match.group("ck"), int(match.group("mid")), int(match.group("cid")))

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        bot = interaction.client
        deleted = False

        try:
            channel = bot.get_channel(self.cid) or await bot.fetch_channel(self.cid)
            msg = await channel.fetch_message(self.mid)
            await msg.delete()
            deleted = True
        except discord.Forbidden:
            pass
        except Exception:
            pass

        view = BaseView()
        c = ui.Container()
        if deleted:
            c.add_item(ui.TextDisplay(
                f"### {DONE} Message Deleted\n"
                "The message containing your bot token has been deleted."
            ))
        else:
            c.add_item(ui.TextDisplay(
                f"### {ERROR} Could Not Delete\n"
                "We were unable to delete the message automatically. "
                "Please delete it manually and regenerate your bot token in the "
                "[Developer Portal](https://discord.com/developers/applications)."
            ))
        view.add_item(c)
        await interaction.followup.send(view=view, ephemeral=True)


# =============================================================================
# API HELPERS
# =============================================================================

async def _delete_via_token(
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
    """Use a user's own token to create a DM channel with the bot. Returns channel_id."""
    result = await _api_post(
        session,
        "/users/@me/channels",
        user_token,
        bot_token=False,
        body={"recipient_id": str(bot_id)},
    )
    if result and "id" in result:
        return int(result["id"])
    return None


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
) -> BaseView:
    view = BaseView()
    c = ui.Container()

    c.add_item(ui.TextDisplay(
        f"### {WARNING} Security Alert — Your Discord Token Was Detected"
    ))
    c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    c.add_item(ui.TextDisplay(
        "Your Discord account token was found in a **public message**. "
        "Anyone with your token can access your account as if they were you.\n\n"
        f"**Detected in:** `{guild_name}` › `#{channel_name}`\n"
        f"**Posted by:** `{author_name}` (`{author_id}`)\n"
        f"**At:** <t:{timestamp}:F>"
    ))
    c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    c.add_item(ui.TextDisplay(
        "-# This alert was sent automatically by Moddy to protect your account. "
        "We do not store your token. Take action immediately."
    ))

    row = ui.ActionRow()
    row.add_item(UserDetailsButton(ck, msg_id, channel_id))
    row.add_item(UserInvalidateButton(ck))
    row.add_item(UserDeleteMsgButton(ck, msg_id, channel_id))
    row.add_item(UserResetPwButton(ck))
    c.add_item(row)

    view.add_item(c)
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
) -> BaseView:
    view = BaseView()
    c = ui.Container()

    c.add_item(ui.TextDisplay(
        f"### {WARNING} Security Alert — Your Bot Token Was Exposed"
    ))
    c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    c.add_item(ui.TextDisplay(
        f"The token for your bot **{bot_name}** (`{bot_id}`) was found in a **public message**. "
        "Anyone with this token can control your bot with full API access.\n\n"
        f"**Detected in:** `{guild_name}` › `#{channel_name}`\n"
        f"**Posted by:** `{author_name}` (`{author_id}`)\n"
        f"**At:** <t:{timestamp}:F>"
    ))
    c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    c.add_item(ui.TextDisplay(
        "-# This alert was sent automatically by Moddy. "
        "Regenerate your bot token in the Developer Portal immediately."
    ))

    row = ui.ActionRow()
    row.add_item(BotDetailsButton(ck, msg_id, channel_id))
    row.add_item(BotDeleteMsgButton(ck, msg_id, channel_id))
    # Link button to the Developer Portal for this specific app
    row.add_item(ui.Button(
        label="Reset Token (Dev Portal)",
        style=discord.ButtonStyle.link,
        url=f"https://discord.com/developers/applications/{bot_id}/bot",
    ))
    c.add_item(row)

    view.add_item(c)
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
) -> bool:
    """Send a DM to *user_id*. Falls back to opening DM via the user's token."""
    # Attempt 1: normal bot DM
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        await user.send(view=view)
        return True
    except (discord.Forbidden, discord.HTTPException):
        pass
    except Exception as exc:
        logger.debug(f"Unexpected error sending DM to {user_id}: {exc}")

    # Attempt 2: open DM channel using the user's own token, then send via bot
    if user_token:
        try:
            ch_id = await _open_dm_via_user_token(session, user_token, bot.user.id)
            if ch_id:
                channel = bot.get_channel(ch_id) or await bot.fetch_channel(ch_id)
                await channel.send(view=view)
                return True
        except Exception as exc:
            logger.debug(f"Fallback DM (user token) failed for {user_id}: {exc}")

    return False


# =============================================================================
# MAIN COG
# =============================================================================

# Tracks message IDs being processed to avoid duplicate handling of edits/reposts.
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
        # Ignore bots and DMs (no guild context to detect leaks in)
        if message.author.bot or not message.guild:
            return
        if not message.content:
            return

        matches = _TOKEN_RE.findall(message.content)
        if not matches:
            return

        # Dedup: skip if we're already handling this message
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
        """Validate a candidate token and alert if it's real."""
        async with aiohttp.ClientSession() as session:
            is_mfa = token.startswith("mfa.")
            is_bot_token = not is_mfa  # we'll determine properly below

            # ── Step 1: decode first segment for a user_id guess ────────────
            uid_guess = None
            if not is_mfa:
                uid_guess = _decode_user_id(token)

            # ── Step 2: lookup the user/bot by ID (uses bot token) ──────────
            target_user_data: Optional[dict] = None
            if uid_guess:
                target_user_data = await _api_get(
                    session, f"/users/{uid_guess}", self.bot.http.token, bot_token=True
                )

            # ── Step 3: determine token type ────────────────────────────────
            # Try user token first (no "Bot" prefix)
            me_data = await _api_get(session, "/users/@me", token, bot_token=False)

            if me_data and me_data.get("id"):
                # Token is valid as a user token
                await self._alert_user(session, message, token, me_data)
                return

            # Try as bot token
            bot_me = await _api_get(session, "/users/@me", token, bot_token=True)
            if bot_me and bot_me.get("id") and bot_me.get("bot"):
                await self._alert_bot(session, message, token, bot_me)
                return

            # Token didn't validate — silent skip (avoid alerting on false positives)
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
        author_name = str(message.author)
        ts = int(message.created_at.timestamp())
        guild = message.guild
        channel = message.channel
        masked = _redact(message.content, token)

        email = me_data.get("email")  # populated if the token has email scope

        payload = {
            "token": token,
            "email": email,
            "masked_content": masked,
            "msg_id": message.id,
            "channel_id": channel.id,
            "channel_name": getattr(channel, "name", str(channel.id)),
            "guild_id": guild.id,
            "guild_name": guild.name,
            "author_id": message.author.id,
            "author_name": author_name,
            "timestamp": ts,
        }
        ck = cache_alert(payload)

        view = _build_user_alert_view(
            ck=ck,
            msg_id=message.id,
            channel_id=channel.id,
            guild_name=guild.name,
            channel_name=getattr(channel, "name", str(channel.id)),
            author_id=message.author.id,
            author_name=author_name,
            timestamp=ts,
        )

        sent = await _send_dm(self.bot, session, user_id, view, user_token=token)
        if sent:
            logger.info(
                f"User token alert sent to user {user_id} "
                f"(token found in message {message.id} in guild {guild.id})."
            )
        else:
            logger.warning(
                f"Could not DM user {user_id} — token alert not delivered "
                f"(message {message.id}, guild {guild.id})."
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
        author_name = str(message.author)
        ts = int(message.created_at.timestamp())
        guild = message.guild
        channel = message.channel
        masked = _redact(message.content, token)

        # Get application info to find the owner / team
        app_data = await _api_get(
            session, "/oauth2/applications/@me", token, bot_token=True
        )

        payload = {
            "token": token,
            "masked_content": masked,
            "msg_id": message.id,
            "channel_id": channel.id,
            "channel_name": getattr(channel, "name", str(channel.id)),
            "guild_id": guild.id,
            "guild_name": guild.name,
            "author_id": message.author.id,
            "author_name": author_name,
            "timestamp": ts,
            "bot_id": bot_id,
            "bot_name": bot_name,
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
            author_name=author_name,
            timestamp=ts,
        )

        targets: list[int] = []

        if app_data:
            team = app_data.get("team")
            owner = app_data.get("owner", {})

            if team:
                # Alert the team owner and all members
                team_owner_id = (team.get("owner_user") or {}).get("id")
                if team_owner_id:
                    targets.append(int(team_owner_id))
                for member in team.get("members", []):
                    member_id = (member.get("user") or {}).get("id")
                    if member_id and int(member_id) not in targets:
                        targets.append(int(member_id))
            elif owner.get("id"):
                targets.append(int(owner["id"]))

        if not targets:
            # Fallback: try to get owner from bot's own user data
            if bot_id:
                targets.append(bot_id)

        for uid in targets:
            sent = await _send_dm(self.bot, session, uid, view, user_token=None)
            if sent:
                logger.info(
                    f"Bot token alert sent to owner/team member {uid} "
                    f"for bot {bot_id} (message {message.id}, guild {guild.id})."
                )
            else:
                logger.warning(
                    f"Could not DM {uid} — bot token alert not delivered "
                    f"(message {message.id}, guild {guild.id})."
                )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TokenDetector(bot))
