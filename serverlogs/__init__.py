"""Moddy's advanced server logs.

Layout of the system::

    serverlogs/registry.py    what can be logged (categories + events)
    serverlogs/renderer.py    what a log looks like (the embed, the labels)
    serverlogs/dispatcher.py  how it gets there (webhooks, batching, queues)
    serverlogs/audit.py       who did it (audit-log correlation)
    serverlogs/service.py     the API listeners use (open / submit)
    serverlogs/listeners/     one module per category — the events themselves

    modules/logs.py                    the configuration (what goes where)
    modules/configs/logs_config.py     the /config panel
    cogs/logs.py                       the Discord wiring

Documentation: ``docs/LOGS.md``.

The submodules are intentionally *not* imported here: ``modules/logs.py``
imports :mod:`serverlogs.registry` at module-discovery time, long before
``discord`` clients or cogs exist, so this package must stay importable
without side effects.
"""

from __future__ import annotations

__all__ = ["registry", "renderer", "dispatcher", "audit", "service"]
