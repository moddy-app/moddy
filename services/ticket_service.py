"""
Ticket service — every action a ticket can undergo, in one place.

``modules/tickets.py`` owns the *configuration*; this owns the *verbs*. The
slash commands (``cogs/tickets.py``) and the buttons on the ticket message
(``utils/ticket_views.py``) are both thin shells over the methods here, which
is what makes "every ticket action is available as a slash command" true by
construction rather than by discipline.

Reachable as ``bot.tickets``.

Failures are raised as :class:`TicketError` carrying an **i18n key**, never a
formatted string: the caller decides whether to answer in the actor's language
(a slash command) or the ticket's (a message posted in the channel).

Two rules shape the side effects here:

- **A ping is its own message, deleted immediately** (:meth:`TicketService.ping`).
  Discord has delivered the notification by the time it goes, so nothing is
  lost, and a ticket is not left with a permanent wall of blue names on every
  card.
- **The channel's coloured status dot is applied in the background**
  (:meth:`TicketService.sync_status_prefix`). Discord allows two renames per
  channel per ten minutes; making a claim wait that out would be far worse
  than a name catching up late.

See docs/TICKETS.md.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional, Tuple, Union

import discord

from modules.tickets import (
    MODULE_ID,
    PERM_ADMIN,
    PERM_CLAIM,
    PERM_CLOSE,
    PERM_MOVE,
    PERM_PARTICIPANTS,
    PERM_RENAME,
    PERM_STAFF_THREAD,
    PERM_UNCLAIM_OTHERS,
    PERM_VIEW,
    apply_status_prefix,
    can_open,
    default_open_message,
    locate_category,
    member_permissions,
    render_channel_name,
    render_text,
    staff_role_ids,
    ticket_status_dot,
)
from utils.i18n import t

logger = logging.getLogger('moddy.services.tickets')

# Permissions the ticket opener (and anyone added by hand) gets in the channel.
_MEMBER_OVERWRITE = discord.PermissionOverwrite(
    view_channel=True,
    send_messages=True,
    read_message_history=True,
    attach_files=True,
    embed_links=True,
    add_reactions=True,
)

# Staff get the same plus the ability to tidy the channel up.
_STAFF_OVERWRITE = discord.PermissionOverwrite(
    view_channel=True,
    send_messages=True,
    read_message_history=True,
    attach_files=True,
    embed_links=True,
    add_reactions=True,
    manage_messages=True,
)

# Same, minus the right to speak: a claimed ticket under `claim_lock` keeps
# every other agent reading along, and an escalation can keep the people added
# by hand in the loop without letting them weigh in.
_STAFF_MUTED_OVERWRITE = discord.PermissionOverwrite(
    view_channel=True,
    send_messages=False,
    read_message_history=True,
    add_reactions=False,
    manage_messages=True,
)

_MEMBER_MUTED_OVERWRITE = discord.PermissionOverwrite(
    view_channel=True,
    send_messages=False,
    read_message_history=True,
    add_reactions=False,
)

_HIDDEN_OVERWRITE = discord.PermissionOverwrite(view_channel=False)


class TicketError(Exception):
    """An action that could not be performed, with a translatable reason."""

    def __init__(self, key: str, **params: Any):
        super().__init__(key)
        self.key = key
        self.params = params

    def message(self, locale: str) -> str:
        return t(self.key, locale=locale, **self.params)


class TicketService:
    """Open, close, escalate, move, rename and staff a ticket."""

    def __init__(self, bot):
        self.bot = bot
        # Background renames, kept referenced: asyncio only holds a weak
        # reference to a running task, so a fire-and-forget one can be
        # collected mid-flight and the channel would silently keep the wrong
        # status dot.
        self._renames: set = set()

    # ------------------------------------------------------------------ #
    # Resolution
    # ------------------------------------------------------------------ #
    async def get_module(self, guild_id: int):
        """The guild's Tickets module instance, or ``None`` when unconfigured."""
        if not getattr(self.bot, 'module_manager', None):
            return None
        try:
            return await self.bot.module_manager.get_module_instance(guild_id, MODULE_ID)
        except Exception as e:
            # Callers read ``None`` as "tickets are not configured here", so a
            # load failure would look to the server like the feature simply
            # being off. Report it centrally instead of guessing.
            from cogs.error_handler import report_error
            await report_error(
                self.bot, e, source="TicketService:get_module",
                guild=self.bot.get_guild(guild_id), error_type="Service Error",
            )
            return None

    async def get_ticket(self, channel_id: int) -> Optional[Dict[str, Any]]:
        if not self.bot.db:
            return None
        return await self.bot.db.get_ticket_by_channel(channel_id)

    async def resolve(self, channel: discord.abc.GuildChannel
                      ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        """``(ticket, panel, category)`` for a ticket channel.

        Raises :class:`TicketError` when the channel is not a ticket, or when
        the category it was opened in has since been deleted from the config —
        the two states every caller has to handle anyway.
        """
        ticket = await self.get_ticket(channel.id)
        if not ticket:
            raise TicketError('modules.tickets.errors.not_a_ticket')

        module = await self.get_module(ticket['guild_id'])
        if not module:
            raise TicketError('modules.tickets.errors.module_disabled')

        panel, category = locate_category(
            {'panels': module.panels}, ticket['panel_id'], ticket['category_id'])
        if not category:
            raise TicketError('modules.tickets.errors.category_gone')
        return ticket, panel, category

    async def ticket_locale(self, guild: discord.Guild) -> str:
        """Language a ticket speaks: the server language.

        Categories used to carry one each, which meant the same server could
        greet a member in French in one category and in English in the next.
        See ``utils/guild_language.py``.
        """
        from utils.guild_language import guild_locale
        return await guild_locale(self.bot, guild)

    # ------------------------------------------------------------------ #
    # Authorization
    # ------------------------------------------------------------------ #
    def require(self, member: discord.Member, category: Dict[str, Any],
                ticket: Dict[str, Any], permission: str) -> set:
        """Assert ``member`` holds ``permission`` here; return all their perms."""
        granted = member_permissions(member, category, ticket)
        if permission not in granted:
            raise TicketError('modules.tickets.errors.missing_permission')
        return granted

    # ------------------------------------------------------------------ #
    # Channel permissions
    # ------------------------------------------------------------------ #
    def build_overwrites(self, guild: discord.Guild, category: Dict[str, Any],
                         ticket: Dict[str, Any]
                         ) -> Dict[Union[discord.Role, discord.Member],
                                   discord.PermissionOverwrite]:
        """The full overwrite map for a ticket channel, from scratch.

        Rebuilding the whole map on every change (instead of patching it) is
        what keeps escalation, closure and participant edits from drifting into
        states nobody can explain.

        - **Escalated** tickets only keep the roles holding ``admin``. The
          people kept in them can be muted rather than dropped
          (``escalation_mute``): still reading, no longer weighing in.
        - **Claimed** tickets under ``claim_lock`` let only the claimer, the
          responsibles, the opener and the manually added people speak.
          Everyone else on the staff side keeps reading — a locked ticket is
          not a private one.
        - **Closed** tickets keep staff and hide the channel from the member
          side, so a closed ticket disappears from the opener's channel list
          without losing anything: reopening restores it exactly.
        """
        escalated = bool(ticket.get('escalated'))
        closed = ticket.get('status') == 'closed'
        muted = escalated and bool(ticket.get('escalation_mute'))

        claimer_id = ticket.get('claimed_by')
        # The lock only bites while somebody actually holds the ticket: an
        # unclaimed ticket that nobody may answer would be a dead end.
        locked = bool(
            category.get('claim_enabled', True)
            and category.get('claim_lock')
            and claimer_id and not closed and not escalated
        )
        responsible_ids = set(staff_role_ids(category, permission=PERM_ADMIN))

        overwrites: Dict[Any, discord.PermissionOverwrite] = {
            guild.default_role: _HIDDEN_OVERWRITE,
        }
        if guild.me:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                manage_channels=True, manage_messages=True, embed_links=True,
                attach_files=True, manage_threads=True, create_private_threads=True,
            )

        # Staff roles.
        wanted = PERM_ADMIN if escalated else PERM_VIEW
        for role_id in staff_role_ids(category, permission=wanted):
            role = guild.get_role(role_id)
            if not role:
                continue
            silence = locked and role_id not in responsible_ids
            overwrites[role] = _STAFF_MUTED_OVERWRITE if silence else _STAFF_OVERWRITE

        # Roles added by hand to this ticket keep their access through an
        # escalation only when they are staff — otherwise escalating would be
        # pointless. They are never hidden by a mere closure either.
        for role_id in ticket.get('participant_roles', []):
            role = guild.get_role(role_id)
            if role and not escalated and not closed:
                overwrites.setdefault(role, _MEMBER_OVERWRITE)

        if not closed:
            # The opener and everyone added by hand. Escalation deliberately
            # keeps them (the opener has to stay, and the manual participants
            # are an explicit staff decision — the escalate flow offers to
            # drop them, or to keep them read-only).
            member_ids = [ticket['owner_id'], *ticket.get('participants', [])]
            for user_id in member_ids:
                member = guild.get_member(user_id)
                if member and member not in overwrites:
                    overwrites[member] = (_MEMBER_MUTED_OVERWRITE if muted
                                          else _MEMBER_OVERWRITE)

        # The claimer, last: a member overwrite outranks the role one they were
        # just muted through, which is the whole point of claiming.
        if locked and claimer_id:
            claimer = guild.get_member(claimer_id)
            if claimer:
                overwrites[claimer] = _STAFF_OVERWRITE

        return overwrites

    # ------------------------------------------------------------------ #
    # Pings
    # ------------------------------------------------------------------ #
    async def ping(self, channel: discord.TextChannel,
                   mentions: Optional[str]) -> None:
        """Notify people with a message that deletes itself immediately.

        A mention printed inside the ticket card would stay there forever,
        turning every ticket into a wall of blue names nobody reads twice. A
        throwaway message rings exactly once and leaves the channel clean —
        Discord has already delivered the notification by the time it goes.
        Best effort: a ping that cannot be sent is never worth an error.
        """
        if not mentions:
            return
        try:
            message = await channel.send(
                mentions,
                allowed_mentions=discord.AllowedMentions(users=True, roles=True),
            )
        except discord.HTTPException as e:
            logger.warning(f"[Tickets] Could not ping in {channel.id}: {e}")
            return
        try:
            await message.delete()
        except discord.HTTPException:
            # Left behind rather than retried: the notification is already out,
            # and a stray mention line is a cosmetic problem, not a failure.
            pass

    # ------------------------------------------------------------------ #
    # Channel name status
    # ------------------------------------------------------------------ #
    async def sync_status_prefix(self, channel: discord.TextChannel,
                                 category: Dict[str, Any],
                                 ticket: Dict[str, Any]) -> None:
        """Bring the coloured dot in the channel name in line with the state.

        Fired and forgotten on purpose. Discord allows two channel renames per
        ten minutes; a busy ticket that is claimed, released and re-claimed
        would otherwise make the *action* wait out the rate limit while
        discord.py sleeps on the request. The claim itself is already stored
        and its permissions already applied — the name catching up late is the
        cheap half.
        """
        wanted = apply_status_prefix(
            channel.name, ticket_status_dot(category, ticket))
        if wanted == channel.name:
            return

        async def rename():
            try:
                await channel.edit(name=wanted,
                                   reason="Moddy tickets: status")
            except discord.HTTPException as e:
                logger.warning(f"[Tickets] Could not update the status of "
                               f"channel {channel.id}: {e}")

        task = asyncio.create_task(rename())
        self._renames.add(task)
        task.add_done_callback(self._renames.discard)

    async def sync_permissions(self, channel: discord.TextChannel,
                               category: Dict[str, Any],
                               ticket: Dict[str, Any]) -> bool:
        """Push the rebuilt overwrite map onto the channel."""
        try:
            await channel.edit(
                overwrites=self.build_overwrites(channel.guild, category, ticket),
                reason="Moddy tickets: permission sync",
            )
            return True
        except discord.Forbidden:
            logger.warning(f"[Tickets] Missing permissions to sync overwrites on "
                           f"channel {channel.id}")
        except discord.HTTPException as e:
            logger.error(f"[Tickets] Could not sync overwrites on {channel.id}: {e}")
        return False

    # ------------------------------------------------------------------ #
    # Open
    # ------------------------------------------------------------------ #
    async def open_ticket(self, member: discord.Member, panel: Dict[str, Any],
                          category: Dict[str, Any]) -> discord.TextChannel:
        """Create the ticket channel and post its pinned control message."""
        from utils.ticket_views import build_ticket_message

        guild = member.guild

        if not category.get('enabled'):
            raise TicketError('modules.tickets.errors.category_disabled')
        if not can_open(member, category):
            raise TicketError('modules.tickets.errors.cannot_open')

        parent = guild.get_channel(category['discord_category_id']) \
            if category.get('discord_category_id') else None
        if not isinstance(parent, discord.CategoryChannel):
            raise TicketError('modules.tickets.errors.destination_missing')

        if not self.bot.db:
            raise TicketError('modules.tickets.errors.unavailable')

        open_count = await self.bot.db.count_open_tickets(
            guild.id, member.id, category['id'])
        if open_count >= category.get('max_open_per_user', 1):
            raise TicketError('modules.tickets.errors.too_many_open',
                              max=category.get('max_open_per_user', 1))

        # The number is predicted first so the channel can be created with its
        # final name, its final overwrites and its topic in ONE call. Renaming
        # afterwards would spend one of the two renames Discord allows per
        # channel per 10 minutes, and a staffer's /ticket rename right after
        # opening would then hang.
        locale = await self.ticket_locale(guild)
        number = await self.bot.db.next_ticket_number(guild.id)
        draft = {'owner_id': member.id, 'status': 'open', 'escalated': False,
                 'participants': [], 'participant_roles': [], 'claimed_by': None}
        try:
            channel = await guild.create_text_channel(
                name=apply_status_prefix(
                    render_channel_name(category, member=member, number=number),
                    ticket_status_dot(category, draft)),
                category=parent,
                overwrites=self.build_overwrites(guild, category, draft),
                topic=t('modules.tickets.channel.topic', locale=locale,
                        number=number, category=category['name'],
                        user=str(member), user_id=member.id)[:1024],
                reason=f"Moddy ticket opened by {member} ({member.id})",
            )
        except discord.Forbidden:
            raise TicketError('modules.tickets.errors.cannot_create_channel')
        except discord.HTTPException as e:
            logger.error(f"[Tickets] Channel creation failed in guild {guild.id}: {e}")
            raise TicketError('modules.tickets.errors.cannot_create_channel')

        # The channel id is part of the row, so the channel comes first and the
        # row is written immediately after; a channel whose insert fails is
        # deleted again rather than left dangling.
        ticket = await self.bot.db.create_ticket(
            guild_id=guild.id, channel_id=channel.id, panel_id=panel['id'],
            category_id=category['id'], owner_id=member.id, number=number,
        )
        if not ticket:
            await channel.delete(reason="Moddy tickets: could not register the ticket")
            raise TicketError('modules.tickets.errors.unavailable')

        # The control message: pinned, so it stays reachable however long the
        # conversation gets. The whole of its text is the category's opening
        # message — title line and footer included — so an admin controls every
        # word their members read.
        body = category.get('open_message') or default_open_message(locale)
        rendered = render_text(body, member=member, guild=guild, category=category,
                               number=ticket['number'], channel=channel)
        try:
            message = await channel.send(
                view=build_ticket_message(ticket, category, rendered, locale=locale))
            await message.pin(reason="Moddy tickets: control message")
        except discord.HTTPException as e:
            logger.warning(f"[Tickets] Could not post the control message in "
                           f"{channel.id}: {e}")

        # The pings come after, in their own throwaway message: they ring once
        # and leave nothing behind in the ticket.
        await self.ping(channel, self._open_ping_content(guild, category, member))

        logger.info(f"[Tickets] #{ticket['number']} opened in guild {guild.id} "
                    f"by {member.id} (category {category['id']})")
        return channel

    def _open_ping_content(self, guild: discord.Guild, category: Dict[str, Any],
                           member: discord.Member) -> Optional[str]:
        """Who is rung when a ticket opens: the opener, the ping roles, the staff.

        The staff half is the roles that can actually work this category —
        pinging a role that cannot even see the ticket would be noise — and it
        is opt-out per category (``ping_staff_roles``), because a server with a
        dedicated ping role does not want both.
        """
        parts = [member.mention]
        seen = set()
        role_ids = list(category.get('ping_role_ids', []))
        if category.get('ping_staff_roles', True):
            role_ids += staff_role_ids(category, permission=PERM_VIEW)
        for role_id in role_ids:
            if role_id in seen:
                continue
            seen.add(role_id)
            role = guild.get_role(role_id)
            if role:
                parts.append(role.mention)
        return " ".join(parts) if parts else None

    def _role_mentions(self, guild: discord.Guild, category: Dict[str, Any],
                       permission: str) -> Optional[str]:
        """Mentions for every role holding ``permission`` in this category."""
        mentions = []
        for role_id in staff_role_ids(category, permission=permission):
            role = guild.get_role(role_id)
            if role:
                mentions.append(role.mention)
        return " ".join(mentions) or None

    # ------------------------------------------------------------------ #
    # Close / reopen
    # ------------------------------------------------------------------ #
    async def close_ticket(self, channel: discord.TextChannel, actor: discord.Member,
                           reason: Optional[str] = None) -> Dict[str, Any]:
        """Lock the ticket and post the closing card.

        The channel is **kept**: nothing is destroyed by a click. Deleting it is
        a separate, explicit action on the closing card.
        """
        from utils.ticket_views import build_closed_message

        ticket, panel, category = await self.resolve(channel)
        if ticket['status'] == 'closed':
            raise TicketError('modules.tickets.errors.already_closed')
        self.require(actor, category, ticket, PERM_CLOSE)

        await self.bot.db.set_ticket_status(
            channel.id, 'closed', actor_id=actor.id, reason=reason)
        ticket = await self.get_ticket(channel.id) or ticket

        await self.sync_permissions(channel, category, ticket)
        await self.sync_status_prefix(channel, category, ticket)

        locale = await self.ticket_locale(channel.guild)
        closing = category.get('close_message')
        rendered = render_text(
            closing, member=actor, guild=channel.guild, category=category,
            number=ticket['number'], channel=channel,
        ) if closing else None

        try:
            await channel.send(view=build_closed_message(
                ticket, category, actor, reason, rendered, locale=locale))
        except discord.HTTPException as e:
            logger.warning(f"[Tickets] Could not post the closing card in "
                           f"{channel.id}: {e}")

        await self._notify_owner_closed(channel, ticket, category, actor, reason)
        logger.info(f"[Tickets] #{ticket['number']} closed by {actor.id}")
        return ticket

    async def _notify_owner_closed(self, channel, ticket, category, actor, reason):
        """DM the opener that their ticket is closed — best effort, never fatal."""
        from utils.ticket_views import build_close_dm

        locale = await self.ticket_locale(channel.guild)
        await self._dm_owner(
            channel, ticket,
            build_close_dm(channel.guild, ticket, category, actor, reason,
                           locale=locale),
            kind="close", category=category, locale=locale)

    async def _dm_owner(self, channel: discord.TextChannel,
                        ticket: Dict[str, Any], view, *,
                        kind: str = "close", category: Optional[Dict[str, Any]] = None,
                        locale: str = "en-US") -> None:
        """Send one card to the ticket's opener. Closed DMs are not an error.

        Routed through the notification system like every other DM: the card is
        Moddy's own wording about a ticket on that server, so it carries the
        service + server attribution and a greyed-out report flag.
        """
        owner = channel.guild.get_member(ticket['owner_id'])
        if not owner or owner.bot:
            return
        from notifications.models import NotificationContent, NotificationSource
        from utils.emojis import TICKET
        try:
            await self.bot.notifications.send_dm(
                owner,
                content=NotificationContent(
                    title=t(f"modules.tickets.{kind}.dm_title", locale=locale),
                    body=t(f"modules.tickets.{kind}.dm_description", locale=locale,
                           server="{server}", number="{number}", category="{category}"),
                    icon=TICKET,
                    template_id=f"tickets.dm.{kind}",
                ),
                variables={
                    "server": channel.guild.name,
                    "number": str(ticket.get("number", "—")),
                    "category": (category or {}).get("name", "—"),
                },
                source=NotificationSource.service_guild("tickets", channel.guild.id),
                view=view,
                locale=locale,
            )
        except (discord.Forbidden, discord.HTTPException):
            pass  # closed DMs are the norm, not an error

    async def reopen_ticket(self, channel: discord.TextChannel,
                            actor: discord.Member) -> Dict[str, Any]:
        from utils.ticket_views import build_reopen_dm

        ticket, panel, category = await self.resolve(channel)
        if ticket['status'] != 'closed':
            raise TicketError('modules.tickets.errors.not_closed')
        self.require(actor, category, ticket, PERM_CLOSE)

        await self.bot.db.set_ticket_status(channel.id, 'open')
        ticket = await self.get_ticket(channel.id) or ticket
        await self.sync_permissions(channel, category, ticket)
        await self.sync_status_prefix(channel, category, ticket)

        # The closure was announced in a DM, so its cancellation has to be too:
        # a member told their ticket was over has no reason to look at a
        # channel that had disappeared from their list.
        locale = await self.ticket_locale(channel.guild)
        await self._dm_owner(
            channel, ticket,
            build_reopen_dm(channel.guild, ticket, category, actor, channel,
                            locale=locale),
            kind="reopen", category=category, locale=locale)

        logger.info(f"[Tickets] #{ticket['number']} reopened by {actor.id}")
        return ticket

    async def delete_ticket(self, channel: discord.TextChannel,
                            actor: discord.Member) -> None:
        """Delete the ticket channel for good. Requires ``admin``."""
        ticket, panel, category = await self.resolve(channel)
        self.require(actor, category, ticket, PERM_ADMIN)
        await self.bot.db.delete_ticket(channel.id)
        await channel.delete(reason=f"Moddy ticket deleted by {actor} ({actor.id})")

    # ------------------------------------------------------------------ #
    # Close request
    # ------------------------------------------------------------------ #
    async def request_close(self, channel: discord.TextChannel, actor: discord.Member,
                            reason: Optional[str] = None) -> Dict[str, Any]:
        """Ask the staff to close — the action for whoever cannot close.

        Deliberately requires no permission: anyone who can see the ticket may
        ask for it to end. Someone who *can* close is told to just close it,
        rather than asking themselves.
        """
        from utils.ticket_views import build_close_request_message

        ticket, panel, category = await self.resolve(channel)
        if ticket['status'] == 'closed':
            raise TicketError('modules.tickets.errors.already_closed')

        granted = member_permissions(actor, category, ticket)
        if PERM_VIEW not in granted:
            raise TicketError('modules.tickets.errors.missing_permission')
        if PERM_CLOSE in granted:
            raise TicketError('modules.tickets.errors.can_close_directly')
        if ticket.get('close_requested_by'):
            raise TicketError('modules.tickets.errors.close_already_requested')

        await self.bot.db.set_close_request(channel.id, actor.id, reason)
        ticket = await self.get_ticket(channel.id) or ticket

        locale = await self.ticket_locale(channel.guild)
        try:
            await channel.send(
                view=build_close_request_message(ticket, actor, reason, locale=locale))
        except discord.HTTPException as e:
            logger.warning(f"[Tickets] Could not post the close request in "
                           f"{channel.id}: {e}")
        await self.ping(channel,
                        self._role_mentions(channel.guild, category, PERM_CLOSE))
        return ticket

    async def cancel_close_request(self, channel: discord.TextChannel,
                                   actor: discord.Member) -> Dict[str, Any]:
        """Refuse a pending close request (staff) or withdraw it (requester)."""
        ticket, panel, category = await self.resolve(channel)
        if not ticket.get('close_requested_by'):
            raise TicketError('modules.tickets.errors.no_close_request')

        granted = member_permissions(actor, category, ticket)
        if PERM_CLOSE not in granted and actor.id != ticket['close_requested_by']:
            raise TicketError('modules.tickets.errors.missing_permission')

        await self.bot.db.set_close_request(channel.id, None, None)
        return await self.get_ticket(channel.id) or ticket

    # ------------------------------------------------------------------ #
    # Claim
    # ------------------------------------------------------------------ #
    def claim_permission(self, ticket: Dict[str, Any]) -> str:
        """Which permission taking this ticket needs right now.

        An escalated ticket belongs to the responsibles: letting a plain agent
        claim it back would undo the escalation through the side door.
        """
        return PERM_ADMIN if ticket.get('escalated') else PERM_CLAIM

    async def claim_ticket(self, channel: discord.TextChannel,
                           actor: discord.Member) -> Dict[str, Any]:
        """Take the ticket in charge."""
        ticket, panel, category = await self.resolve(channel)
        if not category.get('claim_enabled', True):
            raise TicketError('modules.tickets.errors.claim_disabled')
        if ticket['status'] == 'closed':
            raise TicketError('modules.tickets.errors.already_closed')
        self.require(actor, category, ticket, self.claim_permission(ticket))

        holder = ticket.get('claimed_by')
        if holder == actor.id:
            raise TicketError('modules.tickets.errors.already_claimed_by_you')
        if holder:
            raise TicketError('modules.tickets.errors.already_claimed',
                              user=f"<@{holder}>")

        await self.bot.db.set_ticket_claim(channel.id, actor.id)
        ticket = await self.get_ticket(channel.id) or ticket
        await self.sync_permissions(channel, category, ticket)
        await self.sync_status_prefix(channel, category, ticket)
        await self._announce_claim(channel, category, actor, claimed=True)
        logger.info(f"[Tickets] #{ticket['number']} claimed by {actor.id}")
        return ticket

    async def _announce_claim(self, channel: discord.TextChannel,
                              category: Dict[str, Any], actor: discord.Member, *,
                              claimed: bool) -> None:
        """Say in the channel who took the ticket, or that it is free again.

        A claim changes who may answer, so it belongs where the conversation
        is: the coloured dot in the channel name tells the staff, this tells
        the member who they are now talking to.
        """
        from utils.ticket_views import build_claim_notice

        try:
            await channel.send(view=build_claim_notice(
                actor, claimed=claimed,
                locale=await self.ticket_locale(channel.guild)))
        except discord.HTTPException as e:
            logger.warning(f"[Tickets] Could not post the claim notice in "
                           f"{channel.id}: {e}")

    async def unclaim_ticket(self, channel: discord.TextChannel,
                             actor: discord.Member) -> Dict[str, Any]:
        """Release the ticket — yours, or somebody else's with the permission."""
        ticket, panel, category = await self.resolve(channel)
        if not category.get('claim_enabled', True):
            raise TicketError('modules.tickets.errors.claim_disabled')

        holder = ticket.get('claimed_by')
        if not holder:
            raise TicketError('modules.tickets.errors.not_claimed')

        granted = member_permissions(actor, category, ticket)
        if holder != actor.id and PERM_UNCLAIM_OTHERS not in granted:
            # Releasing somebody else's ticket is its own permission: an agent
            # must not be able to take a case off a colleague, a responsible
            # must be able to.
            raise TicketError('modules.tickets.errors.claimed_by_someone_else',
                              user=f"<@{holder}>")
        if holder == actor.id and self.claim_permission(ticket) not in granted:
            raise TicketError('modules.tickets.errors.missing_permission')

        await self.bot.db.set_ticket_claim(channel.id, None)
        ticket = await self.get_ticket(channel.id) or ticket
        await self.sync_permissions(channel, category, ticket)
        await self.sync_status_prefix(channel, category, ticket)
        await self._announce_claim(channel, category, actor, claimed=False)
        logger.info(f"[Tickets] #{ticket['number']} released by {actor.id}")
        return ticket

    async def toggle_claim(self, channel: discord.TextChannel,
                           actor: discord.Member) -> Tuple[Dict[str, Any], bool]:
        """What the **Claim** button does. Returns ``(ticket, claimed_now)``.

        One button, three outcomes, decided by who is clicking:

        - nobody holds it → the clicker takes it;
        - the clicker holds it → they release it, so pressing twice undoes a
          mis-click without hunting for a second button;
        - somebody else holds it → released only by whoever may release
          another's ticket (a responsible, typically), refused otherwise.
        """
        ticket = await self.get_ticket(channel.id)
        if not ticket:
            raise TicketError('modules.tickets.errors.not_a_ticket')
        if ticket.get('claimed_by'):
            await self.unclaim_ticket(channel, actor)
            return await self.get_ticket(channel.id) or ticket, False
        return await self.claim_ticket(channel, actor), True

    # ------------------------------------------------------------------ #
    # Escalate
    # ------------------------------------------------------------------ #
    async def escalate(self, channel: discord.TextChannel, actor: discord.Member,
                       *, reason: Optional[str] = None,
                       keep_participants: bool = True,
                       mute_participants: bool = False) -> Dict[str, Any]:
        """Restrict the ticket to the responsibles, its opener and (optionally)
        the people added by hand.

        Everyone else — every staff role that is not ``admin`` — loses access.
        The people who are kept can be kept **read-only**
        (``mute_participants``): an escalation often needs them to follow along
        without adding to a conversation that is now between responsibles.

        Escalating also releases the claim, and parks it: whoever had the
        ticket gets it back when the escalation is cancelled.
        """
        from utils.ticket_views import build_escalation_notice

        ticket, panel, category = await self.resolve(channel)
        if ticket['status'] == 'closed':
            raise TicketError('modules.tickets.errors.already_closed')
        if ticket.get('escalated'):
            raise TicketError('modules.tickets.errors.already_escalated')
        self.require(actor, category, ticket, PERM_ADMIN)

        if not staff_role_ids(category, permission=PERM_ADMIN):
            # Escalating with nobody to escalate *to* would lock the ticket
            # down to its opener and the server admins alone.
            raise TicketError('modules.tickets.errors.no_responsible_role')

        if not keep_participants:
            await self.bot.db.set_participants(channel.id, users=[], roles=[])
        await self.bot.db.set_escalated(
            channel.id, True,
            mute_participants=bool(keep_participants and mute_participants))
        ticket = await self.get_ticket(channel.id) or ticket

        await self.sync_permissions(channel, category, ticket)
        await self.sync_status_prefix(channel, category, ticket)

        locale = await self.ticket_locale(channel.guild)
        try:
            await channel.send(
                view=build_escalation_notice(ticket, actor, reason, locale=locale))
        except discord.HTTPException as e:
            logger.warning(f"[Tickets] Could not post the escalation notice in "
                           f"{channel.id}: {e}")
        await self.ping(channel,
                        self._role_mentions(channel.guild, category, PERM_ADMIN))

        logger.info(f"[Tickets] #{ticket['number']} escalated by {actor.id}")
        return ticket

    async def deescalate(self, channel: discord.TextChannel,
                         actor: discord.Member) -> Dict[str, Any]:
        """Undo an escalation — access, speech and the previous claim all return."""
        ticket, panel, category = await self.resolve(channel)
        if not ticket.get('escalated'):
            raise TicketError('modules.tickets.errors.not_escalated')
        self.require(actor, category, ticket, PERM_ADMIN)

        await self.bot.db.set_escalated(channel.id, False)
        ticket = await self.get_ticket(channel.id) or ticket
        await self.sync_permissions(channel, category, ticket)
        await self.sync_status_prefix(channel, category, ticket)
        return ticket

    # ------------------------------------------------------------------ #
    # Move
    # ------------------------------------------------------------------ #
    async def move_ticket(self, channel: discord.TextChannel, actor: discord.Member,
                          panel_id: str, category_id: str) -> Dict[str, Any]:
        """Move the ticket to another category (any panel of the guild).

        The destination's permissions replace the current ones — that is the
        point of moving — so the actor needs ``move`` **here**; a category they
        could not work in is still a valid destination, exactly like handing a
        ticket over to another team.
        """
        ticket, panel, category = await self.resolve(channel)
        self.require(actor, category, ticket, PERM_MOVE)

        module = await self.get_module(channel.guild.id)
        target_panel, target = locate_category(
            {'panels': module.panels if module else []}, panel_id, category_id)
        if not target:
            raise TicketError('modules.tickets.errors.unknown_category')
        if target['id'] == category['id']:
            raise TicketError('modules.tickets.errors.same_category')

        parent = channel.guild.get_channel(target.get('discord_category_id')) \
            if target.get('discord_category_id') else None
        if not isinstance(parent, discord.CategoryChannel):
            raise TicketError('modules.tickets.errors.destination_missing')

        await self.bot.db.set_ticket_category(channel.id, target_panel['id'], target['id'])
        ticket = await self.get_ticket(channel.id) or ticket

        try:
            await channel.edit(
                category=parent,
                # The destination decides whether the ticket carries a status
                # dot at all, so the name is rebuilt here rather than left
                # showing the previous category's convention.
                name=apply_status_prefix(channel.name,
                                         ticket_status_dot(target, ticket)),
                overwrites=self.build_overwrites(channel.guild, target, ticket),
                reason=f"Moddy ticket moved by {actor} ({actor.id})",
            )
        except discord.Forbidden:
            raise TicketError('modules.tickets.errors.cannot_edit_channel')
        except discord.HTTPException as e:
            logger.error(f"[Tickets] Could not move channel {channel.id}: {e}")
            raise TicketError('modules.tickets.errors.cannot_edit_channel')

        logger.info(f"[Tickets] #{ticket['number']} moved to {target['id']} "
                    f"by {actor.id}")
        return ticket

    # ------------------------------------------------------------------ #
    # Rename
    # ------------------------------------------------------------------ #
    async def rename_ticket(self, channel: discord.TextChannel, actor: discord.Member,
                            name: str) -> str:
        ticket, panel, category = await self.resolve(channel)
        self.require(actor, category, ticket, PERM_RENAME)

        cleaned = render_channel_name(
            {'name_format': name, 'name': category['name']},
            member=actor, number=ticket['number'])
        if not cleaned:
            raise TicketError('modules.tickets.errors.invalid_name')
        # A rename must not drop the status dot: it is state, not decoration,
        # and the staffer typing a new name is not asking to hide it.
        cleaned = apply_status_prefix(cleaned, ticket_status_dot(category, ticket))

        try:
            await channel.edit(name=cleaned,
                               reason=f"Moddy ticket renamed by {actor} ({actor.id})")
        except discord.Forbidden:
            raise TicketError('modules.tickets.errors.cannot_edit_channel')
        except discord.HTTPException as e:
            logger.error(f"[Tickets] Could not rename channel {channel.id}: {e}")
            raise TicketError('modules.tickets.errors.cannot_edit_channel')
        return cleaned

    # ------------------------------------------------------------------ #
    # Participants
    # ------------------------------------------------------------------ #
    async def add_participant(self, channel: discord.TextChannel, actor: discord.Member,
                              target: Union[discord.Member, discord.Role]) -> Dict[str, Any]:
        ticket, panel, category = await self.resolve(channel)
        self.require(actor, category, ticket, PERM_PARTICIPANTS)

        is_role = isinstance(target, discord.Role)
        key = 'participant_roles' if is_role else 'participants'
        current = list(ticket.get(key, []))

        if not is_role and target.id == ticket['owner_id']:
            raise TicketError('modules.tickets.errors.already_participant')
        if target.id in current:
            raise TicketError('modules.tickets.errors.already_participant')

        current.append(target.id)
        await self.bot.db.set_participants(
            channel.id, **{'roles' if is_role else 'users': current})
        ticket = await self.get_ticket(channel.id) or ticket
        await self.sync_permissions(channel, category, ticket)
        return ticket

    async def remove_participant(self, channel: discord.TextChannel, actor: discord.Member,
                                 target: Union[discord.Member, discord.Role]
                                 ) -> Dict[str, Any]:
        ticket, panel, category = await self.resolve(channel)
        self.require(actor, category, ticket, PERM_PARTICIPANTS)

        if not isinstance(target, discord.Role) and target.id == ticket['owner_id']:
            # Removing the opener from their own ticket makes the ticket
            # meaningless; closing it is the action they are looking for.
            raise TicketError('modules.tickets.errors.cannot_remove_owner')

        is_role = isinstance(target, discord.Role)
        key = 'participant_roles' if is_role else 'participants'
        current = list(ticket.get(key, []))
        if target.id not in current:
            raise TicketError('modules.tickets.errors.not_a_participant')

        current.remove(target.id)
        await self.bot.db.set_participants(
            channel.id, **{'roles' if is_role else 'users': current})
        ticket = await self.get_ticket(channel.id) or ticket
        await self.sync_permissions(channel, category, ticket)
        return ticket

    # ------------------------------------------------------------------ #
    # Staff thread
    # ------------------------------------------------------------------ #
    async def open_staff_thread(self, channel: discord.TextChannel,
                                actor: discord.Member) -> discord.Thread:
        """Create (or join) the ticket's private staff thread.

        A **private** thread, so the opener never sees it. Discord has no
        role-based membership for threads, so the thread is not pre-filled with
        every staff member: each staffer joins by running the action once,
        which is also what makes it cheap on a busy server.
        """
        ticket, panel, category = await self.resolve(channel)
        self.require(actor, category, ticket, PERM_STAFF_THREAD)

        locale = await self.ticket_locale(channel.guild)
        thread = None
        if ticket.get('staff_thread_id'):
            thread = channel.guild.get_channel_or_thread(ticket['staff_thread_id'])
            if thread is None:
                try:
                    thread = await self.bot.fetch_channel(ticket['staff_thread_id'])
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    thread = None

        if thread is None:
            try:
                thread = await channel.create_thread(
                    name=t('modules.tickets.staff_thread.name', locale=locale,
                           number=ticket['number'])[:100],
                    type=discord.ChannelType.private_thread,
                    invitable=False,
                    reason=f"Moddy ticket staff thread opened by {actor} ({actor.id})",
                )
            except discord.Forbidden:
                raise TicketError('modules.tickets.errors.cannot_create_thread')
            except discord.HTTPException as e:
                logger.error(f"[Tickets] Could not create staff thread in "
                             f"{channel.id}: {e}")
                raise TicketError('modules.tickets.errors.cannot_create_thread')

            await self.bot.db.set_staff_thread(channel.id, thread.id)
            try:
                await thread.send(t('modules.tickets.staff_thread.intro',
                                    locale=locale, number=ticket['number']))
            except discord.HTTPException:
                pass

        try:
            await thread.add_user(actor)
        except discord.HTTPException:
            pass
        return thread

    def may_be_in_staff_thread(self, member: discord.Member,
                               category: Dict[str, Any],
                               ticket: Dict[str, Any]) -> bool:
        """Is this member allowed inside the ticket's private staff thread?

        Only the people who may see this category's tickets as **staff**. The
        opener and anyone added to the ticket by hand hold ``view`` through the
        ticket itself, not through the category, and the staff thread is
        precisely the room they must not be in — so their role grant is what
        decides, never their presence in the ticket.
        """
        if getattr(member, 'bot', False):
            return True
        return PERM_VIEW in member_permissions(member, category)

    async def evict_from_staff_thread(self, thread: discord.Thread,
                                      user: discord.abc.User) -> bool:
        """Remove somebody who should not be reading the staff thread.

        Mentioning a member inside a thread adds them to it — one stray ping is
        all it takes to hand a ticket's opener the staff-only conversation
        about them. Pulling them straight back out is the only way that stays
        true whoever typed the mention.
        """
        try:
            await thread.remove_user(user)
            return True
        except discord.HTTPException as e:
            logger.warning(f"[Tickets] Could not remove {user.id} from staff "
                           f"thread {thread.id}: {e}")
            return False

    # ------------------------------------------------------------------ #
    # Housekeeping
    # ------------------------------------------------------------------ #
    async def forget_channel(self, channel_id: int) -> None:
        """Drop the row of a ticket whose channel no longer exists."""
        if self.bot.db:
            await self.bot.db.delete_ticket(channel_id)
