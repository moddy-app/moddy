# 2026-06-30 — Automod ↔ Cases integration overhaul

## What was done

### Critical fixes
- **Cases detail crash** (`maximum number of children exceeded (40)`): the
  detail screen now renders sanctions and comments as **capped, combined**
  `TextDisplay`s, so a long-lived case can never exceed the 40-component
  `LayoutView` ceiling (`utils/cases_views.py`).
- **Automod reused the same case** (always `V3GKV3`): automod now opens **one
  case per incident** (`link_open=False`) and adds the decision's extra
  sanctions to that same folder (`modules/automod.py::_record_case`).
- **`/cases` & `/mycases` unknown errors**: no longer swallowed by a generic
  message — they propagate to the centralized app-command error handler.
- **`AppealReasonModal` 404 (Unknown interaction)**: the modal now `defer`s
  before the slow `open_appeal` work; decision buttons/modals defer too.
- **`/config` module order** was non-deterministic → sorted discovery + picker.

### nano / detection
- System prompt rewritten **in English** (staff/prompt language rule).
- The **detector score is only a routing hint** — message content is
  authoritative and the score is never cited.
- Clear **warn → mute → ban escalation ladder** to rebalance severity.
- **Strips echoed `[DATA:<nonce>]` fence markers** from reason/explanation.
- **Temporary sanctions** via `duree_heures` (warn/mute/ban); `bot.case_expiry`
  lifts a temp ban's Discord ban on expiry.
- **AI response language** = the server's Community locale, else English;
  reason/explanation rendered in that language.

### Messages redesign (shield-branded, per-sanction colour/icon)
- **Member DM** styled like a manual sanction DM (icon + accent per action,
  responsible = Moddy Automod, auto-expiry, linked case id), offending message
  in a spoiler (long → `.txt`), coloured appeal-status panel, struck-through
  sanction once an appeal is accepted.
- **Server alert log** redesigned (shield, accent per sanction, localized
  actions, spoilered message, long → `.txt`); the log message id is stored on
  the case so appeal updates **reply to the original log**.
- **Appeal review panel** (server + team), all buttons persistent:
  Claim (hand) → accent #3661FF → yellow; Invite (link) → one-time server
  invite; Accept (done) → full cancellation **or** modify the case (Modal V2:
  action + duration + reason + note); Decline (undone). Team panel carries the
  technical ids; the server panel omits them.
- A member **can never review their own appeal**; server reviewers still need
  Manage Messages / Manage Server / Administrator.

### Config UI
- Automod `/config` panel rebuilt to Moddy conventions: top toggle button for
  the module, an Options select for the rest, no redundant `Current:` on
  dropdown-backed fields, no gratuitous separators, shield header.

### Error handling
- New `report_component_error()` funnels unknown errors from the persistent
  appeal buttons (which have no live `BaseView`) into the central handler.
- `TransformerError` (bad user/member argument) → friendly message, not a code.

## Files modified
`utils/cases_views.py`, `cogs/cases_user.py`, `cogs/cases_server.py`,
`modules/automod.py`, `modules/configs/automod_config.py`,
`modules/module_manager.py`, `automod/nano.py`, `automod/engine.py`,
`automod/schemas.py`, `utils/appeal_views.py`, `services/appeal_service.py`,
`db/repositories/appeals.py`, `db/repositories/moderation.py`, `db/base.py`,
`bot.py`, `cogs/error_handler.py`, `utils/emojis.py`, `utils/automod_render.py`
(new), `locales/fr.json`, `locales/en-US.json`, `docs/MODERATION_CASES.md`.

## Decisions
- One case per automod incident (instead of dedup-append) — fixes both the
  reference reuse and the 40-children blow-up, and reads better in `/cases`.
- `Invite` = one-time server invite (confirmed with the user) so a team
  reviewer not in the guild can join to investigate.
- Modify-case modal edits everything practical: action, duration, reason, note.

## Known follow-ups
- `case_appeals.claimed_by_id/claimed_at` added via idempotent migration —
  verify on the live DB after deploy.
- New emojis assumed present on the app: `shield`, `hand`, `link`, `pending`.
- The future automod variants (anti-raid / anti-spam / anti-image-scam) will
  plug in as new `AutomodFeature`s; the current detection stays the `content`
  feature under the umbrella `automod` module.
