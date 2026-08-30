"""`/manage stripe …` — Stripe subscription admin actions.

Cancel, resume, refund and start-trial, all sent as signed
``stripe_action`` events on ``moddy:dashboard`` and awaited on ``moddy:bot``
(see ``services/stripe_admin_client.py`` and docs/REDIS_COMMUNICATION.md).
"""
