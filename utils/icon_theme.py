"""
Per-user icon theme: swap white icons for their black twins on Discord's light theme.

Discord does not tell a bot which theme a user runs, so the user picks it in
/preferences (``utils.user_preferences.get_icon_theme``). Instead of touching
every call site, the swap happens centrally at send time:

* a ContextVar holds the active theme (``"dark"`` = white icons as written,
  ``"light"`` = use the ``<name>_dark`` twins);
* ``discord.http.handle_message_parameters`` (and every module that imported
  it by name) and the interaction payload builders are wrapped so the JSON
  that leaves for Discord is rewritten when the theme is ``"light"``;
* the interaction response / follow-up methods set the ContextVar from
  ``interaction.user.id`` for the duration of the call.

``install()`` is the only public entry point; everything is idempotent and
every transformation falls back to the untouched payload on error. See
docs/guides/ICON_THEME.md.
"""
from __future__ import annotations

import contextlib
import contextvars
import functools
import importlib
import json
import logging
import re
from collections import OrderedDict
from typing import Any, Dict, Iterator, List, Optional, Tuple

log = logging.getLogger("moddy.icon_theme")

THEME_DARK = "dark"    # default: icons are sent as written (white)
THEME_LIGHT = "light"  # light Discord theme: dark twins are used

_theme: contextvars.ContextVar[str] = contextvars.ContextVar("moddy_icon_theme", default=THEME_DARK)

_EMOJI_RE = re.compile(r"<(a?):([A-Za-z0-9_]+):(\d+)>")


def _light_to_dark() -> Dict[int, Tuple[str, int]]:
    from utils.emojis import LIGHT_TO_DARK
    return LIGHT_TO_DARK


# --------------------------------------------------------------------------- #
# Pure transformations
# --------------------------------------------------------------------------- #

def swap_text(text: str) -> str:
    """Replace every mapped ``<:name:id>`` / ``<a:name:id>`` token by its dark twin."""
    if not text or "<" not in text:
        return text
    mapping = _light_to_dark()

    def repl(m: "re.Match[str]") -> str:
        found = mapping.get(int(m.group(3)))
        if found is None:
            return m.group(0)
        name, new_id = found
        return f"<{m.group(1)}:{name}:{new_id}>"

    return _EMOJI_RE.sub(repl, text)


