"""
Tunable constants for the automod pipeline.

All of these are starting values. ``SEUIL_EMBEDDING`` in particular must be
calibrated against real server traffic — see ``docs/AUTOMOD.md``.
"""

# --- Embedding (step 4) -----------------------------------------------------

# Cosine-similarity threshold above which a message is routed to nano.
# Below it, the message is dropped (no decision).
SEUIL_EMBEDDING: float = 0.45

# Per-guild severity dial (1 = lenient, 5 = strict). It scales BOTH detection
# sensitivity (the embedding threshold below) and how harsh nano is told to be.
SEVERITY_DEFAULT: int = 3
SEVERITY_MIN: int = 1
SEVERITY_MAX: int = 5
# Lower threshold = more messages reach nano. Higher severity → lower threshold.
SEVERITY_EMBEDDING_THRESHOLDS = {
    1: 0.62,
    2: 0.54,
    3: 0.47,
    4: 0.41,
    5: 0.35,
}


def clamp_severity(value) -> int:
    """Coerce an arbitrary value into the 1–5 severity range (default 3)."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return SEVERITY_DEFAULT
    return max(SEVERITY_MIN, min(SEVERITY_MAX, v))


def embedding_threshold_for(severity: int) -> float:
    """Embedding routing threshold for a given severity level."""
    return SEVERITY_EMBEDDING_THRESHOLDS.get(clamp_severity(severity), SEUIL_EMBEDDING)

# Embedding model used for both the references and incoming messages.
EMBEDDING_MODEL: str = "text-embedding-3-small"


# --- Embedding score cache (step 4 de-duplication) --------------------------
#
# The reference vectors are embedded once per process and never change, so the
# score of a given message is deterministic for the process lifetime. Caching it
# collapses repeated/identical messages (raid spam, copypasta floods) onto a
# single embedding call, and in-flight identical requests are additionally
# coalesced (single-flight) so a burst of N identical messages costs one call,
# not N. Purely an optimization — it can never change a decision, only avoid a
# redundant embedding call. See docs/AUTOMOD.md §5 (Volume note).
EMBED_CACHE_ENABLED: bool = True
# Hard cap on cached message scores (LRU eviction past this). ~4k short strings
# is a few hundred KB — negligible, and comfortably covers a busy server's
# working set of distinct messages.
EMBED_CACHE_MAX_ENTRIES: int = 4096
# Defensive freshness bound; correctness does not depend on it (the score is
# stable for the process lifetime). ``0`` disables expiry.
EMBED_CACHE_TTL_SECONDS: float = 1800.0  # 30 minutes


# --- Pre-filter (step 1) ----------------------------------------------------
#
# A message longer than this is truncated to its de-spammed (collapsed) form
# before embedding: a wall of text (or a repetition flood) never needs its full
# length embedded — the toxic phrase, if any, survives collapsing. Keeps the
# embed input bounded and the embedding-cache key stable across paddings.
PREFILTRE_MAX_CHARS: int = 1500


# --- nano (step 5) ----------------------------------------------------------

# nano model + sampling. This is classification, not generation: temperature 0
# removes needless randomness from what should be a deterministic verdict.
NANO_MODEL: str = "gpt-4.1-nano"
NANO_TEMPERATURE: float = 0.0
# The v2 verdict contract is lean (no free-form ladder reasoning), so 300 tokens
# is comfortably enough for the JSON object.
NANO_MAX_TOKENS: int = 300

# Context window sizing (messages preceding the target, same channel).
CONTEXTE_INITIAL: int = 12     # first call
CONTEXTE_MAX: int = 40         # absolute ceiling (cost + injection surface)
ROUNDS_MAX: int = 3            # max nano calls per message (1 initial + 2 re-asks)


# --- Verdict cache (step 5 de-duplication, session 4) -----------------------
#
# Symmetric to the embedding score cache but on nano *qualifications*. Same text
# in the same guild yields the same qualification (sanctionnable / categorie /
# gravite / citation / cible), so a copypasta raid that reaches nano costs ONE
# chat call instead of N. Only the qualification is cached — the barème (cran +
# recidivism) is recomputed every time, since the author's history differs. The
# cache is per-guild (guidance / severity differ per guild), short-lived, LRU
# bounded and single-flighted, exactly like the embedding cache. See
# docs/AUTOMOD.md §5.1.
VERDICT_CACHE_ENABLED: bool = True
VERDICT_CACHE_MAX_ENTRIES: int = 2048
VERDICT_CACHE_TTL_SECONDS: float = 600.0  # 10 minutes (guidance can change)


# --- Author aggregation window (anti-fragmentation, session 4) --------------
#
# Fragmented harassment ("je vais" / "te" / "retrouver" in three messages)
# individually clears the funnel — each fragment is empty on its own. A short
# sliding Redis buffer per (guild, channel, author) lets the pipeline judge the
# CONCATENATION when a message stops before nano and the buffer holds enough
# recent fragments. The concat is only routed through the cheap steps
# (blocklist + embedding); nano runs only if the combined text actually routes.
AGGREGATION_ENABLED: bool = True
AGGREGATION_WINDOW_SECONDS: int = 45
AGGREGATION_MAX_MESSAGES: int = 6
AGGREGATION_MIN_MESSAGES: int = 2


# --- Per-guild budget guard (session 4) -------------------------------------
#
# A safety net on the bill, independent of the gateway quotas. A Redis counter
# per (guild, UTC day) is bumped on every real nano call. Past the soft cap the
# funnel keeps running (embeddings are cents) but nano is reserved for the
# flagrant cases only — a regex hit, or an embedding score comfortably above the
# routing threshold (+ ``NANO_DEGRADED_SCORE_MARGIN``). No hard cut-off: the
# system degrades, it never goes blind. See docs/AUTOMOD.md §5.2.
NANO_DAILY_SOFT_CAP: int = 300
NANO_DEGRADED_SCORE_MARGIN: float = 0.10
# TTL on the daily counter key (48h so a key set just before UTC midnight still
# rotates cleanly), mirroring the gateway quota counters.
BUDGET_KEY_TTL_SECONDS: int = 172800


# --- Relationship graph & target reaction (session 5) -----------------------
#
# Two facts the raw text never carries: WHO talks to WHOM (familiarity) and HOW
# the target reacted. Per-pair counters are fed passively by existing listeners
# (reply / mention / positive reaction, ~0 cost), stored in Redis with a 60-day
# TTL, and decayed on read (half-life 30 days). A short post-message window then
# observes the target and classifies its reaction. Both are injected into nano's
# payload as TRUSTED server data — not user text — so nano can tell humour from
# genuine intent to harm. See docs/AUTOMOD.md §2ter and docs/AUTOMOD_V2_PLAN.md
# (Session 5). Familiarity only ever ATTENUATES a verdict (never aggravates).
RELATION_ENABLED: bool = True
# 60-day TTL on the per-pair hash — no global graph to maintain, it self-expires.
RELATION_TTL_SECONDS: int = 60 * 86400
# Score half-life: a pair that stops interacting fades over ~a month.
RELATION_DECAY_HALFLIFE_DAYS: float = 30.0
# A reply back within this window counts as a mutual (reciprocal) exchange.
RELATION_MUTUAL_WINDOW_SECONDS: int = 300  # 5 minutes
# "haute" familiarity additionally requires the pair to be at least this old
# (a burst of interactions on day one is not yet a real relationship).
RELATION_MIN_AGE_DAYS_FOR_HAUTE: float = 7.0
# Weighted, decayed score thresholds (score = interactions + 2·mutual + 3·positive).
RELATION_SCORE_HAUTE: float = 40.0
RELATION_SCORE_MOYENNE: float = 12.0
RELATION_SCORE_FAIBLE: float = 3.0

# Familiarity levels (derived from the score above).
FAMILIARITE_HAUTE: str = "haute"
FAMILIARITE_MOYENNE: str = "moyenne"
FAMILIARITE_FAIBLE: str = "faible"
FAMILIARITE_AUCUNE: str = "aucune"

# Target-reaction observation: defer the verdict this long (asyncio, cancellable)
# to see how the target reacted before judging. Invisible latency, high precision.
REACTION_WAIT_SECONDS: int = 20
# Skip the wait for a flagrant regex hit (death threat / doxxing): a high
# indicative gravity means an immediate verdict, no 20-second observation.
REACTION_SKIP_SCORE: float = 0.85

# reaction_cible classifications (how the target reacted in the window).
REACTION_BANTER: str = "banter_reciproque"      # laughed along
REACTION_CONFLIT: str = "conflit_reciproque"     # replied on the same aggressive tone
REACTION_DETRESSE: str = "detresse_possible"     # deleted own messages / left the channel
REACTION_AUCUNE: str = "aucune"                  # nothing observable

# Categories where familiarity / banter is IGNORED at gravite haute+ — friends or
# not, this goes (mirrors the barème's CATEGORIES_SENSIBLES intent).
CATEGORIES_RELATION_IGNOREE = frozenset({
    "haine_discrimination", "incitation_automutilation", "harcelement_sexuel",
})


# --- Gateway call types -----------------------------------------------------

# Quota-gated chat call for a moderation decision (per guild).
CALL_TYPE_DECISION: str = "automod_decision"
# Quota-gated chat call for validating a server's rules text.
CALL_TYPE_RULES_CHECK: str = "automod_rules_check"
# Embedding call (not quota-gated, see API_GATEWAY.md).
CALL_TYPE_EMBED: str = "automod_embed"


# --- Signal sources ---------------------------------------------------------

SOURCE_REGEX: str = "regex"
SOURCE_EMBEDDING: str = "embedding"
SOURCE_NANO_FLAG: str = "signalé_par_nano"


# --- Verdict categories (canonical FR set, v2 contract) ---------------------
#
# nano's v2 contract emits one of these canonical French categories. The legacy
# detector/blocklist categories (``insultes``, ``menaces``, ``contenu_sexuel``…)
# and any old values stored on existing cases are folded onto this set via
# ``nano.CATEGORIE_ALIASES`` / ``nano.normalize_categorie`` so nothing needs a
# manual data migration.
CATEGORIES = (
    "insulte",
    "menace",
    "harcelement",
    "harcelement_sexuel",
    "haine_discrimination",
    "incitation_automutilation",
    "doxxing",
    "arnaque_scam",
    "violation_indications",
)

# Categories that structurally require a victim: an insult/threat/harassment
# with no target (or aimed at the author themselves) is not a real violation.
CATEGORIES_AVEC_VICTIME = frozenset({
    "insulte", "menace", "harcelement", "harcelement_sexuel",
})


# --- Gravity → indicative confidence map (regex signals) --------------------

# A regex match carries an *indicative* gravity, mapped to a confidence value
# passed to nano (purely indicative — nano remains the sole decider).
GRAVITE_TO_SCORE = {
    "basse": 0.55,
    "moyenne": 0.70,
    "haute": 0.85,
}
