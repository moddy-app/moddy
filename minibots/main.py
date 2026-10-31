"""
Entry point of the mini-bots service: ``python -m minibots.main``.

A separate Railway service from Moddy (same repository, same database, same
Redis). Recommended env: ``MALLOC_ARENA_MAX=2`` — see docs/infra/RAILWAY.md.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal


def _setup_logging() -> None:
    level = logging.DEBUG if os.environ.get("DEBUG", "").lower() == "true" else logging.INFO
    logging.basicConfig(level=level,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # discord.py's gateway chatter, times N bots, is noise.
    logging.getLogger("discord").setLevel(logging.WARNING)


async def run() -> None:
    # Before anything imports config.py: this service has no DISCORD_TOKEN of
    # its own (each mini-bot reads its token from its spec's env var).
    os.environ.setdefault("MODDY_SERVICE", "minibots")
    from minibots.resources import SharedResources
    from minibots.runtime import MiniBotRuntime
    from minibots.spec import enabled_specs

    resources = await SharedResources.create()
    runtime = MiniBotRuntime(enabled_specs(), resources)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    await runtime.start()
    try:
        await stop.wait()
    finally:
        await runtime.stop()
        await resources.close()


def main() -> None:
    _setup_logging()
    asyncio.run(run())


if __name__ == "__main__":
    main()
