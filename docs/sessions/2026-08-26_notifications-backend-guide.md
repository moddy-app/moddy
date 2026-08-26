# 2026-08-26 — Notifications: backend implementation guide

## What was done

Rewrote [docs/NOTIFICATIONS_INTEGRATION.md](../NOTIFICATIONS_INTEGRATION.md)
from a short table contract into a complete **backend implementation guide** for
the centralized notification system. No code was touched — the bot side already
implements everything described.

New or substantially expanded sections:

- **§0 Mental model** — template vs. rendered message, `platforms` (intent) vs.
  `notification_deliveries` (fact), why the uuid must exist before the send.
- **§2 The schema, verbatim** — the four `CREATE TABLE` statements as the bot
  creates them (`db/base.py::_init_tables`), with indexes, `CHECK` constraints
  and cascade behaviour.
- **§3 Every legal value** — `kind`, `author`, `recipient_type`, `platform`,
  `status`, the twelve registered `source_service` ids with their i18n labels,
  and `locale` fallback.
- **§5 Substitution** — reference implementations in Python *and* TypeScript,
  including the `pythonStr` note (`True`, not `true`) for JSONB `variables`.
- **§6 Rendering** — exact mail assembly (block order, join, strip), dashboard
  shape, emoji/CDN mapping, and how to reproduce the Discord attribution line.
- **§8 Read models** — hydrated single row, user inbox (keyset), guild outbox,
  campaign stats, wording analytics.
- **§9 Delivery worker** — claiming with `FOR UPDATE SKIP LOCKED`, marking,
  retry policy in a *backend-owned* table, idempotency, failure taxonomy.
- **§10 Dashboard inbox** — what to show, what not to, and the authorisation
  rule (recipient, or Manage Server for a guild-addressed row).
- **§12 Suggested HTTP API shapes** — so the dashboard and backend agree once.
- **§14–16** — performance (no `OFFSET`, cache contents by hash), security and
  privacy (UGC escaping, snowflakes, retention), observability metrics.
- **§18 Test vectors** — substitution, emoji stripping, colour, content hash
  (verified against the running code), mail assembly.
- **§19–20** — implementation checklist split by concern, and an FAQ.

Also updated the doc index entry in `CLAUDE.md` and the "Backend & dashboard"
pointer in `docs/NOTIFICATIONS.md`.

## Files modified

- `docs/NOTIFICATIONS_INTEGRATION.md` (rewritten and expanded)
- `docs/NOTIFICATIONS.md` (cross-reference paragraph)
- `CLAUDE.md` (documentation index entry)

## Decisions

- **Kept it in the existing file** rather than creating a new one: there was
  already a backend-facing notifications doc, and two would drift apart.
- **Documented the schema verbatim** instead of only in prose — the backend
  cannot migrate these tables, so it needs to see exactly what the bot creates.
- **Retry state goes in a backend-owned table.** The shared tables have no
  attempt counter on purpose; adding one would let a backend deploy change a
  table the bot recreates at boot. Stated as an invariant (§17.8).
- **Test vectors verified against the code**, not written from memory: the
  content hash in §7/§18 was recomputed from `NotificationContent.to_dict()`
  canonicalization.
- **Marked `notification_send` / `notification_report` tasks as proposed only.**
  Neither exists in the bot; the doc says so explicitly so nobody ships against
  them early.

## Follow-ups

- No `email` / `dashboard` delivery rows exist yet — every caller sends with the
  default `platforms = {discord}`. The worker described in §9 has nothing to
  claim until a caller opts in.
- Abuse reports still have no entry point: the dashboard report control needs
  the `notification_report` task type (§13) before it can exist.
