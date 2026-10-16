# Session: Automod AI — infraction catalogue, custom infractions, member profiles, /config panel rewrite

**Date:** 2026-10-10
**Agent:** Claude Code

## Summary

Four batches on the Automod AI module (branch `claude/pensive-brahmagupta-g2ox23`,
commits `ed47777`, `fb4472e`, `5d51f0f`, `2ddaa18`), plus a documentation pass that
made the docs complete and consistent with the code:

- **A — Infraction catalogue and per-type sanctions.** `automod/infractions.py` is
  now the single source of truth for what can be sanctioned (message, image,
  profile and custom families). The barème tables (`PLANCHER`,
  `CATEGORIES_SENSIBLES`, `CATEGORIES_BAN_DIRECT`) are derived from it. A new
  `sanctions` config key lets a server disable a type, give it its own cran
  ladder per gravity (`mode: "perso"`, optional recidivism) or choose a profile
  action. Legacy `categories_desactivees` is folded into `sanctions[].enabled`.
- **B — Server-specific custom infractions.** `infractions_perso` (max 10) with
  `automod/custom_infractions.py`: schema, six templates, keyword matcher, routing
  references. Zero extra embedding call (the global message vector is reused),
  opaque `P<n>` ids shown to nano, `perso:<id>` categories downstream, verdict
  cache keyed on a fingerprint, `POST /automod/rules_check` accepts an
  `infraction` object, `validate_custom_infraction` guards the prompt.
- **C — Member profile moderation.** New `profile` feature (avatar, banner,
  names), driven by member events: Google SafeSearch on avatars/banners with a
  separate monthly budget, names through the AI funnel, `action_profil`
  (`hold` / `rename_warn` / `alert` / `bareme`), holds in `automod_profile_holds`
  with a 6 h sweep, labeling-queue lanes `profil_*`.
- **D — /config panel rewrite.** Instant-save panel built around one mandatory
  setting (the alerts channel): first-run wizard (channel, level Light / Balanced
  / Strict, simulation), home, what is monitored, sanctions (list, one type,
  custom infractions), AI guidance, exemptions, advanced, precedents browser.
- **Docs pass.** `AUTOMOD_CONFIG.md` now documents the whole backend contract
  (schema, bounds, validation errors, route extension, tables, Redis keys,
  deployment order); `AUTOMOD.md`, API_GATEWAY, RAILWAY, DATABASE,
  PERSISTENT_VIEWS, REDIS_COMMUNICATION and the `CLAUDE.md` tree were completed.

## Changes Made

Batch A / B (detection core and module)
- `automod/infractions.py` (new), `automod/custom_infractions.py` (new) — catalogue, `sanctions` schema/validation/normalisation, custom infraction schema, templates, keywords.
- `automod/bareme.py` — floors derived from the catalogue; `ConfigBareme.sanctions`, `desactivees()`, `plancher_perso`, perso mode skips severity / veteran / fresh-account, `action_profil` on `ResultatBareme`.
- `automod/engine.py`, `automod/embeddings.py`, `automod/nano.py`, `automod/constants.py`, `automod/schemas.py` — custom routing (keywords, similarity on the reused vector), P-ids, grounding, verdict-cache fingerprint, `PERSO_*` constants.
- `automod/rules_check.py`, `internal_api/routes/automod.py` — `validate_custom_infraction`, `infraction` mode of `POST /automod/rules_check` (`400 invalid_infraction`).
- `modules/automod_ai.py` — `sanctions` / `infractions_perso` load + validation, legacy fold, orphan handling.
- `utils/automod_render.py`, `utils/automod_label_views.py`, `services/appeal_service.py` — custom infraction names on cards; appeals tolerate profile cases.

Batch C (profiles)
- `services/automod_profile_service.py` (new, `bot.automod_profiles`), `cogs/automod_profiles.py` (new, hold sweep), `db/repositories/automod_profiles.py` (new), `db/base.py` (`automod_profile_holds`, `automod_label_items.kind` widened, `automod_profile_safesearch` quota rows).
- `modules/automod_ai.py` — `ProfileFeature`, `apply_profile_decision`, hold lifecycle, `manage_nicknames` permission; `cogs/module_events.py` — member events.
- `gateway/config.py`, `gateway/clients/vision.py`, `services/automod_image_service.py` — rule `google_vision/safe_search_profile`, `budget_model`, per-guild profile share; `stats/registry.py` — `automod.profile`.
- `services/automod_label_service.py`, `utils/automod_label_views.py` — `profil_*` lanes and hold revocation.
- `tests/test_automod_profiles.py` (new).

