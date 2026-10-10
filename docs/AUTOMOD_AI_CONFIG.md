# Automod AI — configuration schema (backend / dashboard integration)

> Contract between the **bot** and the **backend / dashboard** for configuring the
> `automod_ai` module. Pipeline internals → [AUTOMOD_AI.md](AUTOMOD_AI.md).
> Generic module mechanics → [MODULE_SYSTEM.md](MODULE_SYSTEM.md).
> Pub/Sub & Redis conventions → [BACKEND-INTEGRATION.md](BACKEND-INTEGRATION.md).

## 1. Identity

| Field | Value |
|---|---|
| `MODULE_ID` | `automod_ai` |
| Display name | `Automod AI` |
| Code | `modules/automod_ai.py` (load / validate / defaults), `automod/infractions.py` (`sanctions`), `automod/custom_infractions.py` (`infractions_perso`), `automod/presets.py` (wizard levels). Config UI `modules/configs/automod_ai_config.py` (home + wizard), `automod_ai_sanctions_config.py`, `automod_ai_advanced_config.py`, `automod_ai_common.py` |
| Storage | `guilds.data.modules.automod_ai` (JSONB) |

**Rename note.** This module was `automod` until 2026-08. The id is now
`automod_ai` because a separate **classic (rule-based) automod** module will take
the `automod` id. On the next load of a guild, the bot migrates a config still
stored under `data.modules.automod` to `data.modules.automod_ai` and empties the
old key (`ModuleManager.LEGACY_MODULE_IDS`). A config already present under
`automod_ai` always wins. **The backend must read and write `automod_ai` only**;
treat any remaining `data.modules.automod` payload as legacy read-only data.

## 2. Storage

```sql
CREATE TABLE guilds (
    guild_id   BIGINT PRIMARY KEY,
    attributes JSONB DEFAULT '{}'::jsonb,
    data       JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

Read:

```sql
SELECT data->'modules'->'automod_ai' AS config
FROM guilds WHERE guild_id = $1;
```

Write (whole object — the bot always stores the full config, never a patch):

```sql
UPDATE guilds
SET data = jsonb_set(data, '{modules,automod_ai}', $2::jsonb, true),
    updated_at = NOW()
