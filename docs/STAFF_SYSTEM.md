# Staff System

> For how to **build** staff commands (file structure, `StaffCommand`, `SlashOption`,
> design helpers, etc.) see **[STAFF_COMMANDS_FRAMEWORK.md](STAFF_COMMANDS_FRAMEWORK.md)**.
> This document covers the **permission model**, roles, database schema, and
> operational reference.

---

## Command syntax

### Slash commands
Available only on **OFFICIAL** Moddy servers:

```
/dev reload
/team server 1234567890
/manage staff @Jules
/mod case create @user reason
```

### Message commands
Available everywhere Moddy is present (bot mention prefix):

```
@Moddy d.reload all
@Moddy t.server 1234567890
@Moddy m.staff @Jules
@Moddy mod.case create @user reason
```

| Slash group | Message prefix | Who can use |
|-------------|----------------|-------------|
| `/dev` | `d.` | Dev |
| `/team` | `t.` | All staff |
| `/manage` | `m.` | Manager, Supervisor |
| `/mod` | `mod.` | Manager, Supervisor_Mod, Moderator |
| `/support` | `sup.` | Manager, Supervisor_Sup, Support |
| `/com` | `com.` | Manager, Supervisor_Com, Communication |

---

## Staff roles

### Hierarchy (highest → lowest)

| Role | Notes |
|------|-------|
| **Super Admin** | Hard-coded ID `1164597199594852395`. Bypasses all permission checks. |
| **Dev** | Auto-assigned to Discord Developer Portal team members. Access to everything. |
| **Manager** | Full access except dev-only commands. Can assign any non-dev role. |
| **Supervisor** *(Mod / Com / Sup)* | Manages their department. Cannot modify peers or superiors. |
| **Moderator / Communication / Support** | Standard staff in their department. |

### TEAM attribute

Every staff member receives the `TEAM` database attribute automatically when any
role is assigned. It is removed when all roles are removed. The attribute is used
system-wide to identify staff (e.g. for the verified badge system).

Every write to `staff_permissions` must also publish on `moddy:staff`, **after**
the write — that is what tells the backend to republish the `team` and `manager`
booleans Discord's linked roles are built on, and it is the only obligation the
bot has in that pipeline. Both come out of this same table, so one message
covers them. See [LINKED_ROLES.md](LINKED_ROLES.md).

### Role emoji badges

| Role | Badge |
|------|-------|
| Dev | `<:dev_badge:1437514335009247274>` |
| Manager | `<:manager_badge:1437514336355483749>` |
| Supervisor_Mod | `<:mod_supervisor_badge:1437514356135821322>` |
| Supervisor_Com | `<:communication_supervisor_badge:1437514333763535068>` |
| Supervisor_Sup | `<:support_supervisor_badge:1437514347923636435>` |
| Moderator | `<:moderator_badge:1437514357230796891>` |
| Communication | `<:comunication_badge:1437514353304670268>` |
| Support | `<:supportagent_badge:1437514361861177350>` |
| *(any staff)* | `<:moddyteam_badge:1437514344467398837>` |

---

## Permission model

Permission checks happen in two layers (both in `utils/staff_permissions.py`):

1. **Role check** — the command's `command_type` maps to a minimum role.
2. **Node check** — if the command declares a `permission` node, the user must
   hold that node in `role_permissions` (configured via `/manage staff`).

Super-admin and Dev bypass both layers entirely.

### Role permissions (granular nodes)

Nodes are stored per-role in the `role_permissions` JSONB column. A role with
no nodes has no command access even if it matches the type check.

