# /testerror restricted to staff servers

- `/testerror` was a global command (visible everywhere). It is now published only in OFFICIAL guilds, like `/dev` and `/team`.
- `cogs/dev_tools.py`: `cog_load` pulls the command out of the global tree into `bot.staff_guild_commands`; `cog_unload` cleans up.
- `bot.py`: new `staff_guild_commands` list, added to the per-guild tree in `_register_guild_command_set` for OFFICIAL guilds.
- Follow-up: the stale global `/testerror` disappears at the next global sync (startup).
