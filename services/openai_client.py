"""
OpenAI service client — thin async wrapper around the OpenAI SDK.

Usage (from a cog or anywhere you have a bot reference):

    response = await bot.openai.complete(
        messages=[{"role": "user", "content": "..."}],
        context=OpenAIContext(guild_id=ctx.guild.id, user_id=ctx.user.id),
    )
    text = response.choices[0].message.content

The ``context`` parameter is optional today but is the hook point for future
per-guild / per-user quota enforcement and request interception.  Pre- and
post-hooks can be registered on the client to intercept every call:

    bot.openai.add_pre_hook(my_quota_checker)   # runs before the API call
    bot.openai.add_post_hook(my_usage_tracker)  # runs after (with usage info)

A pre-hook that raises ``OpenAIQuotaExceeded`` (or any exception) aborts the
request before it hits the API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, List, Optional

logger = logging.getLogger("moddy.services.openai")


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class OpenAIContext:
    """Caller-supplied metadata attached to every request.

    Passed to all pre- and post-hooks so they can enforce quotas, log usage,
    or tag telemetry without touching the core client logic.
    """
    guild_id: Optional[int] = None
    user_id: Optional[int] = None
    # Arbitrary key/value bag for future extension (feature tag, cog name, …)
    extra: dict = field(default_factory=dict)


class OpenAIQuotaExceeded(Exception):
    """Raised by a pre-hook to block a request before it hits the API."""


# Hook type aliases
PreHook = Callable[[OpenAIContext, dict], Awaitable[None]]
PostHook = Callable[[OpenAIContext, Any, dict], Awaitable[None]]


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIClient:
    """Async OpenAI client attached to the bot instance as ``bot.openai``."""

    def __init__(self, bot) -> None:
        self.bot = bot
        self._client = None
        self._started = False
        self._pre_hooks: List[PreHook] = []
        self._post_hooks: List[PostHook] = []

    # ------------------------------------------------------------------ #
    # Hook registration
    # ------------------------------------------------------------------ #

    def add_pre_hook(self, hook: PreHook) -> None:
        """Register an async pre-request hook.

        Signature: ``async def hook(ctx: OpenAIContext, params: dict) -> None``
        Raise to abort the request (e.g. quota exceeded).
        """
        self._pre_hooks.append(hook)

    def add_post_hook(self, hook: PostHook) -> None:
        """Register an async post-request hook.

        Signature: ``async def hook(ctx: OpenAIContext, response, usage: dict) -> None``
        ``usage`` is a dict with ``prompt_tokens``, ``completion_tokens``, ``total_tokens``.
        """
        self._post_hooks.append(hook)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        if self._started:
            return

        from config import OPENAI_API_KEY
        if not OPENAI_API_KEY:
            logger.warning("[OpenAI] OPENAI_API_KEY not set — OpenAI client disabled")
            return

        try:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=OPENAI_API_KEY)
            self._started = True
            logger.info("[OpenAI] Client ready")
        except ImportError:
            logger.error("[OpenAI] 'openai' package not installed — client disabled")

    async def stop(self) -> None:
        if self._client:
            try:
                await self._client.close()
            except Exception:
                pass
        self._client = None
        self._started = False

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    @property
    def available(self) -> bool:
        return self._started and self._client is not None

    async def complete(
        self,
        messages: list[dict],
        *,
        model: str = DEFAULT_MODEL,
        context: Optional[OpenAIContext] = None,
        **kwargs: Any,
    ) -> Any:
        """Send a chat completion request.

        Args:
            messages: OpenAI-format message list.
            model:    Model name (default: gpt-4o-mini).
            context:  Caller metadata for hooks and future quota checks.
            **kwargs: Forwarded verbatim to ``client.chat.completions.create()``.

        Returns:
            The raw ``ChatCompletion`` object from the OpenAI SDK.

        Raises:
            RuntimeError: If the client is not started / API key missing.
            OpenAIQuotaExceeded: If a pre-hook blocks the request.
        """
        if not self.available:
            raise RuntimeError("OpenAI client is not available (check OPENAI_API_KEY)")

        if context is None:
            context = OpenAIContext()

        params = {"model": model, "messages": messages, **kwargs}

        # Run pre-hooks (any exception propagates to the caller)
        for hook in self._pre_hooks:
            await hook(context, params)

        response = await self._client.chat.completions.create(**params)

        # Build usage dict for post-hooks
        usage: dict = {}
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        # Run post-hooks (errors are caught so they don't kill the response)
        for hook in self._post_hooks:
            try:
                await hook(context, response, usage)
            except Exception as e:
                logger.error(f"[OpenAI] Post-hook error: {e}", exc_info=True)

        return response

    async def complete_text(
        self,
        messages: list[dict],
        *,
        model: str = DEFAULT_MODEL,
        context: Optional[OpenAIContext] = None,
        **kwargs: Any,
    ) -> str:
        """Convenience wrapper — returns the first choice's text directly."""
        response = await self.complete(messages, model=model, context=context, **kwargs)
        return response.choices[0].message.content or ""
