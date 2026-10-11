"""
Advanced Error Handling System for Moddy
Tracking, Discord logs, and notifications with database integration
"""

import discord
from discord import ui
from discord.ext import commands
import traceback
import secrets
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import asyncio
import sys
from collections import deque
import os

from config import COLORS
import logging

logger = logging.getLogger('moddy.error_handler')

# Initialize Sentry integration
import sentry_sdk

# Only initialize Sentry if SENTRY_DSN is set in environment
SENTRY_DSN = os.getenv('SENTRY_DSN')
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        # Add data like request headers and IP for users
        # see https://docs.sentry.io/platforms/python/data-management/data-collected/ for more info
        send_default_pii=False,
        # What we actually use Sentry for is error tracking, which is unaffected by
        # these two. Tracing keeps a span buffer per transaction, and the profiler
        # runs a dedicated sampling thread (and therefore its own glibc malloc
        # arena) -- both are pure memory cost here. Overridable per environment.
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.01")),
        profiles_sample_rate=float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.0")),
        # Auto-enabled integrations import their target library at init time
        # (e.g. the OpenAI integration imports `openai`, which imports its
        # vendored aiohttp transport). A version mismatch in that import
        # chain raises before this module even finishes loading, and since
        # BaseView (defined below) is imported by nearly every cog/module,
        # that takes the whole bot down. Disable auto-detection so a broken
        # third-party import can never crash startup.
        auto_enabling_integrations=False,
    )
    logger.info("Sentry initialized successfully")
else:
    logger.warning("SENTRY_DSN not set - Sentry integration disabled")


def capture_error_to_sentry(error: Exception, context: Dict[str, Any] = None) -> Optional[str]:
    """
    Helper function to capture errors to Sentry with additional context.
    This runs in parallel to the existing error handling system.

    Returns:
        str: The Sentry event ID if capture was successful, None otherwise
    """
    if not SENTRY_DSN:
        return None  # Sentry not configured, skip

    try:
        # Set additional context if provided
        if context:
            with sentry_sdk.push_scope() as scope:
                # Add context information as extras
                for key, value in context.items():
                    scope.set_extra(key, value)

                # Add tags for better filtering in Sentry
                if 'error_code' in context:
                    scope.set_tag('error_code', context['error_code'])
                if 'command' in context:
                    scope.set_tag('command', context['command'])
                if 'guild_id' in context:
                    scope.set_tag('guild_id', str(context['guild_id']))
                if 'user_id' in context:
                    scope.set_tag('user_id', str(context['user_id']))
                if 'error_type' in context:
                    scope.set_tag('error_type', context['error_type'])

                # Capture the exception and get the event ID
                event_id = sentry_sdk.capture_exception(error)
                return event_id
        else:
            # Capture without additional context
            event_id = sentry_sdk.capture_exception(error)
            return event_id
    except Exception as sentry_error:
        # Don't let Sentry errors break the main error handling
        logger.error(f"Failed to capture error to Sentry: {sentry_error}")
        return None


# ---------------------------------------------------------------------------
# Expired-interaction errors (10062 / 10008 / 10015)
# ---------------------------------------------------------------------------
# Discord telling us the interaction (or the message it points at) is gone: the
# 3 s initial-response window elapsed, the token passed its 15 min life, or the
# user dismissed the ephemeral message. These are NOT swallowed: each one is a
# user who may have seen "This interaction failed", so it goes through the full
# error path (code, Sentry, database, error channel) and the delivery chain
# falls back to a DM when the token is dead (see ``deliver_view``). The helpers
# below only add *context* to the details (interaction age, loop lag) so the
# cause - slow handler vs. blocked event loop - can be read off the report.
EXPIRED_INTERACTION_CODES = {
    10062: "unknown_interaction",
    10008: "unknown_message",
    10015: "unknown_webhook",
}
# Kinds meaning the interaction *token* itself is unusable (followups included).
DEAD_TOKEN_KINDS = {"unknown_interaction", "unknown_webhook"}
_EXPIRED_COUNTS: Dict[str, int] = {}
EXPIRED_REPORTED_KEY = "moddy_expired_reported"


def expired_interaction_kind(error: BaseException) -> Optional[str]:
    """Name of the expired-interaction kind wrapped by ``error``, else None."""
    seen = set()
    current: Optional[BaseException] = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, discord.NotFound):
            kind = EXPIRED_INTERACTION_CODES.get(getattr(current, "code", None))
            if kind:
                return kind
        current = (getattr(current, "original", None)
                   or current.__cause__ or current.__context__)
    return None


def is_too_slow(error: BaseException) -> bool:
    """True when ``error`` is (or wraps) the guard's ``InteractionTooSlow``."""
    try:
        from utils.interaction_guard import InteractionTooSlow
    except Exception:  # noqa: BLE001
        return False
    seen = set()
    current: Optional[BaseException] = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, InteractionTooSlow):
            return True
        current = (getattr(current, "original", None)
                   or current.__cause__ or current.__context__)
    return False


