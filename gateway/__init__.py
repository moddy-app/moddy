"""
Moddy API Gateway — centralized client for all external API calls.

All OpenAI, DeepL and Groq calls go through this package. No module calls
provider APIs directly. The gateway enforces quotas and provider-account
rate limits, adds resilience, and logs every request to both a staff webhook
and the api_calls PG table.

Usage (from any cog or module):
    result = await bot.gateway.ai.chat(
        system=..., user=..., model="gpt-4.1-nano",
        quota=[QuotaTarget.guild(guild_id, "ban_reason")],
        call_type="ban_reason",
        metadata={"guild_id": guild_id, "user_id": user_id},
    )

    out = await bot.gateway.translation.translate(
        text, target_lang="EN-US",
        quota=[QuotaTarget.user(user_id, "translation")],
        call_type="translation",
        metadata={"user_id": user_id},
    )

    result = await bot.gateway.transcription.transcribe(
        audio_bytes, filename="voice-message.ogg", duration_hint=12.4,
        quota=[QuotaTarget.user(user_id, "voice_transcription")],
        call_type="voice_transcription",
        metadata={"guild_id": guild_id, "user_id": user_id},
    )
"""

from __future__ import annotations
import logging
from typing import Optional

from .config import GatewayConfig
from .errors import (
    GatewayError,
    QuotaExceededError,
    RateLimitError,
    ModelRateLimitError,
    APIUnavailableError,
    GatewayTimeoutError,
    ProviderError,
    ConfigurationError,
)
from .ratelimit import RateRule, UNIT_AUDIO_SECONDS, UNIT_REQUESTS
from .spec import CallSpec, QuotaTarget, QuotaScope, QuotaPlan

# Named `_log`, not `logger` — `Gateway.start()` below does
# `from .logger import GatewayLogger`, and that import binds the
# `gateway.logger` submodule onto this package's namespace, which would
# silently overwrite a global literally named `logger`.
_log = logging.getLogger("moddy.gateway")

__all__ = [
    "Gateway",
    "GatewayConfig",
    "QuotaTarget",
    "QuotaScope",
    "QuotaPlan",
    "RateRule",
    "UNIT_REQUESTS",
    "UNIT_AUDIO_SECONDS",
    "GatewayError",
    "QuotaExceededError",
    "RateLimitError",
    "ModelRateLimitError",
    "APIUnavailableError",
    "GatewayTimeoutError",
    "ProviderError",
    "ConfigurationError",
]


class Gateway:
    """Central API gateway. Lives on ``bot.gateway``.

    Create in ``bot.__init__``, call ``await gateway.start(...)`` in
    ``setup_hook`` once Redis and DB pool are ready.
    """

    def __init__(self, config: Optional[GatewayConfig] = None):
        self.config = config or GatewayConfig.from_env()
        self._started = False

        # Populated by start()
        self._adapters: dict = {}
        self._quota = None
        self._ratelimiter = None
        self._cb = None
        self._gw_logger = None
        self._executor = None

        # Public clients (available after start())
        self.ai = None
        self.translation = None
        self.transcription = None

    async def start(self, redis, pool, tech_logger=None) -> None:
        """Boot the gateway. Call after Redis + DB pool are ready."""
        from .quota import QuotaManager
        from .ratelimit import RateLimiter
        from .resilience import CircuitBreaker
        from .logger import GatewayLogger
        from .executor import GatewayExecutor
        from .clients.ai import AIClient
        from .clients.translation import TranslationClient
        from .clients.transcription import TranscriptionClient
        from .adapters.openai import OpenAIAdapter
        from .adapters.deepl import DeepLAdapter
        from .adapters.groq import GroqAdapter

        self._quota = QuotaManager(redis, pool)
        self._ratelimiter = RateLimiter(redis, self.config.model_rate_limits)
        self._cb = CircuitBreaker(
            failure_threshold=self.config.cb_failure_threshold,
            cooldown=self.config.cb_cooldown,
        )
        self._gw_logger = GatewayLogger(redis, pool, self.config, tech_logger)

        if self.config.openai_api_key:
            try:
                adapter = OpenAIAdapter(self.config.openai_api_key)
                await adapter.start()
                self._adapters["openai"] = adapter
            except Exception as exc:
                _log.error("OpenAI adapter failed to start: %s", exc)
        else:
            _log.warning("OPENAI_API_KEY not set — OpenAI adapter disabled")

        if self.config.deepl_api_key:
            try:
                adapter = DeepLAdapter(
                    self.config.deepl_api_key, free=self.config.deepl_free
                )
                await adapter.start()
                self._adapters["deepl"] = adapter
            except Exception as exc:
                _log.error("DeepL adapter failed to start: %s", exc)
        else:
            _log.warning("DEEPL_API_KEY not set — DeepL adapter disabled")

        if self.config.groq_api_key:
            try:
                adapter = GroqAdapter(self.config.groq_api_key)
                await adapter.start()
                self._adapters["groq"] = adapter
            except Exception as exc:
                _log.error("Groq adapter failed to start: %s", exc)
        else:
            _log.warning("GROQ_API_KEY not set — Groq adapter disabled")

        self._executor = GatewayExecutor(
            adapters=self._adapters,
            quota=self._quota,
            circuit_breaker=self._cb,
            gw_logger=self._gw_logger,
            config=self.config,
            ratelimiter=self._ratelimiter,
        )

        self.ai = AIClient(self._executor)
        self.translation = TranslationClient(self._executor)
        self.transcription = TranscriptionClient(self._executor)

        self._gw_logger.start()
        self._started = True
        _log.info(
            "Gateway started — adapters: %s",
            list(self._adapters) or ["none"],
        )

    async def stop(self) -> None:
        if self._gw_logger:
            await self._gw_logger.stop()
        for name, adapter in self._adapters.items():
            try:
                await adapter.stop()
            except Exception as exc:
                _log.warning("Error stopping %s adapter: %s", name, exc)
        self._started = False
        _log.info("Gateway stopped")

    @property
    def available(self) -> bool:
        return self._started and self._executor is not None

    def openai_available(self) -> bool:
        return "openai" in self._adapters

    def deepl_available(self) -> bool:
        return "deepl" in self._adapters

    def groq_available(self) -> bool:
        return "groq" in self._adapters

    async def quota_available(self, target: "QuotaTarget") -> bool:
        """Convenience check for consumer-side availability gating."""
        if self._quota is None:
            return True
        return await self._quota.available(target)

    async def rate_limit_usage(self, provider: str, model: str) -> list:
        """Current provider-account rate-limit usage — staff diagnostics."""
        if self._ratelimiter is None:
            return []
        return await self._ratelimiter.snapshot(provider, model)
