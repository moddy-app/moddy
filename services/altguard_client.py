"""
AltGuard service client — the ONLY contract between the bot and AltGuard.

Two transports, four messages (docs/ALTGUARD.md):

HTTP (bot -> service, ``Authorization: Bearer ALTGUARD_BOT_TOKEN``)
  - ``POST /altguard/token``               consent + ids -> personal, single-use
                                           authorization URL (valid 20 min)
  - ``POST /altguard/membership/resync``   full list of the guild's current
                                           members -> bulk reconciliation

Redis Pub/Sub (same Redis the bot already uses for ``moddy:*``)
  - ``altguard:verdict``     service -> bot   (consumed in ``bot._listen_pubsub``)
  - ``altguard:membership``  bot -> service   (published here)

The bot never reads AltGuard's database and never receives personal data:
everything crossing this boundary is Discord ids, statuses and category labels.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

import aiohttp

from config import ALTGUARD_API_URL, ALTGUARD_BOT_TOKEN

logger = logging.getLogger('moddy.services.altguard')

VERDICT_CHANNEL = "altguard:verdict"
MEMBERSHIP_CHANNEL = "altguard:membership"

# Version of the consent text shown by the bot before any call to
# /altguard/token. Bump it whenever the wording in
# ``locales/*.json -> modules.altguard.consent`` changes materially: the
# service stores it alongside the verification as proof of what was agreed to.
CONSENT_VERSION = "2026-08"

# Membership states understood by the service.
MEMBERSHIP_ACTIVE = "active"
MEMBERSHIP_LEFT = "left"
MEMBERSHIP_KICKED = "kicked"
MEMBERSHIP_BANNED = "banned"

REQUEST_TIMEOUT = 15  # seconds


class AltGuardError(Exception):
    """Base error for AltGuard calls the caller is expected to surface."""

    def __init__(self, code: str, message: str = "", retry_after: Optional[int] = None):
        super().__init__(message or code)
        self.code = code
        self.message = message
        self.retry_after = retry_after


class AltGuardClient:
    """Async client for the AltGuard HTTP endpoints and Redis channels."""

    def __init__(self, bot):
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None

    # ------------------------------------------------------------------ #
    # Plumbing
    # ------------------------------------------------------------------ #

    @property
    def configured(self) -> bool:
        """False when the shared secret is missing — every call is refused."""
        return bool(ALTGUARD_BOT_TOKEN and ALTGUARD_API_URL)

    @property
    def redis(self):
        return getattr(self.bot, "redis", None)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                headers={"Authorization": f"Bearer {ALTGUARD_BOT_TOKEN}"},
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.configured:
            raise AltGuardError("not_configured", "ALTGUARD_BOT_TOKEN is not set")

        session = await self._get_session()
        url = f"{ALTGUARD_API_URL}{path}"
        try:
            async with session.post(url, json=payload) as response:
                # 429 carries Retry-After; the caller decides whether to wait.
                if response.status == 429:
                    retry_after = response.headers.get("Retry-After")
                    raise AltGuardError(
                        "rate_limited",
                        "AltGuard quota exceeded",
                        retry_after=int(retry_after) if retry_after and retry_after.isdigit() else None,
                    )
                if response.status == 401:
                    raise AltGuardError("unauthorized", "Invalid ALTGUARD_BOT_TOKEN")
                if response.status == 400:
                    # A malformed call: never retried automatically.
                    body = await response.text()
                    raise AltGuardError("bad_request", body[:500])
                if response.status >= 500:
                    raise AltGuardError("service_unavailable", f"HTTP {response.status}")
                if response.status >= 300:
                    body = await response.text()
                    raise AltGuardError("unexpected_status", f"HTTP {response.status}: {body[:200]}")
                return await response.json()
        except aiohttp.ClientError as e:
            raise AltGuardError("network_error", str(e)) from e

    # ------------------------------------------------------------------ #
    # HTTP: verification token
    # ------------------------------------------------------------------ #

    async def create_verification_token(
        self,
        *,
        discord_user_id: int,
        guild_id: int,
        consent_at: Optional[datetime] = None,
        consent_version: str = CONSENT_VERSION,
    ) -> Dict[str, Any]:
        """Exchange a recorded consent for a personal authorization URL.

        Returns ``{"authorization_url": ..., "expires_at": ...}``. The URL is
        single-use and expires after 20 minutes: it must be sent to the user
        **ephemerally**, and a new one requested (with the consent screen shown
        again) rather than reused.
        """
        moment = consent_at or datetime.now(timezone.utc)
        payload = {
            "discord_user_id": str(discord_user_id),
            "guild_id": str(guild_id),
            "consent_version": consent_version,
            # ISO 8601, UTC — the service rejects a timestamp in the future.
            "consent_at": moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        return await self._post("/altguard/token", payload)

    # ------------------------------------------------------------------ #
    # HTTP: membership resync
    # ------------------------------------------------------------------ #

    async def resync_membership(
        self, guild_id: int, active_discord_user_ids: Iterable[int],
    ) -> bool:
        """Reconcile one guild's membership in bulk.

        ``active_discord_user_ids`` must be the **complete** set of members
        currently present — the service marks everyone missing from it as
        ``left``. Callers must therefore make sure the member cache is
        trustworthy before calling (an empty list wipes the guild's state).
        Accounts banned on the AltGuard side are never reactivated by a resync.
        """
        ids = [str(user_id) for user_id in active_discord_user_ids]
        result = await self._post(
            "/altguard/membership/resync",
            {"guild_id": str(guild_id), "active_discord_user_ids": ids},
        )
        return bool(result.get("reconciled"))

    # ------------------------------------------------------------------ #
    # Redis: membership events
    # ------------------------------------------------------------------ #

    async def publish_membership(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
        state: str,
        occurred_at: Optional[datetime] = None,
    ) -> bool:
        """Publish one membership transition on ``altguard:membership``.

        Events about accounts AltGuard has never verified are silently ignored
        on its side, so there is nothing to filter here beyond having Redis.
        """
        if state not in (MEMBERSHIP_ACTIVE, MEMBERSHIP_LEFT,
                         MEMBERSHIP_KICKED, MEMBERSHIP_BANNED):
            raise ValueError(f"Unknown membership state: {state!r}")

        redis = self.redis
        if not redis:
            logger.debug("[AltGuard] Redis unavailable — membership event dropped")
            return False

        moment = occurred_at or datetime.now(timezone.utc)
        payload = {
            "event": "membership",
            "guild_id": int(guild_id),
            "discord_user_id": int(discord_user_id),
            "state": state,
            "occurred_at": moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        try:
            await redis.publish(MEMBERSHIP_CHANNEL, json.dumps(payload))
            return True
        except Exception as e:
            logger.error(f"[AltGuard] Failed to publish membership event: {e}")
            return False


def parse_verdict(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Validate an ``altguard:verdict`` payload; return None when unusable.

    The bot must not act on a half-formed message: a missing ``enforced`` flag
    would be read as "apply the sanction" and a missing id would break the
    idempotency guarantee. Both default conservatively instead.
    """
    from db.repositories.altguard import VERDICTS

    verification_id = data.get("verification_id")
    verdict = data.get("verdict")
    if not verification_id or verdict not in VERDICTS:
        return None

    try:
        guild_id = int(data["guild_id"])
        user_id = int(data["discord_user_id"])
    except (KeyError, TypeError, ValueError):
        return None

    score = data.get("score")
    try:
        score = int(score) if score is not None else None
    except (TypeError, ValueError):
        score = None

    reasons = data.get("reasons") or []
    if not isinstance(reasons, list):
        reasons = []

    # Always present on the wire (`[]` on a `passed`), but read defensively
    # with `.get` so a bot deployed ahead of the service still works: a
    # payload without the field just yields no matches, nothing raises.
    matches = []
    for entry in (data.get("matches") or []):
        if not isinstance(entry, dict):
            continue
        try:
            match_user_id = int(entry["discord_user_id"])
        except (KeyError, TypeError, ValueError):
            continue
        match_score = entry.get("score")
        try:
            match_score = int(match_score) if match_score is not None else None
        except (TypeError, ValueError):
            match_score = None
        match_reasons = entry.get("reasons") or []
        if not isinstance(match_reasons, list):
            match_reasons = []
        matches.append({
            "user_id": match_user_id,
            "score": match_score,
            "reasons": [str(r) for r in match_reasons],
        })

    # `enforced` decides whether anything is applied, so an absent field and an
    # explicit `false` must not look alike in the logs: the first is a service
    # that forgot the field (and the guild silently never gets its roles), the
    # second is shadow mode working as intended. Both still default to "apply
    # nothing" — the guarantee that the bot never sanctions on an ambiguous
    # payload is worth more than a convenient default.
    enforced_missing = "enforced" not in data
    if enforced_missing:
        logger.warning(
            "[AltGuard] Verdict %s carries no 'enforced' field — treated as shadow "
            "mode, nothing will be applied. The service must send it explicitly.",
            verification_id,
        )

    return {
        "verification_id": str(verification_id),
        "guild_id": guild_id,
        "user_id": user_id,
        "verdict": verdict,
        "score": score,
        "reasons": [str(r) for r in reasons],
        "matches": matches,
        "enforced": bool(data.get("enforced", False)),
        # Kept so the guild's log card can say "the service never sent the
        # field" rather than the ambiguous "shadow mode".
        "enforced_missing": enforced_missing,
    }