WHERE guild_id = $1;
```

Deleting a config = writing `{}` (that is what the "Delete" button does).

## 3. Config schema

```jsonc
{
  "enabled": false,                 // bool  — master switch
  "indications": "",                // string ≤ 3000 — server guidance injected in the AI prompt
  "notify_channel_id": null,        // int|null — REQUIRED for the module to run
  "ignore_moderators": true,        // bool  — skip members with manage_messages
  "severity": 3,                    // int 1..5
  "max_action": "ban",              // "warn" | "mute" | "ban" — hard ceiling of the barème
  "categories_desactivees": [],     // string[] — LEGACY kill-switch, read-only (see §3.3 / §10)
  "sanctions": {},                  // object — per-infraction sanction policy (§3.1)
  "infractions_perso": [],          // object[] — server-defined infractions, max 10 (§3.2)
  "dry_run": false,                 // bool  — shadow mode: decide, apply nothing
  "features": {
    "content": {
      "enabled": false,             // bool
      "exempt_roles": [],           // int[] role ids (≤ 25 via the UI)
      "exempt_channels": []         // int[] channel ids (≤ 25 via the UI)
    },
    "image_nsfw": {                 // sexually explicit images (Google SafeSearch)
      "enabled": false,
      "exempt_roles": [],
      "exempt_channels": []
    },
    "image_gore": {                 // gore / graphic violence (same SafeSearch call)
      "enabled": false,
      "exempt_roles": [],
      "exempt_channels": []
    },
    "image_scam": {                 // crypto / fake-giveaway screenshots
      "enabled": false,
      "exempt_roles": [],
      "exempt_channels": [],
      "scan_all": false             // bool — OCR every image, not only risky ones
    },
    "profile": {                    // member profiles: avatar / banner / names
      "enabled": false,             // run by member events, never by messages
      "exempt_roles": [],           // int[] — members holding one are not checked
      "exempt_channels": [],        // int[] — unused (a profile has no channel), keep the shape
      "avatar": true,               // bool — SafeSearch on the avatar (server avatar wins)
      "banner": true,               // bool — SafeSearch on the banner (REST, throttled)
      "pseudo": true                // bool — nickname / display name / username through the AI funnel
    }
  }
}
```

### Field reference

| Field | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `false` | Master switch. Not sufficient on its own — see §4. |
| `indications` | string | `""` | Server guidance ("no insults even as a joke", "English only"…). Max **3000** chars. Embedded verbatim in the model's system prompt, so it **must** pass the prompt-injection safety check (§6). |
| `notify_channel_id` | int \| null | `null` | **Mandatory.** Text or announcement channel where every automod decision, shadow card and budget notice is posted. Without it the module never runs. |
| `ignore_moderators` | bool | `true` | Members with `manage_messages` are skipped entirely. |
| `severity` | int 1–5 | `3` | Detection dial: scales the embedding routing threshold (1 = 0.62 … 5 = 0.35) and the barème's global cran shift. Values outside 1–5 are clamped. |
| `max_action` | enum | `"ban"` | Hardest sanction the automod may apply: `warn` < `mute` < `ban`. The barème caps itself at this level. |
| `categories_desactivees` | string[] | `[]` | **LEGACY, superseded by `sanctions[<id>].enabled = false`.** Still read: every listed id (any catalogue id or `perso:<id>`) is treated as disabled unless `sanctions[<id>]` carries an explicit `enabled`. A decision in a disabled type is downgraded to deletion only. The bot's panel folds it into `sanctions` on load and **never writes the key back**; new writes (backend/dashboard) must not set it — see §3.3. |
| `sanctions` | object | `{}` | Per-infraction sanction policy, keyed by infraction id. Absent key = Moddy defaults. Shape and bounds → §3.1. |
| `infractions_perso` | object[] | `[]` | The server's own infraction types (max 10). Shape and bounds → §3.2. |
| `dry_run` | bool | `false` | **Shadow mode**: the whole funnel + barème run, but nothing is applied (no delete, no sanction, no case, no DM). A SIMULATION card with ✅/❌/⚠️ annotation buttons is posted to `notify_channel_id` instead. |
| `features.content.enabled` | bool | `false` | The AI message detector (text funnel). |
| `features.content.exempt_roles` | int[] | `[]` | Members holding any of these roles are not moderated. |
| `features.content.exempt_channels` | int[] | `[]` | These channels (and threads whose parent is listed) are not moderated. |
| `features.image_nsfw.enabled` | bool | `false` | Sexually explicit image detection (Google SafeSearch, shared monthly budget). Age-restricted channels are always skipped. |
| `features.image_gore.enabled` | bool | `false` | Gore / graphic-violence image detection (category `contenu_choquant`). Reads the same SafeSearch result as `image_nsfw` (one call per image). Age-restricted channels are always skipped. |
| `features.image_scam.enabled` | bool | `false` | Scam-screenshot detection (known hash → OCR → AI funnel). |
| `features.image_scam.scan_all` | bool | `false` | OCR every image instead of only the ones the free pre-rules flag as risky. No UI yet (ops/backend-set); the panel preserves it. |
| `features.<image_*>.exempt_*` | int[] | `[]` | Same shape as `content`. The bot's own panel writes **one** exemption list onto every feature (copied from `content`); a dashboard should keep them identical too (§3.3). |
| `features.profile.enabled` | bool | `false` | Member-profile moderation (avatar, banner, names). Counts as a feature for the "module runs" rule. |
| `features.profile.avatar` / `.banner` / `.pseudo` | bool | `true` | Which profile checks run. Banners are only checked on join and on any other profile event (Discord emits no reliable event for a banner-only change). |
| `features.profile.exempt_roles` | int[] | `[]` | Members holding any of these roles are not checked (the bot's panel writes the shared list). `exempt_channels` is kept for the shape, unused (the panel writes `[]`). |

`features` is an **open map keyed by feature id** (`content`, `image_nsfw`,
`image_gore`, `image_scam`, `profile` today): future detectors (anti-link, anti-spam…) will add sibling blocks with the same `{enabled, exempt_roles,
exempt_channels}` shape. Unknown feature ids are **rejected** by validation, so
the backend must not invent keys. A missing feature block means "off with defaults"
(`profile.avatar/banner/pseudo` default to `true`).

> There is no `features.situation` block any more. The diffuse-harassment
> ("situation") feature was removed in 2026-08; a stored `features.situation`
> object is ignored by the bot and **rejected by validation** (unknown feature id),
> so the backend must drop it on the next write.

### 3.1 `sanctions` — per-infraction policy

```jsonc
"sanctions": {
  "insulte": {                       // key = infraction id (catalogue id or "perso:<id>")
    "enabled": true,                 // bool, default true — false = deletion only (kill-switch)
    "mode": "auto",                  // "auto" (default: Moddy's policy) | "perso" (the server's ladder)
    "crans": {"basse": 1, "moyenne": 1, "haute": 2, "critique": 3},  // int 0..7 per gravity
    "recidive": true                 // bool, default true — perso mode only: escalate on repeat offences
  },
  "profil_avatar_nsfw": { "action_profil": "hold" }   // profile types only
}
```

An **absent entry is the default** ("auto" = Moddy's policy); the panel's *Reset to
default* deletes the key. In `auto` mode `crans`/`recidive` are ignored (the
barème uses its built-in floors, severity shift, veteran clemency, fresh-account
malus, recidivism). In `perso` mode the server's `crans` are the floor per gravity
(missing gravities fall back to the type's default floor) and the barème **skips**
the severity shift, veteran clemency and fresh-account malus; recidivism escalation
stays on unless `recidive` is `false`. The confidence caps and the `max_action`
ceiling always apply. See [AUTOMOD_AI.md §2bis](AUTOMOD_AI.md).

The ladder a cran maps to: `0` delete only · `1` warn · `2..6` mute 2 h / 12 h / 48 h / 168 h / 672 h · `7` ban.

**Valid ids** (`automod/infractions.py`, the single catalogue):

| Family | Ids | `crans` defaults (`basse/moyenne/haute/critique`) |
|---|---|---|
| message | `insulte` 1/1/2/3 · `menace` 2/3/6/7 · `harcelement` 1/2/3/5 · `harcelement_sexuel` 3/5/7/7 · `haine_discrimination` 2/4/6/7 · `incitation_automutilation` 3/5/7/7 · `doxxing` 4/6/7/7 · `arnaque_scam` 7/7/7/7 · `violation_indications` 0/1/2/4 | |
| image | `contenu_nsfw` 0/2/3/5 · `contenu_choquant` 0/1/2/4 | |
| profile | `profil_avatar_nsfw` 0/2/3/5 · `profil_avatar_choquant` 0/1/2/4 · `profil_pseudo` 1/1/2/3 | |
| custom | `perso:<id>` where `<id>` is the `id` of an `infractions_perso` entry | 0/1/2/4 |

`arnaque_scam` is banned on sight (the medium-confidence cap and veteran clemency never apply); `harcelement_sexuel`, `incitation_automutilation` and `doxxing` are *sensitive* (no veteran clemency at `haute`+). These flags are not configurable.

**`action_profil`** (profile types only — `profil_avatar_nsfw`, `profil_avatar_choquant`, `profil_pseudo`; rejected on any other id):

| Value | Default for | Effect |
|---|---|---|
| `hold` | the two avatar types (not valid for `profil_pseudo`) | timeout until the profile is fixed (case + `automod_profile_holds` row) |
| `rename_warn` | `profil_pseudo` | reset the displayed nickname + warn |
| `alert` | — | alert card only, no sanction |
| `bareme` | — | the cran ladder above, like a message (minus the deletion) |

Normalisation on load (`normalize_sanctions` + `AutomodModule.load_config`): unknown
ids and non-object entries are dropped; a `perso:<id>` whose `<id>` is not in
`infractions_perso` is dropped (orphan); a bad `mode` becomes `auto`; crans are
clamped to 0..7 (garbage → the type's default); `enabled`/`recidive` are `true`
unless exactly `false`; `action_profil` outside the four values falls back to the
type's default; `hold` on `profil_pseudo` is coerced to `rename_warn` (and
rejected by validation with `sanctions_action_profil_invalid`).

### 3.2 `infractions_perso` — server-defined infractions

```jsonc
"infractions_perso": [{
  "id": "k3x9",                    // [a-z0-9]{1,12}, unique, stable (generated by the bot: 4 chars), never shown to the AI
  "nom": "Publicité",              // string, 1..40 chars, shown on cards / DMs / cases
  "description": "…",              // string, 1..300 chars, what the AI looks for (reaches the prompt)
  "exemples": ["…"],               // string[] ≤ 5 × 200 chars, optional (reaches the prompt, embedded as references)
  "mots_cles": ["discord.gg/"],    // string[] ≤ 20 × 60 chars, optional (matched by code, never prompted)
  "modele": "pub_serveur"          // null | template id this entry was created from
}]
```

* Max **10** entries. `modele` ∈ `pub_serveur`, `spoilers`, `politique_religion`, `demande_dm`, `langue`, `liens` (or `null`).
* The downstream category is `perso:<id>` everywhere (cases, evidence, `sanctions` keys). Its sanction policy is `sanctions["perso:<id>"]` (default floors `0/1/2/4`, same as `violation_indications`).
* Keywords route a message to the AI without any embedding call (accent/leet/repeat tolerant; a keyword containing `. / : \ @ #` is a literal substring, also with whitespace evasion); descriptions + examples are embedded once and compared with the message's existing vector (threshold `0.50` at severity 3, scaled by `severity`). Routing only *routes*: the AI still decides.
* Text normalisation on load (`custom_infractions.normalize`): entries with a malformed/duplicate `id`, empty `nom` or empty `description` are dropped; text is trimmed, whitespace collapsed, `[DATA:…]` fence markers stripped and cut to its limit; list entries are de-duplicated (case-insensitive) and capped; an unknown `modele` becomes `null`; the list is capped at 10.
* **Every infraction must pass the prompt-injection safety check before being saved** (name + description + examples reach the model's prompt) — see §6.2. The bot's `validate_config` does **not** run it (it is an AI call); the panel and the backend each must. Moddy's own unmodified templates are trusted and skip the check.

### 3.3 Behaviours the dashboard must respect when it writes the config

* **Write the whole object, never `categories_desactivees`.** Set/remove `sanctions[<id>].enabled` instead. If you keep the legacy array in what you write, it keeps working (read and folded by the bot), but the bot's panel will silently drop it at its next save — so a disabled type must also be expressed in `sanctions`.
* **Deleting an infraction = deleting `sanctions["perso:<id>"]` in the same write.** An orphan `perso:*` sanction is rejected by `validate_config` (`Barème d'une infraction personnalisée inconnue (<id>)`), so the save would fail.
* **Exemptions are shared.** The bot's panel edits ONE exemption list (copied from `features.content`) and writes it on every feature (roles only for `profile`; `profile.exempt_channels` is always `[]`). A dashboard that stores different lists per feature will see them flattened the next time an admin saves from Discord; keep them identical.
* The panel also keeps `scan_all` (no UI: ops/backend-set), rewrites unknown keys of `features.<id>` away, and never writes `categories_desactivees`.
* Wizard levels (`automod/presets.py`, **Light / Balanced / Strict**) are plain configs: Light = `content`+`image_nsfw`+`image_scam`, severity 2, `sanctions.insulte = {enabled: true, mode: "perso", crans: {basse: 0, moyenne: 0, haute: 2, critique: 3}, recidive: true}`; Balanced = + `image_gore`, severity 3, defaults; Strict = + `profile` (avatar, banner, pseudo), severity 4. The wizard applies a level **switched off and in `dry_run: true`** (`enabled: false`, `notify_channel_id` set, existing indications / custom infractions / exemptions / unrelated sanctions kept); its last step switches the module on, in simulation (recommended) or in real mode. A dashboard offering levels should reproduce the same data (`preset_config`).

## 4. When is the module actually running?

The bot computes `enabled` at load time as:

```
running = config.enabled
          AND any(features[*].enabled)      # content, image_nsfw, image_gore, image_scam or profile
          AND notify_channel_id is not null
```

`profile` counts as a feature: a server with only `features.profile.enabled`
runs (it is driven by member events instead of messages). A dashboard should
mirror this to display the real state, and warn when the alert channel is
missing (that is the most common misconfiguration). The bot's panel shows four
states: *Active* (running, `dry_run` false), *Simulation* (running, `dry_run`
true), *Off*, *Needs setup* (no alert channel).

Also skipped at runtime, whatever the config: bots, servers under a global
`restrict`/`ban` sanction, and (per feature) exempt roles/channels and members
with `manage_messages` when `ignore_moderators`.

## 5. Validation rules (bot-side, `AutomodModule.validate_config`)

The backend should apply the same rules before writing; the bot re-validates on
save from its own panel and refuses invalid configs. Checks run in this order and
the **first failure** is returned (French strings, verbatim):

| # | Rule | Error |
|---|---|---|
| 1 | the guild is known to the bot | `Serveur introuvable` |
| 2 | `notify_channel_id` (or legacy `log_channel_id`), when not null, resolves to a text channel of the guild | `Salon d'alertes invalide` |
| 3 | `len(indications) <= 3000` (or legacy `rules`) | `Les indications sont trop longues (max 3000 caractères)` |
| 4 | `severity`, when present, is an integer 1..5 (**not** clamped here) | `Niveau de sévérité invalide (1 à 5)` |
| 5 | `max_action`, when present, ∈ `warn`/`mute`/`ban` | `Action maximale invalide` |
| 6 | `sanctions` passes `validate_sanctions` | `Barème personnalisé invalide (<code>)` |
| 7 | `infractions_perso` passes `custom_infractions.validate` | `Infractions personnalisées invalides (<code>)` |
| 8 | every `perso:<id>` key of `sanctions` is the id of an `infractions_perso` entry | `Barème d'une infraction personnalisée inconnue (<key>)` |
| 9 | every key of `features` is a known feature id | `Fonctionnalité inconnue : <id>` |
| 10 | `features.profile.avatar` / `.banner` / `.pseudo`, when present and non-null, are booleans | `Option de profil invalide : <key>` |

`<code>` of rule 6 (`automod/infractions.py::validate_sanctions`):

| Code | Meaning |
|---|---|
| `sanctions_invalid` | `sanctions` is not an object (`null` is accepted) |
| `sanctions_unknown_id` | a key is neither a catalogue id nor a well-formed `perso:<[a-z0-9]{1,12}>` |
| `sanctions_entry_invalid` | an entry is not an object |
| `sanctions_mode_invalid` | `mode` ∉ `auto`/`perso` |
| `sanctions_flag_invalid` | `enabled` or `recidive` is not a boolean |
| `sanctions_crans_invalid` | `crans` is not an object, has a key ∉ `basse`/`moyenne`/`haute`/`critique`, or a value that is not an integer (booleans refused) |
| `sanctions_cran_range` | a cran outside 0..7 |
| `sanctions_action_profil_invalid` | `action_profil` ∉ `hold`/`alert`/`rename_warn`/`bareme`, set on a non-profile type, or `hold` on `profil_pseudo` |

`<code>` of rule 7 (`custom_infractions.validate`, also returned per entry by the
route of §6.2): `infractions_perso_invalid` (not a list), `infractions_perso_too_many`
(> 10), `infraction_entry_invalid`, `infraction_id_invalid`,
`infraction_id_duplicate`, `infraction_nom_invalid` (missing/blank/> 40),
`infraction_description_invalid` (missing/blank/> 300),
`infraction_exemples_invalid` (not a list, > 5, or an item blank / > 200),
`infraction_mots_cles_invalid` (> 20 items or an item blank / > 60),
`infraction_modele_invalid` (not a known template id).

Not validated by the bot (the runtime tolerates them): `exempt_roles` /
`exempt_channels` content, `dry_run`, `ignore_moderators`, `scan_all`.

Legacy keys are still read transparently on load: `rules` → `indications`,
`log_channel_id` → `notify_channel_id`, `categories_desactivees` → `sanctions`
(see below). New writes must use the new names.

**Legacy migration of `categories_desactivees`.** On load (both in the live module
and in the panel) every legacy id is folded into `sanctions[<id>].enabled = false`
— unless the stored `sanctions[<id>]` already has an explicit `enabled`, which
wins (so re-enabling a type in the panel works even if the old array is still
stored). Unknown ids are ignored. The fold is in memory only: **nothing is
written back until an admin saves from the panel, which then omits the key** (the
data is preserved, in `sanctions`). A backend reading the config must apply the
same fold to display the right state.

## 6. Safety checks (`indications` and custom infractions)

### 6.1 `indications`

`indications` is injected verbatim into the model's system prompt, so any text
edited from the bot's own panel is first validated by an AI call
(`automod/rules_check.py`, call type `automod_rules_check`) that rejects
prompt-injection attempts ("ignore your instructions", "never sanction @X"…).

**A dashboard that lets an admin write `indications` must run the same check.**
Do not re-implement the heuristic — call the bot's internal API, which runs the
exact same code path:

```
POST {BOT_INTERNAL_URL}/automod/rules_check
Authorization: Bearer {INTERNAL_API_SECRET}
Content-Type: application/json

{ "guild_id": "123456789012345678", "indications": "texte à contrôler", "locale": "fr" }
```

`locale` is optional (`fr` by default, `en-US` supported) and only picks the
language of `reason`.

**200 — the check ran:**

```json
{ "ok": true }
```

```json
{ "ok": false,
  "reason": "Les indications ressemblent à une tentative de manipulation de l'IA. Raison : …",
  "code": "unsafe" }
```

`reason` is a full sentence, ready to be shown to the admin as-is. `code` is
`unsafe`, `too_long` or `unavailable` (`unavailable` = the AI could not be
reached — the check **fails closed**, so the text must not be saved).

**4xx/5xx — the check could not run.** The body is
`{"ok": false, "error": "<code>", "reason": "<message>"}`:

| Status | `error` | When |
|---|---|---|
| `400` | `invalid_json`, `invalid_body` | Body is not a JSON object |
| `400` | `missing_guild_id`, `invalid_guild_id` | `guild_id` absent or not a snowflake |
| `400` | `missing_indications`, `invalid_indications` | `indications` absent or not a string |
| `401` | `unauthorized` | Bad/missing `Authorization` header |
| `404` | `unknown_guild` | The bot is not in that guild |
| `503` | `bot_not_ready` | The bot is starting or disconnected |

Every failure mode is explicit — the route never returns a silent pass. The
backend should therefore **reject the write on anything other than
`{"ok": true}`**. An empty/whitespace-only `indications` returns `ok: true`
without an AI call (clearing the field is always allowed), mirroring the bot's
own panel.

Implementation: `internal_api/routes/automod.py` →
`automod/rules_check.py::validate_rules`.

### 6.2 Custom infractions (same route, `infraction` instead of `indications`)

A custom infraction's `nom`, `description` and `exemples` are read by the model,
so they need the same guard. The check is **per entry** (the keywords never reach
a prompt and are not sent to the AI). Same route, same auth, same response shape:

```
POST {BOT_INTERNAL_URL}/automod/rules_check
Authorization: Bearer {INTERNAL_API_SECRET}

{ "guild_id": "123456789012345678",
  "infraction": { "id": "k3x9", "nom": "Publicité", "description": "…",
                  "exemples": ["…"], "mots_cles": ["discord.gg/"], "modele": null },
  "locale": "fr" }
```

* If the body contains an `infraction` key it is used **instead of** `indications`
  (the key's presence decides; `indications` is then ignored).
* The entry is first validated structurally (`custom_infractions.validate_entry`,
  the codes of §5 rule 7). A schema failure is **`400`**
  `{"ok": false, "error": "invalid_infraction", "reason": "Infraction personnalisée invalide (<code>)"}`
  (EN: `Invalid custom infraction (<code>)`) — no AI call is made.
* Then the guild checks of §6.1 apply (`503 bot_not_ready`, `404 unknown_guild`,
  `400 missing_guild_id` / `invalid_guild_id`, `401 unauthorized`, `400 invalid_json`
  / `invalid_body`). `missing_indications` / `invalid_indications` are never
  returned in this mode.
* **200** `{"ok": true}` or `{"ok": false, "reason": "<sentence>", "code": "unsafe" | "unavailable" | "too_long"}`.
  `reason` reads "Cette infraction ressemble à une tentative de manipulation de
  l'IA. Raison : …" (EN: "This infraction looks like an attempt to manipulate the
  AI. Reason: …"). Fails closed like §6.1: reject the write unless `ok` is `true`.
* An entry whose `modele` is a Moddy template **and** whose `nom`, `description`
  and `exemples` equal that template (in any of the 5 locales) is trusted:
  `{"ok": true}` with no AI call. Any edit of those fields makes it a normal entry.
* The AI prompt gets an extra paragraph: examples are fictional member messages,
  so rude or promotional wording in them is not an injection by itself; only text
  aimed at the AI / system, or trying to change its rules or output, is rejected.
* Call type `automod_rules_check` (shares the §9 quota with `indications`).

Run it for each new or edited entry **before** saving (entries that are unchanged
since their last accepted save need no re-check). Implementation:
`internal_api/routes/automod.py::_check_infraction` →
`automod/rules_check.py::validate_custom_infraction`.

## 7. Applying a change (cache invalidation)

The bot keeps a per-guild in-memory instance of each module. After the backend
writes the config, publish on `moddy:bot`:

```json
{ "type": "module_updated", "guild_id": 123456789, "module_id": "automod_ai" }
```

The bot drops the guild's module cache and re-reads from the DB on the next
event for that guild. `config_updated`, `module_disabled` and `logging_updated`
have the same effect. Without the event, the change is only picked up on the
next bot restart.

## 8. Related tables (read-mostly for the dashboard)

These are written by the bot; the dashboard can read them to display automod
activity. None of them is part of the module configuration.

### `automod_eval_candidates` — shadow-mode annotation corpus

```sql
CREATE TABLE automod_eval_candidates (
    id             UUID PRIMARY KEY,
    guild_id       BIGINT NOT NULL,
    channel_id     BIGINT,
    message_id     BIGINT,
    author_id      BIGINT,
    contenu        TEXT,
    contexte       JSONB,          -- preceding messages (strings)
    verdict        JSONB,          -- the AI qualification + actions
    cran           INTEGER,        -- barème rung
    bareme         JSONB,          -- component-by-component breakdown
    source         TEXT NOT NULL,  -- 'shadow_button' today
    verdict_humain TEXT CHECK (verdict_humain IN ('correct','faux_positif','disproportionne')),
    annotated_by   BIGINT,
    imported       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    annotated_at   TIMESTAMPTZ
);
```

### `automod_precedents` — learned per-server jurisprudence

```sql
CREATE TABLE automod_precedents (
    id             BIGSERIAL PRIMARY KEY,
    guild_id       BIGINT NOT NULL,
    contenu_norm   TEXT NOT NULL,
    embedding      BYTEA NOT NULL,     -- float32 vector, normalised
    verdict_humain TEXT NOT NULL CHECK (verdict_humain IN ('non_sanctionnable','sanctionnable')),
    categorie      TEXT,
    gravite        TEXT,
    source         TEXT NOT NULL,      -- appel_accepte | appel_refuse | bouton_fp | bouton_ok
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Capped at 500 rows per guild (oldest evicted). Fed by human rulings (accepted /
refused appeals, shadow-card clicks).

### `automod_label_items` — Moddy team labeling queue (global)

`kind` was widened on 2026-10-10 with the three member-profile lanes
(`profil_avatar_nsfw`, `profil_avatar_choquant`, `profil_pseudo`). The migration
is automatic and idempotent at bot start (`db/base.py` drops and re-adds
`automod_label_items_kind_check` when it does not yet list `profil_pseudo`);
nothing to run by hand. A reader must tolerate the new `kind` values.

```sql
CREATE TABLE automod_label_items (
    id              UUID PRIMARY KEY,
    kind            TEXT NOT NULL CHECK (kind IN ('texte','image_scam','image_nsfw','image_gore',
                                                  'profil_avatar_nsfw','profil_avatar_choquant',
                                                  'profil_pseudo')),
    motif           TEXT NOT NULL CHECK (motif IN ('sanction','doute','simulation')),
    guild_id        BIGINT NOT NULL,
    channel_id      BIGINT,
    message_id      BIGINT,
    author_id       BIGINT,
    contenu         TEXT,            -- message text, or the OCR text of an image
    phash           BIGINT,          -- 64-bit perceptual hash, stored signed
    dhash           BIGINT,
    dedup_key       TEXT,
    details         JSONB,           -- bot decision snapshot, image facts, sanctions[]
    case_id         UUID,
    occurrences     INTEGER NOT NULL DEFAULT 1,
    card_channel_id BIGINT,
    card_message_id BIGINT,
    verdict         TEXT CHECK (verdict IN ('sanctionnable','non_sanctionnable','ignore')),
    categorie_humaine TEXT,
    labeled_by      BIGINT,
    labeled_at      TIMESTAMPTZ,
    revoked         BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`details.sanctions` = `[{guild_id, case_id, author_id, message_id, hold_kind?}]` —
every bot sanction the item (and its duplicates) caused; a "non_sanctionnable"
label revokes them all (and deletes the `automod_profile_holds` row named by
`hold_kind`). A member-profile item (`profil_*` kinds) has no channel and no
message: `channel_id` / `message_id` are NULL and `message_id` is null in
`details.sanctions`. Images are **not** stored here (they live only as a spoilered
attachment on the team card).

### `automod_profile_holds` — members timed out until they fix their profile

```sql
CREATE TABLE automod_profile_holds (
    guild_id      BIGINT NOT NULL,
    user_id       BIGINT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('avatar','banner')),
    categorie     TEXT,                 -- profil_avatar_nsfw | profil_avatar_choquant
    asset_key     TEXT,                 -- Discord asset key of the flagged image
    case_id       UUID,                 -- the case the hold opened
    sanction_id   UUID,                 -- its MUTE sanction (case_sanctions.id)
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    timeout_until TIMESTAMPTZ,          -- end of the Discord timeout currently in force
    last_checked  TIMESTAMPTZ,          -- last sweep re-screen (ALTER ... ADD COLUMN IF NOT EXISTS)
    PRIMARY KEY (guild_id, user_id, kind)
);
CREATE INDEX idx_profile_holds_timeout ON automod_profile_holds (timeout_until);
```

**Writer: the bot only** (`db/repositories/automod_profiles.py`, `AutomodProfileRepository`);
the backend/dashboard may read it (e.g. "members held for their profile") but must
never write it: the Discord timeout it mirrors is owned by the bot. Created when the
`hold` profile action times a member out, renewed by the sweep
(`cogs/automod_profiles.py`) while the profile stays flagged, deleted when the
profile is fixed, the sanction is revoked (appeal / team label) or the feature is
switched off.

### `automod_image_hashes`, `automod_learned_references`, `automod_learned_terms` (global)

```sql
CREATE TABLE automod_image_hashes (
    id BIGSERIAL PRIMARY KEY, phash BIGINT NOT NULL, dhash BIGINT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('scam','nsfw','gore')),
    verdict TEXT NOT NULL CHECK (verdict IN ('block','allow')),
    label_item_id UUID, added_by BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT now());

CREATE TABLE automod_learned_references (
    id BIGSERIAL PRIMARY KEY, categorie TEXT NOT NULL, texte TEXT NOT NULL,
    embedding BYTEA NOT NULL,        -- float32 vector, normalised (like automod_precedents)
    label_item_id UUID, added_by BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT now());

CREATE TABLE automod_learned_terms (
    id BIGSERIAL PRIMARY KEY, terme TEXT NOT NULL, categorie TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('words','compact')),
    label_item_id UUID, added_by BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (terme, mode));
```

Written only by the bot (team labels, `/mod automod`). The bot loads them at
startup; a row written by another service is picked up on the next restart
(hashes: within 15 min).

### Custom and profile categories in cases and evidence

The `categorie` stored on automod evidence events, in `automod_label_items.details`
and in the barème breakdown can now be `perso:<id>` (a custom infraction) or one of
`profil_avatar_nsfw` / `profil_avatar_choquant` / `profil_pseudo`. To show a
readable name for `perso:<id>`, look `<id>` up in `infractions_perso` of the
guild; if the entry was deleted since, fall back to the raw id (this is what the bot does too).

**Name snapshot (stable key).** Every automod evidence event
(`case_events.type = 'evidence'`, `payload.source = 'automod'`) carries
`payload.categorie_label`: the infraction's human name **as it was when the
sanction was applied**. Readers that do not have the guild config (personal
`/cases`, the sanctioned member, a deleted infraction) should display it as-is.
- `perso:<id>` → the server's `nom` at that time (sanitised: backticks replaced,
  `@` neutralised; plain text, no markdown). Raw id only if the infraction no
  longer existed at sanction time.
- built-in ids → the i18n name **in the server's language** at that time (a
  dashboard with its own translations may prefer translating `categorie` itself).
- Present on message and profile sanctions alike; absent on cases recorded
  before 2026-10-10 (fall back to `categorie`).
Pinned by `tests/test_automod_profiles.py::test_evidence_snapshots_the_infraction_name`.
Profile evidence has no `message_id` / `jump_url` /
`context_text` and carries `profil: {kind, asset_key, asset_url}` plus an
`origine` of `profil_avatar` / `profil_banniere` / `profil_pseudo`.

### `case_appeals` — appeals of automod sanctions

See [MODERATION_CASES.md](MODERATION_CASES.md). Sanctions themselves live in the
generic `cases` / `case_sanctions` / `case_events` tables with
`issuer_type = 'automod'`.

## 9. Cost controls the backend can drive

| Lever | Where | Effect |
|---|---|---|
| `quota_limits` / `quota_overrides` (scope `guild`, key = guild id) | PostgreSQL | Hard daily cap per call type: `automod_decision`, `automod_decision_mini`, `automod_confirm`, `automod_rules_check`, `automod_image_ocr` (default 300/guild), `automod_safesearch`, `automod_profile_safesearch` (`automod_embed` is not quota-gated). `-1` = unlimited. `automod_profile_safesearch` (member avatars/banners) is **new** and seeded unlimited for `guild` and `global` at bot start (`db/base.py`). |
| `automod:budget:cap:{guild_id}` | Redis | Per-guild override of the daily soft cap (default 300 "call units"; a `mini` call counts 4). Past the cap the funnel degrades (AI reserved for flagrant cases) instead of stopping. |
| `automod:budget:{guild_id}:{YYYYMMDD}` | Redis | The day's consumption counter (read-only for observability). |
| `GOOGLE_VISION_SAFESEARCH_MONTHLY` / `GOOGLE_VISION_SAFESEARCH_PROFILE_MONTHLY` | bot env (Railway) | Two **separate** fail-closed calendar-month SafeSearch budgets (default 1000 each): message images, and member avatars/banners (gateway rule `google_vision/safe_search_profile`). Google's free tier is 1000 units/month **per account/feature**, so with both at 1000 the combined use can exceed the free tier — lower one of them if that matters. |

See [API_GATEWAY.md](API_GATEWAY.md) for the quota model.

### Redis keys written by the automod (bot-owned, do not write)

| Key | TTL | Content |
|---|---|---|
| `automod:avatar:<asset key>` | 30 d | Verdict of a profile image (avatar **or** banner), keyed by Discord asset key — global, shared by every server |
| `automod:pseudo:<sha1 of normalized name>[:<12-hex digest of the guild's indications>]` | 30 d | Verdict of a judged name. Without indications the key is global (shared by every server); a server with indications gets its own suffixed key. A *clean* verdict is reused only by a server whose severity is not stricter |
| `automod:pseudo:allow:<sha1 of normalized name>` | 30 d | Name cleared by the Moddy team ("not sanctionable" label): never flagged again, on any server |
| `automod:profile:allow:<guild>:<asset key \| n:<hash>>` | 90 d | Asset/name cleared for one server by an accepted appeal |
| `automod:profile:done:<guild>:<user>:<asset key \| n:<hash>>` | 7 d (assets) / 24 h (names) | "Already sanctioned for this asset/name" marker; cleared when a hold is lifted. Simulation mode uses a separate `sim:`-prefixed marker that live mode ignores |
| `automod:profile:share:<guild>:<YYYYMMDD>` | 2 d | Profile SafeSearch calls today for the guild (daily per-guild share) |
| `automod:nsfw:share:<guild>:<YYYYMMDD>` | 2 d | Same, for message images (pre-existing) |
| `automod:img:{scam,nsfw,gore}:<phash>`, `automod:img:xp:...` | | Message-image caches (pre-existing, see [AUTOMOD_AI.md](AUTOMOD_AI.md)) |

All are caches or counters: losing them only costs a repeat check. Without Redis
the profile service falls back to per-process memory.

## 10. What the backend / dashboard must change

Summary of everything this version (infraction catalogue, per-type sanctions,
custom infractions, member profiles, panel rewrite) asks of the backend.

**Deployment order: bot first, backend second.** The bot migrates its own tables
at start (`automod_profile_holds`, the widened `automod_label_items.kind`, the
`automod_profile_safesearch` quota rows) and reads old configs (no `sanctions`,
no `infractions_perso`, no `features.profile`) without any change — every new key
is optional with the defaults of §3. A backend deployed *before* the bot would
write `sanctions` / `infractions_perso` / `features.profile` that an older bot
rejects (`Fonctionnalité inconnue : profile` for `features.profile`) or silently
ignores. Set `GOOGLE_VISION_SAFESEARCH_PROFILE_MONTHLY` on the bot (optional,
default 1000) at the same time.

Backend / dashboard checklist:

1. **Schema.** Read/write `sanctions` (§3.1), `infractions_perso` (§3.2) and
   `features.profile` (§3). Keep unknown keys of the stored object you do not
   manage; drop a stale `features.situation`.
2. **Do not write `categories_desactivees`**; express disabled types as
   `sanctions[<id>].enabled = false`. Apply the legacy fold (§5) when reading.
3. **Deleting a custom infraction deletes `sanctions["perso:<id>"]`** in the same
   write (otherwise the bot refuses the save: rule 8).
4. **Run the infraction safety check** (§6.2) on every new or edited custom
   infraction before saving, and the `indications` check (§6.1) as before; reject
   on anything other than `{"ok": true}`. Skip it only for unmodified Moddy templates
   (the route already does).
5. **Validate** with the rules of §5 (ids, cran 0..7, `action_profil` only on
   `profil_*`, ≤ 10 infractions, text limits). Ids of custom infractions are
   `[a-z0-9]{1,12}`; generate them random and never reuse one.
6. **Keep exemptions identical across features** (the panel edits one list).
7. **Running state** includes `features.profile.enabled` (§4).
8. **Publish `module_updated`** on `moddy:bot` after every write (§7).
9. **Optional read-side work**: profile holds (`automod_profile_holds`), label
   kinds `profil_*`, the `perso:<id>` / `profil_*` categories on cases (§8).

