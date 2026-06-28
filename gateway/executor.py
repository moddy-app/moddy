from __future__ import annotations
import asyncio
import logging
import time
from typing import Any, Optional

from .spec import CallSpec
from .errors import GatewayError, ConfigurationError
from .quota import QuotaManager
from .resilience import CircuitBreaker, retry_with_backoff
from .logger import GatewayLogger
from .config import GatewayConfig

logger = logging.getLogger("moddy.gateway.executor")


class GatewayExecutor:
    """Single execution path for all external API calls.

    Pipeline per call:
      1. Quota check (all targets must pass before the call is made)
      2. Resilience: timeout + retry/backoff + circuit breaker
      3. Provider dispatch
      4. Quota consume (after success only)
      5. Log (always, regardless of outcome)
    """

    def __init__(
        self,
        adapters: dict,
        quota: QuotaManager,
        circuit_breaker: CircuitBreaker,
        gw_logger: GatewayLogger,
        config: GatewayConfig,
    ):
        self._adapters = adapters
        self._quota = quota
        self._cb = circuit_breaker
        self._logger = gw_logger
        self._config = config

    def _timeout_for(self, operation: str) -> float:
        return {
            "embed": self._config.timeout_embed,
            "chat": self._config.timeout_chat,
            "translate": self._config.timeout_translate,
        }.get(operation, 30.0)

    async def execute(self, spec: CallSpec) -> Any:
        adapter = self._adapters.get(spec.provider)
        if adapter is None:
            raise ConfigurationError(
                f"No adapter for provider {spec.provider!r}. "
                f"Available: {list(self._adapters)}"
            )

        # 1. Check quotas (no network call if any limit is exceeded)
        if spec.quota:
            await self._quota.check_all(spec.quota)

        start = time.monotonic()
        attempts = 0
        tokens = (0, 0, 0)
        error_type: Optional[str] = None

        try:
            adapter_result, attempts = await retry_with_backoff(
                lambda: adapter.execute(spec),
                max_retries=self._config.max_retries,
                base_delay=self._config.retry_base_delay,
                provider=spec.provider,
                operation=spec.operation,
                circuit_breaker=self._cb,
                timeout=self._timeout_for(spec.operation),
            )

            tokens = (
                adapter_result.tokens_prompt,
                adapter_result.tokens_completion,
                adapter_result.tokens_total,
            )

            # 3. Consume quotas on success
            if spec.quota:
                await self._quota.consume_all(spec.quota)

            return adapter_result.data

        except GatewayError as exc:
            error_type = type(exc).__name__
            raise
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            latency_ms = int((time.monotonic() - start) * 1000)
            try:
                await self._logger.record(
                    spec,
                    success=error_type is None,
                    latency_ms=latency_ms,
                    attempts=max(attempts, 1),
                    tokens_prompt=tokens[0],
                    tokens_completion=tokens[1],
                    tokens_total=tokens[2],
                    error_type=error_type,
                )
            except Exception as log_exc:
                logger.debug("Logger.record failed: %s", log_exc)
