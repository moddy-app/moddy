# Staff Commands Framework

The staff command system is built around a single, scalable engine that
exposes every staff command through **two transports** from **one definition**:

- **Message command** — `@Moddy d.jsk …` — available everywhere Moddy is.
- **Slash command** — `/dev jsk …` — synced **only** to **OFFICIAL** Moddy
  servers (guilds carrying the `OFFICIAL` attribute).

Each command lives in its own file under `staff/commands/<type>/`. Adding a
command is: drop a file, decorate the class, implement `execute`. The engine
handles routing, permissions, audit logging, localization, the `incognito`
option, and error handling.

---

## Slash groups vs message prefixes

| Type | Slash group | Message prefix | File directory |
|------|-------------|----------------|----------------|
| Developer | `/dev` | `d.` | `staff/commands/dev/` |
| Team | `/team` | `t.` | `staff/commands/team/` |
| Management | `/manage` | `m.` | `staff/commands/manage/` |
| Moderator | `/mod` | `mod.` | `staff/commands/mod/` |
| Support | `/support` | `sup.` | `staff/commands/support/` *(no commands yet)* |
| Communication | `/com` | `com.` | `staff/commands/com/` |

Slash commands are **ephemeral by default**. Every staff slash command carries
an auto-injected `incognito` boolean option (default `true` = ephemeral). Pass
`incognito: false` to make the response public.

Message commands are always rendered in **English** (no per-user language
signal on a message); slash commands use `interaction.locale`.

---

## Anatomy of a command

```python
# staff/commands/dev/reload.py
from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType

@staff_command
class ReloadCommand(StaffCommand):
    command_type = CommandType.DEV          # routes to /dev + d.
    name = "reload"                         # /dev reload  •  d.reload
    description = "Reload a bot extension." # shown in Discord's command list
    aliases = ("re",)                       # optional extra message aliases
    permission = None                       # optional fine-grained permission node
    sensitive = False                       # redact args in the audit log
    options = [
        SlashOption("extension", "string", "Extension to reload, or 'all'.",
                    required=False, default="all"),
    ]

    async def execute(self, ctx):           # runs identically for both transports
        ext = ctx.opt("extension")
        ...
        await ctx.send(view=design.success("Done", f"`{ext}` reloaded."))
```

### `StaffContext`

`ctx` abstracts the transport:

| Attribute / method | Description |
|--------------------|-------------|
| `ctx.opt(name, default)` | Option value (slash) or parsed message arg |
| `ctx.send(view=…, content=…)` | Reply/respond — returns the `Message` for later edits, or `None` if the staffer deleted their command meanwhile (always guard `if msg:` before editing) |
| `ctx.open_modal(factory, label=…)` | Slash opens the modal; message sends a button that opens it |
| `ctx.defer(thinking=True)` | Defer response for long operations |
| `ctx.locale` | User locale (slash) or `"en-US"` (message) |
| `ctx.author` | The invoking user |
| `ctx.guild`, `ctx.channel` | Guild and channel |
| `ctx.bot` | Bot instance |
| `ctx.is_slash` | `True` when invoked via slash |
| `ctx.incognito` | `True` when response should be ephemeral |

### Deleting a command deletes its answers

A message command is tracked from dispatch (`begin_command`) to the last thing
it sends. Deleting the command message deletes **every** message the bot sent
for it — not just the last one — and a reply that lands *after* the deletion
(the report at the end of a `t.role` window, a long `d.sync`) is sent and
removed again, because Discord offers no way to un-send something already in
flight.

Two consequences for command authors:

