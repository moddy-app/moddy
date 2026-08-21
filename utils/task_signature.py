"""HMAC-SHA256 verification for the ``moddy:tasks`` Redis stream.

The stream carries tasks with Discord side effects (bot avatar/bio changes,
announcements, panel updates, guild sanctions). Anyone with write access to
Redis could inject an arbitrary task and have the bot execute it with its own
permissions. The backend therefore signs every entry, and the bot refuses
anything it cannot verify.

Wire contract (six string fields per entry):

======== ======================================================================
type      task type (``bot_customization_update``, ``send_announcement``, …)
guild_id  decimal guild id, ``"0"`` when there is no guild
payload   the business payload, JSON-serialized compact + sorted
task_id   UUID v4, unique per task — the deduplication key
issued_at Unix timestamp in seconds, UTC
signature lowercase hex HMAC-SHA256 of the five fields above
======== ======================================================================

The canonical string is a *double* serialization: ``payload`` is itself a JSON
string inside the canonical object, so its quotes are escaped. Both levels use
``separators=(",", ":")``, ``sort_keys=True`` and ``ensure_ascii=True`` (the
Python defaults for the last one). The ``signature`` field is excluded from the
computation. See ``docs/TASK_SIGNATURE.md``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any, Mapping, Optional

logger = logging.getLogger('moddy.tasks')

# Fields covered by the signature, in contract order (sorting is applied by
# json.dumps anyway, this tuple only defines *which* fields are signed).
SIGNED_FIELDS = ("type", "guild_id", "payload", "task_id", "issued_at")

# Maximum accepted age of an entry, in seconds.
REPLAY_WINDOW = 300
# Tolerance when the backend clock runs ahead of ours, in seconds.
CLOCK_SKEW = 60
# Lifetime of a dedup marker. MUST stay > REPLAY_WINDOW, otherwise a replay
# becomes possible again while the entry is still considered fresh.
DEDUP_TTL = 600

# Redis key prefix for the anti-replay markers.
SEEN_KEY_PREFIX = "task:seen:"

# Minimum secret length accepted, mirroring the backend.
MIN_SECRET_LENGTH = 32


class TaskRejected(Exception):
    """A stream entry failed verification.

    ``code`` is a short machine-readable reason, used for logging and tests:
    ``no_secret``, ``unsigned``, ``missing_field``, ``bad_signature``,
    ``expired``, ``future``, ``replay``.
    """

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def canonical_payload(payload: Any) -> str:
    """Serialize a business payload the way the backend does before signing."""
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def canonical_string(fields: Mapping[str, str]) -> str:
    """Build the canonical string that is fed to HMAC.

    Raises ``TaskRejected('missing_field')`` when a signed field is absent: an
    entry that does not carry the full contract cannot be verified, so it is
    rejected rather than signed over a partial object.
    """
    signed: dict[str, str] = {}
    for key in SIGNED_FIELDS:
        value = fields.get(key)
        if value is None:
            raise TaskRejected("missing_field", key)
        signed[key] = value if isinstance(value, str) else str(value)
    return json.dumps(signed, separators=(",", ":"), sort_keys=True)


def compute_signature(fields: Mapping[str, str], secret: str) -> str:
    """Return the expected lowercase hex HMAC-SHA256 for ``fields``."""
    canonical = canonical_string(fields)
    return hmac.new(
        secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def sign_fields(
    task_type: str,
    guild_id: int | str,
    payload: Any,
    task_id: str,
    issued_at: int | str,
    secret: str,
) -> dict[str, str]:
    """Build a fully signed set of stream fields.

    The bot does not produce tasks in production — this exists so tests (and
    any local tooling) can generate valid entries with the exact same code
    path the verifier uses.
    """
    fields = {
        "type": task_type,
        "guild_id": str(guild_id),
        "payload": canonical_payload(payload),
        "task_id": str(task_id),
        "issued_at": str(issued_at),
    }
    fields["signature"] = compute_signature(fields, secret)
    return fields


def check_signature(fields: Mapping[str, str], secret: str) -> None:
    """Verify the HMAC of an entry. Raises ``TaskRejected`` on failure."""
    signature = fields.get("signature")
    if not signature:
        # An unsigned entry is exactly the hole the signature closes: never
        # tolerated "for compatibility".
        raise TaskRejected("unsigned")

    expected = compute_signature(fields, secret)
    # compare_digest is mandatory: `==` leaks the signature through timing.
    if not hmac.compare_digest(str(signature), expected):
        raise TaskRejected("bad_signature")


def check_freshness(fields: Mapping[str, str], now: Optional[int] = None) -> None:
    """Verify ``issued_at`` sits inside the accepted window."""
    raw = fields.get("issued_at")
    try:
        issued_at = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise TaskRejected("missing_field", "issued_at")

    now = int(time.time()) if now is None else int(now)
    if issued_at < now - REPLAY_WINDOW:
        raise TaskRejected("expired", f"issued_at={issued_at} now={now}")
    if issued_at > now + CLOCK_SKEW:
        raise TaskRejected("future", f"issued_at={issued_at} now={now}")


async def check_not_replayed(fields: Mapping[str, str], redis) -> None:
    """Consume the entry's ``task_id`` once. Raises on a replay.

    ``SET NX`` is atomic, so two bot instances racing on the same entry can
    never both win. Deduplication is keyed on ``task_id``, not on the content:
    two identical legitimate announcements must be able to go out in a row.
    """
    task_id = fields.get("task_id")
    if not task_id:
        raise TaskRejected("missing_field", "task_id")

    fresh = await redis.set(f"{SEEN_KEY_PREFIX}{task_id}", "1", nx=True, ex=DEDUP_TTL)
    if not fresh:
        raise TaskRejected("replay", f"task_id={task_id}")


async def verify_task(
    fields: Mapping[str, str],
    secret: Optional[str],
    redis,
    *,
    allow_unsigned: bool = False,
    now: Optional[int] = None,
) -> None:
    """Full verification of a ``moddy:tasks`` entry.

    Returns ``None`` when the entry may be executed, raises ``TaskRejected``
    otherwise. Order matters: signature first (cheap, local), then freshness,
    then the anti-replay marker — the marker is only burned for an entry that
    is otherwise valid, so garbage cannot flood the dedup keyspace.

    ``allow_unsigned`` exists solely for the deployment window described in
    ``docs/TASK_SIGNATURE.md`` §6, where the bot ships before the backend
    starts signing. It defaults to off and must be turned back off as soon as
    the backend is in production.
    """
    if not secret:
        # Fail closed: without a secret nothing can be verified, so nothing is
        # executed. The boot-time error in config.py says how to fix it.
        raise TaskRejected("no_secret")

    if not fields.get("signature") and allow_unsigned:
        logger.warning(
            "[Tasks] Unsigned task '%s' accepted because TASK_STREAM_ALLOW_UNSIGNED "
            "is enabled — turn it off once the backend signs.",
            fields.get("type"),
        )
        return

    check_signature(fields, secret)
    check_freshness(fields, now=now)
    await check_not_replayed(fields, redis)
