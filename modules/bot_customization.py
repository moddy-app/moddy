"""
Bot Customization module — makes Moddy look like the server's own bot.

Everything here targets ``PATCH /guilds/{guild_id}/members/@me``, the endpoint
that edits the bot's *per-guild* member profile. Nothing is global: another
server never sees the customization of this one.

Two tiers:

- **Premium** (guild linked to an active subscription, see ``docs/PREMIUM.md``):
  nickname, avatar, banner and bio. The bio keeps a trailing attribution line
  (``Powered by @**Moddy**``) which the server cannot remove.
- **Free**: the *name style* (font, effect, colours) applied to the bot's
  display name. This is the undocumented ``display_name_font_id`` /
  ``display_name_effect_id`` / ``display_name_colors`` trio — see
  ``docs/BOT_CUSTOMIZATION.md``.

Persistence asymmetry, and why ``resync_style`` exists: nickname, avatar,
banner and bio are stored by Discord and survive anything. The name style does **not**
reliably survive a bot restart, so it is re-applied once per process the first
time the module is loaded for a guild.

Every successful or failed change goes through :meth:`apply_customization`,
which is the single place that writes the DB, patches Discord and emits the
technical log — no matter whether the change came from ``/config`` or from the
dashboard through Redis.
"""

from __future__ import annotations

import base64
import copy
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import discord

from modules.module_manager import ModuleBase
from utils.emojis import MODDY_SQUARE
from utils.safe_http import (
    DISALLOWED_CONTENT_TYPE,
    RESPONSE_TOO_LARGE,
    UnsafeURL,
    safe_get_bytes,
)

logger = logging.getLogger("moddy.modules.bot_customization")

MODULE_ID = "bot_customization"

# --------------------------------------------------------------------------- #
# Discord limits
# --------------------------------------------------------------------------- #

MAX_NICKNAME_LENGTH = 32
# Discord caps a member bio at 190 characters. The attribution suffix is part
# of that budget, so the server-editable part is what is left.
MAX_BIO_TOTAL_LENGTH = 190

# The attribution line appended to every customized bio. Servers cannot remove
# it — it is re-appended on every write.
BIO_ATTRIBUTION = "<a:Rocket:1535783839870353499> Powered by @**Moddy**"
BIO_SEPARATOR = "\n"
MAX_BIO_LENGTH = MAX_BIO_TOTAL_LENGTH - len(BIO_ATTRIBUTION) - len(BIO_SEPARATOR)

# Avatar / banner upload guard rails. Discord accepts a bit more, but a
# server-supplied file is downloaded into memory before being base64-encoded,
# so we stay low.
MAX_IMAGE_BYTES = 8 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/gif": "gif",
    "image/webp": "webp",
}

# --------------------------------------------------------------------------- #
# Name styles (undocumented API — tested, see docs/BOT_CUSTOMIZATION.md)
# --------------------------------------------------------------------------- #

# font id -> human name. Discord accepts 1..12 but only these render a font
# that is actually distinguishable; the others are the default face.
FONTS: Dict[int, str] = {
    3: "Cherry Bomb",
    4: "Chicle",
    6: "MuseoModerno",
    7: "Neo-Castel",
    8: "Pixelify Sans",
    10: "Sinistre",
    12: "Zilla Slab",
}

EFFECT_GRADIENT = 2
EFFECT_NEON = 3
EFFECT_TOON = 4
EFFECT_POP = 5

# effect id -> number of colours the API requires for it.
EFFECTS: Dict[int, int] = {
    EFFECT_GRADIENT: 2,
    EFFECT_NEON: 1,
    EFFECT_TOON: 1,
    EFFECT_POP: 1,
}

# Translation key suffix per effect, used by the config UI.
EFFECT_KEYS: Dict[int, str] = {
    EFFECT_GRADIENT: "gradient",
    EFFECT_NEON: "neon",
    EFFECT_TOON: "toon",
    EFFECT_POP: "pop",
}

MAX_COLOR = 0xFFFFFF


