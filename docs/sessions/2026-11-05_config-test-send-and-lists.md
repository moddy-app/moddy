# 2026-11-05 — "Send a test" button + cleaner /config lists

## What was done

1. **Send a test** on every module whose job is to post a message: welcome
   channel, welcome DM, bump reminder, social notifications. The button is on
   the confirmation shown after an entry is created and on the entry's manage
   screen. It sends the configured message once, right now, with the clicker
   standing in for the member / bumper / post author.
2. **Lists in /config**: every entry of a multi-entry list (bump reminders,
   welcome messages, welcome DMs, followed accounts, ticket panels, ticket
   categories) is rendered as a block quote (`> ` on every line) so you can
   tell where one stops. Removed the second separator of the bump reminder
   panel and the filler lines: "N reminder(s) per directory.", "N reminder(s)",
   "N account(s) followed", the list titles repeating the screen title, and the
   "Up to N welcome messages/DMs" line (the `count/max` line stays).

## Files

| File | Change |
|---|---|
| `modules/configs/_test_send.py` | **New** — `TestSendButton` (DynamicItem `moddy:cfg:test:<module>:<ref>`), `TestResult`, `add_test_marker`, `confirmation_view`, `test_button_row`, `TestSendPersistence`, sender registry, cooldown |
| `modules/configs/_common.py` | `quote()` (list entries) + `ack_thinking()` (new ephemeral answer) |
| `modules/{welcome_channel,welcome_dm,bump_reminder,social_notifications}.py` | `send_test()` next to each module's real rendering (+ `sample_event()` for social) |
| `modules/configs/{welcome_channel,welcome_dm,bump_reminder,social_notifications}_config.py` | Test button on confirmation + manage screen; quoted entries; filler lines removed |
| `modules/configs/tickets_config.py`, `tickets_panel_config.py` | Quoted panel / category entries |
| `utils/persistent_views.py`, `minibots/builtin.py` | Register `TestSendPersistence` (Moddy + mini-bots) |
| `utils/emojis.py` | `SEND` constant (existing `send` icon, already has a `_dark` twin) |
| `locales/*.json` | `modules.config.test.*`, `modules.social_notifications.test.sample_title`; removed the unused filler keys |
| `tests/test_config_test_send.py` | **New** |
| Docs | DESIGN.md (lists + test button), MODULE_SYSTEM.md (how to wire a new module), the 4 feature docs, CLAUDE.md |

## Decisions

- **One DynamicItem for every module**, carrying the module id and the entry
  id: survives restarts and works on an ephemeral confirmation no panel tracks.
  The entry is re-read on every click, so a deleted entry answers "not found".
- **Tests never notify roles or @everyone.** Role tags are rendered so the
  layout is exact, but `allowed_mentions` drops them; only the clicker can be
  pinged. Pinging a whole role for a test would be worse than no test.
- **A `-#` "test message sent by @user from /config" line** under the card,
  outside the module's own container, so members of the channel aren't
  confused and the message itself is unchanged.
- **10 s cooldown per entry** (in memory) so the button can't flood a channel
  or DMs.
- Welcome DM and bump tests go through `bot.notifications` like the real
  sends; welcome channel and social keep `channel.send`, as their real sends do.
- Social notifications: the button carries the `social_subscriptions.id` row
  id (target ids can be feed URLs, too long for a custom_id). The sample post
  links to the account's profile.

## Known issues / follow-ups

- `tests/test_translate_button.py::TestDefaultEnabled::test_runs_where_nothing_is_stored_and_can_be_paused`
  fails on the base branch too, unrelated to this change.
- Starboard / inter-server are not covered: their message is a copy of a real
  member message, so there is nothing meaningful to send as a test.
