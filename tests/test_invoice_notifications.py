"""Stripe invoice DMs — variant wording, amounts, de-duplication.

What these guard, in order of how badly they would hurt a paying customer:

* **The wording follows ``variant``, never the amount.** Telling someone their
  payment was received when a free trial charged them nothing sends them
  looking for a debit that does not exist. This is the whole reason the field
  exists, so an unknown value must fall back to ``paid``, never to "nothing was
  charged".
* **Amounts are printed as Stripe reported them.** Minor units everywhere
  except the zero-decimal currencies, where dividing by 100 would understate
  an invoice a hundredfold. ``0`` is a legitimate amount.
* **A trial's ``period_end`` is the first charge, not a renewal.** Same date,
  opposite meaning; the mail says "first charge" and the DM must agree.
* **Missing halves stay missing.** ``number``, ``invoice_pdf``,
  ``hosted_invoice_url`` and the dates are all nullable on a fresh invoice; a
  null must not become a dead button or the string "None".

Everything here runs against the pure helpers and the content builder — no
gateway, no database, no Redis.
"""

from __future__ import annotations

import pytest

from notifications.models import SERVICES, NotificationSource, get_service
from notifications.render import OFFICIAL_SERVICES, build_attribution_line, resolve_source_context
from services.invoice_notifier import (
    ZERO_DECIMAL_CURRENCIES, InvoiceNotifier, format_amount, format_date,
    parse_timestamp, variant_of,
)

PAID = {
    "invoice_id": "in_1S000000000000",
    "number": "MODDY-0001",
    "amount_paid": 499,
    "currency": "eur",
    "paid_at": "2026-08-30T11:26:40+00:00",
    "period_end": "2026-09-29T11:26:40+00:00",
    "tier": "monthly",
    "billing_reason": "subscription_cycle",
    "variant": "paid",
    "hosted_invoice_url": "https://invoice.stripe.com/i/abc",
    "invoice_pdf": "https://invoice.stripe.com/i/abc.pdf",
}

TRIAL = dict(PAID, amount_paid=0, billing_reason="subscription_create",
             variant="trial", invoice_id="in_1S000000000001")

FREE = dict(PAID, amount_paid=0, billing_reason="subscription_cycle",
            variant="free", invoice_id="in_1S000000000002")


class FakeBot:
    """Just enough bot for the content builder (no gateway, no database)."""

    def __init__(self):
        self.redis = None
        self.db = None


def _content(invoice, *, locale="en-US"):
    variant = variant_of(invoice)
    return InvoiceNotifier(FakeBot()).build_content(invoice, variant, locale=locale)


# --------------------------------------------------------------------------- #
# variant_of
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("invoice,expected", [
    (PAID, "paid"), (TRIAL, "trial"), (FREE, "free"),
])
def test_the_declared_variant_wins(invoice, expected):
    assert variant_of(invoice) == expected


def test_an_unknown_variant_is_read_as_paid():
    """Never claim nothing was charged on an invoice we do not understand."""
    assert variant_of(dict(PAID, variant="proration")) == "paid"


def test_a_missing_variant_falls_back_to_the_backend_rule():
    """Old payloads: amount first, then the billing reason."""
    assert variant_of({"amount_paid": 499, "billing_reason": "subscription_create"}) == "paid"
    assert variant_of({"amount_paid": 0, "billing_reason": "subscription_create"}) == "trial"
    assert variant_of({"amount_paid": 0, "billing_reason": "subscription_cycle"}) == "free"
    assert variant_of({}) == "free"


# --------------------------------------------------------------------------- #
# Amounts
# --------------------------------------------------------------------------- #

def test_a_minor_unit_amount_becomes_its_major_unit():
    assert format_amount(499, "eur", locale="en-US") == "€4.99"


def test_a_zero_decimal_currency_is_never_divided():
    """500 JPY is 500 yen, not 5."""
    assert format_amount(500, "jpy", locale="en-US") == "500 JPY"
    assert "jpy" in ZERO_DECIMAL_CURRENCIES


def test_zero_is_a_legitimate_amount():
    assert format_amount(0, "eur", locale="en-US") == "€0.00"
    assert format_amount(0, "eur", locale="fr") == "0,00 €"


def test_an_unknown_currency_keeps_its_iso_code():
    assert format_amount(1234, "chf", locale="en-US") == "12.34 CHF"


def test_a_missing_amount_is_read_as_zero_rather_than_raising():
    assert format_amount(None, None, locale="en-US") == "€0.00"


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #

def test_a_date_is_printed_iso_so_it_cannot_be_misread():
    assert format_date("2026-08-30T11:26:40+00:00") == "2026-08-30"


def test_a_naive_timestamp_is_read_as_utc():
    assert parse_timestamp("2026-08-30T11:26:40").tzinfo is not None


