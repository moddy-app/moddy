# Auto Role

Module `auto_role` (`modules/auto_role.py`, panel `modules/configs/auto_role_config.py`).

## Config (`guilds.data.modules.auto_role`)

| Key | Type | Meaning |
|---|---|---|
| `member_roles` | `int[]` | Given to every human who joins |
| `bot_roles` | `int[]` | Given to every bot that joins |
| `tag_roles` | `int[]` | Given while a member **displays the server tag**, removed when they take it off |

The module is enabled as soon as one of the three lists is non-empty. Every
role is validated the same way (exists, not `@everyone`, not managed, below
Moddy's top role). AltGuard holds back all three lists until the member passes
the gate (`AltGuardModule._run_auto_role` → `apply_roles`).

## Server tag roles

A member "wears" the tag when `user.primary_guild.id == guild.id` and
`identity_enabled is True` (`utils/guild_tags.py::wears_tag`).

- **On join** — `apply_roles` adds `tag_roles` on top of `member_roles` when the
  newcomer already wears the tag.
- **Afterwards** — `cogs/server_tag_events.py` wraps discord.py's
  `GUILD_MEMBER_UPDATE` parser (the high-level `on_member_update` /
  `on_user_update` only fire for members already cached, and startup chunking
  is off). After discord.py's own parser ran, it calls
  `AutoRoleModule.sync_tag_roles`, which is idempotent: it only sends the
  difference between the member's roles and what the tag says. Consequence:
  the tag roles *mirror* the tag — a moderator who hands one to a member not
  wearing the tag will see it removed on that member's next update.
- Members wearing the tag before the roles were configured get them on their
  next member update (no backfill sweep).

### Does the server have a tag?

The panel only shows the *Server tag roles* section when the server has a tag
(`guild_has_tag`). Discord gives bots no REST route for it
(`GET /guilds/{id}/profile` is user-only) and discord.py does not model it, so
`utils/guild_tags.py` keeps an in-memory answer fed by the same cog:

1. `profile.tag` of `GUILD_CREATE` / `GUILD_UPDATE` when Discord sends it;
2. the `primary_guild.tag` of any member payload wearing this server's tag
   (also gives the tag text shown in the section description);
3. otherwise the `GUILD_TAGS` guild feature ("can set a tag").

Stored `tag_roles` are kept when the section is hidden; they simply never match.
