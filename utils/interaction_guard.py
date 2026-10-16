"""
Interaction guard: Discord must never show "This interaction failed" for Moddy.

Discord invalidates an interaction token that has not received an initial
response within 3 seconds (``10062 Unknown interaction``); after an initial
response (a defer counts) the token lives 15 minutes. This module is the global
safety net on top of the per-feature ``defer()`` calls:

* every component click, modal submit and application command gets a timer
  armed the moment discord.py builds the ``Interaction`` (patch of
  ``Interaction.__init__`` - the earliest hook, it runs before the view store
  and the command tree see the interaction);
* the deadline is ``DEADLINE_SECONDS`` after the interaction's *snowflake*
  timestamp, so gateway delay and event-loop lag already consumed are counted;
* if nothing has responded by then, the guard acknowledges it itself:

  ====================  ===================================================
  component             ``defer()`` (deferred update, invisible)
  modal submit          ``defer()`` (update when it came from a component
                        message, else ``thinking=True, ephemeral=True``)
  application command   ``defer(thinking=True, ephemeral=True)`` - never
                        risk publishing ephemeral content publicly
  autocomplete          skipped (no defer exists)
  ====================  ===================================================

  A component / modal that **no handler claimed** (dead non-persistent view,
  stopped view, unknown modal) is answered with an ephemeral "no longer
  active" card instead of a silent defer, so the user is told something.

Feature code keeps working unchanged once the guard has deferred: the
``InteractionResponse`` methods are wrapped so that

* ``send_message``  -> ``followup.send`` (same kwargs, ephemeral respected),
* ``edit_message``  -> ``edit_original_response``,
* ``defer``         -> no-op,
* ``send_modal``    -> raises :class:`InteractionTooSlow` (a modal can only be
  an *initial* response; the error handler tells the user to retry).

The wrappers also cover the race where the guard and the feature answer at the
same moment (Discord answers ``40060`` to the loser): whoever loses degrades
gracefully, nothing is double-answered.

If the guard itself gets ``10062`` the interaction was already dead (loop
stall): it goes through the full error path with the DM fallback
(``cogs.error_handler.report_interaction_error``).

Every guard activation is user-impacting latency, so it is logged as a WARNING
naming the slow handler and reported (throttled) to the error channel.

``install()`` is idempotent and patches discord.py once, in the style of
``utils/icon_theme.py``; ``tests/test_interaction_guard.py`` fails loudly when a
discord.py upgrade moves a patched attribute. See docs/guides/ERROR_HANDLING.md.
"""
from __future__ import annotations

import asyncio
import collections
import functools
import inspect
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

import discord

log = logging.getLogger("moddy.interaction_guard")

DEADLINE_SECONDS = 2.0            # ack this long after Discord created the interaction
MAX_SKEW_SECONDS = 3.0            # cap of the local-clock skew correction
STUCK_THINKING_SECONDS = 120.0    # a guard-deferred "thinking" still empty after this
NOTICE_INTERVAL = 600.0           # ops notice per handler
TOKEN_LIFETIME = 15 * 60.0

GUARD_KEY = "moddy_guard"             # interaction.extras key -> GuardState
DISPATCHED_KEY = "moddy_dispatched"   # interaction.extras key -> a handler claimed it

_GUARDED_TYPES = (
    discord.InteractionType.application_command,
    discord.InteractionType.component,
    discord.InteractionType.modal_submit,
)

PATCH_MARK = "__moddy_guard_patched__"


class InteractionTooSlow(discord.ClientException):
    """The guard already acknowledged this interaction, so a modal cannot open.

    Raised by ``InteractionResponse.send_modal`` after an auto-defer. The error
    handler turns it into a localized "the bot was too slow, please retry".
    """

    def __init__(self, interaction: Optional[discord.Interaction] = None):
        self.interaction = interaction
        super().__init__(
            "The interaction was auto-acknowledged because the handler took too long; "
            "a modal can only be the first response."
        )


@dataclass
class GuardState:
    """What the guard did to one interaction (stored in ``interaction.extras``)."""

    status: str = "deferring"   # deferring | deferred | answered | lost | dead | failed
    mode: str = "update"        # update (component defer) | thinking (command / bare modal)
    handler: str = "?"
    fired_age: float = 0.0
    force_ephemeral: bool = False
    ready: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def took_over(self) -> bool:
        """The guard owns the initial response: feature calls must be redirected."""
        return self.status in ("deferred", "answered")

    @property
    def attempted(self) -> bool:
        return self.status in ("deferring", "deferred", "answered")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_ages: Deque[float] = collections.deque(maxlen=64)
