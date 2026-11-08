# 2026-11-08 — AltGuard as its own bot (mini-bot "AltGuard")

## What was done
- New mini-bot **AltGuard** (`altguard_bot`, token `MINIBOT_ALTGUARD_TOKEN`,
  accent `#5865F2`): Moddy's `altguard` module unchanged, under its own name.
  `minibots/bots/altguard.py` holds what Moddy wires elsewhere:
  - `AltGuardBotCog(AltGuard, name="AltGuard")`: gate on join, then report;
    departures through `on_raw_member_remove` (no member cache);
    its own `altguard:verdict` subscription (after ready, backoff, one task per
    verdict); `client.altguard` HTTP client;
  - `build_welcome`: the three things the gate needs, or "Moddy already runs it".
- `minibots/spec.py`: `members` allowed for its events (the cache stays off);
  `presences` still refused. New `MiniBotSpec.extra_permissions` (optional
  extras in the invite link; `administrator` refused), used by AltGuard for
  *Manage Server* (inviter attribution).
- `/config`: the AltGuard screen hides Back and the trail in a one-module app
  and carries the Language button (same as Bump Reminder).
- **Auto Role across processes**:
  - `modules/auto_role.py::_foreign_gate_holds_back`: Moddy holds its roles
    back while a gate another app runs is closed (stored config +
    `altguard_members`), fails closed;
  - `modules/altguard.py::_run_auto_role` → `_hand_off_to_moddy`: publishes
    `altguard_gate_opened` on `minibots:moddy` under a mini-bot;
  - `bot.py::_handle_altguard_gate_opened`: re-checks the database, then
    `apply_roles`.
  - `minibots/status.py`: `EVT_ALTGUARD_GATE_OPENED`, `moddy_event`,
    `parse_moddy_event`.
- `cogs/altguard.py`: `_classify_departure(guild, user_id)` split out of
  `_classify_removal(member)` (usable without a `Member`).

## Files modified
- `minibots/bots/altguard.py` (new), `minibots/spec.py`, `minibots/builtin.py`,
  `minibots/status.py`
- `cogs/altguard.py`, `modules/altguard.py`, `modules/auto_role.py`,
  `modules/configs/altguard_config.py`, `bot.py`
- `locales/{fr,en-US,es-ES,pt-BR,de}.json` (`minibots.bots.altguard.welcome.*`)
- `tests/test_altguard_minibot.py` (new), `tests/test_minibots.py`,
  `tests/test_auto_role_tags.py` (fake manager gained `get_module_config`/`owns`)
- `docs/features/MINIBOTS.md` (§4, §5, §8, new §11), `docs/features/ALTGUARD.md`,
  `docs/infra/RAILWAY.md`, `CLAUDE.md`

## Decisions
- **`members` intent allowed.** AltGuard cannot work without join events, and the
  member list endpoint (hourly resync) needs it. The RAM worry behind the old
  ban was the cache, which `MemberCacheFlags.none()` keeps off for every
  mini-bot. Privileged: enable it on the Developer Portal.
- **Verdicts heard by both processes**, routed by ownership: the module check
  precedes the idempotency insert, so the process without the gate never marks
  a verdict as seen. No new routing key needed from the service.
- **Auto Role hand-off via Redis, re-checked in DB**: the message is unsigned
  like the rest of `minibots:*`, so it only says "look again".
- Own loops: the cog keeps its own `tasks.loop` (hourly resync)
  and subscription, like the Bump Reminder cog keeps its sweeper.

## Known issues / follow-ups
- `/mod altguard verify|unverify|refusal` (Moddy staff) only reach gates Moddy
  runs; on a gate the AltGuard bot runs they answer "not configured".
- A pass while Moddy is down leaves the auto roles unapplied (Pub/Sub, no
  replay); `/altguard verify` re-sends the hand-off.
- Before 100 servers: justify the Server Members intent to Discord.
- `tests/test_translate_button.py::TestDefaultEnabled::test_runs_where_nothing_is_stored_and_can_be_paused`
  still fails, as before this change (unrelated).