@pytest.mark.parametrize("value", [None, "", "not-a-date"])
def test_an_unusable_date_is_none_not_a_crash(value):
    assert format_date(value) is None


# --------------------------------------------------------------------------- #
# Wording
# --------------------------------------------------------------------------- #

def test_a_paid_invoice_says_the_payment_was_received():
    content, variables = _content(PAID)
    assert content.template_id == "subscription.invoice.paid"
    rendered = content.render(variables).body
    assert "€4.99" in rendered
    assert "no amount" not in rendered.lower()


def test_a_trial_never_claims_a_payment():
    content, variables = _content(TRIAL)
    assert content.template_id == "subscription.invoice.trial"
    rendered = content.render(variables).body
    assert "no amount was charged" in rendered.lower()
    assert "payment of" not in rendered.lower()


def test_a_free_period_never_claims_a_payment():
    content, variables = _content(FREE)
    rendered = content.render(variables).body
    assert "no amount was charged" in rendered.lower()


def test_a_trial_labels_period_end_as_the_first_charge():
    """Same date as a renewal, opposite meaning — and the mail says 'first charge'."""
    content, _ = _content(TRIAL)
    titles = [s["title"] for s in content.sections]
    assert "First charge" in titles
    assert "Next renewal" not in titles


def test_a_paid_invoice_labels_period_end_as_the_next_renewal():
    content, _ = _content(PAID)
    titles = [s["title"] for s in content.sections]
    assert "Next renewal" in titles
    assert "First charge" not in titles


def test_the_body_keeps_its_placeholders_so_one_row_serves_every_invoice():
    content, variables = _content(PAID)
    assert "{amount}" in content.body
    assert variables["amount"] == "€4.99"


def test_the_wording_is_translated():
    content, variables = _content(PAID, locale="fr")
    rendered = content.render(variables)
    assert "4,99 €" in rendered.body
    assert "mensuel" in rendered.body


# --------------------------------------------------------------------------- #
# Nullable halves
# --------------------------------------------------------------------------- #

def test_a_missing_link_produces_no_button():
    bare = dict(PAID, hosted_invoice_url=None, invoice_pdf=None)
    content, _ = _content(bare)
    # Only the billing dashboard remains — never a button pointing at nothing.
    assert [l["url"] for l in content.links] == ["https://dashboard.moddy.app/billing"]


def test_a_fresh_invoice_without_a_number_simply_omits_the_line():
    content, _ = _content(dict(PAID, number=None))
    assert all("`{number}`" != s["body"] for s in content.sections)


def test_a_missing_period_end_omits_the_date_line_entirely():
    content, _ = _content(dict(PAID, period_end=None, paid_at=None))
    titles = [s["title"] for s in content.sections]
    assert "Next renewal" not in titles and "Date" not in titles


def test_an_unknown_tier_never_prints_a_translation_key():
    content, variables = _content(dict(PAID, tier=None))
    assert "[" not in variables["tier"]


# --------------------------------------------------------------------------- #
# De-duplication
# --------------------------------------------------------------------------- #

class FakeRedis:
    """A ``SET NX`` that only succeeds once per key."""

    def __init__(self, *, broken=False):
        self.keys = {}
        self.broken = broken

    async def set(self, key, value, ex=None, nx=False):
        if self.broken:
            raise RuntimeError("redis down")
        if nx and key in self.keys:
            return None
        self.keys[key] = value
        return True


async def test_the_same_invoice_is_only_dmed_once():
    bot = FakeBot()
    bot.redis = FakeRedis()
    notifier = InvoiceNotifier(bot)
    assert await notifier._already_sent("in_1") is False
    assert await notifier._already_sent("in_1") is True
    assert await notifier._already_sent("in_2") is False


async def test_redis_being_down_costs_a_duplicate_never_the_dm():
    notifier = InvoiceNotifier(FakeBot())
    notifier.bot.redis = FakeRedis(broken=True)
    assert await notifier._already_sent("in_1") is False


async def test_no_redis_at_all_still_lets_the_dm_through():
    assert await InvoiceNotifier(FakeBot())._already_sent("in_1") is False


# --------------------------------------------------------------------------- #
# Attribution
# --------------------------------------------------------------------------- #

def test_the_subscription_service_is_registered():
    assert "subscription" in SERVICES
    assert get_service("subscription").emoji.startswith("<")


async def test_a_billing_dm_carries_the_verification_check():
    """A billing DM is what a scam impersonates: the check is the tell."""
    assert "subscription" in OFFICIAL_SERVICES
    ctx = await resolve_source_context(None, NotificationSource.service("subscription"))
    assert ctx["verified"] is True
    assert ctx["reportable"] is False  # Moddy wrote it; there is nothing to judge
    assert ctx["badge"] in build_attribution_line(ctx, locale="en-US")
