"""The **Moddy Team** roles — the only roles Moddy staff ever hold in a server.

A server that needs the Moddy team on site (a support session, a migration, a
bug reproduced live) creates them, binds each one to its linked-role
requirement, and Discord hands them out on its own to whoever is actually staff
at that moment. Nothing else in the bot ever gives a staff member permissions in
a server: `/team access` grants to **one of these roles only**, and
`/team ticket` opens its channel to **the Moddy Team role only**.

There are **two** of them, and they are two because the team is not flat:

- **Moddy Team** (metadata ``team``) — everybody on the team.
- **Moddy Team Manager** (metadata ``manager``) — the accounts that lead it.
  A manager holds **both**: ``team`` stays true for them, so nothing granted to
  the base role has to be granted twice.

Two consequences worth stating out loud:

- **A promotion or a destitution is enough.** The backend republishes the
  booleans (see ``services/staff_events.py``), Discord adds or removes the roles
  by itself, everywhere at once. A server never has to clean up after us.
- **The bot must not assign either role by hand.** Discord owns that half; a
  manual grant would be a duplicate Discord removes on its next check.

### Why a human has to do the linking

No API attaches a linked-role requirement to a role. The official one has no
field for it, and the undocumented route the Discord client uses
(``PUT /guilds/{id}/roles/{id}/connections/configuration``) answers a bot token
with ``20001 — Bots cannot use this endpoint``. That was established against the
live API, not assumed; see docs/LINKED_ROLES.md.

So the step belongs to a human holding *Manage Roles*, and `/team role` lends
that permission to the staffer for the length of one window rather than sending
them to find an administrator — see :mod:`services.team_link_session`. This
module keeps only what is genuinely the roles' business: finding them, creating
them, and reporting whether the requirement is there (:func:`is_linked`).

Each role id is remembered under ``guilds.data.moddy_team`` so a rename never
loses it; the name lookup is only a fallback for a role created before the bot
knew about it (or by hand).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Tuple

import discord

logger = logging.getLogger('moddy.moddy_team_role')


@dataclass(frozen=True)
class TeamRoleKind:
    """One of the two roles, and everything that differs between them.

    Carrying the four differences in one object is what keeps the rest of the
    module (and every caller) written once instead of twice.
    """

    #: Short identifier used in commands, custom_ids and stored payloads.
    key: str
    #: Name used when the bot creates the role. Servers may rename it freely —
    #: the stored id is what the bot works from afterwards.
    name: str
    #: Where the id lives in ``guilds.data``.
    store_path: str
    #: The linked-role metadata key the *backend* publishes for it. The bot
    #: never reads or writes the metadata schema; this is here so the contract
    #: is stated next to the role it belongs to.
    metadata: str


TEAM = TeamRoleKind(
    key="team",
    name="Moddy Team",
    store_path="moddy_team.role_id",
    metadata="team",
)

MANAGER = TeamRoleKind(
    key="manager",
    name="Moddy Team Manager",
    store_path="moddy_team.manager_role_id",
    metadata="manager",
)

#: Both roles, in hierarchy order (the base role first). Iterating this is how
#: `/team role` and the linking window stay correct if a third one ever lands.
KINDS: Tuple[TeamRoleKind, ...] = (TEAM, MANAGER)

#: Kept for the callers that only ever mean the base role.
TEAM_ROLE_NAME = TEAM.name
MANAGER_ROLE_NAME = MANAGER.name
STORE_PATH = TEAM.store_path

#: Moddy blurple, so the roles read as ours in the member list.
TEAM_ROLE_COLOR = 0x5865F2

#: Where a member links their Discord account to their Moddy one. Everybody who
#: has ever signed in to moddy.app is already linked (the dashboard asks for
#: ``role_connections.write`` on the first login); this page is for the rest.
LINKED_ROLES_URL = "https://api.moddy.app/linked-roles"


def kind_from_key(key: Optional[str]) -> TeamRoleKind:
    """The kind named by ``key``, defaulting to the base role.

    Anything unrecognised falls back to :data:`TEAM` rather than raising: the
    key travels through command options and custom_ids, and a typo must not be
    able to turn into an unhandled error in front of an administrator.
    """
    lowered = (key or "").strip().lower()
    for kind in KINDS:
        if kind.key == lowered:
            return kind
    return TEAM


def _as_int(value) -> Optional[int]:
    """JSON has no 64-bit integer — a stored snowflake may come back as text."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def stored_role_id(bot, guild_id: int,
                         kind: TeamRoleKind = TEAM) -> Optional[int]:
    """The role id remembered for this guild, if any."""
    if not getattr(bot, 'db', None):
        return None
    try:
        guild_data = await bot.db.get_guild(guild_id)
    except Exception:  # noqa: BLE001 — a lookup failure is not an answer
        logger.warning("Could not read the %s role of guild %s",
                       kind.name, guild_id, exc_info=True)
        return None
    stored = (guild_data.get('data') or {}).get('moddy_team', {}) or {}
    # ``store_path`` is "moddy_team.<field>" — the field is what lives in there.
    return _as_int(stored.get(kind.store_path.split('.', 1)[1]))


