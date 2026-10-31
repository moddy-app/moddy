"""
Declarative registry of the mini-bots.

A mini-bot is *data*: an id, a display name, the env var holding its token, the
modules it is allowed to run, and the gateway intents it needs. The runtime
(:mod:`minibots.runtime`) turns each spec into a lean ``discord.Client``.

This module is imported by Moddy (``/config``, staff commands, transfer offer)
to name an app, so it must stay import-light: no discord.py, no database.

Adding a mini-bot = appending a :class:`MiniBotSpec` to :data:`SPECS`, creating
its Discord application, and setting its token env var on the mini-bots
service. Nothing else in the core changes.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Optional, Tuple

from minibots.ownership import APP_ID_PATTERN, MODDY_APP

#: Intents a mini-bot may ask for. ``guilds`` is always on (the guild cache is
#: what everything else hangs on). ``members`` / ``presences`` are deliberately
#: absent: they are the two most expensive caches and no single-feature bot
#: should need them — a spec that does must be discussed first.
ALLOWED_INTENTS: FrozenSet[str] = frozenset({
    "guild_messages", "message_content", "guild_reactions",
    "moderation", "emojis_and_stickers", "invites", "webhooks",
    "voice_states", "auto_moderation_execution",
})

#: The only web pages a mini-bot's users may reach. Mini-bots have no
#: dashboard: they are configured in Discord, and the dashboard is a Moddy
#: feature (and an argument for switching to Moddy). These two are functional
#: pages a feature cannot work without, not configuration surfaces:
#: - ``altguard_verification``: the verification page members go through;
#: - ``ticket_transcripts``: reading an archived ticket.
WEB_SURFACES: FrozenSet[str] = frozenset({"altguard_verification", "ticket_transcripts"})

_APP_ID_RE = re.compile(rf"^{APP_ID_PATTERN}$")


@dataclass(frozen=True)
class MiniBotSpec:
    """One mini-bot."""

    #: Stable application id stored in ``guilds.data.modules.<id>.app``.
    #: Never rename it once the bot is live (configs reference it).
    id: str
    #: Display name, shown to server admins ("Managed by Bump Reminder").
    name: str
    #: Environment variable holding the bot token.
    token_env: str
    #: Module ids this bot may run (whitelist).
    modules: Tuple[str, ...]
    #: Python modules to import for those module classes (lazy, at start).
    module_paths: Tuple[str, ...] = ()
    #: Optional Python module exposing ``async def setup(client)`` for the
    #: bot's own slash commands / listeners (lazy, at start).
    commands_module: Optional[str] = None
    #: Gateway intents on top of ``guilds`` (see ALLOWED_INTENTS).
    intents: FrozenSet[str] = field(default_factory=frozenset)
    #: Web pages this bot's users may reach (see WEB_SURFACES).
    web_surfaces: FrozenSet[str] = field(default_factory=frozenset)
    #: Discord application id (invite links, backend registry). Optional.
    application_id: Optional[int] = None
    #: Persistent views of its modules, ``"module.path:ClassName"`` (the common
    #: /config ones are always registered, see minibots/builtin.py).
    persistent_views: Tuple[str, ...] = ()
    #: Custom status shown under the bot's name.
    activity: str = "/config • /about"

    def __post_init__(self):
        if not _APP_ID_RE.match(self.id) or self.id == MODDY_APP:
            raise ValueError(f"invalid mini-bot id: {self.id!r}")
        if not self.modules:
            raise ValueError(f"mini-bot {self.id!r} runs no module")
        unknown = set(self.intents) - ALLOWED_INTENTS
        if unknown:
            raise ValueError(f"mini-bot {self.id!r} asks for unsupported intents: {sorted(unknown)}")
        surfaces = set(self.web_surfaces) - WEB_SURFACES
        if surfaces:
            raise ValueError(f"mini-bot {self.id!r} asks for unknown web surfaces: {sorted(surfaces)}")

    @property
    def token(self) -> str:
        return os.environ.get(self.token_env, "").strip()

    def to_public_dict(self) -> dict:
        """What other services may know about this app (no token)."""
        return {
            "id": self.id,
            "name": self.name,
            "modules": list(self.modules),
            "web_surfaces": sorted(self.web_surfaces),
            "application_id": str(self.application_id) if self.application_id else None,
        }


#: Every mini-bot. Empty until the first one is built — the core runs fine
#: with none (it just idles and reports "0 mini-bots").
SPECS: Tuple[MiniBotSpec, ...] = ()


def _index(specs: Tuple[MiniBotSpec, ...]) -> Dict[str, MiniBotSpec]:
    index: Dict[str, MiniBotSpec] = {}
    for spec in specs:
        if spec.id in index:
            raise ValueError(f"duplicate mini-bot id: {spec.id!r}")
        index[spec.id] = spec
    return index


_BY_ID: Dict[str, MiniBotSpec] = _index(SPECS)


def get_spec(app: str) -> Optional[MiniBotSpec]:
    return _BY_ID.get(app)


def all_specs() -> Tuple[MiniBotSpec, ...]:
    return tuple(_BY_ID.values())


def app_display_name(app: str) -> str:
    """Human name of an application id (``moddy`` -> ``Moddy``)."""
    if app == MODDY_APP:
        return "Moddy"
    spec = _BY_ID.get(app)
    if spec is not None:
        return spec.name
    return app.replace("_", " ").title()


def enabled_specs(env_value: Optional[str] = None) -> Tuple[MiniBotSpec, ...]:
    """Specs to start: those with a token, narrowed by ``MINIBOTS_ENABLED``
    (comma-separated ids; empty = all)."""
    raw = os.environ.get("MINIBOTS_ENABLED", "") if env_value is None else env_value
    wanted = {part.strip() for part in raw.split(",") if part.strip()}
    return tuple(
        spec for spec in _BY_ID.values()
        if (not wanted or spec.id in wanted)
    )


def _register_for_tests(*specs: MiniBotSpec) -> None:
    """Replace the registry (tests only)."""
    global _BY_ID
    _BY_ID = _index(tuple(specs))
