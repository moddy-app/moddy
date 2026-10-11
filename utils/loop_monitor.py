"""
Event-loop lag monitor.

A Discord interaction must be acknowledged within 3 seconds of Discord
creating it. When the event loop is blocked (synchronous CPU work, a blocking
call) every pending interaction ages, and ``10062 Unknown interaction`` shows
up in unrelated features. This module makes that diagnosable without asyncio
debug mode (which is far too slow for production):

* a **heartbeat task** on the loop measures how late a 0.25 s sleep wakes up
  (``lag``) and logs a warning when it exceeds ``LAG_WARN_SECONDS``;
* a **watchdog thread** notices when the heartbeat stops *while the loop is
  still blocked* and logs the main thread's current stack, i.e. the exact
  line that is hogging the loop. Python threads can still run because the
  blocking code releases the GIL periodically.

Both cost next to nothing (one timer wake-up per 250 ms plus one thread
polling every 500 ms). ``observe_interaction_age`` is called from
``bot.on_interaction`` and tells apart "the gateway/loop delivered the
interaction late" from "our callback was slow to acknowledge".
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import traceback
from collections import deque
from typing import Deque, Optional, Tuple

logger = logging.getLogger("moddy.loop_monitor")

HEARTBEAT_INTERVAL = 0.25
LAG_WARN_SECONDS = 1.0          # log a warning above this loop lag
STALL_DUMP_SECONDS = 1.0        # watchdog dumps the stack after this long
STALL_DUMP_COOLDOWN = 30.0      # at most one stack dump per stall / 30 s
INTERACTION_AGE_WARN = 1.5      # interaction already this old when received

_recent_lags: Deque[Tuple[float, float]] = deque(maxlen=20)  # (monotonic, lag)
_state = {
    "last_beat": 0.0,
    "running": False,
    "max_lag": 0.0,
    "stalls": 0,
    "late_interactions": 0,
    "interactions": 0,
}
_thread: Optional[threading.Thread] = None
_task: Optional[asyncio.Task] = None


def last_lag_summary() -> str:
    """Short text for log lines: worst loop lag in the last minute."""
    now = time.monotonic()
    worst = max((lag for ts, lag in _recent_lags if now - ts < 60), default=0.0)
    return f"max_lag_60s={worst:.2f}s"


def stats() -> dict:
    return dict(_state)


def observe_interaction_age(interaction) -> Optional[float]:
    """Record how old an interaction is when the bot first sees it.

    Called from ``on_interaction``, which discord.py dispatches immediately on
    gateway receipt. A large value therefore means the interaction was delayed
    *before* any Moddy code ran (loop blocked or gateway lag), not that a
    callback was slow.
    """
    try:
        from discord.utils import utcnow
        age = (utcnow() - interaction.created_at).total_seconds()
    except Exception:
        return None
    _state["interactions"] += 1
    if age >= INTERACTION_AGE_WARN:
        _state["late_interactions"] += 1
        data = getattr(interaction, "data", None) or {}
        logger.warning(
            "Interaction received late: age=%.2fs type=%s custom_id=%s command=%s %s",
            age, getattr(interaction.type, "name", interaction.type),
            data.get("custom_id"), data.get("name"), last_lag_summary())
    return age


async def _heartbeat() -> None:
    loop = asyncio.get_running_loop()
    while True:
        before = loop.time()
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        lag = loop.time() - before - HEARTBEAT_INTERVAL
        _state["last_beat"] = time.monotonic()
        if lag > _state["max_lag"]:
            _state["max_lag"] = lag
        if lag >= 0.1:
            _recent_lags.append((time.monotonic(), lag))
        if lag >= LAG_WARN_SECONDS:
            logger.warning("Event loop lag: %.2fs (tasks=%d)", lag, len(asyncio.all_tasks()))


def _watchdog(main_thread_id: int) -> None:
    last_dump = 0.0
    while _state["running"]:
        time.sleep(0.5)
        now = time.monotonic()
        stalled = now - _state["last_beat"]
        if stalled >= STALL_DUMP_SECONDS and now - last_dump >= STALL_DUMP_COOLDOWN:
            last_dump = now
            _state["stalls"] += 1
            frame = sys._current_frames().get(main_thread_id)
            stack = "".join(traceback.format_stack(frame, limit=12)) if frame else "?"
            logger.warning("Event loop blocked for %.1fs; main thread is at:\n%s",
                           stalled, stack)


def start() -> None:
    """Start the monitor on the running loop. Safe to call twice; never raises."""
    global _thread, _task
    try:
        if _state["running"]:
            return
        _state["running"] = True
        _state["last_beat"] = time.monotonic()
        _task = asyncio.get_running_loop().create_task(_heartbeat(), name="loop-monitor")
        _thread = threading.Thread(
            target=_watchdog, args=(threading.get_ident(),),
            name="loop-watchdog", daemon=True)
        _thread.start()
        logger.info("Loop monitor started (warn >= %.1fs)", LAG_WARN_SECONDS)
    except Exception:
        _state["running"] = False
        logger.warning("Could not start the loop monitor", exc_info=True)


def stop() -> None:
    _state["running"] = False
    if _task is not None and not _task.done():
        _task.cancel()
