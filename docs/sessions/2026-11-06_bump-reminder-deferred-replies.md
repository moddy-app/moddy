# 2026-11-06 — Bump Reminder: DiscordL bumps were never detected

## What was done
- Root cause: DiscordL **defers** its `/bump` reply. The message is created as an
  empty "is thinking…" placeholder (`LOADING` flag, no components) and the result
  is edited into it ~2 s later (the footer `<t:…>` stamp postdates the message's
  creation in every capture). The cog only listened to `on_message`, which saw the
  empty placeholder, so no DiscordL bump was ever armed. The pure detector itself
  recognised the final message fine (banner URL `/v2/bump/`).
- `cogs/bump_reminder.py`: new `on_raw_message_edit` listener feeding the same
  handler (`_handle_directory_message`); loading placeholders skipped on both
  paths; edits only read for messages < 15 min old; a message is claimed once
  (`_claim_message`, bounded `OrderedDict`) so the banner-unfurl edit does not post
  a second thank-you.
- `minibots/bots/bump_reminder.py`: no own `on_message` any more; it overrides
  `_handle_directory_message` (zero-setup) and inherits both listeners.
- `bumpreminder/registry.py`: text markers for DiscordL's new reply
  ("Ton Bump a été effectué", "vient d'être propulsé par"); before, only the banner
  URL matched it.

## Files modified
- `cogs/bump_reminder.py`, `minibots/bots/bump_reminder.py`, `bumpreminder/registry.py`
- `tests/test_bump_reminder.py` (+ `TestLaterCaptures`, `TestDeferredReplies`)
- `tests/data/bump_payloads_extra.json` (new: later captures, several per directory allowed)
- `docs/features/BUMP_REMINDER.md`

## Decisions
- Raw edit event rather than `on_message_edit`: the mini-bot keeps no message cache.
- Dedupe in memory only: the window it guards is seconds long; `record_bump`
  already upserts the row, so the worst case after a restart mid-bump is one extra card.

## Follow-ups
- Only the French wording of DiscordL's new reply has been seen; other languages
  rely on the banner URL marker.
- Other directories may defer too; the edit path now covers them without change.
- `tests/test_translate_button.py::TestDefaultEnabled::test_runs_where_nothing_is_stored_and_can_be_paused`
  fails on the branch before this change as well (unrelated).
