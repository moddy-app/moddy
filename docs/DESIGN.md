# Moddy Bot - Design Guidelines

This document outlines the design best practices and UI/UX standards for the Moddy Discord bot. Following these guidelines ensures consistency across all modules and creates an intuitive user experience.

---

## 🎨 Core Design Principles

### 1. **Simplicity First**
Everything must be designed with the end user in mind. The interface should be so intuitive that even a 5-year-old could understand how to use the bot. If a feature requires explanation, it's too complex.

**Key Points:**
- Use clear, descriptive labels
- Provide context through descriptions
- Show examples when needed
- Guide users with visual cues (emojis, formatting)
- Minimize the number of steps required

---

## 🧱 Components V2 - The Standard

### **ALWAYS use Components V2**

**✅ DO:**
- Use `ui.Container()` with `ui.TextDisplay()`
- Use `ui.LayoutView` or `BaseView` (which extends it)
- Use simple text messages when appropriate

**❌ NEVER:**
- Use regular embeds (`discord.Embed()`)
- Use V1 components for new features
- Mix embed-based and Component V2 approaches

**Why V2 Components?**
- More structured and readable
- Better user experience
- Easier to maintain
- Modern Discord interface
- Native support for interactive layouts

**Example:**
```python
class MyView(BaseView):
    def __init__(self):
        super().__init__(timeout=300)

        container = ui.Container()
        container.add_item(ui.TextDisplay(
            "### <:settings:1398729549323440208> Configuration"
        ))
        container.add_item(ui.TextDisplay(
            "Configure your server settings below."
        ))

        self.add_item(container)
```

---

## 😀 Emoji Usage

### **ONLY use custom emojis from `/docs/EMOJIS.md`**

**✅ DO:**
- Use bot's custom emojis: `<:done:1398729525277229066>`
- Use flag emojis for languages: 🇬🇧 🇫🇷 🇪🇸
- Reference `/docs/EMOJIS.md` for the full list

**❌ NEVER:**
- Use default Unicode emojis (except flags)
- Use emojis from other servers
- Hardcode emoji IDs without checking the documentation

**Common Emojis:**
- Success: `<:done:1398729525277229066>`
- Error: `<:error:1444049460924776478>`
- Warning: `<:warning:1446108410092195902>`
- Info: `<:info:1401614681440784477>`
- Settings: `<:settings:1398729549323440208>`
- User: `<:user:1398729712204779571>`
- Save: `<:save:1444101502154182778>`
- Back: `<:back:1401600847733067806>`
- Delete: `<:delete:1401600770431909939>`
- Cancel: `<:undone:1398729502028333218>`
- Required: `<:required_fields:1446549185385074769>`

**Why custom emojis?**
- Consistent branding
- Better visual identity
- Custom-designed for Moddy's use cases

---

## 📐 Formatting Standards

### **Titles in Components V2**

Titles **MUST** always use `###` (heading 3) format with an emoji prefix:

**Format:**
```
### <:emoji_name:emoji_id> Title Text
```

**✅ Examples:**
```markdown
### <:groups:1446127489842806967> Inter-Server Configuration
### <:user:1398729712204779571> User Information
### <:settings:1398729549323440208> Server Settings
### <:star:1446267438671859832> Starboard Configuration
```

**❌ Wrong:**
```markdown
# Title (wrong heading level)
**Title** (bold, not heading)
Title <:emoji:id> (emoji after title)
### Title (missing emoji)
```

---

### **Configuration Panel Structure**

All configuration panels must follow this exact structure:

```python
# 1. Title with emoji
container.add_item(ui.TextDisplay(
    f"### <:emoji:id> This is a configuration"
))

# 2. Module description
container.add_item(ui.TextDisplay(
    "Description of what this module does"
))

# 3. Separator
container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

# 4. Field 1
container.add_item(ui.TextDisplay(
    f"**Field Number 1**<:required_fields:1446549185385074769>\n"  # Add emoji if required
    f"-# Description of field number 1"
))
# Add select/button/input for field 1
channel_row = ui.ActionRow()
# ... add interactive element ...
container.add_item(channel_row)

# 5. Field 2
container.add_item(ui.TextDisplay(
    f"**Field Number 2**\n"  # No emoji if optional
    f"-# Description of field number 2"
))
# Add select/button/input for field 2
option_row = ui.ActionRow()
# ... add interactive element ...
container.add_item(option_row)

# Continue for all fields...
```

**Important Notes:**
- Required fields: Add `<:required_fields:1446549185385074769>` emoji after the field title
- Optional fields: No emoji needed
- Each field has: **Bold title** + emoji (if required) + newline + `-#` description
- Interactive element (select/button) goes right after the description

---

### **Dynamic Values and Data**

