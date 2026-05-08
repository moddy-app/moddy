# Session — 2026-05-08 — Token Detector

## What was done

Implemented a new `cogs/token_detector.py` cog that automatically detects Discord tokens posted in guild messages, validates them against the Discord API, and sends an action-rich DM to the affected user or bot owner.

## Files modified

| File | Change |
|---|---|
| `cogs/token_detector.py` | **New** — full implementation |
| `requirements.txt` | Added `cryptography>=42.0.0` |
| `config.py` | Added `TOKEN_DETECTOR_KEY` env var |

## Architecture decisions

### In-memory encrypted cache (no DB)
Tokens are never written to the database. Each alert payload (token, message metadata, masked content) is encrypted with **Fernet** (AES-128-CBC + HMAC) and stored in a process-level dict keyed by a 10-char random hex string. The cache key is embedded in button custom_ids. TTL = 24 h; entries are consumed (deleted) after destructive actions (invalidate).

The `TOKEN_DETECTOR_KEY` env var must be a valid Fernet key. If absent, an ephemeral key is generated at startup (tokens lost on restart — acceptable for security reasons).

### DynamicItem for persistence
All buttons use `discord.ui.DynamicItem` subclasses with named-group regex templates. These are registered via `bot.add_dynamic_items()` in `cog_load`. After a restart the cache is empty; clicking any button shows a graceful "action unavailable" message with a link to Discord account settings.

### Token validation flow
1. `_TOKEN_RE` regex detects candidates in every guild message (bots and DMs ignored).
2. Candidate is tried first as a **user token** (`GET /users/@me` without `Bot` prefix).
3. If that fails, tried as a **bot token** (`GET /users/@me` with `Bot` prefix).
4. Invalid candidates (no 200 response) are silently discarded — no false-positive alerts.

### DM fallback for privacy settings
If the bot cannot DM the victim (`discord.Forbidden`), and we have a user token, the code uses the user's own token to `POST /users/@me/channels` to create a DM channel from the user's side, then sends via the bot into that channel.

### Bot token alerts
- Fetches application info via `GET /oauth2/applications/@me` with the bot token.
- If the app has a **team**: alerts the team owner + all team members.
- If solo app: alerts the `owner` field.
- Buttons: Message Info, Delete Message, Dev Portal link (to regenerate the token).

### User token alert buttons
1. **Message Info** — ephemeral, shows server/channel/author/timestamp + masked content
2. **Invalidate Token** — prompts confirmation, then calls `POST /auth/logout`
3. **Delete Message** — tries bot permissions first, falls back to user-token DELETE
4. **Change Password** — calls `POST /auth/forgot` with email from `/users/@me`

### Custom ID lengths
All custom IDs verified to be ≤ 100 chars (Discord limit). Longest is 72 chars.

## Known limitations / follow-ups

- `POST /auth/forgot` may return a non-200 status for phone-number accounts (Discord responds with error code 70007). The code shows a graceful error asking the user to reset manually.
- The `/auth/logout` body (`provider: null`) is based on Discord's internal API — may be subject to change.
- If `email` is not in `/users/@me` (e.g., the token lacks email scope), the "Change Password" button shows a fallback message.
- No rate-limiting guard between messages from the same author — a user spamming tokens would trigger multiple alert cycles. Acceptable for now given validation is the primary cost.
