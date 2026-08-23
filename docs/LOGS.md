# Advanced Server Logs

> Records **163 events across 18 categories** into the server's own log
> channels — who did what, to whom, and what changed.
>
> Read this before touching `serverlogs/`, `modules/logs.py`,
> `modules/configs/logs_config.py` or `cogs/logs.py`.

| | |
|---|---|
| Module id | `logs` |
| Module (config + routing) | [modules/logs.py](../modules/logs.py) |
| Config UI | [modules/configs/logs_config.py](../modules/configs/logs_config.py) |
| Discord wiring | [cogs/logs.py](../cogs/logs.py) |
| Event catalogue | [serverlogs/registry.py](../serverlogs/registry.py) |
| Rendering | [serverlogs/renderer.py](../serverlogs/renderer.py) |
| Delivery | [serverlogs/dispatcher.py](../serverlogs/dispatcher.py) |
| Audit correlation | [serverlogs/audit.py](../serverlogs/audit.py) |
| Listener API | [serverlogs/service.py](../serverlogs/service.py) |
| Builders | [serverlogs/listeners/](../serverlogs/listeners/) |
| Tests | [tests/test_logs.py](../tests/test_logs.py), [tests/test_logs_i18n.py](../tests/test_logs_i18n.py) |

---

## What a server sees

One embed per event, in the channel the category is bound to:

```
┌──────────────────────────────────────────────┐
│ Role(s) added to a user                      │  title       = i18n event title
│ > **User:** @dyvion_ (<@120…>)               │  description = "> **Label:** value"
│ > **Added:** <@&966…>                        │
│ > **Reason:** Join Roles                     │
│                                     [avatar] │  thumbnail   = the subject
│ Moddy#0001                       26/07 13:12 │  footer      = executor + timestamp
└──────────────────────────────────────────────┘
```

The accent colour states the nature of the event at a glance:

| Kind | Colour | Meaning |
|---|---|---|
| `create` | green `0x57F287` | something appeared |
| `delete` | red `0xED4245` | something disappeared |
| `update` | blurple `0x5865F2` | something changed |
| `moderation` | orange `0xF28500` | a moderator acted |

The kind is **inferred** from the event name (`*_create` → create, `*_delete`
→ delete, everything else → update), with a small override table in
`registry._KIND_OVERRIDES` for the names that would infer wrong (`ban_add`
is moderation, not a creation).

> **Components V2 exception.** These messages use a classic
> `discord.Embed`, not `ui.Container`. Logs are posted through a channel
> **webhook**, and Discord rejects the `IS_COMPONENTS_V2` flag on a webhook
> message that carries a classic embed. This is the same documented
> exception `modules/starboard.py` relies on, and it is the only place in
> the logs system where CLAUDE.md rule 1 does not apply. Everything else —
> the `/config` panel and its modals — is Components V2 as usual.

---

## Architecture

```
Discord gateway ──▶ cogs/logs.py ──▶ serverlogs/listeners/<category>.py
                     (wiring only)      (builds the entry)
                          │                    │
                          │                    ▼
                          │            serverlogs/service.py
                          │             open() → is it logged?  ─── modules/logs.py
                          │             submit() → where to?         (config + routing)
                          ▼                    │
              serverlogs/audit.py              ▼
              ("who did this")        serverlogs/renderer.py  →  serverlogs/dispatcher.py
                                        (the one renderer)        (webhook, batching)
```

Each layer has exactly one job:

* **`cogs/logs.py`** — `@commands.Cog.listener()` methods, nothing else. It
  never decides what a log says. Adding a field to a log never touches it.
* **`serverlogs/listeners/<category>.py`** — one builder per event: what
  lines the log carries. `channels.py` is the reference for style.
* **`serverlogs/registry.py`** — the catalogue. Categories, events, colour
  kinds, i18n key names. Everything else reads it instead of hardcoding.
* **`modules/logs.py`** — the stored configuration and the routing
  (`channels_for(event)`).
* **`serverlogs/renderer.py`** — the **only** place that decides what a log
  looks like, plus the value formatters every listener must use.
* **`serverlogs/dispatcher.py`** — webhooks, batching, back-pressure.
* **`serverlogs/audit.py`** — a short-lived audit-log cache so a gateway
  event can be attributed to a moderator without a single REST call.

### The listener contract

```python
entry = await service.open(member.guild, "user_join", subject=member)
if entry is None:                     # not logged, muted channel, ignored actor
    return
entry.line("account_created", fmt_time(member.created_at))
entry.line("member_count", fmt_number(member.guild.member_count))
await service.submit(member.guild, entry)
```

