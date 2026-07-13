"""
AutomodEngine — the shared, per-bot orchestrator for the content pipeline.

One engine instance is shared by every guild (the blocklist and the embedding
references are global, identical everywhere); guild-specific inputs (rules,
author history, context) are passed per call. This is the scalable seam: a guild
module owns *configuration and actions*, the engine owns *detection*.

All external calls go through ``bot.gateway`` (quotas, resilience, logging).

Cost controls (session 4)
-------------------------
Three mechanisms, all off the hot path for a normal message and none adding an
AI call:

* **Verdict cache** — the nano *qualification* (sanctionnable / categorie /
  gravite / citation / cible) is memoised per (guild, collapsed-text) with a
  short TTL and single-flight, so a copypasta raid that reaches nano costs one
  chat call, not N. The barème (cran + recidivism) is always recomputed.
* **Author aggregation** — a short Redis buffer per (guild, channel, author)
  lets the pipeline judge the *concatenation* of consecutive fragments when a
  message stops before nano, catching fragmented harassment ("je vais" / "te" /
  "retrouver"). The concat is only routed through the cheap steps.
* **Budget guard** — a Redis daily counter per guild bounds real nano calls;
  past the soft cap the funnel degrades (nano reserved for flagrant cases) but
  never goes blind.

Redis is optional: without it the verdict cache and single-flight still work
(in-process), while aggregation and the budget guard are simply inert.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, List, Optional, Set, Tuple

from . import constants, nano, precedents as ap, routing, situation as asit
from .blocklist import get_blocklist
from .cache import MISS, LruTtlCache
from .embeddings import EmbeddingEngine, _retrieve_result
from .normalize import collapse_repeats
from .prefiltre import pre_filter
from .schemas import Signal, Decision, TargetMessage, ContextMessage, AuthorHistory
from .triviaux import est_trivial

logger = logging.getLogger("moddy.automod.engine")

ContextFn = Callable[[int], Awaitable[List[ContextMessage]]]
# Session 5: an async provider of the trusted `relation` block. The engine calls
# it lazily — only when a nano call is actually about to happen — passing whether
# it may spend the reaction-observation window (False for flagrant regex hits).
# Returns the relation dict (familiarite + reaction_cible…) or None.
RelationFn = Callable[[bool], Awaitable[Optional[dict]]]
# Session 7: a provider of the guild's matched server precedents. It is handed a
# lazy async ``get_vector`` (the message's normalised embedding) so a guild with
# no precedents can return early WITHOUT paying an embedding call. Returns the
# ranked ``PrecedentMatch`` list (strongest first) — the engine then decides
# whether to short-circuit (a near-identical human "non_sanctionnable" ruling) or
# to inject them into the decision prompt.
VectorFn = Callable[[], Awaitable[Optional[List[float]]]]
PrecedentsFn = Callable[[VectorFn], Awaitable[List["ap.PrecedentMatch"]]]


class AutomodEngine:
    """Runs the funnel: prefilter → trivial → blocklist → embedding → nano."""

    def __init__(self, bot):
        self.bot = bot
        self.blocklist = get_blocklist()
        self.embeddings = EmbeddingEngine(self._embed)

        # Verdict cache: (guild, collapsed-text) → qualification dict. Same text +
        # same guild ⇒ same qualification, so memoising is safe (the barème, which
        # differs per author, is recomputed downstream). Disabled ⇒ max_entries=0.
        self._verdict_cache: LruTtlCache = LruTtlCache(
            max_entries=(
                constants.VERDICT_CACHE_MAX_ENTRIES
                if constants.VERDICT_CACHE_ENABLED
                else 0
            ),
            ttl_seconds=constants.VERDICT_CACHE_TTL_SECONDS,
        )
        # Single-flight: coalesce concurrent identical qualifications onto one call.
        self._verdict_inflight: Dict[str, "asyncio.Future"] = {}

        # Budget guard bookkeeping (process-local observability; the authoritative
        # counter lives in Redis so it is shared across shards / restarts).
        self._budget_calls = 0          # real nano calls counted this process
        self._budget_dropped = 0        # nano-bound messages dropped over budget
        self._budget_degraded: Set[Tuple[int, str]] = set()   # (guild, day) degraded
        self._budget_notified: Set[Tuple[int, str]] = set()   # (guild, day) card sent
        self._budget_notice_pending: Set[int] = set()          # guilds the module owes a card

    # -- Gateway-backed primitives -----------------------------------------

    async def _embed(self, texts: List[str]) -> List[List[float]]:
        """Embed texts through the gateway (call_type not quota-gated)."""
        return await self.bot.gateway.ai.embed(
            texts,
            model=constants.EMBEDDING_MODEL,
            call_type=constants.CALL_TYPE_EMBED,
            metadata={"feature": "automod"},
        )

    def _make_chat_fn(
        self, guild_id: int, correlation_id: str, *,
        model: str = constants.NANO_MODEL,
        call_type: str = constants.CALL_TYPE_DECISION,
        max_tokens: int = constants.NANO_MAX_TOKENS,
    ):
        """A gateway-backed chat function for the decision/confirmation calls.

        The model / call_type are parametrised so the same path serves nano
        (evident), mini (ambigu, session 6.2) and the mini confirmation (§6.3);
        the quota is always scoped to the guild under the call's own type.
        """
        from gateway import QuotaTarget

        async def chat_fn(system: str, user: str) -> dict:
            result = await self.bot.gateway.ai.chat(
                system=system,
                user=user,
                model=model,
                json_mode=True,
                temperature=constants.NANO_TEMPERATURE,
                max_tokens=max_tokens,
                quota=[QuotaTarget.guild(guild_id, call_type)],
                call_type=call_type,
                correlation_id=correlation_id,
                metadata={"feature": "automod", "guild_id": guild_id},
            )
            return result if isinstance(result, dict) else {}

        return chat_fn

    # -- Diagnostics -------------------------------------------------------

    def cache_stats(self) -> dict:
        """Embedding score-cache counters (hits/misses/evictions/hit rate)."""
        return self.embeddings.cache_stats()

    def verdict_cache_stats(self) -> dict:
        """nano verdict-cache counters plus the in-flight coalescing count."""
        stats = self._verdict_cache.stats()
        stats["inflight"] = len(self._verdict_inflight)
        return stats

    def budget_stats(self) -> dict:
        """Per-guild nano budget guard counters (this process + today's guilds)."""
        day = self._utc_day()
        return {
            "soft_cap": constants.NANO_DAILY_SOFT_CAP,
            "nano_calls": self._budget_calls,
            "dropped": self._budget_dropped,
            "degraded_guilds": sorted(
                {g for (g, d) in self._budget_degraded if d == day}),
        }

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
        severity: int = constants.SEVERITY_DEFAULT,
        response_language: str = "English",
        channel_id: Optional[int] = None,
        relation_fn: Optional[RelationFn] = None,
        precedents_fn: Optional[PrecedentsFn] = None,
    ) -> Optional[Decision]:
        """Run a message through the funnel and return a Decision or None.

        ``force_nano`` short-circuits the detectors and sends the message
        straight to nano with ``source=signalé_par_nano`` — used by the caller
        to re-analyse messages flagged in ``a_reverifier``.

        ``channel_id`` (optional) enables the author-aggregation window: when a
        message stops before nano, the pipeline may re-route the concatenation of
        the author's recent fragments (fragmented-harassment detection). Requires
        ``bot.redis``; without it aggregation is simply skipped.

        ``relation_fn`` (optional, session 5) supplies the trusted relationship
        block (familiarity + target reaction) for a message with an identifiable
        target. It is called lazily, right before the nano call, so the (up to
        20-second) reaction-observation window is only ever paid on the path that
        actually spends a nano call. A relation-carrying decision is **never**
        verdict-cached (the reaction is specific to this message).
        """
        correlation_id = str(uuid.uuid4())
        severity = constants.clamp_severity(severity)

        if force_nano:
            signal = Signal(
                source=constants.SOURCE_NANO_FLAG,
                categorie="",
                score_confiance=0.0,
            )
            return await self._decide(
                target, signal, guild_id=guild_id, guild_name=guild_name,
                rules=rules, author_history=author_history,
                fetch_context=fetch_context, correlation_id=correlation_id,
                severity=severity, response_language=response_language,
                relation_fn=relation_fn, precedents_fn=precedents_fn,
            )

        # Buffer this message for the aggregation window BEFORE routing, so a
        # later fragment can reassemble the sequence including this one.
        if channel_id is not None:
            await self._agg_push(guild_id, channel_id, target)

        signal = await self._route_message(
            target.content, is_bot=is_bot, is_system=is_system, severity=severity)

        if signal is not None:
            # Reached nano on its own → mark it judged so the aggregate skips it.
            if channel_id is not None:
                await self._agg_mark_judged(
                    guild_id, channel_id, target.author_id, [str(target.id)])
            return await self._decide(
                target, signal, guild_id=guild_id, guild_name=guild_name,
                rules=rules, author_history=author_history,
                fetch_context=fetch_context, correlation_id=correlation_id,
                severity=severity, response_language=response_language,
                relation_fn=relation_fn, precedents_fn=precedents_fn,
            )

        # Stopped before nano → try the fragmented-harassment aggregate.
        if channel_id is not None:
            return await self._maybe_aggregate(
                target, guild_id=guild_id, channel_id=channel_id,
                guild_name=guild_name, rules=rules, author_history=author_history,
                fetch_context=fetch_context, correlation_id=correlation_id,
                severity=severity, response_language=response_language,
            )
        return None

    # -- Routing (steps 1–4, produce a Signal or None) ---------------------

    async def _route_message(
        self, content: str, *, is_bot: bool, is_system: bool, severity: int,
    ) -> Optional[Signal]:
        """Steps 1–4 for a single message. Returns the nano-routing Signal or None."""
        # Step 1 — pre-filter.
        if not pre_filter(content, is_bot=is_bot, is_system=is_system):
            return None
        # Step 2 — trivial allowlist.
        if est_trivial(content):
            return None
        return await self._route_semantic(content, severity)

    async def _route_semantic(self, content: str, severity: int) -> Optional[Signal]:
        """Steps 3–4 only (regex blocklist + embedding). Shared with aggregation.

        The aggregate path reuses exactly these two cheap steps on the
        concatenation, skipping the (single-message) pre-filter / trivial guards.
        """
        # Step 3 — regex blocklist.
        entry = self.blocklist.match(content)
        if entry is not None:
            return Signal(
                source=constants.SOURCE_REGEX,
                categorie=entry.categorie,
                score_confiance=constants.GRAVITE_TO_SCORE.get(
                    entry.gravite_indicative, 0.7),
            )
        # Step 4 — embedding (threshold scales with severity).
        if not await self.ensure_ready():
            return None  # graceful degradation: embeddings unavailable
        scored = await self.embeddings.score(content)
        if scored is None:
            return None
        score, categorie = scored
        threshold = constants.embedding_threshold_for(severity)
        if not EmbeddingEngine.passes_threshold(score, threshold):
            return None
        return Signal(
            source=constants.SOURCE_EMBEDDING,
            categorie=categorie,
            score_confiance=score,
        )

    # -- Decision (budget gate + verdict cache + nano) ---------------------

    async def _decide(
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
        severity: int,
        response_language: str,
        agregat_de: Optional[List[str]] = None,
        relation_fn: Optional[RelationFn] = None,
        precedents_fn: Optional[PrecedentsFn] = None,
    ) -> Optional[Decision]:
        """Turn a routing Signal into a Decision, spending at most one nano call.

        Order: a **free** verdict-cache probe (and single-flight join) first, so a
        cached qualification is served even when the guild is over budget; only a
        genuine miss consults the budget guard before spending a nano call.

        When ``relation_fn`` is supplied (session 5) the verdict cache and
        single-flight are bypassed entirely: the relation block (and especially
        ``reaction_cible``) is specific to this message, so its verdict must never
        be shared with, or reused by, another message of the same text.
        """
        key = self._verdict_key(guild_id, target.content)
        cacheable = relation_fn is None

        if cacheable:
            cached = self._verdict_cache.get(key)
            if cached is not MISS:
                return self._decision_from_qualif(cached, target, signal, agregat_de)

            pending = self._verdict_inflight.get(key)
            if pending is not None:
                qualif = await pending
                if qualif is None:
                    return None
                return self._decision_from_qualif(qualif, target, signal, agregat_de)

        # Session 7: server precedents. A guild that has taught the bot from its
        # own human rulings gets those matched against this message BEFORE the
        # (paid) decision call — cheaper and more consistent than replaying the
        # model. A near-identical human "non_sanctionnable" ruling stops here (no
        # model call at all, not even budget-gated); otherwise the strongest
        # matches are handed to nano/mini as trusted server data. The query
        # vector is provided lazily, so a guild with no precedents never pays an
        # embedding call for this.
        precedents_payload: List[dict] = []
        if precedents_fn is not None:
            async def _get_vector() -> Optional[List[float]]:
                return await self._message_vector(target.content)
            try:
                matches = await precedents_fn(_get_vector)
            except Exception as e:
                logger.debug("automod: precedents provider failed: %s", e)
                matches = []
            shortcut = ap.deterministic_shortcut(matches)
            if shortcut is not None:
                logger.info(
                    "automod stop_reason=precedent similarite=%.3f message_id=%s",
                    shortcut.similarite, target.id)
                return self._precedent_stop_decision(
                    target, signal, shortcut, agregat_de)
            precedents_payload = ap.to_prompt_payload(matches)

        # A real nano call is about to happen → consult the budget guard.
        if not await self._budget_allows(guild_id, signal, severity):
            self._budget_dropped += 1
            return None

        # Session 5: build the trusted relation block right before nano (so the
        # reaction-observation window is only paid on the nano path). A flagrant
        # regex hit skips the observation wait for an immediate verdict.
        relation: Optional[dict] = None
        if relation_fn is not None:
            try:
                relation = await relation_fn(self._should_observe_reaction(signal))
            except Exception as e:
                logger.debug("automod: relation provider failed: %s", e)
                relation = None

        # Session 6: free difficulty routing — an ``ambigu`` message (very short,
        # banter between regulars, laughter, grey-zone score) is judged by the
        # smarter, pricier mini with ×2 context; ``evident`` stays on nano.
        niveau = routing.difficulte(target.content, signal, relation, severity=severity)

        return await self._nano_call(
            key, target, signal, guild_id=guild_id, guild_name=guild_name,
            rules=rules, author_history=author_history, fetch_context=fetch_context,
            correlation_id=correlation_id, severity=severity,
            response_language=response_language, agregat_de=agregat_de,
            relation=relation, cacheable=cacheable, niveau=niveau,
            precedents=precedents_payload,
        )

    @staticmethod
    def _should_observe_reaction(signal: Signal) -> bool:
        """Whether to spend the target-reaction window before judging.

        Skipped for a flagrant regex hit (high indicative gravity — a death
        threat / doxxing): those warrant an immediate verdict, not a 20-second
        wait. Everything else observes the target first.
        """
        if signal.source == constants.SOURCE_REGEX \
                and signal.score_confiance >= constants.REACTION_SKIP_SCORE:
            return False
        return True

    async def _nano_call(
        self, key: str, target: TargetMessage, signal: Signal, *,
        guild_id: int, guild_name: str, rules: str,
        author_history: AuthorHistory, fetch_context: ContextFn,
        correlation_id: str, severity: int, response_language: str,
        agregat_de: Optional[List[str]],
        relation: Optional[dict] = None,
        cacheable: bool = True,
        niveau: str = constants.DIFFICULTE_EVIDENT,
        precedents: Optional[List[dict]] = None,
    ) -> Optional[Decision]:
        """Real decision call, wrapped in single-flight + cache-fill + budget count.

        ``cacheable=False`` (a relation-carrying message) skips both the cache
        fill and the single-flight bookkeeping — the verdict is message-specific.
        ``niveau`` picks the model (nano vs mini) and the budget weight (§6).
        """
        future: Optional["asyncio.Future"] = None
        if cacheable:
            loop = asyncio.get_event_loop()
            future = loop.create_future()
            future.add_done_callback(_retrieve_result)
            self._verdict_inflight[key] = future
        try:
            decision = await self._judge(
                target, signal, guild_id=guild_id, guild_name=guild_name,
                rules=rules, author_history=author_history,
                fetch_context=fetch_context, correlation_id=correlation_id,
                severity=severity, response_language=response_language,
                agregat_de=agregat_de, relation=relation, niveau=niveau,
                precedents=precedents,
            )
        except BaseException as exc:  # propagate to caller and any waiters
            if future is not None:
                self._verdict_inflight.pop(key, None)
                if not future.done():
                    future.set_exception(exc)
            raise
        else:
            qualif = self._qualif_from_decision(decision)
            if cacheable and qualif is not None:
                self._verdict_cache.set(key, qualif)
            # A mini (ambigu) call weighs ×4 in the daily budget (§6.4).
            weight = (constants.MINI_BUDGET_WEIGHT
                      if routing.is_ambigu(niveau) else 1)
            await self._budget_increment(guild_id, weight)
            self._budget_calls += 1
            if future is not None:
                self._verdict_inflight.pop(key, None)
                if not future.done():
                    future.set_result(qualif)
            return decision

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
        severity: int = constants.SEVERITY_DEFAULT,
        response_language: str = "English",
        agregat_de: Optional[List[str]] = None,
        relation: Optional[dict] = None,
        niveau: str = constants.DIFFICULTE_EVIDENT,
        precedents: Optional[List[dict]] = None,
    ) -> Decision:
        # Session 6: an ``ambigu`` message is routed to the smarter, pricier mini
        # (same v2 contract, ×2 context); ``evident`` stays on nano. The Decision
        # records which model decided it (``decideur``) so the module knows whether
        # a heavy sanction still needs the mini confirmation (§6.3).
        if routing.is_ambigu(niveau):
            model, call_type = constants.MINI_MODEL, constants.CALL_TYPE_DECISION_MINI
            max_tokens, decideur = constants.MINI_MAX_TOKENS, "mini"
        else:
            model, call_type = constants.NANO_MODEL, constants.CALL_TYPE_DECISION
            max_tokens, decideur = constants.NANO_MAX_TOKENS, "nano"
        chat_fn = self._make_chat_fn(
            guild_id, correlation_id, model=model, call_type=call_type,
            max_tokens=max_tokens)
        return await nano.juger(
            target,
            signal,
            guild_name=guild_name,
            rules=rules,
            history=author_history,
            chat_fn=chat_fn,
            fetch_context=fetch_context,
            severite=severity,
            response_language=response_language,
            agregat_de=agregat_de,
            relation=relation,
            precedents=precedents,
            contexte_initial=routing.contexte_initial_for(niveau),
            decideur=decideur,
        )

    # -- Server precedents (session 7) -------------------------------------

    async def _message_vector(self, content: str) -> Optional[List[float]]:
        """The normalised embedding of the current message, for precedent match.

        Reuses the vector captured while the funnel scored this message; a
        regex-routed message (never embedded) pays one embedding call here. Never
        raises — a failure just means no precedent matching for this message.
        """
        try:
            return await self.embeddings.embed_query(content)
        except Exception as e:  # noqa: BLE001 — precedents are best-effort
            logger.debug("automod: message embedding for precedents failed: %s", e)
            return None

    @staticmethod
    def _precedent_stop_decision(
        target: TargetMessage, signal: Signal,
        shortcut: "ap.PrecedentMatch",
        agregat_de: Optional[List[str]] = None,
    ) -> Decision:
        """A non-sanctionnable Decision produced by the precedent shortcut (§7.3).

        A human already ruled near-identical text ``non_sanctionnable`` on this
        server, so the funnel stops before any model call. The decision carries
        ``precedent_applique`` for the logs / alert card so the shortcut is
        observable; it is deliberately not verdict-cached (cheap to recompute and
        the underlying precedents can change).
        """
        return Decision(
            message_id=target.id,
            auteur_id=target.author_id,
            sanctionnable=False,
            actions=[],
            categorie=nano.normalize_categorie(signal.categorie),
            gravite="basse",
            raison="",
            explication="",
            confiance="high",
            signal_source=signal.source,
            score_detecteur=signal.score_confiance,
            a_reverifier=[],
            duree_heures=0,
            citation="",
            cible="aucune",
            agregat_de=[str(x) for x in agregat_de] if agregat_de else [],
            agregat_contenu=target.content if agregat_de else "",
            precedent_applique={
                "similarite": round(float(shortcut.similarite), 4),
                "message": (shortcut.precedent.message or "")[:200],
                "verdict": shortcut.precedent.verdict_humain,
            },
        )

    # -- Heavy-sanction confirmation (session 6.3) -------------------------

    async def confirm_heavy(
        self,
        target: TargetMessage,
        decision: Decision,
        *,
        guild_id: int,
        fetch_context: ContextFn,
        response_language: str = "English",
        correlation_id: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Binary mini senior review of a heavy nano decision (§6.3).

        Returns ``(confirme, motif)``. The call spends one mini chat (counted ×4
        in the budget guard, like an ambigu routing call) and is **fail-safe**: a
        failed / malformed response is a refusal, so a doubtful heavy sanction is
        capped and handed to a human rather than applied. Heavy sanctions are rare
        by construction, so this is not budget-gated — correctness over economy.
        """
        correlation_id = correlation_id or str(uuid.uuid4())
        chat_fn = self._make_chat_fn(
            guild_id, correlation_id,
            model=constants.MINI_MODEL,
            call_type=constants.CALL_TYPE_CONFIRM,
            max_tokens=constants.CONFIRM_MAX_TOKENS,
        )
        verdict_junior = {
            "categorie": decision.categorie,
            "gravite": decision.gravite,
            "citation": decision.citation,
        }
        result = await nano.confirmer(
            target, verdict_junior,
            chat_fn=chat_fn, fetch_context=fetch_context,
            response_language=response_language,
        )
        await self._budget_increment(guild_id, constants.MINI_BUDGET_WEIGHT)
        self._budget_calls += 1
        return bool(result.get("confirme", False)), str(result.get("motif", ""))

    # -- Situation feature — diffuse harassment (session 8) ----------------

    async def friction_probe(
        self, content: str, *, severity: int = constants.SEVERITY_DEFAULT,
        is_bot: bool = False, is_system: bool = False,
    ) -> Optional[float]:
        """Toxicity score of a message for the diffuse-harassment friction machine.

        Runs the cheap funnel gates (pre-filter, trivial allowlist) and returns
        the embedding cosine score **even below** the nano routing threshold —
        that sub-threshold band is exactly the signal the funnel discards today
        and the friction machine consumes (§8.1). A flagrant regex hit returns
        ``None`` (that is a real detection the content feature already owns, not
        diffuse friction). The embedding score is served from the shared score
        cache, so when the content funnel already scored this message it costs
        **zero** extra call. Never raises — a failure just means no friction.
        """
        severity = constants.clamp_severity(severity)
        try:
            if not pre_filter(content, is_bot=is_bot, is_system=is_system):
                return None
            if est_trivial(content):
                return None
            # A flagrant explicit hit is not diffuse friction — the content funnel
            # owns it (it routes straight to a decision, skipping embedding).
            if self.blocklist.match(content) is not None:
                return None
            if not await self.ensure_ready():
                return None
            scored = await self.embeddings.score(content)
        except Exception as e:  # noqa: BLE001 — friction is best-effort
            logger.debug("automod: friction probe failed: %s", e)
            return None
        if scored is None:
            return None
        return float(scored[0])

    async def analyze_situation(
        self,
        cible_presumee: str,
        sequence: List[dict],
        *,
        guild_id: int,
        response_language: str = "English",
        correlation_id: Optional[str] = None,
    ) -> "asit.SituationVerdict":
        """Judge a diffuse-harassment SEQUENCE on ``mini`` (session 8, §8.2).

        The trigger is rare by construction (a friction threshold crossing), so
        — like the heavy-sanction confirmation — this spends one mini call that is
        **counted ×4** in the per-guild budget guard but is **not** budget-gated
        (correctness over economy). The sequence is collected by the module
        (Discord I/O); this method only builds the prompt, calls the model and
        parses it — the pipeline still only decides.
        """
        if not sequence:
            return asit.SituationVerdict(situation=constants.SITUATION_RIEN)
        correlation_id = correlation_id or str(uuid.uuid4())
        chat_fn = self._make_chat_fn(
            guild_id, correlation_id,
            model=constants.MINI_MODEL,
            call_type=constants.CALL_TYPE_SITUATION,
            max_tokens=constants.SITUATION_MAX_TOKENS,
        )
        verdict = await asit.analyser(
            cible_presumee, sequence,
            chat_fn=chat_fn, response_language=response_language,
        )
        await self._budget_increment(guild_id, constants.MINI_BUDGET_WEIGHT)
        self._budget_calls += 1
        return verdict

    # -- Verdict cache helpers ---------------------------------------------

    def _verdict_key(self, guild_id: int, content: str) -> str:
        """Per-guild cache key over the collapsed/normalised message text.

        Collapsing runs before hashing so a padded/re-cased copypasta shares one
        entry; the guild id keeps guidance/severity differences from colliding.
        """
        collapsed = collapse_repeats(content) or content
        raw = f"{guild_id}\x00{collapsed}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _qualif_from_decision(decision) -> Optional[dict]:
        """Extract the cacheable qualification from a Decision (None if not one)."""
        if not isinstance(decision, Decision):
            return None
        return {
            "sanctionnable": decision.sanctionnable,
            "categorie": decision.categorie,
            "gravite": decision.gravite,
            "raison": decision.raison,
            "explication": decision.explication,
            "confiance": decision.confiance,
            "citation": decision.citation,
            "cible": decision.cible,
            "rejet_grounding": decision.rejet_grounding,
            "decideur": decision.decideur,
        }

    @staticmethod
    def _decision_from_qualif(
        q: dict, target: TargetMessage, signal: Signal,
        agregat_de: Optional[List[str]] = None,
    ) -> Decision:
        """Rebuild a Decision from a cached qualification + this message/signal.

        ``a_reverifier`` is deliberately NOT restored (it references context
        message ids specific to the original message); a cache hit yields the same
        qualification, not the same neighbouring-message flags.
        """
        return Decision(
            message_id=target.id,
            auteur_id=target.author_id,
            sanctionnable=q["sanctionnable"],
            actions=[],
            categorie=q["categorie"] or nano.normalize_categorie(signal.categorie),
            gravite=q["gravite"],
            raison=q["raison"],
            explication=q["explication"],
            confiance=q["confiance"],
            signal_source=signal.source,
            score_detecteur=signal.score_confiance,
            a_reverifier=[],
            duree_heures=0,
            citation=q["citation"],
            cible=q["cible"],
            rejet_grounding=q["rejet_grounding"],
            agregat_de=[str(x) for x in agregat_de] if agregat_de else [],
            agregat_contenu=target.content if agregat_de else "",
            decideur=q.get("decideur", "nano"),
        )

    # -- Budget guard ------------------------------------------------------

    def _redis(self):
        return getattr(self.bot, "redis", None)

    @staticmethod
    def _utc_day() -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d")

    async def _budget_used(self, guild_id: int) -> int:
        r = self._redis()
        if r is None:
            return 0
        try:
            v = await r.get(f"automod:budget:{guild_id}:{self._utc_day()}")
            return int(v) if v else 0
        except Exception:
            return 0

    async def _budget_cap(self, guild_id: int) -> int:
        """Soft cap for a guild — a Redis per-guild override, else the constant."""
        r = self._redis()
        if r is not None:
            try:
                v = await r.get(f"automod:budget:cap:{guild_id}")
                if v is not None and str(v) != "":
                    return int(v)
            except Exception:
                pass
        return constants.NANO_DAILY_SOFT_CAP

    async def _budget_increment(self, guild_id: int, weight: int = 1) -> None:
        r = self._redis()
        if r is None:
            return
        try:
            key = f"automod:budget:{guild_id}:{self._utc_day()}"
            # A mini call (ambigu routing / confirmation) weighs ×4 (§6.4).
            if weight and weight != 1 and hasattr(r, "incrby"):
                await r.incrby(key, int(weight))
            else:
                for _ in range(max(1, int(weight))):
                    await r.incr(key)
            await r.expire(key, constants.BUDGET_KEY_TTL_SECONDS)
        except Exception:
            pass

    async def _budget_allows(self, guild_id: int, signal: Signal, severity: int) -> bool:
        """Whether a nano call may be spent for this message right now.

        Without Redis the guard is inert (always allow). Under the soft cap:
        allow. Over it, degrade — only a regex hit or an embedding score
        comfortably above threshold gets a call; everything else is dropped (no
        hard cut-off, the funnel simply loses sensitivity).
        """
        r = self._redis()
        if r is None:
            return True
        cap = await self._budget_cap(guild_id)
        if cap <= 0:
            return True  # 0 / negative = unlimited
        used = await self._budget_used(guild_id)
        if used < cap:
            return True

        # Over the soft cap → degraded mode.
        self._mark_degraded(guild_id)
        if signal.source == constants.SOURCE_REGEX:
            return True
        if signal.source == constants.SOURCE_EMBEDDING:
            threshold = constants.embedding_threshold_for(severity)
            if signal.score_confiance >= threshold + constants.NANO_DEGRADED_SCORE_MARGIN:
                return True
        return False

    def _mark_degraded(self, guild_id: int) -> None:
        day = self._utc_day()
        self._budget_degraded.add((guild_id, day))
        if (guild_id, day) not in self._budget_notified:
            self._budget_notified.add((guild_id, day))
            self._budget_notice_pending.add(guild_id)

    def pop_budget_notice(self, guild_id: int) -> bool:
        """True once per (guild, day) when it first crosses into degraded mode.

        The module calls this and, on True, posts the one-off "AI budget reached,
        reduced sensitivity" card to the alert channel.
        """
        if guild_id in self._budget_notice_pending:
            self._budget_notice_pending.discard(guild_id)
            return True
        return False

    # -- Author aggregation (fragmented harassment) ------------------------

    @staticmethod
    def _agg_buf_key(guild_id: int, channel_id: int, author_id) -> str:
        return f"automod:agg:buf:{guild_id}:{channel_id}:{author_id}"

    @staticmethod
    def _agg_judged_key(guild_id: int, channel_id: int, author_id) -> str:
        return f"automod:agg:judged:{guild_id}:{channel_id}:{author_id}"

    async def _agg_push(self, guild_id: int, channel_id: int,
                        target: TargetMessage) -> None:
        r = self._redis()
        if r is None or not constants.AGGREGATION_ENABLED:
            return
        key = self._agg_buf_key(guild_id, channel_id, target.author_id)
        entry = json.dumps({"id": str(target.id), "c": target.content,
                            "ts": time.time()})
        try:
            await r.lpush(key, entry)
            await r.ltrim(key, 0, constants.AGGREGATION_MAX_MESSAGES - 1)
            await r.expire(key, constants.AGGREGATION_WINDOW_SECONDS)
        except Exception:
            pass

    async def _agg_recent(self, guild_id: int, channel_id: int,
                          author_id) -> List[dict]:
        """The author's buffered messages within the window, oldest → newest."""
        r = self._redis()
        if r is None:
            return []
        key = self._agg_buf_key(guild_id, channel_id, author_id)
        try:
            raw = await r.lrange(key, 0, constants.AGGREGATION_MAX_MESSAGES - 1)
        except Exception:
            return []
        now = time.time()
        items: List[dict] = []
        for s in raw or []:
            try:
                o = json.loads(s)
            except Exception:
                continue
            if now - float(o.get("ts", 0)) <= constants.AGGREGATION_WINDOW_SECONDS:
                items.append(o)
        items.reverse()  # LPUSH stores newest-first → oldest-first
        return items

    async def _agg_judged(self, guild_id: int, channel_id: int,
                          author_id) -> Set[str]:
        r = self._redis()
        if r is None:
            return set()
        try:
            members = await r.smembers(
                self._agg_judged_key(guild_id, channel_id, author_id))
        except Exception:
            return set()
        return {m.decode() if isinstance(m, bytes) else str(m) for m in (members or [])}

    async def _agg_mark_judged(self, guild_id: int, channel_id: int,
                               author_id, ids: List[str]) -> None:
        r = self._redis()
        if r is None or not ids:
            return
        key = self._agg_judged_key(guild_id, channel_id, author_id)
        try:
            await r.sadd(key, *[str(i) for i in ids])
            await r.expire(key, constants.AGGREGATION_WINDOW_SECONDS)
        except Exception:
            pass

    async def _maybe_aggregate(
        self,
        target: TargetMessage,
        *,
        guild_id: int,
        channel_id: int,
        guild_name: str,
        rules: str,
        author_history: AuthorHistory,
        fetch_context: ContextFn,
        correlation_id: str,
        severity: int,
        response_language: str,
    ) -> Optional[Decision]:
        """Re-route the author's recent fragments as one concatenated message.

        Only fires when the single message already stopped before nano, the
        buffer holds ≥ ``AGGREGATION_MIN_MESSAGES`` fragments within the window,
        none of which was already judged individually (anti double-jeopardy), and
        the concatenation routes through the cheap steps (blocklist / embedding).
        """
        if not constants.AGGREGATION_ENABLED or self._redis() is None:
            return None
        author_id = target.author_id
        items = await self._agg_recent(guild_id, channel_id, author_id)
        if len(items) < constants.AGGREGATION_MIN_MESSAGES:
            return None
        frag_ids = [str(o.get("id")) for o in items]

        judged = await self._agg_judged(guild_id, channel_id, author_id)
        if judged & set(frag_ids):
            return None  # a fragment already reached nano on its own → skip

        concat = "\n".join(str(o.get("c", "")) for o in items)
        signal = await self._route_semantic(concat, severity)
        if signal is None:
            return None

        # The aggregate is about to spend a nano call → don't let the same
        # fragments trigger it again.
        await self._agg_mark_judged(guild_id, channel_id, author_id, frag_ids)

        agg_target = TargetMessage(
            id=str(target.id), author_id=author_id, content=concat)
        return await self._decide(
            agg_target, signal, guild_id=guild_id, guild_name=guild_name,
            rules=rules, author_history=author_history, fetch_context=fetch_context,
            correlation_id=correlation_id, severity=severity,
            response_language=response_language, agregat_de=frag_ids,
        )


def get_engine(bot) -> AutomodEngine:
    """Return the shared per-bot engine, creating it on first access."""
    engine = getattr(bot, "_automod_engine", None)
    if engine is None:
        engine = AutomodEngine(bot)
        bot._automod_engine = engine
    return engine