_tasks: set = set()
_by_token: "collections.OrderedDict[str, Tuple[float, GuardState]]" = collections.OrderedDict()
_stats = {"fired": 0, "dead": 0, "lost": 0, "unhandled": 0}
_ORIG: Dict[str, Any] = {}
_installed = False


def stats() -> Dict[str, int]:
    return dict(_stats)


def get_state(interaction) -> Optional[GuardState]:
    try:
        return interaction.extras.get(GUARD_KEY)
    except Exception:  # noqa: BLE001 - a stub without extras is simply "not guarded"
        return None


def _skew() -> float:
    """Local clock ahead of Discord's, estimated from the youngest recent ages.

    Interaction ages seen at receipt are never below the network latency
    (>= ~0.1 s), so a persistently larger minimum means our clock is ahead.
    Without this a skewed host would defer *every* interaction immediately.
    """
    if len(_ages) < 8:
        return 0.0
    return min(MAX_SKEW_SECONDS, max(0.0, min(_ages) - 0.15))


def interaction_age(interaction) -> float:
    return (discord.utils.utcnow() - interaction.created_at).total_seconds()


def describe(interaction) -> str:
    """Name of what is running: command name or component/modal custom_id."""
    try:
        if interaction.type is discord.InteractionType.application_command:
            command = interaction.command
            if command is not None:
                return f"/{command.qualified_name}"
            return f"/{(interaction.data or {}).get('name', '?')}"
        return str((interaction.data or {}).get("custom_id", "?"))
    except Exception:  # noqa: BLE001
        return "?"


def mark_dispatched(interaction) -> None:
    try:
        interaction.extras[DISPATCHED_KEY] = True
    except Exception:  # noqa: BLE001
        pass


def _was_dispatched(interaction) -> bool:
    try:
        return bool(interaction.extras.get(DISPATCHED_KEY))
    except Exception:  # noqa: BLE001
        return True


def _remember_token(interaction, state: GuardState) -> None:
    now = time.monotonic()
    _by_token[interaction.token] = (now, state)
    while _by_token:
        token, (ts, _) = next(iter(_by_token.items()))
        if now - ts > TOKEN_LIFETIME + 60 or len(_by_token) > 4000:
            _by_token.pop(token, None)
        else:
            break


# --------------------------------------------------------------------------- #
# Scheduling (Interaction.__init__)
# --------------------------------------------------------------------------- #

def schedule(interaction) -> Optional[asyncio.TimerHandle]:
    """Arm the deadline timer for one interaction. Never raises."""
    try:
        if interaction.type not in _GUARDED_TYPES:
            return None
        age = interaction_age(interaction)
        _ages.append(age)
        delay = DEADLINE_SECONDS - (age - _skew())
        delay = min(DEADLINE_SECONDS, max(0.0, delay))
        return asyncio.get_running_loop().call_later(delay, _on_deadline, interaction)
    except Exception:  # noqa: BLE001 - e.g. no running loop in a unit test
        log.debug("could not arm the interaction guard", exc_info=True)
        return None


def _on_deadline(interaction) -> None:
    try:
        if interaction.response.is_done() or get_state(interaction) is not None:
            return
        state = GuardState(handler=describe(interaction))
        interaction.extras[GUARD_KEY] = state
        task = asyncio.get_running_loop().create_task(
            _acknowledge(interaction, state), name=f"interaction-guard-{interaction.id}")
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except Exception:  # noqa: BLE001
        log.error("interaction guard deadline callback failed", exc_info=True)


async def _acknowledge(interaction, state: GuardState) -> None:
    state.fired_age = interaction_age(interaction)
    _stats["fired"] += 1
    kind = interaction.type
    try:
        unhandled = (kind is not discord.InteractionType.application_command
                     and not _was_dispatched(interaction))
        for attempt in (1, 2):
            try:
                if unhandled:
                    await _send_inactive_notice(interaction)
                    state.status = "answered"
                    _stats["unhandled"] += 1
                else:
                    state.mode = await _defer(interaction)
                    state.status = "deferred"
                    _remember_token(interaction, state)
                    if state.mode == "thinking":
                        asyncio.get_running_loop().call_later(
                            STUCK_THINKING_SECONDS, _spawn_stuck_check, interaction)
                break
            except discord.InteractionResponded:
                state.status = "lost"
                _stats["lost"] += 1
                break
            except discord.NotFound as exc:
                if getattr(exc, "code", None) == 10062:
                    state.status = "dead"
                    _stats["dead"] += 1
                    await _report_dead(interaction, state, exc)
                else:
                    state.status = "failed"
                    log.warning("Interaction guard: NotFound %s on %s", exc, state.handler)
                break
            except discord.HTTPException as exc:
                if exc.code == 40060:       # someone answered first: nothing to do
                    state.status = "lost"
                    _stats["lost"] += 1
                    break
                if attempt == 1 and exc.status >= 500 and interaction_age(interaction) < 2.7:
                    await asyncio.sleep(0.2)
                    continue
                state.status = "failed"
                log.warning("Interaction guard could not acknowledge %s: %s", state.handler, exc)
                break
        else:  # pragma: no cover - loop always breaks
            pass
        if state.status in ("deferred", "answered"):
            await _announce(interaction, state)
    except Exception:  # noqa: BLE001 - a guard failure must never become a new failure
        state.status = "failed"
        log.error("Interaction guard failed on %s", state.handler, exc_info=True)
    finally:
        state.ready.set()