`open()` returns `None` immediately when the server does not log the event.
On a server that logs nothing, every listener costs one dict lookup — that
is what makes 163 events affordable.

**Every value goes through a formatter** (`fmt_user`, `fmt_channel`,
`fmt_role`, `fmt_time`, `fmt_duration`, `fmt_bool`, `fmt_number`,
`fmt_permissions`, `escape`). Never build a user-facing value with a bare
f-string: markdown in a nickname or a channel topic would otherwise break
the layout of every log around it.

---

## Categories

| Id | Name (en-US) | Events |
|---|---|---|
| `server` | Server | 35 |
| `messages` | Messages | 5 |
| `users` | Users | 7 |
| `moderation` | Moderation | 17 |
| `channels` | Channels | 21 |
| `roles` | Roles | 8 |
| `threads` | Threads | 9 |
| `voice` | Voice | 6 |
| `invites` | Invites | 3 |
| `automod` | Discord AutoMod | 9 |
| `emojis` | Emojis | 4 |
| `stickers` | Stickers | 5 |
| `soundboard` | Soundboard | 5 |
| `events` | Events | 12 |
| `stage` | Stage | 4 |
| `polls` | Polls | 5 |
| `webhooks` | Webhooks | 5 |
| `applications` | Applications | 3 |

A **category is the unit a channel is bound to** — you cannot send two
events of the same category to two different channels. That is deliberate:
163 individual routes would be unusable in a config panel.

The same real-world occurrence can live in **two** categories: a ban is both
`server.ban_add` and `moderation.ban_add`. They are two distinct keys with
two distinct destinations, so a server can send bans to `#server-logs`, to
`#mod-logs`, or to both. Listeners emit the **bare** event name (`ban_add`)
and `LogsModule.channels_for()` fans it out to every category that declares
it and has not excluded it.

---

## Configuration (`/config` → Logs)

Three screens, **no save button** — every change applies immediately:

| Screen | What it does |
|---|---|
| **Root** | One line per configured category, a picker to open one, a "send everything to one channel" shortcut, and a "clear everything" button |
| **Category** | The channels this category posts to (up to `MAX_CHANNELS_PER_CATEGORY = 3`) and a paginated checklist of its events (25 per page) |
| **Options** | Ignored channels, ignored roles, ignore bots, attach transcripts, log language |

Applying on the spot is not a style choice: the category screen is built
from `DynamicItem`s (they carry the category and the page in their
`custom_id`), and a dynamic item is rebuilt from scratch on *every* click —
there is no `self` on which to stage pending edits. See
[docs/PERSISTENT_VIEWS.md](PERSISTENT_VIEWS.md).

### Stored schema — `guilds.data.modules.logs`

