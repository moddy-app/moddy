"""Staff membership events published for the backend (Discord linked roles).

The backend publishes two booleans per account to Discord — ``team`` and
``premium`` — and Discord assigns the roles servers configured from them. The
bot has **one** obligation in that pipeline: telling the backend when the
composition of the team changes. Premium is already covered (Stripe notifies
the backend); the staff rank is not, because the bot is what writes
``staff_permissions`` and the backend has no way of learning it.

Three rules shape this module:

- **Publish after the write, never before.** The backend re-reads
  ``staff_permissions`` from the database; it does not trust the message
  content. Publishing first would have it re-read the *old* state and leave the
  Discord role wrong until the next resynchronisation (6 h).
- **The message carries an identifier, not a state.** ``roles`` is context for
  the backend logs and nothing else — which is also what makes the channel
  safe: a forged message on ``moddy:staff`` cannot hand anybody a role, at
  worst it triggers a republication that recomputes the truth.
- **Fire and forget.** A promotion must never fail because Redis hiccuped; a
  lost message is caught by the backend's periodic resynchronisation.

See docs/LINKED_ROLES.md.
"""

from __future__ import annotations

import json
import logging
from typing import List, Optional

logger = logging.getLogger('moddy.services.staff_events')

STAFF_CHANNEL = "moddy:staff"

# The account entered the team, or gained one more role.
EVENT_RANKED = "staff_ranked"
# The account left the team (no role left at all).
EVENT_UNRANKED = "staff_unranked"
# Its roles changed without its membership changing.
EVENT_UPDATED = "staff_updated"


async def notify_staff_change(bot, user_id: int, *, event: str,
                              roles: Optional[List[str]] = None) -> bool:
    """Tell the backend a staff rank changed. Returns whether it went out.

    Call it **after** the ``staff_permissions`` write and the ``TEAM``
    attribute sync — the two always go together, and the backend re-reads the
    table rather than trusting this payload.

    Never raises: the caller's command has already succeeded by the time this
    runs, and a Redis outage must not turn it into a failure.
    """
    redis = getattr(bot, 'redis', None)
    if not redis:
        logger.debug("staff: no Redis, %s for %s not published", event, user_id)
        return False

    payload = {"type": event, "user_id": str(user_id)}
    if roles is not None:
        payload["roles"] = list(roles)

    try:
        await redis.publish(STAFF_CHANNEL, json.dumps(payload))
    except Exception:  # noqa: BLE001 — a lost event is caught by the 6 h resync
        logger.warning("staff: publishing %s failed for %s", event, user_id,
                       exc_info=True)
        return False

    logger.info("staff: published %s for %s on %s", event, user_id, STAFF_CHANNEL)
    return True