async def _defer(interaction) -> str:
    """Acknowledge with the type-appropriate defer. Returns the guard mode."""
    response = interaction.response
    defer = _ORIG.get("defer") or discord.InteractionResponse.defer
    if interaction.type is discord.InteractionType.application_command:
        await defer(response, ephemeral=True, thinking=True)
        return "thinking"
    if interaction.type is discord.InteractionType.modal_submit and interaction.message is None:
        await defer(response, ephemeral=True, thinking=True)
        return "thinking"
    await defer(response)
    return "update"


async def _send_inactive_notice(interaction) -> None:
    from cogs.error_handler import InfoNoticeView, locale_for
    view = InfoNoticeView("errors.inactive", locale=locale_for(interaction))
    send = _ORIG.get("send_message") or discord.InteractionResponse.send_message
    await send(interaction.response, view=view, ephemeral=True)


async def _announce(interaction, state: GuardState) -> None:
    """Warn + (throttled) tell the owner: this handler answered too slowly."""
    lag = ""
    try:
        from utils.loop_monitor import last_lag_summary
        lag = last_lag_summary()
    except Exception:  # noqa: BLE001
        pass
    what = "no handler claimed it" if state.status == "answered" else "handler did not respond in time"
    log.warning(
        "Interaction guard auto-acknowledged %s (%s, type=%s): %s; age_at_deadline=%.2fs "
        "user=%s guild=%s %s",
        state.handler, state.mode, getattr(interaction.type, "name", interaction.type), what,
        state.fired_age, getattr(interaction.user, "id", "?"), interaction.guild_id, lag)
    try:
        bot = interaction.client
        tracker = bot.get_cog("ErrorTracker") if bot else None
        if tracker is not None:
            await tracker.send_ops_notice(
                key=f"guard:{state.handler}", interval=NOTICE_INTERVAL,
                kind="InteractionGuard",
                title="Interaction auto-acknowledged",
                message=(f"{state.handler} did not respond within the deadline "
                         f"(age {state.fired_age:.2f}s, {what}). {lag}"),
                context={"command": state.handler,
                         "user": str(getattr(interaction.user, "id", "?")),
                         "guild": str(interaction.guild_id)})
    except Exception:  # noqa: BLE001
        log.debug("guard ops notice failed", exc_info=True)


async def _report_dead(interaction, state: GuardState, exc: BaseException) -> None:
    """The guard itself got 10062: the loop stalled. Full error path + DM fallback."""
    log.error("Interaction guard got 10062 on %s (age %.2fs): the event loop is stalling",
              state.handler, interaction_age(interaction))
    from cogs.error_handler import report_interaction_error
    await report_interaction_error(
        interaction, exc, command=f"InteractionGuard:{state.handler}",
        error_type="Interaction Guard (expired before ack)")


def _spawn_stuck_check(interaction) -> None:
    try:
        task = asyncio.get_running_loop().create_task(_stuck_check(interaction))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except Exception:  # noqa: BLE001
        log.debug("could not schedule the stuck-thinking check", exc_info=True)


async def _stuck_check(interaction) -> None:
    """A guard-deferred 'thinking' that is still empty would spin for 15 min."""
    try:
        message = await interaction.original_response()
        flags = getattr(message, "flags", None)
        if not getattr(flags, "loading", False):
            return
        from cogs.error_handler import InfoNoticeView, locale_for
        await interaction.edit_original_response(
            content=None, view=InfoNoticeView("errors.still_working", locale=locale_for(interaction)))
        log.warning("Interaction guard: %s was still 'thinking' after %.0fs, notified the user",
                    describe(interaction), STUCK_THINKING_SECONDS)
    except discord.HTTPException as exc:
        log.debug("stuck-thinking check skipped: %s", exc)
    except Exception:  # noqa: BLE001
        log.debug("stuck-thinking check failed", exc_info=True)