```jsonc
{
  "categories": {
    "server":   { "channel_ids": ["123456789012345678"], "disabled_events": ["user_kick"] },
    "messages": { "channel_ids": ["456789012345678901"], "disabled_events": [] }
  },
  "ignored_channel_ids": ["789012345678901234"],
  "ignored_role_ids": [],
  "ignore_bots": false,
  "attach_transcripts": true,
  "locale": "auto"
}
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `categories` | object | `{}` | Only categories the admin actually configured. Unknown category ids are dropped on load. |
| `categories.<id>.channel_ids` | array of snowflake **strings** | `[]` | Destinations, max **3**. Extra entries are truncated. A category with no channel logs nothing. |
| `categories.<id>.disabled_events` | array of bare event names | `[]` | **Exclusions only.** Anything not listed is enabled. Names unknown to the registry are dropped. |
| `ignored_channel_ids` | array of snowflake strings | `[]` | Max **25**. Matches the channel, its parent category, and a thread's parent. |
| `ignored_role_ids` | array of snowflake strings | `[]` | Max **25**. A member holding one of these roles is never the subject of a log. |
| `ignore_bots` | bool | `false` | Skip events whose subject/actor is a bot. |
| `attach_transcripts` | bool | `true` | Attach `.txt` transcripts and overflow files. When `false`, files are dropped and only the embed is sent. |
| `locale` | `"auto"` \| `fr` \| `en-US` \| `es-ES` \| `pt-BR` \| `de` | `"auto"` | Language of the log messages. `"auto"` follows `guild.preferred_locale`, falling back to `en-US`. |

Two conventions matter here:

* **Snowflakes are strings** in stored JSON (project-wide convention).
  `modules/logs.py::_int_list()` accepts both and coerces to `int`, so an
  integer written by an older client still loads.
* **Only exclusions are persisted.** An event added to the registry later
  therefore starts **enabled** on every server that already had its category
  bound — which is what an admin expects from "log this category".

The module has **no separate on/off switch**: `enabled` is computed as "at
least one category has a channel". Removing the last channel disables the
module.

### Backend / dashboard contract

The dashboard writes `guilds.data.modules.logs` directly and notifies the
bot over Redis (see
[docs/BACKEND-INTEGRATION.md](BACKEND-INTEGRATION.md) and
[docs/MODULE_SYSTEM.md](MODULE_SYSTEM.md)). Three things to know:

1. **`on_external_config_change()` is deliberately not implemented.** That
   hook exists for modules whose configuration is *visible* in Discord — a
   panel message to re-post, channel overwrites to re-apply, an external
   service to notify (`modules/altguard.py` is the example). The logs module
   posts no panel and holds no Discord-side state: reloading the config is
   the whole of the change. This is a decision, **not an oversight** — if
   the logs module ever grows something visible (a "logging started" notice,
   a pinned summary), the hook has to be added at the same time.
2. **Validation is not run on the dashboard's behalf.**
   `LogsModule.validate_config()` (every bound channel exists and the bot
   can `view_channel` + `send_messages` + `embed_links` there) is what
   `/config` enforces. A dashboard write that skips it stores a channel the
   bot cannot post in; delivery then fails silently per channel and only
   logs a warning bot-side. The dashboard should mirror the same three
   permission checks before saving.
3. **Changing a channel leaves the old webhook behind.** Moddy creates a
   webhook named `Moddy Logs` in each destination channel on first delivery
   (see below). If a channel is unbound — from `/config` or from the
   dashboard — that webhook is **not** deleted: it simply stops being used.
   It is inert (Moddy holds no reference to it after a restart, and it is
   never reused for another channel), but it stays visible in the channel's
   integration settings until someone removes it. Deleting it automatically
   was rejected: the bot cannot tell its own leftover webhook from one an
   admin has since repurposed, and deleting a webhook is not reversible.

---

## Delivery

Logs fire hardest exactly when a server is in trouble — a raid, a mass ban,
a purge. Posting them with `channel.send` would burn the bot's own
rate-limit buckets and slow every other command down, so:

* every log goes out through a **channel webhook** named `Moddy Logs`,
  created on demand (requires `manage_webhooks`), with its own rate-limit
  bucket;
* up to **10 embeds per request**, batched over a **0.75 s** window, so a
  burst of 200 deletions collapses into a handful of calls;
* **one worker task and one bounded queue per destination channel**
  (`QUEUE_MAXSIZE = 500`). When the queue is full the **oldest** pending
  entry is dropped and counted — a flood degrades into "the most recent
  events", never into unbounded memory. A warning is logged every 100 drops.
* an idle worker exits after 30 s and is restarted by the next `enqueue`.

**Fallbacks.** No `manage_webhooks` permission, webhook creation refused, or
a webhook deleted behind Moddy's back → delivery falls back to a plain
`channel.send`. A misconfigured server still gets its logs, just slower. The
cached webhook is invalidated on `NotFound` / `HTTPException` so the next
delivery re-resolves it.

**Threads** have no webhook of their own: the parent channel's webhook is
used with `thread=`.

**Entries carrying files** (transcripts, overflowing message bodies) are
sent on their own — attachments are per-message and cannot be batched.

### Size limits

Individual values are capped in the renderer (`MAX_DESCRIPTION = 4000`,
`MAX_FIELD_VALUE = 1000`), and a block longer than a field allows is moved
to a `.txt` attachment rather than silently cut. On top of that,
`LogEntry._fit()` enforces `MAX_EMBED_TOTAL = 5800` on the **whole** embed:
Discord rejects an over-budget message entirely, so a log with many fields
(a permission diff over a dozen roles) would otherwise never arrive.
Trailing fields are dropped first, the description is shortened only if that
is not enough, and the reader is always told the log was shortened
(`modules.logs.values.size_limit`).

---

## Audit-log correlation — "who did this"

The gateway says *that* a role was deleted; only the audit log says *who*
deleted it. A naive `guild.audit_logs(limit=1)` is unreliable (the audit
entry and the gateway event race, either can arrive first) and expensive
during a raid.

So `cogs/logs.py` feeds every `on_audit_log_entry_create` into
`serverlogs/audit.py` — free, it comes over the gateway — and a listener
that needs an executor calls:

```python
executor, reason = await service.executor(
    guild, discord.AuditLogAction.channel_delete, channel.id)