def interaction_context(interaction: Optional[discord.Interaction],
                        error: Optional[BaseException] = None) -> Dict[str, Any]:
    """Diagnostic context for the error details: age, loop lag, guard state."""
    ctx: Dict[str, Any] = {}
    kind = expired_interaction_kind(error) if error is not None else None
    if kind:
        _EXPIRED_COUNTS[kind] = _EXPIRED_COUNTS.get(kind, 0) + 1
        ctx["expired_kind"] = kind
        ctx["expired_count_since_boot"] = _EXPIRED_COUNTS[kind]
    if interaction is None:
        return ctx
    try:
        ctx["interaction_age"] = f"{(discord.utils.utcnow() - interaction.created_at).total_seconds():.2f}s"
        ctx["interaction_type"] = getattr(interaction.type, "name", str(interaction.type))
        ctx["response_done"] = bool(interaction.response.is_done())
    except Exception:  # noqa: BLE001 - context is best-effort
        pass
    try:
        from utils.loop_monitor import last_lag_summary
        ctx["loop"] = last_lag_summary()
    except Exception:  # noqa: BLE001
        pass
    try:
        from utils.interaction_guard import get_state
        state = get_state(interaction)
        if state is not None:
            ctx["guard"] = f"{state.status}/{state.mode} handler={state.handler}"
    except Exception:  # noqa: BLE001
        pass
    return ctx


