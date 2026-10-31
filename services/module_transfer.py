"""
Moving a module from a mini-bot to Moddy — the funnel's last step.

Moddy and the mini-bots share one config per module per server
(``guilds.data.modules.<id>``); its ``app`` key says who runs it. A transfer is
never automatic (a server admin clicks "Transfer to Moddy"), and it must leave
Discord in the state a fresh Moddy setup would: a panel posted by the mini-bot
has buttons only the mini-bot receives, so it has to come down and be re-posted
by Moddy.

The sequence, per module:

1. lock ``minibots:transfer:<guild>:<module>`` (a double click is a no-op);
2. ask the mini-bots runtime to **release** it (``minibots:control``): the
   owning client runs ``on_transfer_out`` — takes its panel down — stops the
   module and acks on ``minibots:ack:<request_id>``. When the runtime does not
   answer in time, Moddy runs that clean-up itself, best-effort;
3. rewrite **only** the ``app`` key, as an atomic compare-and-swap (the
   settings themselves never move: they are already where Moddy reads them);
4. **adopt** it locally: load, enable, ``on_transfer_in`` (re-post the panel,
   re-apply permissions, resync services), resync the guild's commands;
5. technical log + statistic.

See docs/features/MINIBOTS.md → "Transfer".
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from minibots import status as mstatus
from minibots.ownership import MODDY_APP, config_app

logger = logging.getLogger("moddy.module_transfer")


@dataclass
class TransferResult:
    module_id: str
    ok: bool
    from_app: Optional[str] = None
    error: Optional[str] = None
    #: How the previous owner let go: ``acked`` (the mini-bot cleaned up its
    #: own Discord side), ``fallback`` (Moddy did it, best-effort) or ``none``.
    release: str = "none"
    recap: Dict[str, Any] = field(default_factory=dict)


class ModuleTransferService:
    """``bot.module_transfer`` — Moddy side of the handoff."""

    def __init__(self, bot):
        self.bot = bot
        # In-process locks when Redis is unavailable.
        self._local_locks: Dict[tuple, asyncio.Lock] = {}

    @property
    def manager(self):
        return getattr(self.bot, "module_manager", None)

    # ------------------------------------------------------------------ API

    async def transferable(self, guild_id: int,
                           from_app: Optional[str] = None) -> Dict[str, str]:
        """``{module_id: app}`` this guild could move to Moddy (optionally one app)."""
        manager = self.manager
        if manager is None:
            return {}
        foreign = await manager.get_foreign_modules(guild_id)
        if from_app is not None:
            foreign = {m: a for m, a in foreign.items() if a == from_app}
        return foreign

    async def transfer_app(self, guild_id: int, from_app: str, *,
                           actor_id: Optional[int] = None,
                           via: str = "config") -> List[TransferResult]:
        """Move every module ``from_app`` still runs in this guild to Moddy."""
        results = []
        for module_id in sorted(await self.transferable(guild_id, from_app)):
            results.append(await self.transfer(guild_id, module_id,
                                               actor_id=actor_id, via=via))
        return results

    async def transfer(self, guild_id: int, module_id: str, *,
                       actor_id: Optional[int] = None,
                       via: str = "config") -> TransferResult:
        """Move one module to Moddy. Never raises."""
        manager = self.manager
        if manager is None or not getattr(self.bot, "db", None):
            return TransferResult(module_id, False, error="unavailable")
        if module_id not in manager.registered_modules:
            return TransferResult(module_id, False, error="unknown_module")

        lock = await self._acquire(guild_id, module_id)
        if lock is None:
            return TransferResult(module_id, False, error="in_progress")
        try:
            return await self._transfer_locked(guild_id, module_id, actor_id, via)
        except Exception as exc:  # noqa: BLE001 — reported, never raised
            logger.error("Transfer of %s in guild %s failed: %s",
                         module_id, guild_id, exc, exc_info=True)
            return TransferResult(module_id, False, error="internal_error")
        finally:
            await self._release_lock(lock)

    # ------------------------------------------------------------ internals

    async def _transfer_locked(self, guild_id: int, module_id: str,
                               actor_id: Optional[int], via: str) -> TransferResult:
        manager = self.manager
        to_app = manager.app
        config = await manager.get_module_config(guild_id, module_id)
        if not config:
            return TransferResult(module_id, False, error="not_configured")
        from_app = config_app(config)
        if from_app == to_app:
            return TransferResult(module_id, False, from_app=from_app, error="already_owned")

        # 2. the previous owner lets go (and takes its panel down)
        release, release_recap = await self._request_release(
            guild_id, module_id, from_app, to_app)
        if release == "timeout":
            release_recap = await self._fallback_release(guild_id, module_id, config, to_app)
            release = "fallback"

        # 3. ownership only — compare-and-swap
        if not await self.bot.db.set_module_app(guild_id, module_id, from_app, to_app):
            return TransferResult(module_id, False, from_app=from_app,
                                  error="changed", release=release)

        # 4. Moddy takes over (re-post panels, permissions, commands)
        adopt = await manager.adopt_module(guild_id, module_id, from_app)

        result = TransferResult(
            module_id, bool(adopt.get("ok")), from_app=from_app,
            error=None if adopt.get("ok") else adopt.get("error"),
            release=release, recap={"release": release_recap, "adopt": adopt},
        )
        # 5. trace
        await self._log(guild_id, result, actor_id, via)
        stats = getattr(self.bot, "stats", None)
        if stats is not None and result.ok:
            stats.incr("minibots.transfer",
                       dims={"from_app": from_app, "module": module_id, "via": via})
        return result

    async def _request_release(self, guild_id: int, module_id: str,
                               from_app: str, to_app: str) -> tuple[str, Dict[str, Any]]:
        """Ask the mini-bots runtime to let go; ``(acked|timeout, recap)``."""
        redis = getattr(self.bot, "redis", None)
        if redis is None:
            return "timeout", {}
        request_id = mstatus.new_request_id()
        try:
            listeners = await redis.publish(mstatus.CONTROL_CHANNEL, mstatus.control_message(
                mstatus.CTRL_RELEASE, request_id=request_id, guild_id=str(guild_id),
                module_id=module_id, app=from_app, to_app=to_app,
            ))
            if not listeners:
                return "timeout", {}
            reply = await redis.blpop(mstatus.ack_key(request_id),
                                      timeout=mstatus.RELEASE_TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Release request for %s/%s failed: %s", guild_id, module_id, exc)
            return "timeout", {}
        if not reply:
            return "timeout", {}
        raw = reply[1] if isinstance(reply, (list, tuple)) else reply
        try:
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode()
            recap = json.loads(raw)
        except (TypeError, ValueError):
            recap = {}
        return "acked", recap if isinstance(recap, dict) else {}

    async def _fallback_release(self, guild_id: int, module_id: str,
                                config: Dict[str, Any], to_app: str) -> Dict[str, Any]:
        """The runtime did not answer: clean up its Discord side from Moddy.

        Best-effort — deleting another application's message needs *Manage
        Messages*; what cannot be removed is reported, never fatal.
        """
        module_class = self.manager.registered_modules[module_id]
        module = module_class(self.bot, guild_id)
        try:
            await self.manager._load_instance(module, config)
            return await module.on_transfer_out(to_app) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Fallback release of %s in guild %s failed: %s",
                           module_id, guild_id, exc)
            return {"hook_error": str(exc)}

    async def _acquire(self, guild_id: int, module_id: str):
        redis = getattr(self.bot, "redis", None)
        key = mstatus.transfer_lock_key(guild_id, module_id)
        if redis is not None:
            try:
                if await redis.set(key, "1", nx=True, ex=mstatus.TRANSFER_LOCK_TTL):
                    return ("redis", key)
                return None
            except Exception as exc:  # noqa: BLE001 — fall back to a local lock
                logger.debug("Redis transfer lock unavailable: %s", exc)
        lock = self._local_locks.setdefault((guild_id, module_id), asyncio.Lock())
        if lock.locked():
            return None
        await lock.acquire()
        return ("local", lock)

    async def _release_lock(self, handle) -> None:
        kind, value = handle
        if kind == "local":
            value.release()
            return
        try:
            await self.bot.redis.delete(value)
        except Exception:  # noqa: BLE001 — the TTL frees it anyway
            pass

    async def _log(self, guild_id: int, result: TransferResult,
                   actor_id: Optional[int], via: str) -> None:
        tech = getattr(self.bot, "tech_logger", None)
        if tech is None:
            return
        try:
            await tech.log_module_transfer(
                guild_id=guild_id, module_id=result.module_id,
                from_app=result.from_app or "?", to_app=MODDY_APP,
                actor_id=actor_id, ok=result.ok, release=result.release,
                error=result.error, via=via,
            )
        except Exception:  # noqa: BLE001 — logging never breaks a transfer
            pass
