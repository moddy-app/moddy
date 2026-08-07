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
| Code | `modules/automod_ai.py`, config UI `modules/configs/automod_ai_config.py` |
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
  "langue_serveur": "auto",         // "auto" | "fr" | "en-US"
  "categories_desactivees": [],      // string[] — kill-switched AI categories
  "dry_run": false,                 // bool  — shadow mode: decide, apply nothing
  "features": {
    "content": {
      "enabled": false,             // bool
      "exempt_roles": [],           // int[] role ids (≤ 25 via the UI)
      "exempt_channels": []         // int[] channel ids (≤ 25 via the UI)
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
| `langue_serveur` | enum | `"auto"` | Language of sanction DMs / alert cards and of the AI-written reason. `auto` = the guild's `preferred_locale` when Community is on, else `en-US`. |
| `categories_desactivees` | string[] | `[]` | AI categories the server never wants actioned. Allowed values: `insulte`, `menace`, `harcelement`, `harcelement_sexuel`, `haine_discrimination`, `incitation_automutilation`, `doxxing`, `arnaque_scam`, `violation_indications`. A decision in a disabled category is downgraded to deletion only. No UI selector yet — ops/backend-set; the bot's config panel preserves the value on save. |
| `dry_run` | bool | `false` | **Shadow mode**: the whole funnel + barème run, but nothing is applied (no delete, no sanction, no case, no DM). A SIMULATION card with ✅/❌/⚠️ annotation buttons is posted to `notify_channel_id` instead. |
| `features.content.enabled` | bool | `false` | The AI content detector. Today the only feature. |
| `features.content.exempt_roles` | int[] | `[]` | Members holding any of these roles are not moderated. |
| `features.content.exempt_channels` | int[] | `[]` | These channels (and threads whose parent is listed) are not moderated. |

`features` is an **open map keyed by feature id**: future detectors (anti-link,
anti-spam…) will add sibling blocks with the same `{enabled, exempt_roles,
exempt_channels}` shape. Unknown feature ids are **rejected** by validation, so
the backend must not invent keys.

> There is no `features.situation` block any more. The diffuse-harassment
> ("situation") feature was removed in 2026-08; a stored `features.situation`
> object is ignored by the bot and should be dropped by the backend on the next
> write.

## 4. When is the module actually running?

The bot computes `enabled` at load time as:

```
running = config.enabled
          AND any(features[*].enabled)
          AND notify_channel_id is not null
```

A dashboard should mirror this to display the real state, and warn when the
alert channel is missing (that is the most common misconfiguration).

## 5. Validation rules (bot-side, `AutomodModule.validate_config`)

The backend should apply the same rules before writing; the bot re-validates on
save from its own panel and refuses invalid configs.

| Rule | Error |
|---|---|
| `notify_channel_id` resolves to a text channel of the guild | `Salon d'alertes invalide` |
| `len(indications) <= 3000` | `Les indications sont trop longues (max 3000 caractères)` |
| `severity` ∈ 1..5 | `Niveau de sévérité invalide (1 à 5)` |
| `max_action` ∈ `warn`/`mute`/`ban` | `Action maximale invalide` |
| `langue_serveur` ∈ `auto`/`fr`/`en-US` | `Langue invalide` |
| every key of `features` is a known feature id | `Fonctionnalité inconnue : <id>` |

Legacy keys are still read transparently on load: `rules` → `indications`,
`log_channel_id` → `notify_channel_id`. New writes must use the new names.

## 6. `indications` safety check

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

### `case_appeals` — appeals of automod sanctions

See [MODERATION_CASES.md](MODERATION_CASES.md). Sanctions themselves live in the
generic `cases` / `case_sanctions` / `case_events` tables with
`issuer_type = 'automod'`.

## 9. Cost controls the backend can drive

| Lever | Where | Effect |
|---|---|---|
| `quota_limits` / `quota_overrides` (scope `guild`, key = guild id) | PostgreSQL | Hard daily cap per call type: `automod_decision`, `automod_decision_mini`, `automod_confirm`, `automod_rules_check` (`automod_embed` is not quota-gated). `-1` = unlimited. |
| `automod:budget:cap:{guild_id}` | Redis | Per-guild override of the daily soft cap (default 300 "call units"; a `mini` call counts 4). Past the cap the funnel degrades (AI reserved for flagrant cases) instead of stopping. |
| `automod:budget:{guild_id}:{YYYYMMDD}` | Redis | The day's consumption counter (read-only for observability). |

See [API_GATEWAY.md](API_GATEWAY.md) for the quota model.
