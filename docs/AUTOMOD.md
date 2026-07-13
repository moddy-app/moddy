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

## 2ter. Relationship graph & target reaction (`automod/relations.py`)

nano judges text; text alone never carries the two facts that separate humour
from a will to harm: **who is talking to whom** (familiarity) and **how the
target reacted**. Session 5 feeds nano both, as **trusted server data** (not user
text), so "t'es trop nul mdr" between two regulars who banter constantly is not
treated like the same words fired at a stranger.

This lives in `automod/relations.py`. The scoring (`familiarite`) and the reaction
classifier (`classify_target_reaction`) are **pure and table-tested**; only
`RelationStore` touches Redis and is **inert without it** (a missing relation can
only make nano *stricter*, never laxer).

### Familiarity (`familiarite`) — passive per-pair counters

A Redis hash per unordered pair `rel:{guild}:{min}:{max}` accumulates, fed by
**existing listeners at ~0 cost** (no API fetch):

- a **reply / mention** from A to B → `interactions += 1` (and, if B had addressed
  A within `RELATION_MUTUAL_WINDOW_SECONDS` = 5 min, `reponses_mutuelles += 1`);
- a **laughter / friendly reaction** (`😂 👍 ❤️ 😭 …`) from B on A's message →
  `reactions_positives += 1` (via the non-raw `on_reaction_add`, which only fires
  for cached messages — so it never costs a fetch).

On read the counters **decay** (half-life 30 days) into one readable level:

```
score = (interactions + 2·mutual + 3·positive) · 0.5**(days_since_last / 30)
haute  : score ≥ 40 AND pair ≥ 7 days old
moyenne: score ≥ 12
faible : score ≥ 3
aucune : otherwise
```

The pair hash carries a **60-day TTL** — no global graph to maintain, it
self-expires. Only aggregate counters are stored, never message content.

### Target reaction (`classify_target_reaction`) — the post-message window

When a message reaches nano with an **identifiable target** (reply target or a
single human mention), the module defers the verdict by
`REACTION_WAIT_SECONDS` = 20 s (async, invisible to users) and observes the
target's replies, classifying into `reaction_cible`:

| observation in the window | signal |
|---|---|
| target deleted their messages / left the channel | `detresse_possible` |
| a reply carries a laughter marker (`mdr`, `lol`, `😂`, "tg toi-même mdr"…) | `banter_reciproque` |
| a reply itself trips the blocklist, no laughter | `conflit_reciproque` |
| nothing | `aucune` |

`detresse_possible` wins over everything; laughter beats aggression. **Exception:**
a flagrant regex hit (indicative gravity ≥ `REACTION_SKIP_SCORE`, e.g. a death
threat or doxxing) skips the wait for an **immediate** verdict.

### Injection & guardrails

The engine calls a lazy `relation_fn` **right before the nano call** (so the 20 s
wait is only paid on the path that actually spends a call) and passes the result
into nano's payload as `message_cible.relation`
(`{familiarite, interactions_30j, reciprocite, reaction_cible}`). The system
prompt gains a **trusted RELATION block** (with two calibrated few-shots: banter
at high familiarity vs the same text between strangers) — shown **only** when a
relation is present.

