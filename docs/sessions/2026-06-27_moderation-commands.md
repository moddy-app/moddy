# Session — 2026-06-27: Moderation Commands (/ban, /mute, /warn)

## What was done

Implemented three guild-only slash commands (`/ban`, `/mute`, `/warn`) fully integrated with the case system.

## Files modified

| File | Change |
|---|---|
| `cogs/moderation_commands.py` | **New** — full implementation of the three commands + `SanctionModal` |
| `cogs/case_sync.py` | Added `_is_moddy_initiated()` deduplication guard + `time` import |
| `services/case_service.py` | Added optional `group_id` parameter to `record_sanction()` |
| `utils/emojis.py` | Added `LEGAL`, `MIC_OFF`, `CHECK` emoji constants |
| `locales/en-US.json` | Added `commands.moderation.*` i18n keys |
| `locales/fr.json` | Added `commands.moderation.*` i18n keys (French) |

## Architecture decisions

### V2 Modal (5 top-level Labels)
1. `UserSelect` (min 1, max 10) — pre-filled with the optional `user` slash arg
2. `TextInput` (paragraph) — reason
3. `TextInput` (short, optional) — duration, only shown for ban/mute
4. `FileUpload` (optional, max 10 files) — evidence
5. `Checkbox` (default True) — notify user by DM

### Deduplication (case_sync conflict)
When Moddy applies a sanction via these commands, the Discord audit log fires `on_audit_log_entry_create`, which would cause `case_sync` to record a second case for the same action. To prevent this:
- The command records the case FIRST (before the Discord action), writing `(guild_id, user_id, action) → timestamp` into `bot._moddy_initiated_sanctions`.
- `case_sync._is_moddy_initiated()` checks this dict and skips recording if the entry is fresher than 10 seconds.

### Multi-user grouping
When multiple users are sanctioned in one modal submission, a shared `group_id` (UUID4) is generated and passed to `record_sanction()` → `create_case()`, linking all the cases together.

### DM messages
- Sent as a `BaseView` with a coloured `Container` (accent: orange for warn, red for ban/timeout)
- Evidence files are appended as a `ui.MediaGallery`
- Locale: `guild.preferred_locale` (community locale if enabled, otherwise Discord default = en-US)
- Emoji prefixes: `<:warning:...>` (warn), `<:mic_off:...>` (mute), `<:legal:...>` (ban)

### Confirmation panel (moderator)
- Single user: `### <:check:...> @user **banned**\n> Reason / Duration / Case ID`
- Multiple users: count header + per-user mention → case reference links
- Accent: `0x38B04B` (green)
- Respects the `incognito` option (ephemeral by default)

## Known limitations / follow-ups

- For `/mute`, the Discord timeout is capped at 28 days even when the user requests a longer duration; the case records the full requested duration.
- Evidence files in DMs are shown as `ui.MediaGallery` — Discord renders images/GIFs/videos but may not display raw files (PDFs, ZIPs, etc.).
- `/warn` creates no Discord action — it's a case record + optional DM only.
