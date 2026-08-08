from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class QuotaScope(str, Enum):
    GLOBAL = "global"
    GUILD = "guild"
    USER = "user"
    CUSTOM = "custom"


@dataclass(frozen=True)
class QuotaTarget:
    scope: QuotaScope
    key: str    # guild_id / user_id / "" for global / arbitrary for custom
    type: str   # call_type class: "ban_reason", "translation", …

    @classmethod
    def global_(cls, type: str) -> "QuotaTarget":
        return cls(scope=QuotaScope.GLOBAL, key="", type=type)

    @classmethod
    def guild(cls, key: int | str, type: str) -> "QuotaTarget":
        return cls(scope=QuotaScope.GUILD, key=str(key), type=type)

    @classmethod
    def user(cls, key: int | str, type: str) -> "QuotaTarget":
        return cls(scope=QuotaScope.USER, key=str(key), type=type)

    @classmethod
    def custom(cls, key: str, type: str) -> "QuotaTarget":
        return cls(scope=QuotaScope.CUSTOM, key=key, type=type)


QuotaPlan = list[QuotaTarget]


@dataclass
class CallSpec:
    provider: str           # "openai" | "deepl" | "groq"
    operation: str          # "embed" | "chat" | "translate" | "transcribe"
    model: Optional[str]    # "text-embedding-3-small" | "gpt-4.1-nano" | None
    payload: dict
    quota: QuotaPlan        # [] = not quota-gated
    call_type: str          # "ban_reason" | "translation" | …
    correlation_id: str
    metadata: dict = field(default_factory=dict)

    # Provider-account rate limiting (gateway/ratelimit.py). Cost of this call
    # per unit, e.g. {"requests": 1, "audio_seconds": 12.5}. Empty = not rate
    # limited. Estimates are fine: the adapter can report the real cost and the
    # executor reconciles the reservation afterwards.
    rate_cost: dict = field(default_factory=dict)

    # Binary body (audio file, …). Deliberately kept OUT of `payload` because
    # `payload` is forwarded verbatim to the staff webhook log as JSON.
    binary: Optional[bytes] = None
