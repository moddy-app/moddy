"""
Stripe invoice notifications — the Discord half of a billing receipt.

The backend owns everything about an invoice: it activates premium, writes the
Redis cache, sends the customer's mail (Resend) and deduplicates on the Stripe
``invoice_id``. The bot's whole job is one DM, driven by a single Pub/Sub event
on ``moddy:subscription:updates``::

    {"type": "notify_invoice", "user_id": "…", "invoice": {…}}

Three rules carry the design, and all three exist to keep the DM honest:

* **The wording is chosen on ``variant``, never on the amount.** A free trial
  produces a real Stripe invoice at 0. Saying "payment received" on it sends
  the person hunting for a charge that does not exist on their bank statement —
  the worst possible outcome for a billing message. An unknown variant is read
  as ``paid``, the conservative default the backend's own rule falls back to.
* **On a trial, ``period_end`` is the date the trial ends and the *first*
  charge is taken**, not a renewal. It is the most useful line of the message,
  so it is said twice: in the body and as its own field. The mail says the same
  thing, and the two must not contradict.
* **This DM is a receipt, not a thank-you note.** It is deliberately unlike the
  subscription lifecycle DMs (``bot.py::_send_subscription_dm``): formal, in
  English whatever the account's language (:data:`INVOICE_LOCALE`), attributed
  to **Stripe** rather than to Moddy, and carrying Stripe's accent colour. The
  attribution is the truth of the matter — Stripe issues the invoice, processes
  the payment and holds the card details; Moddy only relays it.

Amounts arrive in the currency's smallest unit and are printed as they are:
no conversion, no VAT reconstruction, no hardcoded catalogue price. Anything
that could make the DM disagree with the invoice PDF is a bug.

A missed event is not an incident — like every other ``notify_*`` on this
channel, a restart loses it, and the customer still has their mail and their
Stripe receipt. The backend already deduplicates per invoice; the Redis key
here is a second belt for the case where the same event is replayed.

See docs/SUBSCRIPTION_SCHEMA.md §3.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import discord

logger = logging.getLogger("moddy.invoice_notifier")

#: Currencies Stripe reports without a minor unit: ``amount_paid`` is already
#: the full amount, dividing it by 100 would understate the invoice a hundred
#: times over. https://docs.stripe.com/currencies#zero-decimal
ZERO_DECIMAL_CURRENCIES = frozenset({
    "bif", "clp", "djf", "gnf", "jpy", "kmf", "krw", "mga",
    "pyg", "rwf", "ugx", "vnd", "vuv", "xaf", "xof", "xpf",
})

#: Symbols worth printing instead of the ISO code. Anything else keeps its
#: uppercase code, which is never wrong, only less pretty.
CURRENCY_SYMBOLS = {"eur": "€", "usd": "$", "gbp": "£"}

#: Locales that write "4,99 €" rather than "€4.99".
_SUFFIX_LOCALES = ("fr", "es", "pt", "de", "it", "nl", "pl", "ru", "tr")

#: The three shapes an invoice can take. Anything else is read as ``paid``.
VARIANTS = ("paid", "trial", "free")

#: Belt-and-braces de-duplication, on top of the backend's own.
DEDUP_KEY = "invoice:dm:{invoice_id}"
DEDUP_TTL = 7 * 86400  # 7 days, same window as the backend's

#: Stripe's own indigo, not Moddy's subscription blue: this card is a receipt
#: issued by the payment provider, and looking different from the celebratory
#: subscription DMs is the point.
ACCENT_COLOR = 0x635BFF

#: Billing messages are written in English, whatever the account's ``LANG``.
#: The invoice itself, its PDF and Stripe's hosted page are English documents;
#: a receipt that half-translates the document it describes reads as a forgery,
#: and a legal/billing wording is one text to review rather than five.
INVOICE_LOCALE = "en-US"

BILLING_URL = "https://dashboard.moddy.app/billing"


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #

def variant_of(invoice: Dict[str, Any]) -> str:
    """The invoice's variant: ``paid``, ``trial`` or ``free``.

    The backend computes this (``invoices.variant_of``) and sends it; this is
    only the fallback for a payload written before the field existed, and the
    guard against an unknown value. Recomputing it here is deliberate
    duplication of one rule, not a second source of truth: when ``variant`` is
    present it always wins.
    """
    declared = (invoice.get("variant") or "").strip().lower()
    if declared in VARIANTS:
        return declared
    if declared:
        # A variant the bot does not know about yet: treat it as a real
        # payment rather than telling someone nothing was charged when it was.
        logger.warning("[Invoice] Unknown variant %r — treated as paid", declared)
        return "paid"

    try:
        amount = int(invoice.get("amount_paid") or 0)
    except (TypeError, ValueError):
        amount = 0
    if amount > 0:
        return "paid"
    if invoice.get("billing_reason") == "subscription_create":
        return "trial"
    return "free"


def format_amount(amount_minor: Any, currency: Optional[str], *,
                  locale: str = "en-US") -> str:
    """Print an amount exactly as Stripe reported it.

    ``amount_minor`` is the smallest unit of ``currency`` (cents for EUR/USD),
    except for the zero-decimal currencies, where it already is the amount.
    ``0`` is a legitimate value — a trial invoice is a real invoice.
    """
    try:
        raw = int(amount_minor or 0)
    except (TypeError, ValueError):
        raw = 0

    code = (currency or "eur").strip().lower()
    if code in ZERO_DECIMAL_CURRENCIES:
        number = f"{raw:d}"
    else:
        number = f"{raw / 100:.2f}"

    symbol = CURRENCY_SYMBOLS.get(code)
    if not symbol:
        return f"{number} {code.upper()}"

    if locale.split("-")[0].lower() in _SUFFIX_LOCALES:
        # "4,99 €" — decimal comma included, or the amount reads as English.
        return f"{number.replace('.', ',')} {symbol}"
    return f"{symbol}{number}"


def parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp from the backend, tolerating ``null``."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        logger.warning("[Invoice] Unparsable timestamp: %r", value)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_date(value: Any) -> Optional[str]:
    """An ISO date (``2026-08-30``) — unambiguous in every locale.

    A billing date is compared against a bank statement and against the mail
    the backend sent; ``30/08`` vs ``08/30`` is exactly the kind of ambiguity
    that costs a support ticket.
    """
    parsed = parse_timestamp(value)
    if parsed is None:
        return None
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #

class InvoiceNotifier:
    """Turns a ``notify_invoice`` event into one DM (``bot.invoices``)."""

    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------------ entry
    async def handle(self, payload: Dict[str, Any]) -> bool:
        """Handle one ``notify_invoice`` event. Returns whether a DM was sent.

        Never raises: a malformed payload, a closed DM or an unreachable user
        each degrade to "no notification", and the customer still has the mail
        the backend sent them.
        """
        invoice = payload.get("invoice") or {}
        if not isinstance(invoice, dict) or not invoice:
            logger.warning("[Invoice] Event without an invoice object — ignored")
            return False

        try:
            user_id = int(payload.get("user_id"))
        except (TypeError, ValueError):
            logger.warning("[Invoice] Invalid user_id: %r", payload.get("user_id"))
            return False

        invoice_id = invoice.get("invoice_id")
        if invoice_id and await self._already_sent(invoice_id):
            logger.info("[Invoice] %s already DMed to %s — skipped",
                        invoice_id, user_id)
            return False

        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
        except discord.HTTPException as exc:
            logger.warning("[Invoice] Could not fetch user %s: %s", user_id, exc)
            return False
        if user is None:
            return False

        locale = INVOICE_LOCALE
        variant = variant_of(invoice)
        content, variables = self.build_content(invoice, variant, locale=locale)

        from notifications.models import NotificationSource

        try:
            result = await self.bot.notifications.send_dm(
                user,
                content=content,
                # Attributed to Stripe, which is who actually issued the
                # invoice, and an official service so the line carries the
                # check — the one thing that tells someone this billing DM is
                # not one of the many that impersonate a bot's billing.
                source=NotificationSource.service("stripe"),
                variables=variables,
                locale=locale,
            )
        except discord.HTTPException as exc:
            logger.warning("[Invoice] DM to %s failed: %s", user_id, exc)
            return False

        if result.delivered:
            logger.info("[Invoice] Sent %s invoice %s to user %s",
                        variant, invoice_id, user_id)
        elif result.forbidden:
            logger.info("[Invoice] Cannot DM user %s (DMs closed)", user_id)
        elif result.error is not None:
            logger.error("[Invoice] DM to %s failed: %s", user_id, result.error)
        return result.delivered

    # --------------------------------------------------------------- content
    def build_content(self, invoice: Dict[str, Any], variant: str, *,
                      locale: str = INVOICE_LOCALE):
        """The uniform payload behind an invoice DM, plus its variables.

        Kept separate from :meth:`handle` so the wording of the three variants
        is testable without a bot, a user or a Discord connection. ``locale``
        is a parameter for the dashboard and mail renderings of a stored row;
        the DM itself always passes :data:`INVOICE_LOCALE`.
        """
        from notifications.models import NotificationContent
        from utils.emojis import NOTE
        from utils.i18n import t

        tier = invoice.get("tier")
        tier_key = f"commands.subscription.invoice.tier.{tier}"
        tier_name = t(tier_key, locale=locale) if tier in ("monthly", "yearly") else "—"

        amount = format_amount(invoice.get("amount_paid"),
                               invoice.get("currency"), locale=locale)
        paid_at = format_date(invoice.get("paid_at"))
        period_end = format_date(invoice.get("period_end"))

        sections = []
        number = invoice.get("number")
        if number:
            sections.append({
                "title": t("commands.subscription.invoice.field_number", locale=locale),
                "body": "`{number}`",
            })
        sections.append({
            "title": t("commands.subscription.invoice.field_amount", locale=locale),
            "body": "`{amount}`",
        })
        if paid_at:
            sections.append({
                "title": t("commands.subscription.invoice.field_date", locale=locale),
                "body": "`{paid_at}`",
            })
        if period_end:
            # On a trial this date is the end of the trial and the first
            # charge, not a renewal. Naming it "next renewal" would hide the
            # only thing the customer needs to know about a 0 invoice.
            label = ("field_trial_end" if variant == "trial"
                     else "field_next_renewal")
            sections.append({
                "title": t(f"commands.subscription.invoice.{label}", locale=locale),
                "body": "`{period_end}`",
            })

        links = []
        if invoice.get("hosted_invoice_url"):
            links.append({
                "label": t("commands.subscription.invoice.button_view", locale=locale),
                "url": invoice["hosted_invoice_url"],
            })
        if invoice.get("invoice_pdf"):
            links.append({
                "label": t("commands.subscription.invoice.button_pdf", locale=locale),
                "url": invoice["invoice_pdf"],
            })
        links.append({
            "label": t("commands.subscription.invoice.button_manage", locale=locale),
            "url": BILLING_URL,
        })

        content = NotificationContent(
            title=t(f"commands.subscription.invoice.title.{variant}", locale=locale),
            body=t(f"commands.subscription.invoice.body.{variant}", locale=locale),
            icon=NOTE,
            accent_color=ACCENT_COLOR,
            sections=sections,
            links=links,
            # The one message that names Stripe: a receipt has to say who
            # issued it, and this is also what makes the Stripe attribution
            # line above read as a fact rather than as a stray brand name.
            footer=t("commands.subscription.invoice.footer_stripe", locale=locale),
            template_id=f"subscription.invoice.{variant}",
        )
        variables = {
            "amount": amount,
            "tier": tier_name,
            "number": number or "—",
            "paid_at": paid_at or "—",
            "period_end": period_end or "—",
        }
        return content, variables

    # --------------------------------------------------------------- helpers
    async def _already_sent(self, invoice_id: str) -> bool:
        """Claim this invoice, or report that another delivery already did.

        ``SET NX`` is the claim and the check in one round trip. Redis being
        down must not cost the DM — the backend deduplicates on its side too,
        so the worst case here is a duplicate, never a silence.
        """
        redis = getattr(self.bot, "redis", None)
        if redis is None:
            return False
        try:
            claimed = await redis.set(
                DEDUP_KEY.format(invoice_id=invoice_id), "1",
                ex=DEDUP_TTL, nx=True,
            )
        except Exception as exc:  # noqa: BLE001 — Redis is not the source of truth
            logger.warning("[Invoice] Redis de-duplication unavailable: %s", exc)
            return False
        return not claimed
