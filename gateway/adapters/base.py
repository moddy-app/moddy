from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any


class AdapterResult:
    """Normalized result from any provider call."""
    __slots__ = ("data", "tokens_prompt", "tokens_completion", "tokens_total")

    def __init__(
        self,
        data: Any,
        tokens_prompt: int = 0,
        tokens_completion: int = 0,
        tokens_total: int = 0,
    ):
        self.data = data
        self.tokens_prompt = tokens_prompt
        self.tokens_completion = tokens_completion
        self.tokens_total = tokens_total


class BaseAdapter(ABC):
    """Abstract provider adapter — translates a CallSpec into an HTTP call."""

    @property
    @abstractmethod
    def provider(self) -> str: ...

    @abstractmethod
    async def execute(self, spec: "CallSpec") -> AdapterResult: ...

    async def start(self) -> None:
        """Lifecycle hook — open HTTP session, validate credentials."""

    async def stop(self) -> None:
        """Lifecycle hook — close HTTP session."""
