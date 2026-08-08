from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .ratelimit import (
    DAY,
    HOUR,
    MINUTE,
    RateRule,
    UNIT_AUDIO_SECONDS,
    UNIT_REQUESTS,
)


def _int_env(name: str, default: int) -> int:
    """Read an int env var, falling back to `default` on anything unparseable."""
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _whisper_turbo_rules() -> List[RateRule]:
    """Provider-account limits for ``whisper-large-v3-turbo``.

    These mirror the Groq console (*Configure Model Limits*). They are global
    (not per guild or per user) and are enforced before any call leaves the bot
    — see ``gateway/ratelimit.py``. Raise them here (or via the env vars) the
    day the account tier changes.
    """
    return [
        RateRule("rpm", UNIT_REQUESTS, MINUTE,
                 _int_env("GROQ_WHISPER_RPM", 20)),
        RateRule("rpd", UNIT_REQUESTS, DAY,
                 _int_env("GROQ_WHISPER_RPD", 2_000)),
        RateRule("ash", UNIT_AUDIO_SECONDS, HOUR,
                 _int_env("GROQ_WHISPER_AUDIO_SECONDS_PER_HOUR", 7_200)),
        RateRule("asd", UNIT_AUDIO_SECONDS, DAY,
                 _int_env("GROQ_WHISPER_AUDIO_SECONDS_PER_DAY", 28_800)),
    ]


def _default_model_rate_limits() -> Dict[Tuple[str, str], List[RateRule]]:
    """(provider, model) → the rules enforced for it. Add new models here."""
    return {
        ("groq", "whisper-large-v3-turbo"): _whisper_turbo_rules(),
    }


@dataclass
class GatewayConfig:
    openai_api_key: Optional[str] = None
    deepl_api_key: Optional[str] = None
    groq_api_key: Optional[str] = None
    deepl_free: bool = True

    # Timeouts (seconds)
    timeout_embed: float = 10.0
    timeout_chat: float = 30.0
    timeout_translate: float = 15.0
    # Transcription uploads a file and processes minutes of audio — it is
    # legitimately slower than a chat completion.
    timeout_transcribe: float = 90.0

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

    # Provider-account rate limits, per (provider, model). See ratelimit.py.
    model_rate_limits: Dict[Tuple[str, str], List[RateRule]] = field(
        default_factory=_default_model_rate_limits
    )

    # Cost estimates (USD per 1M tokens)
    cost_table: dict = field(default_factory=lambda: {
        ("openai", "text-embedding-3-small", "input"): 0.02,
        ("openai", "gpt-4.1-nano", "input"): 0.10,
        ("openai", "gpt-4.1-nano", "output"): 0.40,
        ("openai", "gpt-4.1-mini", "input"): 0.40,
        ("openai", "gpt-4.1-mini", "output"): 1.60,
        ("openai", "gpt-4o-mini", "input"): 0.15,
        ("openai", "gpt-4o-mini", "output"): 0.60,
    })

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        return cls(
            openai_api_key=os.environ.get("OPENAI_API_KEY") or None,
            deepl_api_key=os.environ.get("DEEPL_API_KEY") or None,
            groq_api_key=os.environ.get("GROQ_API_KEY") or None,
            deepl_free=os.environ.get("DEEPL_FREE", "true").lower() != "false",
            timeout_embed=float(os.environ.get("GATEWAY_TIMEOUT_EMBED", "10")),
            timeout_chat=float(os.environ.get("GATEWAY_TIMEOUT_CHAT", "30")),
            timeout_translate=float(os.environ.get("GATEWAY_TIMEOUT_TRANSLATE", "15")),
            timeout_transcribe=float(os.environ.get("GATEWAY_TIMEOUT_TRANSCRIBE", "90")),
            max_retries=int(os.environ.get("GATEWAY_MAX_RETRIES", "3")),
            retry_base_delay=float(os.environ.get("GATEWAY_RETRY_BASE_DELAY", "0.5")),
            cb_failure_threshold=int(os.environ.get("GATEWAY_CB_FAILURE_THRESHOLD", "5")),
            cb_cooldown=float(os.environ.get("GATEWAY_CB_COOLDOWN", "60")),
        )
