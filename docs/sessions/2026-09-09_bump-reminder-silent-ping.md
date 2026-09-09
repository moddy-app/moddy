# 2026-09-09 — Bump reminder: the ping that renders but does not notify

## The report

The reminder card mentions the right role, the tag renders in blue, and nobody
receives a notification.

## What was ruled out

The send path is correct, end to end:

- `cogs/bump_reminder.py::_post` builds `AllowedMentions(everyone=False,
  roles=[…], users=[…])` from resolved role objects;
- `notifications/service.py::send_channel` → `_deliver` passes it through to
  `channel.send()` untouched;
- no `silent=True` anywhere, and no global `allowed_mentions` on the bot;
- the internal `moddy/` framework does not touch mentions;
- mentions inside a Components V2 `TextDisplay` do notify, per Discord's
  component reference — they simply obey `allowed_mentions`.

## The actual gap

`allowed_mentions` only ever *asks*. Discord refuses a role ping when the role is
not mentionable and the sender lacks *Mention All Roles* in that channel — and it
refuses **silently**: no error, no rejected request, and the tag still renders.
The feature had no way to tell a delivered ping from a dropped one, which is why
the failure was invisible.

## What changed

- `utils/bump_views.py` — new `unnotifiable_roles()`: the configured roles
  Discord will render but refuse to notify, given the channel permission.
- `cogs/bump_reminder.py::_post` — warns before sending when a role cannot
  notify, and after sending compares the delivered message's own
  `raw_role_mentions` against what was asked, logging anything Discord dropped.
- `tests/test_bump_reminder.py` — covers the helper (mentionable vs not, and the
  permission overriding the flag). 170 pass.
- `docs/BUMP_REMINDER.md` — new section on the render/notify distinction and how
  to read the two log lines.

## Decisions

Diagnosis, not a workaround. Temporarily flipping the role to mentionable around
each send was rejected: it writes to the audit log, fires the server-logs module,
and races with anyone editing the role. The server making the role mentionable
(or granting the permission) is the correct fix, and the logs now say which.

## Follow-up

Surface the same warning in the `/config` panel, next to the role select, so the
server sees it while configuring rather than only in the logs.

---

## Second fix — `B2FC2E55`: the opt-in button crashed

`TypeError: build_thanks_card() missing 1 required keyword-only argument:
'guild_name'` on every click of the "ping me next time" button.

A regression from b66a207 ("Show the server's name in bump reminder cards"),
which made `guild_name` required but only updated the cog's two call sites. The
third — `BumpOptInButton.callback` in `utils/bump_views.py`, which rebuilds the
thank-you card from scratch on every click — was missed, so the button raised
and the person got an error report instead of their opt-in.

- `utils/bump_views.py:146` now passes `guild_name`.
- `tests/test_bump_reminder.py` runs the callback end to end against stubs and
  asserts `edit_message` was reached with the server's name in the card. It has
  to assert on the outcome rather than on a raised exception, because
  `@_guarded` swallows the error into the central handler — which is exactly why
  the regression shipped. Verified failing without the fix, passing with it.