- Familiarity **only ever attenuates** a verdict, never aggravates (aggravation is
  the barème's fresh-account malus, §2bis).
- For `haine_discrimination`, `incitation_automutilation` and
  `harcelement_sexuel` at gravity **haute+**, relation is **ignored** — friends or
  not, it goes (`CATEGORIES_RELATION_IGNOREE`, mirrored in the prompt).
- A relation-carrying message is **never verdict-cached** (the reaction is
  specific to this message); a broken provider degrades to *no relation* — nano
  simply judges the text on its own.

Requires `bot.redis`; without it the graph, the reaction window and the whole
block are inert and the pipeline behaves exactly as before session 5.

---

## 2quater. Difficulty routing — nano → mini (`automod/routing.py`)

Session 6 puts the expensive model only where it earns its keep: subtle cases and
heavy sanctions. Structurally it **routes before it judges** rather than judging
then second-guessing.

### The router (`routing.difficulte`) — free, pure

Before spending a decision call, a **free heuristic** labels the message
`evident` or `ambigu` (no AI call to route). It is a pure function of the message
text, the routing `Signal` and the optional trusted `relation` block:

```python
difficulte(contenu, signal, relation, severity) -> "evident" | "ambigu"
# ambigu when: very short (≤ 3 words) · familiarite in (haute, moyenne) ·
#              a laughter marker (mdr / lol / 😂 …) · embedding score in the
#              grey zone (±0.05 of the routing threshold).
# a flagrant regex hit (indicative score ≥ threshold + 0.15) short-circuits to
# evident first (a clear-cut slur / threat needs no expensive re-read).
```

Laughter markers come from the **single source of truth** `constants.RIRE_MOTS` /
`RIRE_EMOJIS` (via `normalize.has_laughter`), shared with the target-reaction
classifier (§2ter) so the two never drift.

### Routing policy (`§6.2`)

| difficulté | decider | model | context |
|---|---|---|---|
| `evident` | nano (current behaviour) | `gpt-4.1-nano` | `CONTEXTE_INITIAL` |
| `ambigu` | **mini** | `gpt-4.1-mini` | `CONTEXTE_INITIAL × 2` |

Same v2 prompt / contract — only the model, the initial context window and the
call_type (`automod_decision_mini`) differ. The `Decision` records which model
decided it (`decideur = "nano" | "mini"`). If the heuristics ever plateau, a
3-token nano "evident/ambigu" pre-call is the planned upgrade (TODO, not built).

### Mandatory confirmation of heavy sanctions (`§6.3`)

Independently of routing: when the deterministic barème returns a **heavy cran
(≥ `CONFIRM_CRAN_THRESHOLD` = 6, i.e. mute 672 h / ban)** *and* the decider was
**nano**, the module requires a **binary mini senior review**
(`engine.confirm_heavy` → `nano.confirmer`, call_type `automod_confirm`) before
applying it:

```text
SYSTEM: senior moderator, answer ONLY {"confirme": bool, "motif": "…"}.
Confirm ONLY if the literal text unambiguously justifies the category AND its
severity. Any doubt / need for context => confirme=false.
```

- **`confirme=false`** (or a failed call — **fail-safe**) → the cran is capped to
  `CONFIRM_UNCONFIRMED_CRAN` (= 4, mute 48 h) by `bareme.appliquer_non_confirme`,
  a `confirmation_refusee` line is added to the breakdown, and the alert card
  shows the *"heavy sanction proposed, downgraded after AI review — a moderator
  can review it"* hint. **A human always keeps the last word** via the existing
  review affordance. An unconfirmed heavy sanction can therefore **never** be a
  ban — it is mechanically impossible.
- A **mini-decided** verdict is trusted as-is (it is already the smart model);
  so is any cran below the threshold.

### Cost (`§6.4`)

`ambigu` routing + confirmations are a small fraction of decision calls (~5–10 %)
and are already bounded by the §5.3 budget guard — mini calls are counted with a
**×4 weight** (`MINI_BUDGET_WEIGHT`) in the per-guild daily counter, reflecting
their ~4× unit price. Heavy-sanction confirmations are rare by construction and
are counted but **not** budget-gated (correctness over economy).

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
  "dry_run": false,
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
- **`dry_run`** (bool, default `false`) is **shadow mode** (§8): the whole funnel
  and barème run but **nothing is applied** — no delete/warn/mute/ban, no case, no
  DM. A **SIMULATION** card is posted instead, carrying annotation buttons that
  feed the evaluation corpus. Recommended for a server's first week.

### Applying a decision

The module runs the **barème** first (`_compute_bareme`): it loads the member's
recent guild sanctions (`list_member_sanctions`, 180 d), their server tenure and
the guild config, computes the cran and **overwrites `decision.actions` /
`decision.duree_heures`** from it. nano's qualification is the only thing read
from the pipeline.

**Shadow mode short-circuit:** if the guild has `dry_run` on, the module stops
right here — it records an eval candidate and posts the SIMULATION card
(`_notify_shadow`), and returns **before** any delete/case/DM. Everything below
only runs in normal (non-shadow) mode. Then:

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
| `automod_decision` | openai/chat (nano) | guild | ✅ |
| `automod_decision_mini` | openai/chat (mini) | guild | ✅ |
| `automod_confirm` | openai/chat (mini) | guild | ✅ |
| `automod_rules_check` | openai/chat | guild | ✅ |

Seeded unlimited in `db/base.py`; tighten per-guild via `quota_overrides`.
`automod_decision_mini` (ambiguous cases) and `automod_confirm` (heavy-sanction
confirmation) are the **session-6** routing calls — see §2quater.

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
>
> The cache key is the **collapsed/normalised** form of the message (session 4.4,
> `embeddings.cache_key`), so a padded or re-cased duplicate ("aaaa" / "aaaaa",
> "Con" / "con") shares one entry and one call. A message longer than
> `PREFILTRE_MAX_CHARS` (1500) is embedded as its collapsed form, truncated to
> the cap — a wall of text never needs its full length embedded.

### 5.1 Verdict cache (nano de-duplication)

Symmetric to the embedding cache but on nano's **qualification**. Same text in
the same guild yields the same qualification (guidance / severity are per-guild,
so the key is `sha256(guild_id + collapse_repeats(text))`), which makes the
qualification safe to memoise: a copypasta raid that reaches nano costs **one**
chat call, not N. Only the qualification (sanctionnable / categorie / gravite /
citation / cible / raison / explication / confiance / grounding motif) is cached
— the **barème** (cran + recidivism) is recomputed every time, because the
author's history differs per message. `a_reverifier` is context-specific and is
**never** restored from the cache.

- Short TTL (`VERDICT_CACHE_TTL_SECONDS = 600`, guidance can change), LRU bounded
  (`VERDICT_CACHE_MAX_ENTRIES = 2048`), single-flighted like the embedding cache.
- A **free** cache probe runs *before* the budget guard, so a cached "yes" or
  "no" is served even when the guild is over its daily budget.
- Inspect live via `bot._automod_engine.verdict_cache_stats()`.

### 5.2 Author aggregation (fragmented harassment)

"je vais" / "te" / "retrouver" split across three messages clears the funnel —
each fragment is empty on its own. A short **Redis** buffer per
`(guild, channel, author)` (`AGGREGATION_WINDOW_SECONDS = 45`, cap
`AGGREGATION_MAX_MESSAGES = 6`) lets the pipeline judge the **concatenation**
when a message stops before nano and the buffer holds ≥ 2 recent fragments:

- The concat is routed through the **cheap steps only** (blocklist + embedding);
  nano runs only if the combined text actually routes.
- The nano payload marks it (`message_cible.agregat_de = [ids…]`) and the system
  prompt gains an *AGGREGATED MESSAGE* rule so nano judges the reassembled text.
  The `Decision` carries `agregat_de` (every fragment id) and `agregat_contenu`
  (the combined text); the module deletes **all N** fragments.
- **Anti double-jeopardy**: if any fragment already reached nano individually
  (tracked in a short Redis set), the aggregate is skipped.
- Requires `bot.redis`; without it aggregation is simply inert (the funnel is
  unchanged). Pass `channel_id` to `engine.analyze` to enable it.

### 5.3 Per-guild budget guard

A safety net on the bill, independent of the gateway quotas. A Redis counter
`automod:budget:{guild}:{utc-day}` is bumped on every **real** nano call
(`NANO_DAILY_SOFT_CAP = 300`, overridable per guild via
`automod:budget:cap:{guild}`). Past the cap the funnel **degrades, never
cuts**: nano is reserved for the flagrant cases — a regex hit, or an embedding
score ≥ `threshold + NANO_DEGRADED_SCORE_MARGIN` (0.10) — and everything else is
dropped before the call. The guild is posted a **one-off** "AI budget reached —
reduced sensitivity" card (gated by `engine.pop_budget_notice(guild_id)`, at most
once per UTC day). A cache hit never counts against the budget. Inspect via
`bot._automod_engine.budget_stats()` (soft_cap / nano_calls / dropped /
degraded_guilds). Without `bot.redis` the guard is inert.

