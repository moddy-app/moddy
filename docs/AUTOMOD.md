# Moddy — Automod (AI message moderation)

> Read this before touching the `automod/` package, `modules/automod.py`, or
> `modules/configs/automod_config.py`.

The automod is split in two clean halves:

| Half | Where | Responsibility |
|---|---|---|
| **Detection pipeline** | `automod/` (root package, like `gateway/`) | Take a message → run the funnel → produce a `Decision` (or `None`). **Decides only.** Never applies anything, never touches the DB. |
| **Caller / module** | `modules/automod.py` | Owns configuration, **applies** decisions (delete/warn/mute/ban), records cases + evidence, logs, re-submits flagged messages. |

Every external call (embeddings + nano chat + rules safety check) goes through
**`bot.gateway`** — never a provider SDK. See [API_GATEWAY.md](API_GATEWAY.md).

---

## 1. The funnel (pipeline)

```
message → 1.pre-filter → 2.trivial allowlist → 3.regex blocklist ─match→ 5.nano
                                                      │no match
                                                      ▼
                                              4.embedding ─≥threshold→ 5.nano
                                                      │< threshold
                                                      ▼
                                                    STOP
```

| Step | File | Cost | Effect |
|---|---|---|---|
| 1. pre-filter | `prefiltre.py` | free | bot / system / empty → STOP |
| 2. trivial allowlist | `triviaux.py` | free | "ok", "mdr", "gg"… → STOP |
| 3. regex blocklist | `blocklist.py` | free | match → nano (`source=regex`), **skips embedding** |
| 4. embedding | `embeddings.py` | 1 embed call | `score ≥ SEUIL_EMBEDDING` → nano (`source=embedding`); else STOP |
| 5. nano | `nano.py` | 1–3 chat calls | **the only decider** → `Decision` |

Only **nano decides**. Regex and embedding merely *route*. Output is a
`Decision` object (or `None` if the message was stopped before nano).

### Data files (generated, bilingual FR + EN)

- `automod/data/references.json` — embedding reference phrases per category
  (insults, harassment, hate/discrimination, threats, self-harm incitement,
  sexual harassment). These capture toxicity **without keywords**. Their
  coverage directly conditions recall.
- The trivial allowlist (`triviaux.py`) and the regex blocklist (`blocklist.py`)
  are inline Python data (regex / sets), also FR + EN.

> Calibrate `SEUIL_EMBEDDING` and extend the references / blocklist with **real
> server data** over time. The blocklist is intentionally aggressive
> (anti-circumvention substring matching can over-trigger) because nano is the
> safety net.

---

## 2. nano (the decider)

| Param | Value |
|---|---|
| model | `gpt-4.1-nano` |
| response | `json_object` (json_mode) |
| temperature | `0.2` |
| context | `CONTEXTE_INITIAL=12`, `CONTEXTE_MAX=40`, `ROUNDS_MAX=3` |

- **Instructions** live only in the `system` message; **data** only in the
  `user` message (one JSON object).
- nano can ask for more context (bounded loop) and can **flag other authors'
  messages** (`autres_messages_a_verifier`) without deciding for them — the
  module re-submits each as a new target (`force_nano=True`, one level deep).
- Actions are **combinable**: `["supprimer", "warn"]`, `["ban"]`, …

### Decision contract — `raison` vs `explication`

The verdict separates **facts** from **reasoning** (two distinct fields on
`Decision`):

| field | content |
|---|---|
| `raison` | **facts only** — what the message contains / which rule it breaks, one short sentence. No reasoning, no history. This is what is stored as the case reason and shown to the member. |
| `explication` | 1–2 sentences justifying the decision (the *why*, context, real recidivism). Stored on the evidence event + shown in logs. |

### Intent-to-harm + anti-double-sanction (in the system prompt)

- **Intent required**: nano only sanctions when there is genuine intent to harm
  / break a rule. Humour, irony, quotes, self-deprecation, song lyrics, mere
  casual swearing with no target → not sanctionnable. The detector is only a
  suspicion.
- **Individual judgement**: nano judges the target message on **its own content
  only**. A short/ambiguous message ("je vais") is not a threat just because an
  earlier message was. The author's `messages_deja_moderes` (messages already
  actioned by automod, sourced from the case timeline via
  `db.list_automod_evidence_message_ids`) are passed so nano never re-punishes
  conduct already handled in an earlier message.
- **Severity**: the guild's `severite` (1–5) is injected as an explicit
  strictness instruction.

### Anti-prompt-injection (defence in layers)

1. **C1** strict instructions/data separation (system vs user).
2. **C2** locked output via `json_mode` + strict schema coercion
   (`nano.parse_verdict`).
3. **C3** per-request **nonce fence** around every `contenu` (`injection.py`).
4. **C4** opaque ids only (no usernames / roles / resolved mentions).

No anti-injection scheme is 100% reliable on an LLM — the goal is to strongly
reduce surface and impact while the deterministic layers (regex) keep working.

---

## 3. The module (`modules/automod.py`)

`MODULE_ID = "automod"`. Config stored in `guilds.data.modules.automod`:

```json
{
  "enabled": true,
  "indications": "automod guidance (AI-validated for prompt injection)",
  "notify_channel_id": 123,
  "ignore_moderators": true,
  "severity": 3,
  "features": {
    "content": {
      "enabled": true,
      "exempt_roles": [111, 222],
      "exempt_channels": [333]
    }
  }
}
```

The module runs `enabled` only when the module **and** at least one feature are
on **and** `notify_channel_id` is set — the **alert channel is mandatory**
(automod never runs without it). Legacy keys `rules` / `log_channel_id` are
still read transparently.