Batch D (panel)
- `automod/presets.py` (new), `modules/configs/automod_ai_common.py` / `automod_ai_sanctions_config.py` / `automod_ai_advanced_config.py` (new), `modules/configs/automod_ai_config.py` and `automod_ai_precedents_view.py` (rewritten), `cogs/config.py`, `utils/persistent_views.py`, `utils/emojis.py`, locales (`modules.automod_ai.panel.*`, old `modules.automod_ai.config.*` removed), `tests/test_automod_ai_panel.py` (new), `tests/test_persistent_views.py`.
- Tests for A/B: `tests/automod/test_infractions.py`, `tests/automod/test_custom_infractions.py`, `tests/internal_api/test_rules_check_route.py`.

Docs
- `docs/integration/AUTOMOD_CONFIG.md`, `docs/features/AUTOMOD.md`, `docs/infra/API_GATEWAY.md`, `docs/infra/RAILWAY.md`, `docs/infra/DATABASE.md`, `docs/guides/PERSISTENT_VIEWS.md`, `docs/integration/REDIS_COMMUNICATION.md`, `CLAUDE.md`.

## Decisions & Rationale

- **One catalogue, no parallel tables.** The barème tables are computed from `automod/infractions.py` so adding a type is a one-place edit; custom types are synthesized on the fly (`perso:<id>`).
- **A custom ladder gets exactly what the server chose.** `mode: "perso"` skips the severity shift, veteran clemency and fresh-account malus; recidivism is the only optional escalation, and confidence caps plus `max_action` always bound it.
- **Custom routing adds no embedding call.** The vector the global score computed is compared with the guild's reference vectors; keywords are free. A guild without custom infractions takes the exact old code path and prompt.
- **Real ids never reach nano.** Opaque `P<n>` ids, fenced names, the safety check on name + description + examples (keywords are matched by code, never prompted); Moddy's unmodified templates are trusted.
- **Orphan `perso:*` sanctions are rejected on save and dropped on load**, so the panel and the dashboard must delete `sanctions["perso:<id>"]` with the infraction.
- **Profile SafeSearch has its own gateway budget** (`safe_search_profile`) so profile checks and message images never starve each other; the API call is the same.
- **A clean cached name verdict is reused only by a server whose severity is not stricter** than the one it was judged with.
- **The hold sweep runs every 6 h with a 36 h renewal window** so a restart or a missed pass cannot let a 28-day timeout lapse unnoticed; a per-(guild, member, asset/name) "done" marker stops event storms from sanctioning twice.
- **Instant save through `save_module_config`** (it validates), no working copy; the alerts channel lives on Home because it is the one mandatory setting.
- **Legacy `categories_desactivees` is folded into `sanctions[].enabled` on load and never written back**; the panel keeps one shared exemption list (mirrored from `content`).
- **A wizard level is applied switched off and in simulation**; step 3 decides.
- **Old panel custom_ids (`moddy:automod:config:*`) were not kept**, to avoid a compatibility layer for a screen that was fully rewritten.
- **Deployment order is bot first, backend second**: every new key is optional and old configs load unchanged; an older bot would reject `features.profile`.

## Known Issues / Follow-ups

- [ ] Banner-only changes emit no Discord event: banners are checked on join and on any other profile event only.
- [ ] Names are judged only when the blocklist / embedding routing sends them to nano; an unobvious name is not flagged.
- [ ] A nickname reset by `rename_warn` is not restored when the team revokes the sanction.
- [ ] Old messages still showing the previous panel (`moddy:automod:config:*` ids) stop answering ("This interaction failed") until `/config` is reopened.
- [ ] The es-ES / pt-BR / de translations of the new panel, profile and infraction texts have not been reviewed by native speakers.
- [ ] `PERSO_SIMILARITY_THRESHOLD` (0.50 at severity 3) is a starting value and needs calibration on real traffic.
- [ ] Google SafeSearch's free tier is 1000 units/month per account: with the two budgets at their default 1000 each, combined use can exceed the free tier — lower one of the two env vars if that matters.
- [ ] Backend/dashboard: mirror the contract in `docs/integration/AUTOMOD_CONFIG.md` §10 (infraction safety check before saving, delete `sanctions["perso:<id>"]` with an infraction, never write `categories_desactivees`, keep exemptions identical across features).


## Review fixes (12 findings)

- `hold` is no longer valid for `profil_pseudo` (panel, normalize, validate, plus a runtime fallback in `_profile_plan`).
- Banner holds: failed fetch = unknown (never lifts); the sweep now runs hourly and re-checks every hold every 6 h (`last_checked`, ordered so stale holds cannot starve it).
- Name verdict cache keyed by a digest of the server's indications; team negative label writes a global allow marker and resets a leftover "Moderated name" nickname on revoke.
- `screen_member` releases its per-member lock after the `async with` (no more `_profile_locks` growth).
- Simulation uses `sim:` done markers; profile SafeSearch negative cache skips per-guild share refusals.
- Accepted appeal writes a per-guild allow marker; renewed holds extend their MUTE sanction expiry.
- Custom keyword routing no longer outranks a passing global embedding score (`_route_semantic`).
- Follow-up: none known. Rows of departed members stay (for rejoin re-hold) until 60 d past their timeout.
