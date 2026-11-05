# 2026-11-05 — Hardening: tickets edit paths, server logs, AltGuard, automod actions

## What was done
- Tickets: `sync_permissions` (close / claim / escalate / participants / reopen) now
  checks the permissions IN the ticket channel (the bot's own overwrite), not the
  category; clamp is guild|category with a strict retry. `move_ticket` checks
  manage_channels on the channel, rolls the DB category back when Discord refuses,
  and has a dedicated `cannot_move_channel` message. Closing never strands a ticket
  (transcript / DM / log crashes contained; the DB row is kept if the channel
  could not be deleted).
- Server logs: no module-wide `manage_webhooks`; negative webhook cache has a TTL; deleted
  webhook is recreated and the batch retried; 6000-char batches split; fallback checks
  send/embed permissions and degrades; hidden channels render as `<#id>`; no audit wait
  without View Audit Log; channel updates on obfuscated channels are not diffed.
- AltGuard: gate roles changed one call at a time, hierarchy/managed/Manage Roles reported
  precisely, a passed member is never kept behind the unverified role by a verified role
  the bot cannot give; lock overwrites clamped to what the bot holds; resync loop guarded
  per guild; service timeouts are `AltGuardError`.
- Automod: 403 60003 (2FA) is a permission gap, timeout on an Administrator is not attempted.

## Files
See `tests/test_hardening_modules2.py` for the scenarios.
