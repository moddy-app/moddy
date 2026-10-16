# Moddy Framework

`moddy` is Moddy's internal application framework. It is a compatibility layer
over `discord.py`, not a fork and not a replacement for Discord's API client.

## Design goals

- Preserve `discord.py` behaviour and upstream compatibility.
- Put Moddy conventions behind stable imports.
- Make modern Discord application features the easy default.
- Allow cogs to migrate one file at a time.

## Public API

```python
from moddy import Bot, Cog, app_commands, ui
from moddy.interactions import InteractionResponse
```

`Bot` and `Cog` are direct subclasses of the equivalent discord.py classes.
They are safe to adopt without changing lifecycle or command-tree behaviour.

`app_commands` exposes all upstream `discord.app_commands` members and adds:

- `@app_commands.global_command(...)` for commands usable by guild- and
  user-installed apps in guilds, bot DMs, and private channels.
- `@app_commands.guild_command(...)` for commands that require a guild.

`ui` exposes Components V2 primitives (`LayoutView`, `Container`,
`TextDisplay`, and related classes) plus `ui.message()` for small structured
messages. All visible text remains the caller's i18n responsibility.

`InteractionResponse` makes it safe to send a first response or follow-up
without duplicating `interaction.response.is_done()` checks.

## Compatibility and migration

The framework never patches discord.py objects and does not synchronise
commands itself. `ModdyBot` remains responsible for the project's established
global/guild sync policy.

Migrate code incrementally:

1. Replace `commands.Cog` with `moddy.Cog`.
2. Use `moddy.app_commands.global_command` or `guild_command` for new commands.
3. Use `InteractionResponse` for new handlers that may respond after a defer.
4. Keep existing `BaseView` and `BaseModal` until their central error handling
   has a dedicated, tested migration; do not create a parallel error pipeline.

## Testing

The framework contract lives in `tests/test_moddy_framework.py`. Run it with:

```bash
python -m pytest tests/test_moddy_framework.py -q
```
