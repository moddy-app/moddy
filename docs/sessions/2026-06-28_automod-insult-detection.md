# Session — 2026-06-28 — Automod (AI insult / problematic-message detection)

## What was done

Built the first half of the new **Automod** system: AI-assisted detection and
moderation of insults / problematic messages, designed to be **scalable** so
anti-link / anti-invite / anti-spam / anti-raid can be grafted on later without
reworking the core.

### 1. Detection pipeline — `automod/` (new root package)

Pure, Discord-agnostic, dependency-free (no numpy / unidecode). Decides only —
applies nothing.

- `schemas.py` — `TargetMessage`, `ContextMessage`, `AuthorHistory`, `Signal`,
  `BlocklistEntry`, `Decision`.
- `constants.py` — thresholds, model ids, context sizing, call types.
- `normalize.py` — accent folding + leetspeak + anti-circumvention forms.
- `prefiltre.py` (step 1), `triviaux.py` (step 2, FR+EN allowlist),
  `blocklist.py` (step 3, FR+EN regex, compact + word-boundary matching).
- `embeddings.py` (step 4) — pure-Python cosine vs `data/references.json`
  (FR+EN reference phrases), lazy one-time reference embedding.
- `nano.py` (step 5) — exact French system prompt, nonce-fenced JSON payload,
  bounded context loop (12→40, 3 rounds), strict output coercion.
- `injection.py` — per-request nonce fencing (anti-prompt-injection C3).
- `engine.py` — shared per-bot orchestrator wiring the funnel to `bot.gateway`.
- `rules_check.py` — AI safety check for server rules (anti prompt-injection),
  fails closed.

All external calls go through **`bot.gateway`** (embeddings + chat).

### 2. Module — `modules/automod.py`

`ModuleBase` subclass = the "caller". Scalable **feature framework**
(`AutomodFeature` + `FEATURE_CLASSES`); today only `content` (the AI funnel).
Applies decisions (delete / warn / mute / ban), records **guild cases** via
`bot.cases` with `issuer_type=AUTOMOD`, attaches the offending message as an
**`evidence`** case event, logs to a configurable channel, and re-submits
nano-flagged messages (one level, `force_nano`). Marks
`bot._moddy_initiated_sanctions` so `case_sync` doesn't double-record.

### 3. Config UI — `modules/configs/automod_config.py`

Single Components V2 panel: enable/disable module + the content feature, edit
server rules (AI-validated **before** save, in a Modal), pick a log channel,
exempt roles/channels, toggle moderator exemption.

### 4. Wiring

- `cogs/module_events.py` — dispatch `on_message` to the automod module.
- `cogs/config.py` — register `AutomodConfigView`.
- `db/base.py` — seed quota limits for `automod_decision` (guild+global) and
  `automod_rules_check` (guild).
- `locales/fr.json` + `locales/en-US.json` — `modules.automod.*` keys.

### 5. Gateway logging — prompt/response files (separate user request)

`gateway/executor.py` + `gateway/logger.py` now forward the request payload and
the response to `bot.tech_logger.log_api_call(...)`, which attaches them as two
text files (`prompt_<cid>.txt`, `response_<cid>.txt`) on the `api_call` webhook
message via Components V2 `File` items. `utils/tech_logger.py`: `_dispatch`
accepts `files`, `_card` accepts `attachment_names`. Files are forwarded to the
webhook only (not persisted to Redis/PG). Applies to **all** gateway calls.

## Decisions & rationale

- **Pure-Python cosine** (no numpy) to avoid a new dependency; references are
  small (~80 phrases).
- **Shared engine** across guilds (detection data is global); per-guild inputs
  (rules, history, context) passed per call → scalable and cheap.
- **Blocklist routes, never sanctions** → aggressive matching is fine; nano is
  the safety net. Short embedding skip for <4-char content to save calls.
- **Rules safety check fails closed** since rules go into nano's system prompt.

## Known issues / follow-ups

- `SEUIL_EMBEDDING` (0.45) and the reference/blocklist lists must be calibrated
  on real server data.
- **Volume:** every non-trivial, non-blocklisted message → 1 embedding call,
  and the gateway logs every call to the webhook (now with files). Consider the
  optional Redis cache / embedding batch before very large scale.
- Future features (anti-link/invite/spam/raid) plug into `FEATURE_CLASSES`.

## Files modified / added

Added: `automod/` (package + `data/references.json`), `modules/automod.py`,
`modules/configs/automod_config.py`, `docs/AUTOMOD.md`, this session log.
Modified: `cogs/module_events.py`, `cogs/config.py`, `db/base.py`,
`locales/fr.json`, `locales/en-US.json`, `gateway/executor.py`,
`gateway/logger.py`, `utils/tech_logger.py`, `CLAUDE.md`, `docs/API_GATEWAY.md`.
