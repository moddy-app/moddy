# Automod v2 — Session 7: Server precedents (jurisprudence RAG)

**Date:** 2026-07-13
**Branch:** `claude/awesome-mayer-dsxxmr` → PR into `AUTOMOD_V2`

## What was done

Gave the automod a memory of **each server's own culture**: every human ruling it
already produces (accepted/refused appeals, shadow `✅`/`❌` clicks) is stored as a
**precedent** — the message, the final human verdict, and the embedding the funnel
already computed. Before a decision call the current message is matched (cosine)
against the guild's precedents; strong matches are injected into nano/mini as
trusted server data, and a near-identical human "not sanctionable" ruling
short-circuits the model call entirely. No fine-tuning, no new model.

The pipeline still only **decides**: the ranking + shortcut logic is pure
(`automod/precedents.py`), the message vector is produced by the engine (reused
from the funnel's scoring), and all I/O (storage, one-off embedding of a recorded
precedent, the guild cache) lives in `services/precedent_service.py`, injected via
a lazy `precedents_fn` callback — the same shape as `fetch_context` / `relation_fn`.

## Files modified / added

- **`automod/precedents.py`** (new) — pure matcher: `Precedent`/`PrecedentMatch`,
  `cosine`, `match` (top-K ≥ 0.80), `deterministic_shortcut` (≥ 0.97 +
  `non_sanctionnable`), `to_prompt_payload`.
- **`automod/constants.py`** — `PRECEDENT_*` constants (thresholds, cap 500, cache
  TTL, verdict/source labels).
- **`automod/embeddings.py`** — capture the primary message vector while scoring
  (small bounded cache) + `embed_query(content)` reusing it (0 extra call on the
  embedding path; one call on the regex path only when precedents exist).
- **`automod/engine.py`** — `precedents_fn` on `analyze`/`_decide`; lazy
  `_message_vector`; shortcut → `_precedent_stop_decision` (`stop_reason=precedent`,
  no model call); injection threaded to `nano.juger`.
- **`automod/nano.py`** — `PRECEDENTS_PROMPT_BLOCK` (trusted, never overrides
  gravite haute+), `precedents_serveur` in the payload (fenced), block shown only
  when precedents present.
- **`automod/schemas.py`** — `Decision.precedent_applique`.
- **`db/base.py`** + **`db/repositories/precedents.py`** (new) — `automod_precedents`
  table (embedding as float32 BYTEA, no pgvector), repo (add + evict, list/unpack,
  count, last_at, delete, pack/unpack helpers). Registered in `ModdyDatabase`.
- **`services/precedent_service.py`** (new, `bot.precedents`) — record (embed once
  via gateway + store), per-guild cache (TTL 300 s), lazy provider, invalidate.
- **`services/appeal_service.py`** — feed a precedent on accept (`non_sanctionnable`)
  / refuse (`sanctionnable`) from the case's automod evidence extract.
- **`utils/automod_shadow_views.py`** — feed a precedent on the ❌/✅ shadow buttons.
- **`modules/automod.py`** — `make_precedents_provider`, wired into `analyze`.
- **`modules/configs/automod_config.py`** + **`automod_precedents_view.py`** (new) +
  **`cogs/config.py`** — `/config` Précédents section (count + last) + paginated
  browser with per-item deletion.
- **`locales/fr.json`**, **`locales/en-US.json`** — `config.precedents.*`,
  `section_precedents`, `buttons.view_precedents`.
- **`automod/eval/run.py`** — `GoldenCase.precedents` (inert in `--replay`, wrapped
  as `precedents_fn` in `--live`); golden +4 (`gs-0300..0303`); fixtures + baseline.
- **`bot.py`** — instantiate `PrecedentService`.
- **`tests/automod/test_precedents.py`** (new, 15 tests).
- Docs: `AUTOMOD.md` (§2quinquies + tunables), `CLAUDE.md` (structure),
  `AUTOMOD_V2_PLAN.md` (tracking table + session-7 journal).

## Decisions

- **No pgvector.** Vectors stored float32-packed in BYTEA; cosine is a Python dot
  product over the guild's ≤500 precedents (loaded once per 300 s window) — zero
  extension dependency, consistent with the numpy-free package.
- **Zero extra call at judgment time.** The message vector is captured during
  embedding scoring and reused; the provider calls `get_vector` only when the
  guild actually has precedents, so a server with no jurisprudence pays nothing.
  Recording a precedent costs one embed (rare, human-triggered).
- **One-directional shortcut.** Only a `non_sanctionnable` precedent ≥ 0.97 stops
  before the model (economy + consistency); a `sanctionnable` precedent still goes
  through the model because the barème/recidivism must be recomputed per author.
- **Gravity guardrail.** Precedents never override genuinely haute/critique content
  (system block + golden case `gs-0303`).
- **Precedent text is fenced** like any untrusted `contenu`, even though it is
  presented as trusted server data — it is still user-written (injection surface).

## Verification

- `pytest tests/automod/` → **281 passed** (266 + 15 new).
- `python -m automod.eval.run` (replay) → precision/recall 1.0, exit 0; baseline
  regenerated with the 4 new precedent cases.

## Known issues / follow-ups

- **Replay is fixture-driven**, so precedents are exercised for real only under
  `--live` (mirrors the S5 relation approach). The offline gate still protects the
  deterministic layers.
- Verdict cache: a precedent-influenced verdict is cacheable per (guild, text) and
  can be up to `VERDICT_CACHE_TTL_SECONDS` (600 s) stale after a precedent is
  added/deleted — acceptable and bounded; not bypassed for the dedup benefit.
- Session 8 (`situation` feature) is the remaining piece of the v2 plan.
