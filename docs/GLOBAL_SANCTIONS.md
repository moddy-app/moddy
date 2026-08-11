# Global Sanctions

> Sanctions issued by the **Moddy team** against someone who breaks Moddy's own
> rules. They are ordinary **cases** — there is no attribute, no separate table
> and no dedicated flag anywhere.

---

## 1. The three levels

A global sanction targets a **user** or a **server**, and comes in three
severities. Each maps onto an existing `sanction_action`, so nothing had to be
added to the schema:

| Level | `sanction_action` | What it does |
|---|---|---|
| **Warn** | `warn` | Informational. Nothing is blocked — it is on the record, and it counts when the team escalates. |
| **Limited** | `restrict` | Reduced service: **no premium**, **no new module** can be configured, and the **automod AI stops running** in the server. |
| **Suspended** | `ban` | **No access to any Moddy service at all.** Replaces the old "blacklisted" state. |

Levels are ordered — a subject holding several active global sanctions sits at
the **highest** one. A suspension therefore also implies every limitation.

**The UI speaks levels, not actions.** A global case never says "Ban" or
"Restrict" — `get_action_label_key()` (`utils/moderation_cases.py`) swaps the
label for `global_sanctions.level.*`, so staff and members read *Global
warning* / *Limited* / *Suspended*. Guild cases keep the raw vocabulary
(warn / mute / ban).

**Global sanctions can expire.** `restrict` and `ban` accept a duration
(`TEMPORARY_ACTIONS`), so a limitation or a suspension can be temporary; it is
swept by `bot.case_expiry` like any other sanction, and stops applying the
moment it lapses. A `warn` is always permanent (it blocks nothing anyway).

---

## 2. Where they live

A global sanction is a case with:

- `type = global`
- `scope_type = platform` (no `scope_id` — it applies everywhere)
- `subject_type = discord_user` **or** `discord_guild`
- `issuer_type = moddy_staff` (or `system` for migrated ones)

Two sources back it in the registry (`services/case_service.py`):

| key | subject | actions |
|---|---|---|
| `global` | `discord_user` | warn, restrict, ban |
| `global_guild` | `discord_guild` | warn, restrict, ban |

Both are `manual = True`, so `/mod case create` offers them — and only them —
depending on whether the target is a user or a guild
(`_manual_sources(subject_type)` in `utils/case_management_views.py`).

Everything else about the case works exactly as described in
[MODERATION_CASES.md](MODERATION_CASES.md): timeline, comments, revocation,
auto-close, references.

---

## 3. Resolving the level — `utils/global_sanctions.py`

Nothing reads the tables directly. `utils/global_sanctions.py` is the single
resolver:

```python
from utils import global_sanctions

level = await global_sanctions.get_user_level(bot, user_id)     # GlobalLevel
level = await global_sanctions.get_guild_level(bot, guild_id)

# The two checks almost every caller wants — they cover the acting user AND
# the server they act in, whichever is worse.
await global_sanctions.is_suspended(bot, user_id=..., guild_id=...)
await global_sanctions.is_limited(bot, user_id=..., guild_id=...)
```

It sits on hot paths (every interaction, every message the automod would look
at), so resolved levels are cached in memory on the bot. The TTL is
`CACHE_TTL` (120 s) **bounded by the soonest sanction expiry**, so a two-hour
limitation never applies a second longer than it should.

The cache is dropped explicitly whenever a global case is mutated:

- `CaseService.record_sanction` / `revoke_sanction` (targeted);
- the staff flows in `utils/case_management_views.py`;
- `bot.case_expiry`, when a temporary global sanction lapses;
- the backend, over `moddy:blacklist:updates` (see §6).

A DB error **fails open** — a database hiccup must never lock every user out of
the bot.

---

## 4. What each level actually blocks

| Enforcement point | File | Suspended | Limited |
|---|---|---|---|
| Slash commands (`tree.interaction_check`) | `bot.py::_global_sanction_check` | blocked | — |
| Buttons / selects / modals (`on_interaction`) | `bot.py::_check_suspension_and_respond` | blocked | — |
| Prefix commands | `cogs/blacklist_check.py` | blocked | — |
| Bot added to a server | `bot.py::on_guild_join` | bot leaves | bot leaves (person only, see §9bis) |
| Premium (user + guild) | `utils/subscription.py` | off | off |
| Configuring a **new** module | `modules/module_manager.py::_blocked_as_new_module` | refused | refused |
| Automod AI | `modules/automod_ai.py::on_message` | off | off |

"New module" means a module whose config has never been stored for that guild
(a deleted config counts as new). Modules already set up stay fully editable —
the limitation freezes growth, it does not break what already runs.

