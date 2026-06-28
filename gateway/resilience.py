from __future__ import annotations
import asyncio
import logging
import random
import time
from enum import Enum
from typing import Optional

from .errors import APIUnavailableError, GatewayTimeoutError, RateLimitError, ProviderError

logger = logging.getLogger("moddy.gateway.resilience")


class CBState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class _CBEntry:
    __slots__ = ("state", "failures", "opened_at")

    def __init__(self):
        self.state = CBState.CLOSED
        self.failures = 0
        self.opened_at: Optional[float] = None


class CircuitBreaker:
    """In-memory circuit breaker per (provider, operation) pair.

    Uses a simple failure-count strategy: N consecutive failures → OPEN.
    After cooldown seconds, transitions to HALF_OPEN; one success → CLOSED.
    """

    def __init__(self, failure_threshold: int = 5, cooldown: float = 60.0):
        self._threshold = failure_threshold
        self._cooldown = cooldown
        self._entries: dict[str, _CBEntry] = {}

    def _get(self, provider: str, operation: str) -> _CBEntry:
        k = f"{provider}:{operation}"
        if k not in self._entries:
            self._entries[k] = _CBEntry()
        return self._entries[k]

    def check(self, provider: str, operation: str) -> None:
        """Raise APIUnavailableError if the circuit is open."""
        entry = self._get(provider, operation)
        if entry.state == CBState.OPEN:
            if entry.opened_at and time.monotonic() - entry.opened_at >= self._cooldown:
                entry.state = CBState.HALF_OPEN
                logger.info("Circuit %s/%s → HALF_OPEN", provider, operation)
            else:
                raise APIUnavailableError(provider)

    def record_success(self, provider: str, operation: str) -> None:
        entry = self._get(provider, operation)
        if entry.state != CBState.CLOSED:
            logger.info("Circuit %s/%s → CLOSED", provider, operation)
        entry.state = CBState.CLOSED
        entry.failures = 0
        entry.opened_at = None

    def record_failure(self, provider: str, operation: str) -> None:
        entry = self._get(provider, operation)
        entry.failures += 1
        if entry.state == CBState.HALF_OPEN or entry.failures >= self._threshold:
            entry.state = CBState.OPEN
            entry.opened_at = time.monotonic()
            logger.warning(
                "Circuit %s/%s → OPEN (failures=%d)", provider, operation, entry.failures
            )


async def retry_with_backoff(
    coro_fn,
    *,
    max_retries: int,
    base_delay: float,
    provider: str,
    operation: str,
    circuit_breaker: CircuitBreaker,
    timeout: float,
):
    """Execute coro_fn() with timeout, retry+backoff, and circuit breaker.

    Returns (result, total_attempts) on success.
    Raises a typed GatewayError on terminal failure.
    """
    last_exc: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        circuit_breaker.check(provider, operation)

        try:
            result = await asyncio.wait_for(coro_fn(), timeout=timeout)
            circuit_breaker.record_success(provider, operation)
            return result, attempt + 1

        except asyncio.TimeoutError:
            last_exc = GatewayTimeoutError(
                f"{provider}/{operation} timed out after {timeout}s"
            )
            circuit_breaker.record_failure(provider, operation)

        except RateLimitError as exc:
            last_exc = exc
            wait = exc.retry_after if exc.retry_after else base_delay * (2 ** attempt)
            if attempt < max_retries:
                await asyncio.sleep(wait + random.uniform(0, 0.1))
            continue

        except APIUnavailableError:
            raise

        except ProviderError as exc:
            # 4xx (except 429) are not retriable
            if 400 <= exc.status < 500 and exc.status != 429:
                circuit_breaker.record_failure(provider, operation)
                raise
            last_exc = exc
            circuit_breaker.record_failure(provider, operation)

        except Exception as exc:
            last_exc = exc
            circuit_breaker.record_failure(provider, operation)

        if attempt < max_retries:
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.1)
            logger.debug(
                "Retry %d/%d for %s/%s in %.2fs (last error: %s)",
                attempt + 1, max_retries, provider, operation, delay, last_exc,
            )
            await asyncio.sleep(delay)

    raise last_exc or APIUnavailableError(provider)