### 5.4 Cost order of magnitude

For **1M messages/month** on an active server, at `gpt-4.1-nano`
(~$0.10/M in, $0.40/M out) and `text-embedding-3-small` (~$0.02/M):

| Stage | Estimated volume | Cost/month |
|---|---|---|
| Pre-filter + trivial (free) | 100 % → stops ~55 % | $0 |
| Embeddings (~25 tok/msg, ~30 % cache hit) | ~450k msgs | **≈ $0.20** |
| Nano (~2 % cross the threshold, ~1200 in / 120 out) | ~9k calls | **≈ $1.50** |
| Mini (session 6, ~5 % of nano verdicts) | ~450 calls | ≈ $0.80 |

The dangerous line item is **not** the unit price — it is a mis-calibrated
`SEUIL_EMBEDDING` or a raid with no cache. Hence the verdict cache (5.1) and the
budget guard (5.3): they bound exactly those two failure modes.

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
| `PREFILTRE_MAX_CHARS` | 1500 | embed input cap (collapsed-form truncation) |

### Cost-control tunables (session 4, `automod/constants.py`)

| Constant | Default | Meaning |
|---|---|---|
| `VERDICT_CACHE_ENABLED` | `True` | leave on; disable only to A/B the savings |
| `VERDICT_CACHE_MAX_ENTRIES` | 2048 | LRU cap on cached nano qualifications |
| `VERDICT_CACHE_TTL_SECONDS` | 600 | short freshness bound (guidance can change) |
| `AGGREGATION_ENABLED` | `True` | fragmented-harassment aggregation (needs Redis) |
| `AGGREGATION_WINDOW_SECONDS` | 45 | sliding buffer window per (guild, channel, author) |
| `AGGREGATION_MAX_MESSAGES` | 6 | fragments kept in the buffer |
| `AGGREGATION_MIN_MESSAGES` | 2 | minimum fragments before an aggregate is attempted |
| `NANO_DAILY_SOFT_CAP` | 300 | per-guild daily nano calls before degraded mode |
| `NANO_DEGRADED_SCORE_MARGIN` | 0.10 | over-cap: embedding must clear `threshold + margin` |

