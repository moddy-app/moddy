# Lookup tools — `/status`, `/company`, `/username`

Part of the utility tools family ([TOOLS.md](TOOLS.md) is the shared design
contract). Three global commands (servers, DMs, user installs), all answering
**ephemeral by default** through the `incognito` option.

| File | Role |
|---|---|
| `cogs/tools_lookup.py` | The three commands, the `/username` orchestration (cooldown, slots, live progress) |
| `utils/tools_lookup_views.py` | Cards, persistent views and `DynamicItem`s |
| `tools/status_registry.py` | Service registry, Statuspage parsing, 60 s cache (pure) |
| `tools/company.py` | SIREN/SIRET checks, response parsing, INSEE code tables (pure) |
| `tools/whatsmyname.py` | Dataset parsing, detection rule, search engine, pagination, cooldown/slots (pure) |
| `tests/test_tools_lookup.py` | Logic, cards, custom_id contract, command flows, i18n parity |

Every outgoing request goes through `bot.public_api` (no gateway, no key, no
quota): free public endpoints, and `/username` would flood the gateway log.

---

## `/status [service]`

Is Discord / GitHub / Cloudflare… up? Services are Atlassian **Statuspage**
pages (`<page>/api/v2/summary.json`): 16 are registered (Discord, GitHub,
Cloudflare, OpenAI, Claude, Reddit, Twitch, Dropbox, Atlassian, Vercel, Netlify,
Zoom, Epic Games, npm, DigitalOcean, Figma). A service only gets an entry once
its endpoint was verified; products with another status-page engine (Notion,
Slack, Stripe…) are deliberately absent.

* **No argument** — overview: one line per service (`dot **Name** · state`, the
  headline incident appended), problems first, a one-sentence summary on top,
  a select to open one service and a **Refresh** button.
* **With `service`** — autocomplete on the name (value = registry key; free text
  is also resolved). Detail card: affected components grouped by Statuspage
  group (worst first), ongoing incidents (status, "since", latest update),
  scheduled / in-progress maintenance, a **Refresh** button and a link to the
  status page.
* Accent = verdict: green all fine, yellow minor, red major/critical, blue
  maintenance, grey unreachable.
* Cache: one *compact* summary per service, 60 s in process memory (an
  unreachable page is retried after 15 s; **Refresh** cannot bypass the cache
  more than once every 10 s). The raw payload (Cloudflare: ~230 KB) is parsed and dropped.
* An unreachable page never raises: the service shows as "Unknown".

**Persistence.** Overview select `moddy:tools:status:detail` (option value =
service key) and button `moddy:tools:status:refresh` are static (`StatusOverviewView`,
registered with `bot.add_view`). The detail card's refresh is a `DynamicItem`
`moddy:tools:status:refresh:<key>`. Choosing a service in the overview answers
with a **new ephemeral message**, so a public overview is never edited by one
person for everybody. Nothing is stored.

## `/company search`

French company by name, **SIREN** (9 digits) or **SIRET** (14 digits) on the
open `recherche-entreprises.api.gouv.fr` API (data.gouv / INSEE; free, keyless,
~7 req/s; Redis cache 5 min through `public_api`, one retry on 5xx/timeout).

