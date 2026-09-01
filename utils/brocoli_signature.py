"""HMAC-SHA256 identity assertions for Brocoli's HTTP API.

The whole ``/ai`` surface on the backend sits behind a dashboard session, which
the bot does not have. To host a conversation in a Discord channel, the bot
signs an assertion saying *who is typing* — and nothing else.

The bot asserts an **identity**, never a **scope**. It does not claim that the
member is an administrator: the backend asks Discord that question itself. Had
the assertion carried rights, a leaked secret would grant administration of any
server by simply claiming it. This distinction is the whole security model, so
do not add a "is_admin" or "is_staff" field here, however convenient.

Wire contract (five headers, all mandatory):

========================== =================================================
X-Moddy-Assert-User        decimal id of the member who is speaking
X-Moddy-Assert-Guild       decimal id of the channel's guild
X-Moddy-Assert-Request-Id  UUID v4, unique per HTTP request
X-Moddy-Assert-Issued-At   Unix timestamp in seconds, UTC
X-Moddy-Assert-Signature   lowercase hex HMAC-SHA256 of the four above
========================== =================================================

Canonicalization mirrors ``app/redis/signing.py`` on the backend — a single
level this time (no nested payload, unlike ``moddy:tasks``): every field is a
string, ``separators=(",", ":")``, ``sort_keys=True``, ``ensure_ascii=True``,
and ``signature`` is excluded. Sorted order is therefore always
``guild_id, issued_at, request_id, user_id``.

See ``docs/BROCOLI_CHANNEL.md`` and the backend's ``docs/BOT_ASSERTED_AUTH.md``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from typing import Mapping

# Fields covered by the signature. Sorting is applied by json.dumps anyway;
# this tuple documents *which* fields are signed.
SIGNED_FIELDS = ("user_id", "guild_id", "request_id", "issued_at")

# Header names, mirroring app/middleware/bot_auth.py.
HEADER_USER = "X-Moddy-Assert-User"
HEADER_GUILD = "X-Moddy-Assert-Guild"
HEADER_REQUEST_ID = "X-Moddy-Assert-Request-Id"
HEADER_ISSUED_AT = "X-Moddy-Assert-Issued-At"
HEADER_SIGNATURE = "X-Moddy-Assert-Signature"

# Minimum secret length accepted, mirroring the backend.
MIN_SECRET_LENGTH = 32


class AssertionNotConfigured(RuntimeError):
    """No usable ``BOT_ASSERT_SECRET``.

    Raised rather than returning unsigned headers: an unsigned assertion is
    exactly what the signature exists to prevent, and the backend would reject
    it with a 401 anyway. Failing here gives a message that says why.
    """


def is_configured(secret: str | None) -> bool:
    """Can we sign at all?"""
    return bool(secret) and len(secret) >= MIN_SECRET_LENGTH


def canonical_string(fields: Mapping[str, str]) -> str:
    """Build the canonical string fed to HMAC.

    Raises ``KeyError`` when a signed field is absent: signing over a partial
    object would produce a signature the backend cannot reproduce.
    """
    signed = {key: str(fields[key]) for key in SIGNED_FIELDS}
    return json.dumps(signed, separators=(",", ":"), sort_keys=True, ensure_ascii=True)


def compute_signature(fields: Mapping[str, str], secret: str) -> str:
    """Return the expected lowercase hex HMAC-SHA256 for ``fields``."""
    canonical = canonical_string(fields)
    return hmac.new(
        secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def build_headers(
    user_id: int | str,
    guild_id: int | str,
    secret: str,
    *,
    request_id: str | None = None,
    issued_at: int | None = None,
) -> dict[str, str]:
    """Sign one HTTP request and return its assertion headers.

    A fresh ``request_id`` per call is mandatory, not cosmetic: the backend
    burns it once (``SET NX``), so reusing one makes the second request fail as
    a replay. ``request_id`` and ``issued_at`` are only injectable for tests.
    """
    if not is_configured(secret):
        raise AssertionNotConfigured(
            "BOT_ASSERT_SECRET is missing or shorter than "
            f"{MIN_SECRET_LENGTH} characters"
        )

    fields = {
        "user_id": str(user_id),
        "guild_id": str(guild_id),
        "request_id": request_id or str(uuid.uuid4()),
        "issued_at": str(int(time.time()) if issued_at is None else issued_at),
    }
    return {
        HEADER_USER: fields["user_id"],
        HEADER_GUILD: fields["guild_id"],
        HEADER_REQUEST_ID: fields["request_id"],
        HEADER_ISSUED_AT: fields["issued_at"],
        HEADER_SIGNATURE: compute_signature(fields, secret),
    }
