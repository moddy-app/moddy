"""The window in which a staffer sets the linked-role requirements.

Discord refuses the binding to bot tokens (``20001 — Bots cannot use this
endpoint``, established the hard way; see docs/LINKED_ROLES.md). Only a human
holding **Manage Roles** can go through *Server Settings → Roles → Moddy Team →
Links*. So `/team role` lends that permission to the staffer who ran it, for the
length of one window, inside a box built to be as small as a box can be:

- the roles to link — **Moddy Team** and **Moddy Team Manager** — are pushed to
  the very bottom of the hierarchy, and a throwaway role carrying *only*
  ``manage_roles`` is created **just above them**. Discord forbids editing any
  role at or above your own highest one, so the staffer can reach exactly the
  roles they are here to link and no others;
- every other role they hold is taken off them for the duration and given back
  afterwards, so the window cannot combine with something they already had;
- anything they do besides the linking — creating a role, handing a role out,
  touching a channel's permissions — is undone and ends the window on the spot;
- when the clock runs out, or the moment the last requirement appears,
  everything is put back: roles restored, throwaway role deleted, both Moddy
  Team roles moved back under Moddy.

### What this is not

It is a privilege escalation, and it is a deliberate one: no administrator of
the server approves it, unlike `/team access`. It is confined, undone, logged
and short — but the honest description is that Moddy hands one of its own staff
`Manage Roles` in somebody else's server for half a minute.

Two consequences follow, and both are load-bearing:

- **The hierarchy does not contain everything.** ``manage_roles`` is also
  *Manage Permissions* on a channel. The position trick cannot prevent a
  staffer from editing channel overwrites; only the audit-log watch below can,
  and an audit entry may arrive late or not at all. Detection, not a guarantee.
- **Nothing here ever gives anybody a Moddy Team role.** Discord assigns them
  from the linked-role metadata. A manual grant would be a duplicate Discord
  removes on its next check — and it would defeat the point of the roles. The
  restore step filters both of them out explicitly, in case the staffer already
  had one.

### Surviving a restart

The saved roles live in ``guilds.data.moddy_team.link_session`` **before** they
are taken away, never only in memory: a process that dies mid-window would
otherwise leave somebody stripped of every role they had.
:func:`recover_sessions` finishes the teardown of anything it finds at boot.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, Iterable, List, Optional, Sequence, Set

import discord

from utils.moddy_team_role import is_linked

logger = logging.getLogger("moddy.team_link_session")

#: How long the staffer has. Seven clicks per role is a lot; the number stays
#: deliberately tight — the window is an escalation — but it has to cover two
#: bindings now, not one.
WINDOW_SECONDS = 75

#: Name of the throwaway role. Recognisable in the audit log of a server that
#: will, rightly, wonder what happened.
TEMP_ROLE_NAME = "Moddy Team — linking"

#: Where a running session is remembered, so a restart can still put it back.
SESSION_PATH = "moddy_team.link_session"

#: Why a window ended. The card the staffer reads is built from this.
DONE = "done"              #: every requirement appeared — the whole point
PARTIAL = "partial"        #: one role was linked, the other was not
EXPIRED = "expired"        #: the clock ran out with nothing linked
CANCELLED = "cancelled"    #: the staffer did something else, and it was undone
FAILED = "failed"          #: Discord refused a step; nothing was left dangling

#: Audit-log actions a staffer must not take while holding the throwaway role.
#: ``role_update`` is watched too, but exempted for the roles being linked —
#: that edit *is* the task.
_WATCHED = {
    discord.AuditLogAction.role_create,
    discord.AuditLogAction.role_delete,
    discord.AuditLogAction.role_update,
    discord.AuditLogAction.member_role_update,
    discord.AuditLogAction.overwrite_create,
    discord.AuditLogAction.overwrite_update,
    discord.AuditLogAction.overwrite_delete,
}


def id_set(value) -> Set[int]:
    """Role ids from an id, a list of ids, or nothing.

    Both shapes are real: a session persisted before this file handled two
    roles stored a single ``team_role_id``, and one persisted after stores a
    list. A restart must be able to finish either.
    """
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        return {rid for rid in (_int(v) for v in value) if rid}
    single = _int(value)
    return {single} if single else set()


def _int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class LinkSession:
    """One live window. At most one per guild, held in :data:`_sessions`.

    It tracks **every** role the staffer is here to link, not one: `/team role`
    opens a single window for both Moddy Team roles rather than stripping
    somebody twice in a row.
    """

    def __init__(self, bot, guild: discord.Guild, member: discord.Member,
                 team_roles: Sequence[discord.Role]):
        self.bot = bot
        self.guild = guild
        self.member = member
        self.team_roles: List[discord.Role] = list(team_roles)
        #: Ids still waiting for a requirement. Emptying it is the success.
        self.pending: Set[int] = {role.id for role in self.team_roles}
        #: Ids linked during this window — what tells `partial` from `expired`.
        self.linked: Set[int] = set()
        self.temp_role: Optional[discord.Role] = None
        self.saved_role_ids: List[int] = []
        self.deadline = time.monotonic() + WINDOW_SECONDS
        self.finished: asyncio.Future = asyncio.get_event_loop().create_future()

    # -- lifecycle ---------------------------------------------------------

    @property
    def team_role_ids(self) -> Set[int]:
        return {role.id for role in self.team_roles}

    def mark_linked(self, role_id: int) -> None:
        """One of the roles got its requirement; resolve once none is left."""
        self.linked.add(role_id)
        self.pending.discard(role_id)
        if not self.pending:
            self.resolve(DONE)

    def resolve(self, outcome: str) -> None:
        """End the window with an outcome. Safe to call more than once."""
        if not self.finished.done():
            self.finished.set_result(outcome)

    @property
    def remaining(self) -> int:
        return max(0, int(round(self.deadline - time.monotonic())))


#: Live sessions, by guild id. A second `/team role` in the same guild is
#: refused rather than queued: two staffers editing the same role at once is
#: how a restore ends up writing the wrong set of roles back.
_sessions: Dict[int, LinkSession] = {}


def active_session(guild_id: int) -> Optional[LinkSession]:
    return _sessions.get(guild_id)


# --------------------------------------------------------------------------- #
# Persistence — the roles must outlive the process holding them
# --------------------------------------------------------------------------- #
async def _remember(bot, guild_id: int, payload: Optional[dict]) -> None:
    if not getattr(bot, "db", None):
        return
    try:
        await bot.db.update_guild_data(guild_id, SESSION_PATH, payload)
    except Exception:  # noqa: BLE001 — never fail the window over a write
        logger.error("Could not persist the link session of guild %s", guild_id,
                     exc_info=True)


async def _stored(bot, guild_id: int) -> Optional[dict]:
    if not getattr(bot, "db", None):
        return None
    try:
        data = await bot.db.get_guild(guild_id)
    except Exception:  # noqa: BLE001
        return None
    return ((data.get("data") or {}).get("moddy_team", {}) or {}).get("link_session")


# --------------------------------------------------------------------------- #
# Role juggling
# --------------------------------------------------------------------------- #
def _restorable(guild: discord.Guild, role_ids, team_role_ids) -> List[discord.Role]:
    """The roles to hand back: never managed ones, never a Moddy Team role.

    Managed roles were never removed (Discord refuses), so re-adding them is at
    best a no-op and at worst a 403. Both Moddy Team roles are excluded on
    principle: this module must never be the thing that grants either.

    ``team_role_ids`` takes an id or a collection of ids — a window persisted
    before there were two roles stored a single one.
    """
    excluded = id_set(team_role_ids)
    out = []
    for rid in role_ids or []:
        role = guild.get_role(int(rid))
        if role and not role.managed and role.id not in excluded and not role.is_default():
            out.append(role)
    return out


def removable_roles(guild: discord.Guild, member: discord.Member) -> List[discord.Role]:
    """The roles Moddy is actually able to take off *member*.

    Two kinds never leave, and neither is a choice:

    - **managed** roles (a bot's own role, the booster role, an integration's),
      which Discord refuses to remove from anybody;
    - roles **at or above Moddy's own top role**, which Discord refuses to touch
      whoever holds them.

    Everything else goes, and comes back at the end.
    """
    me = guild.me
    top = me.top_role if me else None
    return [r for r in member.roles
            if not r.is_default() and not r.managed
            and (top is None or r < top)]


def unstrippable_roles(guild: discord.Guild, member: discord.Member) -> List[discord.Role]:
    """The roles that will stay on the staffer for the length of the window.

    Non-empty means the containment is partial: the staffer keeps whatever
    those roles carry, so the window lends them `Manage Roles` without being
    able to confine them to it. The card says so rather than implying a box
    that is not there.
    """
    removable = {r.id for r in removable_roles(guild, member)}
    return [r for r in member.roles
            if not r.is_default() and not r.managed and r.id not in removable]


async def _strip_roles(session: LinkSession) -> None:
    """Lend the throwaway role, then set aside everything Moddy is able to.

    The two halves are separate requests on purpose. Handing over the role is
    what makes the window worth opening; setting the others aside is what makes
    it *safe*. A server where the second is impossible (the staffer sits above
    Moddy) still gets the first — with the card saying the box is open.
    """
    member = session.member
    await member.add_roles(session.temp_role,
                           reason="Moddy Team linking window — permission lent")

    removable = removable_roles(session.guild, member)
    session.saved_role_ids = [r.id for r in removable]
    # Persisted *before* the removal, never after: a process that dies in
    # between must still know what to give back.
    await _remember(session.bot, session.guild.id, _session_payload(session))
    if not removable:
        return
    try:
        await member.remove_roles(*removable,
                                  reason="Moddy Team linking window — roles held")
    except discord.HTTPException as e:
        # The window is still useful without this; do not abort and leave the
        # staffer with a half-applied state.
        logger.error("Could not set aside the roles of %s in guild %s — HTTP %s: %s",
                     member.id, session.guild.id, getattr(e, "status", "?"),
                     getattr(e, "text", None) or e)
        session.saved_role_ids = []
        await _remember(session.bot, session.guild.id, _session_payload(session))


def _session_payload(session: "LinkSession") -> dict:
    """What a restart needs to finish this window without the process."""
    return {
        "staff_id": session.member.id,
        "temp_role_id": session.temp_role.id if session.temp_role else None,
        "team_role_ids": sorted(session.team_role_ids),
        "saved_role_ids": session.saved_role_ids,
    }


async def _give_roles_back(bot, guild: discord.Guild, member: discord.Member,
                           role_ids, team_role_ids) -> None:
    roles = _restorable(guild, role_ids, team_role_ids)
    if not roles:
        return
    try:
        await member.add_roles(*roles, reason="Moddy Team linking window — roles restored")
    except discord.HTTPException as e:
        logger.error("Could not restore the roles of %s in guild %s — HTTP %s: %s",
                     member.id, guild.id, getattr(e, "status", "?"),
                     getattr(e, "text", None) or e)


async def _restore_positions(roles: Iterable[discord.Role], reason: str) -> None:
    """Put the linked roles back under Moddy once the window is over.

    One request per role, each independent: a role deleted mid-window (or a
    single refusal) must not stop the others from coming back up.
    """
    for role in roles:
        me = role.guild.me
        if not me:
            continue
        target = max(1, me.top_role.position - 1)
        if role.position == target:
            continue
        try:
            await role.edit(position=target, reason=reason)
        except discord.HTTPException as e:
            logger.warning("Could not move the role %s of guild %s to %s — %s",
                           role.id, role.guild.id, target,
                           getattr(e, "text", None) or e)


# --------------------------------------------------------------------------- #
# Undoing whatever the staffer did that was not the linking
# --------------------------------------------------------------------------- #
async def _undo(session: LinkSession, entry: discord.AuditLogEntry) -> None:
    """Put back what a single audit-log entry changed. Best effort, by design.

    Every branch is independent and swallowed: a revert that fails must not
    stop the *other* reverts, and must never stop the teardown that follows.
    """
    guild = session.guild
    action = entry.action
    reason = "Moddy Team linking window — action outside the linking, reverted"

    try:
        if action is discord.AuditLogAction.role_create:
            role = guild.get_role(getattr(entry.target, "id", 0))
            if role:
                await role.delete(reason=reason)

        elif action is discord.AuditLogAction.role_update:
            role = guild.get_role(getattr(entry.target, "id", 0))
            before = getattr(entry.before, "permissions", None)
            if role and before is not None:
                await role.edit(permissions=before, reason=reason)

        elif action is discord.AuditLogAction.member_role_update:
            member = guild.get_member(getattr(entry.target, "id", 0))
            if member:
                # discord.py puts the roles *added* in `after` and the roles
                # *removed* in `before`; undoing is exactly swapping them back.
                team_ids = session.team_role_ids
                added = [r for r in (getattr(entry.after, "roles", None) or [])
                         if r.id not in team_ids]
                removed = [r for r in (getattr(entry.before, "roles", None) or [])
                           if r.id not in team_ids]
                if added:
                    await member.remove_roles(*added, reason=reason)
                if removed:
                    await member.add_roles(*removed, reason=reason)

        elif action in (discord.AuditLogAction.overwrite_create,
                        discord.AuditLogAction.overwrite_update,
                        discord.AuditLogAction.overwrite_delete):
            channel = guild.get_channel(getattr(entry.target, "id", 0))
            subject = entry.extra
            if channel is not None and subject is not None:
                if action is discord.AuditLogAction.overwrite_create:
                    await channel.set_permissions(subject, overwrite=None, reason=reason)
                else:
                    allow = getattr(entry.before, "allow", None)
                    deny = getattr(entry.before, "deny", None)
                    if allow is not None and deny is not None:
                        await channel.set_permissions(
                            subject,
                            overwrite=discord.PermissionOverwrite.from_pair(allow, deny),
                            reason=reason,
                        )
    except discord.HTTPException as e:
        logger.error("Could not revert %s by %s in guild %s — HTTP %s: %s",
                     action, entry.user_id, guild.id, getattr(e, "status", "?"),
                     getattr(e, "text", None) or e)
    except Exception:  # noqa: BLE001 — a revert is never worth an exception here
        logger.error("Could not revert %s by %s in guild %s", action,
                     entry.user_id, guild.id, exc_info=True)


# --------------------------------------------------------------------------- #
# The two gateway signals the window listens to
# --------------------------------------------------------------------------- #
async def handle_role_update(before: discord.Role, after: discord.Role) -> None:
    """A requirement appeared on one of the roles — the window ends on the last."""
    session = _sessions.get(after.guild.id)
    if not session or after.id not in session.team_role_ids:
        return
    if is_linked(after) and not is_linked(before):
        logger.info("Role %s linked by %s in guild %s (%s left)",
                    after.id, session.member.id, after.guild.id,
                    len(session.pending) - 1)
        session.mark_linked(after.id)


async def handle_audit_entry(entry: discord.AuditLogEntry) -> None:
    """Anything the staffer does that is not the linking ends the window."""
    guild = getattr(entry, "guild", None)
    session = _sessions.get(getattr(guild, "id", 0))
    if not session or entry.user_id != session.member.id:
        return
    if entry.action not in _WATCHED:
        return
    # Editing one of the roles being linked *is* the task — never cancel on it,
    # or the feature would cancel its own success.
    if (entry.action is discord.AuditLogAction.role_update
            and getattr(entry.target, "id", None) in session.team_role_ids):
        return

    logger.warning("Staff %s took %s during the linking window in guild %s — "
                   "reverting and closing the window",
                   entry.user_id, entry.action, guild.id)
    await _undo(session, entry)
    session.resolve(CANCELLED)


# --------------------------------------------------------------------------- #
# The window itself
# --------------------------------------------------------------------------- #
class WindowResult:
    """What came of a window: the outcome, and which roles ended up linked.

    The caller needs both — a `partial` outcome is only readable if the card
    can name the role that is still missing its requirement.
    """

    def __init__(self, outcome: str, linked_ids: Optional[Set[int]] = None):
        self.outcome = outcome
        self.linked_ids: Set[int] = set(linked_ids or ())

    def __eq__(self, other):  # so `result == DONE` keeps reading naturally
        if isinstance(other, str):
            return self.outcome == other
        return NotImplemented

    def __str__(self) -> str:
        return self.outcome


async def run_window(bot, guild: discord.Guild, member: discord.Member,
                     team_roles) -> WindowResult:
    """Open the window, wait for it to close, put everything back.

    ``team_roles`` is every role the staffer is here to link — one window for
    both rather than stripping them twice. The outcome is :data:`DONE` only when
    all of them ended up with a requirement; :data:`PARTIAL` when some did.
    The teardown runs whatever happens, including when the setup itself fails
    halfway.
    """
    roles = list(team_roles) if not isinstance(team_roles, discord.Role) else [team_roles]
    session = LinkSession(bot, guild, member, roles)
    _sessions[guild.id] = session
    outcome = FAILED
    try:
        session.temp_role = await guild.create_role(
            name=TEMP_ROLE_NAME,
            permissions=discord.Permissions(manage_roles=True),
            hoist=False, mentionable=False,
            reason=f"Moddy Team linking window for {member} ({member.id})",
        )
        # The roles to link at the very bottom, the throwaway role directly
        # above them: Discord then lets the staffer edit those and no others.
        positions = {role: index for index, role in enumerate(roles, start=1)}
        positions[session.temp_role] = len(roles) + 1
        await guild.edit_role_positions(
            positions=positions,
            reason="Moddy Team linking window",
        )
        await _strip_roles(session)

        try:
            outcome = await asyncio.wait_for(
                asyncio.shield(session.finished), timeout=WINDOW_SECONDS)
        except asyncio.TimeoutError:
            outcome = EXPIRED
    except discord.HTTPException as e:
        logger.error("Could not open the linking window in guild %s — HTTP %s "
                     "(Discord code %s): %s", guild.id, getattr(e, "status", "?"),
                     getattr(e, "code", "?"), getattr(e, "text", None) or e)
        outcome = FAILED
    finally:
        if outcome in (EXPIRED, CANCELLED):
            # The gateway can miss a role update; ask Discord directly before
            # telling a staffer who did everything right that they failed.
            outcome = await _confirm_outcome(session, outcome)
        await _teardown(session)
        _sessions.pop(guild.id, None)
    return WindowResult(outcome, session.linked)


async def _confirm_outcome(session: LinkSession, outcome: str) -> str:
    """Re-read the roles from Discord before calling a window a failure."""
    try:
        fetched = {role.id: role for role in await session.guild.fetch_roles()}
    except discord.HTTPException:
        fetched = {}

    for role_id in list(session.pending):
        role = fetched.get(role_id)
        if role is not None and is_linked(role):
            session.linked.add(role_id)
            session.pending.discard(role_id)

    if not session.pending:
        return DONE
    # A cancelled window stays cancelled: the staffer did something they should
    # not have, and that is what the card must say, whatever got linked.
    if outcome == CANCELLED:
        return CANCELLED
    return PARTIAL if session.linked else EXPIRED


async def _teardown(session: LinkSession) -> None:
    """Roles back, throwaway role gone, both roles under Moddy. Always."""
    if session.saved_role_ids:
        await _give_roles_back(session.bot, session.guild, session.member,
                               session.saved_role_ids, session.team_role_ids)
    if session.temp_role is not None:
        try:
            await session.temp_role.delete(reason="Moddy Team linking window — over")
        except discord.HTTPException as e:
            logger.error("Could not delete the throwaway role %s in guild %s — %s",
                         session.temp_role.id, session.guild.id,
                         getattr(e, "text", None) or e)
    await _restore_positions(session.team_roles,
                             reason="Moddy Team linking window — over")
    await _remember(session.bot, session.guild.id, None)


async def recover_sessions(bot) -> None:
    """Finish the teardown of any window a restart interrupted.

    A staffer stripped of their roles by a process that then died is the one
    outcome this feature must never produce, so this runs at every boot.
    """
    for guild in bot.guilds:
        stored = await _stored(bot, guild.id)
        if not stored:
            continue
        logger.warning("Recovering an interrupted linking window in guild %s", guild.id)
        # ``team_role_ids`` is what a window stores now; ``team_role_id`` is
        # what one interrupted before this file handled two roles stored. Both
        # are read, or that staffer never gets their roles back.
        team_ids = id_set(stored.get("team_role_ids")) or id_set(stored.get("team_role_id"))
        member = guild.get_member(int(stored.get("staff_id") or 0))
        if member:
            await _give_roles_back(bot, guild, member,
                                   stored.get("saved_role_ids") or [], team_ids)
        temp = guild.get_role(int(stored.get("temp_role_id") or 0))
        if temp:
            try:
                await temp.delete(reason="Moddy Team linking window — recovered after a restart")
            except discord.HTTPException:
                logger.error("Could not delete the throwaway role %s in guild %s",
                             temp.id, guild.id, exc_info=True)
        recovered = [role for role in (guild.get_role(rid) for rid in team_ids)
                     if role is not None]
        await _restore_positions(recovered,
                                 reason="Moddy Team linking window — recovered")
        await _remember(bot, guild.id, None)
