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

See docs/TICKETS.md.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple, Union

import discord

from modules.tickets import (
    MODULE_ID,
    PERM_ADMIN,
    PERM_CLOSE,
    PERM_MOVE,
    PERM_PARTICIPANTS,
    PERM_RENAME,
    PERM_STAFF_THREAD,
    PERM_VIEW,
    can_open,
    locate_category,
    member_permissions,
    render_channel_name,
    render_text,
    staff_role_ids,
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
            logger.error(f"[Tickets] Could not load module for guild {guild_id}: {e}")
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

    def ticket_locale(self, category: Dict[str, Any]) -> str:
        return category.get('locale') or 'en-US'

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

        - **Escalated** tickets only keep the roles holding ``admin``.
        - **Closed** tickets keep staff and hide the channel from the member
          side, so a closed ticket disappears from the opener's channel list
          without losing anything: reopening restores it exactly.
        """
        escalated = bool(ticket.get('escalated'))
        closed = ticket.get('status') == 'closed'

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
            if role:
                overwrites[role] = _STAFF_OVERWRITE

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
            # drop them).
            member_ids = [ticket['owner_id'], *ticket.get('participants', [])]
            for user_id in member_ids:
                member = guild.get_member(user_id)
                if member and member not in overwrites:
                    overwrites[member] = _MEMBER_OVERWRITE

        return overwrites

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
        locale = self.ticket_locale(category)
        number = await self.bot.db.next_ticket_number(guild.id)
        draft = {'owner_id': member.id, 'status': 'open', 'escalated': False,
                 'participants': [], 'participant_roles': []}
        try:
            channel = await guild.create_text_channel(
                name=render_channel_name(category, member=member, number=number),
                category=parent,
                overwrites=self.build_overwrites(guild, category, draft),
                topic=t('modules.tickets.channel.topic', locale=locale,
                        user=str(member), category=category['name'])[:1024],
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
        # conversation gets.
        body = category.get('open_message') or t(
            'modules.tickets.channel.default_open_message', locale=locale)
        rendered = render_text(body, member=member, guild=guild, category=category,
                               number=ticket['number'], channel=channel)
        try:
            message = await channel.send(
                content=self._ping_content(guild, category, member),
                view=build_ticket_message(ticket, category, rendered, locale=locale),
                allowed_mentions=discord.AllowedMentions(users=True, roles=True),
            )
            await message.pin(reason="Moddy tickets: control message")
        except discord.HTTPException as e:
            logger.warning(f"[Tickets] Could not post the control message in "
                           f"{channel.id}: {e}")

        logger.info(f"[Tickets] #{ticket['number']} opened in guild {guild.id} "
                    f"by {member.id} (category {category['id']})")
        return channel

    def _ping_content(self, guild: discord.Guild, category: Dict[str, Any],
                      member: discord.Member) -> Optional[str]:
        """Mentions posted alongside the control message (opener + ping roles)."""
        parts = [member.mention]
        for role_id in category.get('ping_role_ids', []):
            role = guild.get_role(role_id)
            if role:
                parts.append(role.mention)
        return " ".join(parts) if parts else None

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

        locale = self.ticket_locale(category)
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

        owner = channel.guild.get_member(ticket['owner_id'])
        if not owner or owner.bot:
            return
        try:
            await owner.send(view=build_close_dm(
                channel.guild, ticket, category, actor, reason,
                locale=self.ticket_locale(category)))
        except (discord.Forbidden, discord.HTTPException):
            pass  # closed DMs are the norm, not an error

    async def reopen_ticket(self, channel: discord.TextChannel,
                            actor: discord.Member) -> Dict[str, Any]:
        ticket, panel, category = await self.resolve(channel)
        if ticket['status'] != 'closed':
            raise TicketError('modules.tickets.errors.not_closed')
        self.require(actor, category, ticket, PERM_CLOSE)

        await self.bot.db.set_ticket_status(channel.id, 'open')
        ticket = await self.get_ticket(channel.id) or ticket
        await self.sync_permissions(channel, category, ticket)
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

        locale = self.ticket_locale(category)
        mentions = [r.mention for r in (
            channel.guild.get_role(rid) for rid in staff_role_ids(category, permission=PERM_CLOSE)
        ) if r]
        try:
            await channel.send(
                content=" ".join(mentions) or None,
                view=build_close_request_message(ticket, actor, reason, locale=locale),
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
        except discord.HTTPException as e:
            logger.warning(f"[Tickets] Could not post the close request in "
                           f"{channel.id}: {e}")
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
    # Escalate
    # ------------------------------------------------------------------ #
    async def escalate(self, channel: discord.TextChannel, actor: discord.Member,
                       *, reason: Optional[str] = None,
                       keep_participants: bool = True) -> Dict[str, Any]:
        """Restrict the ticket to the responsibles, its opener and (optionally)
        the people added by hand.

        Everyone else — every staff role that is not ``admin`` — loses access.
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
        await self.bot.db.set_escalated(channel.id, True)
        ticket = await self.get_ticket(channel.id) or ticket

        await self.sync_permissions(channel, category, ticket)

        locale = self.ticket_locale(category)
        mentions = [r.mention for r in (
            channel.guild.get_role(rid) for rid in staff_role_ids(category, permission=PERM_ADMIN)
        ) if r]
        try:
            await channel.send(
                content=" ".join(mentions) or None,
                view=build_escalation_notice(ticket, actor, reason, locale=locale),
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
        except discord.HTTPException as e:
            logger.warning(f"[Tickets] Could not post the escalation notice in "
                           f"{channel.id}: {e}")

        logger.info(f"[Tickets] #{ticket['number']} escalated by {actor.id}")
        return ticket

    async def deescalate(self, channel: discord.TextChannel,
                         actor: discord.Member) -> Dict[str, Any]:
        """Undo an escalation — the staff roles regain access."""
        ticket, panel, category = await self.resolve(channel)
        if not ticket.get('escalated'):
            raise TicketError('modules.tickets.errors.not_escalated')
        self.require(actor, category, ticket, PERM_ADMIN)

        await self.bot.db.set_escalated(channel.id, False)
        ticket = await self.get_ticket(channel.id) or ticket
        await self.sync_permissions(channel, category, ticket)
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

        locale = self.ticket_locale(category)
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

    # ------------------------------------------------------------------ #
    # Housekeeping
    # ------------------------------------------------------------------ #
    async def forget_channel(self, channel_id: int) -> None:
        """Drop the row of a ticket whose channel no longer exists."""
        if self.bot.db:
            await self.bot.db.delete_ticket(channel_id)