### Relationship / reaction tunables (session 5, `automod/constants.py`)

| Constant | Default | Meaning |
|---|---|---|
| `RELATION_ENABLED` | `True` | familiarity graph + target-reaction window (needs Redis) |
| `RELATION_TTL_SECONDS` | 60 d | per-pair hash TTL (self-expiring, no global graph) |
| `RELATION_DECAY_HALFLIFE_DAYS` | 30 | familiarity score half-life |
| `RELATION_MUTUAL_WINDOW_SECONDS` | 300 | a reply back within this counts as reciprocal |
| `RELATION_MIN_AGE_DAYS_FOR_HAUTE` | 7 | "haute" also requires the pair to be this old |
| `RELATION_SCORE_{HAUTE,MOYENNE,FAIBLE}` | 40 / 12 / 3 | weighted+decayed familiarity thresholds |
| `REACTION_WAIT_SECONDS` | 20 | verdict deferral to observe the target's reaction |
| `REACTION_SKIP_SCORE` | 0.85 | flagrant regex gravity that skips the wait (immediate verdict) |
| `CATEGORIES_RELATION_IGNOREE` | hate/self-harm/sexual | relation ignored for these at haute+ |

### Difficulty-routing tunables (session 6, `automod/constants.py`)

| Constant | Default | Meaning |
|---|---|---|
| `MINI_MODEL` | `gpt-4.1-mini` | model for `ambigu` cases + heavy-sanction confirmation |
| `ROUTING_EVIDENT_REGEX_MARGIN` | 0.15 | regex indicative score above `threshold + this` ⇒ evident |
| `ROUTING_AMBIGU_MAX_WORDS` | 3 | messages this short ⇒ ambigu (mini) |
| `ROUTING_GRAY_ZONE_MARGIN` | 0.05 | embedding score within this of threshold ⇒ ambigu |
| `AMBIGU_CONTEXT_MULTIPLIER` | 2 | ambigu messages get ×2 initial context |
| `CONFIRM_CRAN_THRESHOLD` | 6 | cran ≥ this decided by nano needs mini confirmation |
| `CONFIRM_UNCONFIRMED_CRAN` | 4 | cran cap when a heavy sanction is not confirmed (mute 48h) |
| `MINI_BUDGET_WEIGHT` | 4 | budget-guard weight of a mini call (routing + confirm) |
| `RIRE_MOTS` / `RIRE_EMOJIS` | sets | laughter markers, shared with §2ter (single source of truth) |

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

---

## 8. Évaluation — regression harness & shadow mode (`automod/eval/`)

> The point: prove a change **improves** the automod instead of moving the
> problem, and roll it out on a live server with **zero risk** while it calibrates.

### 8.1 Golden set (`automod/eval/golden.jsonl`)

A committed corpus of labelled cases, one JSON object per line:

