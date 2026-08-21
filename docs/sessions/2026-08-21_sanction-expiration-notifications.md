# Sanction expiration notifications (unban / unmute / unwarn DM)

## What was done

A member whose temporary sanction expired was never told about it: the DB row
flipped to `expired`, a temporary ban was silently lifted, and that was it. The
member had no way to know they could come back — for a ban, not even a way to
rejoin the server.

Every expired **guild** sanction now sends the subject a DM:

| Expired sanction | Discord side | DM |
|---|---|---|
| `ban` | ban lifted | "your ban expired" + **Rejoin the server** invite button |
| `mute` | nothing (Discord clears the timeout) | "your timeout is over" |
| `warn` | nothing | "your warning no longer counts" |

The invite is attached to bans only (the other actions never removed the
member) and only when the unban actually succeeded — the DM never offers a way
back that does not exist. It is single-use and valid 7 days.

Global (Moddy-team) sanctions keep their own notice flow: `case_expiry` still
only drops their resolver cache, no DM from here.

## Files modified

- `services/expiration_notifier.py` **(new)** — `ExpirationNotifier`
  (`bot.expirations`): reverses the Discord action of each expired row, then
  notifies the subject. Absorbs the unban loop that lived inline in `bot.py`.
- `utils/expiration_views.py` **(new)** — `build_expiration_dm_view`, the green
  sibling of the sanction DM (same layout, case link, `sent by` footer) plus
  the invite link button.
- `utils/invites.py` **(new)** — `create_guild_invite`, the "first channel
  where Moddy may invite" lookup, previously private to the appeal service.
- `services/appeal_service.py` — `_make_invite` now delegates to it.
- `db/repositories/moderation.py` — `expire_due_sanctions` also returns the
  case `reference` and the sanction's `expires_at` (both are rendered in the DM).
- `bot.py` — instantiates `ExpirationNotifier`; `case_expiry` hands it the
  expired rows.
- `locales/{fr,en-US,es-ES,pt-BR,de}.json` — `commands.moderation.expiry_dm.*`.
- `tests/test_expiration_notifications.py` **(new)** — 16 offline tests.
- Docs: `docs/MODERATION_CASES.md` §7bis, `docs/PERSISTENT_VIEWS.md`, `CLAUDE.md`.

## Decisions

- **A service, not inline in `bot.py`.** The expiry loop was already growing a
  per-row Discord branch; moving it out makes the consequences of an expiry
  testable without a gateway (the new suite drives it with duck-typed stubs).
- **No persistent view registration.** The card's only control is a
  `ButtonStyle.link` button: it carries no `custom_id`, triggers no
  interaction, and stays valid across restarts by construction. This is the
  documented "link-only view" exclusion, not a skipped persistence.
- **Guild `preferred_locale` for the DM**, matching
  `cogs/moderation_commands._guild_locale` — the sanction DM and its
  expiration notice are then written in the same language.
- **Failures degrade to silence**: closed DMs, a member who left, a guild
  Moddy was removed from, or one without invite permission each mean "no
  notification"; the expiry itself always stands.

## Follow-ups

- Revocations (an appeal accepted, a moderator lifting a sanction by hand) do
  **not** send this DM — only expirations do. Appeal outcomes have their own
  notice; a manual revocation still tells the member nothing, which may be
  worth a follow-up.
- The invite is created per expired ban. On a mass expiry in one guild this is
  one API call per member; if that ever becomes a problem, cache one
  multi-use invite per guild per sweep.
