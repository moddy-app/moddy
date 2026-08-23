"""
Every Discord surface of the Tickets module.

Four kinds of message live here, and they have deliberately different
persistence models:

- **The panel** (posted in a public channel). Its buttons/options carry the
  panel and category ids, so they are :class:`discord.ui.DynamicItem`\\ s
  reconstructed from their ``custom_id`` on every click.
- **The ticket control bar** (pinned in the ticket channel), **the closing
  card**, **the close request** and **the escalation notice**. A ticket action
  always happens *inside* its own channel, so ``interaction.channel_id`` is the
  ticket's identity: these need no id in their custom_ids at all and are plain
  registered persistent views with static ones.
- **Ephemeral helper panels** (participants, escalation confirmation) — same
  reasoning, same static ids.
- **The closing DM**, which has no interactive child at all.

Authorization is never carried by a view. Every callback resolves the ticket
from the channel, the category from the guild's config, and the actor's
permissions from their roles — see ``services/ticket_service.py``. A stale
button clicked a month after a restart is therefore exactly as safe as a fresh
one.

See docs/TICKETS.md and docs/PERSISTENT_VIEWS.md.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import discord
from discord import ui

from cogs.error_handler import BaseView, BaseModal
from modules.tickets import (
    BUTTON_STYLES,
    DEFAULT_ACCENT_COLOR,
    MAX_TICKET_MESSAGE,
    PERM_ADMIN,
    PERM_CLOSE,
    PERM_PARTICIPANTS,
    STYLE_SELECT,
    find_category,
    find_panel,
    member_permissions,
    parse_emoji,
)
from utils.components_v2 import create_error_message, create_success_message
from utils.emojis import (
    BACK, DELETE, GROUPS, INFO, TICKET, TICKET_CLOSE, TICKET_CLOSE_REQUEST,
    TICKET_ESCALATE, TICKET_PARTICIPANTS, TICKET_REOPEN, TICKET_STAFF_THREAD,
    UNDONE,
)
from utils.i18n import i18n, t

logger = logging.getLogger('moddy.tickets.views')

# --------------------------------------------------------------------------- #
# custom_id constants.
#
# The ticket-scoped ones are static on purpose: the channel the click comes
# from *is* the ticket. Adding an id would only create a second source of truth
# that could disagree with the channel.
# --------------------------------------------------------------------------- #
_CID_CTRL_CLOSE = "moddy:tickets:ctrl:close"
_CID_CTRL_CLOSE_REQUEST = "moddy:tickets:ctrl:close_request"
_CID_CTRL_ESCALATE = "moddy:tickets:ctrl:escalate"
_CID_CTRL_STAFF_THREAD = "moddy:tickets:ctrl:staff_thread"
_CID_CTRL_PARTICIPANTS = "moddy:tickets:ctrl:participants"

_CID_CLOSED_REOPEN = "moddy:tickets:closed:reopen"
_CID_CLOSED_DELETE = "moddy:tickets:closed:delete"

_CID_REQUEST_ACCEPT = "moddy:tickets:request:accept"
_CID_REQUEST_REFUSE = "moddy:tickets:request:refuse"

_CID_ESCALATED_CANCEL = "moddy:tickets:escalated:cancel"

_CID_MEMBERS_USERS = "moddy:tickets:members:users"
_CID_MEMBERS_ROLES = "moddy:tickets:members:roles"

_CID_ESC_KEEP = "moddy:tickets:escalate:keep"
_CID_ESC_DROP = "moddy:tickets:escalate:drop"

# Panel ids are `p_xxxxxx` / `c_xxxxxx` (see modules/tickets.py).
_ENTRY_ID = r"[a-z0-9_]+"


# =========================================================================== #
# Shared helpers
# =========================================================================== #
def _guarded(callback):
    """Route a dynamic-item callback error to the central handler.

    A ``DynamicItem`` dispatched through ``bot.add_dynamic_items`` has no live
    ``BaseView``, so ``BaseView.on_error`` never fires and an unhandled
    exception would simply vanish. See docs/PERSISTENT_VIEWS.md.
    """
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as e:  # noqa: BLE001
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, e, self.__class__.__name__)
    return wrapper


async def send_error(interaction: discord.Interaction, description: str,
                     locale: Optional[str] = None) -> None:
    """Ephemeral error card, whatever state the interaction is in."""
    locale = locale or i18n.get_user_locale(interaction)
    view = create_error_message(
        t('modules.tickets.errors.title', locale=locale), description)
    if interaction.response.is_done():
        await interaction.followup.send(view=view, ephemeral=True)
    else:
        await interaction.response.send_message(view=view, ephemeral=True)


async def send_success(interaction: discord.Interaction, title: str,
                       description: str) -> None:
    view = create_success_message(title, description)
    if interaction.response.is_done():
        await interaction.followup.send(view=view, ephemeral=True)
    else:
        await interaction.response.send_message(view=view, ephemeral=True)


async def handle_ticket_error(interaction: discord.Interaction, error) -> None:
    """Turn a :class:`TicketError` into an ephemeral card in the actor's language."""
    locale = i18n.get_user_locale(interaction)
    await send_error(interaction, error.message(locale), locale)


