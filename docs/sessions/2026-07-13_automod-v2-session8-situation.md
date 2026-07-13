# 2026-07-13 — Automod v2 (session 8): `situation` — diffuse harassment (shadow)

Part of the multi-session **automod v2** effort (see
`docs/AUTOMOD_V2_PLAN.md`, section « SESSION 8 »). All work is on the
`AUTOMOD_V2` branch (this session's feature branch was rebased onto it).

## What was done

Added the **first additional automod feature** beyond `content`: `situation`,
which detects what no per-message verdict can ever see — fifteen individually
anodyne messages that, together, are **sustained harassment or dogpiling**. It
is shipped in **forced shadow mode** (v1): it never applies a sanction, it only
posts a SIMULATION card with annotation buttons that feed a future "situations"
golden set.

### 1. Friction state machine + analyst (`automod/situation.py`, new — pure + Redis)

- **Pure arithmetic** (table-tested): `decayed(value, last_ts, now)` (friction
  decays ×0.5 every 20 min, half-life) and `crosses(pair, agg)` (pair signal
  wins over the dogpiling aggregate).
- **Analyst contract** (pure): `build_situation_system_prompt` /
  `build_situation_user_payload` (every `contenu` wrapped in the nonce DATA
  fence) / `parse_situation` (**fail-safe** — malformed or unknown `situation`
  ⇒ `rien`; participants filtered to valid roles) / `analyser(...)` (injected
  `chat_fn`; `rien` on an empty sequence or a failed call).
- **`FrictionStore`** (Redis): directed pair key
  `friction:{g}:{c}:{author}->{target}`, per-target aggregate
  `friction:agg:{g}:{c}:{target}` (dogpiling — sum of incoming friction, no
  Redis SCAN), and a `friction:cd:…` cooldown. Score decayed on **read and
  write**, TTL 2 h. **Inert without Redis** — no Redis ⇒ no situation detection,
  never a sanction.

### 2. Engine wiring (`automod/engine.py`)

- `friction_probe(content, severity, …)` — replays the free funnel gates
  (pre-filter / trivial) then returns the embedding score **even below the nano
  routing threshold** (the sub-threshold band the funnel discards today),
  reusing the score cache so it costs **zero** extra call when `content` already
  scored the message. Returns `None` on a blocklist hit (flagrant = the content
  feature's job) or a trivial message.
- `analyze_situation(cible, sequence, …)` — one **mini** call
  (`automod_situation`), **counted ×4** in the per-guild budget guard (like the
  heavy-sanction confirmation) but **not** budget-gated. `rien` + no call for an
  empty sequence.

### 3. Module feature (`modules/automod.py`)

- `SituationFeature` (shadow-forced; `process` always returns `[]`). Fed from
  two sources: **feed #1** the sub-threshold score `[0.25, threshold)` with an
  identifiable target (in `process`); **feed #2** a non-sanctionnable
  `cible="membre"` content verdict (from the `on_message` loop).
- Trigger: pair ≥ 1.5 or aggregate ≥ 2.5 → collect the 45-min sequence (cap 30)
  → `analyze_situation` → if `!= "rien"`, record an eval candidate
  (`source="situation"`) and post the situation SIMULATION card. A cooldown is
  set **before** the call so a heated thread doesn't fire an analysis per
  message.

### 4. UI / plumbing

- `utils/automod_situation_views.py` (new) — `render_situation_card` (Components
  V2: pattern, gravity, presumed target, summary, participants + roles, key-
  message jump links, SIMULATION badge) reusing the persistent
  `ShadowAnnotateButton`.
- `utils/automod_shadow_views.py` — a `_render_for` dispatch (situation vs
  sanction card) and `_feed_precedent` **skips** `source="situation"` (a
  multi-message pattern is not a single-message precedent).
- `modules/configs/automod_config.py` — a 4th **Situations** option in the
  options select; `features.situation` in the default + `_deep_default`.
- `db/base.py` — seeds the `automod_situation` quota type (guild + global).
- i18n `modules.automod.situation.*` + `config.situation_{label,desc,active}`
  (fr + en-US).

## Files modified / added

- **new:** `automod/situation.py`, `utils/automod_situation_views.py`,
  `tests/automod/test_situation.py`.
- **changed:** `automod/constants.py`, `automod/engine.py`,
  `modules/automod.py`, `modules/configs/automod_config.py`,
  `utils/automod_shadow_views.py`, `db/base.py`, `locales/fr.json`,
  `locales/en-US.json`, `docs/AUTOMOD.md`, `docs/AUTOMOD_V2_PLAN.md`,
  `CLAUDE.md`.

## Decisions

- **Separation respected.** `automod/situation.py` decides (pure arithmetic +
  analyst contract + Redis store, exactly like S4/S5); every Discord I/O
  (identify the target, collect the sequence, post the card) lives in the
  module; the mini call lives in the engine (`analyze_situation`, like
  `confirm_heavy`).
- **Forced shadow, no sanction in v1** — even with `dry_run` off. The barème's
  `("harcelement", gravite)` floor is already there for the future activation
  once a "situations" golden set exists.
- **≈ 0 cost to feed.** `friction_probe` reuses the cached embedding score; a
  blocklist/trivial message pays no embed. Only the (rare) threshold crossing
  pays one mini call.
- **Cooldown before the call** + a dedicated aggregate counter for dogpiling (no
  Redis SCAN).
- **Situation annotation ≠ precedent** — a pattern is not a single-message
  ruling.

## Tests / verification

- `tests/automod/test_situation.py` — **33** cases (decay, thresholds, parse
  fail-safe, fenced prompt, `FrictionStore` pair/aggregate/dogpiling/decay/
  cooldown, `friction_probe` sub-threshold + `None`, `analyze_situation` mini
  ×4). Full suite **314 passed**.
- `python -m automod.eval.run --replay` — precision/recall **1.0/1.0**, no
  false-positive regression, no baseline change.

## Known issues / follow-ups

- **Activation of situation sanctioning** is deliberately out of scope: it waits
  on a "situations" golden set built from these annotations, then wiring the
  `("harcelement", gravite)` barème floor into an applied `Decision`.
- Per-pair familiarity (S5) could be injected into the analyst payload to damp
  banter false positives — left as a refinement once real annotations arrive.