- **`severity`** (1–5) scales **both** detection sensitivity (the embedding
  threshold, see `constants.embedding_threshold_for`) and how strict nano is
  told to be. Default 3.
- **`indications`** (ex-`rules`) is the guidance fed to nano's system prompt.

### Applying a decision

- `supprimer` → delete the message.
- `ban` (precedence) / `mute` (Discord timeout, duration by gravity) → applied
  if role hierarchy + permissions allow, and the action is marked in
  `bot._moddy_initiated_sanctions` so `case_sync` doesn't double-record the
  audit-log echo.
- For each real sanction action, a **guild case** is opened/extended through
  `bot.cases.record_sanction(source="guild", issuer_type=AUTOMOD, …)`, with the
  **factual `raison`** as the case reason. The offending message is attached as
  an **`evidence`** timeline event (extract, jump URL, `explication`, signal,
  score, confidence, category, gravity).
- The case is recorded **before** the Discord action, so the audit-log reason
  carries the public case reference in the **same format as manual sanctions**:
  `[<REF>] @Moddy (<expiry>) : <raison>` (mirrors
  `cogs.moderation_commands._build_discord_reason`). A timed mute also carries
  its `expires_at` on the case sanction.
- A Components V2 card is posted to the **mandatory alert channel**.
- The sanctioned member is **DM'd** a sanction notice (like a manual mod action)
  carrying the appeal buttons — see §7.

### Evasion hardening (repeated / concatenated content)

`automod/normalize.collapse_repeats` reduces a repeated word/phrase or a
separator-free repeated unit back to one occurrence ("je vais te tuer" ×40 →
"je vais te tuer"). The blocklist matches both the plain and the collapsed form.
For the embedder, a spammed message embeds poorly (the repetition dilutes the
vector below threshold), so `embeddings.score` stays cheap — **one** embedding
for a normal message — and **only when an actual repetition is detected** it
additionally embeds the single de-duplicated unit and takes the **max** cosine.
No blind windowing of long messages.

### Config UI

`modules/configs/automod_config.py` follows the **standard module pattern**
(like the other `modules/configs/*`): a **working copy** is edited in memory and
written to the DB only on **Save**; **Cancel** discards pending edits, **Delete**
removes the stored config, **Back** returns to the module list (disabled while
there are unsaved changes). The view has a 300 s timeout and is opened fresh by
`/config` (it is **not** a persistent view — consistent with the other module
panels). Sections: **État**, **Salon d'alertes** (required), **Sévérité** (1–5),
**Indications** (replaces "Règlement"), **Exemptions**, **Options**.

### Indications safety check

When an admin edits the **indications** in `/config`, the text is run past the
AI (`automod/rules_check.py`, call_type `automod_rules_check`) **before** being
accepted into the working copy, because the indications are embedded verbatim
into nano's system prompt. The check **fails closed**: if the AI is unavailable,
the text is rejected.

## 7. Appeals

When automod opens a sanction case it DMs the member a notice (like a manual mod
action) with two appeal buttons — **server** (the guild's mods) or **Moddy
team** (`config.MODDY_APPEAL_CHANNEL_ID`). A reviewer can **Accept / Refuse /
Transform**; the decision is **binding** and applied by
`services/appeal_service.AppealService`:

| decision | effect |
|---|---|
| accept | revoke the case sanction + reverse the Discord action (unban / clear timeout) |
| refuse | the sanction stands |
| transform | revoke + record a replacement sanction and apply it on Discord |

Every step is mirrored to the **case timeline** (`comment` events), the reviewer
panel and the member's DM, and the server is always informed. State lives in the
`case_appeals` table (`db/repositories/appeals.py`); the UI is persistent
`DynamicItem` buttons + Modals V2 in `utils/appeal_views.py` (registered via
`AppealPersistence`). See [MODERATION_CASES.md](MODERATION_CASES.md).

---

## 4. Scalability — adding a new detector

The module dispatches each message to a set of **features**
(`AutomodFeature`). Today the only one is `content` (insults / problematic
messages via the AI funnel). To graft anti-link / anti-invite / anti-spam /
anti-raid later:

1. Add an `AutomodFeature` subclass in `modules/automod.py` (or a sibling
   module) with a `feature_id` and `async def process(message) -> list[Decision]`.
2. Register it in `FEATURE_CLASSES`.
3. Add its config block under `features.<id>` and surface it in the config UI.

The new feature emits the **same `Decision`** objects and reuses the shared
application / case / logging path — nothing else changes.

---

## 5. Gateway call types & quotas

| call_type | op | quota | gated |
|---|---|---|:--:|
| `automod_embed` | openai/embed | — | ❌ |
| `automod_decision` | openai/chat | guild | ✅ |
| `automod_rules_check` | openai/chat | guild | ✅ |

Seeded unlimited in `db/base.py`; tighten per-guild via `quota_overrides`.

> **Volume note:** every non-trivial, non-blocklisted message triggers one
> embedding call (and the gateway logs every call to the `api_call` webhook with
> prompt/response files attached). Consider the optional cache/batch
> optimizations (see the processing spec) before enabling at very large scale.

---

## 6. Tunables (`automod/constants.py`)

| Constant | Default | Calibrate? |
|---|---|---|
| `SEUIL_EMBEDDING` | 0.45 | **Yes**, on real messages (per-guild via `severity`) |
| `SEVERITY_DEFAULT` / `SEVERITY_EMBEDDING_THRESHOLDS` | 3 / {1:.62…5:.35} | the per-guild 1–5 dial → threshold + nano strictness |
| `CONTEXTE_INITIAL` | 12 | by channel density |
| `CONTEXTE_MAX` | 40 | cost / injection ceiling |
| `ROUNDS_MAX` | 3 | anti-loop |
| `NANO_TEMPERATURE` | 0.2 | decision stability |
