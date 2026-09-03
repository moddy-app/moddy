# Bump Reminder — never miss a bump window

Server directories (DISBOARD, DiscordL, French.gg…) let a server climb back to
the top of their listing with a `/bump` command, reusable every one to four
hours. The window is trivial to miss, and a missed window is visibility lost.

This module watches the channel a server points it at, recognises that a bump
actually **went through**, thanks whoever ran it, and calls the channel back the
moment the command becomes available again.

## Table of contents

- [The hard part is not the timer](#the-hard-part-is-not-the-timer)
- [Supported directories](#supported-directories)
- [Detection](#detection)
- [Files](#files)
- [Limits (free / premium)](#limits-free--premium)
- [Configuration schema](#configuration-schema)
- [The `bump_reminders` table](#the-bump_reminders-table)
- [The two cards](#the-two-cards)
- [Pings](#pings)
- [The sweeper](#the-sweeper)
- [Cost](#cost)
- [Persistence](#persistence)
- [i18n](#i18n)
- [Adding a directory](#adding-a-directory)

---

## The hard part is not the timer

Scheduling a message two hours out is a row in a table. The difficulty is
knowing whether to schedule it at all.

A directory answers `/bump` **from the same bot, with the same command**, whether
the bump went through or the server is still on cooldown. Watching for the
command would therefore arm reminders off failures — and a reminder that fires
an hour early is worse than none: it pings a channel, somebody runs the command,
and the directory says no.

So the module detects *"the bump is through"*, never *"the command was run"*.
When the two cannot be told apart, it stays quiet. That asymmetry is deliberate:
a missed reminder costs one bump window, a false one costs the channel's trust
in every reminder after it.

---

## Supported directories

Ordered as every menu shows them, largest audience first.

| # | `key` | Directory | Command | Cooldown | What a success looks like |
|---|---|---|---|---|---|
| 1 | `disboard` | DISBOARD | `/bump` | 2 h | Anything visible (refuses privately) |
| 2 | `dsmonitoring` | DSMonitoring | `/bump` | 4 h | "successfully liked" wording; embed timestamp = next like |
| 3 | `dinvites` | D-INVITES | `/bump` | 2 h | An image named `bump.png` — and nothing else |
| 4 | `dl` | DiscordL | `/bump` | 1 h | Banner URL under `/v2/bump/`, or "a été bump par" |
| 5 | `beemp` | Beemp | `/bump` | 1 h | "Beemp done successfully" wording |
| 6 | `dtop` | DiscordTop | `/boost` | 1 h | Asset named `boost-success`; states its next boost |
| 7 | `frenchgg` | French.gg | `/bump` | 2 h | Anything visible (refuses privately); its own "remind me" button |

The order is an editorial judgement, not a measured ranking: DISBOARD is
verifiably the largest (700k+ listed servers, and the only global one here); the
rest are ordered by the bot account's age and general reputation, because no
comparable published guild counts exist for them. Reordering is a one-line move
in `BUMP_BOTS` — nothing derives meaning from the position.

---

## Detection

`bumpreminder/` is pure Python: no Discord calls, no database, no network. That
is what lets `tests/test_bump_reminder.py` replay the **real captured reply** of
all seven directories (`tests/data/bump_payloads.json`) through the real code.

The funnel, cheapest test first:

1. **The author is a known directory.** One dict lookup over seven entries. This
   runs for every message the bot can see, and rejects essentially all of them.
2. **The command matches** — `interaction.name` against that directory's
   `command_names`, *when Discord sends it*. Necessary, never sufficient: a
   cooldown reply carries the same name.
3. **Harvest.** Everything textual, every media URL and every button `custom_id`,
   walked recursively — Components V2 nests, and three directories hide their
   only usable marker inside a nested node.
4. **Failure vetoes.** A shared multilingual cooldown blocklist (`wait`,
   `attendre`, `already`, `déjà`, `cooldown`, `espera`, `warte`…) plus the
   directory's own failure markers.
5. **Success.** At least one of that directory's markers.
6. **Due time.** The directory's own stated next-bump time when it publishes one,
   otherwise `now + interval`.

Steps 4 and 5 are in that order on purpose: **a failure marker vetoes, it never
competes**. A message carrying both is a message we do not understand.

### Three kinds of marker

Directories are not built alike, so neither are the markers:

- **`success_text`** — regexes over the harvested text, written multilingual
  because these bots answer in the *reader's* language.
- **`success_media`** — a substring of an image URL. Several directories name
  their assets after the outcome (`boost-success.png`), which beats any sentence
  and survives every translation.
- **`success_custom_id`** — a button's id. French.gg only offers its own
  "remind me" button on a success, which makes that button the tell. Its suffix
  is the bumper's user id, which the detector falls back on when Discord omits
  the interaction metadata.

### Two directories refuse privately

DISBOARD and French.gg answer a cooldown with an **ephemeral** message, which
the gateway never delivers to a bot. Every `/bump` reply Moddy can actually
*see* from them therefore went through.

That is modelled as `refusal_is_ephemeral`. For DISBOARD it makes detection
language-proof — Japanese, Korean, Russian, Turkish all work, with no phrase
list to maintain. For French.gg it is what makes its "remind me in 2h" button
safe to lean on: that button would arguably make *more* sense on a cooldown than
on a success, so without the guarantee it would have been the wrong marker.

Two guard rails keep the shortcut honest:

- it applies **only** to a message Discord tagged with `/bump`. Without a command
  name the reply could be anything the directory posts, so the ordinary markers
  have to carry it instead;
- the directory's own failure markers still veto, as insurance against the day
  it changes its mind.

The shared text blocklist is a **separate** decision, carried by
`answers_in_any_language` and skipped only for DISBOARD. Private refusals alone
are not reason enough to drop it: the flag is an assumption about someone else's
product, and the blocklist is the net under that assumption. Only a directory
that *also* answers in languages we cannot enumerate has anything to lose by
keeping it — and French.gg, being a French listing, does not. Setting either flag
on another directory needs the same evidence.

### A success marker must never appear on a refusal

This is the failure mode that actually bites, and it bit twice.

`Résultat du Bump sur DiscordL` looked like a success marker. It is DiscordL's
message **header**, printed on the refusal too. `propulsé` looked like one for
DiscordTop. Its refusal reads "Ce serveur vient **déjà** d'être propulsé".

Both were harmless — but only because some veto happened to fire first
("attendre", "déjà"). Reword either refusal and the detector starts arming
reminders off failed bumps. `TestCapturedRefusals` now forbids it outright:
no success marker of a directory may match that directory's own captured
refusal. It fails against both original markers, naming them.

The lesson generalises: **the signal is what differs between the two messages**,
never what the directory says about bumping in general. "Boost envoyé" versus
"Boost impossible" is a signal. "propulsé" is vocabulary.

### And a refusal need not sound like one

DSMonitoring answers a cooldown with *"You are so hot! 2 hours 19 minutes until
the next like."* Beemp with *"You can bump again `<t:…>`"*. Neither contains a
single word of the shared cooldown blocklist — no "wait", no "already". Both were
rejected only because no success marker happened to fire.

So each carries an explicit failure marker taken from its captured refusal. And
Beemp's stays **per-directory on purpose**: "you can bump again at X" is a
refusal there, but it is semantically what a *success* says about the next window
elsewhere — DiscordTop's success reads "Prochain boost disponible `<t:…>`".
Promoting it to the shared blocklist would kill real bumps.

Captured refusals now exist for all five directories that have a visible one.
DISBOARD and French.gg refuse ephemerally, so there is nothing to capture.

### D-INVITES is the fragile one

Its successful bump is a bare image with a "view the server" button and **not one
word of text**. The asset filename (`bump.png`) is the only signal a success
carries. If D-INVITES renames that file, detection for it stops — and it is the
one detector here that would.

Its *refusal*, by contrast, is explicit: `bump-error.png` plus "Tu pourras bump à
nouveau `<t:…>`". Both are captured in `tests/data/bump_refusals.json` rather
than guessed — which matters, because the guesses were wrong. The markers were
`cooldown.png` and `error.png` until a real refusal showed the file is called
`bump-error.png`. The refusal was rejected before the fix, but only because no
success marker happened to fire; nothing vetoed it. That is the weakest reason a
detector can be right, and
`TestCapturedRefusals::test_a_captured_refusal_is_vetoed_not_merely_unmatched`
now forbids it.

### Stated next-bump times, and the freshness window

DiscordTop prints `Prochain boost disponible <t:…:R>`; DSMonitoring stamps its
embed with the next like. Both are the authority on their own cooldown, so they
outrank the configured interval — which also means a server that mis-set the
interval still gets a correct reminder.

A stated time is only believed if it lands **between 5 minutes and 24 hours out**.
That window is not decoration: DiscordL's footer carries a `<t:…>` that is the
*current* time, and believing it would schedule a reminder three seconds out.

---

## Files

| File | Role |
|---|---|
| `bumpreminder/registry.py` | The seven directories and their markers |
| `bumpreminder/detect.py` | The funnel, the due-time reader, the interval parser |
| `modules/bump_reminder.py` | `ModuleBase`: config schema, validation, routing |
| `cogs/bump_reminder.py` | The listener and the 30-second sweeper |
| `utils/bump_views.py` | Both cards, the opt-in button, the persistence marker |
| `modules/configs/bump_reminder_config.py` | The `/config` panel and its modal |
| `db/repositories/bump.py` | `bump_reminders` — the live half |
| `tests/data/bump_payloads.json` | The seven captured replies |

---

## Limits (free / premium)

Reminders are capped **per directory**, not as a global total, so a free server
can still cover every listing it bumps on:

| | Per directory | In total |
|---|---|---|
| Free | 1 | 7 |
| Premium | 3 | 21 |

One is all a normal server needs: a bump puts the *whole server* on that
directory's cooldown, so a second entry for the same listing only makes sense to
call a **second channel** back — a large-server need, hence premium.

Premium is `utils.subscription.is_guild_premium(bot, guild_id)`. There is no
PREMIUM guild attribute (see [docs/PREMIUM.md](PREMIUM.md)).

Two reminders for the same directory **in the same channel** are refused at any
tier: both would post the same card, every time.

---

## Configuration schema

`guilds.data.modules.bump_reminder`:

```json
{
  "version": 1,
  "reminders": [
    {
      "id": "br_a1b2c3d4",
      "bot": "disboard",
      "channel_id": 123456789012345678,
      "role_ids": [234567890123456789],
      "ping_mode": "button",
      "interval": 7200,
      "enabled": true,
      "created_by": 345678901234567890,
      "created_at": "2026-09-03T10:00:00+00:00"
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `bot` | A `key` from `BUMP_BOTS`. An entry naming a retired directory is dropped on read rather than raised — a delisted directory must not brick the panel. |
| `channel_id` | Both where Moddy watches for the bump **and** where it answers. |
| `role_ids` | Up to 5. Optional. |
| `ping_mode` | `auto` / `button` / `never` — how the last bumper is mentioned. |
| `interval` | Seconds, 300 – 86 400. Overridden by a time the directory states itself. |
| `enabled` | Paused entries watch nothing. |

`enabled` on the **module** is computed, never stored: it is true as soon as one
entry is enabled and has a channel.

### Detection is channel-scoped, arming is server-scoped

A bump is only *read* in the channel the entry points at — the reminder belongs
to a channel the server chose, and answering in one it never picked would be
Moddy talking out of turn.

But once read, it arms **every** entry for that directory in the guild, because
the cooldown belongs to the server, not the channel. That is what makes a
premium server's three DISBOARD entries useful rather than three quarters dead.

---

## The `bump_reminders` table

```sql
CREATE TABLE bump_reminders (
    guild_id          BIGINT      NOT NULL,
    bot_key           TEXT        NOT NULL,
    channel_id        BIGINT      NOT NULL,
    due_at            TIMESTAMPTZ NOT NULL,
    sent              BOOLEAN     NOT NULL DEFAULT FALSE,
    bumper_id         BIGINT,
    opt_in            BOOLEAN     NOT NULL DEFAULT FALSE,
    thanks_channel_id BIGINT,
    thanks_message_id BIGINT,
    bumped_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (guild_id, bot_key)
);
CREATE INDEX idx_bump_reminders_due
    ON bump_reminders (due_at) WHERE sent = FALSE;
```

**Repository:** `db/repositories/bump.py` — `BumpReminderRepository`

Keyed on the *directory*, not the config entry, for the reason above. A server
therefore owns at most seven rows however often it bumps: nothing accumulates,
and there is no cleanup job to forget to run.

**A second bump before the reminder fires is an upsert** that pushes `due_at`
back, clears `sent`, and records the new bumper. That is the whole "restart the
countdown" behaviour, obtained from the schema rather than coded. `opt_in`
resets with it: a "ping me" armed for the previous bump has been honoured or
superseded, and carrying it over would ping somebody for a bump that is no
longer theirs.

`claim_due_bumps` flips the rows to `sent` **inside the statement that returns
them** (`FOR UPDATE SKIP LOCKED`), so a second sweeper — or this one restarting
mid-pass — can never post the same reminder twice.

---

## The two cards

The whole design of the feature is in the difference between them.

### The thank-you, immediately

```
### <:rocket_launch:…> Merci @juthing !
Le serveur vient de remonter sur <:disboard:…> **DISBOARD**.
Prochain bump possible dans 2 heures.

[ ⏰ Me rappeler ]        ← only when ping_mode is "button"
```

It renders the bumper's mention but **does not notify them**: they typed the
command one second ago and are looking straight at the channel. Buzzing somebody
for their own action is noise, so it goes out with `AllowedMentions.none()`.

### The reminder, when the window opens

```
@Bumpeurs @juthing                      ← TextDisplay at the view's top level
┌──────────────────────────────────
│ ### <:rocket_launch:…> C'est reparti
│ Le serveur peut de nouveau être bumpé sur <:disboard:…> **DISBOARD**.
│ </bump:947088344167366698>
│ -# Dernier bump par @juthing, il y a 2h.
└──────────────────────────────────
```

The command is a real command mention, so it is one click away.

---

## Pings

Discord rejects any message carrying both a `content` field and the
`IS_COMPONENTS_V2` flag that discord.py sets for every `LayoutView` — so the
mentions cannot ride in `content` (see
[docs/TICKETS.md § Pings](TICKETS.md#pings) for where that bites elsewhere).

They ride in a **`TextDisplay` added to the view itself**, above and outside the
container. It is an ordinary V2 component, it renders outside the card's frame,
and it genuinely notifies. Here the mention *is* the message, so unlike a ticket
card it belongs in the message rather than in a self-deleting ghost ping nobody
can scroll back to.

**What actually notifies is decided by `allowed_mentions`, never by what the card
draws.** `reminder_mentions()` builds it from the *resolved* role and member
objects — never `roles=True` — so:

- a role deleted since the config was written simply drops out;
- a bumper who left the guild drops out, and the roles still go through;
- the `-# Dernier bump par @juthing` credit line renders a mention **whatever the
  ping mode**, and stays silent unless the mode put them in `allowed_mentions`.
  The credit survives; the unwanted ping cannot happen.

### The three ping modes

| Mode | The last bumper is mentioned |
|---|---|
| `auto` | By every reminder. |
| `button` *(default)* | Only if they asked, via the button on the thank-you. |
| `never` | Not at all — only the chosen roles. |

The button is a `DynamicItem` carrying the bumper's id, so authorisation is
re-read from the click and a card left in a channel for a month is exactly as
safe as a fresh one. It **toggles**: somebody who armed it by reflex and changed
their mind clicks again. A reminder nobody asked for is the thing this module
exists to avoid, and that includes its own ping.

Clicking it after a newer bump has replaced that one says so, and changes
nothing — the write is scoped to `bumper_id` in the statement itself.

---

## The sweeper

A `modules/*.py` is instantiated *per guild*, so it can own neither a listener
nor a loop. `cogs/bump_reminder.py` owns both.

**The listener is its own**, not a block in `cogs/module_events.py`: that cog
drops bot-authored messages before any module sees them, which is exactly the
class of message this feature reads. Relaxing the shared guard would change what
four other modules receive. (`cogs/logs.py` already watches separately for the
same reason.)

**The loop runs every 30 seconds.** Restart recovery needs no special case: a
reminder missed while the bot was down is simply a row whose `due_at` is in the
past, which the first pass claims. A reminder more than five minutes late says so
on the card rather than pretending to be on time.

Before posting, the sweeper **re-reads the config** rather than trusting the row:
during the hours a reminder was pending it may have been deleted, paused or
re-pointed. Deleting a reminder (or moving it to another directory) drops the
orphaned row too, so a reminder deleted at 3pm cannot still fire at 4.

If the channel is gone or the permissions were withdrawn, the pass logs and moves
on. Nothing retries, nothing crashes.

---

## Cost

Railway bills RAM, so the shape was chosen for it:

- **Zero timers.** A pending reminder is a row, not a task. Nothing is held.
- **≤ 7 rows per guild**, bounded for life.
- **One indexed query per 30 seconds**, whatever the number of servers — the
  partial index `WHERE sent = FALSE` is that query, verbatim.
- **The listener's first test is a dict lookup** over seven ints. Everything
  else — the guild's module, the channel, the detection — sits behind it, so a
  message from an unrelated bot costs one hash.
- The registry is module-level and frozen: one copy, shared by every guild.

---

## Persistence

Every view is persistent (see [docs/PERSISTENT_VIEWS.md](PERSISTENT_VIEWS.md)),
registered in `utils/persistent_views.py` under group 10b:

| Class | Auth |
|---|---|
| `BumpReminderConfigView` | Manage Server, re-checked on every click |
| `ManageBumpReminderView` | Manage Server, re-checked on every click |
| `BumpReminderPersistence` | Marker view registering `BumpOptInButton` |

`BumpOptInButton` template:
`moddy:bump:optin:(?P<bot>[a-z]{1,16}):(?P<user>\d{1,20})`

`\d{1,20}` rather than `\d{17,20}`: the bare shell the persistence tests build
uses zeros and has to match its own template.

The modal is deliberately excluded — modals are never persistent.

---

## i18n

Everything posted into a channel speaks the **server** language
(`guild_locale(bot, guild)`). Everything one person reads privately — the config
panel, an ephemeral error, the "you'll be mentioned" confirmation — speaks
theirs (`i18n.get_user_locale`). See [docs/SERVER_LANGUAGE.md](SERVER_LANGUAGE.md).

Keys live under `modules.bump_reminder.*` in all five locale files.
`tests/test_bump_reminder.py::TestTranslations` asserts key parity in both
directions and that every key the source actually calls resolves.

Directory names are brands and are never translated.

---

## Adding a directory

1. Capture a **real** successful reply and add it to
   `tests/data/bump_payloads.json` under the new key. The tests refuse a
   directory nobody captured a reply for.
2. Add one `BumpBot` entry to `BUMP_BOTS`, positioned by audience size.
3. Add its emoji to `utils/emojis.py` and [docs/EMOJIS.md](EMOJIS.md) if it is new.
4. Capture a **refusal** too, into `tests/data/bump_refusals.json`. This is the
   step that is easy to skip and should not be: a guessed failure marker only
   tests the guess, and the danger is a *success* marker that also appears on
   the refusal — a button, a banner path — which turns every failed bump into a
   reminder. Directories whose success rests on an image URL or a button id are
   the exposed ones.
5. Run `pytest tests/test_bump_reminder.py`. The cross-directory test will tell
   you if a marker is too generic — that is its job, and the reason DiscordL's
   marker is `a été bump **par**` rather than the `a été bump` it started as,
   which also matched French.gg.

Nothing else knows the list. No i18n key, no config migration, no schema change.
