"""
Redis contract between Moddy and the mini-bots process.

Pure serialisation: both sides import it, neither needs the other's code.
Namespace ``minibots:*`` (``moddy:*`` is backend-owned, see
docs/integration/REDIS_COMMUNICATION.md).

- ``minibots:status`` (hash) — one field per app, JSON, rewritten every
  :data:`HEARTBEAT_SECONDS` by the runtime, plus :data:`PROCESS_FIELD` for the
  process itself. A field older than :data:`STALE_AFTER` reads as ``silent``.
- ``minibots:apps`` (hash) — the public registry (name, modules, web
  surfaces), so the backend can name an app without reading this repo.
- ``minibots:control`` (Pub/Sub) — requests to the runtime. Every one is
  idempotent and non-destructive, so they are not signed.
- ``minibots:ack:<request_id>`` (list) — the runtime's answer to a ``release``.
- ``minibots:transfer:<guild>:<module>`` — lock held during a transfer.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional

STATUS_KEY = "minibots:status"
APPS_KEY = "minibots:apps"
CONTROL_CHANNEL = "minibots:control"
PROCESS_FIELD = "_process"

HEARTBEAT_SECONDS = 30
STALE_AFTER = 90
#: How long Moddy waits for the runtime to release a module before cleaning up
#: on its own.
RELEASE_TIMEOUT = 12
ACK_TTL = 60
TRANSFER_LOCK_TTL = 60

# --- control message types ---------------------------------------------------
#: Stop running a module in a guild and take its Discord side down (panel...),
#: then ack. Sent by Moddy right before it takes the module over.
CTRL_RELEASE = "release"
#: Re-read a guild's module configs from the database.
CTRL_RELOAD_GUILD = "reload_guild"
#: Reconnect one app (new gateway session).
CTRL_RESTART = "restart"
CONTROL_TYPES = (CTRL_RELEASE, CTRL_RELOAD_GUILD, CTRL_RESTART)

# --- app states ----------------------------------------------------------------
STATE_CONNECTING = "connecting"
STATE_READY = "ready"
STATE_CRASHED = "crashed"
STATE_STOPPED = "stopped"
STATE_NO_TOKEN = "no_token"
#: Not written by anyone: what a reader shows when the heartbeat is stale.
STATE_SILENT = "silent"


def ack_key(request_id: str) -> str:
    return f"minibots:ack:{request_id}"


def transfer_lock_key(guild_id: int, module_id: str) -> str:
    return f"minibots:transfer:{guild_id}:{module_id}"


def new_request_id() -> str:
    return uuid.uuid4().hex


@dataclass
class AppStatus:
    app: str
    state: str
    guilds: int = 0
    latency_ms: Optional[int] = None
    started_at: Optional[float] = None
    restarts: int = 0
    last_error: Optional[str] = None
    updated_at: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: Any) -> Optional["AppStatus"]:
        try:
            data = json.loads(raw)
            return cls(**{k: data.get(k) for k in cls.__dataclass_fields__ if k in data})
        except (TypeError, ValueError):
            return None

    def effective_state(self, now: Optional[float] = None) -> str:
        now = time.time() if now is None else now
        if (now - (self.updated_at or 0)) > STALE_AFTER:
            return STATE_SILENT
        return self.state


@dataclass
class ProcessStatus:
    rss_mb: Optional[float] = None
    started_at: Optional[float] = None
    version: Optional[str] = None
    apps: int = 0
    updated_at: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: Any) -> Optional["ProcessStatus"]:
        try:
            data = json.loads(raw)
            return cls(**{k: data.get(k) for k in cls.__dataclass_fields__ if k in data})
        except (TypeError, ValueError):
            return None


def control_message(kind: str, **fields: Any) -> str:
    if kind not in CONTROL_TYPES:
        raise ValueError(f"unknown control type: {kind}")
    return json.dumps({"type": kind, **fields}, separators=(",", ":"))


def parse_control(raw: Any) -> Optional[Dict[str, Any]]:
    """A control message, or None when malformed (never raises)."""
    try:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode()
        data = json.loads(raw)
    except (TypeError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or data.get("type") not in CONTROL_TYPES:
        return None
    return data


def _decode(value: Any) -> Any:
    return value.decode() if isinstance(value, (bytes, bytearray)) else value


async def read_statuses(redis) -> tuple[Dict[str, AppStatus], Optional[ProcessStatus]]:
    """Read the whole status hash (staff panel). Never raises."""
    apps: Dict[str, AppStatus] = {}
    process: Optional[ProcessStatus] = None
    if redis is None:
        return apps, process
    try:
        raw = await redis.hgetall(STATUS_KEY)
    except Exception:
        return apps, process
    for key, value in (raw or {}).items():
        key, value = _decode(key), _decode(value)
        if key == PROCESS_FIELD:
            process = ProcessStatus.from_json(value)
            continue
        status = AppStatus.from_json(value)
        if status is not None:
            apps[key] = status
    return apps, process