```json
{"id": "gs-0003", "contenu": "ferme ta gueule connard", "contexte": ["…"],
 "attendu": {"sanctionnable": true, "categorie": "insulte", "gravite_min": "moyenne"},
 "tags": ["insulte_directe", "few_shot"], "origine": "few_shot_3"}
```

- `attendu` carries `sanctionnable` (always), and optionally `categorie` and
  `gravite_min` for sanctionnable cases.
- `tags` classify the case; **`faux_positif_reel`** marks a known real false
  positive — the harness's protected set (see the gate below).
- Seeded with 60+ cases: the 8 calibrated few-shots, the real false positives
  ("je suis con", "arrête stp", quotes, lyrics, casual swearing), varied true
  positives across every category, prompt-injection attempts, and English
  messages. It grows continuously from the annotation flow (§8.4).

### 8.2 Offline runner (`python -m automod.eval.run`)

Replays the **whole funnel** (prefilter → trivial → blocklist → embedding →
nano → grounding → barème) over the golden set. Two modes:

| mode | model calls | cost | use |
|---|---|---|---|
| `--replay` (default) | from `fixtures.json` (recorded scores + verdicts) | free, deterministic | **CI + pytest** |
| `--live` | real `bot.gateway` (embeddings + nano) | a few cents | re-measure after a prompt change; `--update-fixtures` refreshes the corpus |

It reports precision / recall / F1, a per-category confusion matrix, and the
cases whose verdict changed vs the committed **baseline**
(`golden_baseline.json`). It only **decides** — never applies a sanction, never
touches the DB.

**The CI gate.** The runner exits non-zero iff a case tagged
`faux_positif_reel` becomes sanctionnable again. So weakening the grounding
guard (session 1) or a category map turns CI **red** — the known false positives
can never silently regress. `tests/automod/test_eval_harness.py` asserts this
end to end (clean replay matches the baseline; disabling `validate_grounding`
resurrects the false positives and trips the gate).

Common commands (also in the `Makefile`):

```bash
make eval               # replay + baseline diff (exit≠0 on FP regression)
make eval-baseline      # refresh golden_baseline.json from the current replay
make eval-live          # real gateway run; refresh baseline + fixtures
```

Why `--replay` is trustworthy for code changes: the fixtures record the model
outputs, so the deterministic layers the harness protects (grounding guards,
category normalisation, the barème) are exercised for real; only a *prompt*
change needs `--live` to re-measure.

### 8.3 Shadow mode (`dry_run`)

A guild can run the whole system with **nothing applied**. With `dry_run: true`
the funnel and barème run exactly as normal, but instead of acting the module
posts a **SIMULATION** card to the alert channel (badge *"SIMULATION — aucune
action appliquée"*) showing the sanction that *would* have been taken and its
barème breakdown. **No delete, no warn/mute/ban, no case, no DM.** Toggle it in
`/config` (Automod → Options, "Mode simulation", recommended the first week).

The card carries three **persistent** annotation buttons — **✅ Correct**,
**❌ Faux positif**, **⚠️ Disproportionné** (`utils/automod_shadow_views.py`,
`DynamicItem`s registered in `utils/persistent_views.py`). A moderator's click
(requires *manage messages*) records their ruling onto the candidate and
re-renders the card in place. The buttons survive a bot restart: their
`custom_id` encodes the candidate id and the card is rebuilt from the DB row.

### 8.4 Annotation corpus (`automod_eval_candidates`)

Every shadow card — and, over time, every human correction (accepted appeal,
"false positive" click) — writes a row to `automod_eval_candidates`
(`db/repositories/eval_candidates.py`): the message, its context, the verdict,
the cran + barème breakdown, and (once annotated) the moderator's ruling.

`make eval-import` (`python -m automod.eval.import_candidates`, needs
`DATABASE_URL`) turns the **annotated** candidates into golden-shaped JSONL on
stdout, for a curator to review and fold into `golden.jsonl`:

| ruling | expected verdict | tag |
|---|---|---|
| `correct` | `sanctionnable: true` (+ category) | `from_annotation` |
| `faux_positif` | `sanctionnable: false` | `faux_positif_reel` |
| `disproportionne` | `sanctionnable: true` | `disproportionne` (barème signal) |

This is the loop that keeps the golden set — and the whole harness — grounded in
real server traffic. It is also the raw material for per-server precedents
(session 7).
