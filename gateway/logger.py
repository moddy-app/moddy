from __future__ import annotations
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from .spec import CallSpec
from .config import GatewayConfig

logger = logging.getLogger("moddy.gateway.logger")


def _estimate_cost(
    config: GatewayConfig,
    provider: str,
    model: Optional[str],
    tokens_prompt: int,
    tokens_completion: int,
) -> Optional[float]:
    if not model:
        return None
    total = 0.0
    in_key = (provider, model, "input")
    out_key = (provider, model, "output")
    if in_key in config.cost_table:
        total += config.cost_table[in_key] * tokens_prompt / 1_000_000
    if out_key in config.cost_table:
        total += config.cost_table[out_key] * tokens_completion / 1_000_000
    return round(total, 8) if total > 0 else None


class GatewayLogger:
    """Two-tier logging:
    1. Real-time webhook via bot.tech_logger (best-effort, fire-and-forget).
    2. Buffered PG writes via Redis list flushed by a background task.
    """

    def __init__(self, redis, pool, config: GatewayConfig, tech_logger=None):
        self._redis = redis
        self._pool = pool
        self._config = config
        self._tech_logger = tech_logger
        self._flush_task: Optional[asyncio.Task] = None

    def set_tech_logger(self, tech_logger) -> None:
        self._tech_logger = tech_logger

    def start(self) -> None:
        self._flush_task = asyncio.create_task(
            self._flush_loop(), name="gateway-log-flush"
        )

    async def stop(self) -> None:
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        await self._do_flush()

    async def record(
        self,
        spec: CallSpec,
        *,
        success: bool,
        latency_ms: int,
        attempts: int,
        tokens_prompt: int = 0,
        tokens_completion: int = 0,
        tokens_total: int = 0,
        error_type: Optional[str] = None,
        request_payload: Optional[dict] = None,
        response_data: Any = None,
    ) -> None:
        """Buffer a log entry and fire the staff webhook.

        ``request_payload`` (the prompt sent) and ``response_data`` (the raw
        response) are forwarded to the webhook only — they are attached there as
        text files and are intentionally NOT persisted in the Redis buffer / PG
        table to keep those lean.
        """
        cost = _estimate_cost(
            self._config, spec.provider, spec.model, tokens_prompt, tokens_completion
        )
        entry = {
            "correlation_id": spec.correlation_id,
            "call_type": spec.call_type,
            "provider": spec.provider,
            "operation": spec.operation,
            "model": spec.model,
            "guild_id": spec.metadata.get("guild_id"),
            "user_id": spec.metadata.get("user_id"),
            "quota_targets": [
                {"scope": t.scope.value, "key": t.key, "type": t.type}
                for t in spec.quota
            ],
            "tokens_prompt": tokens_prompt,
            "tokens_completion": tokens_completion,
            "tokens_total": tokens_total,
            "latency_ms": latency_ms,
            "attempts": attempts,
            "success": success,
            "error_type": error_type,
            "estimated_cost": cost,
            "ts": datetime.now(timezone.utc).isoformat(),
        }

        # Push to Redis buffer (non-blocking)
        try:
            await self._redis.rpush(self._config.log_buffer_key, json.dumps(entry))
        except Exception as exc:
            logger.debug("Redis buffer push failed: %s", exc)

        # Real-time webhook (best-effort, fire-and-forget)
        if self._tech_logger:
            asyncio.create_task(
                self._safe_webhook(entry, request_payload, response_data),
                name="gateway-webhook-log",
            )

    async def _safe_webhook(
        self,
        entry: dict,
        request_payload: Optional[dict] = None,
        response_data: Any = None,
    ) -> None:
        try:
            await self._tech_logger.log_api_call(
                entry,
                request_payload=request_payload,
                response_data=response_data,
            )
        except Exception as exc:
            logger.debug("Webhook log failed: %s", exc)

    # -------------------------------------------------------------- PG flush

    async def _flush_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._config.log_flush_interval)
                await self._do_flush()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Log flush loop error: %s", exc)

    async def _do_flush(self) -> None:
        if not self._pool:
            return

        batch_size = self._config.log_flush_batch
        rows = []
        try:
            pipe = self._redis.pipeline()
            for _ in range(batch_size):
                pipe.lpop(self._config.log_buffer_key)
            results = await pipe.execute()
            rows = [json.loads(r) for r in results if r]
        except Exception as exc:
            logger.warning("Failed to pop log buffer from Redis: %s", exc)
            return

        if not rows:
            return

        try:
            async with self._pool.acquire() as conn:
                await conn.executemany(
                    """
                    INSERT INTO api_calls (
                        correlation_id, call_type, provider, operation, model,
                        guild_id, user_id, quota_targets,
                        tokens_prompt, tokens_completion, tokens_total,
                        latency_ms, attempts, success, error_type,
                        estimated_cost, created_at
                    ) VALUES (
                        $1, $2, $3, $4, $5,
                        $6, $7, $8::jsonb,
                        $9, $10, $11,
                        $12, $13, $14, $15,
                        $16, $17
                    )
                    """,
                    [
                        (
                            row["correlation_id"],
                            row["call_type"],
                            row["provider"],
                            row["operation"],
                            row.get("model"),
                            row.get("guild_id"),
                            row.get("user_id"),
                            json.dumps(row.get("quota_targets", [])),
                            row.get("tokens_prompt", 0),
                            row.get("tokens_completion", 0),
                            row.get("tokens_total", 0),
                            row.get("latency_ms", 0),
                            row.get("attempts", 1),
                            row["success"],
                            row.get("error_type"),
                            row.get("estimated_cost"),
                            datetime.fromisoformat(row["ts"]),
                        )
                        for row in rows
                    ],
                )
            logger.debug("Flushed %d gateway log entries to PG", len(rows))
        except Exception as exc:
            logger.warning("PG flush failed (%d rows): %s", len(rows), exc)
            # Re-queue entries at the front of the buffer
            try:
                pipe = self._redis.pipeline()
                for row in reversed(rows):
                    pipe.lpush(self._config.log_buffer_key, json.dumps(row))
                await pipe.execute()
            except Exception as exc2:
                logger.error("Failed to re-queue log entries: %s", exc2)
