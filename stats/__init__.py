"""Moddy's statistics system.

Three pieces, one rule — aggregate before writing:

* :mod:`stats.registry` declares every metric (adding one needs no migration),
* :mod:`stats.service` records them (``bot.stats.incr(...)``, synchronous and
  unfailing), flushing an aggregate to Postgres once a minute,
* :mod:`stats.rollup` photographs the gauges daily and ages the data out.

See docs/STATS.md.
"""

from stats.registry import Metric
from stats.rollup import StatsRollup
from stats.service import StatsService

__all__ = ["Metric", "StatsService", "StatsRollup"]
