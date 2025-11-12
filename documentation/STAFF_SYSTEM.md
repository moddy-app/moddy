# MODDY Staff Permissions System

## Overview

The MODDY staff system provides a comprehensive role-based permission system for managing staff members with different levels of access and responsibilities.

## Command Syntax

All staff commands use the following syntax:

```
<@1373916203814490194> [type].[command] [arguments]
```

### Components:

- `<@1373916203814490194>` - Staff command prefix (bot mention)
- `[type]` - Command type prefix (see below)
- `[command]` - Command name
- `[arguments]` - Optional command arguments

### Command Type Prefixes:

| Prefix | Type | Description | Required Roles |
|--------|------|-------------|----------------|
| `t.` | Team | Commands common to all staff | All staff members |
| `m.` | Management | Staff management commands | Manager |
| `d.` | Developer | Developer commands | Dev |
| `mod.` | Moderator | Moderation commands | Manager, Supervisor_Mod, Moderator |
| `sup.` | Support | Support commands | Manager, Supervisor_Sup, Support |
| `com.` | Communication | Communication commands | Manager, Supervisor_Com, Communication |

## Staff Roles

### Hierarchy

The staff hierarchy (from highest to lowest):

1. **Super Admin** (User ID: 1164597199594852395 - bypasses all permission checks)
2. **Dev** (apart from hierarchy - auto-assigned to Discord dev team members)
3. **Manager**
4. **Supervisor** (Mod/Com/Support)
5. **Staff** (Moderator/Communication/Support)

### Role Descriptions:

#### Super Admin
- Hard-coded user ID: 1164597199594852395
- Bypasses **all** permission checks
- Can assign any role, even those they don't have
- Can modify any staff member, including managers and devs
- Has absolute control over the entire system
- Cannot be restricted or limited in any way

#### Dev
- Automatically assigned to Discord Developer Portal team members
- Has access to all commands
- Can assign any role
- Can modify any staff member

#### Manager
- Can manage all staff members
- Can assign any non-dev role
- Has access to all non-dev commands
- Automatically assigned to Discord dev team members

#### Supervisor (Mod/Com/Support)
- Supervises their respective department
- Can assign staff roles in their department
- Cannot modify managers or other supervisors
- Has access to their department's commands

#### Staff (Moderator/Communication/Support)
- Standard staff members in their department
- Can use their department's commands
- Cannot manage other staff members

## Management Commands (m. prefix)

### m.rank @user

Add a user to the staff team.

**Usage:**
```
<@1373916203814490194> m.rank @user
<@1373916203814490194> m.rank [user_id]
```

**Permission:** Manager or Supervisor

**Example:**
```
<@1373916203814490194> m.rank @JohnDoe
<@1373916203814490194> m.rank 123456789012345678
```

Opens an interactive role selection menu to assign roles to the new staff member.

### m.unrank @user

Remove a user from the staff team.

**Usage:**
```
<@1373916203814490194> m.unrank @user
<@1373916203814490194> m.unrank [user_id]
```

**Permission:** Manager or Supervisor (can only remove staff below their level)

**Example:**
```
<@1373916203814490194> m.unrank @JohnDoe
<@1373916203814490194> m.unrank 123456789012345678
```

Removes all staff roles and permissions from the user and removes the TEAM attribute.

### m.setstaff @user

Manage an existing staff member's roles and permissions.

**Usage:**
```
<@1373916203814490194> m.setstaff @user
<@1373916203814490194> m.setstaff [user_id]
```

**Permission:** Manager or Supervisor (can only modify staff below their level)

**Example:**
```
<@1373916203814490194> m.setstaff @JohnDoe
<@1373916203814490194> m.setstaff 123456789012345678
```

**Features:**
- **Role-Based Permission System:** Assign roles to staff members, but roles have no power by default
- **Granular Permissions:** For each assigned role, select specific permissions from dropdown menus
- **Common Permissions:** Set permissions that apply to all roles (e.g., flex, invite, serverinfo)
- **Role-Specific Permissions:** Configure unique permissions for each role:
  - **Moderator:** blacklist, unblacklist, userinfo, guildinfo
  - **Support:** ticket operations and management
  - **Communication:** announce, broadcast
  - **Manager:** rank, unrank, setstaff, stafflist, staffinfo
- **Interactive Components V2 Interface:** All configuration happens through dropdown menus and buttons
- **Real-time Updates:** The interface updates automatically as you configure roles and permissions
- **Save/Cancel Options:** Review all changes before saving