def _accent(panel: Dict[str, Any]) -> discord.Colour:
    return discord.Colour(panel.get('accent_color') or DEFAULT_ACCENT_COLOR)


def _category_label(category: Dict[str, Any]) -> str:
    return (category.get('name') or '')[:80]


# =========================================================================== #
# 1. The panel (public message with the open buttons / select)
# =========================================================================== #
class TicketOpenButton(
    ui.DynamicItem[ui.Button],
    template=rf"moddy:tickets:open:(?P<panel_id>{_ENTRY_ID}):(?P<category_id>{_ENTRY_ID})",
):
    """One category button on a ticket panel. Auth: public (checked on click)."""

    def __init__(self, panel_id: str, category_id: str,
                 category: Optional[Dict[str, Any]] = None):
        category = category or {}
        super().__init__(
            ui.Button(
                label=_category_label(category) or "—",
                style=BUTTON_STYLES.get(category.get('button_style'),
                                        discord.ButtonStyle.primary),
                emoji=parse_emoji(category.get('emoji')),
                custom_id=f"moddy:tickets:open:{panel_id}:{category_id}",
            )
        )
        self.panel_id = panel_id
        self.category_id = category_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match['panel_id'], match['category_id'])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        await _open_from_panel(interaction, self.panel_id, self.category_id)


class TicketOpenSelect(
    ui.DynamicItem[ui.Select],
    template=rf"moddy:tickets:opensel:(?P<panel_id>{_ENTRY_ID})",
):
    """The category dropdown of a select-style panel. Auth: public."""

    def __init__(self, panel_id: str, panel: Optional[Dict[str, Any]] = None,
                 categories: Optional[List[Dict[str, Any]]] = None):
        panel = panel or {}
        options = [
            discord.SelectOption(
                label=_category_label(category) or "—",
                value=category['id'],
                description=(category.get('description') or None),
                emoji=parse_emoji(category.get('emoji')),
            )
            for category in (categories or [])
        ] or [discord.SelectOption(label="—", value="none")]

        super().__init__(
            ui.Select(
                placeholder=panel.get('placeholder') or None,
                options=options,
                min_values=1, max_values=1,
                custom_id=f"moddy:tickets:opensel:{panel_id}",
            )
        )
        self.panel_id = panel_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match['panel_id'])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        values = interaction.data.get('values') or []
        if not values or values[0] == "none":
            await interaction.response.defer()
            return
        await _open_from_panel(interaction, self.panel_id, values[0])


async def _open_from_panel(interaction: discord.Interaction, panel_id: str,
                           category_id: str) -> None:
    """Shared body of both panel controls: open a ticket, or say why not."""
    from services.ticket_service import TicketError

    locale = i18n.get_user_locale(interaction)
    bot = interaction.client
    service = getattr(bot, 'tickets', None)
    if service is None or interaction.guild is None:
        await send_error(interaction,
                         t('modules.tickets.errors.unavailable', locale=locale), locale)
        return

    module = await service.get_module(interaction.guild_id)
    if not module or not module.enabled:
        await send_error(interaction,
                         t('modules.tickets.errors.module_disabled', locale=locale), locale)
        return

    panel = find_panel({'panels': module.panels}, panel_id)
    category = find_category(panel, category_id)
    if not panel or not category:
        await send_error(interaction,
                         t('modules.tickets.errors.category_gone', locale=locale), locale)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        channel = await service.open_ticket(interaction.user, panel, category)
    except TicketError as e:
        await interaction.followup.send(
            view=create_error_message(
                t('modules.tickets.errors.title', locale=locale), e.message(locale)),
            ephemeral=True)
        return

    await interaction.followup.send(
        view=create_success_message(
            t('modules.tickets.open.success_title', locale=locale),
            t('modules.tickets.open.success_description', locale=locale,
              channel=channel.mention)),
        ephemeral=True)


class TicketPanelView(BaseView):
    """The panel message. Not registered: it is a shell around dynamic items.

    Persistence lives entirely in :class:`TicketOpenButton` /
    :class:`TicketOpenSelect`, registered through :class:`TicketsPersistence` —
    same pattern as ``TranscribePromptView``. Registering the wrapper would be
    meaningless: it cannot be built without a panel, and its children
    reconstruct themselves from their custom_id on every click.
    """

    def __init__(self, panel: Dict[str, Any], categories: List[Dict[str, Any]]):
        super().__init__()  # timeout=None
        container = ui.Container(accent_colour=_accent(panel))

        title = panel.get('title') or panel.get('name')
        container.add_item(ui.TextDisplay(f"### {TICKET} {title}"))
        if panel.get('description'):
            container.add_item(ui.TextDisplay(panel['description']))

        self.add_item(container)

        if panel.get('style') == STYLE_SELECT:
            row = ui.ActionRow()
            row.add_item(TicketOpenSelect(panel['id'], panel, categories))
            container.add_item(row)
            return

        # Buttons, five per row (Discord's limit).
        for start in range(0, len(categories), 5):
            row = ui.ActionRow()
            for category in categories[start:start + 5]:
                row.add_item(TicketOpenButton(panel['id'], category['id'], category))
            container.add_item(row)


