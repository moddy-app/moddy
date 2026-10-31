"""
A mini-bot speaks under its own name.

The shared modules' texts say "Moddy" ("Moddy watches this channel…"). Under
a mini-bot that would be wrong, so while a mini-bot is the one talking, every
translated text names the bot instead: the active brand is a ContextVar bound,
like the emoji map, to each client's supervisor task (and to
``client.emoji_context()`` for code running outside it).

What stays "Moddy" on purpose:

- the ``minibots.*`` texts, which talk *about* Moddy ("made by the Moddy team",
  "Get Moddy");
- compound names: "Moddy Team", "Moddy Premium", "Moddy Support";
- anything lowercase (``moddy.app`` links, custom ids).

Moddy's own process never sets the brand: ``utils.i18n`` behaves exactly as
before there.
"""

from __future__ import annotations

import contextvars
import functools
import re
from typing import Optional

_brand: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("minibot_brand", default=None)

#: The single module a one-feature app runs (``/config`` then *is* that module:
#: no home screen, no "Back" to it). None in Moddy and in multi-module apps.
_standalone: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "minibot_standalone_module", default=None)

#: "Moddy" as a name, not as part of "Moddy Team" / "Moddy Premium" / "Moddy Support".
_NAME_RE = re.compile(r"\bModdy\b(?!\s+(?:Team|team|Premium|premium|Support|support))")

#: Translation keys that keep "Moddy" (they talk about Moddy itself).
KEEP_PREFIXES = ("minibots.",)


def current() -> Optional[str]:
    return _brand.get()


def set_brand(name: Optional[str]) -> None:
    _brand.set(name)


def standalone_module() -> Optional[str]:
    """The only module of the app speaking right now, or None (Moddy)."""
    return _standalone.get()


def set_standalone(module_id: Optional[str]) -> None:
    _standalone.set(module_id)


def rebrand(text: str, key: str = "", brand: Optional[str] = None) -> str:
    brand = brand if brand is not None else _brand.get()
    if not brand or not isinstance(text, str) or "Moddy" not in text:
        return text
    if key.startswith(KEEP_PREFIXES):
        return text
    return _NAME_RE.sub(brand, text)


_installed = False


def install() -> None:
    """Wrap ``utils.i18n.i18n.get`` once (the mini-bots process only)."""
    global _installed
    if _installed:
        return
    from utils.i18n import i18n
    original = i18n.get

    @functools.wraps(original)
    def get(key, *args, **kwargs):
        text = original(key, *args, **kwargs)
        return rebrand(text, key) if _brand.get() else text

    i18n.get = get
    _installed = True