**How it works:**
1. Run `m.setstaff @user` to open the permissions management interface
2. Select roles from the first dropdown menu
3. Once roles are selected, dropdown menus appear for:
   - Common permissions (available to all roles)
   - Specific permissions for each assigned role
4. Configure permissions by selecting from the dropdown menus
5. Click "Save Changes" to apply the new configuration

**Important:** Roles without assigned permissions have no command access. You must explicitly grant permissions for each role.

### m.stafflist

List all staff members organized by role.

**Usage:**
```
<@1373916203814490194> m.stafflist
```

**Permission:** Manager or Supervisor

### m.staffinfo [@user]

Show detailed information about a staff member. If no user is mentioned, shows your own information.

**Usage:**
```
<@1373916203814490194> m.staffinfo @user
<@1373916203814490194> m.staffinfo [user_id]
<@1373916203814490194> m.staffinfo
```

**Permission:** All staff members

**Features:**
- Shows roles with emoji badges
- Displays command restrictions
- Shows staff join date and last update

## Team Commands (t. prefix)

Available to all staff members.

### t.help

Show available commands based on your permissions.

**Usage:**
```
<@1373916203814490194> t.help
```

### t.invite [server_id]

Get an invite link to a server where MODDY is present.

**Usage:**
```
<@1373916203814490194> t.invite 1234567890
```

Creates a 7-day invite with 5 max uses. Display shows only the server name and invite link using Components V2.

### t.serverinfo [server_id]

Get detailed information about a server where MODDY is present.

**Usage:**
```
<@1373916203814490194> t.serverinfo 1234567890
```

Shows:
- Basic server information
- Member statistics
- Channel counts
- Boost status
- Server features

### t.mutualserver [user_id]

View mutual servers shared with a user and their permissions in those servers.

**Usage:**
```
<@1373916203814490194> t.mutualserver 123456789012345678
```

Shows:
- User information
- List of mutual servers (up to 10)
- User's top role in each server
- User's key permissions in each server (Administrator, Manage Server, Manage Roles, etc.)

### t.user [user_id]

Get detailed information about a user including database attributes.

**Usage:**
```
<@1373916203814490194> t.user 123456789012345678
```

Shows:
- Basic user information (ID, username, display name, bot status)
- Account creation date
- Database attributes
- Number of shared servers
- First seen date

### t.server [server_id]

Get detailed information about a server including database attributes.

**Usage:**
```
<@1373916203814490194> t.server 1234567890
```

Shows:
- Basic server information
- Member statistics
- Channel counts
- Role count
- Boost status
- Database attributes
- Server features

### t.flex

Prove you are a member of the Moddy team. This command sends a verification message to prevent identity theft.

**Usage:**
```
<@1373916203814490194> t.flex
```

**Permission:** All staff members

**Features:**
- Uses Components V2 for display
- Shows your role (simplified for public display):
  - Developer → "developer"
  - Manager → "manager"
  - Moderation Supervisor → "moderation supervisor"
  - Communication/Communication Supervisor → "member"
  - Support/Support Supervisor → "support agents"
  - Moderator → "moderator"
- Deletes the command message after sending
- Includes links to support and documentation

## Developer Commands (d. prefix)

Exclusive to Discord Dev Portal team members.

### d.reload [extension]

Reload bot extensions. Use "all" to reload everything.

**Usage:**
```
<@1373916203814490194> d.reload all
<@1373916203814490194> d.reload staff.team_commands
```

### d.shutdown

Shutdown the bot.

**Usage:**
```
<@1373916203814490194> d.shutdown
```

### d.stats

Show comprehensive bot statistics including uptime, resources, and database stats.

**Usage:**
```
<@1373916203814490194> d.stats
```

### d.sql [query]

Execute SQL queries directly on the database. Requires confirmation for dangerous operations.

**Usage:**
```
<@1373916203814490194> d.sql SELECT * FROM users LIMIT 10
```

### d.jsk [code]

Execute Python code directly in the bot's runtime environment. Supports async/await and has access to bot context.

**Usage:**
```
<@1373916203814490194> d.jsk print("Hello World")
<@1373916203814490194> d.jsk return len(bot.guilds)
<@1373916203814490194> d.jsk await message.channel.send("Test")
```