| Node | Grants access to |
|------|-----------------|
| `flex` | `t.flex` |
| `invite` | `t.invite` |
| `serverinfo` | `t.server` |
| `blacklist` | `mod.blacklist` |
| `unblacklist` | `mod.unblacklist` |
| `stripe_manage` | `/manage subrefresh`, `/manage stripecreate`, `/manage stripe cancel/resume/refund/trial` |
| `redirect_manage` | `/manage redirect *` |
| `banner_manage` | `/manage banner *` |
| `official_manage` | `/dev official` |
| `user_lookup` | The personal-data section of `/team user` (email, Stripe customer, stored preferences). The command itself stays open to all staff — only that section is gated |
| `support_request` | Claim / reply to / close a `/bug-report` or configuration-help request ([SUPPORT_REQUESTS.md](SUPPORT_REQUESTS.md)) |
| `broadcast` | `/com send`, `/com beta` |

`"common"` key in `role_permissions` = nodes available to all of the user's roles.

---

## Staff management commands

All available via `/manage` (slash) or `m.` (message).

| Command | What it does |
|---------|-------------|
| `/manage staff @user` | Open the unified rank + setstaff panel |
| `/manage rank @user <role>` | Give one staff role directly (fast path, no panel) |
| `/manage unrank @user` | Remove all roles and the TEAM attribute |
| `/manage staffinfo @user` | Show roles, permissions, join date |
| `/manage list` | List all staff by role |
| `/manage badge @user` | Assign a profile badge |

## Working inside somebody else's server

Three `/team` commands, all built on the Moddy Team roles and granting nothing
to any staff member's account — full contract in
[LINKED_ROLES.md](LINKED_ROLES.md).

Two roles exist: **Moddy Team** (metadata `team`, everybody) and **Moddy Team
Manager** (metadata `manager`, whoever leads the team, who holds both). One role
— the base one — is the default everywhere; the manager role is an opt-in a
server can add later.

| Command | What it does |
|---------|-------------|
| `/team role [guild_id] [team\|manager\|both]` | Create (or re-report) the server's Moddy Team roles — the base one by default — and open the window in which a staffer adds the linked-role requirement themselves |
| `/team role_delete [guild_id] [team\|manager\|both]` | Delete them again, same default, and forget the stored ids |
| `/team access [role]` | Pick the permissions you need; an administrator accepts or refuses on a card; accepting adds them to the named Moddy Team role and to nothing else |
| `/team ticket [reason] [guild_id]` | Open a Moddy staff ticket in the server — a real ticket, outside the server's own ticket configuration |

---

## Audit logging

Every staff command invocation is logged automatically to channel
`1408872408827297952` (guild `1394001780148535387`) via `utils/staff_logger.py`.
Sensitive commands (`sql`, `jsk`) have arguments redacted in the log.

---

## Database schema

```sql
CREATE TABLE staff_permissions (
    user_id          BIGINT PRIMARY KEY,
    roles            JSONB DEFAULT '[]',
    denied_commands  JSONB DEFAULT '[]',
    role_permissions JSONB DEFAULT '{}',
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW(),
    created_by       BIGINT,
    updated_by       BIGINT
);
```

**`roles`** — array of role names:
```json
["Manager", "Dev"]
["Supervisor_Mod"]
["Moderator"]
```

**`role_permissions`** — nodes per role plus `"common"`:
```json
{
  "Moderator": ["blacklist", "unblacklist"],
  "common": ["flex", "invite", "serverinfo"]
}
```

---

## Key implementation files

| File | Purpose |
|------|---------|
| `staff/framework/cog.py` | Dispatcher — routes both transports, checks permissions |
| `staff/framework/command.py` | `StaffCommand` base class + `SlashOption` |
| `staff/framework/context.py` | `StaffContext` — unified message/slash context |
| `staff/framework/registry.py` | Discovery + slash group builder |
| `staff/framework/design.py` | Components V2 panel helpers |
| `staff/commands/manage/staff.py` | `/manage staff` panel (persistent DynamicItem controls) |
| `staff/commands/manage/rank.py` | `/manage rank` — single role assignment |
| `staff/base.py` | `StaffCommandsCog` base (auto-delete tracking for message commands) |
| `utils/staff_permissions.py` | `StaffPermissionManager`, `CommandType`, `StaffRole` |
| `utils/staff_role_permissions.py` | Node definitions per role |
| `utils/staff_logger.py` | Audit log writer |
