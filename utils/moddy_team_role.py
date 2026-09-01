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

### How the linking step is done

Discord's **official** API exposes nothing for it: the REST payload for creating
or editing a role has no field for requirements, and ``role.tags`` only *reports*
them (``guild_connections``, read-only). What the client itself calls from
*Server Settings → Roles → Links* is an undocumented route —
``PUT /guilds/{guild.id}/roles/{role.id}/connections/configuration``, needing
only ``MANAGE_ROLES``, which Moddy already holds when it creates the role.

:func:`link_team_role` uses it, and treats it as the unsupported route it is:
every failure is caught and turned into a :class:`LinkResult`, so the day
Discord closes it `/team role` simply goes back to printing the three clicks an
administrator has to do by hand. Nothing else in the bot depends on it.

The role id is remembered in ``guilds.data.moddy_team.role_id`` so a rename
never loses it; the name lookup is only a fallback for a role created before
the bot knew about it (or by hand).
"""

from __future__ import annotations

import json
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


# --------------------------------------------------------------------------- #
# Binding the role to Moddy's linked-role requirement
# --------------------------------------------------------------------------- #
#: The metadata key the requirement is built on. The backend owns the schema
#: (`PUT /applications/{id}/role-connections/metadata`, never called from here);
#: this is the key it registers, and :func:`resolve_metadata_key` checks at
#: runtime that it is really there before anything is written.
#:
#: The schema also carries ``premium``, and that is exactly why nothing here
#: ever falls back on "some other boolean key": binding the Moddy Team role to
#: ``premium`` would hand it to every subscriber. A missing ``team`` is a
#: backend problem, and the command says so instead of guessing.
TEAM_METADATA_KEY = "team"

#: ``BOOLEAN_EQUAL`` — "the metadata value is equal to the configured value (1)".
OPERATOR_BOOLEAN_EQUAL = 7

#: Metadata record types that a boolean requirement can be built on.
_BOOLEAN_METADATA_TYPES = (7, 8)

#: Cache of the resolved metadata key, per application id. The schema changes
#: only when the backend redeploys, and a wrong guess is not worth one HTTP
#: request per `/team role`.
_metadata_key_cache: dict[int, Optional[str]] = {}


class LinkResult:
    """Outcome of :func:`link_team_role` — what to tell the staffer."""

    LINKED_NOW = "linked_now"          #: Moddy just set the requirement.
    ALREADY_LINKED = "already_linked"  #: The requirement was already there.
    NO_METADATA = "no_metadata"        #: The app has no boolean metadata key.
    UNSUPPORTED = "unsupported"        #: Discord does not expose the route to us.
    FORBIDDEN = "forbidden"            #: Missing Manage Roles, or role too high.
    FAILED = "failed"                  #: Anything else Discord answered.

    #: The two outcomes that mean the admin has nothing left to do.
    DONE = (LINKED_NOW, ALREADY_LINKED)


def _log_discord_refusal(what: str, role: discord.Role, exc: discord.HTTPException,
                         *, payload=None) -> None:
    """Log **what Discord actually answered**, not our interpretation of it.

    The route this module depends on is undocumented: the day it changes, the
    only thing that will say so is the body Discord sent back. A log line
    reading "forbidden" would be worthless — the status, Discord's own error
    code and the raw text are the whole point, so they are always logged, at
    ``error`` level, whatever the failure.

    The payload is logged too when there is one: an argument Discord rejects is
    invisible otherwise.
    """
    logger.error(
        "%s failed on role %s in guild %s — HTTP %s (Discord code %s): %s%s",
        what, role.id, role.guild.id,
        getattr(exc, "status", "?"),
        getattr(exc, "code", "?"),
        getattr(exc, "text", None) or str(exc),
        f" | sent: {json.dumps(payload, separators=(',', ':'))}" if payload is not None else "",
    )


def build_requirement(application_id: int, metadata_key: str) -> dict:
    """The single requirement that says "this account is on the Moddy team".

    ``connection_type: "application"`` is how Discord represents an *app*
    requirement, as opposed to a provider connection (Steam, PayPal…).
    """
    return {
        "connection_type": "application",
        "application_id": str(application_id),
        "connection_metadata_field": metadata_key,
        "operator": OPERATOR_BOOLEAN_EQUAL,
        "value": "1",
    }


def _same_requirement(a: dict, b: dict) -> bool:
    """Whether two requirements express the same condition.

    Only the four fields that define the condition are compared: a configuration
    read back from Discord carries extra, receive-only fields (``application``,
    ``name``, ``result``…) that would make a plain ``==`` always false.
    """
    keys = ("connection_type", "application_id", "connection_metadata_field", "operator", "value")
    return all(str(a.get(k)) == str(b.get(k)) for k in keys)


def configuration_contains(configuration, requirement: dict) -> bool:
    """Whether *requirement* already appears anywhere in the configuration."""
    for group in configuration or []:
        for existing in group or []:
            if _same_requirement(existing, requirement):
                return True
    return False


def merge_configuration(configuration, requirement: dict) -> list:
    """Add *requirement* to an existing configuration without losing it.

    The configuration is ``array[array[requirement]]``: the outer array is a
    **OR**, the inner ones a **AND**. Ours goes in as its own OR branch, so a
    server that already had a requirement on this role keeps it working — both
    populations get the role, neither excludes the other.

    A ``PUT`` **replaces** the whole configuration, which is exactly why this
    merge exists: writing our branch alone would silently drop theirs.
    """
    merged = [list(group or []) for group in (configuration or [])]
    if configuration_contains(merged, requirement):
        return merged
    merged.append([requirement])
    return merged


async def resolve_metadata_key(bot) -> Optional[str]:
    """The boolean metadata key to build the requirement on, or ``None``.

    Read from ``GET /applications/{id}/role-connections/metadata`` — the
    documented, bot-token endpoint. Reading is safe: the rule in
    docs/LINKED_ROLES.md forbids the ``PUT`` (it replaces the whole schema),
    not the ``GET``.

    Only :data:`TEAM_METADATA_KEY` is accepted — see the note there on why
    "any boolean key" is not an acceptable fallback.
    """
    application_id = getattr(bot, 'application_id', None)
    if not application_id:
        return None
    if application_id in _metadata_key_cache:
        return _metadata_key_cache[application_id]

    route = discord.http.Route(
        "GET", "/applications/{application_id}/role-connections/metadata",
        application_id=application_id,
    )
    try:
        records = await bot.http.request(route)
    except discord.HTTPException as e:
        logger.error("Could not read Moddy's role-connection metadata — HTTP %s "
                     "(Discord code %s): %s", getattr(e, "status", "?"),
                     getattr(e, "code", "?"), getattr(e, "text", None) or e)
        return None

    key = next((r.get("key") for r in (records or [])
                if r.get("key") == TEAM_METADATA_KEY
                and r.get("type") in _BOOLEAN_METADATA_TYPES), None)
    if key is not None:
        # Only a success is cached: the schema may simply not be registered yet
        # on a bot that booted before the backend did, and one command later is
        # a perfectly good time to find out that it now is.
        _metadata_key_cache[application_id] = key
    else:
        logger.warning("Moddy's role-connection metadata has no boolean %r key — "
                       "the backend has not registered the schema yet",
                       TEAM_METADATA_KEY)
    return key


async def link_team_role(bot, role: discord.Role) -> str:
    """Attach Moddy's linked-role requirement to *role*. Returns a `LinkResult`.

    **This route is not in Discord's official documentation.** It is the one the
    client itself calls from *Server Settings → Roles → Links*
    (``PUT /guilds/{guild.id}/roles/{role.id}/connections/configuration``,
    ``MANAGE_ROLES``), documented by Discord Userdoccers. Treat it as it
    deserves: every failure is caught and answered with a `LinkResult`, never
    raised, so `/team role` falls back on the manual instructions the day
    Discord closes it. Nothing else in the bot depends on it.

    The existing configuration is read first and merged into, because the
    ``PUT`` replaces it whole — a server that had its own requirement on this
    role must not lose it.
    """
    application_id = getattr(bot, 'application_id', None)
    if not application_id:
        return LinkResult.FAILED

    metadata_key = await resolve_metadata_key(bot)
    if not metadata_key:
        return LinkResult.NO_METADATA

    requirement = build_requirement(application_id, metadata_key)
    params = {"guild_id": role.guild.id, "role_id": role.id}
    read = discord.http.Route(
        "GET", "/guilds/{guild_id}/roles/{role_id}/connections/configuration", **params)
    write = discord.http.Route(
        "PUT", "/guilds/{guild_id}/roles/{role_id}/connections/configuration", **params)

    try:
        current = await bot.http.request(read)
    except discord.NotFound as e:
        # No configuration yet is a legitimate answer on a fresh role; only a
        # missing *route* is fatal, and the PUT below tells us which it is. Log
        # it anyway: it is also what a withdrawn route looks like from here.
        logger.info("No role connection configuration on role %s in guild %s "
                    "(HTTP 404: %s)", role.id, role.guild.id,
                    getattr(e, "text", None) or e)
        current = []
    except discord.Forbidden as e:
        _log_discord_refusal("Reading the role connection configuration", role, e)
        return LinkResult.FORBIDDEN
    except discord.HTTPException as e:
        _log_discord_refusal("Reading the role connection configuration", role, e)
        current = []

    if configuration_contains(current, requirement):
        return LinkResult.ALREADY_LINKED

    payload = merge_configuration(current, requirement)
    try:
        await bot.http.request(write, json=payload)
    except discord.Forbidden as e:
        _log_discord_refusal("Binding the role connection", role, e, payload=payload)
        return LinkResult.FORBIDDEN
    except discord.HTTPException as e:
        _log_discord_refusal("Binding the role connection", role, e, payload=payload)
        # 404/405 is the shape a withdrawn route takes: say so plainly rather
        # than reporting a permission problem the admin cannot act on.
        if e.status in (404, 405):
            logger.error("The role connection route is not available to Moddy "
                         "(HTTP %s) — falling back on the manual instructions. "
                         "If this is permanent, `/team role` must go back to "
                         "printing the manual steps only.", e.status)
            return LinkResult.UNSUPPORTED
        return LinkResult.FAILED

    logger.info("Moddy Team role %s bound to the linked-role requirement in guild %s",
                role.id, role.guild.id)
    return LinkResult.LINKED_NOW
