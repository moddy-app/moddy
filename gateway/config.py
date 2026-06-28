from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GatewayConfig:
    openai_api_key: Optional[str] = None
    deepl_api_key: Optional[str] = None
    deepl_free: bool = True

    # Timeouts (seconds)
    timeout_embed: float = 10.0
    timeout_chat: float = 30.0
    timeout_translate: float = 15.0

    # Retry
    max_retries: int = 3
    retry_base_delay: float = 0.5

    # Circuit breaker
    cb_failure_threshold: int = 5
    cb_cooldown: float = 60.0

    # Log buffer (Redis list → PG)
    log_buffer_key: str = "gateway:log_buffer"
    log_flush_batch: int = 50
    log_flush_interval: float = 5.0

    # Cost estimates (USD per 1M tokens)
    cost_table: dict = field(default_factory=lambda: {
        ("openai", "text-embedding-3-small", "input"): 0.02,
        ("openai", "gpt-4.1-nano", "input"): 0.10,
        ("openai", "gpt-4.1-nano", "output"): 0.40,
        ("openai", "gpt-4o-mini", "input"): 0.15,
        ("openai", "gpt-4o-mini", "output"): 0.60,
    })

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        return cls(
            openai_api_key=os.environ.get("OPENAI_API_KEY") or None,
            deepl_api_key=os.environ.get("DEEPL_API_KEY") or None,
            deepl_free=os.environ.get("DEEPL_FREE", "true").lower() != "false",
            timeout_embed=float(os.environ.get("GATEWAY_TIMEOUT_EMBED", "10")),
            timeout_chat=float(os.environ.get("GATEWAY_TIMEOUT_CHAT", "30")),
            timeout_translate=float(os.environ.get("GATEWAY_TIMEOUT_TRANSLATE", "15")),
            max_retries=int(os.environ.get("GATEWAY_MAX_RETRIES", "3")),
            retry_base_delay=float(os.environ.get("GATEWAY_RETRY_BASE_DELAY", "0.5")),
            cb_failure_threshold=int(os.environ.get("GATEWAY_CB_FAILURE_THRESHOLD", "5")),
            cb_cooldown=float(os.environ.get("GATEWAY_CB_COOLDOWN", "60")),
        )
