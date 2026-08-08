from __future__ import annotations
from typing import Optional


class GatewayError(Exception):
    """Base for all gateway errors — consumers catch this for any gateway failure."""


class QuotaExceededError(GatewayError):
    def __init__(self, target: "QuotaTarget"):
        self.target = target
        super().__init__(
            f"Quota exceeded: {target.scope}:{target.key}:{target.type}"
        )


class RateLimitError(GatewayError):
    def __init__(self, provider: str, retry_after: Optional[float] = None):
        self.provider = provider
        self.retry_after = retry_after
        msg = f"Rate limited by {provider!r}"
        if retry_after:
            msg += f" (retry after {retry_after:.1f}s)"
        super().__init__(msg)


class ModelRateLimitError(GatewayError):
    """Our own provider-account limit would be breached — the call was not made.

    Raised by ``gateway.ratelimit.RateLimiter`` *before* contacting the
    provider, unlike ``RateLimitError`` which reports a provider-side 429.
    """

    def __init__(
        self,
        provider: str,
        model: str,
        rule: str,
        limit: int,
        retry_after: Optional[float] = None,
    ):
        self.provider = provider
        self.model = model
        self.rule = rule
        self.limit = limit
        self.retry_after = retry_after
        msg = f"Rate limit {rule!r} reached for {provider}/{model} (limit {limit})"
        if retry_after:
            msg += f" — resets in {retry_after:.0f}s"
        super().__init__(msg)


class APIUnavailableError(GatewayError):
    def __init__(self, provider: str):
        self.provider = provider
        super().__init__(
            f"Provider {provider!r} is unavailable (circuit open or retries exhausted)"
        )


class GatewayTimeoutError(GatewayError):
    pass


class ProviderError(GatewayError):
    def __init__(self, provider: str, status: int, body: str = ""):
        self.provider = provider
        self.status = status
        self.body = body
        super().__init__(f"Provider {provider!r} returned HTTP {status}: {body[:200]}")


class ConfigurationError(GatewayError):
    pass
