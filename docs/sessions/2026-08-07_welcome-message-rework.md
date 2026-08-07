# 2026-08-07 — Welcome Message rework (Components V2, multi-channel)

## What was done

Reworked the `welcome_channel` module end to end.

**Before**: one channel per guild, one message template, and an optional
`discord.Embed` with ~8 embed-specific config keys (title, description, color,
footer, image, thumbnail, author). The config panel was a single form with
Save/Cancel/Delete and five separate modals.

**After**:
- Rendering is **Components V2** — a `ui.LayoutView` with one accent-coloured
  `ui.Container` holding the guild's text. All embed options are gone.
- A guild can configure **up to 5 welcome messages** (cap is per guild, all
  users combined), each with its own channel, text, accent colour and
  enabled/paused switch.
- Message customization is a single **Modal V2**: full text (paragraph),
  a localized placeholder cheat-sheet (`ui.TextDisplay`), and the accent colour
  as hex — modelled on the Social Notifications customization modal.
- The config panel follows the Social Notifications layout: main list + manage
  select, `Add` flow (channel → customize → confirm), `Manage` flow (channel /
  edit / pause / remove). Actions apply immediately instead of batching behind
  a Save button.
- Placeholders: `{server}`, `{user}`, `{display_name}`, `{username}`,
  `{member_count}`, `{timestamp}`.

**Bug fixed along the way**: `cogs/module_events.py` dispatched `on_member_join`
to the module id `'welcome'`, which no registered module has ever answered to
(the ids are `welcome_channel` and `welcome_dm`). Channel welcomes and welcome
DMs therefore never fired. Both modules are now dispatched.

## Files modified

| File | Change |
|---|---|
| `modules/welcome_channel.py` | Rewritten: `messages` list schema, `normalize_config()` (v1→v2 migration), `format_message()`, `build_welcome_view()`, list validation, multi-send `on_member_join` |
| `modules/configs/welcome_channel_config.py` | Rewritten: `WelcomeChannelConfigView` (list) + `AddWelcomeMessageView` + `ManageWelcomeMessageView` + `WelcomeMessageModal` |
| `cogs/config.py` | Route `welcome_channel` through the new async `create()` factory |
| `cogs/module_events.py` | Dispatch `welcome_channel` **and** `welcome_dm` (was the non-existent `'welcome'`) |
| `utils/persistent_views.py` | Register the two new views |
| `locales/{fr,en-US,es-ES,pt-BR,de}.json` | `modules.welcome_channel` subtree replaced |
| `docs/WELCOME_MESSAGES.md` | **New** — DB schema, placeholders, validation contract, dashboard integration |
| `docs/MODULE_SYSTEM.md` | Refreshed the stale "Module Welcome" example section |
| `CLAUDE.md` | Structure + documentation index |

## Decisions

- **Storage stays JSONB** (`guilds.data.modules.welcome_channel`), not a
  dedicated table. Unlike social subscriptions there is no cross-guild reverse
  lookup to serve — a guild's welcome messages are only ever read for that
  guild, so the standard module storage fits and needs no migration.
- **`version: 2` + migrate on read.** `normalize_config()` is pure: a v1 config
  (`channel_id` + `message_template` + `embed_*`) becomes a one-entry list, so
  no guild loses its welcome channel, and nothing is written back until the
  next save. Embed-only settings are dropped — they have no equivalent in the
  V2 container.
- **Live-apply instead of Save/Cancel.** With *n* entries, a working-copy diff
  across add/edit/remove would need per-entry dirty tracking for little gain.
  Every write goes through `save_module_config`, so validation and module
  reload still happen on each change.
- **Placeholder substitution via `str.replace`, never `str.format`** — a stray
  `{` in a user's message would otherwise raise at send time, in the join
  handler. Unknown tokens are left visible rather than swallowed.
- **`AllowedMentions(users=[member])`** — a welcome message pings the joining
  member and nothing else; roles and `@everyone` cannot be mass-pinged even if
  the text contains them.
- **Manage view keeps the `ManageSubscriptionView` persistence trade-off**: the
  entry shown lives in `self` and is lost on a restart (the shell renders an
  empty card), while auth is re-derived from the interaction on every click.
  Same accepted loss, same precedent.

## Verification

- `python3 -m pytest tests/test_persistent_views.py -q` → **154 passed** (custom_id
  namespacing, no collisions, shells construct, all three views persistent).
- Manual smoke test: v1→v2 migration, placeholder formatting (incl. a message
  with an unmatched `{`), modal construction in all 5 locales, view construction
  as shells and with data, `build_welcome_view()` output.
- i18n length audit against Discord limits (modal title 45, `Label.text` 45,
  `Label.description` 100, select placeholder 150, button label 80) across all
  5 locales.

## Follow-ups

- The dashboard must mirror the §4 validation rules of `docs/WELCOME_MESSAGES.md`
  and emit `module_updated` after writing, or the bot serves a stale config
  until restart.
- `welcome_dm` still uses the old embed-based config UI — worth the same rework
  for consistency, out of scope here.
