# Moddy — Automod (AI message moderation)

> Read this before touching the `automod/` package, `modules/automod_ai.py`, or
> `modules/configs/automod_ai_*.py` (the `/config` panel).

The automod is split in two clean halves:

| Half | Where | Responsibility |
|---|---|---|
| **Detection pipeline** | `automod/` (root package, like `gateway/`) | Take a message → run the funnel → produce a `Decision` (or `None`). **Decides only.** Never applies anything, never touches the DB. |
| **Caller / module** | `modules/automod_ai.py` | Owns configuration, **applies** decisions (delete/warn/mute/ban), records cases + evidence, logs, re-submits flagged messages. |

Every external call (embeddings + nano chat + rules safety check) goes through
**`bot.gateway`** — never a provider SDK. See [API_GATEWAY.md](API_GATEWAY.md).

---

## 0. The `/config` panel (`modules/configs/automod_ai_*.py`)

* **Home** (instant save): breadcrumb, the generic state row (Active /
  Simulation / Paused via `module_state_row`, yellow accent in simulation), the
  alerts channel, a "fine-tune" select **outside the container**, Back and a red
  **Delete configuration** button (own confirmation screen, then back to the
  wizard). First run = 3-step wizard (channel, level, simulation/live); every step
  has **Skip** = *Balanced* level in *simulation* (safest sane default; keeps the
  channel if one was chosen, otherwise the home asks for it).
* **Main sub-views** (monitoring, sanctions, exemptions, advanced): classic module
  save model. Edits go to a **draft** (`automod_ai_common`: in-memory map
  `(guild, user, section)` -> the keys that section owns, TTL 30 min, lost on
  restart); Save / Cancel only show while the draft differs from the stored
  config, Back is locked meanwhile and a warning line says so. Save merges the
  draft onto the freshly loaded stored config, validates through
  `save_module_config` and clears it. Deeper screens (one infraction, its timeout
  Modal V2, the custom-infraction editor) have **no Save**: they edit the
  `sanctions` draft, shown as pending on the list, written when the list is saved.
