# Security & dev tools — `/pwned`, `/password`, `/uuid`, `/hash`, `/jwt`, `/color`

Six small tools that run (almost) entirely on Moddy. They follow the shared
contract in [TOOLS.md](TOOLS.md) (card anatomy, phrase-style source footer,
persistence, icons). Pure logic lives in `tools/`, Discord wiring in
`cogs/tools_security.py`, cards and buttons in `utils/tools_security_views.py`,
tests in `tests/test_tools_security.py`.

| Command | Input | Visibility | Network |
|---|---|---|---|
| `/pwned` | Modal V2 (password) | always ephemeral, no `incognito` | HIBP range API (5-char SHA-1 prefix only) |
| `/password [length] [type]` | options | always ephemeral | none |
| `/uuid [version] [count]` | options | always ephemeral | none |
| `/hash [text] [file] [compare]` | options, or Modal V2 when text and file are both empty | `incognito` option (ephemeral by default) | none |
| `/jwt` | Modal V2 (token) | always ephemeral, no `incognito` | none |
| `/color <color>` | option | `incognito` option | none |

All six are global, user-installable and usable in DMs
(`allowed_installs(guilds, users)` + `allowed_contexts(guilds, dms, private_channels)`).

## Secrets: the rule

Slash option values are logged by `cogs/command_logger.py`, so **`/pwned` and
`/jwt` have no option at all**: the secret is typed in a Modal V2 (`PwnedModal`,
`JwtModal`) and is never logged, stored, cached or put in a `custom_id`.
`/pwned` hashes locally and sends HIBP only the first five hex characters of the
SHA-1 (k-anonymity) with `Add-Padding: true`; the card shows only a count. The
generated password of `/password` lives only in the ephemeral message.
`tests/test_tools_security.py` pins these properties (no parameters, no
`save_state`, no logging of secrets, the HIBP request never contains the
password or the suffix).

## Commands

### `/pwned`
Opens a modal (help line + one short input, max 256 characters). On submit:
per-member limit of 8 lookups per minute (`cooldown` error card), a loading card,
then the verdict. Found: red accent, count formatted per locale, advice pointing to
`/password`. Not found: green accent plus a note that this does not make it strong.
Footer: `Checked by <HIBP logo> **Have I Been Pwned**`, and a link button to
haveibeenpwned.com/Passwords. Errors: `timeout`, `network`, `rate_limited`,
`unavailable` (from `PublicAPIError.code`).

### `/password`
`length` 8-128 (default 20), `type`: `strong` (all classes), `clear` (no look-alikes
`Il1O0o` nor awkward symbols), `passphrase` (EFF large wordlist, about one word per
4 characters of `length`, 4 to 10 words, hyphen-separated, one capital, one digit).
Everything goes through `secrets` (CSPRNG, Fisher-Yates with `randbelow`); at least
one character of each class. The card shows the password in a code block, an
entropy bar with a strength label (weak / fair / good / strong / excellent at
40 / 60 / 80 / 120 bits), the kind and size, and the footer "Computed by Moddy
itself". A **Regenerate** button edits the message in place.

### `/uuid`
v4 (`uuid.uuid4`) or v7 (RFC 9562, `uuid.uuid7` on Python 3.14+, an in-house
implementation otherwise), 1 to 10 at once, one per line in a code block.
**Regenerate** keeps version and count.

### `/hash`
MD5, SHA-1, SHA-256, SHA-512 of a text (UTF-8, exactly as typed, up to 4000
characters) or a file (up to 25 MB, read then hashed in `asyncio.to_thread`
behind a 2-slot semaphore; the bytes are dropped right after). Giving both is a
`both` error; giving neither opens the modal (text, file upload, compare field;
`compare` from the option is pre-filled). `compare` accepts any common notation
(case, spaces, `:`, `-`, `0x`) and says which algorithm matched (green card) or
that none did (red card). Public answers name the requester in the footer.

### `/jwt`
Modal with one paragraph input (max 4000 characters). Accepts a bare token,
`Bearer <token>`, `Authorization: Bearer …`, quotes, line breaks. Shows the header
and payload as JSON (long ones are cut and the full text is attached as
`jwt.json`), `iat` / `nbf` / `exp` as Discord timestamps with a status line
(valid / expired / not yet valid), `iss`, `sub`, `aud`, a warning for `alg: none`,
and always the honest note that **the signature is not verified**. Time claims in
milliseconds are understood, absurd values are flagged. Encrypted tokens (JWE)
get a dedicated error. Footer: computed locally.

