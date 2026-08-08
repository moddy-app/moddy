# Bot Customization

Makes Moddy look like the server's own bot. Everything is **per-guild**: a
server never sees another server's customization.

| | Free | Premium |
|---|---|---|
| Nickname | ❌ | ✅ |
| Avatar | ❌ | ✅ |
| Bio | ❌ | ✅ |
| Name style (font, effect, colours) | ✅ | ✅ |

Premium here means "this **guild** is covered by an active subscription" —
`utils.subscription.is_guild_premium`, see [PREMIUM.md](PREMIUM.md). It is not
a guild attribute.

Files:

- [`modules/bot_customization.py`](../modules/bot_customization.py) — module,
  validation, the single write path, the backend task handler
- [`modules/configs/bot_customization_config.py`](../modules/configs/bot_customization_config.py)
  — `/config` panel + the two Modals V2
- [`tests/test_bot_customization.py`](../tests/test_bot_customization.py) — validation tests

---

## The Discord endpoint

Everything goes through **`PATCH /guilds/{guild.id}/members/@me`** ("modify
current member"), called with the raw HTTP layer because discord.py exposes no
wrapper for the style fields:

```python
route = discord.http.Route("PATCH", "/guilds/{guild_id}/members/@me", guild_id=guild_id)
await bot.http.request(route, json=payload, reason="Bot customization (config) by 1234")
```

`reason` becomes the `X-Audit-Log-Reason` header, so every change is visible in
the server's own audit log — not just in our technical logs.

### Documented fields

| Field | Type | Notes |
|---|---|---|
| `nick` | `?string` | max 32 chars. Requires `CHANGE_NICKNAME`. |
| `avatar` | `?string` | [data URI, base64](https://docs.discord.com/developers/reference#image-data) |
| `banner` | `?string` | data URI — **not exposed by the module** (see *Not implemented* below) |
| `bio` | `?string` | member bio, 190 chars including our attribution line |

### Undocumented fields — name styles

Not in Discord's documentation; tested and working on our instance.

| Field | Type |
|---|---|
| `display_name_font_id` | `?int` |
| `display_name_effect_id` | `?int` |
| `display_name_colors` | `?int[]` (24-bit ints, 1 or 2 entries) |

**Effects** (`EFFECTS` in the module — the value is how many colours the API
requires):

| ID | Name | Rendering | Colours |
|---|---|---|---|
| `2` | Gradient | fades between two colours, left → right | **exactly 2** |
| `3` | Neon | glowing outline around the letters | 1 |
| `4` | Toon | light fill + visible stroke | 1 |
| `5` | Pop | coloured drop shadow behind the letters | 1 |

**Fonts** (`FONTS` in the module):

| ID | Name | Style |
|---|---|---|
| `3` | Cherry Bomb | bubbly, playful |
| `4` | Chicle | round, jellybean |
| `6` | MuseoModerno | geometric, modern |
| `7` | Neo-Castel | gothic, medieval |
| `8` | Pixelify Sans | pixel, 8-bit retro |
| `10` | Sinistre | dark, gothic, jagged |
| `12` | Zilla Slab | slab-serif, balanced |

Gotchas, all encoded in `normalize_style` / `_style_payload`:

- The API accepts font ids `1`–`12` and effect ids `1`–`6`, but only the ids
  above render something distinguishable. Anything else is refused by us
  before it reaches Discord.
- Gradient with fewer than 2 colours → `400`.
- **Resetting means sending `null` on all three fields** — `0` or `[]` are
  refused.
- **There is no GET.** The active style cannot be read back from Discord, which
  is why we store it (see the schema below).
- The style does **not** reliably survive a bot restart, unlike the nickname,
  the avatar and the bio which Discord stores. Hence `resync_style()`.

---

## Attribution line

A customized bio always ends with:

```
<a:Rocket:1535783839870353499> Powered by @**Moddy**
```

It is re-appended on every write (`build_bio`) and cannot be removed by the
server. Only the *user portion* is stored in the DB and shown back in the
modal, so the suffix never gets duplicated when a server edits its bio.

The 190-character member-bio budget is shared: `MAX_BIO_LENGTH` is
`190 - len(attribution) - len("\n")` = **137** characters for the server.
`tests/test_bot_customization.py` pins that arithmetic — if the attribution
text changes, the test is what catches the overflow.

Clearing the bio clears everything, attribution included: a server that opted
out of customization is not carrying our line.

---

## Storage

`guilds.data.modules.bot_customization`:

```json
{
  "nickname": "Guardian",
  "bio": "Le bot de notre serveur",
  "avatar_hash": "a_1c9e…",
  "avatar_source": null,
  "style": { "font_id": 7, "effect_id": 2, "colors": [16711680, 255] },
  "updated_at": "2026-08-08T14:31:07+00:00",
  "updated_by": 942386103000000000
}
```

| Key | Meaning |
|---|---|
| `nickname` | user-set nickname, `null` = Moddy's default name |
| `bio` | **user portion only**, attribution excluded |
| `avatar_hash` | hash returned by the API — used to build the CDN preview URL. The image itself is never stored. |
| `avatar_source` | last source URL for a dashboard-set avatar, informational |
| `style.colors` | 24-bit ints (not hex strings) |

The module is `enabled` when any of these is set — there is no separate on/off
switch, an empty configuration is simply off.

Guild avatar preview URL:
`https://cdn.discordapp.com/guilds/{guild_id}/users/{bot_id}/avatars/{hash}.png`
(`.gif` when the hash starts with `a_`).

---

## The single write path

Everything — `/config`, the dashboard, a reset — goes through

```python
await module.apply_customization(
    nickname=..., bio=..., avatar=(bytes, content_type), style={...},
    actor_id=..., source="config" | "dashboard",
)
```

Each field defaults to the `UNSET` sentinel (leave alone); passing `None`
resets it. In one call it:

1. validates (length, colours, font/effect ids, image type and size),
2. builds one payload and issues a **single** PATCH,
3. persists through `module_manager.save_module_config` (which reloads the live
   instance),
4. emits the technical log — **on failure too**.

Failures raise `CustomizationError(code, detail)`. Codes are stable and double
as i18n keys (`modules.bot_customization.errors.<code>`) and as API error codes
for the dashboard:

`premium_required`, `nickname_too_long`, `bio_too_long`, `invalid_color`,
`invalid_font`, `invalid_effect`, `gradient_needs_two_colors`,
`effect_needs_color`, `invalid_image_type`, `image_too_large`,
`image_download_failed`, `missing_permissions`, `rate_limited`,
`rejected_by_discord`, `discord_error`, `save_failed`, `empty_update`,
`guild_not_found`, `internal_error`.

### Avatar guard rails

- allowed: `image/png`, `image/jpeg`, `image/gif`, `image/webp`
- max **8 MiB**, enforced on the attachment size *and* on the downloaded bytes
  (a dashboard URL can lie about `Content-Length`)
- downloaded into memory, base64-encoded, never written to disk

---

## Technical logs

Category **`bot_customization`** → `LOG_WEBHOOK_BOT_CUSTOMIZATION` (falls back
to `LOG_WEBHOOK_DEFAULT`). One card per attempt, successful or not:

```
### Bot Customization
Guild `Ma communauté` `123456789`
Source `config` • By `942386103000000000`
nickname `Guardian`
avatar `48210 bytes (image/png)`
✅ Applied
```

See [TECHNICAL_LOGS.md](TECHNICAL_LOGS.md).

---

## UI (`/config` → Bot Customization)

- **No draft/save cycle.** A modal submit patches Discord immediately, because
  the change is instantly visible in the member list — a pending "Save" button
  would be lying. The panel therefore has no Save/Cancel, only Edit / Reset per
  section and a global Reset.
- **Identity modal** (premium): nickname `TextInput`, bio `TextInput`
  (paragraph), avatar `FileUpload`, plus a `TextDisplay` warning about the
  attribution line. Leaving the upload empty keeps the current avatar — use
  *Reset* to remove it.
- **Style modal** (everyone): font `Select`, effect `Select`, two colour
  `TextInput`s. Colour 2 is only read by the gradient effect.
- Non-premium servers see the identity section rendered but **locked**, with a
  link to the dashboard — the feature is discoverable rather than hidden.
- Premium is re-checked on the button click **and** on the modal submit: a
  panel left open across a subscription lapse must not grant anything.
- The panel is a persistent view (`BotCustomizationConfigView`, guild
  `manage_guild` auth re-derived from the interaction). The modals are not —
  modals never are, see [PERSISTENT_VIEWS.md](PERSISTENT_VIEWS.md).

---

## Startup resync

`on_enable()` → `resync_style()` re-applies the stored name style **once per
process per guild** (`bot._bot_customization_styled`), because Discord does not
reliably keep it across a bot restart. Nickname, avatar and bio are never
re-applied — Discord stores those.

---

## Dashboard integration (Redis)

The dashboard cannot call the endpoint itself (it is the *bot's* own member
profile), so it delegates: a task on the `moddy:tasks` stream, a result on the
`moddy:dashboard` Pub/Sub channel. Full contract, including the field-presence
semantics and every error code, in the backend section of this repository's
integration notes — the bot side is `ModdyBot._process_bot_customization_task`
and `BotCustomizationModule.handle_backend_task`.

Task (stream `moddy:tasks`):

```json
{
  "type": "bot_customization_update",
  "guild_id": "123456789",
  "payload": "{\"request_id\":\"…\",\"actor_id\":\"…\",\"nickname\":\"Guardian\",\"style\":{...}}"
}
```

Result (channel `moddy:dashboard`):

```json
{
  "type": "bot_customization_update_result",
  "request_id": "…", "guild_id": 123456789,
  "ok": true, "nickname": "Guardian", "avatar_hash": "a_1c9e…", "style": {...}
}
```

**Key presence is the contract**: an absent key leaves the field untouched, an
explicit `null` resets it. Premium is re-checked bot-side on every dashboard
task — the backend's check is a UX filter, not a trust boundary.

---

## Not implemented (deliberately)

- **Banner.** The endpoint supports `banner`, and the module's write path would
  take it with a two-line change, but it was not part of the requested scope.
- **Automatic revert on premium loss.** When a guild stops being premium the
  stored identity stays applied; the panel simply locks. Reverting would need a
  backend-driven sweep on `premium_deactivated`.