The check covers **both** the acting user and the server: a limited user cannot
set up new modules anywhere, and nobody can set up new modules in a limited
server. `/config` shows a banner explaining it (`global_sanctions.limited.*`).

User-facing panels are Components V2 and fully translated:
`create_suspension_message()` / `create_limited_message()` in
`utils/components_v2.py`.

---

## 5. Restrict is **not** a server sanction

`restrict` only exists at platform scope. Guild cases offer `warn` / `mute` /
`ban` (`CASE_TYPE_ACTIONS[CaseType.GUILD]`), and `/cases` — which is guild-scoped
by construction — only ever offers those, including in its filter modal. A guild
moderator can never issue, lift or even filter on a global sanction.

`/mycases` spans every scope (it lists the caller's cases wherever they are), so
it keeps the full action list in its filters.

---

## 6. Case groups — one infraction, several cases

A breach rarely hits one subject. Shutting down a raid means sanctioning the
**user** *and* the **server** they run — two cases, one infraction. That is what
`cases.group_id` is for, and global sanctions lean on it heavily:

- `/mod global apply` opens **one case per target, all sharing a fresh
  `group_id`**, with the same level and reason.
- The subject gets **one** notice DM listing every case of the group — never one
  DM per case.
- Every follow-up command (`view`, `halt`, `lift`) takes a **group id or any
  case reference inside it**, so staff never have to remember which is which.
- `db.list_group_cases(group_id)` and `db.get_case_group_id(reference)` are the
  two queries behind all of it.

Groups are the existing `group_id` column — no new mechanism was added for this.

---

## 7. Staff commands (`/mod global`)

| Command | Permission | Purpose |
|---|---|---|
| `apply` | `global_sanction` | Sanction a user and/or servers as one group |
| `view` | `case_view` | Group status: level, cases, countdown |
| `halt` | `global_enforcement` | Stop the countdown — an appeal was filed |
| `lift` | `global_sanction` | Revoke the whole group and cancel the countdown |
| `pending` | `case_list` | The enforcement queue, soonest deadline first |

`apply` takes only the **targets** (`user`, `guilds` — ids separated by commas
or spaces); a **Modals V2** form collects the level (a `Select`), the reason, an
optional duration and the grace period. Five top-level components, one
submission, one grouped sanction.

`view` and the apply recap render the group panel, which carries a live
**Halt countdown** button — a persistent `DynamicItem`
(`moddy:gsanc:halt:<group_id>`) that re-derives the staff permission from the
interaction on every click, so it keeps working after a restart.

Every panel wears the **accent colour of its level** — yellow (warn), orange
(limited), red (suspended) — so severity reads before the text does.

---

## 7bis. The notice DM

One DM per group, laid out as:

1. **The explanation** — "You are breaking Moddy's rules", a link to
   moddy.app/violations, and how to appeal on moddy.app/support.
2. **"What this means for you"** — a bullet list built from the subject's
   actual situation (below), with numbered footnotes.
3. **One container per case** — target, level, reason, case reference.
4. **Details / Appeal** link buttons.

Every container wears the level's accent colour.

### The implications adapt to the subject

`_implications()` in `utils/global_sanction_views.py` states nothing that does
not apply:

| Bullet | Shown when |
|---|---|
| What the level itself changes | always (one line per level) |
| Subscription cancelled without refund | the sanction **restricts** (limited/suspended) **and** they actually pay |
| Moddy leaves the servers | **suspended** **and** a server is involved |
| Stored data may be deleted | same as above |
| Legal action may be taken | **suspended** only |

So a warned user sees a single line and no footnote; a suspended user with no
subscription and no server sees the access line and the legal line, nothing
else. A warning never threatens a subscription or a server, whatever the
subject owns.

"A server is involved" means the group hits one **or** the subject owns one —
a suspension costs them Moddy everywhere, and the enforcement leaves those
servers too, not only the ones named in the group.

Footnotes are numbered **in order of first use** (`_Footnotes`), so the
numbering is always contiguous no matter which bullets were skipped.

---

## 8. The appeal countdown

Some consequences of a global sanction are irreversible, so they are **deferred
by 48h** (`ENFORCEMENT_GRACE_HOURS`) to give the subject a chance to appeal:

| Level | Immediate | Deferred (48h, unless appealed) |
|---|---|---|
| Warn | — | — |
| Limited | premium off, no new modules, automod off | subscription cancelled without refund (if premium) |
| Suspended | no access at all | subscription cancelled without refund (if premium) **+ Moddy leaves the sanctioned servers _and every other server the subject owns_, stored data may be dropped** |

The schedule is one `case_enforcements` row **per group**
(`db/repositories/enforcements.py`), with a status of `pending` → `halted` (an
appeal was filed) / `executed` / `cancelled` (the sanction was lifted).

- The notice DM states the deadline and how to stop it (moddy.app/support).
- `bot.enforcement_sweep` (every 5 min) claims due rows **atomically**
  (`UPDATE … FOR UPDATE SKIP LOCKED`), so a restart mid-sweep can never
  execute a group twice.
- On execution the bot leaves the suspended servers itself and publishes the
  billing/data side for the backend to run.

A guild-only sanction still schedules a countdown — otherwise Moddy would never
leave the server it just suspended. Its notice goes to the server owner.

---

## 9. What a suspended user can still do

A suspension cuts off the product, not the person's ability to contest it.
`utils/global_sanctions.py` holds the two allowlists, and the interaction gates
in `bot.py` consult them **before** any DB lookup:

| Still allowed | Why |
|---|---|
| `/mycases` | read their own cases and their references |
| `/moddy` | reach support, terms and legal links |
| `/ping` | check whether the bot is even up |
| `moddy:apl:*` components | file and follow an automod appeal |
| `moddy:cases:browser:*:user` | the personal cases browser (never the guild one) |
| `moddy:moddy:*` components | the informational panel |

Everything else — commands, buttons, prefix commands — is refused with the
suspension panel.

---

## 9bis. Refusing to join a server

`on_guild_join` decides whether Moddy may stay. The rule itself is one pure
function, `global_sanctions.decide_join_refusal()`; `bot.py` only resolves the
levels and acts on the verdict. Three ways a join is refused:

| Refusal | Condition | Who is DMed |
|---|---|---|
| `guild` | the **server** is suspended | the owner |
| `owner` | the **owner** is limited **or** suspended | the owner |
| `inviter` | whoever **added the bot** is limited **or** suspended | the inviter |

Two asymmetries are deliberate:

- **A limitation is enough to refuse a person.** A limitation freezes growth —
  no premium, no new modules — and bringing Moddy into another server is
  exactly that kind of growth. A `warn` refuses nothing.
- **A limited *server* keeps Moddy.** Its existing setup must keep working;
  evicting the bot would break precisely what the limitation preserves. Only a
  suspended server is left.

The inviter comes from the audit log (`AuditLogAction.bot_add`), the only place
Discord exposes it. It is **best-effort**: on a server where Moddy has no
View Audit Log permission the inviter is unknown, and only the owner is
checked — an unreadable audit log never refuses an otherwise legitimate join.

The refusal DM names the reason: a limited account and a suspended one get
different wording (`join_refused.user.description_limited` /
`_suspended`), and a suspended server gets its own
(`join_refused.guild.description`).

---

## 10. Backend contract

Two channels, one in each direction.

### Bot → backend: `moddy:sanctions`

Published by `services/global_sanction_service.py` on every state change, so
the backend can run billing, data retention and dashboards. Every event carries
`type` and an ISO-8601 `ts`.

| `type` | When | Backend must |
|---|---|---|
| `global_sanction_applied` | A group is opened | Record it; a `warn` needs nothing else |
| `enforcement_halted` | An appeal stopped the countdown | Cancel any scheduled billing action |
| `enforcement_executed` | The grace period elapsed | **Cancel the subscription without refund** (`cancel_subscription`), purge `purge_guild_data` |
| `global_sanction_lifted` | The whole group was revoked | Undo/skip whatever was pending |

See §5 of the backend integration notes below for the full payloads.

### Backend → bot: `moddy:blacklist:updates`

When the backend writes a global case straight in DB, it must tell the bot to
drop its cached level:

```json
{ "type": "refresh", "user_id": "123456789012345678" }
{ "type": "refresh", "guild_id": "987654321098765432" }
```

Sending neither id clears the whole cache. Handled in
`bot.py::_handle_blacklist_event`.

---

## 11. Migration from the legacy attribute

Global sanctions used to be a `BLACKLISTED` attribute on `users` / `guilds`.
That attribute **no longer exists anywhere in the code**.

`ModerationRepository.migrate_legacy_blacklist_attributes()` runs once at
startup (`bot.setup_database`): every remaining `BLACKLISTED` attribute becomes
a global `ban` case (issuer `system`, reason "Migrated from the legacy
BLACKLISTED attribute.") and the attribute is dropped. It is idempotent — a
subject that already has an active global ban only loses the attribute — and a
no-op once there is nothing left to migrate.
