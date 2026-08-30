# 2026-08-30 — Stripe invoice DMs (`notify_invoice`)

## What was done

Wired the last missing half of the backend's invoice pipeline: the Discord DM.
The backend already activates premium, writes the Redis cache, mails the
customer (Resend) and deduplicates on the Stripe `invoice_id`; the bot now
listens for `notify_invoice` on `moddy:subscription:updates` and sends one DM
per invoice — **including invoices at 0**, because a free trial produces a real
Stripe invoice.

- `services/invoice_notifier.py` — new `InvoiceNotifier` (`bot.invoices`):
  payload parsing, `variant` handling, amount/date formatting, Redis
  de-duplication, and the uniform notification payload.
- `bot.py` — `notify_invoice` branch in `_handle_subscription_event`
  (invalidate the cache, then hand the event over) + the service instance.
- `notifications/models.py` — new `subscription` service.
- `notifications/render.py` — `subscription` added to `OFFICIAL_SERVICES`.
- `locales/*.json` (5) — `commands.subscription.invoice.*` and the service name.
- `tests/test_invoice_notifications.py` — 31 tests.
- Docs: `SUBSCRIPTION_SCHEMA.md` (§3 `notify_invoice`, §4 row, new §6 on
  `stripe_action`), `REDIS_COMMUNICATION.md`, `CLAUDE.md`.

## Decisions

**The wording is chosen on `variant`, never on `amount_paid`.** That is the
whole point of the field: "payment received" on a 0 invoice sends someone
looking for a debit that does not exist. An unknown variant is read as `paid`
— announcing that nothing was charged when something was is the worse of the
two failures, so the fallback goes the other way. `variant_of()` still
recomputes the backend rule, but only when the field is absent.

**A trial's `period_end` is labelled *First charge*, not *Next renewal*.** Same
date, opposite meaning, and the mail says "first charge" — the two halves of one
invoice must not contradict each other.

**Amounts are printed exactly as Stripe reported them**, minor units except for
the zero-decimal currencies. No conversion, no VAT, no catalogue price.

**The DM goes through the notification system** (rule #11) under the new
`subscription` service, which is one of the `OFFICIAL_SERVICES`: its attribution
line carries the verification check, because a billing DM is exactly what a scam
impersonates.

**Language: the account's `LANG` attribute**, the same one the backend's mail
reads, falling back to English — one invoice must not arrive in two languages.
This is the first place in the bot that reads `LANG`.

**Dates are printed ISO (`2026-08-30`)** rather than as Discord timestamps: the
uniform payload is what the dashboard and a staff preview render, and `<t:…>`
is noise there. ISO is also unambiguous against a bank statement, unlike
`30/08` vs `08/30`.

**De-duplication is a belt, not a mechanism.** The backend already deduplicates
per invoice; `invoice:dm:{invoice_id}` (`SET NX`, TTL 7 days) only guards a
replayed event, and Redis being down costs a possible duplicate rather than the
DM. No catch-up for a missed event: the customer has the mail and the Stripe
receipt.

## Known issues / follow-ups

- The other subscription DMs (`_send_subscription_dm` in `bot.py`) still call
  `user.send()` directly with hardcoded English, predating both the notification
  system and the i18n rule. Worth migrating in a dedicated pass — the invoice DM
  deliberately does not follow them.
- `notifications.platforms` stays Discord-only here: the backend owns the mail,
  and a second one would read as a second charge.