**Available Variables:**
- `bot` - Bot instance
- `message` - Message object
- `channel` - Current channel
- `author` - Command author
- `guild` - Current guild
- `db` - Database instance
- `discord`, `commands`, `asyncio`, `datetime`, `timezone` - Common modules

**Code Blocks:**
You can use Python code blocks for multi-line code:
```
<@1373916203814490194> d.jsk ```python
guilds = bot.guilds
print(f"Bot is in {len(guilds)} guilds")
for guild in guilds[:5]:
    print(f"- {guild.name}")
\```
```

### d.error [error_code]

Get detailed information about a logged error by its error code.

**Usage:**
```
<@1373916203814490194> d.error A1B2C3D4
```

Shows:
- Error code and type
- Error message
- File and line number where error occurred
- Context information (command, user, server)
- Full traceback
- Timestamp of when the error occurred
- Source (Cache or Database)

The command searches for the error in both the in-memory cache (last 100 errors) and the database.

## Moderator Commands (mod. prefix)

Available to Manager, Supervisor_Mod, and Moderator.

### mod.blacklist @user [reason]

Blacklist a user from using MODDY.

**Usage:**
```
<@1373916203814490194> mod.blacklist @user Spam and abuse
```

### mod.unblacklist @user [reason]

Remove a user from the blacklist.

**Usage:**
```
<@1373916203814490194> mod.unblacklist @user Appeal accepted
```

## Support Commands (sup. prefix)

Available to Manager, Supervisor_Sup, and Support. *In development.*

## Communication Commands (com. prefix)

Available to Manager, Supervisor_Com, and Communication. *In development.*

## Permission Rules

### Hierarchy Rules:

1. **Super Admin (User ID: 1164597199594852395):**
   - Bypasses **ALL** permission checks
   - Can assign any role, even Manager and Dev roles
   - Can modify any staff member, including themselves
   - Cannot be restricted by denied commands
   - Has absolute control over the entire system

2. **Supervisors and Managers cannot:**
   - Assign permissions they don't have (except Super Admin)
   - Modify permissions of staff at the same level or above
   - A Supervisor cannot modify another Supervisor or a Manager

3. **Developers (Discord Dev Team):**
   - Automatically get Manager + Dev roles
   - Can modify anyone (except other devs are always Manager+Dev, and Super Admin can modify anyone)
   - Cannot be removed from Manager+Dev roles

4. **Command Restrictions:**
   - Even if you have access to a command type, specific commands can be denied
   - Denied commands are managed via `m.setstaff`
   - Super Admin bypasses all command restrictions

### TEAM Attribute:

All staff members automatically receive the `TEAM` attribute in the database. This attribute is:
- Automatically added when roles are assigned via `m.rank`
- Automatically removed when all roles are removed
- Used to identify staff members system-wide

## Database Schema

### staff_permissions Table

```sql
CREATE TABLE staff_permissions (
    user_id BIGINT PRIMARY KEY,
    roles JSONB DEFAULT '[]'::jsonb,
    denied_commands JSONB DEFAULT '[]'::jsonb,
    role_permissions JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by BIGINT,
    updated_by BIGINT
)
```

### Roles Format:

```json
["Manager", "Dev"]
["Supervisor_Mod"]
["Moderator"]
```

### Role Permissions Format:

The `role_permissions` field stores permissions for each role and common permissions:

```json
{
  "Moderator": ["blacklist", "unblacklist", "userinfo", "guildinfo"],
  "Support": ["ticket_view", "ticket_close"],
  "common": ["flex", "invite", "serverinfo"]
}
```

**How it works:**
- Each role has its own array of permissions
- The `"common"` key contains permissions available to all assigned roles
- Roles without permissions or with an empty array have no command access
- Permissions must be explicitly granted through `m.setstaff`

### Denied Commands Format:

```json
["mod.ban", "mod.kick", "t.invite"]
```

**Note:** The denied commands system is deprecated in favor of the role permissions system.

## UI/UX Design

### Display Format:

Staff commands use **Discord Components V2** (LayoutView, Container, TextDisplay, Separator) for modern structured messages.

Features:
- Clean, structured layout using Components V2
- Error, success, info, and warning message helpers
- Interactive buttons and select menus (still using embeds for compatibility)
- Messages **reply** to the command message (not sent in channel)
- Messages are **NOT automatically deleted** - they remain visible

### Message Behavior:

- All staff command responses use `message.reply()` instead of `message.channel.send()`
- `mention_author=False` is used to avoid unnecessary mentions
- **Auto-deletion on command removal:** When you delete the command message, the bot's response is automatically deleted as well
  - This feature is implemented through a centralized `StaffBaseCog` base class in `utils/staff_base.py`
  - All staff command cogs inherit from this base class, ensuring consistent behavior
  - The system works automatically for all commands - no manual tracking required
  - Covers all command types: team (t.), management (m.), developer (d.), moderator (mod.), support (sup.), and communication (com.)
- This keeps channels clean while allowing staff to remove accidental or outdated commands
- No footers are displayed (e.g., no "Requested by..." text)

### Language:

All staff commands are in **English only** and do **NOT** use the i18n system.

## Implementation Details

### Files:

- `/utils/staff_permissions.py` - Permission manager and role hierarchy
- `/utils/staff_base.py` - Base cog class with automatic message deletion functionality
- `/staff/staff_manager.py` - Management commands (m. prefix)
- `/staff/team_commands.py` - Team commands (t. prefix)
- `/staff/dev_commands.py` - Developer commands (d. prefix)
- `/staff/moderator_commands.py` - Moderator commands (mod. prefix)
- `/staff/support_commands.py` - Support commands (sup. prefix)
- `/staff/communication_commands.py` - Communication commands (com. prefix)
- `/database.py` - Database methods for staff permissions

### Key Classes and Constants:

- `StaffPermissionManager` - Main permission checking logic
  - `STAFF_PREFIX` - Bot mention: `<@1373916203814490194>`
  - `SUPER_ADMIN_ID` - Super admin user ID: `1164597199594852395`
- `StaffBaseCog` - Base cog class for all staff commands (in `utils/staff_base.py`)
  - Provides automatic message deletion when command is deleted
  - `reply_and_track()` - Reply to a message and automatically track for deletion
  - `send_and_track()` - Send a message to channel and track for deletion
  - All staff cogs inherit from this class
- `StaffRole` - Enum of available roles
- `CommandType` - Enum of command type prefixes
- Components V2 helpers in `utils/components_v2.py`:
  - `create_error_message()` - Create error messages
  - `create_success_message()` - Create success messages
  - `create_info_message()` - Create info messages
  - `create_warning_message()` - Create warning messages
  - `create_staff_info_message()` - Create staff information displays

### Auto-initialization:

When the bot starts:
1. Staff permissions system is initialized
2. Discord dev team members are fetched
3. Dev team members automatically get DEVELOPER attribute + Manager+Dev roles

## Migration from Old System

The old system used:
- Simple `is_developer()` check
- `cog_check()` in each staff cog

The new system:
- Role-based permissions with hierarchy
- Granular command access control
- Interactive management UI
- Database-backed permissions

### Backward Compatibility:

- Old staff commands (developer-only) still work
- Developers automatically get all permissions
- Can be migrated gradually to new command syntax

## Examples

### Adding a Moderator:

```
<@1373916203814490194> m.rank @NewMod
```

Select "Moderator" role in the menu, click Confirm.

### Managing a Staff Member:

```
<@1373916203814490194> m.setstaff @ExistingStaff
```

Click "Edit Roles" to change roles, or "Manage Command Restrictions" to deny specific commands.

### Getting a Server Invite:

```
<@1373916203814490194> t.invite 1234567890
```

### Checking Staff List:

```
<@1373916203814490194> m.stafflist
```

Shows all staff members organized by role.

## Security Considerations

1. **Bot Mention Prefix:** The `<@1373916203814490194>` prefix prevents accidental command execution
2. **Super Admin:** User ID `1164597199594852395` has absolute control and bypasses all checks (hard-coded)
3. **Hierarchy Enforcement:** Lower-level staff cannot modify higher-level staff (except Super Admin)
4. **Command Denial:** Specific commands can be denied even if role allows them (not applicable to Super Admin)
5. **Audit Trail:** All permission changes are logged with `created_by` and `updated_by`
6. **Dev Team Lock:** Discord dev team members cannot be removed from Manager+Dev roles
7. **Database Failsafe:** If database is unavailable, only Super Admin and Discord dev team members can use commands

## Staff Role Emoji Badges

All staff roles are displayed with custom emoji badges for visual identification:

