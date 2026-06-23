"""Small argument parsing helpers shared by staff commands."""

from __future__ import annotations

from typing import Optional


def parse_user_id(args: str) -> Optional[int]:
    """Parse a user id from a mention (``<@123>`` / ``<@!123>``) or raw id."""
    if not args:
        return None
    args = args.strip()
    if args.startswith("<@") and args.endswith(">"):
        inner = args[2:-1]
        if inner.startswith("!"):
            inner = inner[1:]
        try:
            return int(inner)
        except ValueError:
            return None
    try:
        return int(args)
    except ValueError:
        return None


def parse_guild_id(args: str) -> Optional[int]:
    """Parse a raw numeric guild id."""
    if not args:
        return None
    try:
        return int(args.strip())
    except ValueError:
        return None
