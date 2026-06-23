# Staff Commands Framework

The staff command system was rebuilt around a single, scalable engine that
exposes every staff command through **two transports** from **one definition**:

- **Message command** — `@Moddy d.jsk …` — available everywhere Moddy is.
- **Slash command** — `/dev jsk …` — synced **only** to **OFFICIAL** Moddy
  servers (guilds carrying the `OFFICIAL` attribute).

Each command lives in its own file. Adding a command is: drop a file in
`staff/commands/<type>/`, decorate the class, implement `execute`. The engine
handles routing, permissions, audit logging, localization, the `incognito`
option, error handling, and the slash/message duality.

---

## Slash groups vs message prefixes

| Type | Slash group | Message prefix |
|------|-------------|----------------|
| Developer | `/dev` | `d.` |
| Team | `/team` | `t.` |
| Management | `/manage` | `m.` |
| Moderator | `/mod` | `mod.` |
| Support | `/support` | `sup.` |
| Communication | `/com` | `com.` |

Slash commands are **ephemeral by default**. Every staff slash command carries
an auto-injected `incognito` boolean option (default `true` = ephemeral). Pass
`incognito: false` to make the response public.

---

## Anatomy of a command

```python
# staff/commands/dev/reload.py
from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from utils.i18n import t

@staff_command
class ReloadCommand(StaffCommand):
    command_type = CommandType.DEV          # routes to /dev + d.
    name = "reload"                         # /dev reload  •  d.reload
    description = "Reload a bot extension."  # shown in Discord's command list
    aliases = ("re",)                       # optional extra message names
    permission = None                       # optional fine-grained perm node
    sensitive = False                       # redact args in the audit log
    options = [
        SlashOption("extension", "string", "Extension to reload, or 'all'.",
                    required=False, default="all"),
    ]

    async def execute(self, ctx):           # runs identically for both transports
        ext = ctx.opt("extension")
        ...
        await ctx.send(view=design.success(t("...", locale=ctx.locale), "..."))
```

### `StaffContext`

`ctx` abstracts the transport:

- `ctx.opt(name, default)` — option value (slash) or parsed message arg.
- `ctx.send(view=…, content=…)` — reply (message) or respond/followup (slash,
  ephemeral when `incognito`). Returns the `Message` so you can `await msg.edit`.
- `ctx.open_modal(factory, label=…)` — slash opens the modal directly; message
  surfaces a button that opens it (Modals V2).
- `ctx.locale`, `ctx.author`, `ctx.guild`, `ctx.channel`, `ctx.bot`,
  `ctx.is_slash`, `ctx.incognito`.

### Options

`SlashOption(name, type, description, required=False, default=None, choices=None)`
where `type` ∈ `string | integer | boolean | number | user | member | channel |
role`. `choices=[...]` turns it into a fixed choice list. The slash signature is
generated dynamically; message arguments map onto the first option by default —
override `parse_message(self, raw)` for multi-arg commands.

### Design helpers (`staff.framework.design`)

Standardized Components V2 panels with coloured accent bars and
`### <emoji> Title` headers: `design.success / error / info / warning / loading /
panel(...)`, plus `permission_denied`, `invalid_usage`, and `make_container`
for custom interactive layouts. Every panel is a `BaseView`.

---

## OFFICIAL-only slash sync

The framework cog publishes its slash groups on `bot.staff_slash_groups`. They
are **never** added to the global command tree. During per-guild sync
(`bot.sync_all_guild_commands` / `bot.sync_guild_commands`) the groups are added
**only** to guilds whose id is returned by `bot.get_official_guild_ids()` (the
`OFFICIAL` guild attribute). Mark a server with `/dev official <guild_id> add`.

---

## Permissions

Permission checks are centralized in the dispatcher:

1. The command's `command_type` maps to the existing role requirement
   (`utils/staff_permissions.py`).
2. If `permission` is set, the user must also hold that fine-grained node
   (`utils/staff_role_permissions.py`) — devs / super-admin bypass.

This lets commands that aren't really dev-only (Stripe/billing, redirect links,
banners, marking official servers) live in the right department gated by nodes
like `stripe_manage`, `redirect_manage`, `banner_manage`, `official_manage`
instead of the dev prefix.

---

## Audit logging

Every invocation is logged automatically by the dispatcher
(`utils/staff_logger.py`). Set `sensitive = True` on a command to redact its
arguments (used by `sql` and `jsk`). Commands must **not** log themselves.

---

## Migration status

- ✅ **Framework** — complete.
- ✅ **`/dev` group** — migrated: `reload, shutdown, stats, sql, jsk, error,
  sync, serverlist, disable, enable, disabled, cogs, presence, announcements,
  official`.
- ⏳ **Legacy (still on the old message-only cogs)** — `team_commands`,
  `staff_manager`, `moderator_commands`, `support_commands`,
  `communication_commands`, `case_commands`, and the dev `redirect` / `banner` /
  `sub-refresh` commands (the latter to be **recategorized** out of dev into
  proper departments with the new permission nodes).

During migration, a command handled by the framework is skipped by its legacy
cog (guard in `staff/dev_commands.py`), so there is never a double response.