async def fetch_sentry_issue_id(event_id: str, project_slug: str = "moddy") -> Optional[str]:
    """
    Fetch the Sentry Issue ID (group ID) from the Sentry API using the event ID.

    Args:
        event_id: The Sentry event ID
        project_slug: The Sentry project slug (default: "moddy")

    Returns:
        str: The Issue ID (group ID) if found, None otherwise
    """
    if not event_id or not SENTRY_DSN:
        return None

    sentry_api_token = os.getenv('SENTRY_API_TOKEN')
    if not sentry_api_token:
        logger.warning("SENTRY_API_TOKEN not set - cannot fetch issue ID")
        return None

    try:
        import aiohttp

        # API endpoint: /organizations/{org}/eventids/{event_id}/
        # Requires token with org:read, org:write, or org:admin scope
        url = f"https://sentry.io/api/0/organizations/moddy-0f/eventids/{event_id}/"

        headers = {
            "Authorization": f"Bearer {sentry_api_token}",
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    # According to Sentry API docs, groupId is at root level
                    issue_id = data.get('groupId')
                    if issue_id:
                        logger.info(f"Successfully fetched Sentry issue ID: {issue_id} for event {event_id}")
                        return str(issue_id)
                    else:
                        logger.warning(f"Sentry API returned 200 but no groupId found in response")
                        logger.debug(f"Response data: {data}")
                        return None
                elif response.status == 401:
                    # Unauthorized - token issue
                    error_text = await response.text()
                    logger.error(f"Sentry API authentication failed (401). Check SENTRY_API_TOKEN permissions.")
                    logger.error(f"Required scopes: org:read, org:write, or org:admin")
                    logger.debug(f"Response: {error_text[:500]}")
                    return None
                elif response.status == 403:
                    # Forbidden - permission issue
                    error_text = await response.text()
                    logger.error(f"Sentry API access forbidden (403). Token may not have required scope.")
                    logger.error(f"Required scopes: org:read, org:write, or org:admin")
                    logger.debug(f"Response: {error_text[:500]}")
                    return None
                elif response.status == 404:
                    # Event not found yet - might be too early
                    logger.info(f"Sentry event {event_id} not found yet (404). May need to wait longer.")
                    return None
                else:
                    # Other error
                    error_text = await response.text()
                    logger.warning(f"Failed to fetch Sentry issue ID: HTTP {response.status}")
                    logger.debug(f"URL: {url}")
                    logger.debug(f"Response: {error_text[:500]}")
                    return None

    except Exception as e:
        logger.error(f"Error fetching Sentry issue ID: {e}")
        return None


class BaseView(ui.LayoutView):
    """
    Base class for ALL UI Views with centralized error handling.

    ALL discord.ui Views MUST inherit from this class to ensure:
    1. Errors are caught and logged with FULL traceback in ONE log line
    2. User ALWAYS receives an error embed
    3. Error handler processes ALL exceptions

    Persistence
    -----------
    The default ``timeout`` is ``None`` — views never expire in memory.
    Subclasses that still want a timeout can pass ``timeout=<seconds>`` to
    ``super().__init__``; any numeric value overrides the default.

    To make a view survive a bot restart, set the class attribute
    ``__persistent__ = True`` and override :meth:`register_persistent`. The
    registration runs once in :func:`bot.setup_hook` via
    ``utils.persistent_views.register_all_persistent_views``. See
    ``docs/PERSISTENT_VIEWS.md`` for the full pattern.
    """

    # Override to True on subclasses that should be registered as persistent
    # at bot startup. Subclasses that set this to True MUST also implement
    # register_persistent(bot).
    __persistent__: bool = False

    def __init__(self, *, timeout: Optional[float] = None, **kwargs):
        # Default timeout=None so views never expire in memory, regardless of
        # whether they are registered as persistent. Subclasses can still
        # override by passing timeout=<seconds>.
        super().__init__(timeout=timeout, **kwargs)
        self.bot = None  # MUST be set by subclass

    @classmethod
    def register_persistent(cls, bot) -> None:
        """
        Register this view for persistence across bot restarts.

        Subclasses with ``__persistent__ = True`` MUST override this and
        typically call one of:

        - ``bot.add_view(cls())`` for a static shell instance
          (all children have stable ``custom_id``s and callbacks re-derive
          state from ``interaction``).
        - ``bot.add_dynamic_items(...)`` for ``discord.ui.DynamicItem``
          subclasses whose ``custom_id`` encodes state via a regex.

        See ``docs/PERSISTENT_VIEWS.md`` for the cookbook.
        """
        raise NotImplementedError(
            f"{cls.__name__}.__persistent__ is True but register_persistent() "
            f"is not implemented."
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        """
        Called when an error occurs in ANY UI component callback.
        This ensures ALL UI errors are caught and handled properly.
        NO EXCEPTIONS ESCAPE THIS HANDLER.
        """
        await report_interaction_error(
            interaction, error,
            command=f"UI:{self.__class__.__name__}",
            error_type="UI Error",
            log_label=f"UI Error in {self.__class__.__name__} - Item: {item.__class__.__name__}",
            bot=self.bot if self.bot else None,
            details={"item": f"{item.__class__.__name__}"},
            sentry_extra={
                "view_class": self.__class__.__name__,
                "item_class": item.__class__.__name__,
            },
        )


class BaseModal(ui.Modal):
    """
    Base class for ALL UI Modals with centralized error handling.

    Note
    ----
    Modals cannot be registered as persistent — Discord treats a modal
    submission as a one-shot interaction that must be answered while the
    owning message's component store is still in memory. ``discord.ui.Modal``
    already defaults ``timeout`` to ``None``, so modals will not expire
    mid-edit as long as the bot process stays up.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bot = None  # MUST be set by subclass

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        """
        Called when an error occurs in a Modal submit.
        """
        await report_interaction_error(
            interaction, error,
            command=f"Modal:{self.__class__.__name__}",
            error_type="Modal Error",
            log_label=f"Modal Error in {self.__class__.__name__}",
            bot=self.bot if self.bot else None,
            sentry_extra={"modal_class": self.__class__.__name__},
        )


# ---------------------------------------------------------------------------
# Localized error cards
# ---------------------------------------------------------------------------

def locale_for(interaction: Optional[discord.Interaction]) -> str:
    """Locale of the person who clicked (honours the /preferences override)."""
    try:
        from utils.i18n import i18n
        return i18n.get_user_locale(interaction)
    except Exception:  # noqa: BLE001 - the error path must not depend on i18n
        return "en-US"


def _tr(key: str, locale: Optional[str], **kwargs) -> str:
    from utils.i18n import i18n
    return i18n.get(key, locale=locale or "en-US", **kwargs)


class ErrorView(ui.LayoutView):
    """Error display view using Components V2 (localized).

    ``too_slow`` swaps the wording for "the bot was too slow, please retry"
    (``InteractionTooSlow`` / expired interactions); ``dm`` prepends the
    explanation used when the card is delivered by DM because the interaction
    could not be answered any more. Non-interactive (link buttons only), so
    nothing here needs to be persistent.
    """

    def __init__(self, error_code: str, locale: Optional[str] = None, *,
                 too_slow: bool = False, dm: bool = False):
        super().__init__(timeout=None)
        self.error_code = error_code
        self.locale = locale or "en-US"
        self.too_slow = too_slow
        self.dm = dm
        self.build_view()

    def build_view(self):
        loc = self.locale
        container = ui.Container(accent_color=discord.Colour(14296871))
        title = _tr("errors.too_slow.title" if self.too_slow else "errors.card.title", loc)
        container.add_item(ui.TextDisplay(f"### <:FrameBug:1519805334988787843> {title}"))
        if self.dm:
            container.add_item(ui.TextDisplay(_tr("errors.dm_fallback.intro", loc)))
        if self.too_slow:
            container.add_item(ui.TextDisplay(
                _tr("errors.too_slow.body", loc, error_code=self.error_code)))
        else:
            container.add_item(ui.TextDisplay(
                _tr("errors.card.code", loc, error_code=self.error_code)))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(_tr("errors.card.logged", loc)))
        self.add_item(container)

        btn_row = ui.ActionRow()
        btn_row.add_item(ui.Button(
            label=_tr("errors.card.support", loc),
            style=discord.ButtonStyle.link,
            url="https://moddy.app/support",
        ))
        btn_row.add_item(ui.Button(
            label=_tr("errors.card.status", loc),
            style=discord.ButtonStyle.link,
            url="https://status.moddy.app",
        ))
        self.add_item(btn_row)


class InfoNoticeView(ui.LayoutView):
    """Small localized notice card: ``<prefix>.title`` + ``<prefix>.body``.

    Used by the interaction guard ("this control is no longer active") and for
    commands Discord still lists but the bot no longer has. Non-interactive.
    """

    def __init__(self, key_prefix: str, locale: Optional[str] = None, **variables):
        super().__init__(timeout=None)
        loc = locale or "en-US"
        container = ui.Container()
        container.add_item(ui.TextDisplay(
            f"### <:warning:1519789903100121139> {_tr(key_prefix + '.title', loc)}"))
        container.add_item(ui.TextDisplay(_tr(key_prefix + ".body", loc, **variables)))
        self.add_item(container)


# ---------------------------------------------------------------------------
# Delivery chain: the user must ALWAYS get a message from the bot
# ---------------------------------------------------------------------------
DELIVERY_STEP_TIMEOUT = 8.0       # per attempt, a hung HTTP call must not stall the chain
DM_FALLBACK_TIMEOUT = 15.0
DM_FALLBACK_INTERVAL = 300.0      # at most one fallback DM per user / 5 min
_DM_LAST: Dict[int, float] = {}


async def dm_error_fallback(bot, user, error_code: str, locale: str, *,
                            too_slow: bool = False) -> str:
    """DM the error card through ``bot.notifications`` (CLAUDE.md rule #11).

    Returns ``dm`` | ``dm_throttled`` | ``dm_failed`` | ``dm_unavailable``. Rate
    limited per user so a loop stall that kills fifty interactions sends one DM.
    """
    import time as _time
    service = getattr(bot, "notifications", None)
    user_id = getattr(user, "id", None)
    if service is None or user is None or user_id is None:
        return "dm_unavailable"
    now = _time.monotonic()
    last = _DM_LAST.get(user_id)
    if last is not None and now - last < DM_FALLBACK_INTERVAL:
        return "dm_throttled"
    _DM_LAST[user_id] = now  # reserve first: concurrent failures must not all DM
    if len(_DM_LAST) > 5000:
        for uid in [u for u, ts in _DM_LAST.items() if now - ts > DM_FALLBACK_INTERVAL]:
            _DM_LAST.pop(uid, None)
    try:
        from notifications import NotificationContent, NotificationSource
        content = NotificationContent(
            title=_tr("errors.dm_fallback.title", locale),
            # Placeholders stay in the stored template; the code travels as a variable.
            body=_tr("errors.too_slow.dm_body" if too_slow else "errors.dm_fallback.body", locale),
            accent_color=14296871,
            template_id="error_handler.dm_fallback",
        )
        result = await asyncio.wait_for(
            service.send_dm(
                user, content=content, source=NotificationSource.official("moddy"),
                variables={"error_code": error_code},
                view=ErrorView(error_code, locale, too_slow=too_slow, dm=True),
                locale=locale),
            DM_FALLBACK_TIMEOUT)
        if getattr(result, "delivered", False):
            return "dm"
        logger.warning("Fallback error DM to %s not delivered (%s)", user_id,
                       getattr(result, "error", None))
        return "dm_failed"
    except Exception as exc:  # noqa: BLE001 - last resort, never raise into the handler
        logger.warning("Fallback error DM to %s failed: %s", user_id, exc)
        return "dm_failed"


async def deliver_view(interaction: discord.Interaction, view: ui.LayoutView, *,
                       dm_fallback=None) -> str:
    """Show ``view`` to the user by every path that can still work.

    Order: ``response.send_message`` (if not answered yet) -> ``followup.send``
    -> ``edit_original_response`` -> ``dm_fallback()`` (an async callable
    returning its own outcome string). A dead token (10062 / 10015, or older
    than 15 minutes) skips straight to the DM. Every failed attempt is logged.

    Returns how the user was reached: ``response`` | ``followup`` |
    ``edit_original`` | ``dm*`` | ``none``.
    """
    token_dead = False
    try:
        token_dead = (discord.utils.utcnow() - interaction.created_at).total_seconds() >= 14.5 * 60
    except Exception:  # noqa: BLE001
        pass

    async def attempt(name: str, factory) -> bool:
        nonlocal token_dead
        try:
            await asyncio.wait_for(factory(), DELIVERY_STEP_TIMEOUT)
            return True
        except asyncio.TimeoutError:
            logger.warning("Error delivery via %s timed out after %.0fs", name, DELIVERY_STEP_TIMEOUT)
        except discord.HTTPException as exc:
            logger.warning("Error delivery via %s failed: %s", name, exc)
            if expired_interaction_kind(exc) in DEAD_TOKEN_KINDS:
                token_dead = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error delivery via %s failed: %s: %s", name, type(exc).__name__, exc)
        return False

    if not token_dead:
        try:
            already = interaction.response.is_done()
        except Exception:  # noqa: BLE001
            already = True
        if not already and await attempt(
                "response", lambda: interaction.response.send_message(view=view, ephemeral=True)):
            return "response"
        if not token_dead and await attempt(
                "followup", lambda: interaction.followup.send(view=view, ephemeral=True)):
            return "followup"
        if not token_dead and await attempt(
                "edit_original", lambda: interaction.edit_original_response(content=None, view=view)):
            return "edit_original"
    if dm_fallback is not None:
        return await dm_fallback()
    return "none"


def _who(interaction: discord.Interaction) -> Dict[str, Any]:
    """Standard user/guild/channel entries of the error details."""
    user = getattr(interaction, "user", None)
    guild = getattr(interaction, "guild", None)
    channel = getattr(interaction, "channel", None)
    return {
        "user": f"{user} ({user.id})" if user else "?",
        "guild": f"{guild.name} ({guild.id})" if guild else "DM",
        "channel": f"#{channel.name}" if hasattr(channel, "name") else "DM",
        "user_id": user.id if user else None,
        "guild_id": guild.id if guild else getattr(interaction, "guild_id", None),
    }


_FATAL_TYPES = (RuntimeError, AttributeError, ImportError, MemoryError, SystemError, ModuleNotFoundError)


async def report_interaction_error(
    interaction: discord.Interaction,
    error: BaseException,
    *,
    command: str,
    error_type: str,
    log_label: Optional[str] = None,
    bot=None,
    details: Optional[Dict[str, Any]] = None,
    sentry_extra: Optional[Dict[str, Any]] = None,
    dm_fallback: bool = True,
) -> Optional[str]:
    """The single full error path for anything that failed under an interaction.

    Error code, one-line traceback log, Sentry, database row, error-channel post
    and a localized card for the user - delivered by whichever path still works,
    down to a DM when the interaction token is dead. Expired-interaction errors
    (10062 / 10008 / 10015) take this path too: they are user-impacting. The
    returned error code is ``None`` only when the same interaction was already
    reported for an expired token (the guard and the handler racing).
    """
    bot = bot or getattr(interaction, "client", None)
    kind = expired_interaction_kind(error)
    extras = getattr(interaction, "extras", None)
    if kind and isinstance(extras, dict):
        if extras.get(EXPIRED_REPORTED_KEY):
            logger.warning("Expired interaction (%s) in %s already reported; not repeating",
                           kind, command)
            return None
        extras[EXPIRED_REPORTED_KEY] = True

    compact_tb = ''.join(
        traceback.format_exception(type(error), error, error.__traceback__)).replace('\n', ' ⮐ ')
    logger.error(f"{log_label or error_type} [{command}] - {compact_tb}")

    locale = locale_for(interaction)
    too_slow = is_too_slow(error) or kind in DEAD_TOKEN_KINDS
    who = _who(interaction)
    ctx = interaction_context(interaction, error)
    tracker = bot.get_cog('ErrorTracker') if bot else None

    error_code = tracker.generate_error_code(error) if tracker else "UNTRACKED"
    sentry_event_id = capture_error_to_sentry(error, {
        'error_type': error_type,
        'error_code': error_code,
        'command': command,
        'user_id': who["user_id"],
        'guild_id': who["guild_id"],
        'channel_id': getattr(getattr(interaction, "channel", None), "id", None),
        **ctx,
        **(sentry_extra or {}),
    })

    async def _dm() -> str:
        if not dm_fallback:
            return "none"
        return await dm_error_fallback(
            bot, getattr(interaction, "user", None), error_code, locale, too_slow=too_slow)

    try:
        reached = await deliver_view(
            interaction, ErrorView(error_code, locale, too_slow=too_slow), dm_fallback=_dm)
    except Exception as exc:  # noqa: BLE001 - delivery problems must not hide the report
        logger.error(f"CRITICAL: error delivery chain crashed: {exc}", exc_info=True)
        reached = "none"
    if reached == "none" or reached.startswith("dm_"):
        logger.error("CRITICAL: user %s was NOT reached for error %s (%s)",
                     who["user_id"], error_code, reached)

    if tracker is None:
        logger.error("ERROR TRACKER NOT AVAILABLE - error %s only reached Sentry/logs", error_code)
        return error_code

    error_details = tracker.format_error_details(error)
    error_details.update({
        "command": command,
        "user": who["user"], "guild": who["guild"], "channel": who["channel"],
        "user_id": who["user_id"], "guild_id": who["guild_id"],
        "user_reached": reached,
        "interaction_context": ctx,
    })
    error_details.update(details or {})
    if sentry_event_id:
        error_details['sentry_event_id'] = sentry_event_id

    tracker.store_error(error_code, error_details)
    await tracker.store_error_db(error_code, error_details)
    if sentry_event_id and getattr(bot, "db", None):
        asyncio.create_task(tracker._update_sentry_issue_id(error_code, sentry_event_id))
    await tracker.send_error_log(
        error_code, error_details, is_fatal=isinstance(error, _FATAL_TYPES))
    return error_code


class ErrorTracker(commands.Cog):
    """Error tracking and management system"""

    def __init__(self, bot):
        self.bot = bot
        self.error_cache = deque(maxlen=100)  # Keeps the last 100 errors in memory
        self._ops_last: Dict[str, float] = {}
        self._ops_suppressed: Dict[str, int] = {}
        self._ops_recent: deque = deque(maxlen=32)
        self.error_channel_id = 1392439223717724160
        self.dev_user_id = 1164597199594852395

    def generate_error_code(self, error: Exception, ctx: Optional[commands.Context] = None) -> str:
        """Generates a unique error code"""
        # Opaque random reference: it neither leaks the error nor relies on a
        # cryptographically broken digest.
        return secrets.token_hex(4).upper()

    def store_error(self, error_code: str, error_data: Dict[str, Any]):
        """Stores the error in the memory cache"""
        self.error_cache.append({
            "code": error_code,
            "timestamp": datetime.now(timezone.utc),
            "data": error_data
        })

    async def store_error_db(self, error_code: str, error_data: Dict[str, Any], ctx: Optional[commands.Context] = None):
        """Stores the error in the database"""
        if not self.bot.db:
            return

        try:
            # Prepare data for the DB
            db_data = {
                "type": error_data.get("type"),
                "message": error_data.get("message"),
                "file": error_data.get("file"),
                "line": int(error_data.get("line")) if error_data.get("line", "").isdigit() else None,
                "traceback": error_data.get("traceback"),
                "user_id": error_data.get("user_id"),
                "guild_id": error_data.get("guild_id"),
                "command": error_data.get("command"),
                "context": self._details_context(error_data),
                "sentry_event_id": error_data.get("sentry_event_id"),
                "sentry_issue_id": error_data.get("sentry_issue_id")
            }

            # Add context info if available
            if ctx:
                db_data["user_id"] = ctx.author.id
                db_data["guild_id"] = ctx.guild.id if ctx.guild else None
                db_data["context"] = json.dumps({
                    "channel": str(ctx.channel),
                    "message": ctx.message.content[:200] if hasattr(ctx, 'message') else None
                })

            # Store in the DB
            await self.bot.db.log_error(error_code, db_data)

        except Exception as e:
            import logging
            logger = logging.getLogger('moddy')
            logger.error(f"Error while storing in DB: {e}")

    @staticmethod
    def _details_context(error_data: Dict[str, Any]) -> Optional[str]:
        """JSON stored in the ``context`` column: how the interaction looked + delivery."""
        extra = {}
        if error_data.get("interaction_context"):
            extra.update(error_data["interaction_context"])
        if error_data.get("user_reached"):
            extra["user_reached"] = error_data["user_reached"]
        if not extra:
            return None
        try:
            return json.dumps(extra, default=str)[:2000]
        except (TypeError, ValueError):
            return None

    async def _update_sentry_issue_id(self, error_code: str, sentry_event_id: str):
        """
        Fetch Sentry issue ID from the API and update the database.
        This runs asynchronously in the background.

        Retries up to 3 times with exponential backoff:
        - First attempt: after 5 seconds
        - Second attempt: after 10 seconds (5 + 5)
        - Third attempt: after 15 seconds (5 + 10)
        """
        max_retries = 3
        base_delay = 5  # Start with 5 seconds

        for attempt in range(max_retries):
            try:
                # Wait before attempting (exponential backoff)
                wait_time = base_delay * (attempt + 1)
                await asyncio.sleep(wait_time)

                logger.debug(f"Attempting to fetch Sentry issue ID for {error_code} (attempt {attempt + 1}/{max_retries}, waited {wait_time}s)")

                # Fetch the issue ID from Sentry API
                sentry_issue_id = await fetch_sentry_issue_id(sentry_event_id)

                if sentry_issue_id and self.bot.db:
                    # Success! Update the database with the issue ID
                    await self.bot.db.update_error_sentry_ids(
                        error_code,
                        sentry_issue_id=sentry_issue_id
                    )
                    logger.info(f"✅ Updated error {error_code} with Sentry issue ID: {sentry_issue_id} (attempt {attempt + 1})")
                    return  # Success, exit the retry loop
                elif attempt < max_retries - 1:
                    # No issue ID yet, but we have more retries
                    logger.debug(f"No issue ID yet for {error_code}, will retry...")
                    continue
                else:
                    # Last attempt failed
                    logger.warning(f"Failed to fetch Sentry issue ID for {error_code} after {max_retries} attempts")
                    return

            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Error fetching Sentry issue ID for {error_code} (attempt {attempt + 1}): {e}, retrying...")
                else:
                    logger.error(f"Failed to update Sentry issue ID for error {error_code} after {max_retries} attempts: {e}")
                    return

    async def get_error_channel(self) -> Optional[discord.TextChannel]:
        """Gets the error channel"""
        return self.bot.get_channel(self.error_channel_id)

    def format_error_details(self, error: Exception, ctx: Optional[commands.Context] = None) -> Dict[str, Any]:
        """Formats the error details"""
        tb = traceback.format_exception(type(error), error, error.__traceback__)

        # Find the source file
        source_file = "Unknown"
        line_number = "?"
        for line in tb:
            if "File" in line and "site-packages" not in line:
                parts = line.strip().split('"')
                if len(parts) >= 2:
                    source_file = parts[1].split('/')[-1]
                    line_parts = line.split("line ")
                    if len(line_parts) >= 2:
                        line_number = line_parts[1].split(",")[0]
                    break

        details = {
            "type": type(error).__name__,
            "message": str(error),
            "file": source_file,
            "line": line_number,
            "traceback": ''.join(tb[-3:])  # Last 3 lines of the traceback
        }

        if ctx:
            details.update({
                "command": str(ctx.command) if ctx.command else "None",
                "user": f"{ctx.author} ({ctx.author.id})",
                "guild": f"{ctx.guild.name} ({ctx.guild.id})" if ctx.guild else "DM",
                "channel": f"#{ctx.channel.name}" if hasattr(ctx.channel, 'name') else "DM",
                "message": ctx.message.content[:100] + "..." if len(ctx.message.content) > 100 else ctx.message.content
            })

        return details

    async def send_error_log(self, error_code: str, error_details: Dict[str, Any], is_fatal: bool = False):
        """Sends the error log to the Discord channel"""
        channel = await self.get_error_channel()
        if not channel:
            return

        # Determine the color based on severity
        color = COLORS["error"] if is_fatal else COLORS["warning"]

        embed = discord.Embed(
            title=f"{'Fatal Error' if is_fatal else 'Error'} Detected",
            color=color,
            timestamp=datetime.now(timezone.utc)
        )

        # Header with the error code
        embed.add_field(
            name="Error Code",
            value=f"`{error_code}`",
            inline=True
        )

        embed.add_field(
            name="Type",
            value=f"`{error_details['type']}`",
            inline=True
        )

        embed.add_field(
            name="File",
            value=f"`{error_details['file']}:{error_details['line']}`",
            inline=True
        )

        # Error message
        embed.add_field(
            name="Message",
            value=f"```{error_details['message'][:500]}```",
            inline=False
        )

        # Context if available
        if 'command' in error_details:
            embed.add_field(
                name="Context",
                value=(
                    f"**Command:** `{error_details['command']}`\n"
                    f"**User:** {error_details['user']}\n"
                    f"**Server:** {error_details['guild']}\n"
                    f"**Channel:** {error_details['channel']}"
                ),
                inline=False
            )

            if 'message' in error_details:
                embed.add_field(
                    name="Original Message",
                    value=f"```{error_details['message']}```",
                    inline=False
                )

        # How the user was (or was not) reached + the interaction's health
        if error_details.get('user_reached'):
            ictx = error_details.get('interaction_context') or {}
            lines = [f"**User reached:** `{error_details['user_reached']}`"]
            if ictx:
                lines.append("**Interaction:** " + " ".join(
                    f"`{k}={v}`" for k, v in ictx.items()))
            embed.add_field(name="Delivery", value="\n".join(lines)[:1000], inline=False)

        if error_details.get('stack'):
            embed.add_field(
                name="Stack",
                value=f"```\n{str(error_details['stack'])[-900:]}```",
                inline=False
            )

        # Traceback for fatal errors
        if is_fatal and 'traceback' in error_details:
            embed.add_field(
                name="Traceback",
                value=f"```py\n{error_details['traceback'][:500]}```",
                inline=False
            )

        # Note about the DB
        if self.bot.db:
            embed.set_footer(text="✅ Error saved to the database")
        else:
            embed.set_footer(text="⚠️ Database not connected - Error cached only")

        # Ping for fatal errors
        content = f"<@{self.dev_user_id}> Fatal error detected!" if is_fatal else None

        await channel.send(content=content, embed=embed)

        # Technical log (webhook-based, dedicated error feed)
        tech = getattr(self.bot, "tech_logger", None)
        if tech:
            await tech.log_error(error_code, error_details, is_fatal=is_fatal)

    async def send_ops_notice(
        self,
        *,
        key: str,
        kind: str,
        title: str,
        message: str,
        interval: float = 300.0,
        context: Optional[Dict[str, Any]] = None,
        stack: Optional[str] = None,
    ) -> bool:
        """Throttled operational notice to the error channel (no error code).

        For conditions that hurt users without being an exception: a handler
        that needed the interaction guard, an event-loop stall. At most one
        notice per ``key`` per ``interval`` and six per minute overall; the
        number of suppressed repeats is appended to the next one.
        """
        import time as _time
        now = _time.monotonic()
        last = self._ops_last.get(key)
        if last is not None and now - last < interval:
            self._ops_suppressed[key] = self._ops_suppressed.get(key, 0) + 1
            return False
        while self._ops_recent and now - self._ops_recent[0] > 60.0:
            self._ops_recent.popleft()
        if len(self._ops_recent) >= 6:
            self._ops_suppressed[key] = self._ops_suppressed.get(key, 0) + 1
            return False
        self._ops_last[key] = now
        self._ops_recent.append(now)
        suppressed = self._ops_suppressed.pop(key, 0)
        if suppressed:
            message += f" (+{suppressed} similar since the last notice)"
        details: Dict[str, Any] = {
            "type": kind, "file": title, "line": "-", "message": message,
            "command": "-", "user": "-", "guild": "-", "channel": "-",
        }
        details.update(context or {})
        if stack:
            details["stack"] = stack
        try:
            await self.send_error_log(secrets.token_hex(2).upper(), details, is_fatal=False)
        except Exception as exc:  # noqa: BLE001 - a notice must never raise into its caller
            logger.warning("Ops notice %s failed: %s", key, exc)
            return False
        return True

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        """Handles command errors"""
        # Ignored errors (already handled)
        ignored = (
            commands.CommandNotFound,
            commands.NotOwner,
            commands.CheckFailure,
            commands.DisabledCommand,
            commands.NoPrivateMessage
        )

        if isinstance(error, ignored):
            return

        # Errors with specific handling (no log)
        if isinstance(error, commands.MissingPermissions):
            embed = discord.Embed(
                title="Insufficient Permissions",
                description=f"Missing permissions: `{', '.join(error.missing_permissions)}`",
                color=COLORS["error"]
            )
            await ctx.send(embed=embed)
            return

        if isinstance(error, commands.CommandOnCooldown):
            embed = discord.Embed(
                title="Cooldown Active",
                description=f"Try again in `{error.retry_after:.1f}` seconds",
                color=COLORS["warning"]
            )
            await ctx.send(embed=embed)
            return

        if isinstance(error, commands.MissingRequiredArgument):
            embed = discord.Embed(
                title="Missing Argument",
                description=f"The argument `{error.param.name}` is required",
                color=COLORS["error"]
            )
            await ctx.send(embed=embed)
            return

        # For all other errors, we log
        error_code = self.generate_error_code(error, ctx)
        error_details = self.format_error_details(error.original if hasattr(error, 'original') else error, ctx)

        # Capture to Sentry and get event ID
        actual_error = error.original if hasattr(error, 'original') else error
        sentry_event_id = capture_error_to_sentry(actual_error, {
            'error_type': 'Command Error',
            'error_code': error_code,
            'command': str(ctx.command) if ctx.command else 'None',
            'user_id': ctx.author.id if ctx.author else None,
            'guild_id': ctx.guild.id if ctx.guild else None,
            'channel_id': ctx.channel.id if ctx.channel else None,
        })

        # Add Sentry event ID to error details
        if sentry_event_id:
            error_details['sentry_event_id'] = sentry_event_id

        # Store the error in memory
        self.store_error(error_code, error_details)

        # Store in the DB if available
        await self.store_error_db(error_code, error_details, ctx)

        # Fetch Sentry issue ID asynchronously (don't wait for it)
        if sentry_event_id and self.bot.db:
            asyncio.create_task(self._update_sentry_issue_id(error_code, sentry_event_id))

        # Determine if it's fatal
        is_fatal = isinstance(error.original if hasattr(error, 'original') else error, (
            RuntimeError,
            AttributeError,
            ImportError,
            MemoryError,
            SystemError
        ))

        # Log to Discord
        await self.send_error_log(error_code, error_details, is_fatal)

        # Create error view with Components V2
        interaction = getattr(ctx, 'interaction', None)
        locale = locale_for(interaction) if interaction else "en-US"
        error_view = ErrorView(error_code, locale)

        async def _dm_fallback() -> str:
            return await dm_error_fallback(self.bot, ctx.author, error_code, locale)

        try:
            if interaction:
                # Slash / hybrid command: every path that can still reach the user.
                await deliver_view(interaction, error_view, dm_fallback=_dm_fallback)
            else:
                # Text command: reply in the channel, DM (notification system) if that fails.
                try:
                    await ctx.send(view=error_view)
                except discord.HTTPException as send_error:
                    logger.warning("Could not reply with the error card in the channel: %s", send_error)
                    await _dm_fallback()
        except Exception as send_error:
            logger.error(f"Failed to send error message to user: {send_error}")

    @commands.Cog.listener()
    async def on_error(self, event: str, *args, **kwargs):
        """Handles event errors (non-commands)"""
        # Get the actual exception from sys.exc_info()
        exc_type, exc_value, exc_traceback = sys.exc_info()

        if exc_value is None:
            return

        error_code = self.generate_error_code(exc_value)
        error_details = self.format_error_details(exc_value)

        error_details.update({
            "event": event,
            "context": f"Discord event: {event}"
        })

        # Capture to Sentry and get event ID
        sentry_event_id = capture_error_to_sentry(exc_value, {
            'error_type': 'Event Error',
            'error_code': error_code,
            'event': event,
        })

        # Add Sentry event ID to error details
        if sentry_event_id:
            error_details['sentry_event_id'] = sentry_event_id

        self.store_error(error_code, error_details)

        # Store in the DB if available
        if self.bot.db:
            await self.store_error_db(error_code, error_details)

        # Fetch Sentry issue ID asynchronously (don't wait for it)
        if sentry_event_id and self.bot.db:
            asyncio.create_task(self._update_sentry_issue_id(error_code, sentry_event_id))

        await self.send_error_log(error_code, error_details, is_fatal=True)

    async def _reply_card(self, interaction: discord.Interaction, view: ui.LayoutView) -> None:
        """Show a (non-error) card to the user through the delivery chain, no DM."""
        reached = await deliver_view(interaction, view)
        if reached == "none":
            logger.warning("Could not show a card to user %s on %s",
                           getattr(interaction.user, "id", "?"), interaction.id)

    @commands.Cog.listener()
    async def on_app_command_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        """Handles slash command (app command) errors"""
        # Check if the error was already handled
        if hasattr(error, '__error_handled__'):
            return

        # Mark error as handled to prevent duplicate processing
        error.__error_handled__ = True

        # A command Discord still lists but the bot no longer has: tell the user
        # instead of leaving "The application did not respond".
        if isinstance(error, discord.app_commands.CommandNotFound):
            logger.warning("Unknown application command invoked: %s",
                           (interaction.data or {}).get("name"))
            await self._reply_card(
                interaction, InfoNoticeView("errors.command_unavailable", locale_for(interaction)))
            return

        # Errors with specific handling
        if isinstance(error, discord.app_commands.MissingPermissions):
            # Create inline view for permissions error
            class PermissionErrorView(ui.LayoutView):
                def __init__(self):
                    super().__init__(timeout=None)
                    container = ui.Container()
                    container.add_item(
                        ui.TextDisplay(f"### <:error:1519790252594827264> Insufficient Permissions")
                    )
                    container.add_item(
                        ui.TextDisplay("You don't have the necessary permissions to execute this command.")
                    )
                    button_row = ui.ActionRow()
                    support_btn = ui.Button(
                        label="Support Server",
                        style=discord.ButtonStyle.link,
                        url="https://moddy.app/support"
                    )
                    button_row.add_item(support_btn)
                    container.add_item(button_row)
                    self.add_item(container)

            await self._reply_card(interaction, PermissionErrorView())
            return

        if isinstance(error, discord.app_commands.CommandOnCooldown):
            # Create inline view for cooldown error
            class CooldownErrorView(ui.LayoutView):
                def __init__(self, retry_after: float):
                    super().__init__(timeout=None)
                    container = ui.Container()
                    container.add_item(
                        ui.TextDisplay(f"### ⏱️ Cooldown Active")
                    )
                    container.add_item(
                        ui.TextDisplay(f"Please try again in `{retry_after:.1f}` seconds.")
                    )
                    self.add_item(container)

            await self._reply_card(interaction, CooldownErrorView(error.retry_after))
            return

        # Argument that couldn't be resolved (e.g. a username typed where a
        # Member/User is expected) — a user mistake, not a bug. Show a friendly
        # message instead of an error code.
        if isinstance(error, discord.app_commands.TransformerError):
            class NotFoundView(ui.LayoutView):
                def __init__(self):
                    super().__init__(timeout=None)
                    container = ui.Container()
                    container.add_item(ui.TextDisplay(
                        f"### <:warning:1519789903100121139> Couldn't find that"))
                    container.add_item(ui.TextDisplay(
                        f"`{str(error.value)[:80]}` doesn't match a member or user I can see. "
                        f"Try mentioning them or using their ID."))
                    self.add_item(container)

            await self._reply_card(interaction, NotFoundView())
            return

        # For all other errors: the full path (code, Sentry, DB, error channel,
        # card for the user with DM fallback).
        actual_error = error.original if hasattr(error, 'original') else error
        await report_interaction_error(
            interaction, actual_error,
            command=interaction.command.name if interaction.command else "Unknown",
            error_type="App Command Error",
            log_label="App Command Error",
        )


async def report_component_error(
    interaction: "discord.Interaction", error: Exception, source: str
) -> None:
    """Route an unexpected error from a standalone component to the central handler.

    ``discord.ui.DynamicItem`` callbacks that are dispatched without a live
    ``BaseView`` (persistent buttons registered via ``add_dynamic_items``) never
    reach :meth:`BaseView.on_error`. Wrap such callbacks and call this so an
    unknown error still gets an error code, a Sentry capture, a Discord log and
    a user-facing ``ErrorView`` - exactly like every other UI error.
    """
    await report_interaction_error(
        interaction, error,
        command=f"Component:{source}",
        error_type="Component Error",
        log_label=f"Component error in {source}",
        sentry_extra={"source": source},
    )


async def setup(bot):
    await bot.add_cog(ErrorTracker(bot))
