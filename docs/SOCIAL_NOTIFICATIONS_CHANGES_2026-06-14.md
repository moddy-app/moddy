# Social Notifications — Backend/Dashboard change spec (2026-06-14)

Precise list of what changed in the bot so the **backend** and **dashboard**
stay in sync. This is a delta on top of [SOCIAL_NOTIFICATIONS.md](SOCIAL_NOTIFICATIONS.md)
(the canonical contract) — read both.

> TL;DR: 3 new columns, `message` semantics changed, a per-platform quota,
> 2 new error codes, and 3 new optional task fields. Nothing is renamed or
> removed.

---

## 1. Database — `social_subscriptions` (3 new columns)

| Column | Type | Default | Meaning |
|---|---|---|---|
| `embed_color` | `INTEGER` | `NULL` | Accent colour of the notification container (left bar), stored as a 24-bit RGB int (`0xRRGGBB`). `NULL` = platform brand colour. |
| `show_avatar` | `BOOLEAN` | `TRUE` | Render the author profile picture as a Section thumbnail. Ignored where the platform has no avatar. |
| `show_media` | `BOOLEAN` | `TRUE` | Render the large media/cover as a MediaGallery. Ignored where the platform has no media. |

Migration applied by the bot (idempotent, safe to run anywhere):

```sql
ALTER TABLE social_subscriptions
    ADD COLUMN IF NOT EXISTS embed_color INTEGER,
    ADD COLUMN IF NOT EXISTS show_avatar BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS show_media BOOLEAN NOT NULL DEFAULT TRUE;
```

If the **backend** writes the table directly (Option B), it MUST include these
columns in its INSERT/UPSERT and mirror the defaults. Full upsert example is in
SOCIAL_NOTIFICATIONS.md §6.A — now also set `embed_color`, `show_avatar`,
`show_media`.

### `embed_color` encoding
- Stored as **integer** (e.g. `#FF0000` → `16711680`).
- Dashboard should expose a hex colour picker and convert to int on save:
  `int(hex.lstrip('#'), 16)`. Empty/clear → store `NULL`.

---

## 2. `message` semantics CHANGED ⚠️

`message` was "an optional caption". It is now the **whole notification body**:

- It **includes the title** as a markdown heading (`##` / `#` / `###`), e.g.
  `## <:youtube:…> New video!\n{author} posted …\n{url}`.
- `NULL` no longer means "localized default caption". It now means **"use the
  per-platform default template"** (`modules/social_notifications.py::DEFAULT_MESSAGES`,
  English). The bot fills the template at render time.
- Max length unchanged: **1500 chars**.
- The notification container renders **only this text** — no bot-authored
  type/author/title/preview lines anymore. Role mentions render **outside** the
  container (above it).

The dashboard message editor should therefore be a single multi-line field whose
default value (when none is set) is the platform template, with the placeholder
cheat-sheet below it.

### Placeholders
`{author}`, `{title}`, `{url}` (alias `{link}`), `{platform}`, `{timestamp}`.

- `{timestamp}` = unix epoch (seconds), taken from the event
  (`timestamp` / `published_at` / `published` / `created_at`), falling back to
  dispatch time. Intended to be wrapped by the user as `<t:{timestamp}:R>`.
- Availability varies per platform (`PLATFORM_PLACEHOLDERS`):

| Platform | author | title | url | platform | timestamp | avatar | media |
|---|---|---|---|---|---|---|---|
| youtube | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| twitch | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bluesky | ✅ | — | ✅ | ✅ | ✅ | ✅ | — |
| rss | — | ✅ | ✅ | ✅ | ✅ | — | — |
| instagram (disabled) | ✅ | — | ✅ | ✅ | ✅ | ✅ | ✅ |

`avatar`/`media` columns above are the `show_avatar`/`show_media` toggles the
dashboard should only expose when the platform supports them.

---

## 3. Per-platform quota (NEW) ⚠️

A guild may follow a limited number of **distinct accounts per platform**:

| Tier | Accounts per platform |
|---|---|
| Free | **1** |
| Premium (`PREMIUM` guild attribute) | **5** |

Source of truth: `modules/social_notifications.py::platform_subscription_limit`
(`FREE_PER_PLATFORM_LIMIT = 1`, `PREMIUM_PER_PLATFORM_LIMIT = 5`).

Enforcement (bot, in `add_subscription` — also covers backend Option A tasks):
1. Count existing rows for `(guild_id, platform)`.
2. Resolve the target via the service.
3. If at/over the cap **and** the resolved `target_id` is **not** already
   followed by the guild → **reject** and reconcile the service subscription
   (unsubscribe if now orphaned). Re-adding an already-followed target is an
   **update** and is always allowed.

- The **backend** (Option B, direct table writes) MUST enforce the same cap and
  count distinct `(guild_id, platform)` rows.
- The **dashboard** should enforce/preview it client-side (disable "Add" / show
  an upsell when the guild is at the cap), but server-side enforcement is what
  guarantees it. **Keep the numbers (1/5) in sync.**

---

## 4. Error codes (2 new)

Returned by `add_subscription` / the `social_subscribe_result` payload:

| Code | When |
|---|---|
| `limit_reached_free` | Free guild already follows 1 account on that platform. |
| `limit_reached_premium` | Premium guild already follows 5 on that platform. |

The result dict also carries `"limit": <int>` for these. Existing codes
(`channel_not_found`, `timeout`, `service_unavailable`, …) are unchanged.

---

## 5. `moddy:tasks` payloads (3 new optional fields)

Option A delegation is unchanged in shape; new optional passthrough fields:

| Task type | New optional fields |
|---|---|
| `social_subscribe` | `embed_color?` (int), `show_avatar?` (bool), `show_media?` (bool) |
| `social_update` | `embed_color?` (int), `show_avatar?` (bool), `show_media?` (bool) |

`social_unsubscribe` / `social_remove` unchanged. The result published on
`moddy:dashboard` is unchanged except it can now carry the new `limit_reached_*`
error codes.

---

## 6. Rendering contract (so the dashboard preview matches the bot)

When the bot posts a notification it builds (Components V2):

1. **Role mentions** as a top-level `TextDisplay` **above** the container (only
   if `mention_role_ids` is non-empty; `AllowedMentions(roles=True)`).
2. A **Container** with `accent_colour = embed_color ?? platform_color`:
   - the **message** (custom or platform default, placeholders substituted) —
     wrapped in a `Section` with the author avatar as a `Thumbnail` accessory
     when `show_avatar` is on and an avatar exists; otherwise a plain
     `TextDisplay`;
   - a `MediaGallery` with the large media when `show_media` is on and media
     exists.
3. **No other text.** No buttons.

Platform brand colours (defaults): youtube `#FF0000`, twitch `#9146FF`,
bluesky `#1185FE`, rss `#EE802F`, instagram `#E1306C`.

---

## 7. Dependency

The bot now requires **discord.py ≥ 2.7.1** (modal `Checkbox` / `CheckboxGroup`).
This only affects the bot runtime, not the backend/dashboard — listed for
completeness.

---

## Checklist for the backend/dashboard

- [ ] Add `embed_color` / `show_avatar` / `show_media` to the table model + any
      ORM/serializer (int + 2 bools, defaults `NULL` / `TRUE` / `TRUE`).
- [ ] Update the message editor: single field, default = platform template,
      title included, placeholder cheat-sheet, `{timestamp}` documented.
- [ ] Hex colour picker ↔ int conversion; default to the platform colour.
- [ ] Only show `show_avatar` / `show_media` toggles for supported platforms.
- [ ] Enforce the per-platform quota (1 free / 5 premium) on create.
- [ ] Handle the `limit_reached_free` / `limit_reached_premium` results.
- [ ] If using Option A: optionally pass the 3 new fields in the task payload.
- [ ] If using Option B: include the 3 columns and enforce the quota yourself.
