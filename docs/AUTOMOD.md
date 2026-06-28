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
  "rules": "server rules (AI-validated for prompt injection)",
  "log_channel_id": 123,
  "ignore_moderators": true,
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
on.

### Applying a decision

- `supprimer` → delete the message.
- `ban` (precedence) / `mute` (Discord timeout, duration by gravity) → applied
  if role hierarchy + permissions allow, and the action is marked in
  `bot._moddy_initiated_sanctions` so `case_sync` doesn't double-record the
  audit-log echo.
- For each real sanction action, a **guild case** is opened/extended through
  `bot.cases.record_sanction(source="guild", issuer_type=AUTOMOD, …)`, and the
  offending message is attached as an **`evidence`** timeline event
  (`bot.db.add_event`).
- A Components V2 card is posted to the configured log channel.

### Config UI

`modules/configs/automod_config.py` is a **persistent, immediate-apply** panel
(see [PERSISTENT_VIEWS.md](PERSISTENT_VIEWS.md)): every toggle / select / rules
edit saves straight to the DB and re-renders — no Save/Cancel step. All
components have static namespaced custom_ids (`moddy:automod:cfg:*`), the view
never times out, and a shell is registered so it survives restarts. Auth is
**Manage Server**, re-checked on every click; callbacks re-derive context from
`interaction` + the DB (never from `self`).

### Server rules safety check

When an admin edits the rules in `/config`, the text is run past the AI
(`automod/rules_check.py`, call_type `automod_rules_check`) **before** being
stored, because the rules are embedded verbatim into nano's system prompt. The
check **fails closed**: if the AI is unavailable, the rules are rejected.

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
| `SEUIL_EMBEDDING` | 0.45 | **Yes**, on real messages |
| `CONTEXTE_INITIAL` | 12 | by channel density |
| `CONTEXTE_MAX` | 40 | cost / injection ceiling |
| `ROUNDS_MAX` | 3 | anti-loop |
| `NANO_TEMPERATURE` | 0.2 | decision stability |
