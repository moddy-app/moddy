# Discord permissions — the exact rules Moddy relies on

> Source: Discord's official API docs (docs.discord.com, fetched 2026-11-05).
> Quotes are verbatim. Anything marked **[observed]** was tested live against
> Discord (PR #443); anything marked **[assumption]** is NOT in the docs and is
> the safe reading Moddy uses. Read this before writing any permission check.
>
> The golden rule: **never check stricter than Discord, never check in a
> different place than Discord.** A check stricter than Discord is a feature
> that only works with Administrator (Top.gg declines, see
> `docs/sessions/2026-11-02_tickets-category-permissions.md`).

## 1. How effective permissions are computed

From *Topics → Permissions → Permission Overwrites*:

1. Base permissions given to @everyone are applied at a guild level
2. Permissions allowed to a user by their roles are applied at a guild level
3. Overwrites that deny permissions for @everyone are applied at a channel level
4. Overwrites that allow permissions for @everyone are applied at a channel level
5. Overwrites that deny permissions for specific roles are applied at a channel level
6. Overwrites that allow permissions for specific roles are applied at a channel level
7. Member-specific overwrites that deny permissions are applied at a channel level
8. Member-specific overwrites that allow permissions are applied at a channel level

- The guild **owner** and anyone with **ADMINISTRATOR** get ALL permissions and
  bypass every overwrite.
- Role overwrites are OR-ed together: "permissions do not obey the role
  hierarchy" — a role allowing VIEW_CHANNEL beats another role denying it,
  whatever their positions.
- discord.py 2.7.1 `GuildChannel.permissions_for(member)` implements exactly
  this, plus the timeout mask. `Member.guild_permissions` = roles only (owner,
  admin and timeout considered, **no overwrites**).

### Implicit permissions (discord.py does NOT apply these)

"Denying a user or a role `VIEW_CHANNEL` on a channel implicitly denies other
permissions on the channel." — "Denying `SEND_MESSAGES` implicitly denies
`MENTION_EVERYONE`, `SEND_TTS_MESSAGES`, `ATTACH_FILES`, and `EMBED_LINKS`."
For voice/stage: "denying the `CONNECT` permission also implicitly denies
other permissions such as `MANAGE_CHANNELS`."

**[observed]** `permissions_for` keeps the other bits set when `view_channel`
is denied. So: **if `view_channel` is missing in a channel, treat every
permission in that channel as missing** (and report only "View Channel" to the
admin — granting it is what fixes it).

### Timed-out members (the bot itself can be timed out)

"Timed out members will temporarily lose all permissions except
`VIEW_CHANNEL` and `READ_MESSAGE_HISTORY`. Owners and admin users with
`ADMINISTRATOR` permissions are exempt." discord.py applies this mask in both
`guild_permissions` and `permissions_for`.

### Threads

"Threads inherit permissions from the parent channel … with one exception: The
`SEND_MESSAGES` permission is not inherited; users must have
`SEND_MESSAGES_IN_THREADS`". "Users must have the `VIEW_CHANNEL` permission to
view *any* threads in the channel". Private threads: you must be invited, or
have MANAGE_THREADS. In forum/media channels "creating a thread … only
requires the `SEND_MESSAGES` permission" (`CREATE_PUBLIC_THREADS` is ignored).

### Two-factor requirement

KICK_MEMBERS, BAN_MEMBERS, ADMINISTRATOR, MANAGE_CHANNELS, MANAGE_GUILD,
MANAGE_MESSAGES, MANAGE_ROLES, MANAGE_WEBHOOKS, MANAGE_GUILD_EXPRESSIONS,
MANAGE_THREADS, VIEW_CREATOR_MONETIZATION_ANALYTICS "require the owner account
to use two-factor authentication when used on a guild that has server-wide 2FA
enabled." → a bot can hold the permission and still get 403 on such a server.
Always handle `Forbidden` even after a passing pre-check.

### Names in the Discord client

"'Manage Permissions' refers to MANAGE_ROLES, … 'Timeout Members' refers to
MODERATE_MEMBERS." In a category/channel settings screen, MANAGE_ROLES is
labelled **Manage Permissions** — use that wording when telling an admin what
to change *on a channel*.

## 2. Role hierarchy

"A bot can grant roles to other users that are of a lower position than its
own highest role." "A bot can edit roles of a lower position than its highest
role, but it can only grant permissions it has to those roles." "A bot can only
kick, ban, and edit nicknames for users whose highest role is lower than the
bot's highest role." Nobody can act on the guild owner. Managed roles
(bot/integration/booster) cannot be assigned. Timeout: "Will throw a 403 error
if the user has the ADMINISTRATOR permission or is the owner of the guild".

## 3. Per endpoint — what is checked, and WHERE

| Action | Docs say | Checked where |
|---|---|---|
| Create Message | "must have the `SEND_MESSAGES` permission"; reply → "must have the `READ_MESSAGE_HISTORY` permission" (error 160002) | channel (thread: SEND_MESSAGES_IN_THREADS) |
| Get Channel Message / fetch_message | "`VIEW_CHANNEL` and `READ_MESSAGE_HISTORY`" (+CONNECT for voice) | channel |
| Get Channel Messages / history | VIEW_CHANNEL; "missing `READ_MESSAGE_HISTORY` … no messages will be returned" | channel |
| Edit Message | own messages: no permission; others: only `flags`, with MANAGE_MESSAGES | channel |
| Delete Message (not own) | MANAGE_MESSAGES | channel |
| Bulk Delete | MANAGE_MESSAGES | channel |
| Create Reaction | READ_MESSAGE_HISTORY, + ADD_REACTIONS "if nobody else has reacted … using this emoji" | channel |
| Delete others' reactions / all reactions | MANAGE_MESSAGES | channel |
| Pin / Unpin | **`PIN_MESSAGES`** (split from MANAGE_MESSAGES, Feb 2026) | channel |
| Crosspost | SEND_MESSAGES (own) / + MANAGE_MESSAGES (others) | channel |
| Create Guild Channel | MANAGE_CHANNELS. "If setting permission overwrites, only permissions your bot has **in the guild** can be allowed/denied. Setting `MANAGE_ROLES` permission in channels is only possible for guild administrators." | MANAGE_CHANNELS in the parent category **[assumption]**; overwrites vs **guild** perms; MANAGE_ROLES at **guild** level **[observed]**; the bot must see the category **[observed]** |
| Modify Channel (name, topic, slowmode…) | "Requires the `MANAGE_CHANNELS` permission for the guild" | **[assumption]** channel (overwrites apply; safe side) |
| Modify Channel / Edit Channel Permissions (overwrites) | MANAGE_ROLES. "Only permissions your bot has in the guild or parent channel (if applicable) can be allowed/denied (unless your bot has a `MANAGE_ROLES` overwrite in the channel)." | MANAGE_ROLES: guild or channel overwrite |
| Delete Channel | MANAGE_CHANNELS ("for the guild"); thread: MANAGE_THREADS | channel **[assumption]** |
| Move channel to another parent | "MANAGE_CHANNELS permission on that channel and on the destination … and the current user must be able to view the channel" | channel + destination |
| Create Webhook / Get Channel Webhooks / Modify / Delete | MANAGE_WEBHOOKS. Max 15 webhooks per channel (error 30007) | channel |
| Execute Webhook (with token) | no bot permission | — |
| Follow Announcement Channel | "MANAGE_WEBHOOKS permission in the target channel" | target channel |
| Create Channel Invite | CREATE_INSTANT_INVITE | channel |
| Delete Invite | MANAGE_CHANNELS on the invite's channel, or MANAGE_GUILD | channel / guild |
| Get Guild Invites | MANAGE_GUILD **or** VIEW_AUDIT_LOG (metadata/uses only with MANAGE_GUILD) | guild |
| Start Thread (from message / without) | CREATE_PUBLIC_THREADS / CREATE_PRIVATE_THREADS | channel |
| Start Thread in Forum/Media | SEND_MESSAGES only | forum channel |
| Edit thread: archived=false | "only requires the current user has already been added to the thread"; locked → creator or MANAGE_THREADS | thread |
| Edit thread: name, auto_archive | MANAGE_THREADS or thread creator | thread |
| Edit thread: rate_limit_per_user, locked | MANAGE_THREADS | thread |
| Add Thread Member | "ability to send messages in the thread"; thread not archived (error 50083) | thread |
| Remove Thread Member | MANAGE_THREADS, or creator of a private thread | thread |
| Delete Thread | MANAGE_THREADS | thread |
| Add / Remove Member Role | MANAGE_ROLES + hierarchy (§2) | guild |
| Modify Member: nick | MANAGE_NICKNAMES + hierarchy | guild |
| Modify Member: timeout | MODERATE_MEMBERS; 403 on admin/owner; max 28 days | guild |
| Modify Member: move voice | MOVE_MEMBERS + CONNECT to the target channel | guild + channel |
| Kick | KICK_MEMBERS + hierarchy | guild |
| Ban / Unban | BAN_MEMBERS (+ hierarchy for ban) | guild |
| Bulk Ban | BAN_MEMBERS **and** MANAGE_GUILD | guild |
| Get Guild Bans / Ban | BAN_MEMBERS **or** VIEW_AUDIT_LOG | guild |
| Create / Modify / Delete Role | MANAGE_ROLES; only below the bot's top role; only permissions the bot has. Max 250 roles (30005) | guild |
| Modify Guild | MANAGE_GUILD | guild |
| Modify Current Member (bot nick/avatar/banner/bio) | nick: CHANGE_NICKNAME **[assumption]**; per-guild avatar/banner/bio: no extra permission **[assumption]** | guild |
| Audit log | VIEW_AUDIT_LOG | guild |
| Create emoji | CREATE_GUILD_EXPRESSIONS; edit/delete others' → MANAGE_GUILD_EXPRESSIONS | guild |
| List Guild Members | GUILD_MEMBERS privileged intent | — |

Error codes to handle: `50013` missing permissions, `50001` missing access
(cannot see the channel), `10003` unknown channel, `10007` unknown member,
`10008` unknown message, `10011` unknown role, `10062` unknown interaction,
`50083` archived thread, `160002` reply without READ_MESSAGE_HISTORY, `30005`
role limit, `30007` webhook limit.

## 4. Interactions

- `app_permissions` on every interaction is the "Bitwise set of permissions
  the app has in the source location of the interaction" — computed by
  Discord, the most reliable value available (`interaction.app_permissions`).
- Interaction responses and followups are sent through the interaction token,
  not as the bot in the channel **[assumption, widely observed]**: they work
  without SEND_MESSAGES. A command reply must use `interaction.response` /
  `followup`, never `interaction.channel.send`.
- User-installed commands in a server where Moddy is not a member:
  `interaction.guild` is partial / `guild.me` is None; "Apps are limited to 5
  followup messages per interaction if it was initiated from a user-installed
  app and isn't installed in the server".
- Interaction payloads are never obfuscated (§5): "an app always receives full
  channel data in interaction payloads for channels it can act on."

## 5. Channel obfuscation — **in effect from November 16, 2026**

"Starting November 16, 2026, channels will be obfuscated if your bot doesn't
have permissions to view them."

- Over the Gateway, such a channel is still dispatched but "The channel's
  `name` becomes `"___hidden___"`, other sensitive fields are nulled or
  reduced, a new `CHANNEL_OBFUSCATED` flag (`1 << 17`) is set on the channel's
  `flags`, and the `permission_overwrites` will contain a single permission
  overwrite denying `VIEW_CHANNEL` for the guild's `@everyone` role."
- "`id`, `type`, `position`, and `parent_id` will not be obfuscated … Other
  fields should not be relied on." Detect with the flag, "Do not rely on
  inspecting `name`".
- "Channel categories are viewable if any of their child channels are
  viewable."
- `GET /guilds/{id}/channels` omits them entirely.
- When the bot regains access, a Channel Update carries the full data.

Consequences for Moddy:
- `channel.permissions_for(guild.me)` on an obfuscated channel → missing
  `view_channel` (correct for the bot).
- `channel.permissions_for(<another member or role>)` on an obfuscated channel
  is **meaningless** (the real overwrites are gone). Never compute other
  people's access to a channel the bot cannot see.
- Never show `channel.name` of a channel the bot cannot see — use the
  mention `<#id>` (the client resolves it for the reader).
- Use `utils.bot_permissions.is_obfuscated(channel)` (flag `1 << 17`).

## 6. Moddy conventions derived from the above

- Use `utils/bot_permissions.py` helpers; they implement §1 (view_channel
  implies everything, threads use SEND_MESSAGES_IN_THREADS):
  `channel_missing()` / `can_act_in()` for one channel,
  `CHANNEL_SCOPED_PERMISSIONS` / `GUILD_SCOPED_PERMISSIONS` +
  `analyze_requirements()` / `format_gaps()` for a module (what `/config`
  uses), `role_assignment_error()` for roles Moddy must give (hierarchy,
  managed, @everyone), `is_obfuscated()` / `bot_is_timed_out()`.
- Guild-level requirement only for guild-level actions (roles, kick, ban,
  timeout, nicknames, audit log, manage_guild). Channel-scoped actions
  (send, webhooks, reactions, pins, threads, delete messages, slowmode) are
  checked **in the channel**, so a permission granted only on that channel is
  enough.
- Automatic / default-enabled features degrade silently (log at debug/info).
  Admin-facing features name exactly what to grant **and where**.
- Always catch `discord.Forbidden` / `discord.NotFound` around the call even
  after a passing pre-check (2FA, races, hierarchy changes, cache staleness).
