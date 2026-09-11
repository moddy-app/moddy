"""Single source of truth for every statistic Moddy measures.

A metric is declared **once**, here. The service that records it, the rollup
that ages it and the dashboard that reads it all resolve the same entry, so
adding a new statistic is one entry in :data:`METRICS` — no migration, no
column, no change anywhere else:

.. code-block:: python

    Metric("ticket.opened", scope=SCOPE_GUILD, dimensions=("category",))

    bot.stats.incr("ticket.opened", guild_id=guild.id, dims={"category": cat})

The registry is also the guard rail. Storage cost is driven by *cardinality*
— the number of distinct ``(metric, scope_id, bucket, dims)`` tuples — and
the classic way to blow it up is to record a user id as a dimension, turning
one row a day into a hundred thousand. :func:`normalise_dims` drops anything
the metric did not declare, so that mistake is impossible to make by
accident.

The second cost lever is :attr:`Metric.resolution`. Per-server metrics are
daily by default: hourly buckets multiply their volume by 24 for a precision
nobody reads at that scope. Global metrics are hourly, because there is only
one of them.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, Optional, Tuple

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

#: "How many times did this happen" — summed into ``stats_counters``.
KIND_COUNTER = "counter"
#: "How many are there right now" — photographed daily into ``stats_snapshots``.
KIND_GAUGE = "gauge"
#: "How many *distinct* somethings" — a Redis HyperLogLog counted daily into
#: ``stats_snapshots``. Never stores the ids themselves.
KIND_UNIQUE = "unique"

SCOPE_GLOBAL = "global"
SCOPE_GUILD = "guild"
SCOPE_USER = "user"

RESOLUTION_HOUR = "hour"
RESOLUTION_DAY = "day"

#: Money is stored as an integer count of millionths of a dollar: the counter
#: column is a BIGINT, and a float would not survive the ``+=`` upsert intact.
UNIT_COUNT = "count"
UNIT_MICRO_USD = "micro_usd"
UNIT_TOKENS = "tokens"
UNIT_MILLISECONDS = "ms"

#: A dimension value longer than this is truncated. Long values are almost
#: always an id or a free-text string that has no business being a dimension.
MAX_DIM_VALUE_LEN = 64


@dataclass(frozen=True)
class Metric:
    """One measurable thing.

    :param key: ``namespace.name`` — the identity stored in the database.
    :param kind: :data:`KIND_COUNTER`, :data:`KIND_GAUGE` or :data:`KIND_UNIQUE`.
    :param scope: whose number this is — global, one server, or one user.
    :param resolution: bucket size for counters. Defaults to daily for
        anything scoped to a server or a user, hourly for global metrics.
    :param dimensions: the **only** keys accepted in ``dims``. Every one of
        them must have bounded cardinality (a command name, a boolean, a
        module id — never an id of a user, a message or a channel).
    :param raw: also write one row per occurrence to ``stats_events``, kept
        for a fortnight. Reserve it for the handful of events whose detail is
        worth the storage.
    """

    key: str
    kind: str = KIND_COUNTER
    scope: str = SCOPE_GUILD
    resolution: Optional[str] = None
    dimensions: Tuple[str, ...] = ()
    unit: str = UNIT_COUNT
    raw: bool = False
    doc: str = ""

    def __post_init__(self) -> None:
        if self.resolution is None:
            default = RESOLUTION_HOUR if self.scope == SCOPE_GLOBAL else RESOLUTION_DAY
            object.__setattr__(self, "resolution", default)


# --------------------------------------------------------------------------- #
# The metrics
# --------------------------------------------------------------------------- #

_ALL: Tuple[Metric, ...] = (
    # -- Commands ---------------------------------------------------------- #
    Metric(
        "command.used",
        dimensions=("command", "kind"),
        doc="A command finished. `kind` is slash | context | prefix.",
    ),
    Metric(
        "command.error",
        dimensions=("command", "error"),
        doc="A command raised. `error` is the exception class name.",
    ),
    # -- Modules ----------------------------------------------------------- #
    Metric(
        "module.action",
        dimensions=("module", "action"),
        doc="A module actually did something — which is not the same as being enabled.",
    ),
    # -- AI / API ---------------------------------------------------------- #
    Metric(
        "ai.calls",
        dimensions=("provider", "model", "call_type", "ok"),
        doc="One call through the gateway. api_calls keeps the fine detail.",
    ),
    Metric(
        "ai.tokens",
        dimensions=("provider", "model", "call_type"),
        unit=UNIT_TOKENS,
        doc="Tokens consumed, prompt + completion.",
    ),
    Metric(
        "ai.cost",
        dimensions=("provider", "model", "call_type"),
        unit=UNIT_MICRO_USD,
        doc="Estimated spend, in millionths of a dollar.",
    ),
    # -- The bot's own footprint ------------------------------------------- #
    Metric("guild.join", scope=SCOPE_GLOBAL, dimensions=("source",), raw=False,
           doc="Moddy was added to a server. guild_events keeps the per-server row."),
    Metric("guild.leave", scope=SCOPE_GLOBAL, dimensions=("source",),
           doc="Moddy was removed from a server."),
    Metric("member.join", dimensions=(), doc="Someone joined a server Moddy is in."),
    Metric("member.leave", dimensions=(), doc="Someone left a server Moddy is in."),
    # -- Features ---------------------------------------------------------- #
    Metric("notification.sent", dimensions=("kind", "platform", "ok"),
           doc="One notification delivery attempt (see notifications/)."),
    Metric("case.created", dimensions=("type", "action"),
           doc="A moderation case was recorded in a server."),
    Metric("case.created", scope=SCOPE_GLOBAL, dimensions=("type", "action"),
           doc="A platform-scoped case (a Moddy-team sanction) was recorded."),
    Metric("ticket.opened", dimensions=("panel",)),
    Metric("ticket.closed", dimensions=("reason",)),
    Metric("ticket.transcript", dimensions=("codec",),
           doc="A closed ticket's conversation was archived."),
    Metric("ticket.closure_suggested",
           doc="Moddy offered to close a ticket whose conversation looked over."),
    Metric("ticket.rated", dimensions=("score",),
           doc="A member rated how their ticket was handled. The score is a "
               "dimension because it has five values; who was rated is not, "
               "and lives in `ticket_ratings` instead."),
    Metric("bump.reminded", dimensions=("bot",)),
    Metric("log.dispatched", dimensions=("category",),
           doc="A server-log entry was delivered to a webhook."),
    Metric("automod.decision", dimensions=("sanction", "dry_run"),
           doc="Automod AI reached a verdict."),
    # -- Uniques (HyperLogLog, never a list of ids) ------------------------ #
    Metric("user.active", kind=KIND_UNIQUE, scope=SCOPE_GUILD,
           doc="Distinct people who used Moddy in this server, per day."),
    Metric("user.active", kind=KIND_UNIQUE, scope=SCOPE_GLOBAL,
           doc="Distinct people who used Moddy anywhere, per day."),
    # -- Gauges (written by the daily rollup) ------------------------------- #
    Metric("bot.guilds", kind=KIND_GAUGE, scope=SCOPE_GLOBAL,
           doc="Servers Moddy is in, at the time of the snapshot."),
    Metric("bot.members", kind=KIND_GAUGE, scope=SCOPE_GLOBAL,
           doc="Members reachable across those servers (sum of member counts)."),
    Metric("bot.known_users", kind=KIND_GAUGE, scope=SCOPE_GLOBAL,
           doc="Rows in the users table."),
    Metric("bot.premium_guilds", kind=KIND_GAUGE, scope=SCOPE_GLOBAL),
    Metric("bot.premium_users", kind=KIND_GAUGE, scope=SCOPE_GLOBAL),
    Metric("module.enabled", kind=KIND_GAUGE, scope=SCOPE_GLOBAL,
           dimensions=("module",),
           doc="Servers with this module enabled — module adoption over time."),
    Metric("guild.members", kind=KIND_GAUGE, scope=SCOPE_GUILD,
           doc="Member count of one server, daily — its growth curve."),
)

#: ``(key, scope)`` → metric. Two scopes may share a key (``user.active`` is
#: measured both per server and globally); the pair is the real identity.
METRICS: Dict[Tuple[str, str], Metric] = {(m.key, m.scope): m for m in _ALL}

#: Convenience lookup for the common case where a key exists in one scope only.
_BY_KEY: Dict[str, Tuple[Metric, ...]] = {}
for _m in _ALL:
    _BY_KEY[_m.key] = _BY_KEY.get(_m.key, ()) + (_m,)


class UnknownMetric(KeyError):
    """Raised by :func:`get` when a key was never declared here."""


def get(key: str, scope: Optional[str] = None) -> Metric:
    """Resolve a metric, or raise :class:`UnknownMetric`.

    ``scope`` may be omitted when the key is declared in a single scope,
    which is the case for all but ``user.active``.
    """
    if scope is not None:
        try:
            return METRICS[(key, scope)]
        except KeyError:
            raise UnknownMetric(f"{key} (scope={scope})") from None
    candidates = _BY_KEY.get(key)
    if not candidates:
        raise UnknownMetric(key)
    if len(candidates) > 1:
        raise UnknownMetric(f"{key} is ambiguous, pass a scope")
    return candidates[0]


def all_metrics() -> Iterable[Metric]:
    return _ALL


def gauges() -> Tuple[Metric, ...]:
    return tuple(m for m in _ALL if m.kind == KIND_GAUGE)


def uniques() -> Tuple[Metric, ...]:
    return tuple(m for m in _ALL if m.kind == KIND_UNIQUE)


# --------------------------------------------------------------------------- #
# Dimensions
# --------------------------------------------------------------------------- #

def normalise_dims(metric: Metric, dims: Optional[Dict]) -> Dict[str, str]:
    """Keep only what ``metric`` declared, as short strings.

    Undeclared keys are dropped rather than raising: a statistic must never
    be able to break the feature it measures. Callers that want to know are
    served by :func:`unknown_dims`.
    """
    if not dims:
        return {}
    clean: Dict[str, str] = {}
    for name in metric.dimensions:
        if name not in dims:
            continue
        value = dims[name]
        if value is None:
            continue
        if isinstance(value, bool):
            text = "true" if value else "false"
        else:
            text = str(value)
        clean[name] = text[:MAX_DIM_VALUE_LEN]
    return clean


def unknown_dims(metric: Metric, dims: Optional[Dict]) -> Tuple[str, ...]:
    """The keys :func:`normalise_dims` would silently drop — for logging."""
    if not dims:
        return ()
    return tuple(k for k in dims if k not in metric.dimensions)


def dims_hash(dims: Dict[str, str]) -> bytes:
    """Stable 20-byte identity of a dimension set.

    Hashing the canonical JSON keeps the primary key of ``stats_counters``
    short and fixed-width whatever the dimensions are, which is what lets the
    same table hold every metric without a column per dimension.
    """
    canonical = json.dumps(dims, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha1(canonical.encode("utf-8")).digest()


# --------------------------------------------------------------------------- #
# Buckets
# --------------------------------------------------------------------------- #

def floor_bucket(resolution: str, when: Optional[datetime] = None) -> datetime:
    """Start of the ``resolution``-sized window containing ``when`` (UTC)."""
    moment = (when or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if resolution == RESOLUTION_HOUR:
        return moment.replace(minute=0, second=0, microsecond=0)
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def next_bucket(resolution: str, bucket: datetime) -> datetime:
    """The window that follows ``bucket``."""
    step = timedelta(hours=1) if resolution == RESOLUTION_HOUR else timedelta(days=1)
    return bucket + step