# --------------------------------------------------------------------------- #
# InteractionResponse shims
# --------------------------------------------------------------------------- #

async def _settle(state: Optional[GuardState]) -> None:
    """If the guard is mid-flight, wait for it so we know who owns the response."""
    if state is not None and state.status == "deferring":
        try:
            await asyncio.wait_for(state.ready.wait(), 6.0)
        except asyncio.TimeoutError:
            log.warning("interaction guard did not settle in time for %s", state.handler)


def _is_already_acknowledged(exc: BaseException) -> bool:
    return isinstance(exc, discord.InteractionResponded) or (
        isinstance(exc, discord.HTTPException) and exc.code == 40060)


_EDIT_PARAMS = None


def _edit_original_params() -> set:
    global _EDIT_PARAMS
    if _EDIT_PARAMS is None:
        _EDIT_PARAMS = set(inspect.signature(discord.Interaction.edit_original_response).parameters)
    return _EDIT_PARAMS


async def _redirect_send(interaction, args, kwargs):
    kwargs = dict(kwargs)
    kwargs["wait"] = True
    # Webhook.send has no delete_after (InteractionResponse.send_message does).
    delete_after = kwargs.pop("delete_after", None)
    message = await interaction.followup.send(*args, **kwargs)
    if delete_after is not None and message is not None:
        async def _later():
            await asyncio.sleep(delete_after)
            try:
                await message.delete()
            except discord.HTTPException:
                pass
        task = asyncio.get_running_loop().create_task(_later())
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    return message


async def _redirect_edit(interaction, kwargs):
    kwargs = dict(kwargs)
    delete_after = kwargs.pop("delete_after", None)
    allowed = _edit_original_params()
    kwargs = {k: v for k, v in kwargs.items() if k in allowed}
    message = await interaction.edit_original_response(**kwargs)
    if delete_after is not None:
        async def _later():
            await asyncio.sleep(delete_after)
            try:
                await interaction.delete_original_response()
            except discord.HTTPException:
                pass
        task = asyncio.get_running_loop().create_task(_later())
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    return message


def _wrap_send_message(original):
    @functools.wraps(original)
    async def send_message(self, *args, **kwargs):
        interaction = self._parent
        state = get_state(interaction)
        await _settle(state)
        if state is not None and state.took_over:
            return await _redirect_send(interaction, args, kwargs)
        try:
            return await original(self, *args, **kwargs)
        except (discord.InteractionResponded, discord.HTTPException) as exc:
            if _is_already_acknowledged(exc):
                state = get_state(interaction)
                await _settle(state)
                if state is not None and state.took_over:
                    return await _redirect_send(interaction, args, kwargs)
            raise
    setattr(send_message, PATCH_MARK, True)
    return send_message


def _wrap_edit_message(original):
    @functools.wraps(original)
    async def edit_message(self, **kwargs):
        interaction = self._parent
        state = get_state(interaction)
        await _settle(state)
        if state is not None and state.took_over:
            return await _redirect_edit(interaction, kwargs)
        try:
            return await original(self, **kwargs)
        except (discord.InteractionResponded, discord.HTTPException) as exc:
            if _is_already_acknowledged(exc):
                state = get_state(interaction)
                await _settle(state)
                if state is not None and state.took_over:
                    return await _redirect_edit(interaction, kwargs)
            raise
    setattr(edit_message, PATCH_MARK, True)
    return edit_message


def _wrap_defer(original):
    @functools.wraps(original)
    async def defer(self, *, ephemeral: bool = False, thinking: bool = False):
        interaction = self._parent
        state = get_state(interaction)
        await _settle(state)
        if state is not None and state.took_over:
            if ephemeral and thinking and state.mode == "update":
                # The feature wanted an ephemeral "thinking" reply; the guard's
                # invisible defer cannot be that, so make its first followup
                # ephemeral instead of publishing it.
                state.force_ephemeral = True
            return None
        try:
            return await original(self, ephemeral=ephemeral, thinking=thinking)
        except (discord.InteractionResponded, discord.HTTPException) as exc:
            if _is_already_acknowledged(exc):
                state = get_state(interaction)
                await _settle(state)
                if state is not None and state.took_over:
                    return None
            raise
    setattr(defer, PATCH_MARK, True)
    return defer


def _wrap_send_modal(original):
    @functools.wraps(original)
    async def send_modal(self, modal, /):
        interaction = self._parent
        state = get_state(interaction)
        await _settle(state)
        if state is not None and state.took_over:
            raise InteractionTooSlow(interaction)
        try:
            return await original(self, modal)
        except (discord.InteractionResponded, discord.HTTPException) as exc:
            if _is_already_acknowledged(exc):
                state = get_state(interaction)
                await _settle(state)
                if state is not None and state.took_over:
                    raise InteractionTooSlow(interaction) from exc
            raise
    setattr(send_modal, PATCH_MARK, True)
    return send_modal


