# Domain and link tools

`/whois`, `/dns`, `/ssl`, `/subdomains` and `/link-check`. They follow the shared contract of [TOOLS.md](TOOLS.md) (card
anatomy, phrase-style source footer, loading / error cards, persistence).

| Piece | File |
|---|---|
| Commands + menu, orchestration | `cogs/tools_web.py` |
| Cards, persistent items | `utils/tools_web_views.py` |
| Network (RDAP, DoH, TLS, crt.sh, redirects, VirusTotal, urlscan.io) | `services/web_tools_service.py` |
| Pure logic: names, RDAP, DoH, TLS, crt.sh | `tools/domain/` |
| Pure logic: URLs, redirect chain, signals, verdict | `tools/linkcheck.py` |
| Gateway: adapters / clients | `gateway/adapters/virustotal.py`, `gateway/adapters/urlscan.py`, `gateway/clients/web_security.py` |
| Tests | `tests/test_tools_web.py` |

All commands are global (servers, DMs, user installs), take an `incognito`
option (ephemeral by default) and at most 10 lookups a minute per member
(`LOOKUPS_PER_MINUTE`). On a public answer the footer adds `requested by @user`.

## Input

Every command accepts a bare domain, an IDN (`bücher.de`) or a whole URL
(`https://user@www.example.com:8443/x`): the host is extracted, lower-cased and
converted to punycode (`tools/domain/names.py`). Refused with a clear card:
empty, malformed, too long, `localhost` / `.local` / `.internal`…, private or
reserved IP addresses, and (except `/ssl`) IP addresses at all.

