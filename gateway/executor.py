from __future__ import annotations
import asyncio
import logging
import time
from typing import Any, Optional

from .spec import CallSpec
from .errors import GatewayError, ConfigurationError
from .quota import QuotaManager
from .ratelimit import RateLimiter, Reservation
from .resilience import CircuitBreaker, retry_with_backoff
from .logger import GatewayLogger
from .config import GatewayConfig

logger = logging.getLogger("moddy.gateway.executor")


class GatewayExecutor:
    """Single execution path for all external API calls.

    Pipeline per call:
      1. Quota check (all targets must pass before the call is made)
      2. Provider rate-limit reservation (released again if the call fails)
      3. Resilience: timeout + retry/backoff + circuit breaker
      4. Provider dispatch
      5. Quota consume + rate-limit reconciliation (after success only)
      6. Log (always, regardless of outcome)
    """

    def __init__(
        self,
        adapters: dict,
        quota: QuotaManager,
        circuit_breaker: CircuitBreaker,
        gw_logger: GatewayLogger,
        config: GatewayConfig,
        ratelimiter: Optional[RateLimiter] = None,
    ):
        self._adapters = adapters
        self._quota = quota
        self._cb = circuit_breaker
        self._logger = gw_logger
        self._config = config
        self._ratelimiter = ratelimiter

    def _timeout_for(self, operation: str) -> float:
        return {
            "embed": self._config.timeout_embed,
            "chat": self._config.timeout_chat,
            "translate": self._config.timeout_translate,
            "transcribe": self._config.timeout_transcribe,
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

        # 2. Reserve against our provider-account rate limits. Debited up front
        #    so concurrent calls cannot collectively overshoot; released below
        #    if the call ends up failing. A retried call is billed once, not
        #    once per attempt — retries only happen on 429/5xx, where the
        #    provider itself did not serve the request.
        reservation = Reservation()
        if spec.rate_cost and self._ratelimiter is not None:
            reservation = await self._ratelimiter.acquire(
                spec.provider, spec.model, spec.rate_cost
            )

        start = time.monotonic()
        attempts = 0
        tokens = (0, 0, 0)
        error_type: Optional[str] = None
        response_data: Any = None

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
            response_data = adapter_result.data

            # Consume quotas on success, and correct the rate-limit reservation
            # now that the provider has reported what the call really cost.
            if spec.quota:
                await self._quota.consume_all(spec.quota)
            await reservation.reconcile(adapter_result.rate_cost)

            return adapter_result.data

        except GatewayError as exc:
            error_type = type(exc).__name__
            await reservation.release()
            raise
        except Exception as exc:
            error_type = type(exc).__name__
            await reservation.release()
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
                    request_payload=spec.payload,
                    response_data=response_data,
                )
            except Exception as log_exc:
                logger.debug("Logger.record failed: %s", log_exc)
