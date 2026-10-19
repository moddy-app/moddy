from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .ratelimit import (
    CALENDAR_MONTH,
    DAY,
    HOUR,
    MINUTE,
    MONTH,
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


def _openai_rpm_rules(env_name: str, default_rpm: int) -> List[RateRule]:
    """Request-per-minute cap mirroring the OpenAI console (*Limits* page).

    Only RPM is enforced here (not TPM): estimating tokens before the call
    would need a tokenizer, and RPM alone is enough to stop the bot from
    bursting past what the account tier allows.
    """
    return [RateRule("rpm", UNIT_REQUESTS, MINUTE, _int_env(env_name, default_rpm))]


def _google_vision_monthly_rules(env_name: str, default: int) -> List[RateRule]:
    """Google Vision free tier: ``default`` units per calendar month, per feature.

    The window follows the calendar month in Pacific time (Google's billing
    clock) and the rule is **fail-closed**: past the allowance every unit is
    billed, so a Redis outage must refuse the call rather than let it through.
    """
    return [RateRule(
        "rpmo", UNIT_REQUESTS, MONTH, _int_env(env_name, default),
        calendar=CALENDAR_MONTH, fail_closed=True,
    )]


def _saucenao_rules() -> List[RateRule]:
    """SauceNAO account limits: a 30-second burst window and a daily cap.

    Defaults are deliberately below what a free account is documented to allow
    (third-party wrappers quote 6-8 searches per 30 s and 100-200 per day;
    SauceNAO's own page is the reference — check it before raising them). Raise
    them here or via the env vars when the account tier changes.
    """
    return [
        RateRule("r30s", UNIT_REQUESTS, 30, _int_env("SAUCENAO_PER_30S", 6)),
        RateRule("rpd", UNIT_REQUESTS, DAY, _int_env("SAUCENAO_PER_DAY", 100)),
    ]


def _tracemoe_rules() -> List[RateRule]:
    """trace.moe quota: 100 searches a month anonymously, more with a key.

    The anonymous allowance is shared by every Moddy user (one IP), so the rule
    is a calendar-month counter in UTC. With ``TRACEMOE_API_KEY`` set the
    default rises to 1000 — set ``TRACEMOE_MONTHLY`` to the account's real quota.
    """
    default = 1_000 if os.environ.get("TRACEMOE_API_KEY") else 100
    return [
        RateRule("rpmo", UNIT_REQUESTS, MONTH, _int_env("TRACEMOE_MONTHLY", default),
                 calendar=CALENDAR_MONTH, tz="UTC"),
        RateRule("rpm", UNIT_REQUESTS, MINUTE, _int_env("TRACEMOE_PER_MINUTE", 30)),
    ]


def _cloudconvert_rules() -> List[RateRule]:
    """CloudConvert account limits: a burst cap and the daily free allowance.

    The free plan gives 25 conversions a day; past that every conversion spends
    paid credits, so the daily rule is **fail-closed** and defaults to the free
    allowance. Raise ``CLOUDCONVERT_PER_DAY`` once a credit pack is bought.
    """
    return [
        RateRule("rpm", UNIT_REQUESTS, MINUTE, _int_env("CLOUDCONVERT_PER_MINUTE", 10)),
        RateRule("rpd", UNIT_REQUESTS, DAY, _int_env("CLOUDCONVERT_PER_DAY", 25), fail_closed=True),
    ]


def _default_model_rate_limits() -> Dict[Tuple[str, str], List[RateRule]]:
    """(provider, model) → the rules enforced for it. Add new models here.

    OpenAI values mirror the org's Tier 1 limits (platform.openai.com →
    Limits → Rate limits) as of 2026-09-02. Raise them here (or via the env
    vars) the day the account tier changes.
    """
    return {
        ("groq", "whisper-large-v3-turbo"): _whisper_turbo_rules(),
        ("openai", "text-embedding-3-small"): _openai_rpm_rules(
            "OPENAI_EMBED_RPM", 3_000
        ),
        ("openai", "gpt-4.1-nano"): _openai_rpm_rules("OPENAI_NANO_RPM", 500),
        ("openai", "gpt-4.1-mini"): _openai_rpm_rules("OPENAI_MINI_RPM", 500),
        # Google Vision has no model: rules are keyed on the billed feature.
        ("google_vision", "safe_search"): _google_vision_monthly_rules(
            "GOOGLE_VISION_SAFESEARCH_MONTHLY", 1_000
        ),
        # Profile avatars / banners: the SAME Google API (SafeSearch), accounted on
        # its own key so member-profile checks and message images never starve
        # each other. The adapter still calls the real "safe_search" operation —
        # only the rate-limit accounting key differs (see VisionClient).
        ("google_vision", "safe_search_profile"): _google_vision_monthly_rules(
            "GOOGLE_VISION_SAFESEARCH_PROFILE_MONTHLY", 1_000
        ),
        ("google_vision", "document_text"): _google_vision_monthly_rules(
            "GOOGLE_VISION_OCR_MONTHLY", 1_000
        ),
        # Reverse image search (/sauce). The operation is the model key.
        ("saucenao", "search"): _saucenao_rules(),
        ("tracemoe", "search"): _tracemoe_rules(),
        # Office documents / ebooks conversion (/convert, cloud path only).
        ("cloudconvert", "convert"): _cloudconvert_rules(),
    }


@dataclass
class GatewayConfig:
    openai_api_key: Optional[str] = None
    deepl_api_key: Optional[str] = None
    groq_api_key: Optional[str] = None
    google_vision_api_key: Optional[str] = None
    saucenao_api_key: Optional[str] = None
    tracemoe_api_key: Optional[str] = None  # optional: trace.moe works anonymously
    cloudconvert_api_key: Optional[str] = None  # /convert office docs + ebooks; hidden when absent
    deepl_free: bool = True

    # Timeouts (seconds)
    timeout_embed: float = 10.0
    timeout_chat: float = 30.0
    timeout_translate: float = 15.0
    # Transcription uploads a file and processes minutes of audio — it is
    # legitimately slower than a chat completion.
    timeout_transcribe: float = 90.0
    # Image calls upload a few MB and run OCR/vision server-side.
    timeout_vision: float = 30.0
    # Reverse image search: the provider fetches the image and compares it.
    timeout_search: float = 25.0
    # Document conversion (CloudConvert job: import + convert + export, polled).
    timeout_convert: float = 120.0

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
            google_vision_api_key=os.environ.get("GOOGLE_VISION_API_KEY") or None,
            saucenao_api_key=os.environ.get("SAUCENAO_API_KEY") or None,
            tracemoe_api_key=os.environ.get("TRACEMOE_API_KEY") or None,
            cloudconvert_api_key=os.environ.get("CLOUDCONVERT_API_KEY") or None,
            deepl_free=os.environ.get("DEEPL_FREE", "true").lower() != "false",
            timeout_embed=float(os.environ.get("GATEWAY_TIMEOUT_EMBED", "10")),
            timeout_chat=float(os.environ.get("GATEWAY_TIMEOUT_CHAT", "30")),
            timeout_translate=float(os.environ.get("GATEWAY_TIMEOUT_TRANSLATE", "15")),
            timeout_transcribe=float(os.environ.get("GATEWAY_TIMEOUT_TRANSCRIBE", "90")),
            timeout_vision=float(os.environ.get("GATEWAY_TIMEOUT_VISION", "30")),
            timeout_search=float(os.environ.get("GATEWAY_TIMEOUT_SEARCH", "25")),
            timeout_convert=float(os.environ.get("GATEWAY_TIMEOUT_CONVERT", "120")),
            max_retries=int(os.environ.get("GATEWAY_MAX_RETRIES", "3")),
            retry_base_delay=float(os.environ.get("GATEWAY_RETRY_BASE_DELAY", "0.5")),
            cb_failure_threshold=int(os.environ.get("GATEWAY_CB_FAILURE_THRESHOLD", "5")),
            cb_cooldown=float(os.environ.get("GATEWAY_CB_COOLDOWN", "60")),
        )