def _wrap_webhook_send(original):
    """First followup after a feature-requested ephemeral 'thinking': keep it private."""
    @functools.wraps(original)
    async def send(self, *args, **kwargs):
        try:
            entry = _by_token.get(getattr(self, "token", None))
            state = entry[1] if entry else None
            if state is not None and state.force_ephemeral:
                state.force_ephemeral = False
                if not kwargs.get("ephemeral"):
                    kwargs["ephemeral"] = True
        except Exception:  # noqa: BLE001
            log.debug("followup ephemeral guard failed", exc_info=True)
        return await original(self, *args, **kwargs)
    setattr(send, PATCH_MARK, True)
    return send


# --------------------------------------------------------------------------- #
# Dispatch markers (did any handler claim this interaction?)
# --------------------------------------------------------------------------- #

def _wrap_dispatch_item(original):
    @functools.wraps(original)
    def _dispatch_item(self, item, interaction):
        task = original(self, item, interaction)
        if task is not None:
            mark_dispatched(interaction)
        return task
    setattr(_dispatch_item, PATCH_MARK, True)
    return _dispatch_item


def _wrap_dynamic_call(original):
    @functools.wraps(original)
    async def schedule_dynamic_item_call(self, component_type, factory, interaction, custom_id, match):
        mark_dispatched(interaction)
        return await original(self, component_type, factory, interaction, custom_id, match)
    setattr(schedule_dynamic_item_call, PATCH_MARK, True)
    return schedule_dynamic_item_call


def _wrap_dispatch_submit(original):
    @functools.wraps(original)
    def _dispatch_submit(self, interaction, *args, **kwargs):
        mark_dispatched(interaction)
        return original(self, interaction, *args, **kwargs)
    setattr(_dispatch_submit, PATCH_MARK, True)
    return _dispatch_submit


def _wrap_init(original):
    @functools.wraps(original)
    def __init__(self, *args, **kwargs):
        original(self, *args, **kwargs)
        schedule(self)
    setattr(__init__, PATCH_MARK, True)
    return __init__


# --------------------------------------------------------------------------- #
# Install
# --------------------------------------------------------------------------- #

# (module, class, attribute, wrapper factory). A discord.py upgrade that moves
# one of them must fail tests/test_interaction_guard.py loudly.
PATCH_TARGETS: List[Tuple[str, str, str, Any]] = [
    ("discord.interactions", "Interaction", "__init__", _wrap_init),
    ("discord.interactions", "InteractionResponse", "send_message", _wrap_send_message),
    ("discord.interactions", "InteractionResponse", "edit_message", _wrap_edit_message),
    ("discord.interactions", "InteractionResponse", "defer", _wrap_defer),
    ("discord.interactions", "InteractionResponse", "send_modal", _wrap_send_modal),
    ("discord.webhook.async_", "Webhook", "send", _wrap_webhook_send),
    ("discord.ui.view", "BaseView", "_dispatch_item", _wrap_dispatch_item),
    ("discord.ui.view", "ViewStore", "schedule_dynamic_item_call", _wrap_dynamic_call),
    ("discord.ui.modal", "Modal", "_dispatch_submit", _wrap_dispatch_submit),
]


def install() -> None:
    """Patch discord.py once. Safe to call repeatedly; never raises.

    Call it after ``utils.icon_theme.install()`` so the guard wraps the
    theme-aware methods (either order works, both keep their marker).
    """
    global _installed
    if _installed:
        return
    try:
        import importlib
        patched = []
        for mod_name, cls_name, attr, factory in PATCH_TARGETS:
            cls = getattr(importlib.import_module(mod_name), cls_name)
            current = cls.__dict__[attr]
            if getattr(current, PATCH_MARK, False):
                continue
            _ORIG[f"{cls_name}.{attr}"] = current
            setattr(cls, attr, factory(current))
            patched.append((cls, attr))
        _ORIG["defer"] = _ORIG.get("InteractionResponse.defer", discord.InteractionResponse.defer)
        _ORIG["send_message"] = _ORIG.get(
            "InteractionResponse.send_message", discord.InteractionResponse.send_message)
        _installed = True
        log.info("Interaction guard installed (deadline %.1fs)", DEADLINE_SECONDS)
    except Exception:
        log.error("Interaction guard install failed; interactions are NOT auto-acknowledged",
                  exc_info=True)
