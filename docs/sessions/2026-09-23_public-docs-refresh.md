# 2026-09-23 — Public docs refresh (Mintlify): changelog Sep 7–23, Member Applications, module pages

## What was done

Brought the public documentation (`moddy-app/mintlify-docs`, docs.moddy.app) up to
date with everything shipped since the last changelog entry (Sep 3), using the
session logs and commit history of this repo as the source. All edits were made
through the Mintlify MCP and committed to `claude/peaceful-turing-3fbmhu` of the
docs repo, in the three doc languages (EN/FR/ES).

- **Changelog**: entries for Sep 7 (native command permissions for `/config` and
  `/altguard`), Sep 9 (bump reminder fixes, announcement translation), Sep 11
  (ticket transcripts, ratings, closure detection, ticket log, member leaving),
  Sep 17 (no Administrator, per-module permissions; ticket auto-delete + keep
  option; Inter-Server paused), Sep 20 (Configure with AI in `/config`), Sep 23
  (Member Applications).
- **New page**: `modules/member-applications` (the only module with no page).
- **Updated pages**: modules overview (card + per-module bot permission table
  mirroring `REQUIRED_BOT_PERMISSIONS`), Tickets (closing flow, `stats`
  permission, module settings, transcripts, ratings + `/ticket stats`, closure
  suggestions, ticket log, member leaving/returning), `/config` and quickstart
  (Administrator no longer needed, stale closed-beta notices removed), AltGuard
  (`/altguard` defaults to Kick Members), Server Logs (Manage Webhooks now gates
  the config), Inter-Server (paused notice), Bump Reminder (role pings that
  render but don't notify), module commands (`/ticket` reference).

## Decisions

- Internal-only changes (stats system, staff-only Bot Customization grant,
  transcript body precision) were left out of the public changelog.
- The transcript "staff thread withheld from the author" rule was not
  documented publicly: it is a dashboard contract not implemented yet
  (`docs/TICKETS_INTEGRATION.md`).
- The exact Discord settings path to enable Apply to Join was not asserted,
  since it could not be verified.

## Follow-ups

- The docs branch has no PR yet.
- Member Applications is still unverified on a real server (see
  `2026-09-23_member-applications-module.md`); the public page should be
  revisited if gateway delivery or the list route behave differently.
