"""
Which Discord application runs a module in a server.

Moddy and the mini-bots share ``guilds.data.modules.<module_id>``: one config
per module per server, whoever runs it. The ``app`` key of that config names
the application that owns it. Absent means Moddy, so every config written
before mini-bots existed is Moddy's without any migration.

Pure: no Discord, no database. Moddy imports it on hot paths.

The rule it guarantees: **one runner per module per server**. A config is run
by its owner only; another app never acts on it, and ownership only changes
through an explicit transfer (``ModuleManager.transfer_module``) or a claim
that :func:`can_claim` allows.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

#: Key of the owning application inside a module config.
APP_KEY = "app"

#: Application id of Moddy itself — the default owner.
MODDY_APP = "moddy"

#: Application ids are short lowercase slugs (they end up in custom_ids,
#: Redis keys and log lines).
APP_ID_PATTERN = r"[a-z][a-z0-9_]{1,31}"


def config_app(config: Optional[Mapping[str, Any]]) -> str:
    """The application that owns ``config`` (Moddy when unset or malformed)."""
    if not config:
        return MODDY_APP
    app = config.get(APP_KEY)
    if isinstance(app, str) and app.strip():
        return app.strip()
    return MODDY_APP


def is_owned_by(config: Optional[Mapping[str, Any]], app: str) -> bool:
    """True when ``app`` is the owner of ``config``."""
    return config_app(config) == app


def is_foreign(config: Optional[Mapping[str, Any]], app: str) -> bool:
    """True when a *stored, non-empty* config belongs to another app.

    An empty config (``{}`` is how a deletion is stored) belongs to nobody.
    """
    return bool(config) and not is_owned_by(config, app)


def stamp(config: Optional[Mapping[str, Any]], app: str) -> dict:
    """A copy of ``config`` owned by ``app``.

    Moddy is the default owner, so a Moddy config does not carry the key at
    all: the stored shape stays exactly what it was before mini-bots existed.
    """
    stamped = dict(config or {})
    if app == MODDY_APP:
        stamped.pop(APP_KEY, None)
    else:
        stamped[APP_KEY] = app
    return stamped


def strip(config: Optional[Mapping[str, Any]]) -> dict:
    """``config`` without the ownership key (what a module validates)."""
    stripped = dict(config or {})
    stripped.pop(APP_KEY, None)
    return stripped


def can_claim(config: Optional[Mapping[str, Any]], claimant: str,
              present_apps: Iterable[str], *,
              moddy_default_enabled: bool = False) -> bool:
    """May ``claimant`` start running this module in this server?

    - its own config: yes;
    - an empty config: yes, unless Moddy is in the server and already runs the
      module with its defaults (``DEFAULT_ENABLED``) — that would run it twice;
    - another app's config: only when that app has left the server (nobody
      runs it any more). Otherwise the handoff is manual, never a takeover.
    """
    present = set(present_apps)
    if config and is_owned_by(config, claimant):
        return True
    if not config:
        if moddy_default_enabled and claimant != MODDY_APP and MODDY_APP in present:
            return False
        return True
    return config_app(config) not in present
