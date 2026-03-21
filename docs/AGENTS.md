# AI Agent Instructions — Moddy Project

> **Primary reference:** Read [/CLAUDE.md](/CLAUDE.md) first. It contains the complete project overview, architecture, mandatory coding rules, and documentation index.

This file contains supplementary context for AI agents that is not covered in CLAUDE.md.

---

## Incognito System

The incognito system allows users to control whether bot responses are ephemeral (visible only to them) or public.

### How It Works

- User preference stored in `users.attributes.DEFAULT_INCOGNITO` (JSONB)
- `true` = messages private by default, `false` = public, `null`/absent = `true`
- Each command that supports incognito has an `incognito: Optional[bool] = None` parameter
- If `None`, the user's stored preference is used
- Users can change their default via `/preferences`

### Implementation Pattern

```python
@app_commands.command(name="mycommand", description="My command")
@app_commands.describe(
    incognito="Make response visible only to you"
)
async def my_command(
    self,
    interaction: discord.Interaction,
    # ... other params ...
    incognito: Optional[bool] = None  # ALWAYS last, ALWAYS Optional with = None
):
    # Incognito resolution block
    if incognito is None and self.bot.db:
        try:
            user_pref = await self.bot.db.get_attribute('user', interaction.user.id, 'DEFAULT_INCOGNITO')
            ephemeral = True if user_pref is None else user_pref
        except:
            ephemeral = True
    else:
        ephemeral = incognito if incognito is not None else True

    # Use ephemeral in ALL send_message / followup.send calls
    await interaction.response.send_message("...", ephemeral=ephemeral)
```

### Rules
- `incognito` parameter must be **last** in the signature
- Type must be `Optional[bool] = None` (never just `bool`)
- Error messages are **always** ephemeral regardless of preference
- Followups must use the **same** ephemeral value as the initial response
- The `@add_incognito_option()` decorator does **NOT** work with discord.py — always implement manually

### Commands That Should Have Incognito
- Personal info commands: `/ping`, `/user`, `/avatar`, `/banner`
- Utility commands: `/translate`, `/reminder`, `/roll`
- Bot info: `/moddy`

### Commands That Should NOT Have Incognito
- Server configuration commands (`/config`)
- Staff/dev commands
- Commands that are public by nature

---

## Quick Reference: Emojis

Full list in [EMOJIS.md](EMOJIS.md). Most commonly used:

| Purpose | Emoji | Syntax |
|---|---|---|
| Success | done | `<:done:1398729525277229066>` |
| Error/Cancel | undone | `<:undone:1398729502028333218>` |
| Error (red) | error | `<:error:1444049460924776478>` |
| Warning | warning | `<:warning:1446108410092195902>` |
| Info | info | `<:info:1401614681440784477>` |
| Settings | settings | `<:settings:1398729549323440208>` |
| User | user | `<:user:1398729712204779571>` |
| Save | save | `<:save:1444101502154182778>` |
| Back | back | `<:back:1401600847733067806>` |
| Delete | delete | `<:delete:1401600770431909939>` |
| Required field | required_fields | `<:required_fields:1446549185385074769>` |
| Loading | loading | `<a:loading:1455219844080336907>` |
| Premium | premium | `<:premium:1401602724801548381>` |

**Rule:** Never use Unicode emojis (except country flags). Always use custom emojis from `/utils/emojis.py`.

---

## Related Documentation

| Document | Content |
|---|---|
| [/CLAUDE.md](/CLAUDE.md) | Project overview, architecture, mandatory rules, doc index |
| [DESIGN.md](DESIGN.md) | UI/UX design guidelines, Components V2 patterns |
| [COMMANDS.md](COMMANDS.md) | Slash command creation guide |
| [MODULE_SYSTEM.md](MODULE_SYSTEM.md) | Server module system |
| [STAFF_SYSTEM.md](STAFF_SYSTEM.md) | Staff permission system |
| [DATABASE.md](DATABASE.md) | Database schema and queries |
| [ERROR_HANDLING.md](ERROR_HANDLING.md) | Error handling with BaseView/BaseModal |
| [COMPONENTS_V2.md](COMPONENTS_V2.md) | Components V2 technical reference |
| [INTERNAL_API.md](INTERNAL_API.md) | Bot ↔ Backend communication |
| [RAILWAY.md](RAILWAY.md) | Environment variables and deployment |
| [EMOJIS.md](EMOJIS.md) | Complete custom emoji list |
