"""
Moddy API Gateway — centralized client for all external API calls.

All OpenAI and DeepL calls go through this package. No module calls
provider APIs directly. The gateway enforces quotas, resilience, and
logs every request to both a staff webhook and the api_calls PG table.

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
"""

from __future__ import annotations
import logging
from typing import Optional

from .config import GatewayConfig
from .errors import (
    GatewayError,
    QuotaExceededError,
    RateLimitError,
    APIUnavailableError,
    GatewayTimeoutError,
    ProviderError,
    ConfigurationError,
)
from .spec import CallSpec, QuotaTarget, QuotaScope, QuotaPlan

logger = logging.getLogger("moddy.gateway")

__all__ = [
    "Gateway",
    "GatewayConfig",
    "QuotaTarget",
    "QuotaScope",
    "QuotaPlan",
    "GatewayError",
    "QuotaExceededError",
    "RateLimitError",
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
        self._cb = None
        self._gw_logger = None
        self._executor = None

        # Public clients (available after start())
        self.ai = None
        self.translation = None

    async def start(self, redis, pool, tech_logger=None) -> None:
        """Boot the gateway. Call after Redis + DB pool are ready."""
        from .quota import QuotaManager
        from .resilience import CircuitBreaker
        from .logger import GatewayLogger
        from .executor import GatewayExecutor
        from .clients.ai import AIClient
        from .clients.translation import TranslationClient
        from .adapters.openai import OpenAIAdapter
        from .adapters.deepl import DeepLAdapter

        self._quota = QuotaManager(redis, pool)
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
                logger.error("OpenAI adapter failed to start: %s", exc)
        else:
            logger.warning("OPENAI_API_KEY not set — OpenAI adapter disabled")

        if self.config.deepl_api_key:
            try:
                adapter = DeepLAdapter(
                    self.config.deepl_api_key, free=self.config.deepl_free
                )
                await adapter.start()
                self._adapters["deepl"] = adapter
            except Exception as exc:
                logger.error("DeepL adapter failed to start: %s", exc)
        else:
            logger.warning("DEEPL_API_KEY not set — DeepL adapter disabled")

        self._executor = GatewayExecutor(
            adapters=self._adapters,
            quota=self._quota,
            circuit_breaker=self._cb,
            gw_logger=self._gw_logger,
            config=self.config,
        )

        self.ai = AIClient(self._executor)
        self.translation = TranslationClient(self._executor)

        self._gw_logger.start()
        self._started = True
        logger.info(
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
                logger.warning("Error stopping %s adapter: %s", name, exc)
        self._started = False
        logger.info("Gateway stopped")

    @property
    def available(self) -> bool:
        return self._started and self._executor is not None

    def openai_available(self) -> bool:
        return "openai" in self._adapters

    def deepl_available(self) -> bool:
        return "deepl" in self._adapters

    async def quota_available(self, target: "QuotaTarget") -> bool:
        """Convenience check for consumer-side availability gating."""
        if self._quota is None:
            return True
        return await self._quota.available(target)
