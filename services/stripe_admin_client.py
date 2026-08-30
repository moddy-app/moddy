"""Stripe admin actions — signed request/response over Redis Pub/Sub.

Wire contract (see docs/REDIS_COMMUNICATION.md):

- **Request** (bot -> backend): published on ``moddy:dashboard`` as
  ``{"type": "stripe_action", "action": ..., "request_id": ..., "discord_id": ...,
  ...action fields}``.
- **Response** (backend -> bot): published on ``moddy:bot`` as
  ``{"type": "stripe_action_result", "request_id": ..., "ok": bool, ...}``,
  correlated to the request by ``request_id``.

Both directions are HMAC-SHA256 signed with the shared ``TASK_STREAM_SECRET``
(the same secret as ``moddy:tasks``, see docs/TASK_SIGNATURE.md) — this channel
carries real money movement (refunds, subscription cancellation), so an
unsigned or forged message must never be actionable. The signature covers the
whole message body (every field except ``signature`` itself, plus an
``issued_at`` timestamp added at signing time), canonicalized as compact,
sorted-key JSON.

An incoming reply is only trusted once verified: a bad signature is logged and
dropped, never surfaced as ``ok: true`` to the caller.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, Optional
from uuid import uuid4

logger = logging.getLogger('moddy.services.stripe_admin')

DASHBOARD_CHANNEL = "moddy:dashboard"
REPLY_TYPE = "stripe_action_result"

DEFAULT_TIMEOUT = 15  # seconds — Stripe API round-trips can be slow
REPLAY_WINDOW = 300   # seconds, mirrors utils/task_signature.py
CLOCK_SKEW = 60


def sign_event(event: Dict[str, Any], secret: str) -> Dict[str, Any]:
    """Sign a Stripe admin event: adds ``issued_at`` and ``signature``."""
    body = {k: v for k, v in event.items() if k != "signature"}
    body["issued_at"] = str(int(time.time()))
    canonical = json.dumps(body, separators=(",", ":"), sort_keys=True, ensure_ascii=True)
    body["signature"] = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return body


def verify_event(event: Dict[str, Any], secret: str) -> bool:
    """Verify a signed Stripe admin event. Does not check freshness — see :func:`is_fresh`."""
    signature = event.get("signature")
    if not signature or not secret:
        return False
    body = {k: v for k, v in event.items() if k != "signature"}
    canonical = json.dumps(body, separators=(",", ":"), sort_keys=True, ensure_ascii=True)
    expected = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(str(signature), expected)


def is_fresh(event: Dict[str, Any], now: Optional[int] = None) -> bool:
    """Reject a signed event whose ``issued_at`` is stale or from the future."""
    try:
        issued_at = int(event.get("issued_at"))
    except (TypeError, ValueError):
        return False
    now = int(time.time()) if now is None else int(now)
    return (now - REPLAY_WINDOW) <= issued_at <= (now + CLOCK_SKEW)


class StripeAdminClient:
    """Sends signed Stripe admin actions and correlates their signed replies."""

    def __init__(self, bot):
        self.bot = bot
        self._pending: Dict[str, asyncio.Future] = {}

    @property
    def redis(self):
        return getattr(self.bot, "redis", None)

    @property
    def _secret(self) -> str:
        from config import TASK_STREAM_SECRET
        return TASK_STREAM_SECRET

    async def cancel_subscription(self, discord_id: int, immediate: bool = False) -> Dict[str, Any]:
        return await self.send_action(
            "cancel_subscription", discord_id, immediate=immediate,
        )

    async def resume_subscription(self, discord_id: int) -> Dict[str, Any]:
        return await self.send_action("resume_subscription", discord_id)

    async def refund(
        self,
        discord_id: int,
        payment_intent_id: Optional[str] = None,
        amount_cents: Optional[int] = None,
    ) -> Dict[str, Any]:
        fields: Dict[str, Any] = {}
        if payment_intent_id is not None:
            fields["payment_intent_id"] = payment_intent_id
        if amount_cents is not None:
            fields["amount_cents"] = amount_cents
        return await self.send_action("refund", discord_id, **fields)

    async def start_trial(
        self,
        discord_id: int,
        email: str,
        plan: str = "monthly",
        trial_days: int = 7,
    ) -> Dict[str, Any]:
        return await self.send_action(
            "start_trial", discord_id, email=email, plan=plan, trial_days=trial_days,
        )

    async def send_action(
        self, action: str, discord_id: int, timeout: int = DEFAULT_TIMEOUT, **fields: Any
    ) -> Dict[str, Any]:
        """Sign, publish and await the correlated reply for a Stripe admin action."""
        if not self.redis:
            return {"ok": False, "error": "service_unavailable"}
        if not self._secret:
            logger.error("[StripeAdmin] TASK_STREAM_SECRET not configured — action not sent")
            return {"ok": False, "error": "service_unavailable"}

        request_id = str(uuid4())
        event = sign_event({
            "type": "stripe_action",
            "action": action,
            "request_id": request_id,
            "discord_id": str(discord_id),
            **fields,
        }, self._secret)

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[request_id] = future

        try:
            await self.redis.publish(DASHBOARD_CHANNEL, json.dumps(event))
        except Exception as e:
            self._pending.pop(request_id, None)
            logger.error(f"[StripeAdmin] Failed to publish '{action}': {e}")
            return {"ok": False, "error": "service_unavailable"}

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"[StripeAdmin] Timeout waiting for reply to '{action}' ({request_id})")
            return {"ok": False, "error": "timeout"}
        finally:
            self._pending.pop(request_id, None)

    def handle_reply(self, data: Dict[str, Any]) -> None:
        """Handle a ``stripe_action_result`` event received on ``moddy:bot``.

        Called from ``bot.py::_handle_bot_event``. A reply that fails
        signature verification or freshness is logged and dropped — its
        ``ok`` field, true or false, is never trusted or surfaced.
        """
        request_id = data.get("request_id")
        if not request_id:
            logger.warning("[StripeAdmin] Reply without request_id — ignored")
            return

        if not self._secret or not verify_event(data, self._secret):
            logger.warning(
                f"[StripeAdmin] Reply for request_id={request_id} failed signature "
                "verification — ignored"
            )
            return
        if not is_fresh(data):
            logger.warning(
                f"[StripeAdmin] Reply for request_id={request_id} is stale or "
                "dated in the future — ignored"
            )
            return

        future = self._pending.get(request_id)
        if future and not future.done():
            future.set_result(data)
        else:
            logger.debug(f"[StripeAdmin] Reply for unknown/already-resolved request_id={request_id}")
