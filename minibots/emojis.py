"""
Moddy's icons, shown by a mini-bot.

Moddy's custom emojis are **application emojis of the Moddy app**: another
application cannot render them (Discord shows ``:done:`` as text). Two pieces
make every existing card work unchanged under a mini-bot:

1. **Sync** (:func:`sync_application_emojis`, once per start, in the
   background): every emoji the code and the locales use is uploaded to the
   mini-bot's own application under the **same name**, if missing. One
   ``fetch_application_emojis`` call when everything is already there.
2. **Rewrite at send time**: each client's id map lives in a ContextVar set in
   that client's supervisor task — every task the client spawns (gateway,
   events, views) inherits it, so the right app's ids are used without any
   call site knowing. The rewrite wraps the same discord.py payload builders as
   :mod:`utils.icon_theme`, *outside* it (theme first, then the app swap), so a
   light-theme user of a mini-bot gets the mini-bot's copy of the ``_dark`` twin.

Moddy's own process never sets the ContextVar: zero cost there (the patch is
only installed by the mini-bots runtime).
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import importlib
import json
import logging
import pathlib
import re
from typing import Any, Dict, Iterable, Optional, Set, Tuple

log = logging.getLogger("moddy.minibots.emojis")

_EMOJI_RE = re.compile(r"<(a?):([A-Za-z0-9_]{2,32}):(\d{15,21})>")
ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Moddy emoji id -> (name, mini-bot emoji id) for the client running here.
_app_map: contextvars.ContextVar[Optional[Dict[int, Tuple[str, int]]]] = \
    contextvars.ContextVar("minibot_emoji_map", default=None)


def set_active_map(mapping: Optional[Dict[int, Tuple[str, int]]]) -> None:
    """Bind ``mapping`` to the current context (a client's supervisor task)."""
    _app_map.set(mapping)


def suspend():
    """Context manager: send untouched (technical webhooks, Moddy-owned logs)."""
    class _Suspend:
        def __enter__(self):
            self._token = _app_map.set(None)

        def __exit__(self, *exc):
            _app_map.reset(self._token)
    return _Suspend()


# --------------------------------------------------------------------------- #
# Which emojis Moddy uses (scanned once)
# --------------------------------------------------------------------------- #

_SKIP_DIRS = {"tests", ".git", "docs", "__pycache__", ".venv", "venv", "node_modules"}


def moddy_emojis() -> Dict[int, Tuple[str, bool]]:
    """``{moddy emoji id: (name, animated)}`` used anywhere in code or locales
    (every Python file of the repository, outside tests and docs)."""
    found: Dict[int, Tuple[str, bool]] = {}
    files = [path for path in ROOT.rglob("*.py")
             if not _SKIP_DIRS.intersection(path.relative_to(ROOT).parts)]
    files.extend((ROOT / "locales").glob("*.json"))
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for animated, name, emoji_id in _EMOJI_RE.findall(text):
            found.setdefault(int(emoji_id), (name, bool(animated)))
    return found


def build_map(moddy: Dict[int, Tuple[str, bool]],
              app_emojis: Dict[str, int]) -> Dict[int, Tuple[str, int]]:
    """Map every Moddy id to the mini-bot emoji of the same name."""
    mapping: Dict[int, Tuple[str, int]] = {}
    for moddy_id, (name, _animated) in moddy.items():
        target = app_emojis.get(name)
        if target is not None and target != moddy_id:
            mapping[moddy_id] = (name, target)
    return mapping


def source_key(app: str) -> str:
    """Redis hash ``emoji name -> Moddy emoji id`` it was copied from."""
    return f"minibots:emojis:{app}"


def plan_sync(moddy: Dict[int, Tuple[str, bool]], existing: Dict[str, int],
              sources: Dict[str, int]) -> Tuple[list, list]:
    """``(to_upload, to_replace)``: missing names, and names whose Moddy emoji
    changed since the copy (``/dev emoji_replace`` gives a new id, same name)."""
    upload, replace = [], []
    for moddy_id, (name, animated) in moddy.items():
        if name not in existing:
            upload.append((moddy_id, name, animated))
        elif name in sources and sources[name] != moddy_id:
            replace.append((moddy_id, name, animated))
    return upload, replace


async def sync_application_emojis(client, mapping: Dict[int, Tuple[str, int]]) -> Dict[str, int]:
    """Copy Moddy's emojis into ``client``'s application and fill ``mapping``
    in place (the dict bound to the client's context). Never raises.

    - a missing name is uploaded;
    - a name whose Moddy emoji changed since it was copied is re-uploaded
      (the source id of every copy is kept in Redis);
    - everything else is left alone: one API call when nothing changed.

    Returns ``{"present", "uploaded", "replaced", "failed"}``.
    """
    import aiohttp

    stats = {"present": 0, "uploaded": 0, "replaced": 0, "failed": 0}
    redis = getattr(client, "redis", None)
    try:
        moddy = await asyncio.to_thread(moddy_emojis)
        app_emojis = {e.name: e for e in await client.fetch_application_emojis()}
        existing = {name: e.id for name, e in app_emojis.items()}
        sources: Dict[str, int] = {}
        if redis is not None:
            try:
                raw = await redis.hgetall(source_key(client.app_id)) or {}
                sources = {str(k): int(v) for k, v in raw.items()}
            except Exception:  # noqa: BLE001 — without it, nothing is replaced
                sources = {}
        upload, replace = plan_sync(moddy, existing, sources)
        stats["present"] = len(existing)

        async def copy(session, moddy_id, name, animated):
            url = f"https://cdn.discordapp.com/emojis/{moddy_id}.{'gif' if animated else 'png'}"
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
                image = await resp.read()
            emoji = await client.create_application_emoji(name=name, image=image)
            existing[name] = emoji.id
            sources[name] = moddy_id

        if upload or replace:
            log.info("Mini-bot %s: %d emoji(s) to upload, %d to replace",
                     client.app_id, len(upload), len(replace))
            async with aiohttp.ClientSession() as session:
                for moddy_id, name, animated in replace:
                    try:
                        await app_emojis[name].delete()
                        await copy(session, moddy_id, name, animated)
                        stats["replaced"] += 1
                    except Exception as exc:  # noqa: BLE001
                        stats["failed"] += 1
                        log.warning("Mini-bot %s: emoji %s not replaced: %s", client.app_id, name, exc)
                for moddy_id, name, animated in upload:
                    try:
                        await copy(session, moddy_id, name, animated)
                        stats["uploaded"] += 1
                    except Exception as exc:  # noqa: BLE001 — one emoji never stops the rest
                        stats["failed"] += 1
                        log.warning("Mini-bot %s: emoji %s not uploaded: %s", client.app_id, name, exc)
        # Remember where every copy came from (first run: what is there now).
        for moddy_id, (name, _a) in moddy.items():
            if name in existing:
                sources.setdefault(name, moddy_id)
        if redis is not None and sources:
            try:
                await redis.hset(source_key(client.app_id),
                                 mapping={k: str(v) for k, v in sources.items()})
            except Exception:  # noqa: BLE001
                pass
        mapping.update(build_map(moddy, existing))
    except Exception as exc:  # noqa: BLE001
        log.error("Mini-bot %s: emoji sync failed: %s", getattr(client, "app_id", "?"), exc)
    return stats


# --------------------------------------------------------------------------- #
# Send-time rewrite
# --------------------------------------------------------------------------- #

def swap_text(text: str, mapping: Dict[int, Tuple[str, int]]) -> str:
    if not text or "<" not in text:
        return text

    def repl(m):
        found = mapping.get(int(m.group(3)))
        return m.group(0) if found is None else f"<{m.group(1)}:{found[0]}:{found[1]}>"
    return _EMOJI_RE.sub(repl, text)


def swap_payload(obj: Any, mapping: Dict[int, Tuple[str, int]]) -> Any:
    if isinstance(obj, str):
        return swap_text(obj, mapping)
    if isinstance(obj, dict):
        raw = obj.get("id")
        if "name" in obj and raw is not None and not isinstance(raw, bool):
            try:
                found = mapping.get(int(raw))
            except (TypeError, ValueError):
                found = None
            if found is not None:
                obj = {**obj, "id": str(found[1]) if isinstance(raw, str) else found[1],
                       "name": found[0]}
        return {k: swap_payload(v, mapping) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [swap_payload(v, mapping) for v in obj]
    return obj


def _transform(params, mapping):
    try:
        payload = params.payload
        if payload is not None:
            payload = swap_payload(payload, mapping)
        multipart = params.multipart
        if multipart:
            parts = []
            for part in multipart:
                if part.get("name") == "payload_json" and isinstance(part.get("value"), str):
                    part = {**part, "value": json.dumps(swap_payload(json.loads(part["value"]), mapping))}
                parts.append(part)
            multipart = parts
        return params._replace(payload=payload, multipart=multipart)
    except Exception:  # noqa: BLE001 — never block a send
        log.warning("mini-bot emoji swap failed; payload sent untouched", exc_info=True)
        return params


def _wrap(original):
    @functools.wraps(original)
    def wrapper(*args, **kwargs):
        params = original(*args, **kwargs)
        mapping = _app_map.get()
        return _transform(params, mapping) if mapping else params
    wrapper.__minibot_emojis_patched__ = True
    return wrapper


_installed = False


def install() -> None:
    """Wrap discord.py's payload builders (after utils.icon_theme.install)."""
    global _installed
    if _installed:
        return
    from utils.icon_theme import PARAMS_BUILDER_TARGETS
    for mod_name, attr, _kind in PARAMS_BUILDER_TARGETS:
        module = importlib.import_module(mod_name)
        current = getattr(module, attr)
        if not getattr(current, "__minibot_emojis_patched__", False):
            setattr(module, attr, _wrap(current))
    _installed = True
