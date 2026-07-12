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
| 4. embedding | `embeddings.py` | 1 embed call (cached) | `score ≥ SEUIL_EMBEDDING` → nano (`source=embedding`); else STOP |
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

## 2. nano (the decider) — v2 grounded verdict contract

| Param | Value |
|---|---|
| model | `gpt-4.1-nano` |
| response | `json_object` (json_mode) |
| temperature | `0.0` (classification, not generation) |
| max tokens | `300` |
| context | `CONTEXTE_INITIAL=12`, `CONTEXTE_MAX=40`, `ROUNDS_MAX=3` |

- **Instructions** live only in the `system` message; **data** only in the
  `user` message (one JSON object).
- nano can ask for more context (bounded loop) and can **flag other authors'
  messages** (`autres_messages_a_verifier`) without deciding for them — the
  module re-submits each as a new target (`force_nano=True`, one level deep).
- The prompt is built from contrasted **few-shot examples** (`nano.build_system_prompt`),
  not abstract rules: self-deprecation, reciprocal banter, quotes, "arrête stp",
  and genuine insults/threats are each shown explicitly.

### The verdict contract (what nano returns)

nano **qualifies** the message; it no longer decides the sanction on its own —
that is the deterministic barème's job (session 2). The key v2 fields:

| field | content |
|---|---|
| `citation` | **verbatim substring** of `message_cible` that, alone, justifies `categorie`. Mandatory when `sanctionnable=true`. |
| `cible` | who the message targets: `"membre"` \| `"auteur_lui_meme"` \| `"groupe"` \| `"aucune"`. |
| `categorie` | one of the **canonical FR categories** (below). |
| `gravite` | `basse` \| `moyenne` \| `haute` \| `critique`. |
| `raison` | **facts only**, one short sentence, in the server's language. No speculation, no history. Case reason + shown to the member. |
| `explication` | 1–2 sentences justifying the decision (the *why*). Stored on the evidence event + logs. |
| `confiance` | `low` \| `medium` \| `high`. |

> **`actions` / `duree_heures` are gone from the nano contract (v2, session 2).**
> nano no longer decides *any* punishment — it only qualifies. The sanction is
> computed by the deterministic **barème** (§2bis). The pipeline leaves
> `Decision.actions` empty; the module fills it from the barème's cran.

**Canonical categories** (`automod.constants.CATEGORIES`): `insulte`, `menace`,
`harcelement`, `harcelement_sexuel`, `haine_discrimination`,
`incitation_automutilation`, `doxxing`, `arnaque_scam`, `violation_indications`.
Legacy detector/stored values (`insultes`, `menaces`, `contenu_sexuel`…) fold
onto this set via `nano.CATEGORIE_ALIASES` / `nano.normalize_categorie` — no data
migration needed.

### Grounding — deterministic guards (`nano.validate_grounding`)

After nano answers, **`validate_grounding(verdict, target.content)`** runs as the
last, non-negotiable filter before a verdict can carry a sanction. It makes a
hallucinated verdict *mechanically impossible*. Any failure ⇒ `sanctionnable=false`,
`actions=[]`, and the motif is recorded on `Decision.rejet_grounding` (never raises):

1. **`grounding_citation_absente`** — the `citation` is empty, contains echoed
   `[DATA:…]` markers, or is not a verbatim substring of the (fence-stripped)
   message. Comparison is case- and accent-insensitive with collapsed whitespace
   (`nano._norm`), so only real hallucinated *content* is rejected, not casing.
2. **`grounding_cible_incoherente`** — a victim-requiring category (`insulte`,
   `menace`, `harcelement`, `harcelement_sexuel`) with `cible` = `aucune` or
   `auteur_lui_meme`. Self-deprecation ("je suis con") can never be an insult.
3. **`grounding_raison_speculative`** — `raison` contains speculative wording
   (`suggests`, `pourrait`, `semble`, `could imply`…): if nano is only guessing,
   the message is not sanctionnable.

Every rejection is logged (`logger.info`, tag `grounding_rejected`, → webhook
logs) — free evaluation data on avoided false positives. The motif is also kept
on the `Decision` for the alert card / case timeline.

### Cold judgement — no signal, no history leaked to nano

- **No routing signal**: the `signal` (source / category / score) that routed the
  message here is **never** in nano's user payload. There is nothing to
  rubber-stamp: nano reads the message cold and sets `categorie` / `confiance`
  from scratch. `signal` stays internal (routing, evidence text,
  `Decision.signal_source` / `score_detecteur`).
