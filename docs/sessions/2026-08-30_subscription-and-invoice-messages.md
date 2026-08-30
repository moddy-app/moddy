# 2026-08-30 — Subscription welcome DM, Stripe invoice receipt, unlimited trial length

## What was done

### 1. The welcome DM (`notify_subscription_started`)

- New GIF (`https://media.tenor.com/eaDPAe9OLSoAAAAM/cat-kissing.gif`,
  from Tenor's *mwa* search as requested). The previous `files.catbox.moe`
  link stays dead in history only.
- The card now **says out loud that servers have to be selected**. This was the
  one thing missing: premium is not active on any server until the customer
  picks them, and the DM only hinted at it in the grey footnote. It is now a
  bolded paragraph in the body with an inline link, next to the existing
  *Select servers* button.

### 2. The invoice DM is now a Stripe receipt

`services/invoice_notifier.py` — the message is deliberately unlike the
celebratory lifecycle DMs:

| | Before | After |
|---|---|---|
| Language | account `LANG` | **always English** (`INVOICE_LOCALE`) |
| Attribution | `Sent by **Moddy Subscription**` | `Sent by **Stripe**<:verified:…>` |
| Icon / accent | `PREMIUM` gem / `0x245F9F` | `DOLLARS` / Stripe indigo `0x635BFF` |
| Wording | "Thanks for supporting Moddy!" | formal receipt, no thanks, no exclamation |
| Footer | none | Stripe is Moddy's billing/payment provider, Moddy never holds card details |
| Trial | `First charge: 2026-09-29` | end date **in the body** + `Trial ends — first charge` field |
| Buttons | Manage my subscription | Manage billing |

- **The trial end date was already available** — `period_end` on a trial
  invoice *is* the end of the trial and the date of the first charge. Nothing
  new had to be asked of the backend; it is now stated in the body sentence
  rather than only as a field label, since it is the whole point of a €0
  invoice.
- New notification service **`stripe`** (`notifications/models.py`), added to
  `OFFICIAL_SERVICES` so the attribution line carries the verification check.
  It is the first entry in that list that is not Moddy itself. Its icon, and
  the invoice card's title icon, is the new `DOLLARS` emoji
  (`<:dollars:1543645900797116436>`).
- `InvoiceNotifier.user_locale()` deleted — dead once the DM pins English.

### 3. `/manage stripe trial` — no more day cap

`staff/commands/manage/stripe/trial.py`: the Moddy-side `1-30` clamp is gone.
The only bound left is Stripe's own `trial_period_days` maximum (**730**),
kept as a clamp so an out-of-range value becomes the largest legal trial
instead of an opaque Stripe API error.

## Files modified

- `bot.py` — welcome DM (GIF + server selection)
- `utils/emojis.py` — `DOLLARS`
- `services/invoice_notifier.py` — English-only, Stripe source, footer, trial end, accent/icon
- `notifications/models.py` — `stripe` service
- `notifications/render.py` — `stripe` in `OFFICIAL_SERVICES`
- `locales/{en-US,fr,es-ES,pt-BR,de}.json` — invoice block rewritten, `services.stripe`
- `staff/commands/manage/stripe/trial.py` — trial length cap
- `tests/test_invoice_notifications.py` — updated + new cases (English-only, Stripe footer/attribution, trial end in body)
- `docs/SUBSCRIPTION_SCHEMA.md`, `docs/NOTIFICATIONS.md`, `docs/EMOJIS.md`

## Decisions and why

- **English-only invoice DM, against the previous "same language as the mail"
  rule.** The invoice, its PDF and Stripe's hosted page are English documents;
  a receipt that half-translates the document it describes reads as a forgery,
  and billing wording is one text to review rather than five. The localized
  keys were kept (updated, not deleted) so the mail/dashboard renderings of a
  stored row still work and the decision stays reversible in one constant.
- **Attributed to `stripe`, not `subscription`.** Stripe issues the invoice,
  takes the payment and holds the card details. The footer naming Stripe is
  what makes that attribution read as a fact instead of a stray brand name, and
  it is the only place in Moddy that names Stripe to a customer.
- **730-day clamp rather than none at all.** "No limit" is about Moddy not
  imposing one; Stripe still refuses anything above 730, and clamping turns
  that into the trial the staff member asked for.

## Follow-ups

- The lifecycle DMs in `bot.py::_send_subscription_dm` are still hardcoded
  English strings outside i18n — untouched here, but they are the obvious next
  cleanup.