def _swap_emoji_dict(obj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return a copy of an emoji dict with the dark id/name, or None if unmapped."""
    raw = obj.get("id")
    if raw is None or "name" not in obj or isinstance(raw, bool):
        return None
    try:
        found = _light_to_dark().get(int(raw))
    except (TypeError, ValueError):
        return None
    if found is None:
        return None
    name, new_id = found
    new = dict(obj)
    new["id"] = str(new_id) if isinstance(raw, str) else new_id
    new["name"] = name
    return new


# Button component type and the styles drawn with a coloured background
# (primary / blurple, success / green, danger / red). Their icon sits on a
# colour, never on the client background, so the white icon is right on every
# theme. Grey buttons (secondary, link) follow the theme like the rest.
_BUTTON_TYPE = 2
_COLOURED_BUTTON_STYLES = frozenset({1, 3, 4})


def _is_coloured_button(obj: Dict[str, Any]) -> bool:
    return obj.get("type") == _BUTTON_TYPE and obj.get("style") in _COLOURED_BUTTON_STYLES


def swap_payload(obj: Any) -> Any:
    """Recursively rewrite a JSON payload. Never mutates ``obj``; returns a copy."""
    if isinstance(obj, str):
        return swap_text(obj)
    if isinstance(obj, dict):
        if _is_coloured_button(obj):
            return dict(obj)
        swapped = _swap_emoji_dict(obj)
        source = swapped if swapped is not None else obj
        return {k: swap_payload(v) for k, v in source.items()}
    if isinstance(obj, (list, tuple)):
        return [swap_payload(v) for v in obj]
    return obj


# --------------------------------------------------------------------------- #
# Active theme
# --------------------------------------------------------------------------- #

def current_theme() -> str:
    return _theme.get()


@contextlib.contextmanager
def use_icon_theme(theme: str) -> Iterator[None]:
    """Make everything sent inside the block use ``theme`` (``"dark"`` / ``"light"``)."""
    token = _theme.set(THEME_LIGHT if theme == THEME_LIGHT else THEME_DARK)
    try:
        yield
    finally:
        _theme.reset(token)


@contextlib.contextmanager
def use_icon_theme_for(user_id: Optional[int]) -> Iterator[None]:
    """Same as :func:`use_icon_theme`, with the theme the user picked in /preferences."""
    theme = THEME_DARK
    try:
        from utils.user_preferences import get_icon_theme
        theme = get_icon_theme(user_id)
    except Exception:
        log.debug("icon theme lookup failed for %s", user_id, exc_info=True)
    with use_icon_theme(theme):
        yield


# --------------------------------------------------------------------------- #
# Params wrappers (the actual payload rewrite)
# --------------------------------------------------------------------------- #

def _transform_params(params: Any) -> Any:
    """Rewrite a ``MultipartParameters`` (payload dict and/or payload_json part)."""
    try:
        payload = params.payload
        multipart = params.multipart
        if payload is not None:
            payload = swap_payload(payload)
        if multipart:
            new_parts = []
            for part in multipart:
                if part.get("name") == "payload_json" and isinstance(part.get("value"), str):
                    part = dict(part)
                    part["value"] = json.dumps(swap_payload(json.loads(part["value"])))
                new_parts.append(part)
            multipart = new_parts
        return params._replace(payload=payload, multipart=multipart)
    except Exception:
        log.warning("icon theme swap failed; sending the payload untouched", exc_info=True)
        return params


def _wrap_params_builder(original):
    @functools.wraps(original)
    def wrapper(*args, **kwargs):
        params = original(*args, **kwargs)
        if _theme.get() != THEME_LIGHT:
            return params
        return _transform_params(params)

    wrapper.__icon_theme_patched__ = True
    wrapper.__icon_theme_original__ = original
    return wrapper


# --------------------------------------------------------------------------- #
# Theme selection wrappers (who is the recipient?)
# --------------------------------------------------------------------------- #

# interaction token -> user id, filled when ``Interaction.followup`` is read, so
# follow-up Webhook sends/edits (which only know the token) can find the user.
_TOKEN_USERS: "OrderedDict[str, int]" = OrderedDict()
_TOKEN_USERS_MAX = 8192


def _remember_token(interaction: Any) -> None:
    token = getattr(interaction, "token", None)
    user = getattr(interaction, "user", None)
    if not token or user is None:
        return
    _TOKEN_USERS[token] = user.id
    _TOKEN_USERS.move_to_end(token)
    while len(_TOKEN_USERS) > _TOKEN_USERS_MAX:
        _TOKEN_USERS.popitem(last=False)


def _wrap_interaction_coro(original, get_interaction):
    @functools.wraps(original)
    async def wrapper(self, *args, **kwargs):
        user = getattr(get_interaction(self), "user", None)
        if user is None:
            return await original(self, *args, **kwargs)
        with use_icon_theme_for(user.id):
            return await original(self, *args, **kwargs)

    wrapper.__icon_theme_patched__ = True
    wrapper.__icon_theme_original__ = original
    return wrapper


def _wrap_webhook_coro(original):
    @functools.wraps(original)
    async def wrapper(self, *args, **kwargs):
        user_id = _TOKEN_USERS.get(getattr(self, "token", None) or "")
        if user_id is None:
            return await original(self, *args, **kwargs)
        with use_icon_theme_for(user_id):
            return await original(self, *args, **kwargs)

    wrapper.__icon_theme_patched__ = True
    wrapper.__icon_theme_original__ = original
    return wrapper


class _PatchedProperty(property):
    """A ``property`` that can carry the patch marker (plain ones have no __dict__)."""
    __icon_theme_patched__ = True


def _wrap_followup_property(original):
    def getter(self):
        webhook = original.__get__(self, type(self))
        try:
            _remember_token(self)
        except Exception:
            log.debug("could not remember interaction token", exc_info=True)
        return webhook

    prop = _PatchedProperty(getter)
    prop.__icon_theme_original__ = original
    return prop


# --------------------------------------------------------------------------- #
# Install
# --------------------------------------------------------------------------- #

# (module, attribute) of every module-level function that is wrapped. A
# discord.py upgrade that moves one of them must fail the guard test loudly.
PARAMS_BUILDER_TARGETS: List[Tuple[str, str, str]] = [
    # (module, attribute, kind)
    ("discord.http", "handle_message_parameters", "message"),
    ("discord.interactions", "handle_message_parameters", "message"),
    ("discord.webhook.async_", "handle_message_parameters", "message"),
    ("discord.webhook.sync", "handle_message_parameters", "message"),
    ("discord.message", "handle_message_parameters", "message"),
    ("discord.abc", "handle_message_parameters", "message"),
    ("discord.channel", "handle_message_parameters", "message"),
    # Interaction responses build their payload without handle_message_parameters.
    ("discord.interactions", "interaction_message_response_params", "message"),
    ("discord.webhook.async_", "interaction_message_response_params", "message"),
    # send_modal (and autocomplete) go through this one.
    ("discord.interactions", "interaction_response_params", "message"),
    ("discord.webhook.async_", "interaction_response_params", "message"),
]

# (module, class, method, how to reach the Interaction from ``self``)
INTERACTION_METHOD_TARGETS: List[Tuple[str, str, str]] = [
    ("discord.interactions", "InteractionResponse", "send_message"),
    ("discord.interactions", "InteractionResponse", "edit_message"),
    ("discord.interactions", "InteractionResponse", "send_modal"),
    ("discord.interactions", "Interaction", "edit_original_response"),
]
WEBHOOK_METHOD_TARGETS: List[Tuple[str, str, str]] = [
    ("discord.webhook.async_", "Webhook", "send"),
    ("discord.webhook.async_", "Webhook", "edit_message"),
]
FOLLOWUP_TARGET = ("discord.interactions", "Interaction", "followup")

_installed = False


def install() -> None:
    """Patch discord.py once. Safe to call repeatedly; never raises."""
    global _installed
    if _installed:
        return
    try:
        for mod_name, attr, _kind in PARAMS_BUILDER_TARGETS:
            module = importlib.import_module(mod_name)
            current = getattr(module, attr)
            if getattr(current, "__icon_theme_patched__", False):
                continue
            setattr(module, attr, _wrap_params_builder(current))

        for mod_name, cls_name, method in INTERACTION_METHOD_TARGETS:
            cls = getattr(importlib.import_module(mod_name), cls_name)
            current = getattr(cls, method)
            if getattr(current, "__icon_theme_patched__", False):
                continue
            if cls_name == "InteractionResponse":
                getter = lambda self: self._parent  # noqa: E731
            else:
                getter = lambda self: self  # noqa: E731
            setattr(cls, method, _wrap_interaction_coro(current, getter))

        for mod_name, cls_name, method in WEBHOOK_METHOD_TARGETS:
            cls = getattr(importlib.import_module(mod_name), cls_name)
            current = getattr(cls, method)
            if not getattr(current, "__icon_theme_patched__", False):
                setattr(cls, method, _wrap_webhook_coro(current))

        mod_name, cls_name, attr = FOLLOWUP_TARGET
        cls = getattr(importlib.import_module(mod_name), cls_name)
        current = cls.__dict__[attr]
        if not getattr(current, "__icon_theme_patched__", False):
            setattr(cls, attr, _wrap_followup_property(current))

        _installed = True
        log.info("Icon theme patches installed")
    except Exception:
        log.error("Icon theme install failed; icons stay white", exc_info=True)
