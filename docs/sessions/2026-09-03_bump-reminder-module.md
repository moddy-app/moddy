# 2026-09-03 — Bump Reminder module

New server module: detect a **successful** bump on seven server directories,
thank whoever ran it, and call the channel back when the command is available
again.

## What was done

A complete module, from the detection core to the `/config` panel:

- `bumpreminder/` — a new pure-Python package (no Discord, no DB) holding the
  registry of the seven directories and the detection funnel.
- `bump_reminders` table + `BumpReminderRepository`.
- `modules/bump_reminder.py` — `ModuleBase`, config schema, validation, routing.
- `cogs/bump_reminder.py` — a dedicated `on_message` listener and a 30s sweeper.
- `utils/bump_views.py` — both cards and the persistent opt-in button.
- `modules/configs/bump_reminder_config.py` — the panel and its Modal V2.
- `modules.bump_reminder.*` in all five locales (68 keys each).
- `tests/test_bump_reminder.py` (141 tests) + `tests/data/bump_payloads.json`.
- `docs/BUMP_REMINDER.md`, plus DATABASE / EMOJIS / CLAUDE.md updates.

Full suite: **1683 passed**.

## Decisions, and why

**Detect "the bump is through", never "the command was run".** A directory
answers `/bump` from the same bot with the same command whether it succeeded or
the server is on cooldown. A reminder armed off a failure fires an hour early,
pings the channel, and the directory then refuses — worse than no reminder. So
a failure marker *vetoes*, it never competes with a success marker, and a message
carrying both is treated as not understood.

