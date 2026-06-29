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


# --- nano (step 5) ----------------------------------------------------------

# nano model + sampling. Low temperature for stable decisions.
NANO_MODEL: str = "gpt-4.1-nano"
NANO_TEMPERATURE: float = 0.2
NANO_MAX_TOKENS: int = 400

# Context window sizing (messages preceding the target, same channel).
CONTEXTE_INITIAL: int = 12     # first call
CONTEXTE_MAX: int = 40         # absolute ceiling (cost + injection surface)
ROUNDS_MAX: int = 3            # max nano calls per message (1 initial + 2 re-asks)


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


# --- Gravity → indicative confidence map (regex signals) --------------------

# A regex match carries an *indicative* gravity, mapped to a confidence value
# passed to nano (purely indicative — nano remains the sole decider).
GRAVITE_TO_SCORE = {
    "basse": 0.55,
    "moyenne": 0.70,
    "haute": 0.85,
}
