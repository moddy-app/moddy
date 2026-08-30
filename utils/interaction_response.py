"""Interaction delivery guarantees.

Discord gives an interaction a 3-second window to receive its *first*
response, then 15 minutes for follow-ups. Miss the first window and every
call on that interaction fails with ``10062 Unknown interaction`` — and the
user is left staring at Discord's own "The application did not respond".

That outcome is never acceptable: an unexpected error must always reach the
user as Moddy's own error card carrying an error code. This module is the
single place that knows how to make that true.

Two public helpers:

- :func:`safe_defer` — acknowledge an interaction as early as possible and
  never explode when the window has already closed.
- :func:`deliver` — put a view in front of the user no matter the state of
  the interaction, walking down a cascade of transports until one works
  (followup → edit → initial response → channel message).

Both are total: they never raise. Callers on an error path can rely on that.
"""

from __future__ import annotations

import logging
from typing import Optional

import discord

logger = logging.getLogger("moddy.interactions")

#: Discord error codes meaning "this interaction token is no longer usable".
#: 10062 — unknown interaction (the 3s acknowledgement window elapsed).
#: 40060 — interaction has already been acknowledged.
_EXPIRED_CODES = {10062}
_ALREADY_ACKED_CODES = {40060}


def is_expired_interaction(error: BaseException) -> bool:
    """True if ``error`` means the interaction token is dead (10062)."""
    return isinstance(error, discord.HTTPException) and error.code in _EXPIRED_CODES


def is_already_acknowledged(error: BaseException) -> bool:
    """True if ``error`` means the interaction was already acknowledged (40060)."""
    return isinstance(error, discord.HTTPException) and error.code in _ALREADY_ACKED_CODES


async def safe_defer(
    interaction: discord.Interaction,
    *,
    ephemeral: bool = True,
    thinking: Optional[bool] = None,
) -> bool:
    """Defer ``interaction`` without ever raising.

    Returns ``True`` when the interaction is acknowledged (either by this
    call or by an earlier one), ``False`` when the token is already dead.

    Call this as the *first* awaited statement of any handler that may take
    more than a moment — a permission lookup, an audit-log write and an API
    call are each enough to burn the 3-second budget on a cold connection.

    ``thinking`` defaults to what the interaction type actually wants. A
    slash command has no message yet, so it needs the "thinking…" placeholder
    or the user sees nothing. A component callback re-rendering its own panel
    in place must NOT get one — ``thinking=False`` there means "acknowledge
    silently, the message is about to be edited". Passing the wrong one
    leaves a stray placeholder next to the panel, so let this decide unless
    you have a reason not to.
    """
    if interaction.response.is_done():
        return True
    if thinking is None:
        thinking = interaction.type is discord.InteractionType.application_command
    try:
        await interaction.response.defer(ephemeral=ephemeral, thinking=thinking)
        return True
    except discord.HTTPException as exc:
        if is_already_acknowledged(exc):
            return True
        if is_expired_interaction(exc):
            logger.warning(
                "Interaction expired before it could be deferred (command=%s user=%s) — "
                "falling back to channel delivery",
                getattr(interaction.command, "qualified_name", None),
                getattr(interaction.user, "id", None),
            )
            return False
        logger.warning("Failed to defer interaction: %s", exc)
        return False
    except Exception as exc:  # pragma: no cover - defensive, defer must never raise
        logger.warning("Unexpected failure deferring interaction: %s", exc)
        return False


async def deliver(
    interaction: discord.Interaction,
    *,
    view: Optional[discord.ui.LayoutView] = None,
    content: Optional[str] = None,
    ephemeral: bool = True,
    allow_channel_fallback: bool = True,
) -> bool:
    """Get ``view``/``content`` in front of the user, whatever it takes.

    The cascade, stopping at the first transport that succeeds:

    1. ``followup.send`` — the interaction is acknowledged and alive.
    2. ``edit_original_response`` — acknowledged, but the followup was
       refused (a deferred-and-consumed response, typically).
    3. ``response.send_message`` — not acknowledged yet.
    4. a plain channel message mentioning the user — the token is dead, so
       this is the only way the user ever learns what happened.

    Returns ``True`` if any transport delivered. Never raises: this runs on
    the error path, where a second exception would silence the first.
    """
    ordered = (
        (_via_followup, _via_edit, _via_initial)
        if interaction.response.is_done()
        else (_via_initial, _via_followup, _via_edit)
    )

    for transport in ordered:
        try:
            await transport(interaction, view=view, content=content, ephemeral=ephemeral)
            return True
        except discord.HTTPException as exc:
            if is_expired_interaction(exc):
                break  # token is dead — no interaction transport can work
            continue
        except Exception:  # pragma: no cover - defensive
            continue

    if not allow_channel_fallback:
        logger.error(
            "Could not deliver interaction response (user=%s) and channel fallback is disabled",
            getattr(interaction.user, "id", None),
        )
        return False

    return await deliver_out_of_band(interaction, view=view, content=content)


async def deliver_out_of_band(
    interaction: discord.Interaction,
    *,
    view: Optional[discord.ui.LayoutView] = None,
    content: Optional[str] = None,
) -> bool:
    """Deliver outside the interaction, as a plain channel message.

    Used when the interaction token is unusable. The message pings the
    invoker so the card is not mistaken for a reply to someone else.

    Deliberately does not fall back to a DM: every DM Moddy sends goes
    through ``bot.notifications`` (see docs/NOTIFICATIONS.md), and an error
    card is not a notification.
    """
    user = getattr(interaction, "user", None)
    mention = user.mention if user else ""
    channel = getattr(interaction, "channel", None)

    if channel is not None and hasattr(channel, "send"):
        try:
            await channel.send(
                content=f"{mention} {content}".strip() if content else (mention or None),
                view=view,
                allowed_mentions=discord.AllowedMentions(users=True),
            )
            return True
        except Exception as exc:
            logger.debug("Channel fallback failed: %s", exc)

    logger.error(
        "CRITICAL: no transport could reach the user (user=%s guild=%s) — "
        "they will see Discord's own failure message",
        getattr(user, "id", None),
        getattr(interaction, "guild_id", None),
    )
    return False


# --- transports ------------------------------------------------------------


async def _via_followup(interaction, *, view, content, ephemeral):
    await interaction.followup.send(content=content, view=view, ephemeral=ephemeral)


async def _via_edit(interaction, *, view, content, ephemeral):
    await interaction.edit_original_response(content=content, view=view)


async def _via_initial(interaction, *, view, content, ephemeral):
    await interaction.response.send_message(content=content, view=view, ephemeral=ephemeral)