def build_panel_view(panel: Dict[str, Any]) -> TicketPanelView:
    """Render a panel from its stored configuration."""
    categories = [c for c in panel.get('categories', [])
                  if c.get('enabled') and c.get('discord_category_id')]
    return TicketPanelView(panel, categories)


# =========================================================================== #
# 2. The ticket control bar (pinned in the ticket channel)
# =========================================================================== #
async def _resolve(interaction: discord.Interaction):
    """``(service, ticket, panel, category)`` for the channel a click came from.

    Answers the user and returns ``None`` when the channel is not (or is no
    longer) a ticket, which is the only thing every caller does about it.
    """
    from services.ticket_service import TicketError

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


class TicketControlView(BaseView):
    """The pinned control bar of a ticket.

    Persistent: yes. Auth: derived from the channel — the ticket is resolved
    from ``interaction.channel_id`` and the actor's permissions from their
    roles in the ticket's category, on every single click. The buttons are
    shown to everyone who can see the ticket; each one refuses politely when
    the clicker may not use it, which is what lets a member discover
    *Request closure* instead of silently facing a bar with one button on it.
    """

    __persistent__ = True

    def __init__(self, ticket: Optional[Dict[str, Any]] = None,
                 category: Optional[Dict[str, Any]] = None,
                 body: Optional[str] = None, locale: str = "en-US"):
        super().__init__()  # timeout=None
        self.ticket = ticket or {}
        self.category = category or {}
        self.body = body
        self.locale = locale
        self._build_view()

    def _build_view(self):
        self.clear_items()
        container = ui.Container(accent_colour=discord.Colour(DEFAULT_ACCENT_COLOR))

        number = self.ticket.get('number')
        container.add_item(ui.TextDisplay(
            f"### {TICKET} "
            f"{t('modules.tickets.channel.title', locale=self.locale, number=number or '—')}"
        ))
        if self.ticket:
            container.add_item(ui.TextDisplay(
                f"-# {t('modules.tickets.channel.meta', locale=self.locale)} · "
                f"<@{self.ticket.get('owner_id')}> · "
                f"`{self.category.get('name', '—')}`"
            ))
        if self.body:
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(ui.TextDisplay(self.body))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(
            f"-# {t('modules.tickets.channel.commands_hint', locale=self.locale)}"))

        row = ui.ActionRow()
        row.add_item(self._button(
            _CID_CTRL_CLOSE, 'close', TICKET_CLOSE,
            discord.ButtonStyle.danger, self.on_close))
        row.add_item(self._button(
            _CID_CTRL_CLOSE_REQUEST, 'close_request', TICKET_CLOSE_REQUEST,
            discord.ButtonStyle.secondary, self.on_close_request))
        row.add_item(self._button(
            _CID_CTRL_ESCALATE, 'escalate', TICKET_ESCALATE,
            discord.ButtonStyle.secondary, self.on_escalate))
        row.add_item(self._button(
            _CID_CTRL_STAFF_THREAD, 'staff_thread', TICKET_STAFF_THREAD,
            discord.ButtonStyle.secondary, self.on_staff_thread))
        row.add_item(self._button(
            _CID_CTRL_PARTICIPANTS, 'participants', TICKET_PARTICIPANTS,
            discord.ButtonStyle.secondary, self.on_participants))
        container.add_item(row)

        self.add_item(container)

    def _button(self, custom_id: str, key: str, emoji: str,
                style: discord.ButtonStyle, callback) -> ui.Button:
        button = ui.Button(
            label=t(f'modules.tickets.actions.{key}', locale=self.locale)[:80],
            style=style, custom_id=custom_id,
            emoji=discord.PartialEmoji.from_str(emoji),
        )
        button.callback = callback
        return button

    # -- callbacks (everything re-derived from the interaction) ------------ #
    async def on_close(self, interaction: discord.Interaction):
        resolved = await _resolve(interaction)
        if not resolved:
            return
        service, ticket, panel, category = resolved
        locale = i18n.get_user_locale(interaction)
        granted = member_permissions(interaction.user, category, ticket)
        if PERM_CLOSE not in granted:
            # The one refusal that is really a redirection: tell them about
            # the button that *is* theirs rather than just saying no.
            await send_error(
                interaction,
                t('modules.tickets.errors.use_close_request', locale=locale), locale)
            return
        await interaction.response.send_modal(
            TicketReasonModal(locale, 'close', self._do_close))

    async def _do_close(self, interaction: discord.Interaction, reason: Optional[str]):
        from services.ticket_service import TicketError
        resolved = await _resolve(interaction)
        if not resolved:
            return
        service, ticket, panel, category = resolved
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await service.close_ticket(interaction.channel, interaction.user, reason)
        except TicketError as e:
            await handle_ticket_error(interaction, e)
            return
        locale = i18n.get_user_locale(interaction)
        await send_success(interaction,
                           t('modules.tickets.close.done_title', locale=locale),
                           t('modules.tickets.close.done_description', locale=locale))

    async def on_close_request(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        await interaction.response.send_modal(
            TicketReasonModal(locale, 'close_request', self._do_close_request))

    async def _do_close_request(self, interaction: discord.Interaction,
                                reason: Optional[str]):
        from services.ticket_service import TicketError
        resolved = await _resolve(interaction)
        if not resolved:
            return
        service, ticket, panel, category = resolved
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await service.request_close(interaction.channel, interaction.user, reason)
        except TicketError as e:
            await handle_ticket_error(interaction, e)
            return
        locale = i18n.get_user_locale(interaction)
        await send_success(
            interaction,
            t('modules.tickets.close_request.sent_title', locale=locale),
            t('modules.tickets.close_request.sent_description', locale=locale))

    async def on_escalate(self, interaction: discord.Interaction):
        resolved = await _resolve(interaction)
        if not resolved:
            return
        service, ticket, panel, category = resolved
        await start_escalation(interaction, service, ticket, category)

    async def on_staff_thread(self, interaction: discord.Interaction):
        resolved = await _resolve(interaction)
        if not resolved:
            return
        service, ticket, panel, category = resolved
        await run_staff_thread(interaction, service)

    async def on_participants(self, interaction: discord.Interaction):
        resolved = await _resolve(interaction)
        if not resolved:
            return
        service, ticket, panel, category = resolved
        locale = i18n.get_user_locale(interaction)
        granted = member_permissions(interaction.user, category, ticket)
        if PERM_PARTICIPANTS not in granted:
            await send_error(
                interaction,
                t('modules.tickets.errors.missing_permission', locale=locale), locale)
            return
        await interaction.response.send_message(
            view=TicketParticipantsView(interaction.guild, ticket, locale),
            ephemeral=True)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: resolved from the ticket channel on every click."""
        bot.add_view(cls())


def build_ticket_message(ticket: Dict[str, Any], category: Dict[str, Any],
                         body: Optional[str], locale: str) -> TicketControlView:
    return TicketControlView(ticket, category, body, locale)


# =========================================================================== #
# 3. The closing card
# =========================================================================== #
class TicketClosedView(BaseView):
    """Posted when a ticket closes: reopen it, or delete the channel for good.

    Persistent: yes. Auth: resolved from the ticket channel (``close`` to
    reopen, ``admin`` to delete — deletion destroys the conversation, so it is
    never a permission a plain agent role gets by default).
    """

    __persistent__ = True

    def __init__(self, ticket: Optional[Dict[str, Any]] = None,
                 category: Optional[Dict[str, Any]] = None,
                 actor: Optional[discord.abc.User] = None,
                 reason: Optional[str] = None, body: Optional[str] = None,
                 locale: str = "en-US"):
        super().__init__()  # timeout=None
        self.ticket = ticket or {}
        self.category = category or {}
        self.actor = actor
        self.reason = reason
        self.body = body
        self.locale = locale
        self._build_view()

    def _build_view(self):
        self.clear_items()
        container = ui.Container(accent_colour=discord.Colour(0xED4245))

        container.add_item(ui.TextDisplay(
            f"### {TICKET_CLOSE} {t('modules.tickets.close.card_title', locale=self.locale)}"))
        lines = []
        if self.actor:
            lines.append(
                f"-# {t('modules.tickets.close.by', locale=self.locale)} "
                f"{self.actor.mention}")
        if self.reason:
            lines.append(
                f"**{t('modules.tickets.fields.reason', locale=self.locale)}**\n"
                f"{self.reason}")
        if lines:
            container.add_item(ui.TextDisplay("\n".join(lines)))
        if self.body:
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(ui.TextDisplay(self.body))

        row = ui.ActionRow()
        reopen = ui.Button(
            label=t('modules.tickets.actions.reopen', locale=self.locale)[:80],
            style=discord.ButtonStyle.success, custom_id=_CID_CLOSED_REOPEN,
            emoji=discord.PartialEmoji.from_str(TICKET_REOPEN),
        )
        reopen.callback = self.on_reopen
        row.add_item(reopen)

        delete = ui.Button(
            label=t('modules.tickets.actions.delete', locale=self.locale)[:80],
            style=discord.ButtonStyle.danger, custom_id=_CID_CLOSED_DELETE,
            emoji=discord.PartialEmoji.from_str(DELETE),
        )
        delete.callback = self.on_delete
        row.add_item(delete)
        container.add_item(row)

        self.add_item(container)

    async def on_reopen(self, interaction: discord.Interaction):
        from services.ticket_service import TicketError
        resolved = await _resolve(interaction)
        if not resolved:
            return
        service = resolved[0]
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await service.reopen_ticket(interaction.channel, interaction.user)
        except TicketError as e:
            await handle_ticket_error(interaction, e)
            return
        locale = i18n.get_user_locale(interaction)
        await send_success(interaction,
                           t('modules.tickets.reopen.done_title', locale=locale),
                           t('modules.tickets.reopen.done_description', locale=locale))

    async def on_delete(self, interaction: discord.Interaction):
        from services.ticket_service import TicketError
        resolved = await _resolve(interaction)
        if not resolved:
            return
        service, ticket, panel, category = resolved
        locale = i18n.get_user_locale(interaction)
        if PERM_ADMIN not in member_permissions(interaction.user, category, ticket):
            await send_error(
                interaction,
                t('modules.tickets.errors.missing_permission', locale=locale), locale)
            return
        await interaction.response.send_message(
            view=create_success_message(
                t('modules.tickets.delete.pending_title', locale=locale),
                t('modules.tickets.delete.pending_description', locale=locale)),
            ephemeral=True)
        try:
            await service.delete_ticket(interaction.channel, interaction.user)
        except TicketError as e:
            await interaction.followup.send(
                view=create_error_message(
                    t('modules.tickets.errors.title', locale=locale), e.message(locale)),
                ephemeral=True)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: resolved from the ticket channel on every click."""
        bot.add_view(cls())


def build_closed_message(ticket: Dict[str, Any], category: Dict[str, Any],
                         actor: discord.abc.User, reason: Optional[str],
                         body: Optional[str], locale: str) -> TicketClosedView:
    return TicketClosedView(ticket, category, actor, reason, body, locale)


# =========================================================================== #
# 4. The close request
# =========================================================================== #
class TicketCloseRequestView(BaseView):
    """A member asked for the ticket to be closed; staff accepts or refuses.

    Persistent: yes. Auth: resolved from the ticket channel — accepting needs
    ``close``, refusing needs ``close`` or being the requester.
    """

    __persistent__ = True

    def __init__(self, ticket: Optional[Dict[str, Any]] = None,
                 requester: Optional[discord.abc.User] = None,
                 reason: Optional[str] = None, locale: str = "en-US"):
        super().__init__()  # timeout=None
        self.ticket = ticket or {}
        self.requester = requester
        self.reason = reason
        self.locale = locale
        self._build_view()

    def _build_view(self):
        self.clear_items()
        container = ui.Container(accent_colour=discord.Colour(0xFEE75C))

        container.add_item(ui.TextDisplay(
            f"### {TICKET_CLOSE_REQUEST} "
            f"{t('modules.tickets.close_request.card_title', locale=self.locale)}"))
        body = t('modules.tickets.close_request.card_description', locale=self.locale,
                 user=self.requester.mention if self.requester else "—")
        if self.reason:
            body += (f"\n\n**{t('modules.tickets.fields.reason', locale=self.locale)}**\n"
                     f"{self.reason}")
        container.add_item(ui.TextDisplay(body))

        row = ui.ActionRow()
        accept = ui.Button(
            label=t('modules.tickets.close_request.accept', locale=self.locale)[:80],
            style=discord.ButtonStyle.danger, custom_id=_CID_REQUEST_ACCEPT,
            emoji=discord.PartialEmoji.from_str(TICKET_CLOSE),
        )
        accept.callback = self.on_accept
        row.add_item(accept)

        refuse = ui.Button(
            label=t('modules.tickets.close_request.refuse', locale=self.locale)[:80],
            style=discord.ButtonStyle.secondary, custom_id=_CID_REQUEST_REFUSE,
            emoji=discord.PartialEmoji.from_str(UNDONE),
        )
        refuse.callback = self.on_refuse
        row.add_item(refuse)
        container.add_item(row)

        self.add_item(container)

    async def on_accept(self, interaction: discord.Interaction):
        from services.ticket_service import TicketError
        resolved = await _resolve(interaction)
        if not resolved:
            return
        service = resolved[0]
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await service.close_ticket(interaction.channel, interaction.user, None)
        except TicketError as e:
            await handle_ticket_error(interaction, e)
            return
        locale = i18n.get_user_locale(interaction)
        await send_success(interaction,
                           t('modules.tickets.close.done_title', locale=locale),
                           t('modules.tickets.close.done_description', locale=locale))

    async def on_refuse(self, interaction: discord.Interaction):
        from services.ticket_service import TicketError
        resolved = await _resolve(interaction)
        if not resolved:
            return
        service = resolved[0]
        try:
            await service.cancel_close_request(interaction.channel, interaction.user)
        except TicketError as e:
            await handle_ticket_error(interaction, e)
            return
        locale = i18n.get_user_locale(interaction)
        await interaction.response.edit_message(
            view=_close_request_resolved(interaction.user, locale))

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: resolved from the ticket channel on every click."""
        bot.add_view(cls())


def _close_request_resolved(actor: discord.abc.User, locale: str) -> ui.LayoutView:
    """The close-request card once it has been refused (no buttons left)."""
    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=discord.Colour(0x99AAB5))
    container.add_item(ui.TextDisplay(
        f"### {TICKET_CLOSE_REQUEST} "
        f"{t('modules.tickets.close_request.card_title', locale=locale)}"))
    container.add_item(ui.TextDisplay(
        f"{INFO} {t('modules.tickets.close_request.refused', locale=locale, user=actor.mention)}"))
    view.add_item(container)
    return view


def build_close_request_message(ticket: Dict[str, Any], requester: discord.abc.User,
                                reason: Optional[str],
                                locale: str) -> TicketCloseRequestView:
    return TicketCloseRequestView(ticket, requester, reason, locale)


# =========================================================================== #
# 5. The escalation notice
# =========================================================================== #
class TicketEscalationView(BaseView):
    """Posted when a ticket is escalated. Persistent: yes.

    Auth: resolved from the ticket channel — cancelling needs ``admin``, the
    same permission escalating needed.
    """

    __persistent__ = True

    def __init__(self, ticket: Optional[Dict[str, Any]] = None,
                 actor: Optional[discord.abc.User] = None,
                 reason: Optional[str] = None, locale: str = "en-US"):
        super().__init__()  # timeout=None
        self.ticket = ticket or {}
        self.actor = actor
        self.reason = reason
        self.locale = locale
        self._build_view()

    def _build_view(self):
        self.clear_items()
        container = ui.Container(accent_colour=discord.Colour(0x9B59B6))

        container.add_item(ui.TextDisplay(
            f"### {TICKET_ESCALATE} "
            f"{t('modules.tickets.escalate.card_title', locale=self.locale)}"))
        body = t('modules.tickets.escalate.card_description', locale=self.locale,
                 user=self.actor.mention if self.actor else "—")
        if self.reason:
            body += (f"\n\n**{t('modules.tickets.fields.reason', locale=self.locale)}**\n"
                     f"{self.reason}")
        container.add_item(ui.TextDisplay(body))

        row = ui.ActionRow()
        cancel = ui.Button(
            label=t('modules.tickets.actions.deescalate', locale=self.locale)[:80],
            style=discord.ButtonStyle.secondary, custom_id=_CID_ESCALATED_CANCEL,
            emoji=discord.PartialEmoji.from_str(BACK),
        )
        cancel.callback = self.on_cancel
        row.add_item(cancel)
        container.add_item(row)

        self.add_item(container)

    async def on_cancel(self, interaction: discord.Interaction):
        from services.ticket_service import TicketError
        resolved = await _resolve(interaction)
        if not resolved:
            return
        service = resolved[0]
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await service.deescalate(interaction.channel, interaction.user)
        except TicketError as e:
            await handle_ticket_error(interaction, e)
            return
        locale = i18n.get_user_locale(interaction)
        await send_success(
            interaction,
            t('modules.tickets.escalate.cancelled_title', locale=locale),
            t('modules.tickets.escalate.cancelled_description', locale=locale))

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: resolved from the ticket channel on every click."""
        bot.add_view(cls())


def build_escalation_notice(ticket: Dict[str, Any], actor: discord.abc.User,
                            reason: Optional[str], locale: str) -> TicketEscalationView:
    return TicketEscalationView(ticket, actor, reason, locale)


# =========================================================================== #
# 6. Escalation confirmation (ephemeral)
# =========================================================================== #
async def start_escalation(interaction: discord.Interaction, service,
                           ticket: Dict[str, Any], category: Dict[str, Any],
                           reason: Optional[str] = None) -> None:
    """Escalate, asking first what to do with the manually added participants.

    Skipping the question when there is nobody to ask about is the whole point
    of asking it: a confirmation that always appears stops being read.
    """
    from services.ticket_service import TicketError

    locale = i18n.get_user_locale(interaction)
    if PERM_ADMIN not in member_permissions(interaction.user, category, ticket):
        await send_error(interaction,
                         t('modules.tickets.errors.missing_permission', locale=locale),
                         locale)
        return

    manual = list(ticket.get('participants', [])) + list(ticket.get('participant_roles', []))
    if manual:
        await interaction.response.send_message(
            view=TicketEscalateConfirmView(ticket, reason, locale), ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        await service.escalate(interaction.channel, interaction.user, reason=reason)
    except TicketError as e:
        await handle_ticket_error(interaction, e)
        return
    await send_success(interaction,
                       t('modules.tickets.escalate.done_title', locale=locale),
                       t('modules.tickets.escalate.done_description', locale=locale))


class TicketEscalateConfirmView(BaseView):
    """Keep or drop the manually added participants when escalating.

    Persistent: yes (ephemeral messages survive a restart too, and a dead
    button on one is just as broken). Auth: resolved from the ticket channel.

    The escalation reason typed in the slash command is not carried across a
    restart — the shell escalates without one rather than refusing, which is
    the harmless half of the trade.
    """

    __persistent__ = True

    def __init__(self, ticket: Optional[Dict[str, Any]] = None,
                 reason: Optional[str] = None, locale: str = "en-US"):
        super().__init__()  # timeout=None
        self.ticket = ticket or {}
        self.reason = reason
        self.locale = locale
        self._build_view()

    def _build_view(self):
        self.clear_items()
        container = ui.Container(accent_colour=discord.Colour(0x9B59B6))

        container.add_item(ui.TextDisplay(
            f"### {TICKET_ESCALATE} "
            f"{t('modules.tickets.escalate.confirm_title', locale=self.locale)}"))
        container.add_item(ui.TextDisplay(
            t('modules.tickets.escalate.confirm_description', locale=self.locale)))

        listed = [f"<@{uid}>" for uid in self.ticket.get('participants', [])]
        listed += [f"<@&{rid}>" for rid in self.ticket.get('participant_roles', [])]
        if listed:
            container.add_item(ui.TextDisplay(
                f"-# {t('modules.tickets.fields.participants', locale=self.locale)} : "
                f"{', '.join(listed)}"))

        row = ui.ActionRow()
        keep = ui.Button(
            label=t('modules.tickets.escalate.keep', locale=self.locale)[:80],
            style=discord.ButtonStyle.primary, custom_id=_CID_ESC_KEEP,
            emoji=discord.PartialEmoji.from_str(GROUPS),
        )
        keep.callback = self.on_keep
        row.add_item(keep)

        drop = ui.Button(
            label=t('modules.tickets.escalate.drop', locale=self.locale)[:80],
            style=discord.ButtonStyle.danger, custom_id=_CID_ESC_DROP,
            emoji=discord.PartialEmoji.from_str(DELETE),
        )
        drop.callback = self.on_drop
        row.add_item(drop)
        container.add_item(row)

        self.add_item(container)

    async def on_keep(self, interaction: discord.Interaction):
        await self._escalate(interaction, keep=True)

    async def on_drop(self, interaction: discord.Interaction):
        await self._escalate(interaction, keep=False)

    async def _escalate(self, interaction: discord.Interaction, *, keep: bool):
        from services.ticket_service import TicketError
        resolved = await _resolve(interaction)
        if not resolved:
            return
        service = resolved[0]
        locale = i18n.get_user_locale(interaction)
        await interaction.response.defer()
        try:
            await service.escalate(interaction.channel, interaction.user,
                                   reason=self.reason, keep_participants=keep)
        except TicketError as e:
            await handle_ticket_error(interaction, e)
            return
        await interaction.edit_original_response(view=create_success_message(
            t('modules.tickets.escalate.done_title', locale=locale),
            t('modules.tickets.escalate.done_description', locale=locale)))

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: resolved from the ticket channel on every click."""
        bot.add_view(cls())


# =========================================================================== #
# 7. Participants (ephemeral)
# =========================================================================== #
class TicketParticipantsView(BaseView):
    """Who is in this ticket, on top of its opener and the staff roles.

    Persistent: yes. Auth: resolved from the ticket channel + the
    ``participants`` permission, re-checked on every change.

    Both selects **replace** the list rather than adding to it: a picker that
    already shows the current members as selected reads as "this is who is in",
    and unselecting is then the obvious way to remove someone. An add-only
    picker would need a second, mirror-image remove control for no gain.
    """

    __persistent__ = True

    def __init__(self, guild: Optional[discord.Guild] = None,
                 ticket: Optional[Dict[str, Any]] = None, locale: str = "en-US"):
        super().__init__()  # timeout=None
        self.guild = guild
        self.ticket = ticket or {}
        self.locale = locale
        self._build_view()

    def _build_view(self):
        self.clear_items()
        container = ui.Container(accent_colour=discord.Colour(DEFAULT_ACCENT_COLOR))

        container.add_item(ui.TextDisplay(
            f"### {TICKET_PARTICIPANTS} "
            f"{t('modules.tickets.participants.title', locale=self.locale)}"))
        container.add_item(ui.TextDisplay(
            t('modules.tickets.participants.description', locale=self.locale)))

        if self.ticket.get('owner_id'):
            container.add_item(ui.TextDisplay(
                f"-# {t('modules.tickets.participants.owner_note', locale=self.locale)} "
                f"<@{self.ticket['owner_id']}>"))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        container.add_item(ui.TextDisplay(
            f"**{t('modules.tickets.participants.members_title', locale=self.locale)}**\n"
            f"-# {t('modules.tickets.participants.members_description', locale=self.locale)}"))
        user_row = ui.ActionRow()
        user_select = ui.UserSelect(
            placeholder=t('modules.tickets.participants.members_placeholder',
                          locale=self.locale),
            min_values=0, max_values=25, custom_id=_CID_MEMBERS_USERS,
        )
        user_select.default_values = [
            discord.Object(id=uid) for uid in self.ticket.get('participants', [])
        ]
        user_select.callback = self.on_users
        user_row.add_item(user_select)
        container.add_item(user_row)

        container.add_item(ui.TextDisplay(
            f"**{t('modules.tickets.participants.roles_title', locale=self.locale)}**\n"
            f"-# {t('modules.tickets.participants.roles_description', locale=self.locale)}"))
        role_row = ui.ActionRow()
        role_select = ui.RoleSelect(
            placeholder=t('modules.tickets.participants.roles_placeholder',
                          locale=self.locale),
            min_values=0, max_values=10, custom_id=_CID_MEMBERS_ROLES,
        )
        role_select.default_values = [
            discord.Object(id=rid) for rid in self.ticket.get('participant_roles', [])
        ]
        role_select.callback = self.on_roles
        role_row.add_item(role_select)
        container.add_item(role_row)

        self.add_item(container)

    async def on_users(self, interaction: discord.Interaction):
        await self._apply(interaction, users=[
            int(v) for v in (interaction.data.get('values') or [])
        ])

    async def on_roles(self, interaction: discord.Interaction):
        await self._apply(interaction, roles=[
            int(v) for v in (interaction.data.get('values') or [])
        ])

    async def _apply(self, interaction: discord.Interaction, **selection):
        resolved = await _resolve(interaction)
        if not resolved:
            return
        service, ticket, panel, category = resolved
        locale = i18n.get_user_locale(interaction)

        if PERM_PARTICIPANTS not in member_permissions(interaction.user, category, ticket):
            await send_error(
                interaction,
                t('modules.tickets.errors.missing_permission', locale=locale), locale)
            return

        users = selection.get('users')
        if users is not None:
            # The opener is in the ticket by definition; keeping them out of
            # the manual list stops "remove the owner" from being one click.
            users = [uid for uid in users if uid != ticket['owner_id']]

        await interaction.client.db.set_participants(
            interaction.channel.id, users=users, roles=selection.get('roles'))
        ticket = await service.get_ticket(interaction.channel.id) or ticket
        await service.sync_permissions(interaction.channel, category, ticket)

        await interaction.response.edit_message(
            view=TicketParticipantsView(interaction.guild, ticket, locale))

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: resolved from the ticket channel on every click."""
        bot.add_view(cls())


# =========================================================================== #
# 8. Modals
# =========================================================================== #
class TicketReasonModal(BaseModal):
    """One optional free-text reason, reused by close / close request / escalate."""

    def __init__(self, locale: str, action: str, callback_func):
        super().__init__(
            title=t(f'modules.tickets.actions.{action}', locale=locale)[:45],
            timeout=None,
        )
        self.callback_func = callback_func
        self.reason_input = ui.TextInput(
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=MAX_TICKET_MESSAGE,
        )
        self.add_item(ui.Label(
            text=t('modules.tickets.fields.reason', locale=locale)[:45],
            description=t('modules.tickets.fields.reason_hint', locale=locale)[:100],
            component=self.reason_input,
        ))

    async def on_submit(self, interaction: discord.Interaction):
        value = (self.reason_input.value or "").strip()
        await self.callback_func(interaction, value or None)


class TicketRenameModal(BaseModal):
    """Rename the ticket channel."""

    def __init__(self, locale: str, current: str, callback_func):
        super().__init__(
            title=t('modules.tickets.actions.rename', locale=locale)[:45],
            timeout=None,
        )
        self.callback_func = callback_func
        self.name_input = ui.TextInput(
            style=discord.TextStyle.short,
            default=current,
            max_length=90,
            required=True,
        )
        self.add_item(ui.Label(
            text=t('modules.tickets.fields.channel_name', locale=locale)[:45],
            description=t('modules.tickets.fields.channel_name_hint', locale=locale)[:100],
            component=self.name_input,
        ))

    async def on_submit(self, interaction: discord.Interaction):
        await self.callback_func(interaction, (self.name_input.value or "").strip())


# =========================================================================== #
# 9. Shared action bodies (used by the buttons AND the slash commands)
# =========================================================================== #
async def run_staff_thread(interaction: discord.Interaction, service) -> None:
    """Open/join the staff thread and answer with a link to it."""
    from services.ticket_service import TicketError

    locale = i18n.get_user_locale(interaction)
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        thread = await service.open_staff_thread(interaction.channel, interaction.user)
    except TicketError as e:
        await handle_ticket_error(interaction, e)
        return
    await send_success(
        interaction,
        t('modules.tickets.staff_thread.done_title', locale=locale),
        t('modules.tickets.staff_thread.done_description', locale=locale,
          thread=thread.mention))


# =========================================================================== #
# 10. The closing DM (no interactive child)
# =========================================================================== #
def build_close_dm(guild: discord.Guild, ticket: Dict[str, Any],
                   category: Dict[str, Any], actor: discord.abc.User,
                   reason: Optional[str], locale: str) -> ui.LayoutView:
    """Tell the opener their ticket is closed. Zero interactive children."""
    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=discord.Colour(0xED4245))

    container.add_item(ui.TextDisplay(
        f"### {TICKET_CLOSE} {t('modules.tickets.close.dm_title', locale=locale)}"))
    container.add_item(ui.TextDisplay(
        t('modules.tickets.close.dm_description', locale=locale,
          server=guild.name, number=ticket.get('number', '—'),
          category=category.get('name', '—'))))
    if reason:
        container.add_item(ui.TextDisplay(
            f"**{t('modules.tickets.fields.reason', locale=locale)}**\n{reason}"))

    view.add_item(container)
    return view


# =========================================================================== #
# 11. Persistence marker
# =========================================================================== #
class TicketsPersistence(BaseView):
    """Marker view: registers the ticket panel's dynamic items at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(TicketOpenButton, TicketOpenSelect)
