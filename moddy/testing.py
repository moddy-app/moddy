"""Small fakes for deterministic tests of interaction helpers."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeResponse:
    """In-memory response recorder compatible with ``InteractionResponse`` tests."""

    done: bool = False
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def is_done(self) -> bool:
        return self.done

    async def send_message(self, **kwargs: Any) -> None:
        self.done = True
        self.calls.append(("send_message", kwargs))

    async def defer(self, **kwargs: Any) -> None:
        self.done = True
        self.calls.append(("defer", kwargs))


__all__ = ("FakeResponse",)
