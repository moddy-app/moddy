# 2026-09-13 — Transcripts: precise system events (reply/pin previews, member target)

## What was asked

In a ticket transcript's stored body, system events (`"s"` field —
`discord.MessageType` for anything non-default) were opaque: a `reply`
carried `"p"` (the referenced message id) but nothing self-contained to show
for it, and a dashboard would have had to cross-reference the rest of the
transcript — which may not even hold the target (older message, truncated
export). Asked to make these precise enough to render correctly, reply
included: say which message it is.

## What changed

`services/ticket_transcript_service.py`:

- `"pr"` — a best-effort preview of whatever `"p"` points to: `{"a": author_id,
  "c": up to 150 chars}`. Discord attaches the same `message_reference` to a
  **reply** and to a **"pinned a message" notice** (`s: "pin_add"`), so one
  function (`_reference_preview`) covers both. Falls back through
  `extract_component_text` when the original had no plain `content` (one of
  Moddy's own Components V2 cards). `{"deleted": true}` when the original was
  deleted before export; absent when Discord could not resolve the reference
  at all — `"p"` is then the only thing left to show, which is why it was
  never removed.
- `"tg"` — the user id a `recipient_add` / `recipient_remove` notice names
  (added to / removed from the channel). Every other system type has nothing
  beyond its author (`"a"`, already stored).

Both are additive, optional keys under the existing `TRANSCRIPT_VERSION = 1`
— they don't change the meaning of anything already documented, so no version
bump (per the doc's own rule: bump only when a key's meaning changes).

## Decisions worth recording

**Duck-typed, not `isinstance(discord.Message)`.** The resolved reference is
whatever discord.py handed back; checking for `.author` is what every other
function in this file already does (`_serialise_message` never type-checks
its `message` argument either) and it is what makes the stub-based test suite
exercise the real code path instead of a mocked one.

**Not every system type got a field.** `channel_name_change` already carries
the new name in plain `"c"` — nothing to add. `thread_created` and
`new_member` were left alone: no evidence either occurs inside a ticket
channel in practice, and inventing a field for a case that can't be observed
is exactly the premature-generality this codebase avoids.

## Files

`services/ticket_transcript_service.py`, `tests/test_ticket_transcripts.py`
(7 new tests), `docs/TICKETS_INTEGRATION.md`.

## Verification

`pytest tests/test_ticket_transcripts.py -q` — 73 passed (one pre-existing
failure in this sandbox is `asyncpg` not being installed, unrelated).
Confirmed against the branch's `HEAD` that the same failures exist without
this change too.

## Follow-ups

- None — this is additive and the backend/dashboard is free to ignore `"pr"`
  and `"tg"` until it wants to render them.
