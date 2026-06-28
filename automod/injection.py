"""
Anti-prompt-injection helpers (defence layer C3 in docs/AUTOMOD.md).

User content reaches nano: it is the main attack surface. We wrap every piece
of untrusted ``contenu`` in a marker carrying a per-request random nonce, and we
declare that nonce in the system prompt. An attacker cannot "close" the data
block because they cannot guess the nonce.

This is a hardening measure, not a guarantee — no anti-injection scheme is 100%
reliable on an LLM. It substantially reduces the surface and the impact.
"""

from __future__ import annotations

import secrets


def new_nonce() -> str:
    """Fresh random nonce, regenerated for every request."""
    return secrets.token_hex(4)


def fence(text: str, nonce: str) -> str:
    """Wrap untrusted content in a nonce-delimited data block."""
    return f"[DATA:{nonce}]{text}[/DATA:{nonce}]"