- `ctx.send` returns `None` in that case. Guard before editing.
- Anything sent outside `ctx.send` — a card posted with `channel.send` — is
  **not** tracked. Call `cog.track_response(ctx.message.id, msg)` when it should
  disappear with the command, and leave it alone when it should not
  (`t.flex`'s public proof, the `/team access` card an administrator answers).

See [`staff/base.py`](../staff/base.py) and
[`tests/test_staff_response_tracking.py`](../tests/test_staff_response_tracking.py).

### SlashOption types

`SlashOption(name, type, description, required=False, default=None, choices=None)`

| `type` value | Python annotation |
|---|---|
| `string` | `str` |
| `integer` | `int` |
| `boolean` | `bool` |
| `number` | `float` |
| `user` | `discord.User` |
| `member` | `discord.Member` |
| `channel` | `discord.abc.GuildChannel` |
| `role` | `discord.Role` |
| `attachment` | `discord.Attachment` |

`choices=[...]` turns the option into a fixed-choice list (Literal type).

For commands with multiple arguments, override `parse_message(self, raw)` to
map the raw string to the options dict.

### Sub-groups

Set `group = "case"` and `group_description = "…"` on a command to nest it
as `/mod case create` / `mod.case create`. All commands with the same
`group` string share one `app_commands.Group`.

### Slash-only commands

If a command makes no sense on the message transport (e.g. requires an
attachment option), check `ctx.is_slash` at the top of `execute` and return
early with a plain message. The message router will still route it but will
receive the early-exit response.

---

## Design helpers (`staff.framework.design`)

Standardized Components V2 panels with coloured accent bars and
`### <emoji> Title` headers.

| Helper | Accent |
|--------|--------|
| `design.success(title, description)` | Green |
| `design.error(title, description)` | Red |
| `design.warning(title, description)` | Yellow |
| `design.info(title, description)` | Blue |
| `design.loading(title, description)` | Grey |
| `design.panel(kind, title, description, fields=[], footer=None)` | Any kind |
| `design.permission_denied(locale, reason)` | Red |
| `design.invalid_usage(locale, usage)` | Red |
| `design.make_container(accent)` | Returns a bare `ui.Container` |

All helpers return a `BaseView`. Pass `fields=[{"name": "…", "value": "…"}]`
for additional sections. Use `make_container` + action rows for custom
interactive layouts.

---

## OFFICIAL-only slash sync

The framework cog publishes its slash groups on `bot.staff_slash_groups`.
They are **never** added to the global command tree. During per-guild sync
(`bot.sync_guild_commands`) the groups are added only to guilds whose id is
returned by `bot.get_official_guild_ids()` (the `OFFICIAL` guild attribute).

Mark a server as official: `/dev official <guild_id> add`.

---

## Permissions

Permission checks are centralized in the dispatcher (`staff/framework/cog.py`):

1. `command_type` maps to a role requirement (`utils/staff_permissions.py`).
2. If `permission` is set, the user must also hold that fine-grained node
   (`utils/staff_role_permissions.py`) — devs and super-admin bypass all nodes.

Available permission nodes: `stripe_manage`, `redirect_manage`, `banner_manage`,
`official_manage`, `global_sanction`, `global_enforcement`, `notif_review`,
`notif_lookup`, `broadcast`, `user_lookup`. Nodes let a command live in a non-dev department
(e.g. `/manage`) while still being gated beyond the base role check.

The node check lives in `utils/staff_permissions.has_staff_node(bot, user_id,
node)`, shared with persistent components that must re-derive authorization on
every click (a button outlives the panel that carried it).

---

## Audit logging

Every invocation is logged automatically by the dispatcher via
`utils/staff_logger.py`. Set `sensitive = True` on a command to redact its
arguments (used by `sql` and `jsk`). **Commands must not log themselves.**

---

## Current command inventory

### `/dev` (Developer)

| Command | Message alias | Description |
|---------|--------------|-------------|
| `reload` | `re` | Reload extensions |
| `shutdown` | — | Shutdown the bot |
| `stats` | — | Runtime, Discord, DB and system stats |
| `sql` | — | Execute SQL (sensitive) |
| `jsk` | — | Execute Python in bot context (sensitive) |
| `error` | — | Look up a logged error by code |
| `sync` | — | Sync slash commands |
| `serverlist` | — | List all guilds |
| `disable` | — | Disable a cog |
| `enable` | — | Enable a cog |
| `disabled` | — | List disabled cogs |
| `cogs` | — | List all loaded cogs |
| `presence` | — | Change bot presence |
| `announcements` | — | Manage in-bot announcements |
| `official` | — | Mark/unmark a guild as OFFICIAL |
| `emoji_replace` | — | Replace an application emoji with a new image *(slash-only)* |
| `emoji_preview` | — | Preview an emoji in every Discord context *(slash-only)* |

### `/team` (All staff)

| Command | Message alias | Description |
|---------|--------------|-------------|
| `invite` | — | Get an invite to a guild |
| `server` | `serverinfo` | Server info + DB attributes |
| `user` | `whois` | Everything about a user: Discord, Moddy account, subscription, staff, attributes, cases, notifications. Email / billing / preferences need the `user_lookup` node |
| `mutualserver` | — | Mutual guilds with a user |
| `flex` | — | Public staff verification message |
| `subscription` | — | Check a user's subscription status |
| `role` | — | Create (or inspect) a server's **Moddy Team** role — the one bound to the *Moddy Team* linked role |
| `access` | — | Ask that server's administrators for permissions on the Moddy Team role, with an Accept/Refuse card |
| `ticket` | — | Open a Moddy staff ticket in a server, without going through its own ticket configuration |
| `help` | — | List available commands *(legacy, message-only)* |

The last three are the field commands: they are what a staffer runs *inside*
somebody else's server. All three revolve around one role and grant nothing to
anybody's account — see [LINKED_ROLES.md](LINKED_ROLES.md).

### `/manage` (Management)

| Command | Message aliases | Description |
|---------|----------------|-------------|
| `staff` | `rank`, `setstaff` | Unified rank + setstaff panel |
| `unrank` | — | Remove a staff member |
| `staffinfo` | — | Show a staff member's info |
| `list` | `stafflist` | List all staff by role |
| `badge` | — | Assign a profile badge |
| `subrefresh` | — | Refresh Stripe subscription cache (`stripe_manage`) |
| `stripecreate` | — | Request Stripe customer creation for a user (`stripe_manage`), fire-and-forget via `moddy:dashboard` |
| `stripe cancel/resume/refund/trial` | — | Signed Stripe subscription admin actions (`stripe_manage`), request on `moddy:dashboard` / signed reply on `moddy:bot` (`services/stripe_admin_client.py`) |
| `redirect add/edit/list/delete` | — | Manage redirect links (`redirect_manage`) |
| `banner add/edit/list/info/activate/deactivate/delete` | — | Manage banners (`banner_manage`) |

### `/mod` (Moderator)

| Command | Description |
|---------|-------------|
| `interserver_info` | Info on an interserver channel |
| `interserver_delete` | Delete an interserver channel |
| `case create/view/list/edit/close/note` | Moderation case management |
| `global apply/view/halt/resume/lift/pending` | Moddy-team global sanctions (grouped cases, appeal countdown) |
| `altguard verify/unverify/refusal` | AltGuard gate overrides + refusal details (`altguard_manage`) — see [ALTGUARD.md](ALTGUARD.md) |
| `notif` | Look a notification (or a report filed on it) up by its uuid (`notif_lookup`) — see [NOTIFICATIONS.md](NOTIFICATIONS.md) |

### `/com` (Communication)

| Command | Message alias | Description |
|---------|--------------|-------------|
| `send` | `com.send` | Send a Moddy notification to a user, a server, or a group of them (`broadcast`) |

`/com send <target> <recipient> [dm_owner]` — `target` is `user`, `guild`,
`users` or `guilds`; `recipient` is a Discord id for the single targets, or a
segment (`all`, or an attribute such as `PREMIUM` / `OFFICIAL`) for the group
ones. The wording is written in a **Modal V2** (title, body, optional button
label + URL) and stored as a uniform notification payload. Group sends are
confirmed first, then run in the background with a live progress panel, every
row sharing one `batch_id` — see [NOTIFICATIONS.md](NOTIFICATIONS.md).

### `/support`

No framework commands yet. A legacy placeholder `help` command exists.

---

## Files

```
staff/
├── framework/
│   ├── __init__.py        # Public API re-exports
│   ├── cog.py             # Dispatcher: routing, permissions, logging, error handling
│   ├── command.py         # StaffCommand base + SlashOption + @staff_command registry
│   ├── context.py         # StaffContext (unifies message + slash)
│   ├── registry.py        # Module discovery + slash group builder
│   ├── design.py          # Components V2 panel helpers
│   ├── parsing.py         # parse_user_id / parse_guild_id helpers
│   ├── views.py           # ConfirmView and other shared views
│   └── badges.py          # Staff role badge helpers
├── commands/
│   ├── dev/               # /dev commands (one file each)
│   ├── team/              # /team commands
│   ├── manage/            # /manage commands (+ redirect/ and banner/ sub-dirs)
│   ├── mod/               # /mod commands (+ case/ and global_sanction/ sub-dirs)
│   └── com/               # /com commands (send + its compose modal)
├── base.py                # StaffCommandsCog base (auto-delete tracking)
├── staff_commands.py      # Entry point extension loaded by the bot
├── support_commands.py    # /sup legacy placeholder
└── communication_commands.py  # /com legacy placeholder
```
