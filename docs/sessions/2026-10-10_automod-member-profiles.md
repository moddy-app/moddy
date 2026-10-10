# Session: Automod member profiles (Lot C)

**Date:** 2026-10-10
**Agent:** Claude Code

## Summary

New `profile` feature of the Automod AI module: avatar and banner (Google
SafeSearch) and names (nickname / display name / username, through the AI funnel)
are checked on member join and whenever a member changes their profile, then
sanctioned according to the server's `action_profil` for the profile infraction.
Full description: [AUTOMOD_AI.md §4.4](../AUTOMOD_AI.md).

## What was done

- `features.profile` (`avatar` / `banner` / `pseudo`), not run from `on_message`;
  `manage_nicknames` added to `REQUIRED_BOT_PERMISSIONS`.
- `services/automod_profile_service.py` (`bot.automod_profiles`): per-asset-key
  verdict cache (Redis `automod:avatar:<key>`), single-flight checks, throttled
  banner fetch, per-name verdict cache (`automod:pseudo:<sha1>`), idempotence
  markers, bounded background screening.
- Separate SafeSearch budget: gateway rule `google_vision/safe_search_profile`
  (`GOOGLE_VISION_SAFESEARCH_PROFILE_MONTHLY`), `call_type`
  `automod_profile_safesearch`, `VisionClient.safe_search(budget_model=...)`.
- `modules/automod_ai.py`: shared steps made message-optional,
  `apply_profile_decision`, hold lifecycle (apply / enforce / lift / sweep),
  profile alert + simulation cards, `_apply_discord_sanction` extracted.
- Events in `cogs/module_events.py`; hold sweep in `cogs/automod_profiles.py`.
- DB: `automod_profile_holds`, label kinds widened, `db/repositories/automod_profiles.py`.
- Labeling queue: `profil_*` lanes (hashes feed message images too); revocation
  also frees held members. Appeals tolerate cases without message fields.
- i18n in the 5 locales; tests in `tests/test_automod_profiles.py`.

## Decisions

- The profile SafeSearch budget is accounted on its own gateway key so profile
  checks and message images never starve each other; the API call is the same.
- A clean cached name verdict is only reused by a server whose severity is not
  stricter than the one it was judged with.
- The sweep runs every 6 h (not daily) with a 36 h renewal window: a bot restart
  or a missed pass cannot let a 28-day timeout lapse unnoticed.
- A "done" marker per (guild, member, asset or name) keeps an event storm from
  sanctioning the same thing twice; lifting a hold clears it.

## Known issues / follow-ups

- Banner-only changes emit no Discord event: checked on join and on any other
  profile event only.
- Names are judged only when the blocklist / embedding routing sends them to nano.
- A nickname reset by `rename_warn` is not restored by a team revocation.
- The `/config` panel does not expose the profile toggles yet (carried through).