```

which looks the cache up, then waits **up to 2 s** for a matching entry to
arrive, then gives up. **No REST call is ever made.** Cached entries live
`CACHE_TTL = 20 s`, 60 per guild.

A log whose executor could not be resolved is still sent, without a footer:
"we know it happened" beats "we said nothing".

---

## Adding an event

Three steps, nothing else:

1. **Registry** — add the bare event name to its category in
   `_CATALOGUE` (`serverlogs/registry.py`). Add a `_KIND_OVERRIDES` entry
   only if the name would infer the wrong colour.
2. **i18n** — add two keys **in all five locales**
   (`fr`, `en-US`, `es-ES`, `pt-BR`, `de`):
   * `modules.logs.events.<category>.<event>` — the short name in `/config`
   * `modules.logs.titles.<category>.<event>` — the embed title
3. **Builder** — emit it from a listener in `serverlogs/listeners/`, and
   wire the gateway event in `cogs/logs.py` if it is a new one.

The `/config` panel paginates itself, the stored config validates the new
key away on old servers, and the event starts **enabled** everywhere its
category is bound.

`tests/test_logs_i18n.py` reads the listener sources: a new
`entry.line("something")` or `modules.logs.values.<key>` fails the suite
until it is translated in all five locales. That is the point — a missing
key does not crash, it renders as `[modules.logs.…]` in a production log
channel.

## Changing what a log says

* **Wording** — edit `locales/<locale>.json` under `modules.logs`. No Python.
* **A field of one log** — edit that one builder in
  `serverlogs/listeners/`.
* **The look of every log** — `serverlogs/renderer.py`, and only there.

---

## Known gaps and deliberate choices

### `moderation` events with no source yet

Wired today: `warn_add/remove`, `mute_add/remove`, `ban_add/remove` (via
`services/case_service.py::_log_to_server` → `serverlogs/listeners/moderation.py`),
`kick_add` (Discord audit log), `auto_moderation` (Discord AutoMod).

These are declared in the registry, served by
`serverlogs/listeners/moderation.py::log_case_event(...)`, and **nothing
calls them**:

| Event | Why |
|---|---|
| `case_delete`, `mass_case_delete` | Moddy cannot delete a case — there is no `delete_case` in `db/repositories/moderation.py` |
| `case_update` | Only reached today by the `restrict` / `revoke_access` sanctions; a reason edit or a manual status change is not mirrored yet |
| `kick_remove` | A kick cannot be "lifted" |
| `report_create`, `reports_ignore`, `reports_accept` | Moddy has no report system |
| `user_note_add`, `user_note_remove` | Notes are recorded as case events, and the case service does not mirror them |

They are declared on purpose so they light up the day the feature lands,
without touching the logs system. The natural hook-ups are
`db.add_event` (notes), `db.update_case_reason` and `db.set_status_manual`
(`case_update`) — but from **`CaseService`**, not from the DB repository:
`_log_to_server` is where the guild-scope check and the "a log never breaks
the moderation action" guarantee live.

### `channel_voice_status_update`

Discord ships no gateway event for a voice-channel status change and
discord.py has no enum member for it, so it is matched on the **raw audit
action id `192`** (`cogs/logs.py::_VOICE_STATUS_ACTION`). This has not been
confirmed against a live server: if the id is wrong or Discord removes the
action, the event is simply never emitted — harmless, but the config panel
would advertise a log that never fires.

### Not validated live

The system is covered by 59 unit tests but **has never run against a real
Discord server**. Worth checking on a test guild before it reaches
production: webhook creation and reuse, the `manage_webhooks` fallback,
batching under a burst of deletions, the 1–2 s audit-correlation windows
(they may need widening), the raw action id above, and the rendering of at
least one event per category.

### Premium

Logs are **not** premium-gated today — every server gets all 18 categories.
If that changes, `utils/subscription.py::is_guild_premium` is the check (see
[docs/PREMIUM.md](PREMIUM.md)); the natural knobs are the number of
categories a free server may bind and `MAX_CHANNELS_PER_CATEGORY`.

`MAX_CHANNELS_PER_CATEGORY = 3` and `MAX_IGNORED_CHANNELS` /
`MAX_IGNORED_ROLES = 25` (the Discord select limit) are working values, not
researched ones.

---

## Tests

```bash
python3 -m pytest tests/test_logs.py tests/test_logs_i18n.py -q
python3 -m pytest tests/test_persistent_views.py -q
```

* `tests/test_logs.py` — registry consistency, routing and fan-out, stored
  schema round-trip, ignore lists, rendering (escaping, truncation,
  attachments, size budget), delivery and batching.
* `tests/test_logs_i18n.py` — every event name, title, field label, standalone
  value and Discord permission name exists in all five locales, and no stale
  translation is left behind after a rename.