When displaying values that change from user to user, data, or any dynamic content, **ALWAYS** wrap them in backticks:

**✅ DO:**
```python
f"**Moddy ID:** `{moddy_id}`"
f"**User:** {user.mention} (`{user.id}`)"
f"**Server:** {guild.name} (`{guild.id}`)"
f"**Current value:** `{config['reaction_count']}`"
```

**❌ DON'T:**
```python
f"**Moddy ID:** {moddy_id}"  # Missing backticks
f"**User:** {user.id}"  # Should be in backticks
```

**Why backticks?**
- Clearly distinguishes dynamic data from static text
- Improves readability
- Follows Discord markdown conventions
- Makes IDs and values stand out

---

### **Descriptions and Hints**

Use `-#` markdown for greyed-out descriptive text:

**Example:**
```python
container.add_item(ui.TextDisplay(
    f"**Inter-Server Type**\n"
    f"-# Choose which inter-server network to join"
))
```

**Why `-#`?**
- Creates subtle, greyed-out text
- Perfect for hints and descriptions
- Doesn't overwhelm the main content
- Improves visual hierarchy

---

## 🚧 Separator Usage

### **Use separators ONLY when truly necessary**

Separators should be used sparingly to avoid visual clutter.

**✅ When to use separators:**
- Between major sections of a configuration
- Before/after important warnings
- To separate action buttons from content

**❌ When NOT to use separators:**
- Between every field
- Between title and description
- Just for visual decoration

**Example:**
```python
# Good - separator between major sections
container.add_item(ui.TextDisplay("### <:settings:...> Configuration"))
container.add_item(ui.TextDisplay("Configure your settings"))

container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

container.add_item(ui.TextDisplay("**Channel Selection**"))
# ... channel selector ...

container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

container.add_item(ui.TextDisplay("<:warning:...> **Security Warning**"))
```

**Spacing options:**
- `discord.SeparatorSpacing.small` - Minimal spacing
- `discord.SeparatorSpacing.large` - More spacing (use rarely)

---

## 🎛️ Configuration Panel Design

### **Standard Configuration Structure**

Based on the interserver configuration and other modules, follow this structure:

```python
class ModuleConfigView(BaseView):
    def __init__(self, bot, guild_id: int, user_id: int, locale: str, current_config: Optional[Dict] = None):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale

        # Configuration state
        self.current_config = current_config or get_default_config()
        self.working_config = self.current_config.copy()
        self.has_changes = False

        self._build_view()

    def _build_view(self):
        """Build the configuration interface"""
        self.clear_items()

        container = ui.Container()

        # 1. Title with emoji
        container.add_item(ui.TextDisplay(
            f"### <:module_emoji:id> {t('modules.name.config.title', locale=self.locale)}"
        ))

        # 2. Description
        container.add_item(ui.TextDisplay(
            t('modules.name.config.description', locale=self.locale)
        ))

        # 3. Separator (if needed)
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # 4. Configuration sections with clear headers
        container.add_item(ui.TextDisplay(
            f"**{t('section.title', locale=self.locale)}**<:required_fields:...>\n"
            f"-# {t('section.description', locale=self.locale)}"
        ))

        # 5. Interactive elements (selects, buttons)
        # ...

        self.add_item(container)

        # 6. Action buttons at the bottom
        self._add_action_buttons()
```

### **Action Button Standards**

**Always provide these buttons:**

1. **Back Button** - Return to main menu
   - Emoji: `<:back:1401600847733067806>`
   - Style: `secondary`
   - Disabled when changes are pending

2. **Save Button** - Save changes (only shown when changes exist)
   - Emoji: `<:save:1444101502154182778>`
   - Style: `success`

3. **Cancel Button** - Discard changes (only shown when changes exist)
   - Emoji: `<:undone:1398729502028333218>`
   - Style: `danger`

4. **Delete Button** - Remove configuration (only shown when no changes and config exists)
   - Emoji: `<:delete:1401600770431909939>`
   - Style: `danger`

**Example:**
```python
def _add_action_buttons(self):
    """Add action buttons at the bottom"""
    button_row = ui.ActionRow()

    # Back button (disabled if changes pending)
    back_btn = ui.Button(
        emoji=discord.PartialEmoji.from_str("<:back:1401600847733067806>"),
        label=t('modules.config.buttons.back', locale=self.locale),
        style=discord.ButtonStyle.secondary,
        disabled=self.has_changes
    )
    back_btn.callback = self.on_back
    button_row.add_item(back_btn)

    if self.has_changes:
        # Save button
        save_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str("<:save:1444101502154182778>"),
            label=t('modules.config.buttons.save', locale=self.locale),
            style=discord.ButtonStyle.success
        )
        save_btn.callback = self.on_save
        button_row.add_item(save_btn)

        # Cancel button
        cancel_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str("<:undone:1398729502028333218>"),
            label=t('modules.config.buttons.cancel', locale=self.locale),
            style=discord.ButtonStyle.danger
        )
        cancel_btn.callback = self.on_cancel
        button_row.add_item(cancel_btn)

    self.add_item(button_row)
```

