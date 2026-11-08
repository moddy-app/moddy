# 2026-11-08 — AltGuard: `bot` field in `POST /altguard/token`

## What was done
AltGuard (moddy-app/AltGuard PR #25 / #26) now accepts an optional `bot` field
on `POST /altguard/token`: the verification page shows the logo of the app that
asked for the verification. The bot now always sends it.

- `services/altguard_client.py`: constants `BOT_MODDY = "moddy"` and
  `BOT_MINIBOT = "minibot"`; `AltGuardClient(bot, *, bot_name=BOT_MODDY)`;
  `create_verification_token` adds `"bot": self.bot_name` to the payload.
- `bot.py`: Moddy builds its client with `bot_name=BOT_MODDY`.
- `minibots/bots/altguard.py`: the AltGuard mini-bot builds its client with
  `bot_name=BOT_MINIBOT`.

## Decisions
- The value is a constant fixed where each process builds its client, never
  inferred at runtime (requirement from the AltGuard side). No env var: the
  two values never change per deployment.
- Always sent (not optional on our side): the service ignores unknown/malformed
  values without a 400, so there is no failure mode to handle.
- No change to the response handling or the `altguard:verdict` consumer: the
  service leaves both unchanged.

## Files modified
- `services/altguard_client.py`, `bot.py`, `minibots/bots/altguard.py`
- `tests/test_altguard_invites.py`, `tests/test_altguard_minibot.py`
- `docs/features/ALTGUARD.md`, `docs/integration/ALTGUARD_INTEGRATION.md`

## Follow-ups
- Visual check without the bot: https://verify.moddy.app/demo?bot=minibot