- **No author history (v2)**: `historique_auteur` and `severite` were **removed**
  from nano's payload — the author's history was contaminating the *culpability*
  judgement (nano is asked "is this message a violation?", not "is this member a
  repeat offender?"). Recidivism and severity become deterministic inputs to the
  session-2 barème. `build_author_history` / `AuthorHistory` still flow through the
  pipeline for the barème's benefit; they are simply no longer serialised to nano.
- **Self-harm — high bar**: an ordinary word/imperative on its own ("stop" /
  "arrête") is never, by itself, incitement to self-harm.
- **Severity**: in v2, the guild's `severite` (1–5) drives the **embedding routing
  threshold** (detection sensitivity) only; nano's own strictness dial is gone.
  It returns as a deterministic cran modulator in the session-2 barème.

### Anti-prompt-injection (defence in layers)

1. **C1** strict instructions/data separation (system vs user).
2. **C2** locked output via `json_mode` + strict schema coercion
   (`nano.parse_verdict`).
3. **C3** per-request **nonce fence** around every `contenu` (`injection.py`).
4. **C4** opaque ids only (no usernames / roles / resolved mentions).

No anti-injection scheme is 100% reliable on an LLM — the goal is to strongly
reduce surface and impact while the deterministic layers (regex) keep working.

---

## 2bis. The barème — deterministic sanction scale (`automod/bareme.py`)

nano **qualifies**; the **barème computes the sanction**. It is a pure module
(no I/O, no Discord, no DB — fully table-testable): given the same verdict, the
same recidivism history and the same guild config it always returns the same
*cran* (rung) plus a line-by-line breakdown of how it got there. The module
(`modules/automod.py`) gathers the inputs and translates the cran into Discord
actions.

### The ladder (`LADDER`)

Every sanction is one rung on a single scale. `supprimer` is **always** included
(in the `content` feature the message is always the problem).

| Cran | Sanction | actions | duree_heures |
|---|---|---|---|
| 0 | deletion only | `["supprimer"]` | — |
| 1 | warn | `["warn","supprimer"]` | 0 |
| 2–6 | mute (2h→12h→48h→168h→672h) | `["mute","supprimer"]` | 2 … 672 |
| 7 | ban | `["ban","supprimer"]` | 0 (permanent) |

### How a cran is computed (`bareme.calculer`)

1. **Floor** `PLANCHER[(categorie, gravite)]` — the "cold" first-offence policy.
2. **Recidivism** `+ crans_recidive(points_actifs(...))` — past sanctions are
   **weighted points** (`POINTS_GRAVITE`) that **decay exponentially** (half-life
   45 days), scaled by source reliability (`POIDS_SOURCE`) and ×1.5 for a repeat
   in the **same category**. Thresholds: ≥5 pts → +1, ≥15 → +2, ≥40 → +3.
3. **Guild severity (1–5)** — global shift `{1:-1, 5:+1}`.
4. **Confidence cap** — `low` ⇒ at most a warn (cran 1); `medium` ⇒ never a mute
   >48h nor a ban (cran ≤ 4).
5. **Veteran clemency** (−1) — ≥90 days on the server, clean record, gravity
   `basse`/`moyenne` only; **never** for sensitive categories.
6. **Fresh-account malus** (+1) — <7 days on the server.
7. **Guild ceiling** — `max_action` (`warn`/`mute`/`ban`) caps the cran
   (`PLAFOND_CONFIG`). Deletion always survives the ceiling.
8. Clamp to 0–7.

**Source reliability** (`POIDS_SOURCE`): `manuel` 1.5, `automod_confirme` 1.25
(appeal **refused** — a human confirmed it), `automod` 1.0, `automod_appel_accepte`
**0.0** (appeal **accepted** — a proven false positive never counts). The module
derives it per past sanction from the issuer + the appeal state
(`db.list_member_sanctions`).

**Kill-switch** (`categories_desactivees`): a guild can opt a category out of AI
sanctioning entirely — it is capped to deletion only (cran 0).

### Explainability

`ResultatBareme.composantes` lists every step (`plancher`, `recidive`,
`severite`, `confiance`, `veteran`, `compte_recent`, `plafond`, …) with its
signed delta; the deltas sum to the final cran. The module renders this as a
breakdown on the alert card and stores it on the case evidence (`payload.bareme`
/ `payload.cran`) so the timeline explains the sanction line by line — no other
automod on the market does this. A cran ≥ 6 decided purely by automod sets
`needs_review` (a "Réviser" highlight today; session-6 mini confirmation later).

### Recidivism data (`db.list_member_sanctions`)

`list_member_sanctions(guild_id, user_id, since=now-180d)` returns
`{action, categorie, gravite, date, source_fiabilite}` per past guild sanction.
`categorie`/`gravite` come from the automod evidence event (else derived from the
action for manual sanctions). **No migration needed**: `source_fiabilite` is
derived live from `case_sanctions.issued_by_type` + the latest `case_appeals`
status. An **accepted appeal** additionally drops the message from
`messages_deja_moderes` (it was not at fault).

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

- **`severity`** (1–5) scales detection sensitivity (the embedding threshold,
  see `constants.embedding_threshold_for`) and is the barème's global cran shift.
  Default 3.
- **`indications`** (ex-`rules`) is the guidance fed to nano's system prompt.
- **`max_action`** (`warn`/`mute`/`ban`, default `ban`) is the barème's hard
  ceiling — a server can forbid the automod from ever muting/banning.
- **`langue_serveur`** (`auto`/`fr`/`en-US`, default `auto`) overrides the
  language of the sanction reason / member DM (auto = derive from the guild).
- **`categories_desactivees`** (list) kill-switches AI sanctioning per category.

### Applying a decision

The module runs the **barème** first (`_compute_bareme`): it loads the member's
recent guild sanctions (`list_member_sanctions`, 180 d), their server tenure and
the guild config, computes the cran and **overwrites `decision.actions` /
`decision.duree_heures`** from it. nano's qualification is the only thing read
from the pipeline. Then:

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
**Limites & langue** (`max_action` + `langue_serveur`), **Indications**
(replaces "Règlement"), **Exemptions**, **Options**.

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
> prompt/response files attached).
>
> **Embedding score cache (`automod/cache.py`).** The reference vectors are
> embedded once per process and never change, so a message's cosine score is
> deterministic for the process lifetime. `EmbeddingEngine.score()` therefore
> memoises `(score, category)` on the exact message text in a bounded LRU+TTL
> cache, and coalesces concurrent identical requests (**single-flight**). Net
> effect: a raid or copypasta flood — by construction the same text repeated N
> times — costs **one** embedding call instead of N, whether the duplicates
> arrive back-to-back (cache hit) or all at once (single-flight). It is purely
> an optimization: identical input → identical output, so it can never change a
> decision. Tune it via `EMBED_CACHE_*` in `constants.py`; inspect it live via
> `bot._automod_engine.cache_stats()` (hits / misses / evictions / hit_rate).

