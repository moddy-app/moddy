# Session: AltGuard anti multi-account verification module

**Date:** 2026-08-18
**Agent:** Claude Code

## Summary

Added the **AltGuard** module: a verification gate that holds every joining
human behind an unverified role until the external AltGuard service
(`verify.moddy.app`) decides whether their account looks like a second account.
The member clicks one button in the verification channel, accepts an explicit
consent modal, receives a personal single-use link, and the service publishes
its verdict on `altguard:verdict`.

The bot never receives a personal datum: Discord ids, statuses and category
labels only.

## Changes Made

### Feature (first commit)

- `modules/altguard.py` — guild config, gate roles, panel lifecycle (deleted and
  re-posted on every save so a removed panel repairs itself), verdict
  application, manual staff overrides, optional log channel.
- `modules/configs/altguard_config.py` — persistent `/config` panel: channel,
  both roles, optional log channel, panel language. The wording itself is not
  configurable.
- `utils/altguard_views.py` — persistent verification panel, consent Modal V2
  (mandatory `CheckboxGroup`, links to the data notice / terms / privacy),
  ephemeral link card, log cards.
- `services/altguard_client.py` — `POST /altguard/token`, `POST
  /altguard/membership/resync`, `altguard:membership` publisher, `parse_verdict`.
- `db/base.py`, `db/repositories/altguard.py` — `altguard_verifications`
  (audit + idempotency key + refusal lookup) and `altguard_members` (gate state).
- `cogs/altguard.py` — verdict dispatch, membership events (kick / leave / ban
  told apart via the audit log), hourly full resync, `/altguard verify|unverify`.
- `staff/commands/mod/altguard/` — `/mod altguard verify|unverify|refusal`
  behind the new `altguard_manage` permission node.
- `modules/auto_role.py` — holds its roles back for an unverified human; the
  roles are applied by AltGuard the moment the gate opens.
- `bot.py`, `cogs/module_events.py`, `config.py`, `utils/persistent_views.py`,
  `utils/emojis.py`, `utils/staff_role_permissions.py` — wiring.

### This session (second commit)

- `locales/{fr,en-US,es-ES,pt-BR,de}.json` — the 93 `modules.altguard.*` and
  `staff.mod.altguard.*` keys the code was already calling, in all five
  languages.
- `locales/commands/*.json` (32 files) — `/altguard`, `/altguard verify`,
  `/altguard unverify` names, descriptions and the `member` option.
- `utils/altguard_views.py` — `ui.Separator(divider=…)` → `visible=…`; the
  keyword does not exist in the pinned discord.py 2.7.1 and every panel build
  raised `TypeError`.
- `tests/test_altguard.py` — 30 tests (see below).
- `docs/ALTGUARD.md` — full module documentation.
- `CLAUDE.md`, `docs/RAILWAY.md`, `docs/REDIS_COMMUNICATION.md`,
  `docs/STAFF_COMMANDS_FRAMEWORK.md` — structure, env vars, channels, staff
  commands.

### Unrelated i18n fixes (reported at runtime)

- `global_sanctions.notice.level` / `.cases` were missing in all five locales
  (used by the staff sanction-group panel).
- `languages.*` only covered 34 codes: added `es-419`, `hi`, `hr`, `id`, `lt`,
  `th`, `vi`, `zh-TW`, `is` and the literal `Icelandic` label discord.py hands
  back for that locale.

## Decisions & Rationale

- **The panel wording ships with the module.** Admins choose the channel, the
  roles and the language — not the text. Every server must state the same thing
  about the same data processing; a server-edited consent notice would be a
  consent record we cannot vouch for.
- **Consent is a separate, explicit act.** Two mandatory checkboxes in a Modal
  V2 (`CheckboxGroup(min_values=2, required=True)` — a bare `Checkbox` cannot be
  made mandatory), and the submit is re-checked server-side rather than trusting
  the client.
- **Score and signals never reach the member.** They live in the guild log
  channel and `/mod altguard refusal`. The service withholds them from the
  browser precisely so a bypass attempt gets no oracle; reproducing them
  bot-side would break the same guarantee. A test asserts the link card carries
  neither.
- **`enforced` is read as false when in doubt.** A malformed or partial verdict
  logs and applies nothing, instead of sanctioning on a half-formed message.
- **Idempotency is the `verification_id` primary key.** A replayed Pub/Sub
  message inserts nothing and stops there — no second role change, no second log.
- **Auto Role fails closed.** If the AltGuard lookup raises, the auto roles
  wait; opening the gate on an error would defeat the whole module.
- **A resync is skipped when the member cache is incomplete.** A partial list
  would mark real members as `left`.

## Known Issues / Follow-ups

- [ ] `blocked` currently keeps the member behind the gate and logs; it never
      kicks. The contract states `action_on_block` lives on the AltGuard side
      and is already reflected in `verdict`/`enforced`, so a kick would need the
      service to expose that intent in the payload.
- [ ] `CONSENT_VERSION` is `"2026-08"` — bump it whenever the consent wording in
      `locales/*.json → modules.altguard.consent` changes materially.
- [ ] The unverified role's channel permissions are the server's own job; the
      module only reminds the admin in the config panel. Auto-locking every
      channel on save was deliberately not done — it would rewrite permissions
      the server never asked us to touch.
- [ ] End-to-end flow untested against the real service (no staging token in
      this environment): the HTTP layer is covered only by its unit contract.
