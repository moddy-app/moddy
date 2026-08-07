# 2026-08-07 — Internal API: `POST /automod/rules_check`

## What was done

Exposed the automod **indications safety check** over the bot's internal API so
the backend/dashboard can validate `indications` before writing them, instead of
re-implementing the anti prompt-injection heuristic on its side.

The backend (website-backend PR #47) fails closed: without this route, every
`PUT /guilds/{id}/modules/automod_ai` touching `indications` returned `503`.

### The route

```
POST /automod/rules_check
Authorization: Bearer {INTERNAL_API_SECRET}

{ "guild_id": "123456789012345678", "indications": "…", "locale": "fr" }
```

- `200 {"ok": true}` — text accepted.
- `200 {"ok": false, "reason": "…", "code": "unsafe|too_long|unavailable"}` —
  rejected; `reason` is a full sentence, displayable as-is on the dashboard.
- `4xx/5xx {"ok": false, "error": "<code>", "reason": "…"}` — the check could
  not run: `invalid_json`, `invalid_body`, `missing_guild_id`,
  `invalid_guild_id`, `missing_indications`, `invalid_indications` (400),
  `unauthorized` (401), `unknown_guild` (404), `bot_not_ready` (503).

The backend must reject the write on anything other than `{"ok": true}`.

## Files modified

| File | Change |
|---|---|
| `internal_api/routes/automod.py` | **New.** `APIRouter` with `POST /automod/rules_check`. |
| `internal_api/server.py` | Public `check_auth()` / `get_bot()` helpers (`_check_auth` kept as an alias), router wiring, docstring. |
| `tests/internal_api/test_rules_check_route.py` | **New.** 19 tests via FastAPI `TestClient`, bot + gateway stubbed. |
| `requirements-dev.txt` | Added `httpx` (needed by `TestClient`). |
| `docs/AUTOMOD_AI_CONFIG.md` | § 6 now documents the endpoint contract (backend-facing). |
| `docs/AUTOMOD_AI.md` | Indications safety check section points at the route. |
| `CLAUDE.md` | Structure tree: `internal_api/routes/automod.py`, `tests/internal_api/`. |

## Decisions

- **No new heuristic.** The route is a thin HTTP wrapper over
  `automod/rules_check.py::validate_rules` (call type `automod_rules_check`) —
  the exact code path the `/config` panel uses, including the nonce fencing and
  the fail-closed behaviour on gateway errors.
- **Reason codes expanded into sentences.** `validate_rules` returns bare codes
  (`too_long`, `unavailable`) that the panel maps to i18n strings. Those strings
  carry Discord emoji/markdown, so the route keeps its own plain-text FR/EN
  table (`_REASONS`) — the dashboard gets something it can render directly. An
  optional `locale` field picks the language (French by default, since the AI's
  own `raison` is French).
- **Manual body parsing** instead of a pydantic model, so a malformed
  `guild_id` yields an explicit `400 invalid_guild_id` rather than FastAPI's
  generic `422` — the ticket asks for explicit errors, not a silent crash.
  `guild_id` is accepted both as a JSON string and as a number.
- **`unavailable` returns 200, not 503**, per the agreed contract: the caller
  treats any non-`ok` answer as a refusal, and the outcome (nothing saved) is
  the same. Infrastructure failures the route itself can detect (bot down,
  unknown guild) do use proper 4xx/5xx codes with an `error` field.
- **Empty `indications` returns `ok: true` without an AI call**, mirroring the
  panel: clearing the field cannot be an injection.
- **Auth reuses the existing `INTERNAL_API_SECRET` bearer check** — same
  contract as `/status`. When the env var is unset (dev), access is open, which
  is the pre-existing behaviour of the internal API.

## Tests

```
python3 -m pytest tests/automod tests/internal_api -q   # 300 passed
```

Covered: clean text, known injection payload, FR/EN reason localisation,
over-cap text, gateway outage (fails closed), empty text (no AI call), every
`400/401/404/503` path, and that `/health` + `/status` still work.

`tests/test_persistent_views.py`, `tests/test_embeds.py` and
`tests/test_command_localizations.py` cannot be collected in a bare container
(they need `discord.py` installed) — unrelated and pre-existing.

## Follow-ups

- Backend side: point `check_indications` at this route and verify end-to-end
  through `PUT /guilds/{id}/modules/automod_ai` (acceptance criterion 4 — needs
  both services running, not doable from this repo alone).
- There is still no `docs/INTERNAL_API.md`; the internal API is documented
  per-feature (here, `AUTOMOD_AI_CONFIG.md § 6`). Worth consolidating if a
  third route lands.