### Team Badges
- <:dev_badge:1437514335009247274> **Dev** - Developer badge
- <:manager_badge:1437514336355483749> **Manager** - Manager badge
- <:mod_supervisor_badge:1437514356135821322> **Supervisor_Mod** - Moderation Supervisor badge
- <:communication_supervisor_badge:1437514333763535068> **Supervisor_Com** - Communication Supervisor badge
- <:support_supervisor_badge:1437514347923636435> **Supervisor_Sup** - Support Supervisor badge
- <:moderator_badge:1437514357230796891> **Moderator** - Moderator badge
- <:comunication_badge:1437514353304670268> **Communication** - Communication badge
- <:supportagent_badge:1437514361861177350> **Support** - Support Agent badge
- <:moddyteam_badge:1437514344467398837> **General Team Badge** - Moddy Team member

These badges are automatically displayed in:
- `m.setstaff` - Staff management interface
- `m.staffinfo` - Staff member information
- Any other staff-related displays

## Recent Changes (Latest Update)

**Automatic Message Deletion System Overhaul:**
- **Centralized Base Class:** All staff command cogs now inherit from `StaffBaseCog` in `utils/staff_base.py`
- **Robust Auto-Deletion:** When a staff command message is deleted, the bot's response is automatically deleted
  - Works consistently across ALL staff commands (team, management, developer, moderator, support, communication)
  - No manual tracking required in individual command handlers
  - Future commands automatically benefit from this system
- **Helper Methods:** New `reply_and_track()` and `send_and_track()` methods for easy integration
- **Developer Commands Fixed:** Developer commands (d. prefix) now properly support auto-deletion
- **Support/Communication Commands Fixed:** These commands now use proper reply tracking instead of channel.send()

**Previous Updates:**

**New Team Commands Added:**
- **t.mutualserver [user_id]:** View mutual servers shared with a user and their permissions in those servers
- **t.user [user_id]:** Get detailed information about a user including database attributes
- **t.server [server_id]:** Get detailed information about a server including database attributes

**New Developer Command Added:**
- **d.error [error_code]:** Get detailed information about a logged error from cache or database

**Commands Removed:**
- **mod.userinfo:** Removed (replaced by t.user)
- **mod.guildinfo:** Removed (replaced by t.server)
- **t.userinfo:** Does not exist (never implemented)

**t.invite Command Updated:**
- Simplified display to show only server name and invite link using Components V2
- No longer shows detailed server information or invite details

**Previous Updates:**

**Major Permission System Overhaul:**
- **Role-Based Permissions:** Completely redesigned permission system where roles have no power by default
- **Granular Control:** Each role must have permissions explicitly granted through dropdown menus
- **Common Permissions:** New system for permissions that apply to all roles (flex, invite, serverinfo)
- **Role-Specific Permissions:** Each role has its own set of available permissions:
  - Moderator: blacklist, unblacklist, userinfo, guildinfo
  - Support: ticket operations
  - Communication: announce, broadcast
  - Manager: staff management commands
- **Interactive Interface:** New Components V2 interface with dynamic dropdown menus
- **Real-time Updates:** Interface automatically updates when roles are modified

**Message Behavior Improvements:**
- **Auto-deletion:** When you delete a command message, the bot's response is automatically deleted
- **Clean Interface:** Removed all footers ("Requested by...", "Removed by...", etc.)
- **Improved UX:** Commands now feel more natural and less cluttered

**Database Changes:**
- Added `role_permissions` JSONB column to `staff_permissions` table
- Stores permissions for each role and common permissions
- Automatic migration on bot startup

**Code Architecture:**
- New `utils/staff_role_permissions.py` for permission definitions
- New `StaffPermissionsManagementView` class for managing permissions
- Message deletion tracking system in all staff command cogs

**Previous Updates:**

**User Identification Improvements:**
- All management commands now support both user mentions and direct user IDs
- Bot mention is automatically excluded from user identification
- Improved parsing: `<@1373916203814490194> m.setstaff @user` or `<@1373916203814490194> m.setstaff 123456789`

**New Commands:**
- Added `m.unrank` command to remove users from staff team
- Command properly removes all roles, permissions, and TEAM attribute

**Display Enhancements:**
- All staff role displays now show emoji badges
- `t.flex` command updated to use Components V2
- Role names simplified for public display in verification messages

**Bug Fixes:**
- Fixed embed/Components V2 conflict in `m.setstaff`
- Removed duplicate emojis from staff displays
- Improved error handling for user identification

## Future Enhancements

Potential additions:
- Department-specific commands (mod., sup., com. prefixes)
- Permission templates for quick role assignment
- Audit log viewer
- Bulk staff management
- Permission inheritance
- Temporary permissions/roles
