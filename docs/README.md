# Moddy documentation

Entry point for everything under `docs/`. The AI-agent overview and the mandatory
coding rules live in [`/CLAUDE.md`](../CLAUDE.md), whose *Documentation Index* lists
every document with a "when to read" hint.

## Structure

| Folder | What goes there | Examples |
|---|---|---|
| [`guides/`](guides/) | Cross-cutting rules and how-tos that apply to any feature: UI, i18n, errors, persistence, commands, modules, notifications, staff tooling | `DESIGN`, `COMPONENTS_V2`, `MODALS_V2`, `PERSISTENT_VIEWS`, `ERROR_HANDLING`, `EMOJIS`, `COMMANDS`, `MODULE_SYSTEM`, `NOTIFICATIONS` |
| [`features/`](features/) | One doc per feature or module: how it works on the bot side | `AUTOMOD`, `TICKETS`, `ALTGUARD`, `LOGS`, `BUMP_REMINDER`, `WELCOME_MESSAGES` |
| [`integration/`](integration/) | Contracts with the backend / dashboard: schemas, wire formats, Redis and DB contracts other services depend on | `AUTOMOD_CONFIG`, `TICKETS_INTEGRATION`, `BACKEND-INTEGRATION`, `REDIS_COMMUNICATION`, `TASK_SIGNATURE` |
| [`infra/`](infra/) | Hosting, gateway, data and monitoring | `RAILWAY`, `API_GATEWAY`, `DATABASE`, `STATS`, `HEALTH_MONITOR`, `TECHNICAL_LOGS` |
| [`archive/`](archive/) | Point-in-time documents: audits, dated change specs, finished plans, superseded docs | `SECURITY_AUDIT`, `AUTOMOD_V2_PLAN` |
| [`sessions/`](sessions/) | One summary per AI work session (see [`sessions/README.md`](sessions/README.md)) | `2026-10-16_*.md` |
| [`public/`](public/) | User-facing pages published outside the repo (e.g. the token detector page). Do not move or rename files here: external links may depend on them | `token-detector.md` |

## Where to put a new doc

1. A rule every contributor must follow, whatever the feature: `guides/`.
2. A feature or module of the bot: `features/<FEATURE>.md`.
3. Something the backend or dashboard reads or writes (table, Redis key, payload): `integration/<FEATURE>_INTEGRATION.md`, linked from the feature doc.
4. Hosting, quotas, database, monitoring: `infra/`.
5. A one-off report or a plan that is finished: `archive/`, plus a row in CLAUDE.md's Archive table.

Then add a row to the matching table in CLAUDE.md's *Documentation Index*.

## Conventions

- File names are `UPPER_SNAKE_CASE.md` (the feature name, not a version: `AUTOMOD`, not `AUTOMOD_AI`).
- Links between docs are relative (`../guides/PERSISTENT_VIEWS.md`); links to code from a doc in a subfolder start with `../../`.
- Code comments may point to a doc with its full path from the repo root (`docs/guides/DESIGN.md`).
- When a doc is moved or renamed, grep the repo for its old path (CLAUDE.md, other docs, code comments, tests) and fix every reference.
- Superseded docs are moved to `archive/`, never deleted.

## Session logs

After each work session, add `sessions/YYYY-MM-DD_short-description.md`: what was done, files modified, decisions and why, known issues and follow-ups. Template in [`sessions/README.md`](sessions/README.md).
