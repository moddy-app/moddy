"""Shared utility functions for repository modules."""

from typing import Any


def set_nested_value(data: dict, parts: list, val: Any) -> dict:
    """Recursively set a nested value in a dictionary"""
    if len(parts) == 1:
        data[parts[0]] = val
        return data

    # Ensure the parent key exists
    if parts[0] not in data:
        data[parts[0]] = {}
    elif not isinstance(data[parts[0]], dict):
        data[parts[0]] = {}

    # Recurse
    data[parts[0]] = set_nested_value(data[parts[0]], parts[1:], val)
    return data


_HOOK_TASKS: set = set()


def fire_and_forget(coro, *, name: str = "db-hook") -> None:
    """Run a best-effort hook coroutine in the background.

    Keeps a strong reference until it ends (asyncio only holds tasks weakly, so
    a bare ``create_task`` can be garbage-collected mid-flight) and logs a
    failure instead of leaving "Task exception was never retrieved". Never
    raises: without a running loop the coroutine is simply dropped.
    """
    import asyncio
    import logging

    try:
        task = asyncio.get_running_loop().create_task(coro, name=name)
    except RuntimeError:
        coro.close()
        return
    _HOOK_TASKS.add(task)

    def _done(t: "asyncio.Task") -> None:
        _HOOK_TASKS.discard(t)
        if not t.cancelled() and t.exception() is not None:
            logging.getLogger("moddy.db").debug("%s failed: %r", name, t.exception())

    task.add_done_callback(_done)
