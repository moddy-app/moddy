"""
AutomodEngine — the shared, per-bot orchestrator for the content pipeline.

One engine instance is shared by every guild (the blocklist and the embedding
references are global, identical everywhere); guild-specific inputs (rules,
author history, context) are passed per call. This is the scalable seam: a guild
module owns *configuration and actions*, the engine owns *detection*.

All external calls go through ``bot.gateway`` (quotas, resilience, logging).
"""

from __future__ import annotations

import logging
import uuid
from typing import Awaitable, Callable, List, Optional

from . import constants, nano
from .blocklist import get_blocklist
from .embeddings import EmbeddingEngine
from .prefiltre import pre_filter
from .schemas import Signal, Decision, TargetMessage, ContextMessage, AuthorHistory
from .triviaux import est_trivial

logger = logging.getLogger("moddy.automod.engine")

ContextFn = Callable[[int], Awaitable[List[ContextMessage]]]


class AutomodEngine:
    """Runs the funnel: prefilter → trivial → blocklist → embedding → nano."""

    def __init__(self, bot):
        self.bot = bot
        self.blocklist = get_blocklist()
        self.embeddings = EmbeddingEngine(self._embed)

    # -- Gateway-backed primitives -----------------------------------------

    async def _embed(self, texts: List[str]) -> List[List[float]]:
        """Embed texts through the gateway (call_type not quota-gated)."""
        return await self.bot.gateway.ai.embed(
            texts,
            model=constants.EMBEDDING_MODEL,
            call_type=constants.CALL_TYPE_EMBED,
            metadata={"feature": "automod"},
        )

    def _make_chat_fn(self, guild_id: int, correlation_id: str):
        from gateway import QuotaTarget

        async def chat_fn(system: str, user: str) -> dict:
            result = await self.bot.gateway.ai.chat(
                system=system,
                user=user,
                model=constants.NANO_MODEL,
                json_mode=True,
                temperature=constants.NANO_TEMPERATURE,
                max_tokens=constants.NANO_MAX_TOKENS,
                quota=[QuotaTarget.guild(guild_id, constants.CALL_TYPE_DECISION)],
                call_type=constants.CALL_TYPE_DECISION,
                correlation_id=correlation_id,
                metadata={"feature": "automod", "guild_id": guild_id},
            )
            return result if isinstance(result, dict) else {}

        return chat_fn

    async def ensure_ready(self) -> bool:
        """Embed the reference phrases once (lazy). Returns readiness."""
        try:
            return await self.embeddings.ensure_ready()
        except Exception as e:
            logger.error("automod: failed to embed references: %s", e)
            return False

    # -- Funnel ------------------------------------------------------------

    async def analyze(
        self,
        target: TargetMessage,
        *,
        guild_id: int,
        guild_name: str,
        rules: str,
        author_history: AuthorHistory,
        fetch_context: ContextFn,
        is_bot: bool = False,
        is_system: bool = False,
        force_nano: bool = False,
    ) -> Optional[Decision]:
        """Run a message through the funnel and return a Decision or None.

        ``force_nano`` short-circuits the detectors and sends the message
        straight to nano with ``source=signalé_par_nano`` — used by the caller
        to re-analyse messages flagged in ``a_reverifier``.
        """
        correlation_id = str(uuid.uuid4())

        if force_nano:
            signal = Signal(
                source=constants.SOURCE_NANO_FLAG,
                categorie="",
                score_confiance=0.0,
            )
            return await self._judge(
                target, signal, guild_id=guild_id, guild_name=guild_name,
                rules=rules, author_history=author_history,
                fetch_context=fetch_context, correlation_id=correlation_id,
            )

        # Step 1 — pre-filter.
        if not pre_filter(target.content, is_bot=is_bot, is_system=is_system):
            return None

        # Step 2 — trivial allowlist.
        if est_trivial(target.content):
            return None

        # Step 3 — regex blocklist.
        entry = self.blocklist.match(target.content)
        if entry is not None:
            signal = Signal(
                source=constants.SOURCE_REGEX,
                categorie=entry.categorie,
                score_confiance=constants.GRAVITE_TO_SCORE.get(
                    entry.gravite_indicative, 0.7
                ),
            )
            return await self._judge(
                target, signal, guild_id=guild_id, guild_name=guild_name,
                rules=rules, author_history=author_history,
                fetch_context=fetch_context, correlation_id=correlation_id,
            )

        # Step 4 — embedding.
        if not await self.ensure_ready():
            return None  # graceful degradation: embeddings unavailable
        scored = await self.embeddings.score(target.content)
        if scored is None:
            return None
        score, categorie = scored
        if not EmbeddingEngine.passes_threshold(score):
            return None
        signal = Signal(
            source=constants.SOURCE_EMBEDDING,
            categorie=categorie,
            score_confiance=score,
        )

        # Step 5 — nano.
        return await self._judge(
            target, signal, guild_id=guild_id, guild_name=guild_name,
            rules=rules, author_history=author_history,
            fetch_context=fetch_context, correlation_id=correlation_id,
        )

    async def _judge(
        self,
        target: TargetMessage,
        signal: Signal,
        *,
        guild_id: int,
        guild_name: str,
        rules: str,
        author_history: AuthorHistory,
        fetch_context: ContextFn,
        correlation_id: str,
    ) -> Decision:
        return await nano.juger(
            target,
            signal,
            guild_name=guild_name,
            rules=rules,
            history=author_history,
            chat_fn=self._make_chat_fn(guild_id, correlation_id),
            fetch_context=fetch_context,
        )


def get_engine(bot) -> AutomodEngine:
    """Return the shared per-bot engine, creating it on first access."""
    engine = getattr(bot, "_automod_engine", None)
    if engine is None:
        engine = AutomodEngine(bot)
        bot._automod_engine = engine
    return engine
