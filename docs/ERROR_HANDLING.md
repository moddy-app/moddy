# Error Handling Guide

## Table of Contents
1. [Overview](#overview)
2. [Error Handler System](#error-handler-system)
3. [How to Use BaseView and BaseModal](#how-to-use-baseview-and-basemodal)
4. [Best Practices](#best-practices)
5. [Logging](#logging)
6. [Troubleshooting](#troubleshooting)

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
