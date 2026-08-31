# 2026-08-31 — Never let an interaction die on Discord's own failure message

## The report

`/manage stripe trial` produced, in production:

```
moddy.staff.router - ERROR - Error in staff command m.trial: 404 Not Found (error code: 10062): Unknown interaction
  File "/app/staff/commands/manage/stripe/trial.py", line 62, in execute
    await ctx.defer()
moddy - ERROR - Failed to send app command error to user: 404 Not Found (error code: 10062): Unknown interaction
```

Sentry received the error. The user saw only Discord's **"The application
did not respond"** — no message, no error code.

## Why both halves failed

The error pipeline has two halves, and only the first ran.

1. **Why the token died.** The staff router did a permission lookup (DB) and
   an audit-log write (DB + webhook) *before* the command body reached
   `ctx.defer()`. Two round-trips is enough to burn the 3-second
   acknowledgement window, so `defer()` itself raised 10062.
2. **Why the user saw nothing.** `on_app_command_error` reported correctly
   (log, Sentry, DB, internal Discord log) and then tried to show `ErrorView`
   **on the same dead token**. That second 404 fell into a last-resort
   `except` that only logged — the second line above. Delivery had no plan B,
   so it gave up.

The same two shapes were everywhere in the codebase, plus a third the user
called out: modules answering an unexpected exception with a bare "une erreur
s'est produite" carrying no code and no central report.

## What was built

**`utils/interaction_response.py` (new)** — the single place that knows how to
reach a user:
- `safe_defer()` — acknowledges without ever raising; returns `False` on a dead
  token; picks `thinking` from the interaction type (a slash command needs the
  placeholder, a component re-rendering in place must not get one).
- `deliver()` — walks followup → edit original → initial response → **plain
  channel message mentioning the user**, so a dead token still gets the card in
  front of the user. Never raises: a second exception on the error path would
  silence the first.

**`cogs/error_handler.py`** — every delivery now goes through `deliver`
(BaseView, BaseModal, app commands, components, permission/cooldown/transformer).
New `report_error()` exposes the transport-agnostic half of the pipeline
(log → Sentry → DB → Discord log → **error code**) for paths with no global
handler above them. `ErrorView` accepts `error_code=None` and still renders, so
a missing `ErrorTracker` cog no longer means delivering nothing at all.

**Staff framework** — the router defers before any awaited work;
`StaffCommand.opens_modal = True` opts out the commands that answer with a
Modal (Discord refuses one on a deferred interaction). Its error path reports
centrally and shows the code instead of a generic card.

**Codebase sweep** (four parallel agents, disjoint directories):
- *Late acknowledgement*: `/ban /kick /mute /warn` (a 2.8s OpenAI call in front
  of an un-deferrable `send_modal` — the budget is now derived from
  `interaction.created_at` and degrades to "no prefill"), `/ticket*`, `/config`,
  `/altguard`, `/report`, `/reminder*`, `/library`, `/preferences`, the
  saved-roles commands, and the ticket / cases / appeals / notifications /
  support / automod-shadow view callbacks.
- *Bugs disguised as expected errors*: the translate and text-tools gateway
  wrappers answered "API unavailable" for **any** exception;
  `webhook.fetch_webhook_data` turned a crash into "webhook not found". Both
  narrowed to their real error types.
- *Errors reaching nothing*: three modals in `saved_messages.py` inherited
  `ui.Modal` instead of `BaseModal`; `token_detector._route_error` was a
  hand-rolled copy of the central pipeline. Listener paths with no handler above
  them (module events, case sync, ticket cleanup, the reminder loop) now report
  centrally.

**Root cause of a whole class of late acks** — `add_incognito_option` read
`DEFAULT_INCOGNITO` from the database before every command body ran, and the
same block was copy-pasted across ten call sites. `resolve_incognito()` now owns
it: 5-minute cache (the value is set from the dashboard, never by the bot), a
1-second ceiling, private as the fallback. Timeouts are deliberately not cached.

## Decisions worth keeping

- **`deliver`'s channel fallback is public.** An ephemeral error posted in
  channel reveals that the user ran a command. Accepted: silence is worse, and
  it only happens on an already-dead interaction.
- **No DM fallback.** Every DM goes through `bot.notifications`
  (CLAUDE.md rule 11); an error card is not a notification.
- **`serverlogs/` and `notifications/` were deliberately left alone.** Their
  failure mode is "cannot post to Discord", and the central handler also posts
  to Discord and writes to the DB — routing them there would turn a channel
  outage into an error storm. Same for `cogs/logs.py` (163 event types on the
  hottest gateway path) and the console/dev loggers (recursion risk).
- **Expected errors keep their own message.** Missing permissions, invalid
  input, premium required, quota exhausted: a specific translated sentence, no
  error code. A code there is noise that trains people to ignore codes.

## Known follow-ups

- **`AltGuardPanelView.on_verify`** does a module lookup plus up to two DB reads
  and *then* opens the consent modal, so it cannot be fixed by deferring. The
  only real fix is reordering (open the modal first, check verified state on
  submit), which changes the UX for already-verified members. **Product call.**
- **`cogs/bug_report.py` (`is_rate_limited`)** and
  **`saved_messages.save_message_context_menu` (`count_saved_messages`)** each
  run one query in front of an un-deferrable `send_modal`. Currently fast, but
  structurally fragile — same shape as the bug this session fixed.
- **`utils/global_sanction_views.py::_may_manage`** turns an unexpected failure
  into a "permission denied" card with no code. Fail-closed is right for an
  authorization check, but the failure is currently invisible.