* The query is classified automatically (spaces / dots / dashes tolerated in a
  number). A number whose checksum fails is rejected before any request
  (SIREN: Luhn; SIRET: Luhn, plus La Poste's digit-sum-by-5 exception). A name
  needs at least 3 characters.
* One match (or a number): the **record** straight away. Several matches: a
  list (up to 10) with a select; **each option's value is the SIREN**, so the
  view is stateless and a click simply re-queries that SIREN. The record opens as
  a new ephemeral message.
* Record: SIREN (or the searched SIRET) in a code block, status (active /
  closed on…), acronym and commercial names, legal form, NAF code + division
  label, creation date + age, type (association, ESS, SME/ETI/large), searched
  establishment (SIRET search), headquarters, establishment counts, headcount
  bracket (+ year), up to 3 directors (non-disclosable ones are skipped), and a
  link button to the **Annuaire des Entreprises**. Accent: green active, red closed.
* Legal-form and NAF labels are vendored tables in the official **French**
  wording (the API returns codes only); everything around them is translated.
* Persistence: `moddy:tools:company:pick` (static, `CompanyResultsView`).

## `/username username`

Where does this username exist? Uses the community
[WhatsMyName](https://whatsmyname.app) dataset (`wmn-data.json`, **CC BY-SA 4.0**,
~650 usable sites), cached 24 h (memory + Redis).

* **Checks** — 40 requests in flight, 2 per site, 6 s per site, 45 s overall. A
  profile counts as found only if the HTTP status equals `e_code`, `e_string`
  is in the page and `m_string` is not (so a "not found" page answering 200 does
  not match). Only the link to a found profile ever leaves the search, never page content.
* **Live progress** — a loading card with a 12-cell bar and `done/total`, edited every 2 s.
* **Result** — profiles grouped by category (`social`, `gaming`, `coding`…) in
  a fixed order, paginated (≈2600 characters of profile lines per page, a
  category continued on the next page repeats its heading), a `.txt` export and
  a link to WhatsMyName (licence attribution + "provided by" phrase).
* **Excluded** — the NSFW category is dropped when the dataset is parsed.
* **Guard rails** — per-user cooldown of 120 s (in memory), at most **2
  searches at once bot-wide** (never queued: a full pool answers "busy" at once
  and does not consume the cooldown; a dataset failure refunds it).
* **Honest limits shown on every result** — "some sites block datacenter IP
  addresses, so a few profiles can be missing; a match only means the name is
  taken there, not that it is the same person". The card also says how many
  sites did not answer, and whether the 45 s deadline cut the search.
* Username: letters, digits, `.`, `_`, `-`, max 40 (a leading `@` is dropped).

**Persistence.** The result list does not fit in a `custom_id`: it is kept 1 h
(`RESULT_TTL`, privacy) under a 10-character token with `save_state`/`load_state`
(Redis, in-process LRU fallback). Buttons: `moddy:tools:username:prev|next:<token>:<page>`
and `moddy:tools:username:export:<token>` (`DynamicItem`s, registered by
`LookupPersistence`). After the TTL a click answers the shared "expired — run the
command again" card. The "requested by" footer of public results is re-derived
from the message's interaction metadata.

---

## Data sources and attribution

| Tool | Footer phrase | Notes |
|---|---|---|
| `/status` | "Provided by Atlassian Statuspage" (overview), "Provided by *<Service> Status*" (detail) | Each service's own public status page |
| `/company` | "Data provided by Annuaire des Entreprises" | Open data, Etalab licence |
| `/username` | "Data provided by WhatsMyName" + link button | CC BY-SA 4.0, named on the card and in the `.txt` export |

## Environment, gateway, quotas

None. No env var, no gateway provider, no quota seed, no new dependency.

## i18n

* Runtime strings: `tools.status.*`, `tools.company.*`, `tools.username.*` in the
  5 locales (key parity and placeholder parity are tested).
* Commands: `status`, `company`, `username` in the 32 `locales/commands/*.json`
  (French: `/statut`, `/entreprise`, `/pseudo`).
* Error codes: `tools.status.errors.not_found`; `tools.company.errors.{too_short,bad_number,not_found}`;
  `tools.username.errors.{empty,too_long,invalid,busy,dataset}`; network codes fall back to `tools.common.errors.*`.

## Icons

Used (some still empty until artwork is uploaded, then dropped cleanly):
`MONITOR_HEART`, `BLUE_STATUS`, `GREY_STATUS` (status dots — every state is also
written as text, so a missing dot loses nothing), the service logos
(`DISCORD_LOGO`, `GITHUB`, `CLOUDFLARE`, `OPENAI`, `ANTHROPIC`, `REDDIT`,
`TWITCH`, `DROPBOX`, `ATLASSIAN`, `VERCEL`, `NETLIFY`, `ZOOM`, `EPIC_GAMES`,
`NPM`, `DIGITALOCEAN`, `FIGMA`), `BUSINESS`, `DATA_GOUV`, `PERSON_SEARCH`,
`WHATSMYNAME`. Existing icons reused for their own meaning: `REFRESH`, `BACK`,
`NEXT`, `DOWNLOAD`, `WEB`, `GREEN/YELLOW/RED_STATUS`.

## Known limitations

* `/username`: results depend on where Moddy runs. Railway datacenter IPs are
  blocked or captcha'd by a share of the sites (they count as "did not answer"),
  and WhatsMyName rules rot as sites change — expect false negatives and some
  false positives. It is a lead, not proof of identity.
* The per-user cooldown and the 2-slot cap are per process (the bot is one process).
* `/status` only covers Statuspage-based pages; adding a service means checking
  its `summary.json` first.
* `/company` covers French companies only; headcount is a bracket from the last
  declared year; directors may be absent (non-disclosable).