**Markers come in three kinds** because the directories are not built alike:
text regexes (multilingual — these bots answer in the reader's language), media
URL substrings (several name their assets after the outcome, which beats any
sentence and survives every translation), and button custom_ids (French.gg only
offers its "remind me" button on a success).

**DISBOARD refuses privately** (Jules), so anything visible from it went through
— modelled as `refusal_is_ephemeral`, which makes it language-proof instead of
hostage to a phrase list. Two guard rails: the shortcut needs Discord to have
tagged the message with `/bump` (otherwise it could be anything DISBOARD posts),
and its own failure markers still veto.

**A stated next-bump time beats the configured interval**, but only inside a
5min–24h freshness window. DiscordTop and DSMonitoring publish theirs and are the
authority on their own cooldown; DiscordL's footer stamp is the *current* time
and would otherwise schedule a reminder three seconds out.

**Detection is channel-scoped, arming is server-scoped.** A bump is only read in
the channel the entry points at (Jules's call), but once read it arms every entry
for that directory in the guild — the cooldown belongs to the server, not the
channel. That is what makes a premium server's three DISBOARD entries useful
rather than three quarters dead, and it is why `bump_reminders` is keyed on
`(guild_id, bot_key)` rather than on the config entry.

**Quotas are per directory** (1 free / 3 premium), not a global total, so a free
server can still cover every listing it bumps on. Premium via
`utils.subscription.is_guild_premium` — there is no PREMIUM guild attribute.

**The reminder's mentions ride in a top-level `TextDisplay`**, above and outside
the container (Jules's placement). `content=` is illegal alongside a
`LayoutView`, and a self-deleting ghost ping cannot be scrolled back to. Here the
mention *is* the message.

**The "last bumped by" credit line keeps its mention even when the bumper ping is
off** (Jules caught this): what notifies is `allowed_mentions`, which lists
resolved objects only — so the credit survives and the unwanted ping cannot
happen. The thank-you card goes out with `AllowedMentions.none()`: buzzing
somebody one second after they typed the command is noise.

**Adding is a select, not a button.** The delay field must open pre-filled with
the directory's own cooldown, and a Discord modal is static — it cannot react to
a choice made inside itself. Picking the directory on the panel means the modal
opens fully filled in, for the same number of clicks.

**Zero timers.** A pending reminder is a row; restart recovery is
`due_at <= now()` with no special case, and `claim_due_bumps` flips rows to
`sent` inside the statement that returns them, so nothing double-posts. ≤7 rows
per guild, one indexed query per 30s regardless of scale.

**A dedicated listener, not a `module_events.py` block**: that cog drops
bot-authored messages before any module sees them, which is exactly what this
feature reads. Relaxing the shared guard would change what four other modules
receive.

## Corrections after the first push

Jules supplied real refusals for D-INVITES, DiscordL and DiscordTop, and the
information that DISBOARD *and* French.gg refuse ephemerally. Three real bugs
came out of it, all of the same shape: **a marker that is present on both the
success and the refusal**.

| Directory | The bad marker | Why it looked right |
|---|---|---|
| D-INVITES | failure `cooldown.png` / `error.png` | Invented. The file is `bump-error.png`. |
| DiscordL | success `Résultat du Bump` | It is the message *header*, printed on both. |
| DiscordTop | success `propulsé` | The refusal reads "vient **déjà** d'être propulsé". |

None of the three was *observable*: every refusal was still rejected, because
some other veto happened to fire first — `bump-error` missing a success marker,
"attendre", "déjay". That is the worst kind of correct: reword any of those
refusals and the detector silently starts arming reminders off failed bumps.

`TestCapturedRefusals` now holds two assertions that make this class of bug
impossible to reintroduce: a captured refusal must trip an **explicit** veto,
and **no success marker may match a directory's own refusal**. Both fail against
the original markers, naming them.

The generalisable lesson, now in the docs: the signal is what *differs* between
the two messages, never what the directory says about bumping in general.
"Boost envoyé" versus "Boost impossible" is a signal; "propulsé" is vocabulary.

Separately, `refusal_is_ephemeral` was doing two jobs and has been split.
Granting the "visible means success" shortcut is one fact; dropping the shared
cooldown blocklist is another, now `answers_in_any_language` and true only for
DISBOARD. Private refusals alone are an assumption about someone else's product,
and the blocklist is the net under it — French.gg, being a French listing, keeps
it for free.

## Superseded note (kept for the record)

Jules supplied a real D-INVITES refusal. It showed the failure markers were
wrong: invented as `cooldown.png` / `error.png`, the file is actually
`bump-error.png`, and the message reads "Tu pourras bump à nouveau `<t:…>`".

The refusal *was* already rejected — but only because no success marker happened
to fire, which is the weakest reason a detector can be right: the day a
directory adds a banner or a phrase to its refusal, the rejection silently
becomes an acceptance. `tests/data/bump_refusals.json` now holds captured
refusals, and `TestCapturedRefusals` asserts each one trips an **explicit** veto.
That test fails against the old guessed markers, which is how it earns its place.

## Bugs caught during the work

- `_by_channel` keyed on the channel alone would have silently dropped a second
  directory sharing one `#bump` channel — the most common setup. Now `(channel,
  bot)`, with duplicate `(bot, channel)` pairs refused at validation.
- The `<t:…>` regex was case-sensitive while the harvested text is lowercased, so
  DiscordTop's stated time was never read. Caught by the payload replay.
- DiscordL and French.gg shared the marker `a été bump`. Caught by the
  cross-directory test; DiscordL now requires `a été bump **par**`.
- The German module description was 111 characters — Discord caps a
  SelectOption description at 100 and `/config` would have truncated it mid-word.
  Now tested for all five locales.
- Two `except discord.Forbidden` blocks in the cog were dead: the notification
  service swallows it and returns a failed `DeliveryResult`.

## Known limits / follow-ups

- **D-INVITES is the fragile detector.** Its successful bump is a bare image with
  no text at all; the asset filename (`bump.png`) is the only signal. If they
  rename it, detection stops. Documented in `docs/BUMP_REMINDER.md`.
- **The directory ordering is an editorial judgement**, not a measured ranking.
  DISBOARD is verifiably largest; the rest are ordered by bot account age and
  reputation because no comparable published guild counts exist. Reordering is a
  one-line move in `BUMP_BOTS`.
- **Captured refusals exist for D-INVITES, DiscordL and DiscordTop.** DISBOARD
  and French.gg refuse ephemerally, so there is nothing to capture. Still
  synthetic, and worth replacing whenever a real one is seen: **Beemp** and
  **DSMonitoring**. Both rest on text markers covered by the shared blocklist,
  so the exposure is lower than it was for the three above — but "lower" is what
  I said about DiscordL before its refusal turned up a live marker collision.
- The module ships no slash command, so `locales/commands/` is untouched.