* **Sanctions** shows the *effective* sanction (never "Moddy default"): a bullet
  list under icon headings (messages / images / profiles / server's own). One
  infraction = a multi-select of the five actions (conflicts resolved by
  `automod/actions.py` and explained), the exact timeout duration (button ->
  Modal V2, `parse_duration`, 1 min to 28 days, shown as text), the moderator
  approval toggle, an escalation toggle, on/off, reset, and Edit/Delete for custom
  infractions. Picking actions switches the entry to `mode: "perso"`
  (one plan for every severity; "Reset" returns to Moddy's ladder).
* The removed "AI guidance" screen does not exist any more (custom infractions
  replace it).

## 1. The funnel (pipeline)

```
message → 1.pre-filter → 2.trivial allowlist → 3.regex blocklist ─match→ 5.nano
                                                      │no match
                                                      ▼
                                              3bis.scam anchors ─≥1.0→ 5.nano
                                                      │< 1.0
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
| 3bis. scam anchors | `scam_anchors.py` | free | score ≥ `SCAM_ANCHOR_THRESHOLD` → nano (`source=ancres_scam`, category `arnaque_scam`) |
| 4. embedding | `embeddings.py` | 1 embed call (cached) | `score ≥ SEUIL_EMBEDDING` → nano (`source=embedding`); else STOP |
| 5. nano | `nano.py` | 1–3 chat calls | **the only decider** → `Decision` |

A guild that declared **custom infractions** (§2sexies) gets one extra, free
routing step between 3bis and 4 (its own keywords, `source=perso`) and a second
comparison at step 4 (the already-computed message vector against the guild's own
reference vectors) — no additional embedding call. A guild without any follows
the exact diagram above.

Only **nano decides**. Regex and embedding merely *route*. Output is a
`Decision` object (or `None` if the message was stopped before nano).

### Data files (generated, bilingual FR + EN)

- `automod/data/references.json` — embedding reference phrases per category
  (insults, harassment, hate/discrimination, threats, self-harm incitement,
  sexual harassment). These capture toxicity **without keywords**. Their
  coverage directly conditions recall.
- The trivial allowlist (`triviaux.py`) and the regex blocklist (`blocklist.py`)
  are inline Python data (regex / sets), also FR + EN.
- `arnaque_scam` has its own blocklist block (free nitro, claim your reward,
  crypto casino…) and 14 FR + EN references (added 2026-09 — before that the
  category existed only on nano's side, so scam text was never routed).
- **Scam anchors** (`scam_anchors.py`) — weighted anchor phrases of scam
  interfaces ("Withdrawal Success", "promo code", "bonus", "USDT", "Bank card",
  money amounts, impersonated celebrities…), matched per token with a small
  edit distance (≤ 1 from 4 letters, ≤ 2 from 9) **and** on the separator-free
  form, so noisy OCR text ("BANK CARO", "Vyrowith") still counts. One strong
  anchor routes alone; weak ones must add up. It only routes — nano decides.
- **Learned data** (Moddy team labeling queue, §9): embedding references
  (`automod_learned_references`) are scored exactly like the static ones
  (`EmbeddingEngine.set_learned/add_learned`), and blocklist terms
  (`automod_learned_terms`) are appended to the static lists
  (`Blocklist.reload`). Both are global and loaded at startup.

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
`incitation_automutilation`, `doxxing`, `arnaque_scam`,
`contenu_nsfw` (explicit images) and `contenu_choquant` (gore / graphic violence
in an image) — the last two decided by SafeSearch only, never by nano. nano may
additionally answer `infraction_perso` (+ a `perso` P-id) for a server-defined
infraction; the engine maps it to `perso:<id>` (§2sexies), so `infraction_perso`
never leaves the pipeline. The member-profile categories `profil_avatar_nsfw`,
`profil_avatar_choquant` and `profil_pseudo` are assigned by the profile service
(§4.4), not returned by nano.
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
(`modules/automod_ai.py`) gathers the inputs and translates the cran into Discord
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

1. **Floor** `PLANCHER[(categorie, gravite)]` — the "cold" first-offence policy
   (derived from the infraction catalogue, see below). A type the server set to
   `mode: "perso"` uses the server's own cran for that gravity instead
   (`plancher_perso` in the breakdown).
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

**Scams are banned on sight** (`CATEGORIES_BAN_DIRECT = {arnaque_scam}`): floor =
ban (cran 7) at every gravity, the `medium` confidence cap does not apply and
veteran clemency never does. Still bounded by the `low` confidence cap (no ban on
a guess), the guild `max_action` ceiling and the mini confirmation of a
nano-decided ban (§2quater). A scam ban also deletes the author's last hour of
messages (`delete_message_seconds=3600`) — compromised accounts spam every channel.

**Kill-switch** (`sanctions[<id>].enabled = false`, or the legacy
`categories_desactivees` list): a guild can opt an infraction out of sanctioning
entirely — it is capped to deletion only (cran 0, component `categorie_desactivee`).
`ConfigBareme.desactivees()` is the union of both sources.

### Infraction catalogue and per-server sanctions (`automod/infractions.py`)

`automod/infractions.py` is the **single source of truth** for what the automod
can sanction. Each `InfractionType` carries `id`, `famille` (`message` · `image` ·
`profil` · `perso`), the default floors per gravity (`plancher`), `sensible`
(never softened), `ban_direct` (banned on sight) and, for profile types, the
default `action_profil`. `bareme.PLANCHER`, `CATEGORIES_SENSIBLES` and
`CATEGORIES_BAN_DIRECT` are **derived** from it (message + image families), so
adding a type there is the only edit the barème needs. `infractions.get()`
synthesizes a `perso:<id>` type (family `perso`, floors `0/1/2/4`) for a custom
infraction.

Per server, `sanctions[<id>]` (normalised by `normalize_sanctions`, validated by
`validate_sanctions`; schema in [AUTOMOD_AI_CONFIG.md §3.1](AUTOMOD_AI_CONFIG.md))
overrides the policy of one type. `bareme.calculer` reads `ConfigBareme.sanctions`:

| Entry | Effect on the cran |
|---|---|
| absent / `mode: "auto"` | Moddy's defaults, unchanged (floor, severity shift, veteran clemency, fresh-account malus, recidivism) |
| `mode: "perso"` | Floor = the cran of the server's `actions` (+ exact `timeout_seconds`), see "Multiple actions" below (legacy: `crans[gravité]`, migrated). **Skips** the severity shift, veteran clemency and the fresh-account malus ("the server gets exactly what it chose"). Recidivism stays unless `recidive: false`. Confidence caps and the `max_action` ceiling still apply |
| `enabled: false` | Deletion only (kill-switch above) |
| `action_profil` (profile types) | Not a cran: tells the profile feature what to do (§4.4). Carried on `ResultatBareme.action_profil`; `None` for every other type and for a disabled type |

The breakdown (`composantes`) shows `plancher_perso` instead of `plancher` for a
custom ladder. Legacy `categories_desactivees` is folded into
`sanctions[<id>].enabled` when the config is loaded (an explicit `enabled` wins).

### Multiple actions per infraction (`automod/actions.py`)

An infraction (catalogue or custom) can apply several actions at once, chosen
among `delete`, `warn`, `timeout`, `kick`, `ban`, with an **exact** timeout
(`timeout_seconds`, 1 min .. 28 d; `parse_duration("16h53")` /
`format_duration`). Conflicts are resolved, never rejected: `ban` > `kick` >
{`timeout`, `warn`}; `delete` combines with all. Schema:
[AUTOMOD_AI_CONFIG.md §3.1](AUTOMOD_AI_CONFIG.md).

**How it sits on the cran ladder.** The ladder stays the one source of truth for
escalation, so recidivism, the confidence caps, `max_action` and the
heavy-sanction confirmation all keep working:

1. The chosen set has a **floor cran** (`actions.floor_cran`): ban 7, kick 6,
   timeout by duration (≤2 h → 2, ≤12 h → 3, ≤48 h → 4, ≤7 d → 5, else 6), warn
   1, delete only 0.
2. The barème runs as usual from that floor (recidivism unless `recidive: false`;
   no severity/veteran/fresh-account adjustments in `perso` mode).
3. Final cran = floor → the server gets **exactly** its set and its exact
   timeout. Final cran ≠ floor (escalated or capped) → the sanction is the
   ladder's at that cran (with its duration), and the lighter actions the server
   chose (`delete`, `warn`) are kept.
4. `ResultatBareme` carries `actions` (internal names), `duree_secondes` (exact;
   `duree_heures` is the rounded-up legacy view) and `require_approval`.

Why: replacing the ladder by free-form actions would have dropped recidivism
escalation; mapping the set onto a cran keeps one coherent explanation ("plancher
· récidive · plafond…") on the card and in the case evidence. A legacy
`mode: perso` + `crans` entry is migrated on load to the equivalent plan
(`infractions.migrate_entry`), per-gravity differences kept in `by_gravity`.

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

## 2quinquies. Server precedents — jurisprudence RAG (`automod/precedents.py`)

nano judges the text; the barème scales the sanction; the relation graph reads
the pair. Session 7 adds the last missing axis: **this server's own culture**.
Every human ruling the system already produces — an accepted/refused appeal, a
shadow `✅`/`❌` click — is stored as a **precedent** (the message + the final
human verdict + the embedding the funnel already computed). Before a decision
call, the current message is matched against the guild's precedents; strong
matches are handed to nano/mini as trusted server data, and a near-identical
human "not sanctionable" ruling short-circuits the call entirely. No
fine-tuning, no new model — the bot learns each server's local norms from the
corrections its moderators are already making.

The ranking + shortcut logic is **pure and table-tested** (`automod/precedents.py`):
it receives an already-computed query vector and the guild's already-loaded
precedents and returns ranked matches. Storage/loading and the one-off embedding
of a recorded precedent live in `services/precedent_service.py`; capturing the
message embedding lives in the engine — the pipeline still only *decides*.

### Storage (`automod_precedents`, `db/repositories/precedents.py`)

| column | meaning |
|---|---|
| `guild_id` | the server |
| `contenu_norm` | `collapse_repeats(normalize(message))` — for display / dedup |
| `embedding` | the **normalised** message vector, float32-packed BYTEA (no pgvector needed — cosine is a dot product in Python) |
| `verdict_humain` | `non_sanctionnable` \| `sanctionnable` |
| `categorie` / `gravite` | from the automod evidence (when known) |
| `source` | `appel_accepte` \| `appel_refuse` \| `bouton_fp` \| `bouton_ok` |

Fed by (everything already exists after S2/S3): **accept appeal** → a
`non_sanctionnable` precedent; **refuse appeal** → `sanctionnable` (human
confirmed); shadow **❌ faux positif** → `non_sanctionnable`; shadow **✅ correct**
→ `sanctionnable`. A *transform* appeal is ambiguous (the sanction changed, not
the sanctionable/not call) and is **not** recorded. The embedding is computed
once, at record time, through `bot.gateway` (`automod_embed`, not quota-gated).
Capped at `PRECEDENT_MAX_PER_GUILD` (500) — the oldest are evicted, so storage is
bounded and self-maintaining.

#### In-memory representation

Every embedding vector the bot keeps alive is a **float32 `array.array`**, never a
`list[float]`: `unpack_vector()` (repository) and `_normalize_vec()`
(`automod/embeddings.py`) are the two points that produce them. A 1536-dim vector
costs ~6.6 KB as an array against ~49 KB as a list of Python floats (7.4×), and
since the on-disk format is float32 already, **no precision is lost** — the
measured drift on a cosine is ~4e-9, against thresholds at 0.80/0.85/0.97. The
matcher takes any `Sequence[float]`, so this is purely a memory choice.

This matters because `PrecedentService._cache` holds up to
`PRECEDENT_MAX_PER_GUILD` vectors **per guild**: 24.6 MB/guild as lists, 3.3 MB as
arrays. That cache is also swept on every load (`_evict_expired`) — its
`PRECEDENT_CACHE_TTL_SECONDS` TTL used to be enforced on read only, and only for
the guild being read, so a guild that used the automod once kept its vectors
resident for the whole process lifetime.

### Injection & the deterministic shortcut (`§7.2` / `§7.3`)

Right before the decision call the engine gets the message's **normalised
embedding** — reusing the vector captured while the funnel scored it, so a message
routed by embedding pays **zero** extra call; a regex-routed message pays one
embedding **only when the guild actually has precedents** (a lazy `get_vector`
the provider calls only when its precedent set is non-empty). Cosine picks the
top `PRECEDENT_TOP_K` (3) matches at or above `PRECEDENT_MIN_SIMILARITE` (0.80):

- **Shortcut (`≥ PRECEDENT_SHORTCUT_SIMILARITE` = 0.97, `non_sanctionnable`)** →
  the funnel **stops before any model call** (`stop_reason=precedent`), returning
  a not-sanctionable `Decision` carrying `precedent_applique` for the logs. A
  human already ruled near-identical text on this exact server — replaying the
  model would only risk contradicting them and cost a call. Only the
  not-sanctionable direction short-circuits (a "sanctionable" precedent still
  goes through the model — the barème/recidivism must be recomputed).
- **Injection** → the top matches are added to nano/mini's payload as
  `precedents_serveur` (message fenced like any untrusted content, plus the human
  ruling and the similarity), and a trusted **SERVER PRECEDENTS** system block
  tells the model to give them strong weight above `PRECEDENT_STRONG_SIMILARITE`
  (0.85), especially toward not sanctioning. **Precedents never override
  genuinely gravite haute/critique content** (real threats / hate / doxxing /
  self-harm) — the block says so and the golden set asserts it.

Precedents are served through a short in-process cache
(`PRECEDENT_CACHE_TTL_SECONDS`, 300 s) so matching stays in-process and DB load
is one query per guild per window. Without a DB the whole feature is inert.

### Admin visibility (`§7.4`)

`/config → Automod → Advanced` shows the learned-precedent count and a **View precedents** button opening a paginated browser with per-item deletion —
a wrong precedent can always be purged (it also busts the service cache).

### Tunables (session 7, `automod/constants.py`)

| Constant | Default | Meaning |
|---|---|---|
| `PRECEDENTS_ENABLED` | `True` | server precedents (needs a DB) |
| `PRECEDENT_TOP_K` | 3 | matches injected into the decision prompt |
| `PRECEDENT_MIN_SIMILARITE` | 0.80 | minimum cosine to inject a precedent |
| `PRECEDENT_STRONG_SIMILARITE` | 0.85 | above this the prompt gives strong weight |
| `PRECEDENT_SHORTCUT_SIMILARITE` | 0.97 | a `non_sanctionnable` match this close stops before the model |
| `PRECEDENT_MAX_PER_GUILD` | 500 | per-guild cap (oldest evicted) |
| `PRECEDENT_CACHE_TTL_SECONDS` | 300 | in-process freshness of a guild's precedent set |
| `PRECEDENT_CACHE_MAX_GUILDS` | 50 | hard cap on guilds held in memory at once (LRU-evicted) |
| `PRECEDENT_QUERY_VECTOR_CACHE` | 256 | bounded cache of the funnel's own embedding, reused for the precedent query |

---

## 2sexies. Custom (server-defined) infractions (`automod/custom_infractions.py`)

A server declares its own infraction types in `infractions_perso` (max 10):
`{id [a-z0-9]{1,12}, nom <=40, description <=300, exemples <=5x200, mots_cles <=20x60, modele}`.
Downstream the category is `perso:<id>`; its sanction is `sanctions["perso:<id>"]`
(§2bis; default floors `0/1/2/4`). Orphan `perso:*`
sanctions are dropped on load and rejected by `validate_config`. They replace the
removed free-text "AI guidance". Entries are normalised on load (`custom_infractions.normalize`: bad
entries dropped, text cut to its limits, lists de-duplicated, max 10) and strictly
validated on save (`validate` / `validate_entry`); error codes and the full schema
→ [AUTOMOD_AI_CONFIG.md §3.2 and §5](AUTOMOD_AI_CONFIG.md). Six Moddy **templates**
(`pub_serveur`, `spoilers`, `politique_religion`, `demande_dm`, `langue`, `liens`)
give one-click starting points, filled in the server language.

> Code comments in `automod/constants.py` / `automod/engine.py` still cite
> "§2quinquies" for this feature; that section is the server precedents RAG — the
> custom infractions are this §2sexies.

- **Routing, zero extra embedding call.** In `_route_semantic`, AFTER the regex
  blocklist and scam anchors (a global hit keeps priority, **including a passing
  global embedding score, which outranks a custom keyword hit**): (1) keyword match
  (`match_keywords`, same accent/leet/repeat/separator tolerance as the blocklist;
  URL fragments like `discord.gg/` match literally, also with whitespace evasion)
  -> `Signal(SOURCE_PERSO, score=PERSO_KEYWORD_SCORE)`, returned only once the global
  embedding score failed to pass (the message is embedded once either way; a
  non-trivial message with a keyword now costs that one embedding, but never the
  custom references); (2) similarity: the message
  vector the global score already computed (`EmbeddingEngine.score_with_vector`)
  is compared with the guild's custom reference vectors (description + examples,
  embedded once per content digest, shared across guilds with identical text) at
  `perso_threshold_for(severity)` (0.50 at severity 3, shifted like the global
  threshold). A guild without custom infractions takes the exact old code path.
- **Pre-filter / trivial.** Bots, system and empty messages never route. A trivial
  interjection ("lol") is never embedded, but a server keyword still routes it.
  Aggregation (fragmented messages) also uses custom routing. The OCR image path
  does not.
- **nano.** Only when the guild has custom infractions, a block after the
  role description lists them as `[P1] <fenced nom: description>` (opaque ids, real ids
  never shown, nano still judges cold and is not told which one routed). nano
  answers `"categorie": "infraction_perso", "perso": "P<n>"`; `validate_grounding`
  rejects an unknown P-id (`grounding_perso_inconnu`); `juger` maps it to
  `perso:<id>`. No victim is required. The prompt is byte-identical without custom
  infractions.
- **Caches.** The verdict-cache key includes `custom_infractions.fingerprint`
  (id + nom + description + examples + keywords; `""` without custom infractions,
  so such a guild keeps the exact old keys). Custom reference vectors are cached
  per content digest, shared across guilds with identical text
  (`PERSO_REFS_CACHE_MAX_GUILDS` 256, `PERSO_REFS_CACHE_TTL_SECONDS` 3600).
- **Mini confirmation.** A heavy sanction on a custom infraction is re-read by
  the mini reviewer with the server-authored definition handed to it (the
  `perso:<id>` id alone would be opaque).
- **Safety.** `rules_check.validate_custom_infraction` (also
  `POST /automod/rules_check` with `{"guild_id", "infraction": {...}}`) runs the AI
  injection guard over nom + description + exemples; unmodified Moddy templates
  (`TEMPLATES`, `from_template`) are trusted. Fails closed.
- **Rendering.** `utils/automod_render.infraction_label` shows the infraction's
  `nom` (sanitised) on cases, evidence, barème breakdown, shadow card and the team
  label card. A `perso:*` category is never learned as a global reference.

## 3. The module (`modules/automod_ai.py`)

> **Globally limited servers are skipped entirely.** `on_message` returns
> immediately when the guild carries a global `restrict` (or `ban`) sanction:
> Moddy does not moderate a community it has itself sanctioned. See
> [GLOBAL_SANCTIONS.md](GLOBAL_SANCTIONS.md).

`MODULE_ID = "automod_ai"` (module name **Automod**; the id stays `automod_ai`). Config stored in
`guilds.data.modules.automod_ai`. A config still stored under the historical
`automod` key is migrated to `automod_ai` on the next guild load
(`ModuleManager.LEGACY_MODULE_IDS`), so the classic (non-AI) automod module can
take the `automod` id later. Full schema (for the dashboard / backend) →
[AUTOMOD_AI_CONFIG.md](AUTOMOD_AI_CONFIG.md).

```json
{
  "state": "active",   // active | simulation | paused (generic module state)
  "notify_channel_id": 123,
  "ignore_moderators": true,
  "severity": 3,
  "max_action": "ban",
  "sanctions": { "insulte": { "enabled": true, "mode": "perso",
                              "crans": {"basse": 0, "moyenne": 0, "haute": 2, "critique": 3},
                              "recidive": true } },
  "infractions_perso": [ { "id": "k3x9", "nom": "Advertising",
                           "description": "…", "exemples": [], "mots_cles": [], "modele": null } ],
  "dry_run": false,
  "features": {
    "content": {
      "enabled": true,
      "exempt_roles": [111, 222],
      "exempt_channels": [333]
    },
    "profile": { "enabled": false, "exempt_roles": [], "exempt_channels": [],
                 "avatar": true, "banner": true, "pseudo": true }
  }
}
```

(`image_nsfw`, `image_gore` and `image_scam` blocks have the same shape as `content`.)

The module runs `enabled` only when the module **and** at least one feature
(`profile` included) are on **and** `notify_channel_id` is set — the **alert channel is mandatory**
(automod never runs without it). Legacy keys `rules` / `log_channel_id` are
still read transparently.

- **`severity`** (1–5) scales detection sensitivity (the embedding threshold,
  see `constants.embedding_threshold_for`) and is the barème's global cran shift.
  Default 3.
- The former **`indications`** ("AI guidance") setting is **removed**: custom
  infractions replace it. A stored value is ignored by the bot and dropped on the
  next save; nano's prompt no longer has a guidance section.
- **`max_action`** (`warn`/`mute`/`ban`, default `ban`) is the barème's hard
  ceiling — a server can forbid the automod from ever muting/banning.
- The language of the sanction reason / member DM is the **server language**
  (`/config` → Server settings, `utils/guild_language.py`) — automod has no
  language setting of its own.
- **`sanctions`** (object) — per-infraction policy (kill-switch, own severity
  ladder, profile action); **`infractions_perso`** (list, ≤ 10) — the server's
  own infraction types. See §2bis, §2sexies and
  [AUTOMOD_AI_CONFIG.md](AUTOMOD_AI_CONFIG.md).
- **`categories_desactivees`** (list) — legacy kill-switch, superseded by
  `sanctions[<id>].enabled`; still read, never written by the panel.
- **`state: "simulation"`** (the former `dry_run` flag, now the generic module
  state; `SUPPORTS_SIMULATION = True`) is **shadow mode** (§8): the whole funnel
  and barème run but **nothing is applied** — no delete/warn/mute/ban, no case, no
  DM. A **SIMULATION** card is posted instead, carrying annotation buttons that
  feed the evaluation corpus. Recommended for a server's first week.

### Applying a decision

The module runs the **barème** first (`_compute_bareme`): it loads the member's
recent guild sanctions (`list_member_sanctions`, 180 d), their server tenure and
the guild config, computes the cran and **overwrites `decision.actions` /
`decision.duree_heures`** from it. nano's qualification is the only thing read
from the pipeline.

**Shadow mode short-circuit:** if the guild is in `simulation` state (`module.dry_run` == `is_simulation`), the module stops
right here — it records an eval candidate and posts the SIMULATION card
(`_notify_shadow`), and returns **before** any delete/case/DM. Everything below
only runs in normal (non-shadow) mode. Then:

**Protection (not an option).** The server owner and any member whose top role is
not strictly below Moddy's (`AutomodModule._is_protected`) are skipped before
anything else: no AI call, no deletion, no case, no DM, no card (message path,
profile path and approvals alike; re-checked when a pending approval is applied).

**Apply first, verify, then announce** (`_execute_incident`). Nothing is shown as
done before it happened:

1. `supprimer` → delete the message(s); a Discord refusal is reported (a message
   that is already gone is not an error).
2. `ban` / `kick` / `mute` (`_apply_discord_sanction`; ban > kick > timeout) with
   the permission and hierarchy checks; the action is marked in
   `bot._moddy_initiated_sanctions` so `case_sync` skips the audit-log echo. A
   timeout is applied with `member.edit(timed_out_until=…)` and **verified**
   against the member Discord returns (`actions.timeout_verified`). Every failure
   gets a reason code (`hierarchy`, `permission`, `absent`, `discord`,
   `unverified`, `case`).
3. The **guild case** (`bot.cases.record_sanction(source="guild",
   issuer_type=AUTOMOD, …)`) only carries the sanctions that were really applied
   (a `warn` counts once its case is written; a kick is not a case sanction). The
   factual `raison` is the reason and the message is attached as `evidence`. The
   audit-log reason therefore reads `[Automod] @Moddy (<expiry>) : <raison>`
   (the public case reference does not exist yet when Discord is called).
4. The alert card (`utils/automod_approval_views.render_alert_card`, shared with
   the approval flow) lists the actions that took effect and, under "Not
   applied", each failed one with a short reason. Accent turns yellow when
   something failed.
5. The member is **DM'd through `bot.notifications`** (appeal buttons, §7) only
   for a sanction that was really recorded.

### Moderator approval (`require_approval`)

Per infraction option. When the plan contains a `warn` / `timeout` / `kick` /
`ban`, the module does not sanction on its own:

- The offending message is **deleted immediately** (harmful content should not
  stay up while waiting, and removal is not what needs a second opinion).
  Everything else — warn, timeout, kick, ban, the case, the member's DM — waits.
- A card with the evidence and persistent **Approve / Reject** buttons is posted
  in the alert channel; the decision snapshot (decision, barème, message snapshot
  with context) is stored in `automod_pending_approvals`
  (`db/repositories/automod_approvals.py`) so the buttons survive restarts.
- `ApprovalButton` (`utils/automod_approval_views.py`) is a `DynamicItem`
  (`moddy:automod:approval:<ok|no>:<id>`), registered through
  `AutomodApprovalPersistence`. Every click re-reads the row; only members with
  the matching permission may decide (`actions.approval_permission`:
  `ban_members` for a ban, `kick_members` for a kick, else `moderate_members`;
  administrators always). The row is claimed by one guarded `UPDATE … WHERE
  status = 'pending'`, so two moderators cannot both win.
- **Approve** → `AutomodModule.apply_approval`: protection and permission checks
  run again *now*, then the same apply → verify → case → DM path as a live
  sanction; the card is edited to show exactly what happened. **Reject** → nothing
  applied, card edited. After `APPROVAL_TTL_HOURS` (48) the buttons only mark the
  request expired.
- Not supported on profile infractions (their `action_profil` is the policy).
  Approvals are not copied to the team labeling queue.

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

The panel is a rewrite built around one idea: *the only mandatory thing is where
Moddy posts its alerts; everything else has a ready-made answer.* Plain words
throughout (never "cran", "barème", "nano"). **Every change is saved instantly**
(no Save/Cancel): the screen reads the stored config, changes one thing, and
writes it with `bot.module_manager.save_module_config` (which runs
`AutomodModule.validate_config` and refreshes the live module). A refused change
writes nothing and shows the reason. CLAUDE.md rule 9 applies: no "current
value" text under a select / channel / role picker; the stored value is printed
only where a button opens a Modal (the sanction of a type).

| Screen | File | What it is |
|---|---|---|
| **Wizard** (no alerts channel yet) | `automod_ai_config.py` | 1. alerts channel · 2. level *Light / Balanced (recommended) / Strict* (`automod/presets.py`, pure) · 3. start in simulation (recommended) or enable now |
| **Home** | `automod_ai_config.py` | status line (Active / Simulation / Off / Needs setup), a four-line summary, on/off, *Switch to real mode*, the alerts channel select, and a menu to the screens below |
| **What is monitored** | `automod_ai_advanced_config.py` | two multi-selects: messages + 3 image kinds, and avatar / banner / names (`features.profile.*`) |
| **Sanctions** | `automod_ai_sanctions_config.py` | types grouped Messages · Images · Profiles · Specific to this server, each with its sanction in plain words; a type select; *+ Add an infraction* (Moddy template in one click — no AI check — or from scratch, validated by `rules_check.validate_custom_infraction`, max 10) |
| **One type** | same | description, Enabled toggle, *Customise* (Modal V2: four severity pickers + an "escalate on repeat offences" checkbox), *Reset to default*, the profile action select, and Edit / Delete (confirmation) for custom infractions |
| **Exemptions** | same | roles + channels (shared by every feature) and *Ignore moderators* |
| **Advanced** | same | simulation toggle, maximum sanction, sensitivity (*Very tolerant … Very strict*), learned precedents (`automod_ai_precedents_view.py`), delete the configuration (confirmation) |

The alerts channel lives on the **Home** screen rather than under Advanced: it is
the one mandatory setting and the first thing an admin looks for.

Shared plumbing is `automod_ai_common.py`: `normalize_cfg` (the single place that
turns the stored config — legacy keys included — into the edited dict),
`save_cfg`, the plain-language sanction wording (`sanction_summary`), and `go()`
navigation. Sanction **action** icons come from `utils/emojis.py::LOG_EMOJIS`
(a documented, product-approved exception); all other icons are regular
constants.

Legacy `categories_desactivees` is folded into `sanctions[<id>].enabled = false`
when the config is loaded (an explicit `sanctions[<id>].enabled` wins, exactly
like `AutomodModule.load_config`) and is **never written back**; deleting a
custom infraction also deletes `sanctions["perso:<id>"]`. The panel keeps ONE
shared exemption list, written onto every feature (profiles get roles only).

Persistence: every screen is persistent. Guild-scoped screens (wizard steps,
home, sanctions list, monitoring, guidance, exemptions, advanced) are registered
shells with static `moddy:automod:cfg:<screen>:<action>` custom_ids. Entity-scoped
screens use `DynamicItem`s: `moddy:automod:cfg:tbtn:<action>:<type>` /
`moddy:automod:cfg:tsel:<type>` (one infraction type, `<type>` = built-in id or
`perso:<id>`) and `moddy:automod:cfg:prec:<action>:<page>` /
`moddy:automod:cfg:precdel:<page>` (precedents), registered by
`AutomodConfigPersistence`. Tests: `tests/test_automod_ai_panel.py`.

### Custom-infraction safety check

When an admin creates or edits a custom infraction in `/config`, its text
(name, description, examples) is run past the AI (`automod/rules_check.py`,
call_type `automod_rules_check`) **before** being stored, because it is embedded
in nano's system prompt. The check **fails closed**. (The former free-text "AI
guidance" and its check were removed.)

The same check is exposed to the backend over the internal API:

```
POST {BOT_INTERNAL_URL}/automod/rules_check
{ "guild_id": "…", "infraction": {…} }  →  { "ok": true } | { "ok": false, "reason": "…" }
```

The entry is validated structurally first (`400 invalid_infraction`), then run
through `rules_check.validate_custom_infraction` (an extra prompt paragraph tells
the guard that examples are fictional messages; unmodified Moddy templates are
trusted and skip the AI call). A body without `infraction` is `400
missing_infraction`.

See `internal_api/routes/automod.py` and
[AUTOMOD_AI_CONFIG.md § 6](AUTOMOD_AI_CONFIG.md#6-safety-check-custom-infractions)
for the full contract.

---

## 4. Scalability — features

The module dispatches each message to a set of **features**
(`AutomodFeature`). Each has a `feature_id`, an `async process(message) ->
list[Decision]`, is registered in `FEATURE_CLASSES`, and carries its own config
block under `features.<id>` (surfaced in the config UI). A feature emits the
**same `Decision`** objects and reuses the shared application / case / logging
path — nothing else changes. To graft anti-link / anti-invite / anti-spam /
anti-raid later, follow the same three steps.

### 4.1 `content` — the AI funnel

Insults / problematic messages via the funnel of §1 → `Decision` → barème →
sanction. Also the feature that carries the server's custom infractions (§2sexies).

### 4.2 `image_scam` — crypto / fake-giveaway images

The raids this targets post a screenshot (fake MrBeast announcement + a fake
"Withdrawal Success! +5 600 USDT" proof) — often from a compromised account,
often in several channels at once. The feature (`ImageScamFeature`) turns the
image into a normal automod decision:

```
image posted (attachments only, ≤ 4 / message, ≥ 128 px, ≤ 8 MB)
   │ download + decode ONCE (bot.automod_images, bounded concurrency)
   ▼
1. perceptual hash (pHash + dHash, automod/image_hash.py)
   ├─ known "block" (team-validated) → sanction on sight: arnaque_scam / haute /
   │                                    high, decideur="equipe", no AI call
   ├─ known "allow"                   → ignored (never flagged again)
   ▼
2. per-hash OCR cache (Redis 30 d, global) ─ hit → reuse the text
   ▼ miss
3. free pre-rules (image_policy.scam_risk_score) — OCR only if score ≥ 2
   (or the guild enabled `scan_all`):
     account < 30 d +2 · on the server < 7 d +2 · ≥ 2 images +1 ·
     text empty / link / scam words +1 · same image in ≥ 3 channels (10 min) +3 ·
     screenshot-like layout +1
   ▼
4. OCR — gpt-4.1-nano vision (call_type automod_image_ocr, guild quota 300/day)
   ▼
5. OCR text → engine.analyze(TargetMessage(origine="image_ocr"))
   blocklist → scam anchors → embedding → nano (dedicated prompt block:
   "text read from an image, noisy"), grounding unchanged (the citation must
   be a substring of the OCR text)
   ▼
6. Decision → barème (arnaque_scam = **ban**, §2bis) → applied like any
   decision; every cross-posted copy of the image is deleted too and the ban
   purges the author's last hour of messages → copy to the team queue (§9)
```

An image the pipeline could not read (OCR unavailable, decode failure, queue
full) is **never** sanctioned.

### 4.3 `image_nsfw` + `image_gore` — explicit and shocking images (Google SafeSearch)

Two **independent features** with their own `/config` toggle and their own
labeling lane: `image_nsfw` (sexual content, category `contenu_nsfw`) and
`image_gore` (gore / graphic violence, category `contenu_choquant`). Both read
the **same** SafeSearch result: it is cached per image hash (in-process + Redis),
so an image costs one call even with both on.

```
image posted  (skipped in age-restricted channels)
   ▼
1. perceptual hash → known "block" of this feature's kind (nsfw | gore) → sanction
   on sight · "allow" → ignored
2. per-hash SafeSearch cache (Redis 30 d, global) — an image seen anywhere costs 0
3. budget: the Google free tier (1000 / month) is shared by ALL of Moddy
     - smoothed: allowed(t) = cap × elapsed-month-fraction + 30 (burst)
     - priority tier (account < 30 d or on the server < 7 d) may spend up to
       100 % of allowed(t); everyone else only while usage < 70 % of it
     - at most NSFW_GUILD_DAILY_SHARE (15) calls per guild per day
     - the hard cap is a calendar-month, fail-closed gateway rule
4. SafeSearch (call_type automod_safesearch) → image_policy.safesearch_to_verdict(lk, axis)
   image_nsfw (axis "nsfw"):
     adult VERY_LIKELY → sanction, contenu_nsfw / haute / high
     adult LIKELY      → sanction, contenu_nsfw / moyenne / medium (barème caps at mute 48 h)
     adult POSSIBLE, racy VERY_LIKELY → doubt only (team queue, nothing applied)
   image_gore (axis "gore"):
     violence VERY_LIKELY → sanction, contenu_choquant / haute / high
     violence LIKELY      → doubt only
```

No model decides an NSFW image: the likelihood scale is the whole policy
(`decideur="safesearch"`). Barème floors for `contenu_nsfw`: basse 0 ·
moyenne 2 · haute 3 · critique 5; for `contenu_choquant`: basse 0 · moyenne 1 ·
haute 2 (delete + 2 h mute) · critique 4. SafeSearch's violence score can fire on
video games, films or medical pictures — a wrong call is revoked from the team
queue (§9) and its hash set to `allow`. SafeSearch is **not** a CSAM detector.

### 4.4 `profile` — member profiles (avatar, banner, names)

Moderates what a member **is**, not what they say: avatar and banner (NSFW / gore,
Google SafeSearch) and names (nickname, global display name, username: slurs,
sexual or hateful wording). Not run from `on_message`: `cogs/module_events.py`
dispatches member events to the module (`on_member_join`, `on_member_update` for
nickname / server avatar, `on_user_update` for username / display name / avatar,
fanned out to every shared server like `cogs/logs.py`). The module schedules the
check as a **bounded background task** (`bot.automod_profiles.spawn`, queue cap
`PROFILE_QUEUE_MAX`) so a raid of joins never delays another module.

Skipped: bots, the owner, exempt roles, moderators (`ignore_moderators`), servers
under a global sanction. Config: `features.profile.{enabled, avatar, banner,
pseudo}` (`exempt_roles` shared, `exempt_channels` unused). The module runs when
`enabled AND any feature AND notify_channel_id`, `profile` counting as a feature.

**Shared service** (`services/automod_profile_service.py`, `bot.automod_profiles`):

- *Avatar* — `member.display_avatar` (server avatar overrides the global one); a
  default avatar is skipped. Verdict cached per Discord **asset key**
  (`asset.key`) in Redis `automod:avatar:<key>` (30 d) + an in-process LRU: an
  avatar already seen on ANY server costs no download and no call. A miss goes
  download (`AutomodImageService.prepare_asset`) → team hash index (`nsfw` /
  `gore` kinds, block/allow) → SafeSearch (also reuses a result already cached by
  perceptual hash from message images). Concurrent checks of one asset share a
  single in-flight job, so the same avatar in 20 servers = one call.
- *Banner* — only reachable over REST (`fetch_user`): at most one fetch per user
  per 10 min (shared by every guild), same cache / hash / SafeSearch path keyed by
  the banner asset key. Discord emits no event for a banner-only change, so banners
  are checked on join, on any other profile event, and by the hold sweep (below)
  for banners that are held. A failed fetch is **unknown** (`BANNER_UNKNOWN`, not
  cached, retried after 1 min): it never counts as "banner removed", so a held
  member is not freed by a Discord/REST hiccup. Only a successful fetch with no
  banner (or `NotFound`) lifts a banner hold.
- *SafeSearch budget* — **separate** from message images: gateway rule
  `google_vision/safe_search_profile` (`rpmo`, calendar month, fail-closed,
  `GOOGLE_VISION_SAFESEARCH_PROFILE_MONTHLY`, default 1000). The adapter still
  calls the real SafeSearch API (operation `safe_search`); only the accounting
  key differs (`VisionClient.safe_search(budget_model=...)`). `call_type`
  `automod_profile_safesearch`. Spending is smoothed with
  `image_policy.pacing_allows` (`PROFILE_SAFESEARCH_*`), priority tier = account
  < 30 d or fresh join (`nsfw_tier`), plus a per-guild daily share
  (`PROFILE_GUILD_DAILY_SHARE`). A refusal sanctions nothing (debug log). Only
  *global* refusals (monthly budget, pacing, unreadable) are negative-cached for
  10 min per asset; a refusal caused by ONE guild's daily share is not
  (`AssetCheck.guild_scoped_refusal`), so it never blocks other guilds.
- *Names* — the distinct nickname / global name / username go through
  `engine.analyze` with `origine=profil_pseudo` (own short prompt block in
  `nano.py`: judge the NAME, staff impersonation is out of scope; no context, no
  relation, no precedents). Any sanctionable verdict is re-labelled category
  `profil_pseudo` (nano's own category kept in the evidence as `categorie_nano`).
  Verdicts cached per normalized name in Redis `automod:pseudo:<sha1>` (30 d;
  one global key shared by every server; a *clean* verdict is only reused by a server whose severity is not
  stricter). A cached verdict never bypasses per-server gates: the type's
  `enabled` / `action_profil` are read when the decision is applied.
  A team "not sanctionable" label on a name writes a global allow marker
  `automod:pseudo:allow:<sha1>` (30 d) checked before any cache/judge call.
  Grounding still applies (citation ⊆ name).
- *Idempotence* — Redis `automod:profile:done:<guild>:<user>:<asset key | n:hash>`
  (7 d for assets, 24 h for names) so a burst of profile events never sanctions
  the same thing twice; lifting a hold clears its marker. A **simulation**
  (`dry_run`) uses its own `sim:`-prefixed markers, so switching to live mode
  does not skip members already seen in simulation.
- *Allow markers* — an **accepted appeal** on a profile sanction writes
  `automod:profile:allow:<guild>:<asset key | n:hash>` (90 d); that guild no
  longer sanctions that exact asset / name (the hold row alone would not stop a
  rejoin from re-holding the same avatar once the done marker expired).

**Verdicts** reuse `image_policy.safesearch_to_verdict` per axis: `adult` →
`profil_avatar_nsfw`, `violence` → `profil_avatar_choquant` (banners map to the
same two categories; the evidence `origine` tells `profil_avatar` /
`profil_banniere` / `profil_pseudo` apart). A doubt goes to the team labeling
queue only.

**What happens to the member** is the server's `sanctions[<type>].action_profil`
(`modules/automod_ai.py::apply_profile_decision`, which shares the barème, heavy
confirmation, stats, simulation, case, Discord sanction, alert card, DM and
labeling queue with `apply_decision`):

| action | default for | effect |
|---|---|---|
| `hold` | avatar types (**never offered / accepted for `profil_pseudo`**: a name has no asset to re-check and the hold table only knows avatar/banner; stored values are coerced to `rename_warn`, `validate_sanctions` rejects it, and `apply_profile_decision` falls back to the type default as a last resort) | Discord timeout (28 d) + case `MUTE` ("the {kind} must be changed") + a row in `automod_profile_holds` + DM "change your avatar/banner". On a later profile event, a clean (or removed) profile **lifts** it: timeout cleared (only ours), sanction revoked, `profil_corrige` note on the case, hold deleted, a short "profile fixed" line in the alert channel. Still flagged → the timeout is kept (a rejoin is re-timed-out). A `max_action: warn` ceiling degrades it to a plain warn. Each renewal also pushes the case MUTE sanction's `expires_at` (`extend_sanction_expiry`), so the expiration notifier does not DM "your timeout is over" while the member is still held (chosen over creating the sanction without expiry: the case keeps a real end date and a team revoke/expiry job still works). |
| `rename_warn` | `profil_pseudo` | If the flagged name is the one displayed on the server, nickname ← "Moderated name" (server language, needs `manage_nicknames`), then a `WARN` case. Rename impossible (permission / hierarchy) or name not displayed → warn only, said on the card. |
| `alert` | — | Alert card only: no case, no sanction, no DM. |
| `bareme` | — | The cran ladder exactly like a message (warn / mute / ban), minus the deletion. |

A profile type switched off (`sanctions[<type>].enabled = false`, or listed in the
legacy `categories_desactivees`) is not acted on at all (`profile_action_for`
returns `None`). In `bareme` mode the server's `mode` / `crans` / `recidive` for
that type apply as for any other. The `/config` → Sanctions → *Profiles* group
shows these three types, the action select writes `action_profil`.

`dry_run` posts a SIMULATION card (member, kind, judged name or spoilered image,
policy) and applies nothing. Discord actions are marked in
`bot._moddy_initiated_sanctions` so `case_sync` does not double-record.

**Hold sweep** (`cogs/automod_profiles.py`, every hour, one query): holds
whose timeout ends within `PROFILE_HOLD_RENEW_WINDOW_HOURS` **and every hold not
re-screened for 6 h** (`last_checked`, so a removed banner is noticed; ordered
expiring first, then least recently checked, and stamped after each attempt even
when it fails, so stale holds of departed members cannot starve the batch; their
row is kept until 60 d past the timeout so a rejoin is re-held) are re-screened
through the module (cached verdicts → no new SafeSearch call for an unchanged
image): still flagged → timeout renewed, fixed → lifted. A server that switched
the feature off just lets the timeout lapse.

**Evidence** (`payload`): `source=automod`, `origine`, `extrait` (the name, or the
asset URL), `author_name`, `ts`, `categorie` / `gravite` / `confiance` / barème
fields, `profil: {kind, asset_key, asset_url}`; **no** `message_id` / `jump_url` /
`context_text`. The appeal helpers (`_proof`, `_automod_extract`, `_context_text`,
`_log_ref`) tolerate that; an accepted/modified appeal deletes the hold
(`delete_profile_hold_by_sanction`). Asset-URL cases show no message proof.

**Labeling queue** — new lanes `profil_avatar_nsfw`, `profil_avatar_choquant`
(image: block/allow hash of kind `nsfw`/`gore`, so one team label protects message
images too) and `profil_pseudo` (teaches nothing globally, like a server's own
infraction). "Not sanctionable" revokes the sanctions **and** deletes the hold /
lifts the timeout (`details.sanctions[].hold_kind`). On a name, revoking also
resets the nickname if it is still exactly the "Moderated name" placeholder
(best effort; noted on the case), and the name is allowed for every server.

**Known limitations** — banners (above); a name judged by the funnel only when
the blocklist / embedding routing sends it to nano (an unobvious name is not
flagged); the cached name verdict is the first server's (language of the AI text
is replaced by server-language wording, the AI explanation stays on the case); a
`rename_warn` nickname reset is only undone while the nickname is still the placeholder.

### Image decisions, everywhere else

- `Decision.origine` (`texte` / `image_ocr` / `image_hash` / `image_nsfw`),
  `Decision.contenu_juge` (the OCR text), `Decision.image` (`ImageMeta`: hashes,
  SafeSearch likelihoods, scam anchors, known-hash match, cross-post copies).
- The alert card, the case evidence (`payload.origine`, `payload.image`), the
  sanction DM and the shadow card show the **judged text** (OCR) instead of the
  empty message; the alert card **and the SIMULATION card** attach a downscaled
  copy of the image, **always spoilered** (the shadow candidate stores
  `verdict.image_phash` / `image_attached` so annotation re-renders keep it).
  Without *Attach Files* in the alert channel the card is resent without the
  image — the alert is never lost.
- The heavy-sanction mini confirmation re-reads the OCR text with the same
  origin block; `equipe` / `safesearch` decisions skip it (nothing to re-read).
- Once a feature deleted the message, the remaining features stop (no double
  sanction on one message).
- Config: `features.image_nsfw` / `features.image_gore` / `features.image_scam`
  (`+ scan_all`, no UI), toggled in `/config` → Automod → *What is monitored*;
  the panel's exemptions apply to every feature.

> A `situation` feature (diffuse harassment / dogpiling, judged on a friction
> state machine + a `mini` sequence analyst) existed until 2026-08 and was
> removed: it never applied anything (forced shadow mode) and did not earn its
> complexity. Nothing of it remains in the code, config or DB.

---

## 5. Gateway call types & quotas

| call_type | op | quota | gated |
|---|---|---|:--:|
| `automod_embed` | openai/embed | — | ❌ |
| `automod_decision` | openai/chat (nano) | guild | ✅ |
| `automod_decision_mini` | openai/chat (mini) | guild | ✅ |
| `automod_confirm` | openai/chat (mini) | guild | ✅ |
| `automod_rules_check` | openai/chat | guild | ✅ |
| `automod_image_ocr` | openai/vision (nano) | guild (300/day) + global | ✅ |
| `automod_safesearch` | google_vision/safe_search | guild + global (+ monthly fail-closed rule) | ✅ |
| `automod_profile_safesearch` | google_vision/safe_search, accounted on the `safe_search_profile` rule (member avatars / banners) | guild + global (+ its own monthly fail-closed rule) | ✅ |

Seeded unlimited in `db/base.py` (the `automod_profile_safesearch` rows included);
tighten per-guild via `quota_overrides`. `automod_rules_check` also covers the
custom-infraction check. Names of members (§4.4) go through `automod_decision`
like any text, so they consume the same nano quota and budget guard.
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
so the key is `sha256(guild_id + collapse_repeats(text) [+ origin] [+ custom-infraction fingerprint])`), which makes the
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

### Image tunables (`automod/constants.py`, §4.2/§4.3)

| Constant | Default | Meaning |
|---|---|---|
| `IMAGE_OCR_MODEL` | `gpt-4.1-nano` | vision model reading scam screenshots |
| `IMAGE_MAX_SIDE` | 1280 | longest side sent to OCR / SafeSearch |
| `IMAGE_MAX_BYTES` / `IMAGE_MIN_SIDE` / `IMAGE_MAX_PER_MESSAGE` | 8 MB / 128 px / 4 | which attachments are looked at |
| `IMAGE_CONCURRENCY` / `IMAGE_QUEUE_MAX` | 4 / 64 | process-wide image jobs (memory bound) |
| `PHASH_MAX_DISTANCE` / `DHASH_MAX_DISTANCE` | 6 / 10 | known-hash match (both must hold) |
| `IMAGE_VERDICT_TTL_SECONDS` | 30 d | per-hash OCR text / SafeSearch cache |
| `CROSSPOST_WINDOW_SECONDS` / `CROSSPOST_MIN_CHANNELS` | 600 / 3 | same image, same author, several channels |
| `SCAM_RISK_THRESHOLD` | 2 | pre-rules score needed to OCR |
| `SCAM_ANCHOR_THRESHOLD` | 1.0 | anchor score that routes text to nano |
| `SAFESEARCH_MONTHLY_CAP` / `SAFESEARCH_BURST` / `SAFESEARCH_NORMAL_TIER_RATIO` | 1000 / 30 / 0.7 | smoothed monthly SafeSearch budget |
| `NSFW_GUILD_DAILY_SHARE` | 15 | SafeSearch calls per guild per day |

### Custom-infraction tunables (`automod/constants.py`, §2sexies)

| Constant | Default | Meaning |
|---|---|---|
| `PERSO_SIMILARITY_THRESHOLD` | 0.50 | cosine between a message and a custom infraction's description / examples that routes it to nano, at severity 3. **Starting value — calibrate on real traffic** like `SEUIL_EMBEDDING`. A false route costs one nano call; a miss silently disables the feature |
| `perso_threshold_for(severity)` | 0.65 / 0.57 / 0.50 / 0.44 / 0.38 | the threshold per severity 1..5 (shifted by the same delta as the global threshold, clamped 0.2..0.9) |
| `PERSO_KEYWORD_SCORE` | 0.85 | confidence handed to the routing signal of a keyword hit |
| `PERSO_REFS_CACHE_MAX_GUILDS` / `PERSO_REFS_CACHE_TTL_SECONDS` | 256 / 3600 | in-memory cache of custom reference vectors (≤ 60 per guild) |
| `SOURCE_PERSO` / `CATEGORIE_PERSO` | `perso` / `infraction_perso` | routing source tag / the category nano answers before the engine maps it to `perso:<id>` |
| `custom_infractions.MAX_*` | 10 / 40 / 300 / 5×200 / 20×60 | infractions, name, description, examples, keywords |
| `custom_infractions.KEYWORD_COMPACT_MIN` | 6 | keywords this long also match inside separator-free text (shorter ones match on word boundaries only) |

### Profile tunables (`automod/constants.py`, §4.4)

| Constant | Default | Meaning |
|---|---|---|
| `CALL_TYPE_PROFILE_SAFESEARCH` | `automod_profile_safesearch` | gateway call type |
| `PROFILE_SAFESEARCH_MONTHLY_CAP` | 1000 | fallback only; the enforced cap is env `GOOGLE_VISION_SAFESEARCH_PROFILE_MONTHLY` |
| `PROFILE_SAFESEARCH_BURST` / `PROFILE_SAFESEARCH_NORMAL_TIER_RATIO` | 15 / 0.6 | smoothed monthly pacing of the profile budget |
| `PROFILE_GUILD_DAILY_SHARE` | 30 | profile SafeSearch calls per guild per day |
| `PROFILE_ASSET_TTL_SECONDS` / `PROFILE_PSEUDO_TTL_SECONDS` | 30 d / 30 d | verdict caches (asset key / normalized name) |
| `PROFILE_AVATAR_SIZE` / `PROFILE_BANNER_SIZE` | 512 / 1024 | longest side requested from Discord's CDN |
| `PROFILE_BANNER_FETCH_INTERVAL_SECONDS` | 600 | at most one banner REST fetch per user in this window |
| `PROFILE_DONE_TTL_ASSET_SECONDS` / `PROFILE_DONE_TTL_PSEUDO_SECONDS` | 7 d / 24 h | "already sanctioned" markers |
| `PROFILE_QUEUE_MAX` | 200 | pending profile screenings process-wide |
| `PROFILE_HOLD_DURATION_HOURS` / `PROFILE_HOLD_RENEW_WINDOW_HOURS` | 672 (28 d) / 36 | timeout length of a hold / renewed by the sweep when less than this is left |

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
| `PLANCHER` | table | floor cran per (category, gravity), **derived** from `automod/infractions.py` (message + image families); a server's `sanctions[...].crans` override it per type |
| `POINTS_GRAVITE` | basse 1 / moyenne 3 / haute 7 / critique 15 | recidivism points per gravity |
| `DEMI_VIE_JOURS` | 45 | points half-life (decay) |
| `POIDS_SOURCE` | manuel 1.5 … appel accepté 0 | source-reliability weight |
| `MULT_MEME_CATEGORIE` | 1.5 | same-category repeat multiplier |
| `PLAFOND_CONFIG` | warn 1 / mute 6 / ban 7 | guild `max_action` cran ceiling |
| `CATEGORIES_SENSIBLES` | self-harm/doxxing/sexual | no veteran clemency at haute+ (derived from `InfractionType.sensible`) |
| `CATEGORIES_BAN_DIRECT` | `arnaque_scam` | banned on sight (derived from `InfractionType.ban_direct`) |
| `infractions.CRAN_MIN` / `CRAN_MAX` | 0 / 7 | bounds of a server-set cran |

---

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

### 8.3 Shadow mode (`simulation` state, formerly `dry_run`)

A guild can run the whole system with **nothing applied**. With `state: "simulation"`
the funnel and barème run exactly as normal, but instead of acting the module
posts a **SIMULATION** card to the alert channel (badge *"SIMULATION — aucune
action appliquée"*) showing the sanction that *would* have been taken and its
barème breakdown. **No delete, no warn/mute/ban, no case, no DM.** Toggle it in
`/config` (Automod home → state buttons, or the last wizard step; **Skip** in the
wizard also starts in simulation; recommended the first week). A config that still
has `dry_run: true` / `enabled: false` and no `state` key is migrated on load.

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

---

## 9. Moddy team labeling queue (`services/automod_label_service.py`)

Every automod **sanction** (text or image, shadow simulations included) and every
**doubtful** decision is copied to a Moddy team channel
(`MODDY_AUTOMOD_LABEL_CHANNEL_ID`, queue off while it is `0`) where humans
label it. The goal is to grow the samples the bot learns from — and to catch
its mistakes.

**Doubt** (`Decision.doute`, `engine.detect_doute`): a grounding rejection
(nano wanted to sanction, the guard voided it), a sanction taken with `low`
confidence, a heavy sanction the mini confirmation refused, a scam-anchor hit
nano cleared, SafeSearch `POSSIBLE` / racy / violence.

**The card** (`utils/automod_label_views.py`, English like every team panel):
server, author, channel + jump, why it is here, the bot's decision and what was
actually applied, the text / OCR (spoilered), the image (**always spoilered**),
SafeSearch likelihoods, scam anchors, known-hash match, duplicate count.
Controls (persistent `DynamicItem`s, staff node `automod_label`):

| control | effect |
|---|---|
| category select | corrects the category before labeling (text / scam) |
| **Sanctionable** | image → hash `block` of the lane's kind (scam / nsfw / gore; + OCR text → `arnaque_scam` reference) · text → embedding reference in the chosen category · eval candidate `correct` |
| **Not sanctionable** | image → hash `allow` · text → server precedent `non_sanctionnable` (`source=equipe_moddy`) + eval candidate `faux_positif` · **if the bot sanctioned: revocation** |
| **Skip** | nothing |
| Blocklist terms | Modal V2 (terms, words/compact, category) → `automod_learned_terms` |

**The label never applies a sanction.** Its only enforcement is the reverse:
"not sanctionable" on a decision the bot sanctioned revokes every active
sanction of the case (`db.revoke_sanction`, by `moddy_staff`), lifts the
Discord side (`utils/sanction_reversal.py` — unban / clear timeout, shared with
appeals), adds a timeline comment, posts a notice in the server's alert channel
(server language) and DMs the member through `bot.notifications`. A deleted
message cannot be restored. A labeled item is final.

**Duplicates** — an unlabeled item with the same key within 24 h is counted
(`occurrences`) instead of a new card: images by hash (any server), text by
collapsed text **per server** (the same words can be banter here and harassment
there). Each duplicate's sanction is appended to `details.sanctions`, so one
label revokes them all. A safety cap (`MODDY_AUTOMOD_LABEL_HOURLY_CAP`, 300/h)
bounds the channel.

**Learned data** is global and bounded: hashes (index in memory, 20 000 max),
embedding references (1000 max, oldest evicted, near-duplicates cos ≥ 0.97
skipped), blocklist terms. `/mod automod` shows the queue, the monthly Google
Vision budget and the learned sets, and purges a wrong entry by id.

Tables: `automod_label_items`, `automod_image_hashes`,
`automod_learned_references`, `automod_learned_terms` (+ `automod_profile_holds`
for member profiles, §4.4) — schemas in
[AUTOMOD_AI_CONFIG.md §8](AUTOMOD_AI_CONFIG.md#8-related-tables-read-mostly-for-the-dashboard).

