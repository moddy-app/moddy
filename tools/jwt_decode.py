"""JSON Web Token decoding for /jwt — pure, nothing is verified.

The signature is never checked (no key is ever asked for): this only reads what
the token *claims*. ``alg: none`` and encrypted (JWE) tokens are reported as
such so the card can say so loudly.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

STATUS_VALID = "valid"
STATUS_EXPIRED = "expired"
STATUS_NOT_YET = "not_yet"
STATUS_NONE = "none"  # no time claim that decides validity

#: Time claims shown as Discord timestamps, in display order.
TIME_CLAIMS = ("iat", "nbf", "exp")
#: Hard cap on the pasted token (a modal paragraph input caps at 4000 anyway).
MAX_TOKEN_LENGTH = 4000

_B64URL = re.compile(r"^[A-Za-z0-9_\-]*={0,2}$")


class JwtError(Exception):
    """The input is not a decodable JWT. ``code`` maps to ``tools.jwt.errors.<code>``."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class DecodedJwt:
    header: Dict[str, Any]
    payload: Any  # normally a dict; any JSON value is tolerated
    signature_present: bool
    alg: str = ""
    alg_none: bool = False
    times: Dict[str, int] = field(default_factory=dict)  # claim -> epoch seconds
    status: str = STATUS_NONE
    issues: List[str] = field(default_factory=list)  # e.g. "bad_time:exp"


def clean_token(raw: str) -> str:
    """Strip whitespace, an ``Authorization:`` / ``Bearer`` prefix and quotes."""
    text = (raw or "").strip().strip("\"'`")
    if text.lower().startswith("authorization:"):
        text = text.split(":", 1)[1].strip()
    if text.lower().startswith("bearer "):
        text = text[7:]
    return re.sub(r"\s+", "", text)


def b64url_decode(segment: str) -> bytes:
    """Base64url decode tolerant to missing padding. Raises :class:`JwtError`."""
    if not _B64URL.match(segment):
        raise JwtError("not_a_jwt")
    segment = segment.rstrip("=")
    if len(segment) % 4 == 1:
        raise JwtError("not_a_jwt")
    try:
        return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except (binascii.Error, ValueError) as exc:
        raise JwtError("not_a_jwt") from exc


def _json_segment(segment: str) -> Any:
    raw = b64url_decode(segment)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise JwtError("not_a_jwt") from exc


def decode(raw: str, *, now: Optional[float] = None) -> DecodedJwt:
    """Decode a compact JWS. Raises :class:`JwtError` (``empty``, ``encrypted``,
    ``not_a_jwt``)."""
    token = clean_token(raw)
    if not token:
        raise JwtError("empty")
    if len(token) > MAX_TOKEN_LENGTH:
        raise JwtError("not_a_jwt")
    parts = token.split(".")
    if len(parts) == 5:
        # Compact JWE: header.encryptedKey.iv.ciphertext.tag — cannot be read.
        header = _json_segment(parts[0])
        if isinstance(header, dict) and "enc" in header:
            raise JwtError("encrypted")
        raise JwtError("not_a_jwt")
    if len(parts) != 3:
        raise JwtError("not_a_jwt")

    header = _json_segment(parts[0])
    payload = _json_segment(parts[1])
    if not isinstance(header, dict):
        raise JwtError("not_a_jwt")
    if not _B64URL.match(parts[2]):
        raise JwtError("not_a_jwt")

    alg = header.get("alg")
    alg = alg if isinstance(alg, str) else ""
    result = DecodedJwt(header=header, payload=payload, signature_present=bool(parts[2]),
                        alg=alg, alg_none=alg.strip().lower() == "none")
    _read_times(result, now if now is not None else time.time())
    return result


def _read_times(result: DecodedJwt, now: float) -> None:
    claims = result.payload if isinstance(result.payload, dict) else {}
    for name in TIME_CLAIMS:
        value = claims.get(name)
        if value is None:
            continue
        # bool is an int in Python; a JSON true is not a timestamp.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            result.issues.append(f"bad_time:{name}")
            continue
        seconds = value / 1000 if value > 10_000_000_000 else value  # some issuers use ms
        if not 0 <= seconds <= 253_402_300_799:  # up to year 9999
            result.issues.append(f"bad_time:{name}")
            continue
        result.times[name] = int(seconds)

    if "exp" in result.times and result.times["exp"] <= now:
        result.status = STATUS_EXPIRED
    elif "nbf" in result.times and result.times["nbf"] > now:
        result.status = STATUS_NOT_YET
    elif "exp" in result.times or "nbf" in result.times:
        result.status = STATUS_VALID


def pretty_json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def audience(value: Any) -> str:
    """``aud`` is a string or a list of strings."""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return "" if value is None else str(value)
