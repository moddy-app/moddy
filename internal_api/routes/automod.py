"""
Internal automod routes.

Exposes the automod AI safety checks over HTTP so the backend (dashboard) can
reuse the *exact same* guard as the bot's own `/config` panel instead of
re-implementing the heuristic on its side.

Currently:
- POST /automod/rules_check — validate an `indications` text before it is
  written into `guilds.data.modules.automod_ai.indications`.

The text is embedded verbatim into the moderation engine's system prompt, so it
must never be saved without passing `automod.rules_check.validate_rules`
(call type `automod_rules_check`). The check fails closed: an AI/gateway
outage yields `ok: false`, never a silent pass.

Authentication: `Authorization: Bearer {INTERNAL_API_SECRET}` — same contract as
the rest of the internal API.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from automod.rules_check import validate_rules, MAX_RULES_LENGTH

logger = logging.getLogger("moddy.internal_api.automod")

router = APIRouter(prefix="/automod", tags=["automod"])

# Discord snowflakes are 64-bit ids; anything outside this range is malformed.
_MIN_SNOWFLAKE = 1
_MAX_SNOWFLAKE = (1 << 64) - 1

# `reason` must be displayable as-is on the dashboard, so the reason codes
# returned by validate_rules() are expanded into full sentences here. The AI
# reason (`unsafe` case) is already a human sentence and is passed through.
_REASONS = {
    "fr": {
        "too_long": f"Les indications sont trop longues (max {MAX_RULES_LENGTH} caractères).",
        "unavailable": (
            "Impossible de vérifier les indications pour le moment "
            "(service IA indisponible). Réessayez plus tard."
        ),
        "unsafe": "Les indications ressemblent à une tentative de manipulation de l'IA.",
        "unsafe_detail": (
            "Les indications ressemblent à une tentative de manipulation de l'IA. "
            "Raison : {reason}"
        ),
    },
    "en-US": {
        "too_long": f"The guidance is too long (max {MAX_RULES_LENGTH} characters).",
        "unavailable": (
            "Can't verify the guidance right now (AI service unavailable). "
            "Try again later."
        ),
        "unsafe": "The guidance looks like an attempt to manipulate the AI.",
        "unsafe_detail": (
            "The guidance looks like an attempt to manipulate the AI. Reason: {reason}"
        ),
    },
}


def _strings(locale: str) -> dict:
    """Resolve a request locale to a reason string table (French by default)."""
    if isinstance(locale, str) and locale.lower().startswith("en"):
        return _REASONS["en-US"]
    return _REASONS["fr"]


def _error(status: int, code: str, message: str) -> JSONResponse:
    """Explicit failure — never a silent pass, never a bare 500."""
    return JSONResponse(
        status_code=status,
        content={"ok": False, "error": code, "reason": message},
    )


@router.post("/rules_check")
async def rules_check(request: Request):
    """Run the anti prompt-injection check on an `indications` text.

    Body: `{"guild_id": "123...", "indications": "...", "locale": "fr"}`
    (`locale` is optional and only picks the language of `reason`).

    Returns `{"ok": true}` or `{"ok": false, "reason": "..."}` with `reason`
    ready to be shown to the admin.
    """
    # Deferred import: avoids a circular import at module load time.
    from ..server import check_auth, get_bot

    if not check_auth(request):
        return _error(401, "unauthorized", "Unauthorized")

    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return _error(400, "invalid_json", "Invalid JSON body")
    if not isinstance(payload, dict):
        return _error(400, "invalid_body", "Body must be a JSON object")

    # --- guild_id ---------------------------------------------------------
    raw_guild_id = payload.get("guild_id")
    if raw_guild_id is None or (isinstance(raw_guild_id, str) and not raw_guild_id.strip()):
        return _error(400, "missing_guild_id", "Missing 'guild_id'")
    try:
        guild_id = int(str(raw_guild_id).strip())
    except (TypeError, ValueError):
        return _error(400, "invalid_guild_id", f"Invalid guild_id: {raw_guild_id!r}")
    if not (_MIN_SNOWFLAKE <= guild_id <= _MAX_SNOWFLAKE):
        return _error(400, "invalid_guild_id", f"Invalid guild_id: {raw_guild_id!r}")

    # --- indications ------------------------------------------------------
    indications = payload.get("indications")
    if indications is None:
        return _error(400, "missing_indications", "Missing 'indications'")
    if not isinstance(indications, str):
        return _error(400, "invalid_indications", "'indications' must be a string")

    strings = _strings(payload.get("locale") or "fr")

    # --- bot / guild reachability ----------------------------------------
    bot = get_bot()
    if bot is None or not bot.is_ready():
        return _error(503, "bot_not_ready", strings["unavailable"])
    if bot.get_guild(guild_id) is None:
        return _error(404, "unknown_guild", f"Unknown guild: {guild_id}")

    # --- the check itself (same code path as the bot's config panel) ------
    text = indications.strip()
    if not text:
        # Clearing the field needs no AI call — mirrors the panel's behaviour.
        return {"ok": True}

    safe, reason = await validate_rules(bot, guild_id, text)
    if safe:
        return {"ok": True}

    if reason in ("too_long", "unavailable"):
        message = strings[reason]
        code = reason
    elif reason:
        message = strings["unsafe_detail"].format(reason=reason)
        code = "unsafe"
    else:
        message = strings["unsafe"]
        code = "unsafe"

    logger.info("automod rules_check rejected (guild %s): %s", guild_id, code)
    return {"ok": False, "reason": message, "code": code}