---

## 📊 Configuration Panel Best Practices

### **1. Show Current Values**

Always display the current/selected value clearly:

```python
container.add_item(ui.TextDisplay(
    f"-# Current value: **{self.working_config['reaction_count']}** {self.working_config['emoji']}"
))
```

### **2. Pre-select Current Values**

When using selects, pre-populate with current values:

```python
channel_select = ui.ChannelSelect(
    placeholder="Select a channel",
    channel_types=[discord.ChannelType.text]
)

# Pre-select current channel
if self.working_config.get('channel_id'):
    channel = self.bot.get_channel(self.working_config['channel_id'])
    if channel:
        channel_select.default_values = [channel]
```

### **3. Clear Option Descriptions**

Provide helpful descriptions for select options:

```python
discord.SelectOption(
    label="English",
    value="english",
    description="Join the English inter-server network",
    emoji="🇬🇧",
    default=self.working_config.get('interserver_type') == 'english'
)
```

### **4. Warnings and Important Information**

Use the warning emoji for critical information:

```python
container.add_item(ui.TextDisplay(
    f"<:warning:1446108410092195902> **Security Warning**\n"
    f"-# Messages from other servers will be visible to your members"
))
```

### **5. Track Changes State**

Always maintain a working copy of the configuration:

```python
# On interaction
self.working_config['setting'] = new_value
self.has_changes = True
self._build_view()
await interaction.response.edit_message(view=self)
```

---

## 🎯 User Experience Guidelines

### **1. Immediate Visual Feedback**

- Update the interface immediately after user interaction
- Show current state clearly
- Disable buttons when actions aren't available

### **2. Error Handling**

Use appropriate message types:

```python
from utils.components_v2 import create_error_message, create_success_message

# Error
view = create_error_message(
    "Invalid Input",
    f"The Moddy ID `{moddy_id}` is invalid. Format should be `XXXX-XXXX`."
)

# Success
view = create_success_message(
    "Settings Saved",
    "Your configuration has been saved successfully!"
)
```

### **3. Confirm Destructive Actions**

For delete operations, consider adding confirmation:

```python
# Option 1: Modal confirmation
# Option 2: Follow-up message explaining the action
await interaction.followup.send(
    t('modules.config.delete.success', locale=self.locale),
    ephemeral=True
)
```

### **4. Use Ephemeral Messages**

For feedback and errors, use ephemeral messages:

```python
await interaction.response.send_message(
    t('modules.config.errors.wrong_user', locale=self.locale),
    ephemeral=True
)
```

---

## 🌍 Internationalization (i18n)

### **ALWAYS use i18n for all user-facing text**

**✅ DO:**
```python
from utils.i18n import t

title = t('modules.interserver.config.title', locale=self.locale)
description = t('modules.interserver.config.description', locale=self.locale)
```

**❌ DON'T:**
```python
title = "Inter-Server Configuration"  # Hardcoded
```

**Why i18n?**
- Moddy supports multiple languages
- Allows easy translation updates
- Maintains consistency across languages
- Better user experience for non-English users

---

## 📋 Complete Example: Configuration View

Here's a complete example following all design guidelines:

```python
class ExampleConfigView(BaseView):
    """Example configuration view following all design guidelines"""

    def __init__(self, bot, guild_id: int, user_id: int, locale: str, current_config: Optional[Dict] = None):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale

        # Config state
        if current_config and current_config.get('channel_id') is not None:
            self.current_config = current_config.copy()
            self.has_existing_config = True
        else:
            self.current_config = self._get_default_config()
            self.has_existing_config = False

        self.working_config = self.current_config.copy()
        self.has_changes = False

        self._build_view()

    def _build_view(self):
        """Build the configuration interface"""
        self.clear_items()

        container = ui.Container()

        # Title with emoji (### format)
        container.add_item(ui.TextDisplay(
            f"### <:settings:1398729549323440208> {t('modules.example.config.title', locale=self.locale)}"
        ))

        # Description
        container.add_item(ui.TextDisplay(
            t('modules.example.config.description', locale=self.locale)
        ))

        # Separator before sections
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Section: Channel Selection (Required)
        container.add_item(ui.TextDisplay(
            f"**{t('modules.example.config.channel.title', locale=self.locale)}**<:required_fields:1446549185385074769>\n"
            f"-# {t('modules.example.config.channel.description', locale=self.locale)}"
        ))

        # Channel selector
        channel_row = ui.ActionRow()
        channel_select = ui.ChannelSelect(
            placeholder=t('modules.example.config.channel.placeholder', locale=self.locale),
            channel_types=[discord.ChannelType.text],
            min_values=0,
            max_values=1
        )

        # Pre-select current channel
        if self.working_config.get('channel_id'):
            channel = self.bot.get_channel(self.working_config['channel_id'])
            if channel:
                channel_select.default_values = [channel]

        channel_select.callback = self.on_channel_select
        channel_row.add_item(channel_select)
        container.add_item(channel_row)

        # Separator before warning
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Warning
        container.add_item(ui.TextDisplay(
            f"<:warning:1446108410092195902> **{t('modules.example.config.warning.title', locale=self.locale)}**\n"
            f"-# {t('modules.example.config.warning.description', locale=self.locale)}"
        ))

        self.add_item(container)
        self._add_action_buttons()

    def _add_action_buttons(self):
        """Add action buttons"""
        button_row = ui.ActionRow()

        # Back button
        back_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str("<:back:1401600847733067806>"),
            label=t('modules.config.buttons.back', locale=self.locale),
            style=discord.ButtonStyle.secondary,
            disabled=self.has_changes
        )
        back_btn.callback = self.on_back
        button_row.add_item(back_btn)

        if self.has_changes:
            # Save button
            save_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str("<:save:1444101502154182778>"),
                label=t('modules.config.buttons.save', locale=self.locale),
                style=discord.ButtonStyle.success
            )
            save_btn.callback = self.on_save
            button_row.add_item(save_btn)

            # Cancel button
            cancel_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str("<:undone:1398729502028333218>"),
                label=t('modules.config.buttons.cancel', locale=self.locale),
                style=discord.ButtonStyle.danger
            )
            cancel_btn.callback = self.on_cancel
            button_row.add_item(cancel_btn)
        else:
            if self.has_existing_config:
                # Delete button
                delete_btn = ui.Button(
                    emoji=discord.PartialEmoji.from_str("<:delete:1401600770431909939>"),
                    label=t('modules.config.buttons.delete', locale=self.locale),
                    style=discord.ButtonStyle.danger
                )
                delete_btn.callback = self.on_delete
                button_row.add_item(delete_btn)

        self.add_item(button_row)

    async def on_channel_select(self, interaction: discord.Interaction):
        """Handle channel selection"""
        if not await self.check_user(interaction):
            return

        # Update config
        if interaction.data['values']:
            channel_id = int(interaction.data['values'][0])
            self.working_config['channel_id'] = channel_id
        else:
            self.working_config['channel_id'] = None

        # Mark as changed
        self.has_changes = True

        # Rebuild view
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def check_user(self, interaction: discord.Interaction) -> bool:
        """Verify correct user"""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                t('modules.config.errors.wrong_user', locale=self.locale),
                ephemeral=True
            )
            return False
        return True
```

---

## ✅ Quick Checklist

Before submitting any UI code, verify:

- [ ] Using Components V2 (`ui.Container`, `ui.TextDisplay`)
- [ ] NOT using regular embeds (`discord.Embed`)
- [ ] Using ONLY custom emojis from `/docs/EMOJIS.md` (except flags)
- [ ] Titles use `###` format with emoji prefix
- [ ] Dynamic values are wrapped in backticks
- [ ] Separators used sparingly (only when necessary)
- [ ] Section headers use bold with emoji
- [ ] Descriptions use `-#` for greyed text
- [ ] All text uses i18n system (`t('key', locale=locale)`)
- [ ] Required fields marked with `<:required_fields:...>`
- [ ] Action buttons follow standard pattern (Back, Save, Cancel, Delete)
- [ ] Interface is simple and intuitive
- [ ] Immediate visual feedback on interactions
- [ ] Error messages use `create_error_message()`
- [ ] Success messages use `create_success_message()`
- [ ] All Views inherit from `BaseView`
- [ ] All Modals inherit from `BaseModal`

---

## Related Documentation

- [COMPONENTS_V2.md](COMPONENTS_V2.md) — Technical details on Components V2
- [EMOJIS.md](EMOJIS.md) — Complete list of custom emojis
- [ERROR_HANDLING.md](ERROR_HANDLING.md) — BaseView and BaseModal requirements
- [COMMANDS.md](COMMANDS.md) — Slash command standards
- `/utils/i18n.py` — Internationalization system
- `/utils/components_v2.py` — Helper functions for V2 components

---

**Remember:** The goal is to create interfaces that are beautiful, intuitive, and accessible to all users. When in doubt, choose simplicity over complexity.
