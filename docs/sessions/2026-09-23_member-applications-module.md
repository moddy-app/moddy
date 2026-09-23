# Session: Member Applications module

**Date:** 2026-09-23
**Agent:** Claude Code

## Summary

This session added a new module, `member_applications`. It helps servers that
use Discord's "Apply to Join" (Server Member Applications). Each submitted
application goes to a review channel as a Components V2 card, with persistent
*Approve* / *Reject* buttons. Rejecting asks for the reason Discord shows the
applicant, in a Modal V2 that offers the server's preset reasons. The card also
closes when the decision is taken in Discord's own UI, or when the applicant
withdraws.

Before writing any code, the API surface was researched in Discord's official
OpenAPI spec (`discord/discord-api-spec`) and in Discord Userdoccers. Only two
join request routes are open to bot tokens: `GET /guilds/{id}/requests` and
`PATCH /guilds/{id}/requests/{request_id}`. Both require Kick Members.

## Changes Made

- `modules/member_applications.py`: the module (config, validation,
  `can_review`). `REQUIRED_BOT_PERMISSIONS = ["kick_members"]`.
- `modules/configs/member_applications_config.py`: the `/config` panel
  (toggle, review channel, ping roles, reviewer roles, preset reasons edited
  in `PresetReasonsModal`, a Modal V2).
- `services/member_application_service.py`: the raw API calls,
  `ingest` / `decide` / `sync_guild`, and posting/refreshing the card.
- `cogs/member_applications.py`: the gateway parser hook
  (`GUILD_JOIN_REQUEST_*` → `on_join_request_event`), the reconciliation poll
  and the 180-day purge.
- `utils/member_application_views.py`: the card, the `ApproveButton` /
  `RejectButton` dynamic items, and `RejectModal` (Modal V2).
- `db/repositories/member_applications.py` and `db/base.py`: the
  `member_applications` table and its repository.
- `cogs/config.py` and `utils/persistent_views.py`: registration.
- `utils/emojis.py` and `docs/EMOJIS.md`: `SHAPES` (`<:shapes:1552314638782300260>`),
  the module icon.
- `locales/*.json`: `modules.member_applications.*` in the 5 languages.
- `tests/test_member_applications.py`: 66 tests.
- `docs/MEMBER_APPLICATIONS.md` (new), `docs/DATABASE.md` and `CLAUDE.md`.

## Decisions & Rationale

- **Gateway and poll together.** Nothing documents whether bots receive the
  `GUILD_JOIN_REQUEST_*` events. The parser hook costs nothing if they never
  arrive. The poll runs every 2 minutes until an event has been seen, then
  every 15 as a safety net.
- **The parser table rather than `enable_debug_events`.** Debug events would
  dispatch a raw event for every gateway payload; the three table entries only
  run for these three events.
- **Store the request snapshot.** Bots have no "get one request" route, so the
  card is rebuilt from `member_applications.request`.
- **A malformed list is "unknown", not "empty".** Developers have reported
  `{"total": 1}` with no list. Treating that as empty would close every
  pending card.
- **The first decision wins, and Moddy's echo is ignored.** Resolving only
  moves a row out of `SUBMITTED`. An `UPDATE` event naming the bot as the
  reviewer is dropped, so the moderator who clicked stays recorded.
- **Reviewer roles are a delegation.** Moddy acts with its own Kick Members,
  and a server can let a recruitment team decide without giving it Kick
  Members.
- **The form is not edited from Moddy.** `PATCH /member-verification` may need
  Administrator, which CLAUDE.md #12 forbids.

## Known Issues / Follow-ups

- Not tested against a real server with applications enabled. Two things need
  checking there: that the list route returns complete data, and that bots
  receive the gateway events (look for the log line `Join request gateway
  events are delivered`).
- Possible next steps: a role granted on approval, a server-log entry, a
  dashboard history, a "ticket" stand-in for interviews (applicants rarely see
  the server before approval).

## Follow-up (same day): card layout and no on/off switch

Feedback from the project owner:

- **The card reads like a form.** The applicant's information is now a list of
  labelled lines in a fixed order: Member, Display name, Username, ID,
  Created, Earlier applications. The status block uses the same format.
  `field_line()` builds every line, and the locale owns the label and its
  punctuation. The only emoji on the card is the one in the title.
- **The buttons sit outside the container.**
- **There is no separator between the answers**: they are one text block,
  under an "Answers" heading.
- **There is no on/off toggle in `/config`.** Configured means active, and
  deleting the configuration turns the module off. `enabled` is no longer
  stored, and a legacy `enabled` key is ignored.

## Follow-up: Bump Reminder directory icons

PR #407 was merged before the card/toggle follow-up was pushed. The branch was
restarted from `main`, that commit replayed on top, and this change added:

- `utils/emojis.py`: one constant per directory (`DISBOARD`, `DSMONITORING`,
  `DINVITES`, `DISCORDL`, `BEEMP`, `DISCORDTOP`, `FRENCHGG`) and
  `BUMP_DIRECTORY_EMOJIS` (key → emoji). `bumpreminder/registry.py` now reads
  `BumpBot.emoji` from it instead of hardcoding each string, so there is one
  source for the icons.
- `docs/EMOJIS.md`: a "Bump Reminder directory icons" section.
- `tests/test_bump_reminder.py::TestDirectoryIcons`: every directory has its
  icon, and the registry matches the dict.
