# Error Handling Guide

## Table of Contents
1. [Overview](#overview)
2. [Error Handler System](#error-handler-system)
3. [How to Use BaseView and BaseModal](#how-to-use-baseview-and-basemodal)
4. [Best Practices](#best-practices)
5. [Logging](#logging)
6. [Troubleshooting](#troubleshooting)
7. [Interaction guard and delivery chain](#interaction-guard-and-delivery-chain)
8. [Expected errors](#expected-errors)

---

## Overview

**CRITICAL**: All errors in Moddy MUST pass through the centralized error handler (`cogs/error_handler.py`).

The error handling system ensures:
- ✅ **ALL errors are logged** with full traceback in a single line
- ✅ **ALL errors are stored** in the database
- ✅ **ALL errors are sent** to the Discord error logging channel
- ✅ **ALL users ALWAYS receive** an error card with an error code (localized, by DM when the interaction is dead)
- ✅ **Expired interactions (10062 / 10008 / 10015) are NOT exempt**: they take the full path too
- ✅ **NO errors escape** without proper handling

---

## Error Handler System

### What Errors Are Handled?

The error handler captures ALL types of errors:

1. **Slash Commands (app_commands)** → `on_app_command_error`
2. **Text Commands** → `on_command_error`
3. **Discord Events** → `on_error`
4. **Discord.ui Views** → `BaseView.on_error`
5. **Discord.ui Modals** → `BaseModal.on_error`

### Error Flow

```
Error Occurs
    ↓
Caught by appropriate handler (on_app_command_error, BaseView.on_error, etc.)
    ↓
Full traceback logged in ONE single line (not fragmented)
    ↓
Sentry capture
    ↓
User receives the error card (delivery chain: response → followup →
edit original → DM fallback), outcome recorded as `user_reached`
    ↓
Error stored in database + sent to the Discord error logging channel
```

All interaction-bound handlers (`BaseView.on_error`, `BaseModal.on_error`, the
app-command handler, `report_component_error`, the token detector) call the
same function, `cogs.error_handler.report_interaction_error`.

---

## How to Use BaseView and BaseModal

### **RULE**: All UI components MUST inherit from BaseView or BaseModal

### For LayoutViews

**❌ WRONG:**
```python
from discord import ui

class MyConfigView(ui.LayoutView):
    def __init__(self, bot, ...):
        super().__init__(timeout=300)
        self.bot = bot
        # ...
```

**✅ CORRECT:**
```python
from discord import ui
from cogs.error_handler import BaseView

class MyConfigView(BaseView):
    def __init__(self, bot, ...):
        super().__init__(timeout=300)
        self.bot = bot  # MUST set self.bot for error tracking
        # ...
```

### For Modals

**❌ WRONG:**
```python
from discord import ui

class MyModal(ui.Modal, title="My Modal"):
    def __init__(self, locale: str, callback_func):
        super().__init__(timeout=300)
        # ...
```

**✅ CORRECT:**
```python
from discord import ui
from cogs.error_handler import BaseModal

class MyModal(BaseModal, title="My Modal"):
    def __init__(self, locale: str, callback_func):
        super().__init__(timeout=300)
        # Note: self.bot will be set by the View that creates this Modal
        # ...
```

### Setting `self.bot` for Modals

When creating a Modal from a View, you MUST set `modal.bot`:

**❌ WRONG:**
```python
async def on_edit_button(self, interaction: discord.Interaction):
    modal = MyModal(self.locale, self.callback)
    await interaction.response.send_modal(modal)
```

**✅ CORRECT:**
```python
async def on_edit_button(self, interaction: discord.Interaction):
    modal = MyModal(self.locale, self.callback)
    modal.bot = self.bot  # Set bot for error handling
    await interaction.response.send_modal(modal)
```

### What if I don't have `self.bot`?

**Don't worry!** `BaseView` and `BaseModal` will automatically use `interaction.client` as a fallback.

However, it's **strongly recommended** to always set `self.bot` when possible for consistency.

---

## Best Practices

### 1. Always Inherit from Base Classes

**ALL** discord.ui components MUST inherit from:
- `BaseView` for `ui.LayoutView`
- `BaseModal` for `ui.Modal`

### 2. Set `self.bot` in Views

```python
class MyView(BaseView):
    def __init__(self, bot, ...):
        super().__init__(timeout=300)
        self.bot = bot  # ✅ Required
```

### 3. Set `modal.bot` Before Sending

```python
modal = MyModal(...)
modal.bot = self.bot  # ✅ Required
await interaction.response.send_modal(modal)
```

### 4. Don't Catch Exceptions in UI Callbacks

**❌ WRONG:**
```python
async def on_button_click(self, interaction: discord.Interaction):
    try:
        # Do something that might fail
        result = await risky_operation()
    except Exception as e:
        # This prevents the error handler from seeing the error!
        await interaction.response.send_message(f"Error: {e}")
```

**✅ CORRECT:**
```python
async def on_button_click(self, interaction: discord.Interaction):
    # Let errors propagate - BaseView.on_error will handle them
    result = await risky_operation()
    await interaction.response.send_message(f"Success: {result}")
```

**Exception**: Only catch exceptions if you can **fully recover** from them:

```python
async def on_button_click(self, interaction: discord.Interaction):
    try:
        result = await fetch_data()
    except aiohttp.ClientError:
        # Recoverable - use cached data instead
        result = get_cached_data()

    await interaction.response.send_message(f"Result: {result}")
```

### 5. Use `logger.error()` with `exc_info=True` for Manual Logging

If you need to manually log an exception:

```python
import logging
logger = logging.getLogger('moddy.my_module')

try:
    dangerous_operation()
except Exception as e:
    # This will log the full traceback in ONE line
    logger.error(f"Failed to do operation: {e}", exc_info=True)
    raise  # Re-raise to let error handler process it
```

---

## Logging

### Compact Traceback Format

All tracebacks are logged in **ONE SINGLE LINE** using the `⮐` separator:

**Before (BAD - multiple lines):**
```
2025-12-04 22:52:33 - discord.ui.view - ERROR - Ignoring exception in view
Traceback (most recent call last):
  File "/app/cogs/config.py", line 124, in on_module_select
    from modules.configs.welcome_dm_config import WelcomeDmConfigView
ModuleNotFoundError: No module named 'modules.configs.welcome_config'
```

**After (GOOD - single line):**
```
2025-12-04 22:52:33 - moddy.error_handler - ERROR - UI Error in ConfigMainView - Item: Select - Traceback (most recent call last): ⮐   File "/app/cogs/config.py", line 124, in on_module_select ⮐     from modules.configs.welcome_dm_config import WelcomeDmConfigView ⮐ ModuleNotFoundError: No module named 'modules.configs.welcome_config' ⮐
```

### Logging Best Practices

1. **Use the module-specific logger:**
   ```python
   logger = logging.getLogger('moddy.my_module')
   ```

2. **For exceptions, use `exc_info=True`:**
   ```python
   logger.error("Operation failed", exc_info=True)
   ```

3. **For debugging, be descriptive:**
   ```python
   logger.debug(f"Processing user {user.id} in guild {guild.id}")
   ```

---

## Troubleshooting

### "Error handler didn't show an embed to the user"

**Possible causes:**
1. ❌ Your View doesn't inherit from `BaseView`
2. ❌ Your Modal doesn't inherit from `BaseModal`
3. ❌ You caught the exception with `try/except` without re-raising

**Solution:** Follow the [Best Practices](#best-practices) section.

### "Traceback is still fragmented across multiple lines"

**Possible cause:**
- You're not using `logger.error(..., exc_info=True)`

**Solution:**
```python
logger.error("My error message", exc_info=True)
```

### "Modal errors are not being handled"

**Possible causes:**
1. ❌ Modal doesn't inherit from `BaseModal`
2. ❌ `modal.bot` wasn't set before sending

**Solution:**
```python
modal = MyModal(...)
modal.bot = self.bot  # ← Add this line
await interaction.response.send_modal(modal)
```

---

## Summary Checklist

When creating new UI components:

- [ ] View inherits from `BaseView`
- [ ] Modal inherits from `BaseModal`
- [ ] `self.bot` is set in View's `__init__`
- [ ] `modal.bot = self.bot` is called before `send_modal()`
- [ ] No unnecessary `try/except` blocks that hide errors
- [ ] Using `logger.error(..., exc_info=True)` for manual exception logging

---

## Examples

### Complete View Example

```python
from discord import ui
from cogs.error_handler import BaseView, BaseModal

class MyModal(BaseModal, title="Edit Something"):
    def __init__(self, locale: str, callback_func):
        super().__init__(timeout=300)
        self.locale = locale
        self.callback_func = callback_func

        self.input = ui.TextInput(
            label="Enter value",
            style=discord.TextStyle.short
        )
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        await self.callback_func(interaction, self.input.value)


class MyConfigView(BaseView):
    def __init__(self, bot, guild_id: int, user_id: int):
        super().__init__(timeout=300)
        self.bot = bot  # ✅ REQUIRED
        self.guild_id = guild_id
        self.user_id = user_id
        self._build_view()

    def _build_view(self):
        self.clear_items()

        container = ui.Container()
        container.add_item(ui.TextDisplay("### My Config"))

        button_row = ui.ActionRow()
        edit_btn = ui.Button(label="Edit", style=discord.ButtonStyle.primary)
        edit_btn.callback = self.on_edit
        button_row.add_item(edit_btn)
        container.add_item(button_row)

        self.add_item(container)

    async def on_edit(self, interaction: discord.Interaction):
        modal = MyModal(str(interaction.locale), self._on_edit_complete)
        modal.bot = self.bot  # ✅ REQUIRED
        await interaction.response.send_modal(modal)

    async def _on_edit_complete(self, interaction: discord.Interaction, value: str):
        # Update config with new value
        await self.save_config(value)

        self._build_view()
        await interaction.response.edit_message(view=self)
```

---

**For more information, see:**
- `cogs/error_handler.py` - The complete error handling implementation
- `main.py` - Logging configuration with `CompactExceptionFormatter`

---

## Expired interactions (10062 / 10008 / 10015) and the 3-second window

Discord requires the **first response** (`defer`, `send_message`,
`edit_message`, `send_modal`) within 3 s of *creating* the interaction.
`404 ... 10062 Unknown interaction` therefore means the ack came too late (or
the token was already used/expired). `10008 Unknown Message` on a follow-up /
`edit_original_response` means the message was deleted or dismissed; `10015`
means the webhook token expired (15 min).

**Handling** (`cogs/error_handler.py::expired_interaction_kind` /
`log_expired_interaction`): `BaseView.on_error`, `BaseModal.on_error`, the app
command handler, `report_component_error` and the token-detector router treat
these as *expected*: one `WARNING` line (view/item, interaction age, user,
guild, recent loop lag), an in-process counter, and at most one Sentry sample
per kind every 10 minutes. No error code, no DB row, no `ErrorView` (the
channel it would use is the one that failed). Any other `NotFound` is still a
normal error.

**Root causes to look for** (do not just rely on the handler):

1. *Event loop blocked.* `utils/loop_monitor.py` (started in `setup_hook`)
   logs `Event loop lag: Ns` and, from a watchdog thread, the main thread's
   stack while it is blocked (`Event loop blocked for Ns; main thread is at`).
   `on_interaction` logs `Interaction received late: age=...` when an
   interaction is already >1.5 s old on arrival, i.e. the delay happened before
   any callback ran. Never run CPU-heavy work on the loop: use
   `asyncio.to_thread` (zstd/json of transcripts, embedding dot products, PIL).
2. *Slow work before the first response.* Ack first. Rules:
   - A callback that needs a DB/HTTP/`fetch_*` round trip before it can render
     must `await interaction.response.defer(...)` first, then use
     `edit_original_response` / `followup.send`.
   - `send_modal` must be the first response and cannot follow a defer, so do
     only cache/sync work before it.
   - The default-incognito preference is read through
     `utils.incognito.resolve_default_incognito` (60 s cache, 0.8 s bound).
   - Sanction checks on the interaction path use a budget
     (`global_sanctions.INTERACTION_LOOKUP_BUDGET`, 1 s, fail-open, single
     flight, stale "no sanction" served while refreshing).
3. *DB pool starvation.* `DB_POOL_MAX_SIZE` defaults to 8 and `pool.acquire()`
   has no timeout: a burst of background queries makes every awaiting callback
   late. Raise the pool or keep hot-path queries cached.


---

## Interaction guard and delivery chain

> Owner requirement: errors may be noisy in the logs, but the user must never be
> impacted silently, the owner must be notified when they are, and even when
> everything fails the user must **never** see Discord's "This interaction
> failed" without a message from the bot.

Discord invalidates an interaction token that has not received an initial
response within **3 s** (`10062 Unknown interaction`). After an initial response
(a defer counts) the token is valid for **15 min** (followups, edits).

### 1. Prevention: `utils/interaction_guard.py`

`interaction_guard.install()` (called from `bot.setup_hook`, patches discord.py
once, same style as `utils/icon_theme.py`) arms a timer in `Interaction.__init__`
- the earliest hook, before the view store / command tree see the interaction.
The deadline is **2.0 s after the interaction's snowflake timestamp**, so
gateway delay and event-loop lag are already counted (clamped to 0 when late;
a skewed local clock is estimated from the youngest recent ages). If nothing
responded by then the guard acknowledges it:

| Interaction | Guard action |
|---|---|
| component (button / select / `DynamicItem`) | `defer()` - invisible deferred update |
| modal submit | `defer()` (update if it came from a component message, else `thinking=True, ephemeral=True`) |
| slash / context-menu command | `defer(thinking=True, ephemeral=True)` - never publish ephemeral content |
| autocomplete / ping | skipped |
| component or modal **no handler claimed** (stopped view, dead non-persistent view, unknown modal) | ephemeral "no longer active" card (`errors.inactive`) instead of a silent defer |

`BaseView._dispatch_item`, `ViewStore.schedule_dynamic_item_call` and
`Modal._dispatch_submit` are wrapped only to mark an interaction as claimed.

**Feature code needs no change.** After an auto-defer the
`InteractionResponse` methods are redirected:

| Feature call | After the guard deferred |
|---|---|
| `response.send_message(...)` | `followup.send(..., wait=True)` (same kwargs; `delete_after` emulated) |
| `response.edit_message(...)` | `edit_original_response(...)` (`delete_after` emulated) |
| `response.defer(...)` | no-op (an ephemeral `thinking` request keeps the first followup private) |
| `response.send_modal(...)` | raises `InteractionTooSlow` (a modal can only be an initial response); the error handler shows `errors.too_slow` ("Moddy was too slow, please retry") |
| `response.is_done()` | `True` |

Known behaviour differences, only on the slow path: a public command reply
becomes ephemeral (the guard deferred ephemerally); `send_message` returns the
followup `Message` instead of an `InteractionCallbackResponse` (no caller uses
the return value). The guard and a feature answering at the same instant are
reconciled through Discord's `40060`: whoever loses degrades gracefully, nothing
is answered twice.

A guard-deferred "thinking" still empty after 120 s is replaced by a
"taking longer than expected" card (`errors.still_working`).

**Owner visibility.** Each activation is logged as a WARNING naming the slow
handler (command / `custom_id`) and the age, and posted (throttled: per handler
10 min, 6/min overall) to the error channel as an `InteractionGuard` notice
(`ErrorTracker.send_ops_notice`). If the guard itself gets `10062` the loop
already stalled: it goes through the full error path with the DM fallback.

### 2. Delivery chain: `deliver_view`

For every error card, in order, each attempt time-boxed (8 s) and logged on
failure:

1. `response.send_message` (if the response is not done)
2. `followup.send(ephemeral=True)`
3. `edit_original_response`
4. **DM fallback** when the token is dead (`10062` / `10015`, or older than
   15 min) or every path failed: `dm_error_fallback` sends the localized
   `ErrorView(dm=True)` through `bot.notifications` (`NotificationSource.official("moddy")`,
   CLAUDE.md rule #11), at most **one per user per 5 min**.

The outcome is stored in the error details (`user_reached`: `response`,
`followup`, `edit_original`, `dm`, `dm_throttled`, `dm_failed`,
`dm_unavailable`, `none`) and shown in the error-channel post and the database
`context` column, with the interaction age, loop lag and guard state
(`interaction_context`). The cards are localized in the five locales
(`errors.card.*`, `errors.too_slow.*`, `errors.dm_fallback.*`,
`errors.inactive`, `errors.still_working`, `errors.command_unavailable`).

### 3. Expired interactions are fully reported

10062 / 10008 / 10015 get an error code, Sentry event, database row and
error-channel post like any other error, plus `expired_kind` and
`interaction_age` in the details. A second expired error for the *same*
interaction (guard + handler racing) is not repeated.

### 4. Event-loop stalls

`utils/loop_monitor.py` still logs lag and the blocked main-thread stack. A stall
longer than **2.5 s** is also reported to the error channel once the loop
recovers (throttled to one per 5 min) with the captured stack
(`loop_monitor.set_notifier`, wired in `bot.setup_hook`).

### Rules for new code

- Keep calling `interaction.response.defer()` yourself before slow work: the
  guard is a safety net, not a replacement (a guard activation is reported as
  user-impacting latency).
- Never open a modal after slow work: `send_modal` must be the first response.
- Never swallow an interaction error: let it reach `on_error`, or call
  `report_interaction_error` / `report_component_error`.
- Upgrading discord.py: `tests/test_interaction_guard.py` fails if a patched
  attribute moved.


---

## Expected errors

> One system for every error a user sees. An **unexpected** error (a bug) gets
> the `ErrorView` card with an error code, Sentry, a database row and an
> error-channel post. An **expected** error (missing permission, invalid input,
> not found, cooldown, module disabled, premium required...) is not a bug: it
> gets a calm, uniform card with **no code, no Sentry, no DB row, no log above
> INFO**.

Code: `utils/errors.py` (exceptions + card builder). Handling:
`cogs/error_handler.py::report_expected_error`, called from
`report_interaction_error` (so `BaseView.on_error`, `BaseModal.on_error`,
`report_component_error` and the app-command handler all recognise it) and from
`on_command_error` (prefix / hybrid commands).

### Raising

```python
from utils.errors import NotFound, InvalidInput, MissingPermissions, ModuleDisabled

async def on_button(self, interaction):           # inside any view / modal / command
    role = guild.get_role(role_id)
    if role is None:
        raise NotFound(f"`{role_id}`")            # "I couldn't find `123`. Check it and try again."
    if not 1 <= minutes <= 60:
        raise InvalidInput("duration")            # "`duration` isn't valid. ..."
    if not module_active:
        raise ModuleDisabled("Tickets")
```

Do not catch it in the callback: let it propagate. Never wrap it in
`create_error_message` yourself unless you need to answer *and* continue.

| Class | Use for | Severity |
|---|---|---|
| `MissingPermissions(perms)` | the **user** lacks a permission | warning |
| `BotMissingPermissions(perms)` | **Moddy** lacks a permission | warning |
| `InvalidInput(field=None)` | a value is not acceptable | warning |
| `NotFound(what=None)` | something referred to does not exist | warning |
| `Cooldown(retry_after)` | rate limit | info |
| `ModuleDisabled(module=None)` | module not active on this server | info |
| `PremiumRequired(feature=None)` | premium feature (premium icon) | info |
| `NotAllowed()` / `NotYours()` / `GuildOnly()` / `Unavailable()` / `MissingArgument(name)` | the rest | warning / info |
| `ModdyError(description, title=, severity=, icon=, accent=, hint=, fields=, buttons=)` | anything else (literal, already localized text) | error |

Strings come from `errors.expected.<key>.title|description|hint` in the five
locales (`ModdyError(key="...", **variables)`), or are passed literally.
discord.py built-ins are mapped automatically: `MissingPermissions`,
`BotMissingPermissions`, `CommandOnCooldown`, `NoPrivateMessage`,
`TransformerError`, `MissingRole`, `MissingRequiredArgument`, `BadArgument`.
A plain `CheckFailure` renders "Not allowed" unless the check already answered
the interaction (then it stays silent). Prefix commands keep ignoring
`CommandNotFound`, `NotOwner`, `DisabledCommand`.

### The card

`build_error_card(title, description, *, severity, accent, icon, hint, fields, buttons)`
is the only builder. `create_error_message` / `create_warning_message`
(`utils/components_v2.py`) delegate to it, so existing call sites share the look.

| Severity | Accent | Default icon |
|---|---|---|
| `info` | blurple | info |
| `warning` | yellow | warning |
| `error` | red | error |
| `critical` | dark red | exclamation |

Never green (green is success only). A custom `icon` **replaces** the default
one (`icon=""` shows none): one icon per title. The unexpected card is dark red
with the bug icon, an error code and Support/Status buttons: that is how users
tell "your input" from "our bug". Buttons must be link buttons (the card is not
persistent; it is an error-recovery view, see PERSISTENT_VIEWS.md exclusions).

### Writing guidelines (every expected error)

- **Tone**: calm, direct, "you/vous". No apology, no blame, no exclamation marks,
  no jargon, no raw exception text.
- **Content**: what happened + what to do next. Description is 1-2 short
  sentences (about 10-20 words). Title is 2-5 words, a noun phrase.
- **Dynamic values** in backticks; the `hint` (`-#`) is optional extra context.
- All text through i18n in the 5 locales.

| Do | Don't |
|---|---|
| `Missing permission` / `You need `Manage Roles` to do this. Ask an admin if you need access.` | `Error!! You are not allowed to execute this command because of insufficient perms.` |
| `Not found` / `I couldn't find `123`. Check it and try again.` | `Oops, something went wrong :(` |
| `Slow down` / `You can use this again in `4s`.` | `Cooldown active, wait 4.0 seconds` |
