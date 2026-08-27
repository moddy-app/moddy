# Support requests — `/bug-report` and "configure it for me"

> What users send **to the Moddy team**, and how the team answers it.
>
> Two entry points, one object, one staff card. Read this before touching
> `cogs/bug_report.py`, `services/support_request_service.py`,
> `utils/support_request_views.py` or the `support_requests` tables.

---

## Why one system for two features

A bug report and a "please configure Moddy for me" request are the same object
seen from two angles: **a user wrote to the team about one server, a staffer
takes it, answers it, and closes it.** The wording on the card differs, the
channel differs, nothing else does. So they share a table, a service, a card, a
reply flow and a set of persistent buttons; `kind` is the only branch.

| | `/bug-report` | Configuration help |
|---|---|---|
| Entry point | the `/bug-report` slash command (Modal V2) | the **Configure it for me** button under Moddy's own announcements and its install welcome |
| `kind` | `bug` | `config_help` |
| Channel | `MODDY_BUG_REPORT_CHANNEL_ID` | `MODDY_CONFIG_HELP_CHANNEL_ID` |
| Lifetime | permanent | tied to the beta launch (see [Temporary pieces](#temporary-pieces)) |

---

## The flow

```
user                         Moddy                          team channel
 |  /bug-report               |                                   |
 |-------------------------->| BugReportModal (Modal V2)          |
 |  submits                   |                                   |
 |-------------------------->| open_request()                     |
 |                            |  -> support_requests row (uuid)   |
 |                            |  -> build_request_card() --------->| card + Claim/Reply/Close
 |<---- receipt (ephemeral) --|                                   |
 |                            |                                   |
 |                            |<------------------ Reply (modal) --|
 |<---- DM (notification) ----|  reply() -> notifications.send_dm  |
 |                            |  -> support_request_messages row   |
 |  Reply (button on the DM)  |                                   |
 |-------------------------->| user_followup() ------------------>| notice under the card
 |                            |                                   |
 |<---- DM "closed" ----------|  resolve() <----------- Close -----|
```

Every message to the user goes through `bot.notifications`
(docs/NOTIFICATIONS.md): a staff reply is a stored, attributed notification
like everything else Moddy sends, not a raw `user.send`.

---

## Pieces

| File | Role |
|---|---|
| `cogs/bug_report.py` | the `/bug-report` command and its Modal V2 |
| `services/support_request_service.py` | `bot.support_requests`: open, post the card, claim, reply, resolve, follow up |
| `utils/support_request_views.py` | the staff card, the reply DM, the modals, every persistent button, the **Configure it for me** entry point |
| `db/repositories/support_requests.py` | `support_requests` + `support_request_messages` |
| `utils/install_welcome.py` | the DM the installer gets when Moddy joins a server (carries the same button) |
| `utils/beta_announcement.py` | the beta-launch campaign card (carries the same button) |

---

## Database

Both tables are created idempotently in `db/base.py`, like every other table.

### `support_requests`

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` PK | the reference printed on the card, in the DM, and carried by every button's `custom_id` |
| `kind` | `TEXT` | `bug` \| `config_help` |
| `user_id` | `BIGINT` | who opened it |
| `guild_id` / `guild_name` | `BIGINT` / `TEXT` | the server it is about. `guild_name` is free text when the request came from a DM, where there is no guild to resolve |
| `locale` | `TEXT` | the requester's Discord language, so the reply DM speaks it |
| `subject` | `TEXT` | bug reports only (the one-line summary) |
| `body` | `TEXT` | the description |
| `details` | `JSONB` | per-kind extras: `steps`, `context` (bug), `availability` (config help). Rendered on the card via `support.card.details.<key>` |
| `status` | `TEXT` | `open` \| `claimed` \| `resolved` |
| `claimed_by` / `claimed_at` | | who took it |
| `resolved_by` / `resolved_at` | | who closed it |
| `channel_id` / `message_id` | `BIGINT` | where the staff card lives, so a click years later can refresh it |

### `support_request_messages`

One row per turn of the exchange: `author` is `staff` or `user`, and a staff
reply keeps the `notification_id` it was delivered as — "what exactly did we
send them" is one join away.

---

## Permissions

Handling a request needs the staff node **`support_request`**
(`utils/staff_role_permissions.py`, granted to Support and Supervisor_Sup;
developers and Managers are never gated by nodes). It is re-checked on **every
click**, never trusted from view state, exactly like the notification review
buttons.

The requester's own **Reply** button is authorized by the request itself: the
service refuses a follow-up whose author is not `support_requests.user_id`, so
the button carries no owner id.

---

## Anti-spam

`/bug-report` is open to everyone, so `SupportRequestService.is_rate_limited`
refuses more than `SPAM_LIMIT` (3) requests of one kind per user per
`SPAM_WINDOW_MINUTES` (10). A real bug survives a ten-minute wait; a flood
fills the team's channel for a day.

---

## Persistence

Every button is a `DynamicItem` carrying the request uuid, registered through
`SupportPersistence` (`utils/persistent_views.py`, group 12j):

| Button | `custom_id` |
|---|---|
| Claim | `moddy:support:claim:<uuid>` |
| Reply (staff) | `moddy:support:reply:<uuid>` |
| Close | `moddy:support:resolve:<uuid>` |
| Reply (requester, on the DM) | `moddy:support:ureply:<uuid>` |
| Configure it for me | `moddy:support:confighelp` or `moddy:support:confighelp:<guild_id>` |

Modals are one-shot, per the documented exclusion in
docs/PERSISTENT_VIEWS.md.

---

## Configuration

| Variable | Default | What |
|---|---|---|
| `MODDY_BUG_REPORT_CHANNEL_ID` | `1542307806055759943` | where bug reports land |
| `MODDY_CONFIG_HELP_CHANNEL_ID` | `1542307892970131516` | where configuration-help requests land |
| `MODDY_SUPPORT_URL` | `https://moddy.app/support` | shown on every card |
| `MODDY_DASHBOARD_URL` | `https://dashboard.moddy.app` | idem |
| `MODDY_DOCS_URL` | `https://docs.moddy.app` | idem |

Commands named inside a message are written with `config.command_label()`
(**`` `/config` ``**), never as a `</config:id>` mention: that id changes if the
command is re-registered, and a stale mention renders as raw text.

---

## i18n

Everything the requester reads is under `support.*` in the five content
locales; the staff card is rendered in one language
(`STAFF_PANEL_LOCALE = "en-US"`), like the notification review panels. The
`/bug-report` command name and description are localized separately in
`locales/commands/*.json` (32 locales) — see docs/COMMAND_LOCALIZATION.md.

---

## Temporary pieces

Two things here exist for the beta launch and are meant to be deleted
afterwards, without touching anything else:

* `utils/beta_announcement.py` + `staff/commands/com/beta.py` + the
  `notifications.beta` / `staff.com.beta` i18n blocks — the one-off campaign to
  the owners who installed Moddy during development (`/com beta`). Its
  **Translate** button re-renders the DM in the reader's own language from the
  notification's stored template and variables.
* The **configuration help** half of this system, if the team stops offering to
  configure servers by hand. Dropping it means removing the button from the two
  cards that carry it and the `config_help` branch; the bug-report half stands
  on its own.

`/com beta` has three targets: `preview` (renders nothing but the card),
`test <user_id>` (one real send, every platform included, for checking the mail
and dashboard renderings), and `owners` (the whole campaign, one DM per owner,
naming all of their servers). The mass send is confirmed through a **Modal**
where the sender types `SEND`: a campaign cannot be recalled, so it must not be
one mis-click away.

---

## Related

- docs/NOTIFICATIONS.md — how a reply reaches the user and why it is attributed
- docs/PERSISTENT_VIEWS.md — the button contract
- docs/MODALS_V2.md — the modals used here
- docs/STAFF_SYSTEM.md — the `support_request` permission node