---

## 6. Tunables (`automod/constants.py`)

| Constant | Default | Calibrate? |
|---|---|---|
| `SEUIL_EMBEDDING` | 0.45 | **Yes**, on real messages (per-guild via `severity`) |
| `SEVERITY_DEFAULT` / `SEVERITY_EMBEDDING_THRESHOLDS` | 3 / {1:.62…5:.35} | the per-guild 1–5 dial → threshold + nano strictness |
| `EMBED_CACHE_ENABLED` | `True` | leave on; disable only to A/B the savings |
| `EMBED_CACHE_MAX_ENTRIES` | 4096 | raise for very high distinct-message volume |
| `EMBED_CACHE_TTL_SECONDS` | 1800 | defensive freshness bound (`0` = never expire) |
| `CONTEXTE_INITIAL` | 12 | by channel density |
| `CONTEXTE_MAX` | 40 | cost / injection ceiling |
| `ROUNDS_MAX` | 3 | anti-loop |
| `NANO_TEMPERATURE` | 0.0 | classification → deterministic |
| `NANO_MAX_TOKENS` | 300 | lean v2 contract |

### Barème tunables (`automod/bareme.py`)

| Constant | Default | Meaning |
|---|---|---|
| `PLANCHER` | table | floor cran per (category, gravity) |
| `POINTS_GRAVITE` | basse 1 / moyenne 3 / haute 7 / critique 15 | recidivism points per gravity |
| `DEMI_VIE_JOURS` | 45 | points half-life (decay) |
| `POIDS_SOURCE` | manuel 1.5 … appel accepté 0 | source-reliability weight |
| `MULT_MEME_CATEGORIE` | 1.5 | same-category repeat multiplier |
| `PLAFOND_CONFIG` | warn 1 / mute 6 / ban 7 | guild `max_action` cran ceiling |
| `CATEGORIES_SENSIBLES` | self-harm/doxxing/sexual | no veteran clemency at haute+ |
