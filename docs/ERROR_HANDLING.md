# Error Handling Guide

## Table of Contents
1. [Overview](#overview)
2. [Error Handler System](#error-handler-system)
3. [How to Use BaseView and BaseModal](#how-to-use-baseview-and-basemodal)
4. [The 3-Second Window](#the-3-second-window)
5. [Reaching the User No Matter What](#reaching-the-user-no-matter-what)
6. [Best Practices](#best-practices)
7. [Logging](#logging)
8. [Troubleshooting](#troubleshooting)

---

## Overview

**CRITICAL**: All errors in Moddy MUST pass through the centralized error handler (`cogs/error_handler.py`).

The error handling system ensures:
- ✅ **ALL errors are logged** with full traceback in a single line
- ✅ **ALL errors are stored** in the database
- ✅ **ALL errors are sent** to the Discord error logging channel
- ✅ **ALL users ALWAYS receive** an error embed with an error code
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
Error stored in database
    ↓
Error sent to Discord error logging channel
    ↓
User receives error embed with error code
```

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

## The 3-Second Window

Discord gives an interaction **3 seconds** to receive its first response.
Miss it and the token is dead: every later call fails with `10062 Unknown
interaction`, the error handler itself cannot answer, and the user sees
Discord's own **"The application did not respond"** — no message, no error
code, nothing to trace. It looks like Moddy is broken, and there is no
recovering the interaction afterwards.

**So acknowledge before you work.** Any handler that awaits a database
query, an HTTP or gateway call, a `fetch_*`, or Redis must acknowledge
first:

```python
from utils.interaction_response import safe_defer

async def callback(self, interaction: discord.Interaction):
    await safe_defer(interaction, ephemeral=True)   # ← first awaited statement
    data = await self.bot.db.get_something(...)     # now you have 15 minutes
    await interaction.followup.send(view=build(data))
```

`safe_defer` never raises. It returns `False` when the token was already
dead, and it picks `thinking` from the interaction type — a slash command
gets the "thinking…" placeholder, a component re-rendering its own panel
does not. Pass `thinking=` explicitly only when you need to override that.

**Two things do not fit this pattern:**

- **Modals.** Discord refuses `send_modal()` on an acknowledged
  interaction, so a handler that opens a modal must stay un-deferred —
  which means it must do **no** slow work first. Move the lookups into the
  modal's `on_submit`, or cache them. Staff commands declare this with
  `StaffCommand.opens_modal = True`, which tells the router not to defer.
- **Work that cannot be moved.** If a value is genuinely needed before
  acknowledging (visibility, a permission), cache it and put a hard timeout
  on the lookup — see `utils/incognito.py::resolve_incognito`. Degrading
  one reply beats killing the interaction.

---

## Reaching the User No Matter What

**An unexpected error must always surface as Moddy's error card with an
error code.** Never as Discord's failure message, and never as a bare "an
error occurred" that the team cannot trace.

`utils/interaction_response.py::deliver` is how that is guaranteed. It
walks every transport until one works — followup, then editing the original
response, then the initial response, then a plain channel message
mentioning the user when the token itself is dead — and it never raises,
because a second exception on the error path would silence the first.

```python
from utils.interaction_response import deliver

await deliver(interaction, view=ErrorView(error_code), ephemeral=True)
```

Use it instead of hand-rolling `if interaction.response.is_done(): ... else: ...`.

### Getting an error code outside a View, Modal or app command

`BaseView.on_error`, `BaseModal.on_error` and `on_app_command_error`
already run the full pipeline. Everywhere else — a listener, a background
task, a message command, a service callback — call `report_error`:

```python
from cogs.error_handler import ErrorView, report_error
from utils.interaction_response import deliver

except Exception as exc:
    error_code = await report_error(
        self.bot, exc, source="Cog:reminders.check_loop",
        user=interaction.user, guild=interaction.guild, channel=interaction.channel,
    )
    if error_code:
        await deliver(interaction, view=ErrorView(error_code), ephemeral=True)
```

For a `DynamicItem` callback dispatched without a live `BaseView`, use
`report_component_error(interaction, exc, source)` — it reports **and**
delivers.

### Expected errors keep their own message

None of this applies to errors that are the user's fault or a known
condition: missing permissions, member not found, invalid input, module
disabled, premium required, quota exhausted. Those keep a specific,
translated message via `create_error_message()` and get **no** error code —
a code there is noise that trains people to ignore codes.

The test to apply: *could the team act on this?* If yes it is a bug and
needs a code; if the user simply has to do something differently, it needs
a sentence.

**Never report a bug as an expected error.** A catch-all around an API
call that answers "the service is unavailable" hides real crashes — catch
the provider's own error type instead.

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
- [ ] `safe_defer()` before any awaited work, unless the handler opens a modal
- [ ] A handler that opens a modal does no slow work first
- [ ] Error paths use `deliver()`, never a hand-rolled `is_done()` branch
- [ ] Unexpected errors reach the user with an error code (`report_error` /
      `report_component_error` where no global handler covers the path)
- [ ] Expected errors keep a specific translated message and no code

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
