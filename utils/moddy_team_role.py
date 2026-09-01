"""The **Moddy Team** role — the one role Moddy staff ever hold in a server.

A server that needs the Moddy team on site (a support session, a migration, a
bug reproduced live) creates one role, binds it to Moddy's *Équipe Moddy*
linked-role requirement, and Discord hands it out on its own to whoever is
actually staff at that moment. Nothing else in the bot ever gives a staff
member permissions in a server: `/team access` grants to **this role only**,
and `/team ticket` opens its channel to **this role only**.

Two consequences worth stating out loud:

- **A promotion or a destitution is enough.** The backend republishes the
  ``team`` boolean (see ``services/staff_events.py``), Discord adds or removes
  the role by itself, everywhere at once. A server never has to clean up after
  us.
- **The bot must not assign the role by hand.** Discord owns that half; a
  manual grant would be a duplicate Discord removes on its next check.

### Why a human has to do the linking

No API attaches a linked-role requirement to a role. The official one has no
field for it, and the undocumented route the Discord client uses
(``PUT /guilds/{id}/roles/{id}/connections/configuration``) answers a bot token
with ``20001 — Bots cannot use this endpoint``. That was established against the
live API, not assumed; see docs/LINKED_ROLES.md.

So the step belongs to a human holding *Manage Roles*, and `/team role` lends
that permission to the staffer for thirty seconds rather than sending them to
find an administrator — see :mod:`services.team_link_session`. This module keeps
only what is genuinely the role's business: finding it, creating it, and
reporting whether the requirement is there (:func:`is_linked`).

The role id is remembered in ``guilds.data.moddy_team.role_id`` so a rename
never loses it; the name lookup is only a fallback for a role created before
the bot knew about it (or by hand).
"""

from __future__ import annotations

import logging
from typing import Optional

import discord

logger = logging.getLogger('moddy.moddy_team_role')

#: Name used when the bot creates the role. Servers may rename it freely — the
#: stored id is what the bot works from afterwards.
TEAM_ROLE_NAME = "Moddy Team"

#: Moddy blurple, so the role reads as ours in the member list.
TEAM_ROLE_COLOR = 0x5865F2

#: Where the id lives in ``guilds.data``.
STORE_PATH = "moddy_team.role_id"

#: Where a member links their Discord account to their Moddy one. Everybody who
#: has ever signed in to moddy.app is already linked (the dashboard asks for
#: ``role_connections.write`` on the first login); this page is for the rest.
LINKED_ROLES_URL = "https://api.moddy.app/linked-roles"


def _as_int(value) -> Optional[int]:
    """JSON has no 64-bit integer — a stored snowflake may come back as text."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def stored_role_id(bot, guild_id: int) -> Optional[int]:
    """The role id remembered for this guild, if any."""
    if not getattr(bot, 'db', None):
        return None
    try:
        guild_data = await bot.db.get_guild(guild_id)
    except Exception:  # noqa: BLE001 — a lookup failure is not an answer
        logger.warning("Could not read the Moddy Team role of guild %s",
                       guild_id, exc_info=True)
        return None
    return _as_int((guild_data.get('data') or {}).get('moddy_team', {}).get('role_id'))


async def remember_role(bot, guild_id: int, role_id: Optional[int]) -> None:
    """Store (or forget, with ``None``) the guild's Moddy Team role id."""
    if not getattr(bot, 'db', None):
        return
    try:
        await bot.db.update_guild_data(guild_id, STORE_PATH, role_id)
    except Exception:  # noqa: BLE001 — the name lookup still finds the role
        logger.warning("Could not store the Moddy Team role of guild %s",
                       guild_id, exc_info=True)


async def find_team_role(bot, guild: discord.Guild) -> Optional[discord.Role]:
    """The guild's Moddy Team role: the stored id first, then the name.

    A stored id that no longer resolves is forgotten on the spot, so a role
    deleted in Discord does not leave `/team access` pointing at a ghost.
    """
    role_id = await stored_role_id(bot, guild.id)
    if role_id:
        role = guild.get_role(role_id)
        if role:
            return role
        await remember_role(bot, guild.id, None)

    lowered = TEAM_ROLE_NAME.lower()
    for role in guild.roles:
        if role.name.lower() == lowered and not role.managed:
            # Found by name: remember it, so a later rename stays resolvable.
            await remember_role(bot, guild.id, role.id)
            return role
    return None


async def create_team_role(bot, guild: discord.Guild, *,
                           actor: Optional[discord.abc.User] = None) -> discord.Role:
    """Create the Moddy Team role, with **no permissions at all**.

    Permissions are added later, one request at a time, by an administrator
    accepting a `/team access` card. Creating the role pre-loaded would be the
    opposite of that promise.

    Raises :class:`discord.Forbidden` when the bot cannot manage roles, and
    :class:`discord.HTTPException` on anything else — both are the caller's to
    render.
    """
    who = f" at the request of {actor} ({actor.id})" if actor else ""
    role = await guild.create_role(
        name=TEAM_ROLE_NAME,
        colour=discord.Colour(TEAM_ROLE_COLOR),
        permissions=discord.Permissions.none(),
        hoist=False,
        mentionable=False,
        reason=f"Moddy Team role{who}",
    )
    await remember_role(bot, guild.id, role.id)
    logger.info("Moddy Team role %s created in guild %s", role.id, guild.id)
    return role


def is_linked(role: discord.Role) -> bool:
    """Whether the role carries a linked-role requirement.

    ``guild_connections`` is the flag Discord sets on a role as soon as it has
    at least one *Links* requirement — including requirements that have nothing
    to do with Moddy. It answers "this role has a requirement on it", which is
    what `/team role` reports before deciding whether to bind it itself.

    It is deliberately *not* used to confirm a binding Moddy just made: tags
    only refresh on the ``GUILD_ROLE_UPDATE`` gateway event, so they are still
    stale one millisecond after the ``PUT``.
    """
    tags = role.tags
    return bool(tags and tags.is_guild_connection())


def can_manage(guild: discord.Guild, role: Optional[discord.Role] = None) -> bool:
    """Whether Moddy may create or edit that role here.

    Discord refuses any role edit above the bot's own top role, and refuses to
    grant a permission the bot does not itself hold — both checked before the
    request rather than after a 403.
    """
    me = guild.me
    if not me or not me.guild_permissions.manage_roles:
        return False
    if role is not None and role >= me.top_role:
        return False
    return True

