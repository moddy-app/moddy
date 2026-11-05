# 2026-11-05 — Chaos resilience + hostile permissions

## Goal
Keep Moddy running when any part of the code or infrastructure fails, and make
every feature work (or say exactly what to grant, and where) with zero or odd
permissions — the way Top.gg reviewers test (permissions=0, single-permission
roles, restrictive categories). Context: three Top.gg declines for
"requires ADMINISTRATOR", the last one on ticket creation (PR #443).

## Method
1. Discord's official docs (docs.discord.com, Markdown pages) were read and the
   rules Moddy relies on were written down with verbatim quotes in
   `docs/guides/DISCORD_PERMISSIONS.md` (assumptions are marked as such).
2. New helpers in `utils/bot_permissions.py` (`channel_missing`,
   `can_act_in`, `is_obfuscated`, `channel_keys`).
3. Five parallel audit+fix passes on disjoint file sets (core, /config,
   modules 1, modules 2, commands), each reviewed before commit.

## Notable findings (bugs, not only hardening)
- Interserver never relayed messages with attachments (UnboundLocalError).
- `/ban` `/kick` `/mute` showed success and DMed the target when Discord
  refused the action.
- `/config` checked channel-scoped permissions (send, webhooks, …) server-wide:
  stricter than Discord (same pattern as the ticket bug).
- Ticket sync (close / claim / escalate / participants) was refused by Moddy in
  the Top.gg TEST category although Discord would accept; a refused move left
  the DB saying "moved"; a refused delete orphaned the channel.
- Logs required `manage_webhooks` server-wide; "no webhook" was cached until
  restart; every event waited for the audit log without `view_audit_log`.
- Auto Role / Auto Restore Roles: one unassignable role failed the whole call.
- Default-enabled replies ignored `read_message_history` (replies) and threads.
- Core: Postgres down at boot blocked startup; Redis listeners could die
  silently; `on_ready` re-ran everything after each re-IDENTIFY; a DB blip
  during command sync could publish a tree without module/staff commands;
  `case_expiry` loop could die for good; an API server crash killed the bot.
- Discord obfuscates channels the bot cannot see from **2026-11-16**: names
  become `___hidden___`, overwrites become fake. Handled in /config, logs,
  tickets, AltGuard, interserver.

## Commits (branch claude/amazing-mendel-3guqjn)
`0b496e9` docs + helpers · `1681515` commands · `8e25b37` /config ·
`af748b3` tickets/logs/AltGuard/automod · `ab3c984` modules ·
`2c6d6d5` background tasks · `1cfa403` core · docs commit.

## Tests
New `tests/test_hardening_{core,config,modules1,modules2,commands}.py`
(~207 tests, fake guilds implementing Discord's permission algorithm, incl. the
Top.gg review categories). Full suite: 4208 passed, 1 skipped, 1 known
unrelated failure (`test_translate_button.py::TestDefaultEnabled::
test_runs_where_nothing_is_stored_and_can_be_paused`). Nothing was verified
live against Discord, Postgres or Redis.

## Open decisions / follow-ups
- Moderation commands record the case before the Discord action: a refused
  ban leaves an "active" case. Reorder or mark the case failed?
- Automod `/config` requires ban, timeout, nicknames and manage messages even
  for servers that only want delete + warn. Make sanction permissions optional
  (shown as gaps) instead of blocking?
- Starboard message map is in memory: a restart can create a duplicate entry
  (needs a table).
- `case_expiry` marks rows expired before the unban; `global_sanction_service`
  loses a consequence when `_execute` fails after the atomic claim.
- Interserver relay is awaited inline in `on_message` (slow relay delays
  automod for that message).
- Some `validate_config` messages (auto_role, altguard, auto_restore_roles)
  are still hardcoded strings; `role_assignment_error()` is ready to replace
  them. `auto_restore_roles` logs still use `discord.Embed`.
- `/pwned` left unchanged on purpose (owner's decision).
- Live checks still to do: a real ticket panel click and the ticket edit paths
  under the Top.gg TEST category; logs in the "Logging" category.