### `/color`
Accepts `#RGB`, `#RGBA`, `#RRGGBB`, `#RRGGBBAA`, `rgb()/rgba()`, `hsl()/hsla()`,
the CSS named colours (148, `Rebecca Purple` spellings tolerated), a Discord
integer (`5793266`), and `random`. Alpha is parsed but ignored (the card says so).
The card is accented with the colour itself, shows a Pillow-rendered image (wide
swatch + "Aa Moddy" in that colour on a Discord dark and a light surface, set in
**Inter Bold**), a code block with HEX / RGB / HSL / HSV / CMYK / Discord int, the
exact or closest CSS name (within an RGB distance of 30), and WCAG contrast ratios
against Discord dark, Discord light, white and black with AA / AAA marks. The
**Random colour** button edits an ephemeral card in place; on a public card it
answers privately instead.

## Persistence

Three `DynamicItem`s, all state in the `custom_id`, callbacks wrapped with
`@tc.guarded` and re-derived from `interaction` (the item reconstructs on every
click):

| Item | `custom_id` |
|---|---|
| `PasswordRegenButton` | `moddy:tools:password:regen:<strong\|clear\|passphrase>:<length>` |
| `UuidRegenButton` | `moddy:tools:uuid:regen:<v4\|v7>:<count>` |
| `ColorRandomButton` | `moddy:tools:color:random` |

`ToolsSecurityPersistence` (marker `BaseView`, `__persistent__ = True`) registers
them; it and the sample cases are listed in `PERSISTENT_VIEWS` /
`DYNAMIC_ITEM_CASES`, which `utils/tool_views_registry.py` feeds to
`utils/persistent_views.py` and `tests/test_persistent_views.py`. No
`save_state` token is used anywhere. Link buttons (HIBP) need nothing.

## Data and assets

- `tools/data/eff_large_wordlist.txt`: the EFF large wordlist (7776 words, CC BY 3.0
  US, https://www.eff.org/dice), checked at load time.
- `assets/fonts/Inter-Bold.otf`: Inter (SIL OFL 1.1), notice in
  `assets/fonts/README.md`. If missing, Pillow's default font is used.
- Pillow is imported lazily inside the colour code (start-up and memory).

## Configuration, quotas, dependencies

No environment variable, no gateway provider, no quota, no database access, no
new dependency (Pillow was already required). HIBP is keyless and goes through
`bot.public_api` (descriptive User-Agent, public-only resolver, 2 MB cap).

## i18n

Runtime strings under `tools.pwned.*`, `tools.password.*`, `tools.uuid.*`,
`tools.hash.*`, `tools.jwt.*`, `tools.color.*` in the 5 locale files (shared text
in `tools.common.*`). Each tool defines its own `errors.<code>`; others fall back
to `tools.common.errors.*`. Command names, descriptions and option names are in
all 32 `locales/commands/*.json` (e.g. `/password` is `/mot-de-passe`, `/color`
is `/couleur` in French). `tests/test_tools_security.py` asserts key parity and
placeholder parity across the five locales, that every key used in code (including
the dynamic families) resolves, and that the six commands are localized in all 32
files.

## Icons

Heading and button icons are the "UTILITY TOOLS" constants of `utils/emojis.py`.
Still empty until the artwork is uploaded (the helpers drop them cleanly):
`GPP_MAYBE` (`/pwned`), `HIBP` (logo), `PASSWORD`, `NUMBERS` (`/uuid`), `TAG`
(`/hash`), `TOKEN` (`/jwt`). `COLOR`, `DATA_OBJECT`, `DONE`, `ERROR`, `WARNING`,
`REFRESH` are set.

## Known limitations

- Discord does not publish the hex values of its current surfaces; the contrast
  table and preview use reference backgrounds (white and `#1A1A1E`), so ratios are
  indicative for the real themes (see the comment in `tools/color.py`).
- `/jwt` never verifies signatures and does not decrypt JWE; tokens above 4000
  characters cannot be pasted (modal limit).
- `/hash` caps files at 25 MB and hashes a text exactly as typed; Discord may
  normalise line endings in modal input.
- `/pwned` relies on HIBP's availability and on the shared `public_api` limits; the
  per-member 8/minute limit is in memory (reset on restart, per process).
- The entropy figure is an estimate of what the generator produces, not a
  guarantee about how a given site stores the password.