The *registrable domain* (`example.co.uk` for `a.b.example.co.uk`) is found with a
compact heuristic (explicit multi-part suffixes + "ccTLD preceded by a generic
label" + a list of shared-hosting suffixes). It is not the full Public Suffix
List on purpose (230 KB of rules to keep fresh); an exotic suffix can make the
card show the registry's answer for the wrong level, which is harmless.

## `/whois domain`

RDAP, the JSON successor of WHOIS. The IANA bootstrap file (`data.iana.org/rdap/dns.json`,
flattened to `{tld: server}`, cached 24 h in Redis) names the registry's server; when the
TLD is missing we fall back to `rdap.org`. Thin registries (`.com`) only know the
registrar, so when the expiry date or the name servers are missing the registrar's own
RDAP record (the `related` link) is fetched and merged. Records are cached 1 h.

Card: the registered domain (and its punycode form for an IDN) as a code block, alerts
first, then registrar, creation / expiry / update dates (Discord timestamps), statuses
(humanised: *transfer locked*, *on hold*…), name servers, DNSSEC. A button links to the
registrar's record.

* **Domain age alert** — created 7 days ago or less: error accent and a strong
  warning; 30 days or less: warning accent. Brand-new domains are where phishing lives.
* Expired → error accent; expiring in 30 days or less → warning; `on hold`, `pending
  delete`, `redemption`, `inactive` statuses get their own warning line.
* A sub-domain shows the record of the registered domain (and says so). A shared-hosting
  name (`me.github.io`) shows the **platform's** record with a note: the customer owns no
  registration.
* Errors: `not_registered` (the registry answered 404), `no_rdap` (the TLD does not
  publish RDAP), shared codes for timeout / rate limit.

## `/dns domain`

DNS-over-HTTPS in its JSON form: Cloudflare (`cloudflare-dns.com/dns-query`), Google
(`dns.google/resolve`) as fallback, no dependency. One lookup asks the eight types
(A, AAAA, CNAME, MX, NS, TXT, CAA, SOA) in parallel, plus `_dmarc.<name>` and five common
DKIM selectors (`default`, `google`, `selector1`, `selector2`, `k1`). Result cached 5 min.

Card: the records in a code block (one line each, long values clipped; past ~2600
characters the full list is attached as a `.txt`), the TTL range, "DNSSEC validated by
the resolver" when the resolver's AD bit is set, and an **Email protection** section
(SPF policy `-all` / `~all` / `?all` / `+all`, DMARC policy, DKIM selectors found) shown
on the *All* and *TXT* views. Types that could not be queried are named in a hint.

A **Record type** select under the card filters one type. It is a persistent
`DnsTypeSelect` whose `custom_id` carries the domain
(`moddy:tools:dns:t:<domain>`, domain ≤ 60 characters — for a longer name the select is
simply not shown). A click re-reads the 5-minute cache (or queries again).

DKIM selectors are a probe, not an inventory: a domain using another selector shows no
DKIM line, and the card never says "no DKIM".

## `/ssl domain [port]`

Reads the certificate the server presents, directly from Moddy. The host is resolved
first and **only public addresses are connected to** — and the connection goes to that
checked address, not to the name again (no DNS-rebinding window). Allowed ports:
443, 8443, 465, 636, 853, 993, 995 (a port typed in the input is honoured when allowed):
anything else would turn the command into a port scanner.

Two handshakes at most: one verifying (system trust store, hostname check); if OpenSSL
refuses the certificate, a second one without verification still reads it, and the
diagnosis combines OpenSSL's verify code with the certificate's own facts (dates, names,
self-signature). Card: verdict (valid / valid but expires within 14 days / invalid with
**every** reason: expired, not yet valid, self-signed, untrusted issuer, hostname
mismatch, revoked), subject, issuer, validity with relative time, the first names (SAN),
TLS version, key and signature algorithm. The footer says the certificate was read
directly from the server (no third party is involved).

Limits: no OCSP / CRL / Certificate Transparency check, no chain display; "valid" means
"trusted by Python's certifi/system store and matching the name".

## `/subdomains domain`

`crt.sh` (Certificate Transparency logs) as JSON, query `%.domain`, 20 s timeout, response
cap 24 MB parsed in a worker thread. `www.example.com` is treated as `example.com`.
Names are deduplicated, wildcards (`*.x`) are not listed as subdomains but counted, e-mail
addresses and foreign names are dropped; at most 5000 names are kept (the true total is
shown). Result cached 6 h (the *extracted* list, not crt.sh's raw answer).

**Fallback.** crt.sh is regularly overloaded (502 / timeouts, even for small domains).
When it fails (timeout, network, 5xx, 429, unreadable answer) Moddy asks **Cert Spotter**
(`api.certspotter.com/v1/issuances?include_subdomains=true&expand=dns_names`, keyless,
rate limited) and walks at most 3 pages of issuances (`CERTSPOTTER_MAX_PAGES`). Its list
can be shorter than crt.sh's, so the card says which provider answered
(`tools.subdomains.fallback`) and the footer names it. That answer is cached 30 min
instead of 6 h so crt.sh is tried again soon. A domain too big for crt.sh
(`too_large`) is not sent to the fallback: it would show a sliver of it. Only when both
fail does the user get the "did not answer" card with **Retry**.

Card: count, 40 names per page, **Previous / Next** and **Export** (`.txt`, sent
privately) when there is more than one page — all persistent
(`moddy:tools:subdomains:{prev|next}:<page>:<domain>`, `…:export:<domain>`). Pagination
re-reads the cache. crt.sh is often overloaded: a failure gives an explicit card with a
**Retry** button (`moddy:tools:subdomains:retry:<domain>`); a domain with too many
certificates asks for a more specific name.

Honest limit, said on the card: only names that appeared in a public certificate are
listed — private or HTTP-only subdomains are invisible.

## `/link-check url`

A link is *never opened by the member* and never shown as a clickable link: the card
prints it in a code block.

1. **Normalisation** — `https://` is added when missing, the fragment is dropped,
   credentials in the URL are split off (`paypal.com@evil.example` is a signal, and they
   are never sent anywhere), the host is converted to punycode. Private / reserved hosts
   are refused.
2. **Redirect chain** (`tools/linkcheck.follow`, fetched by
   `WebToolsService._fetch`): HEAD first, GET when HEAD is not enough (errors, HTML
   bodies), up to 10 hops, HTML `<meta http-equiv="refresh">` followed, each hop validated
   by `utils/safe_http.validate_public_url` (ports 80/443 only) and connected through the
   public-only resolver, 8 s per hop, 32 KB of body at most, loops detected. An invalid
   HTTPS certificate stops the chain with its own message.
3. **Local signals** — shortener (list of ~45), look-alike / mixed-script IDN, user-info
   trick, raw IP, plain HTTP, many redirects, **domain age** (RDAP of the final domain,
   same cache as `/whois`; skipped for IPs and shared-hosting names).
4. **Reputation** (gateway, both optional) — see below.
5. **Verdict** (`linkcheck.assess`): *No threat detected* (green), *Suspicious link*
   (warning), *Dangerous link: do not open it* (red), or *Could not be reached*
   (neutral). Hard signals (≥ 3 VirusTotal engines, urlscan.io "malicious") alone make a
   link dangerous; soft ones add up (3 soft, or a very recent domain + 2 soft); weak ones
   (shortener, HTTP, 4+ redirects) are shown but never decide. Green carries the line
   "No threat detected does not mean a link is safe".

The card is sent as soon as the local part is done, then **edited as each provider
answers** (spinner line per provider, verdict recomputed every time). Reputation is asked
about the **final** URL (what the member would land on).

### VirusTotal (`gw.virustotal`)

`url_report` first; a report younger than 3 days is used as is; otherwise the URL is
submitted and the analysis is polled twice (after 15 s and 10 s); an older report is used
(marked "older analysis") if the new one is not ready. Existing results are cached 15 min
per URL so a link posted by many people costs the free key once. Provider states shown on
the card: *Analysing…*, *Analysis not finished yet* (with the report button), *Busy right
now*, *Could not be checked*. The URL travels in `CallSpec.binary`, never in the logged
`payload` / `metadata` (a link can carry a token).

**The free VirusTotal API forbids commercial use.** Moddy has a Premium tier: either get a
VirusTotal commercial licence or leave `VIRUSTOTAL_API_KEY` unset (the command then runs
on its local checks and urlscan.io).

### urlscan.io (`gw.urlscan`)

Scan submitted with visibility **`unlisted`**; the result call polls inside the adapter
(first look after ~8 s, then every 3 s, up to 40 s) so the gateway sees one call. The page
screenshot goes in a `MediaGallery` as a **spoiler** (the page may be shocking) and a
button links to the full report. urlscan refuses some targets (unresolvable, blocked): the
card says "This page could not be scanned". Not asked when the chain could not reach the
site. Results cached 15 min per URL.

### Degradation

| Situation | Card |
|---|---|
| Neither key set | local checks only + "Online reputation checks … are not enabled on this Moddy." |
| One key set | the other provider's line is simply absent |
| `link_check` daily quota used up | local checks + "Daily limit reached: only Moddy's own checks were done" |
| Provider rate limit / 429 / 402 / 403 | *Busy right now, try again in a minute* |
| Provider error / timeout | *Could not be checked* |

> There is no "Check links" message menu: Discord caps an app at five message
> context menus and they are all taken (see VOICE_TRANSCRIPTION.md).

## Gateway wiring (docs/infra/API_GATEWAY.md)

| | VirusTotal | urlscan.io |
|---|---|---|
| Env key | `VIRUSTOTAL_API_KEY` | `URLSCAN_API_KEY` |
| Operations (`model`) | `url_report`, `url_submit`, `analysis` (`public_api`) | `scan_submit` (`scan`), `scan_result` (`result`) |
| Provider-account limits | 4/min (`VIRUSTOTAL_PER_MINUTE`), 500/day (`VIRUSTOTAL_PER_DAY`) — one budget for the three operations | scan: 10/min, 400/day (`URLSCAN_SCAN_PER_MINUTE`, `URLSCAN_SCAN_PER_DAY`); result: 60/min, 4000/day (`URLSCAN_RESULT_PER_MINUTE`, `URLSCAN_RESULT_PER_DAY`) |
| Timeout | `GATEWAY_TIMEOUT_SEARCH` (25 s) | scan submit 25 s; `scan_result` `GATEWAY_TIMEOUT_SCAN` (60 s, > the adapter's 40 s wait) |

User quota: call type **`link_check`** — 10/day/user, 100/day/server, unlimited globally
(seeded in `db/base.py`). One check counts **once**: the quota is attached to the first
provider call (VirusTotal if configured, else urlscan) and not to the others.

## Statistics

`domain_tools.used` (`tool`: whois | dns | ssl | subdomains) and `link_check.used`
(`source`: slash | menu; `verdict`: safe | suspicious | dangerous | unreachable).

## i18n

Runtime strings under `tools.domain` (input errors shared by the four domain commands and
`/link-check`), `tools.whois`, `tools.dns`, `tools.ssl`, `tools.subdomains`,
`tools.linkcheck`, identical keys (and placeholders) in the five locales — checked by
`tests/test_tools_web.py`. Slash command names, descriptions and parameters, and the
are in the 32 `locales/commands/*.json`.
`whois`, `dns` and `ssl` keep their Latin names in every language, like `hash`.

## Persistence

Four `DynamicItem`s, registered by `ToolsWebPersistence` and listed in
`DYNAMIC_ITEM_CASES`: `DnsTypeSelect`, `SubdomainsPageButton`, `SubdomainsExportButton`,
`SubdomainsRetryButton`. Nothing is stored for them: the domain (and page) is in the
`custom_id`, the data comes back from the Redis caches or a fresh lookup. On an
**ephemeral** card a click edits the card in place; on a **public** card it answers
privately instead, so one member's click never rewrites what the channel reads. The
link-check and whois / ssl cards have no interactive component beyond link buttons.

## Known limitations

* crt.sh and RDAP servers are third-party and flaky; RDAP data varies by registry (GDPR
  redacts the registrant, which is deliberately never shown).
* Domains longer than 60 characters get cards without the select / pagination buttons.
* The redirect chain is followed with a `HEAD`/`GET` from Railway's IP; some sites serve
  bots a different page, and a site that blocks datacenter IPs shows as unreachable.
* "No threat detected" only means no engine and no heuristic complained; new phishing
  pages are by definition unknown to reputation services.
* The free VirusTotal key (4 requests / minute) can serve about one fresh analysis a
  minute for the whole bot: busy periods show *Busy right now*.
* Links sent to VirusTotal and urlscan.io leave Moddy (query strings included): the footer
  names them, and the scan is `unlisted` on urlscan.io, but a secret in a URL still goes
  to those services.
