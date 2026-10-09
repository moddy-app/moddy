# Member Applications — review Discord's "Apply to Join" from a channel

Discord lets a server require an application form before anyone can join
([Server Member Applications](https://support.discord.com/hc/en-us/articles/29729107418519-Server-Member-Applications)).
The questions and the gate itself belong to Discord and are set up in the
server settings. This module handles the **review** side. Each submitted
application goes to a review channel as a card with *Approve* / *Reject*
buttons, and the card stays up to date whoever takes the decision.

## Table of contents

- [What Discord allows a bot to do](#what-discord-allows-a-bot-to-do)
- [Files](#files)
- [Configuration schema](#configuration-schema)
- [How an application reaches Moddy](#how-an-application-reaches-moddy)
- [The card](#the-card)
- [Deciding](#deciding)
- [The `member_applications` table](#the-member_applications-table)
- [Permissions](#permissions)
- [Persistence](#persistence)
- [i18n](#i18n)
- [Known limits and open questions](#known-limits-and-open-questions)

---

## What Discord allows a bot to do

Discord calls an application a **guild join request**. Its lifecycle is
`STARTED` → `SUBMITTED` → `APPROVED` or `REJECTED`, and Discord keeps it for 180
days. The form has four field types: `TERMS` (accept the rules), `TEXT_INPUT`
(≤ 150 chars), `PARAGRAPH` (≤ 1000 chars) and `MULTIPLE_CHOICE` (one choice,
answered as an index). A server whose form has more than a `TERMS` field carries
the guild feature `MEMBER_VERIFICATION_MANUAL_APPROVAL`.

Discord's official OpenAPI spec
([`discord/discord-api-spec`](https://github.com/discord/discord-api-spec))
opens **exactly two** join request routes to bot tokens:

| Route | Use | Permission |
|---|---|---|
| `GET /guilds/{guild_id}/requests?status=&limit=&before=&after=` | List requests (100 per page) | Kick Members |
| `PATCH /guilds/{guild_id}/requests/{request_id}` `{action, rejection_reason}` | Approve / reject (reason ≤ 160 chars) | Kick Members |

A bot **cannot** do the following:

- **Fetch a single request by id**, so the card is always rebuilt from a stored
  snapshot.
- **Open an interview**: Discord's interview is a group DM, which bots cannot
  use.
- **Bulk-action requests**, or **see `STARTED` requests**.
- **Edit the form reliably**: `PATCH /member-verification` exists, but the
  permission it needs is unclear and may be Administrator (CLAUDE.md #12).
- **DM a pending applicant reliably**: before approval, they share no server
  with Moddy.

The `GUILD_JOIN_REQUEST_*` gateway events are documented (by the community, in
Discord Userdoccers) for **users** with Kick Members. Nothing says whether bots
receive them. See [How an application reaches Moddy](#how-an-application-reaches-moddy).

## Files

| File | Role |
|---|---|
| `modules/member_applications.py` | `MemberApplicationsModule`: config, validation, `can_review()` |
| `modules/configs/member_applications_config.py` | `/config` panel + `PresetReasonsModal` (Modal V2) |
| `services/member_application_service.py` | Raw API calls, `ingest` / `decide` / `sync_guild`, card post/refresh |
| `cogs/member_applications.py` | Gateway parser hook, reconciliation poll, retention purge |
| `utils/member_application_views.py` | The card, `ApproveButton` / `RejectButton` (dynamic items), `RejectModal` (Modal V2) |
| `db/repositories/member_applications.py` | `MemberApplicationRepository` |
| `tests/test_member_applications.py` | Config, card, service lifecycle, gateway hook, i18n |

## Configuration schema

`guilds.data.modules.member_applications`:

```json
{
  "channel_id": 123456789012345678,
  "ping_role_ids": [111],
  "reviewer_role_ids": [222],
  "rejection_reasons": ["Account too recent", "Incomplete answers"]
}
```

| Key | Meaning | Limit |
|---|---|---|
| `channel_id` | Review channel (text/announcement). Required to save. **A stored `channel_id` is the module being active** | — |
| `ping_role_ids` | Roles mentioned on a new card (first post only) | 5 |
| `reviewer_role_ids` | Roles allowed to decide, **in addition to** members with Kick Members | 10 |
| `rejection_reasons` | Preset reasons offered in the reject modal | 10 × 100 chars |

There is **no on/off switch**, neither in `/config` nor in the stored config:
configured means active, and deleting the configuration (stored as `{}`) is
how a server turns the module off. A legacy `enabled` key is ignored.

A preset reason is capped at 100 characters, below the API's 160, because it
also has to fit as a select option label. A dashboard writing this key must
follow the same limits. `normalize_reasons()` trims, deduplicates and caps
whatever it reads.

## How an application reaches Moddy

There are two paths, and both feed the same idempotent
`MemberApplicationService.ingest()`:

1. **Gateway.** discord.py drops unknown events after one dict lookup in
   `ConnectionState.parsers`. The cog adds three entries to that table
   (`GUILD_JOIN_REQUEST_CREATE` / `_UPDATE` / `_DELETE`), each of which
   dispatches `on_join_request_event(kind, data)`. This costs nothing for other
   events: no `enable_debug_events`, no raw socket listener. If a future
   discord.py version learns these events natively, its parser is left in
   place.
2. **Poll.** `sync_applications` runs every 30 s and reconciles each server with
   the module enabled against `GET /requests?status=SUBMITTED`, at most 25
   servers per tick:
   - each server is reconciled every **120 s**, until the first join request
     event has been received;
   - after that, every **900 s**, as a safety net.

   New requests get their card. A pending card whose request is no longer in
   the list is looked up in the `APPROVED` / `REJECTED` lists, starting just
   before the oldest orphan. It is closed as `WITHDRAWN` only when both lists
   were read **to the end** without finding it.

**A malformed list is "unknown", never "empty".** Developers have reported the
list route answering `{"total": 1}` with no `guild_join_requests` key. The poll
skips such a pass: treating it as empty would close every pending card on the
server.

**One card per application.** `claim_member_application` is an
`INSERT … ON CONFLICT DO NOTHING`, and only the caller whose insert landed posts
the card. If that post failed (no permission, channel gone), the next pass sees
a pending row with no `message_id` and tries again.

## The card

The card is Components V2, written in the **server language**, and posted
through `bot.notifications.send_channel` (`attribution=False`). Top to bottom:

1. the role mentions, above the container, **on the first post only**. The
   `allowed_mentions` list names exactly those roles;
2. the container:
   - the title `### <:shapes:…> New application`, the only emoji on the card;
   - the applicant, next to their avatar, as **labelled lines in a fixed
     order, with no emoji**: Member (mention), Display name (with the
     verification badge), Username, ID, Created (date + relative, followed by
     "recent account" when under 7 days), Earlier applications (with how many
     were rejected). Every line is built by `field_line()`: the locale owns the
     label and its punctuation (`**Membre :**` in French, `**Member:**` in
     English);
   - one separator, then **Answers**: every question and its answer in a
     single text block, with **no separator between answers**. Rules checkboxes
     read `**Server rules:** Accepted`. The answers are quoted with mentions
     escaped, and share a 2,800-character budget so the message stays under
     Discord's 4,000;
   - one separator, then the status as labelled lines: Status, and once
     decided, Decided by, Decided on, From (only when decided in Discord's
     screen) and Reason (for a rejection);
   - a footer with the request id and the submission time;
3. while pending, the *Approve* / *Reject* row, **outside** the container.

The accent follows the status: primary, success, error or neutral.

## Deciding

**Who may click:** a member with Kick Members (or Administrator), or a member
holding one of the `reviewer_role_ids`. This is re-derived on every click.
Moddy acts with its own Kick Members, so the reviewer roles are a deliberate
delegation: a recruitment team can review without being able to kick.

**Approve** acts in one click. **Reject** opens `RejectModal`, a Modal V2:

- a text display naming the applicant and saying Discord shows them the reason;
- an optional select of preset reasons, shown only if the server has some;
- an optional free-text reason (≤ 160 chars) that **wins** over the preset.

Both empty means a rejection without a reason, which Discord allows.

`decide()` calls the API and then `resolve_member_application(…,
decided_in="moddy")`. The resolve only moves a row out of `SUBMITTED`, so the
**first decision wins**. When the API accepts but the row was already resolved
elsewhere, the card shows the first decision and the clicker gets
`already_decided`. A 404 marks the application `WITHDRAWN`. A 403 means Moddy
lost Kick Members.

Discord echoes Moddy's own decision back as an `UPDATE` event naming the bot as
`actioned_by_user`. `ingest()` ignores it, so the moderator who clicked stays
recorded. A decision taken in Discord's own review screen closes the card with
`decided_in = "discord"` and the reviewer Discord reports.

## The `member_applications` table

There is one row per join request id. Discord keeps the application; this row
keeps the card and how the application ended.

| Column | Meaning |
|---|---|
| `request_id` (PK) | Discord's join request id |
| `guild_id`, `user_id` | — |
| `status` | `SUBMITTED` / `APPROVED` / `REJECTED` / `WITHDRAWN` |
| `request` (JSONB) | The join request as Discord sent it (answers, applicant). Bots cannot fetch it again |
| `channel_id`, `message_id` | Where the card is. `NULL` if posting failed; retried |
| `reviewed_by`, `reviewed_at`, `rejection_reason` | The decision |
| `decided_in` | `moddy` (card button) or `discord` (Discord's UI / withdrawal) |
| `submitted_at`, `created_at`, `updated_at` | — |

The indexes are: pending per guild (partial), `(guild_id, user_id)` for the
history line, and `created_at` for the purge.

**Retention:** the purge loop (every 6 h) deletes rows older than **180
days**, Discord's own retention. The answers are personal data.

## Permissions

`REQUIRED_BOT_PERMISSIONS = ["kick_members"]`: Discord requires it to list and
to action join requests. The review channel is checked for View Channel and
Send Messages at save time. Administrator is never required.

The panel shows whether the server has applications enabled in Discord (the
`MEMBER_VERIFICATION_MANUAL_APPROVAL` feature). Nothing else on the panel says
so, and a module configured on a server without the feature would otherwise
sit there silently.

## Persistence

- `MemberApplicationsConfigView`: registered view, static custom ids
  `moddy:member_apps:config:*`. Auth is Manage Server.
  **Unsaved changes live in the panel message.** Every change re-renders the
  panel, so the message itself holds the draft. When the click reaches a
  process that did not render the panel (after a restart, the registration
  shell, or a second bot process answering during a deploy), the draft is read
  back from the message by `draft_from_message()`: the three selects'
  `default_values`, and the `- `reason`` lines of the reasons list. It is not
  replaced by the stored config. Before this, such a Save re-wrote the old
  config and answered "saved".
- `MemberApplicationsPersistence`: registers the dynamic items
  `moddy:member_apps:card:approve:<request_id>` and `…:reject:<request_id>`.
  Auth is re-derived from the clicker, as described in [Deciding](#deciding).
- `RejectModal` and `PresetReasonsModal` are modals, excluded like every other
  modal.

## i18n

Everything lives under `modules.member_applications.*` in the 5 locales. The
card is in the server language; ephemeral errors and `/config` are in the
clicker's language. `tests/test_member_applications.py::TestTranslations`
checks:

- key parity across the locales;
- that every key the code uses resolves;
- the `/config` description (≤ 100 characters);
- the Modal V2 limits: title and label ≤ 45, description and placeholder ≤ 100.

## Known limits and open questions

- **Gateway delivery to bots is unverified.** The first
  `on_join_request_event` logs `Join request gateway events are delivered`. If
  that line never appears in production, the poll is the only path and cards
  arrive within about two minutes.
- **List route reliability.** It returned incomplete data for some developers
  as late as July 2026 (GitHub discussion `discord/discord-api-docs#8016`).
  Check it on a real server before announcing the feature.
- The spec lists `rejection_reason` ≤ 160 characters. Discord's response shape
  may still change: the code only relies on `id`, `application_status`,
  `user_id` / `user`, `form_responses`, `created_at`, `actioned_by_user` and
  `rejection_reason`.
- Not done yet: interviews (bots cannot use the group DM), a role granted on
  approval, a server-log entry, a dashboard view of the history.
