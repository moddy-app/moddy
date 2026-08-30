"""
Tickets cog — the ``/ticket`` command group and the module's Discord events.

**Every** ticket action is available here as a slash command, including the
five that also have a button on the pinned control bar. The buttons are the
shortcut; the commands are the contract. Both call the same
:class:`~services.ticket_service.TicketService` method, so they can never drift
apart.

The group is declared at **module level**, not as a Cog attribute: a Cog
attribute would be added to the global command tree by discord.py, and the
whole point is that ``/ticket`` exists **only in guilds where the Tickets
module is enabled**. ``setup()`` hands it to
``ModdyBot.register_module_commands``, which publishes it per guild and
withdraws it the moment the module is switched off.

See docs/TICKETS.md.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Union

import discord
from discord import app_commands
from discord.ext import commands

from modules.tickets import (
    MODULE_ID,
    PERM_RENAME,
    member_permissions,
)
from services.ticket_service import TicketError
from utils.components_v2 import create_success_message
from utils.i18n import i18n, t
from utils.interaction_response import safe_defer
from utils.ticket_views import (
    TicketRenameModal,
    handle_ticket_error,
    open_participants_modal,
    run_claim,
    run_staff_thread,
    send_error,
    send_success,
    start_escalation,
)

logger = logging.getLogger('moddy.cogs.tickets')


# --------------------------------------------------------------------------- #
# The command group (module-level on purpose — see the module docstring)
# --------------------------------------------------------------------------- #
ticket_group = app_commands.Group(
    name="ticket",
    description="Manage the ticket you are in",
    guild_only=True,
)


async def _service_and_ticket(interaction: discord.Interaction, *,
                              defer: bool = False):
    """``(service, ticket, panel, category)`` for the channel the command ran in.

    Answers the user and returns ``None`` when the channel is not a ticket —
    the check every single subcommand starts with.

    ``defer=True`` acknowledges the interaction *before* the resolve, which is
    a database read: a subcommand that ends in a plain answer must never let
    that read eat its 3-second window. Subcommands that open a modal (or hand
    over to a helper that acknowledges by itself) leave it off.
    """
    if defer:
        await safe_defer(interaction, ephemeral=True, thinking=True)

    service = getattr(interaction.client, 'tickets', None)
    locale = i18n.get_user_locale(interaction)
    if service is None or not isinstance(interaction.channel, discord.TextChannel):
        await send_error(interaction,
                         t('modules.tickets.errors.not_a_ticket', locale=locale), locale)
        return None
    try:
        ticket, panel, category = await service.resolve(interaction.channel)
    except TicketError as e:
        await handle_ticket_error(interaction, e)
        return None
    return service, ticket, panel, category


# --------------------------------------------------------------------------- #
# Close / reopen
# --------------------------------------------------------------------------- #
@ticket_group.command(name="close", description="Close this ticket")
@app_commands.describe(reason="Why the ticket is being closed")
async def ticket_close(interaction: discord.Interaction, reason: Optional[str] = None):
    resolved = await _service_and_ticket(interaction, defer=True)
    if not resolved:
        return
    service = resolved[0]
    locale = i18n.get_user_locale(interaction)
    try:
        await service.close_ticket(interaction.channel, interaction.user, reason)
    except TicketError as e:
        await handle_ticket_error(interaction, e)
        return
    await send_success(interaction,
                       t('modules.tickets.close.done_title', locale=locale),
                       t('modules.tickets.close.done_description', locale=locale))


@ticket_group.command(name="reopen", description="Reopen this closed ticket")
async def ticket_reopen(interaction: discord.Interaction):
    resolved = await _service_and_ticket(interaction, defer=True)
    if not resolved:
        return
    service = resolved[0]
    locale = i18n.get_user_locale(interaction)
    try:
        await service.reopen_ticket(interaction.channel, interaction.user)
    except TicketError as e:
        await handle_ticket_error(interaction, e)
        return
    await send_success(interaction,
                       t('modules.tickets.reopen.done_title', locale=locale),
                       t('modules.tickets.reopen.done_description', locale=locale))


@ticket_group.command(name="close-request",
                      description="Ask the staff to close this ticket")
@app_commands.describe(reason="Why you would like the ticket closed")
async def ticket_close_request(interaction: discord.Interaction,
                               reason: Optional[str] = None):
    resolved = await _service_and_ticket(interaction, defer=True)
    if not resolved:
        return
    service = resolved[0]
    locale = i18n.get_user_locale(interaction)
    try:
        await service.request_close(interaction.channel, interaction.user, reason)
    except TicketError as e:
        await handle_ticket_error(interaction, e)
        return
    await send_success(
        interaction,
        t('modules.tickets.close_request.sent_title', locale=locale),
        t('modules.tickets.close_request.sent_description', locale=locale))


# --------------------------------------------------------------------------- #
# Escalation
# --------------------------------------------------------------------------- #
@ticket_group.command(name="escalate",
                      description="Restrict this ticket to the responsibles")
@app_commands.describe(reason="Why the ticket is being escalated")
async def ticket_escalate(interaction: discord.Interaction,
                          reason: Optional[str] = None):
    resolved = await _service_and_ticket(interaction)
    if not resolved:
        return
    service, ticket, panel, category = resolved
    await start_escalation(interaction, service, ticket, category, reason)


@ticket_group.command(name="deescalate", description="Undo the escalation")
async def ticket_deescalate(interaction: discord.Interaction):
    resolved = await _service_and_ticket(interaction, defer=True)
    if not resolved:
        return
    service = resolved[0]
    locale = i18n.get_user_locale(interaction)
    try:
        await service.deescalate(interaction.channel, interaction.user)
    except TicketError as e:
        await handle_ticket_error(interaction, e)
        return
    await send_success(
        interaction,
        t('modules.tickets.escalate.cancelled_title', locale=locale),
        t('modules.tickets.escalate.cancelled_description', locale=locale))


# --------------------------------------------------------------------------- #
# Move
# --------------------------------------------------------------------------- #
async def _category_autocomplete(interaction: discord.Interaction, current: str
                                 ) -> List[app_commands.Choice[str]]:
    """Every ticket category of the guild, as ``Panel › Category``.

    The value is ``panel_id:category_id`` because a category is only unique
    inside its panel.
    """
    service = getattr(interaction.client, 'tickets', None)
    if service is None or interaction.guild_id is None:
        return []
    module = await service.get_module(interaction.guild_id)
    if not module:
        return []

    needle = (current or "").lower()
    choices = []
    for panel in module.panels:
        for category in panel.get('categories', []):
            if not category.get('enabled') or not category.get('discord_category_id'):
                continue
            label = f"{panel['name']} › {category['name']}"[:100]
            if needle and needle not in label.lower():
                continue
            choices.append(app_commands.Choice(
                name=label, value=f"{panel['id']}:{category['id']}"))
            if len(choices) >= 25:
                return choices
    return choices


@ticket_group.command(name="move", description="Move this ticket to another category")
@app_commands.describe(category="The category the ticket should move to")
@app_commands.autocomplete(category=_category_autocomplete)
async def ticket_move(interaction: discord.Interaction, category: str):
    resolved = await _service_and_ticket(interaction, defer=True)
    if not resolved:
        return
    service = resolved[0]
    locale = i18n.get_user_locale(interaction)

    panel_id, _, category_id = category.partition(":")
    if not panel_id or not category_id:
        await send_error(interaction,
                         t('modules.tickets.errors.unknown_category', locale=locale),
                         locale)
        return
    try:
        await service.move_ticket(interaction.channel, interaction.user,
                                  panel_id, category_id)
    except TicketError as e:
        await handle_ticket_error(interaction, e)
        return
    await send_success(interaction,
                       t('modules.tickets.move.done_title', locale=locale),
                       t('modules.tickets.move.done_description', locale=locale))


# --------------------------------------------------------------------------- #
# Rename
# --------------------------------------------------------------------------- #
@ticket_group.command(name="rename", description="Rename this ticket")
@app_commands.describe(name="The new channel name (leave empty to open a form)")
async def ticket_rename(interaction: discord.Interaction, name: Optional[str] = None):
    resolved = await _service_and_ticket(interaction)
    if not resolved:
        return
    service, ticket, panel, category = resolved
    locale = i18n.get_user_locale(interaction)

    if PERM_RENAME not in member_permissions(interaction.user, category, ticket):
        await send_error(interaction,
                         t('modules.tickets.errors.missing_permission', locale=locale),
                         locale)
        return

    async def apply(inner: discord.Interaction, new_name: str):
        await inner.response.defer(ephemeral=True, thinking=True)
        try:
            final = await service.rename_ticket(inner.channel, inner.user, new_name)
        except TicketError as e:
            await handle_ticket_error(inner, e)
            return
        await send_success(
            inner, t('modules.tickets.rename.done_title', locale=locale),
            t('modules.tickets.rename.done_description', locale=locale, name=final))

    if name is None:
        modal = TicketRenameModal(locale, interaction.channel.name, apply)
        modal.bot = interaction.client
        await interaction.response.send_modal(modal)
        return
    await apply(interaction, name)


# --------------------------------------------------------------------------- #
# Participants
# --------------------------------------------------------------------------- #
@ticket_group.command(name="add", description="Add a member or a role to this ticket")
@app_commands.describe(target="The member or role to add")
async def ticket_add(interaction: discord.Interaction,
                     target: Union[discord.Member, discord.Role]):
    await _participant_action(interaction, target, add=True)


@ticket_group.command(name="remove",
                      description="Remove a member or a role from this ticket")
@app_commands.describe(target="The member or role to remove")
async def ticket_remove(interaction: discord.Interaction,
                        target: Union[discord.Member, discord.Role]):
    await _participant_action(interaction, target, add=False)


async def _participant_action(interaction: discord.Interaction,
                              target: Union[discord.Member, discord.Role],
                              *, add: bool):
    resolved = await _service_and_ticket(interaction, defer=True)
    if not resolved:
        return
    service = resolved[0]
    locale = i18n.get_user_locale(interaction)
    try:
        if add:
            await service.add_participant(interaction.channel, interaction.user, target)
        else:
            await service.remove_participant(interaction.channel, interaction.user, target)
    except TicketError as e:
        await handle_ticket_error(interaction, e)
        return

    key = 'added' if add else 'removed'
    await send_success(
        interaction,
        t(f'modules.tickets.participants.{key}_title', locale=locale),
        t(f'modules.tickets.participants.{key}_description', locale=locale,
          target=target.mention))


@ticket_group.command(name="participants",
                      description="See and edit who has access to this ticket")
async def ticket_participants(interaction: discord.Interaction):
    resolved = await _service_and_ticket(interaction)
    if not resolved:
        return
    service, ticket, panel, category = resolved
    # The form opens pre-filled with whoever is in the ticket right now — see
    # utils/ticket_views.TicketParticipantsModal.
    await open_participants_modal(interaction, ticket, category)


# --------------------------------------------------------------------------- #
# Claim
# --------------------------------------------------------------------------- #
@ticket_group.command(name="claim", description="Take this ticket in charge")
async def ticket_claim(interaction: discord.Interaction):
    resolved = await _service_and_ticket(interaction)
    if not resolved:
        return
    await run_claim(interaction, resolved[0], force=True)


@ticket_group.command(name="unclaim",
                      description="Release this ticket so anyone can take it")
async def ticket_unclaim(interaction: discord.Interaction):
    resolved = await _service_and_ticket(interaction)
    if not resolved:
        return
    await run_claim(interaction, resolved[0], force=False)


# --------------------------------------------------------------------------- #
# Staff thread
# --------------------------------------------------------------------------- #
@ticket_group.command(name="staff-thread",
                      description="Open or join the private staff thread")
async def ticket_staff_thread(interaction: discord.Interaction):
    resolved = await _service_and_ticket(interaction)
    if not resolved:
        return
    await run_staff_thread(interaction, resolved[0])


# --------------------------------------------------------------------------- #
# Info
# --------------------------------------------------------------------------- #
@ticket_group.command(name="info", description="Show this ticket's details")
async def ticket_info(interaction: discord.Interaction):
    resolved = await _service_and_ticket(interaction, defer=True)
    if not resolved:
        return
    service, ticket, panel, category = resolved
    locale = i18n.get_user_locale(interaction)

    granted = sorted(member_permissions(interaction.user, category, ticket))
    fields = [
        {"name": t('modules.tickets.fields.opener', locale=locale),
         "value": f"<@{ticket['owner_id']}>"},
        {"name": t('modules.tickets.fields.category', locale=locale),
         "value": f"`{category['name']}`"},
        {"name": t('modules.tickets.fields.status', locale=locale),
         "value": t(f"modules.tickets.status.{ticket['status']}", locale=locale)},
    ]
    if ticket.get('escalated'):
        fields.append({
            "name": t('modules.tickets.fields.escalated', locale=locale),
            "value": t('modules.tickets.status.escalated', locale=locale),
        })
    if category.get('claim_enabled', True):
        holder = ticket.get('claimed_by')
        fields.append({
            "name": t('modules.tickets.fields.claimed_by', locale=locale),
            "value": f"<@{holder}>" if holder
                     else t('modules.tickets.claim.nobody', locale=locale),
        })
    participants = [f"<@{uid}>" for uid in ticket.get('participants', [])]
    participants += [f"<@&{rid}>" for rid in ticket.get('participant_roles', [])]
    if participants:
        fields.append({
            "name": t('modules.tickets.fields.participants', locale=locale),
            "value": ", ".join(participants)[:1000],
        })
    fields.append({
        "name": t('modules.tickets.fields.your_permissions', locale=locale),
        "value": ", ".join(
            t(f'modules.tickets.permissions.{p}.name', locale=locale) for p in granted
        ) or t('modules.tickets.fields.none', locale=locale),
    })

    await interaction.followup.send(
        view=create_success_message(
            t('modules.tickets.info.title', locale=locale, number=ticket['number']),
            t('modules.tickets.info.description', locale=locale),
            fields=fields),
        ephemeral=True)


# --------------------------------------------------------------------------- #
# The cog (events + registration)
# --------------------------------------------------------------------------- #
class Tickets(commands.Cog):
    """Keeps the ticket table in step with what actually exists in Discord."""

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        """A ticket channel deleted by hand must not leave a row behind.

        Without this, the member's open-ticket quota would count a channel that
        no longer exists and they could never open another one.
        """
        service = getattr(self.bot, 'tickets', None)
        if service is None or not self.bot.db:
            return
        try:
            ticket = await service.get_ticket(channel.id)
            if ticket:
                await service.forget_channel(channel.id)
                logger.info(f"[Tickets] #{ticket['number']} forgotten "
                            f"(channel {channel.id} deleted)")
        except Exception as e:
            # A row left behind burns the member's open-ticket quota forever,
            # so this failure needs an error code, not just a log line.
            from cogs.error_handler import report_error
            await report_error(
                self.bot, e, source="Cog:Tickets.on_guild_channel_delete",
                guild=getattr(channel, "guild", None),
            )

    @commands.Cog.listener()
    async def on_thread_member_join(self, member: discord.ThreadMember):
        """Keep the staff thread staff-only, whoever gets mentioned in it.

        Mentioning somebody inside a thread adds them to it. In a ticket's
        private staff thread that is enough to hand the member the thread the
        staff are discussing them in — one stray ping, no warning. Anyone who
        is not allowed to see this category's tickets as staff is therefore
        pulled straight back out, and the same applies to the people added to
        the ticket by hand: the staff thread is the one room they are not in.
        """
        thread = member.thread
        service = getattr(self.bot, 'tickets', None)
        if service is None or thread is None or thread.parent is None:
            return
        if member.id == self.bot.user.id:
            return

        try:
            ticket = await service.get_ticket(thread.parent.id)
            if not ticket or ticket.get('staff_thread_id') != thread.id:
                return

            guild_member = thread.guild.get_member(member.id)
            if guild_member is None:
                guild_member = await thread.guild.fetch_member(member.id)

            _, _, category = await service.resolve(thread.parent)
            if service.may_be_in_staff_thread(guild_member, category, ticket):
                return

            if await service.evict_from_staff_thread(thread, guild_member):
                logger.info(f"[Tickets] Removed {member.id} from the staff "
                            f"thread of ticket #{ticket['number']}")
        except (TicketError, discord.HTTPException):
            # Not a ticket any more, or the member left in the meantime.
            return
        except Exception as e:
            # This guard is what keeps a ticket's staff thread staff-only:
            # a silent failure is a privacy leak, so it is filed centrally.
            from cogs.error_handler import report_error
            await report_error(
                self.bot, e, source="Cog:Tickets.on_thread_member_join",
                guild=thread.guild,
            )


async def setup(bot):
    await bot.add_cog(Tickets(bot))
    # Published per guild, only where the module is enabled — never globally.
    bot.register_module_commands(MODULE_ID, [ticket_group])
