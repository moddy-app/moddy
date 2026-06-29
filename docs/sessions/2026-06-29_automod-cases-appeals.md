# 2026-06-29 — Automod overhaul: cases, reasoning, appeals, severity, config

## What was done

A broad overhaul of the AI automod, driven by user feedback.

### Detection quality (`automod/`)
- **Facts vs reasoning**: `Decision` gains `explication`. nano's `raison` now
  carries **only the facts** (what the message contains / the rule broken);
  `explication` (≤2 sentences) holds the *why*. System prompt + `parse_verdict`
  updated.
- **Intent-to-harm** required before sanctioning (humour / quotes / casual
  swearing are not sanctionnable).
- **Anti-double-sanction**: nano judges each message on its own content and
  receives `messages_deja_moderes` (already-actioned message ids, from
  `db.list_automod_evidence_message_ids`) so it never re-punishes earlier
  conduct ("je vais" is not a threat just because a prior message was).
- **Severity 1–5** (`severite`): scales the embedding threshold
  (`constants.embedding_threshold_for`) **and** nano strictness.
- **Evasion hardening**: `normalize.collapse_repeats` + blocklist matching the
  collapsed form + `embeddings.score` segmenting long content (max cosine) — so
  `"je vais te tuer"×40` and diluted/long messages are caught.
- **Blocklist** expanded with the user's term list (relying on the existing
  leet/accent/separator/repeat normalization for variants), a new **doxxing**
  category, and raw **emoji-gesture** flags. New **doxxing** embedding
  references.

### Cases integration (`modules/automod.py`)
- Every sanction is a **guild case** (issuer `AUTOMOD`), recorded **before** the
  Discord action so the audit reason matches manual sanctions
  (`[REF] @Moddy (expiry) : reason`). Timed mute carries `expires_at`.
- Rich `evidence` event (extract, jump URL, explication, signal/score…).
- Mandatory **alert channel** (`notify_channel_id`, ex-`log_channel_id`).

### Appeals (new subsystem)
- `case_appeals` table + `db/repositories/appeals.py`; `record_sanction` returns
  the `sanction_id`.
- `services/appeal_service.AppealService` (`bot.appeals`): open + **binding**
  decide (accept reverses Discord action, transform replaces, refuse keeps),
  mirrored to the case timeline + reviewer panel + member DM + server notice.
- `utils/appeal_views.py`: persistent `DynamicItem` appeal buttons (server/team)
  + reviewer panel + Modals V2; routes to the guild alert channel or
  `config.MODDY_APPEAL_CHANNEL_ID`.

### Config UI (`modules/configs/automod_config.py`)
- Rebuilt to the **standard module Save/Cancel pattern** (working copy +
  Save/Cancel/Back/Delete), consistent with the other module panels (no longer
  persistent/immediate-apply). Sections: État, Salon d'alertes (required),
  Sévérité (1–5), Indications (replaces "Règlement", AI-checked), Exemptions,
  Options.

### Misc
- `tech_logger`: embedding response log shows a real per-vector preview (norm +
  first dims) instead of "(vectors omitted)".
- Added missing `languages.lmo` locale key.
- Full FR + EN i18n for all of the above.

## Files
- `automod/`: schemas, nano, engine, constants, embeddings, blocklist,
  normalize, data/references.json
- `modules/automod.py`, `modules/configs/automod_config.py`, `cogs/config.py`
- `db/base.py`, `db/repositories/{moderation,appeals}.py`,
  `services/{case_service,appeal_service}.py`, `utils/appeal_views.py`,
  `utils/persistent_views.py`, `bot.py`, `config.py`, `utils/tech_logger.py`
- `locales/fr.json`, `locales/en-US.json`, docs (AUTOMOD, MODERATION_CASES,
  CLAUDE)

## Decisions
- Appeal options: **Server + Team (user chooses)**; Moddy-team decision is
  **binding** (per user). Severity scales **both** sensitivity and harshness.
- `case_appeals.status` is TEXT+CHECK (no new PG enum); appeal timeline uses
  `comment` events (no `event_type` enum migration).
- Automod config made **non-persistent** to match the other module panels (user
  asked for the standard Save button); appeal buttons remain persistent.

## Follow-ups / not done
- Could not run discord.py at full runtime here (no live bot/DB): verified by
  imports, `py_compile`, view-build smoke tests and unit checks. Recommend a
  staging run of the end-to-end appeal flow + a DB `_init_tables` run to confirm
  the `case_appeals` table/indexes apply cleanly.
- Appeal "transform" Discord reason is readable but not yet in the exact
  `[REF] @mod (expiry)` shape (low priority).
