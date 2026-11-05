"""Fire-and-forget coroutines that are never lost and never fail silently.

asyncio keeps only a weak reference to a task, so a bare
``asyncio.create_task(coro)`` can be garbage-collected mid-flight, and an
exception nobody awaits only surfaces as "Task exception was never retrieved"
at interpreter exit. ``spawn()`` keeps a strong reference until the task ends
and logs how it failed, with its name. ``ModdyBot.spawn`` does the same for
code that has the bot at hand; this one needs nothing.
"""

import asyncio
import logging
from typing import Optional, Set

logger = logging.getLogger("moddy.background")

_TASKS: Set[asyncio.Task] = set()


def spawn(coro, *, name: Optional[str] = None) -> Optional[asyncio.Task]:
    """Schedule ``coro`` on the running loop; ``None`` (coroutine closed)
    when there is no running loop. Never raises."""
    try:
        task = asyncio.get_running_loop().create_task(coro, name=name)
    except RuntimeError:
        coro.close()
        return None
    _TASKS.add(task)

    def _done(t: asyncio.Task) -> None:
        _TASKS.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error("Background task %s failed: %r", t.get_name(), exc,
                         exc_info=(type(exc), exc, exc.__traceback__))

    task.add_done_callback(_done)
    return task