class _Unset:
    """Sentinel: 'this field is not part of the change'."""

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return "<unset>"


UNSET = _Unset()


class CustomizationError(Exception):
    """A change was refused. ``code`` is a stable i18n / API error code."""

    def __init__(self, code: str, detail: Optional[str] = None):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def build_bio(user_bio: Optional[str]) -> Optional[str]:
    """Return the bio actually sent to Discord, attribution line included."""
    user_bio = (user_bio or "").strip()
    if not user_bio:
        return BIO_ATTRIBUTION
    return f"{user_bio}{BIO_SEPARATOR}{BIO_ATTRIBUTION}"


def parse_hex_color(raw: Optional[str]) -> Optional[int]:
    """Parse ``#RRGGBB`` / ``RRGGBB`` into a 24-bit int, or None if empty."""
    if raw is None:
        return None
    raw = raw.strip().lstrip("#")
    if not raw:
        return None
    try:
        value = int(raw, 16)
    except ValueError:
        raise CustomizationError("invalid_color", raw)
    if not 0 <= value <= MAX_COLOR:
        raise CustomizationError("invalid_color", raw)
    return value


def color_to_hex(value: Optional[int]) -> str:
    return "" if value is None else f"#{value:06X}"


def normalize_style(style: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate a style dict and return it in canonical form.

    ``{'font_id': None, 'effect_id': None, 'colors': []}`` means "no style".
    """
    style = style or {}
    font_id = style.get("font_id")
    effect_id = style.get("effect_id")
    colors = list(style.get("colors") or [])

    if font_id is not None:
        try:
            font_id = int(font_id)
        except (TypeError, ValueError):
            raise CustomizationError("invalid_font")
        if font_id not in FONTS:
            raise CustomizationError("invalid_font")

    if effect_id is not None:
        try:
            effect_id = int(effect_id)
        except (TypeError, ValueError):
            raise CustomizationError("invalid_effect")
        if effect_id not in EFFECTS:
            raise CustomizationError("invalid_effect")

    parsed: List[int] = []
    for color in colors:
        if isinstance(color, str):
            value = parse_hex_color(color)
            if value is None:
                continue
        else:
            try:
                value = int(color)
            except (TypeError, ValueError):
                raise CustomizationError("invalid_color", str(color))
            if not 0 <= value <= MAX_COLOR:
                raise CustomizationError("invalid_color", str(color))
        parsed.append(value)

    if effect_id is None:
        # An effect is what paints the colours — without one they are ignored.
        parsed = []
    else:
        required = EFFECTS[effect_id]
        if len(parsed) < required:
            raise CustomizationError(
                "gradient_needs_two_colors" if effect_id == EFFECT_GRADIENT
                else "effect_needs_color"
            )
        parsed = parsed[:required]

    return {"font_id": font_id, "effect_id": effect_id, "colors": parsed}


def style_is_empty(style: Optional[Dict[str, Any]]) -> bool:
    style = style or {}
    return style.get("font_id") is None and style.get("effect_id") is None


def to_data_uri(data: bytes, content_type: str) -> str:
    """Build the ``data:image/png;base64,…`` URI Discord expects."""
    mime = content_type.split(";")[0].strip().lower()
    if mime not in ALLOWED_IMAGE_TYPES:
        raise CustomizationError("invalid_image_type", mime)
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


async def download_image(url: str) -> Tuple[bytes, str]:
    """Fetch an image URL, enforcing the type and size guard rails."""
    try:
        response = await safe_get_bytes(
            url,
            max_bytes=MAX_IMAGE_BYTES,
            timeout_seconds=30,
            allowed_content_types=set(ALLOWED_IMAGE_TYPES),
        )
        if response.status != 200:
            raise CustomizationError("image_download_failed", str(response.status))
        return response.body, response.content_type
    except UnsafeURL as exc:
        if exc.code == RESPONSE_TOO_LARGE:
            raise CustomizationError("image_too_large", str(exc)) from exc
        if exc.code == DISALLOWED_CONTENT_TYPE:
            raise CustomizationError("invalid_image_type", str(exc)) from exc
        raise CustomizationError("image_download_failed", str(exc)) from exc
    except CustomizationError:
        raise
    except Exception as exc:
        raise CustomizationError("image_download_failed", str(exc))


# --------------------------------------------------------------------------- #
# Module
# --------------------------------------------------------------------------- #

class BotCustomizationModule(ModuleBase):
    """Per-guild identity and name style for Moddy."""

    MODULE_ID = MODULE_ID
    MODULE_NAME = "Bot Customization"
    MODULE_DESCRIPTION = "Personnalise l'identité de Moddy sur ce serveur"
    MODULE_EMOJI = MODDY_SQUARE

    def __init__(self, bot, guild_id: int):
        super().__init__(bot, guild_id)
        self.nickname: Optional[str] = None
        self.bio: Optional[str] = None
        self.avatar_hash: Optional[str] = None
        self.banner_hash: Optional[str] = None
        self.style: Dict[str, Any] = {"font_id": None, "effect_id": None, "colors": []}

    # ----------------------------------------------------------------- #
    # Configuration
    # ----------------------------------------------------------------- #

    def get_default_config(self) -> Dict[str, Any]:
        return {
            "nickname": None,
            "bio": None,
            "avatar_hash": None,
            "avatar_source": None,
            "banner_hash": None,
            "banner_source": None,
            "style": {"font_id": None, "effect_id": None, "colors": []},
            "updated_at": None,
            "updated_by": None,
        }

    async def load_config(self, config_data: Dict[str, Any]) -> bool:
        try:
            self.config = config_data or {}
            self.nickname = self.config.get("nickname")
            self.bio = self.config.get("bio")
            self.avatar_hash = self.config.get("avatar_hash")
            self.banner_hash = self.config.get("banner_hash")
            raw_style = self.config.get("style") or {}
            self.style = {
                "font_id": raw_style.get("font_id"),
                "effect_id": raw_style.get("effect_id"),
                "colors": list(raw_style.get("colors") or []),
            }
            # "Configured" is what makes the module active — there is no
            # separate on/off switch, an empty configuration is simply off.
            self.enabled = bool(
                self.nickname or self.bio or self.avatar_hash or self.banner_hash
                or not style_is_empty(self.style)
            )
            return True
        except Exception as exc:
            logger.error(f"Error loading bot_customization config: {exc}", exc_info=True)
            return False

    async def validate_config(self, config_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        nickname = config_data.get("nickname")
        if nickname and len(nickname) > MAX_NICKNAME_LENGTH:
            return False, "nickname_too_long"
        bio = config_data.get("bio")
        if bio and len(bio) > MAX_BIO_LENGTH:
            return False, "bio_too_long"
        try:
            normalize_style(config_data.get("style"))
        except CustomizationError as exc:
            return False, exc.code
        return True, None

    # ----------------------------------------------------------------- #
    # Style resync (the only field Discord does not reliably keep)
    # ----------------------------------------------------------------- #

    async def on_enable(self):
        await self.resync_style()

    async def resync_style(self) -> None:
        """Re-apply the stored name style once per process for this guild."""
        if style_is_empty(self.style) or self.bot is None:
            return
        applied = getattr(self.bot, "_bot_customization_styled", None)
        if applied is None:
            applied = set()
            setattr(self.bot, "_bot_customization_styled", applied)
        if self.guild_id in applied:
            return
        applied.add(self.guild_id)

        payload = _style_payload(self.style)
        try:
            await patch_self_member(self.bot, self.guild_id, payload,
                                    reason="Bot customization: name style resync")
            logger.info(f"Name style re-applied for guild {self.guild_id}")
        except Exception as exc:
            logger.warning(f"Could not re-apply name style for guild {self.guild_id}: {exc}")

    # ----------------------------------------------------------------- #
    # The single write path
    # ----------------------------------------------------------------- #

    async def apply_customization(
        self,
        *,
        nickname: Any = UNSET,
        bio: Any = UNSET,
        avatar: Any = UNSET,          # (bytes, content_type) tuple, or None to remove
        avatar_source: Optional[str] = None,   # bookkeeping only (dashboard URL)
        banner: Any = UNSET,          # same shape as avatar
        banner_source: Optional[str] = None,
        style: Any = UNSET,
        actor_id: Optional[int] = None,
        source: str = "config",
    ) -> Dict[str, Any]:
        """Patch Discord, persist the result and emit the technical log.

        Every field defaults to ``UNSET`` (untouched). Passing ``None``
        explicitly resets that field. Raises :class:`CustomizationError` when
        the change is refused; the failure is logged too.
        """
        config = self.get_default_config()
        config.update(copy.deepcopy(self.config or {}))

        payload: Dict[str, Any] = {}
        changes: Dict[str, str] = {}

        if not isinstance(nickname, _Unset):
            nickname = (nickname or "").strip() or None
            if nickname and len(nickname) > MAX_NICKNAME_LENGTH:
                raise CustomizationError("nickname_too_long")
            payload["nick"] = nickname
            changes["nickname"] = nickname or "reset"
            config["nickname"] = nickname

        if not isinstance(bio, _Unset):
            bio = (bio or "").strip() or None
            if bio and len(bio) > MAX_BIO_LENGTH:
                raise CustomizationError("bio_too_long")
            # A customized bio always carries the attribution; clearing the
            # bio clears the whole thing, attribution included.
            payload["bio"] = build_bio(bio) if bio else None
            changes["bio"] = bio or "reset"
            config["bio"] = bio

        # Avatar and banner are the same kind of field (a data URI or null),
        # so they share one code path.
        images = {"avatar": (avatar, avatar_source), "banner": (banner, banner_source)}
        for field, (image, _url) in images.items():
            if isinstance(image, _Unset):
                continue
            if image is None:
                payload[field] = None
                changes[field] = "reset"
                config[f"{field}_hash"] = None
                config[f"{field}_source"] = None
            else:
                data, content_type = image
                if len(data) > MAX_IMAGE_BYTES:
                    raise CustomizationError("image_too_large", str(len(data)))
                payload[field] = to_data_uri(data, content_type)
                changes[field] = f"{len(data)} bytes ({content_type})"

        if not isinstance(style, _Unset):
            normalized = normalize_style(style)
            payload.update(_style_payload(normalized))
            config["style"] = normalized
            changes["style"] = (
                "reset" if style_is_empty(normalized)
                else f"font={normalized['font_id']} effect={normalized['effect_id']} "
                     f"colors={[color_to_hex(c) for c in normalized['colors']]}"
            )

        if not payload:
            return config

        try:
            member_data = await patch_self_member(
                self.bot, self.guild_id, payload,
                reason=f"Bot customization ({source})"
                       + (f" by {actor_id}" if actor_id else ""),
            )
        except discord.HTTPException as exc:
            error = _http_error_code(exc)
            await self._log(changes, success=False, actor_id=actor_id,
                            source=source, error=f"{error}: {exc}")
            raise CustomizationError(error, str(exc))

        for field, (image, url) in images.items():
            if isinstance(image, _Unset) or image is None:
                continue
            config[f"{field}_hash"] = (member_data or {}).get(field)
            config[f"{field}_source"] = url

        config["updated_at"] = datetime.now(timezone.utc).isoformat()
        config["updated_by"] = actor_id

        await self._persist(config)
        await self._log(changes, success=True, actor_id=actor_id, source=source)
        return config

    async def _persist(self, config: Dict[str, Any]) -> None:
        """Save through the module manager so the live instance is refreshed."""
        success, error = await self.bot.module_manager.save_module_config(
            self.guild_id, MODULE_ID, config,
        )
        if not success:
            raise CustomizationError("save_failed", error)
        # save_module_config reloads the cached instance, but `self` may be a
        # short-lived one built by the backend task path.
        await self.load_config(config)

    async def _log(self, changes: Dict[str, str], *, success: bool,
                   actor_id: Optional[int], source: str,
                   error: Optional[str] = None) -> None:
        tech_logger = getattr(self.bot, "tech_logger", None)
        if not tech_logger:
            return
        guild = self.bot.get_guild(self.guild_id)
        try:
            await tech_logger.log_bot_customization(
                guild_id=self.guild_id,
                guild_name=guild.name if guild else None,
                changes=changes,
                actor_id=actor_id,
                source=source,
                success=success,
                error=error,
            )
        except Exception as exc:  # never break a customization over a log
            logger.warning(f"Bot customization tech log failed: {exc}")

    # ----------------------------------------------------------------- #
    # Backend (dashboard) entry point
    # ----------------------------------------------------------------- #

    async def handle_backend_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Apply a dashboard-issued change coming from the Redis task stream.

        Key presence is the contract: an absent key leaves the field alone, an
        explicit ``null`` resets it. Premium fields are re-checked here — the
        bot never trusts the dashboard on entitlement.
        """
        from utils.subscription import is_guild_premium

        actor_id = payload.get("actor_id")
        try:
            actor_id = int(actor_id) if actor_id is not None else None
        except (TypeError, ValueError):
            actor_id = None

        kwargs: Dict[str, Any] = {}
        wants_premium = any(
            k in payload for k in ("nickname", "bio", "avatar_url", "banner_url")
        )
        if wants_premium and not await is_guild_premium(self.bot, self.guild_id):
            return {"ok": False, "error": "premium_required"}

        if "nickname" in payload:
            kwargs["nickname"] = payload["nickname"]
        if "bio" in payload:
            kwargs["bio"] = payload["bio"]
        for field in ("avatar", "banner"):
            key = f"{field}_url"
            if key not in payload:
                continue
            url = payload[key]
            kwargs[field] = None if not url else await download_image(url)
            kwargs[f"{field}_source"] = url or None
        if "style" in payload:
            kwargs["style"] = payload["style"]

        if not kwargs:
            return {"ok": False, "error": "empty_update"}

        try:
            config = await self.apply_customization(
                actor_id=actor_id, source="dashboard", **kwargs
            )
        except CustomizationError as exc:
            return {"ok": False, "error": exc.code, "detail": exc.detail}

        return {
            "ok": True,
            "nickname": config.get("nickname"),
            "bio": config.get("bio"),
            "avatar_hash": config.get("avatar_hash"),
            "banner_hash": config.get("banner_hash"),
            "style": config.get("style"),
        }


# --------------------------------------------------------------------------- #
# Raw API access
# --------------------------------------------------------------------------- #

def _style_payload(style: Dict[str, Any]) -> Dict[str, Any]:
    """Map a canonical style dict onto the three API fields.

    Resetting means sending ``null`` on all three — ``0`` / ``[]`` are refused
    by the API.
    """
    if style_is_empty(style):
        return {
            "display_name_font_id": None,
            "display_name_effect_id": None,
            "display_name_colors": None,
        }
    colors = style.get("colors") or []
    return {
        "display_name_font_id": style.get("font_id"),
        "display_name_effect_id": style.get("effect_id"),
        "display_name_colors": colors or None,
    }


async def patch_self_member(bot, guild_id: int, payload: Dict[str, Any],
                            *, reason: Optional[str] = None) -> Dict[str, Any]:
    """``PATCH /guilds/{guild_id}/members/@me`` — returns the updated member."""
    route = discord.http.Route(
        "PATCH", "/guilds/{guild_id}/members/@me", guild_id=guild_id,
    )
    return await bot.http.request(route, json=payload, reason=reason)


def _http_error_code(exc: discord.HTTPException) -> str:
    if exc.status == 403:
        return "missing_permissions"
    if exc.status == 429:
        return "rate_limited"
    if exc.status == 400:
        return "rejected_by_discord"
    return "discord_error"
