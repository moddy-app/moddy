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
| Bot added to a server | `bot.py::on_guild_join` | bot leaves | — |
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

## 6. Backend contract

The backend can create/revoke global cases straight in DB. It must then publish
on `moddy:blacklist:updates` so the bot drops its cached level:

```json
{ "type": "refresh", "user_id": "123456789012345678" }
{ "type": "refresh", "guild_id": "987654321098765432" }
```

Sending neither id clears the whole cache. Handled in
`bot.py::_handle_blacklist_event`.

---

## 7. Migration from the legacy attribute

Global sanctions used to be a `BLACKLISTED` attribute on `users` / `guilds`.
That attribute **no longer exists anywhere in the code**.

`ModerationRepository.migrate_legacy_blacklist_attributes()` runs once at
startup (`bot.setup_database`): every remaining `BLACKLISTED` attribute becomes
a global `ban` case (issuer `system`, reason "Migrated from the legacy
BLACKLISTED attribute.") and the attribute is dropped. It is idempotent — a
subject that already has an active global ban only loses the attribute — and a
no-op once there is nothing left to migrate.