async def remember_role(bot, guild_id: int, role_id: Optional[int],
                        kind: TeamRoleKind = TEAM) -> None:
    """Store (or forget, with ``None``) one of the guild's Moddy Team role ids."""
    if not getattr(bot, 'db', None):
        return
    try:
        await bot.db.update_guild_data(guild_id, kind.store_path, role_id)
    except Exception:  # noqa: BLE001 — the name lookup still finds the role
        logger.warning("Could not store the %s role of guild %s",
                       kind.name, guild_id, exc_info=True)


async def _other_stored_ids(bot, guild_id: int, kind: TeamRoleKind) -> set:
    """The ids stored for the *other* kinds, so a name lookup cannot steal one."""
    ids = set()
    for other in KINDS:
        if other.key == kind.key:
            continue
        rid = await stored_role_id(bot, guild_id, other)
        if rid:
            ids.add(rid)
    return ids


async def find_team_role(bot, guild: discord.Guild,
                         kind: TeamRoleKind = TEAM) -> Optional[discord.Role]:
    """The guild's role of that kind: the stored id first, then the name.

    A stored id that no longer resolves is forgotten on the spot, so a role
    deleted in Discord does not leave `/team access` pointing at a ghost.

    The name match is **exact**, and a role already stored as the other kind is
    skipped: "Moddy Team Manager" contains "Moddy Team", and a looser match
    would hand the base role's permissions to the manager role.
    """
    role_id = await stored_role_id(bot, guild.id, kind)
    if role_id:
        role = guild.get_role(role_id)
        if role:
            return role
        await remember_role(bot, guild.id, None, kind)

    taken = await _other_stored_ids(bot, guild.id, kind)
    lowered = kind.name.lower()
    for role in guild.roles:
        if role.name.lower() == lowered and not role.managed and role.id not in taken:
            # Found by name: remember it, so a later rename stays resolvable.
            await remember_role(bot, guild.id, role.id, kind)
            return role
    return None


async def create_team_role(bot, guild: discord.Guild, *,
                           actor: Optional[discord.abc.User] = None,
                           kind: TeamRoleKind = TEAM) -> discord.Role:
    """Create one of the Moddy Team roles, with **no permissions at all**.

    Permissions are added later, one request at a time, by an administrator
    accepting a `/team access` card. Creating the role pre-loaded would be the
    opposite of that promise.

    Raises :class:`discord.Forbidden` when the bot cannot manage roles, and
    :class:`discord.HTTPException` on anything else — both are the caller's to
    render.
    """
    who = f" at the request of {actor} ({actor.id})" if actor else ""
    role = await guild.create_role(
        name=kind.name,
        colour=discord.Colour(TEAM_ROLE_COLOR),
        permissions=discord.Permissions.none(),
        hoist=False,
        mentionable=False,
        reason=f"{kind.name} role{who}",
    )
    await remember_role(bot, guild.id, role.id, kind)
    logger.info("%s role %s created in guild %s", kind.name, role.id, guild.id)
    return role


def is_linked(role: discord.Role) -> bool:
    """Whether the role carries a linked-role requirement.

    ``guild_connections`` is the flag Discord sets on a role as soon as it has
    at least one *Links* requirement — including requirements that have nothing
    to do with Moddy. It answers "this role has a requirement on it", which is
    what `/team role` reports before deciding whether to open a window for it.

    It cannot say *which* requirement is on the role: reading that needs the
    endpoint Discord closed to bots. What protects us is that the person who
    sets it is our own staff.
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
