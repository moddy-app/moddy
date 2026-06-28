"""
Step 1 — pre-filter.

Eliminates what must never be analysed. Purely local, free, instant. Role /
channel exemptions are the *caller's* responsibility (the module applies them
before invoking the pipeline); this stays agnostic.
"""

from __future__ import annotations


def pre_filter(content: str, *, is_bot: bool, is_system: bool) -> bool:
    """Return True if the message should continue down the funnel."""
    if is_bot:
        return False
    if is_system:
        return False
    if not content or not content.strip():
        return False
    return True
