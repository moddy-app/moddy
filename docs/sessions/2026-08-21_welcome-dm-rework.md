# 2026-08-21 — Welcome DM rework (Components V2, multi-message)

Follow-up of `2026-08-07_welcome-message-rework.md`, which left `welcome_dm` on
the old embed-based config UI.

## What was done

Reworked the `welcome_dm` module end to end, mirroring `welcome_channel`.

**Before**: one `message_template` per guild plus ~8 `discord.Embed` keys
(title, description, color, footer, image, thumbnail, author), a single-form
config panel with Save/Cancel/Delete and four legacy (V1) modals, and
placeholder substitution via `str.format` (a stray `{` in a guild's text raised
inside the join handler).

**After**:
- Rendering is **Components V2** — a `ui.LayoutView` with one accent-coloured
  `ui.Container` holding the guild's text. All embed options are gone.
- A guild can configure **up to 3 welcome DMs** (cap per guild, all users
  combined), each with its own text, accent colour and enabled/paused switch.
- Customization is a single **Modal V2**: full text (paragraph), a localized
  placeholder cheat-sheet (`ui.TextDisplay`), and the accent colour as hex.
- Config panel = list + manage select + `Add`; actions apply immediately
  instead of batching behind a Save button.
- Placeholders: `{server}`, `{user}`, `{display_name}`, `{username}`,
  `{member_count}`, `{timestamp}`, substituted with `str.replace`.
- `AllowedMentions(everyone=False, roles=False, users=[member])` on every send.
- A `Forbidden` on the first DM (member has DMs closed) stops the loop instead
  of retrying every remaining entry.

## Files modified

| File | Change |
|---|---|
| `modules/welcome_dm.py` | Rewritten: `messages` list schema, `normalize_config()` (v1→v2 migration), `format_message()`, `build_welcome_view()`, list validation, multi-send `on_member_join` |
| `modules/configs/welcome_dm_config.py` | Rewritten: `WelcomeDmConfigView` (list) + `ManageWelcomeDmView` + `WelcomeDmMessageModal` |
| `cogs/config.py` | Route `welcome_dm` through the new async `create()` factory |
| `utils/persistent_views.py` | Register `ManageWelcomeDmView` alongside the list view |
| `locales/{fr,en-US,es-ES,pt-BR,de}.json` | `modules.welcome_dm` subtree replaced |
| `docs/WELCOME_DM.md` | **New** — DB schema, placeholders, validation contract, dashboard integration |
| `docs/WELCOME_MESSAGES.md` | Cross-link to the sibling module |
| `docs/PERSISTENT_VIEWS.md` | Stale `working_config` example (`WelcomeChannelConfigView` no longer has one) → `StarboardConfigView` |
| `CLAUDE.md` | Structure + documentation index |

## Decisions

- **Cap of 3, not 5.** Unlike a channel message, every extra entry is another
  private message pushed at the same person the second they join. Three is
  enough for "welcome + rules + links" without turning a join into DM spam.
- **No "Add" wizard view.** `welcome_channel` needs an intermediate view to
  hold the channel picker; a DM has nothing to pick, so the `Add` button opens
  the customization modal directly and the submit writes the entry. Two
  persistent views instead of three.
- **v1 migration folds the embed into the text.** The V2 container has no title
  field, so an enabled embed's title becomes a `###` heading and its
  description is appended after the message template (skipped when identical).
  Image/footer/thumbnail/author have no equivalent and are dropped. Pure
  function, nothing written back until the next save.
- **Entries are numbered in the UI** (`Message 1`, `Message 2`, …) since there
  is no channel to name them by; the select description carries a one-line
  preview of the stored text.
- **Custom_ids renamed** from `moddy:welcomedm:config:*` to
  `moddy:welcomedm:{main,manage}:*` to match the new view split. Panels posted
  before the deploy are config UI only — they are re-opened with `/config`.

## Verification

- `python3 -m pytest tests/test_persistent_views.py -q` → **190 passed**
  (custom_id namespacing, no collisions, shells construct, both views
  persistent).
- Smoke test: v1→v2 migration (with and without embed), placeholder formatting
  incl. an unmatched `{`, `build_welcome_view()` output, `validate_config()` on
  every failure branch, modal + both views constructed in all 5 locales.
- i18n length audit against Discord limits (modal title 45, `Label.text` 45,
  `Label.description` 100, select placeholder 150, button label 80) across all
  5 locales.

## Follow-ups

- The dashboard must mirror the §4 validation rules of `docs/WELCOME_DM.md` and
  emit `module_updated` after writing, or the bot serves a stale config until
  restart.
