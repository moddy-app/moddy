"""
Every Discord surface of the Tickets module.

Four kinds of message live here, and they have deliberately different
persistence models:

- **The panel** (posted in a public channel). Its buttons/options carry the
  panel and category ids, so they are :class:`discord.ui.DynamicItem`\\ s
  reconstructed from their ``custom_id`` on every click.
- **The ticket control bar** (pinned in the ticket channel), **the closing
  card**, **the close request**, **the escalation notice** and **the claim
  notice**. A ticket action always happens *inside* its own channel, so
  ``interaction.channel_id`` is the ticket's identity: these need no id in
  their custom_ids at all and are plain registered persistent views with
  static ones.
- **The escalation confirmation** (ephemeral) — same reasoning, same static ids.
- **The DMs** (closed, reopened), which have no interactive child at all.

Authorization is never carried by a view. Every callback resolves the ticket
from the channel, the category from the guild's config, and the actor's
permissions from their roles — see ``services/ticket_service.py``. A stale
button clicked a month after a restart is therefore exactly as safe as a fresh
one.

**Buttons live outside the container, mentions live in their own message.**
Two rules that shape every card below:

- A Components V2 ``Container`` is the card; the actions belong under it, not
  inside its frame. Every view here adds its container first and its
  ``ActionRow``\\ s to the view itself afterwards.
- Discord rejects a message carrying both the ``IS_COMPONENTS_V2`` flag and a
  ``content`` field, and discord.py sets that flag for any ``LayoutView`` — so
  a ping can never ride along with a card anyway. It goes out as its own
  message, which the service deletes immediately
  (``TicketService.ping``): it rings once and leaves nothing behind.

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
    BTN_CLAIM,
    BTN_CLOSE,
    BTN_CLOSE_REQUEST,
    BTN_ESCALATE,
    BTN_PARTICIPANTS,
    BTN_STAFF_THREAD,
    BUTTON_STYLES,
    DEFAULT_ACCENT_COLOR,
    DEFAULT_TICKET_BUTTONS,
    MAX_TICKET_MESSAGE,
    PERM_ADMIN,
    PERM_CLOSE,
    PERM_PARTICIPANTS,
    STYLE_SELECT,
    TICKET_BUTTONS,
    find_category,
    find_panel,
    member_permissions,
    parse_emoji,
    split_message_blocks,
)
from utils.components_v2 import create_error_message, create_success_message
from utils.emojis import (
    BACK, DELETE, GROUPS, INFO, MIC_OFF, TICKET, TICKET_CLAIM, TICKET_CLOSE,
    TICKET_CLOSE_REQUEST, TICKET_ESCALATE, TICKET_PARTICIPANTS, TICKET_REOPEN,
    TICKET_STAFF_THREAD, UNDONE,
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
_CID_CTRL = {
    BTN_CLOSE: "moddy:tickets:ctrl:close",
    BTN_CLAIM: "moddy:tickets:ctrl:claim",
    BTN_ESCALATE: "moddy:tickets:ctrl:escalate",
    BTN_STAFF_THREAD: "moddy:tickets:ctrl:staff_thread",
    BTN_PARTICIPANTS: "moddy:tickets:ctrl:participants",
    BTN_CLOSE_REQUEST: "moddy:tickets:ctrl:close_request",
}

_CID_CLOSED_REOPEN = "moddy:tickets:closed:reopen"
_CID_CLOSED_DELETE = "moddy:tickets:closed:delete"

_CID_REQUEST_ACCEPT = "moddy:tickets:request:accept"
_CID_REQUEST_REFUSE = "moddy:tickets:request:refuse"

_CID_ESCALATED_CANCEL = "moddy:tickets:escalated:cancel"

_CID_ESC_KEEP = "moddy:tickets:escalate:keep"
_CID_ESC_MUTE = "moddy:tickets:escalate:mute"
_CID_ESC_DROP = "moddy:tickets:escalate:drop"

# Panel ids are `p_xxxxxx` / `c_xxxxxx` (see modules/tickets.py).
_ENTRY_ID = r"[a-z0-9_]+"

# One row per five buttons — Discord's ActionRow limit.
_BUTTONS_PER_ROW = 5


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


def add_message_body(container: ui.Container, body: Optional[str]) -> None:
    """Render an admin-written ticket message into a container.

    A line holding nothing but ``---`` becomes a real Components V2 separator:
    it is the only piece of layout an admin can ask for from a text box, and
    markdown's own horizontal rule does not exist inside a container.
    """
    for index, block in enumerate(split_message_blocks(body)):
        if index:
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(block))


def _add_rows(view: ui.LayoutView, buttons: List[ui.Button]) -> None:
    """Attach buttons to the **view**, five per row — never to the container.

    Buttons inside a container are drawn in its frame, which reads as part of
    the card; the actions on a ticket belong under it.
    """
    for start in range(0, len(buttons), _BUTTONS_PER_ROW):
        row = ui.ActionRow()
        for button in buttons[start:start + _BUTTONS_PER_ROW]:
            row.add_item(button)
        view.add_item(row)


def _button(custom_id: str, label: str, emoji: str,
            style: discord.ButtonStyle, callback) -> ui.Button:
    button = ui.Button(label=label[:80], style=style, custom_id=custom_id,
                       emoji=discord.PartialEmoji.from_str(emoji))
    button.callback = callback
    return button


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
            self.add_item(row)
            return

        # Buttons, five per row (Discord's limit).
        for start in range(0, len(categories), 5):
            row = ui.ActionRow()
            for category in categories[start:start + 5]:
                row.add_item(TicketOpenButton(panel['id'], category['id'], category))
            self.add_item(row)


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
    """The opening message of a ticket, and the actions under it.

    Its **whole** text is the category's opening message, rendered as written
    (``---`` on its own line becomes a separator): the module adds no title and
    no footer of its own, so an admin controls every word their members read.

    Which buttons appear is the category's decision too (``buttons``). The
    persistent shell deliberately carries *all* of them so that whatever a
    guild configured keeps working after a restart — a registered view matches
    on custom_id, and an id the shell never declared would be dead.

    Persistent: yes. Auth: derived from the channel — the ticket is resolved
    from ``interaction.channel_id`` and the actor's permissions from their
    roles in the ticket's category, on every single click. The buttons are
    shown to everyone who can see the ticket; each one refuses politely when
    the clicker may not use it.
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

    # -- layout ------------------------------------------------------------ #
    def _enabled_buttons(self) -> List[str]:
        """Which actions this message offers, in their fixed order."""
        if not self.category:
            # The persistent shell: declare every id so no configuration ends
            # up with buttons nothing answers.
            return list(TICKET_BUTTONS)

        configured = self.category.get('buttons')
        if configured is None:
            configured = list(DEFAULT_TICKET_BUTTONS)
        keep = [b for b in TICKET_BUTTONS if b in configured]
        if not self.category.get('claim_enabled', True):
            keep = [b for b in keep if b != BTN_CLAIM]
        return keep

    def _build_view(self):
        self.clear_items()
        container = ui.Container(accent_colour=discord.Colour(DEFAULT_ACCENT_COLOR))

        if self.body:
            add_message_body(container, self.body)
        else:
            # Only reachable on the persistent shell, which is never sent.
            container.add_item(ui.TextDisplay(
                f"### {TICKET} "
                f"{t('modules.tickets.channel.title', locale=self.locale, number='—')}"))

        self.add_item(container)

        specs = {
            BTN_CLOSE: (t('modules.tickets.actions.close', locale=self.locale),
                        TICKET_CLOSE, discord.ButtonStyle.danger, self.on_close),
            BTN_CLAIM: (t('modules.tickets.actions.claim', locale=self.locale),
                        TICKET_CLAIM, discord.ButtonStyle.primary, self.on_claim),
            BTN_ESCALATE: (t('modules.tickets.actions.escalate', locale=self.locale),
                           TICKET_ESCALATE, discord.ButtonStyle.secondary,
                           self.on_escalate),
            BTN_STAFF_THREAD: (
                t('modules.tickets.actions.staff_thread', locale=self.locale),
                TICKET_STAFF_THREAD, discord.ButtonStyle.secondary,
                self.on_staff_thread),
            BTN_PARTICIPANTS: (
                t('modules.tickets.actions.participants', locale=self.locale),
                TICKET_PARTICIPANTS, discord.ButtonStyle.secondary,
                self.on_participants),
            BTN_CLOSE_REQUEST: (
                t('modules.tickets.actions.close_request', locale=self.locale),
                TICKET_CLOSE_REQUEST, discord.ButtonStyle.secondary,
                self.on_close_request),
        }
        _add_rows(self, [
            _button(_CID_CTRL[key], *specs[key]) for key in self._enabled_buttons()
        ])

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
            # the command that *is* theirs rather than just saying no.
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

    async def on_claim(self, interaction: discord.Interaction):
        resolved = await _resolve(interaction)
        if not resolved:
            return
        await run_claim(interaction, resolved[0])

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
        await open_participants_modal(interaction, ticket, category)

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
            add_message_body(container, self.body)

        self.add_item(container)

        _add_rows(self, [
            _button(_CID_CLOSED_REOPEN,
                    t('modules.tickets.actions.reopen', locale=self.locale),
                    TICKET_REOPEN, discord.ButtonStyle.success, self.on_reopen),
            _button(_CID_CLOSED_DELETE,
                    t('modules.tickets.actions.delete', locale=self.locale),
                    DELETE, discord.ButtonStyle.danger, self.on_delete),
        ])

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

        self.add_item(container)

        _add_rows(self, [
            _button(_CID_REQUEST_ACCEPT,
                    t('modules.tickets.close_request.accept', locale=self.locale),
                    TICKET_CLOSE, discord.ButtonStyle.danger, self.on_accept),
            _button(_CID_REQUEST_REFUSE,
                    t('modules.tickets.close_request.refuse', locale=self.locale),
                    UNDONE, discord.ButtonStyle.secondary, self.on_refuse),
        ])

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
                                reason: Optional[str], locale: str
                                ) -> TicketCloseRequestView:
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
        if self.ticket.get('escalation_mute'):
            body += ("\n" + t('modules.tickets.escalate.muted_notice',
                              locale=self.locale))
        if self.reason:
            body += (f"\n\n**{t('modules.tickets.fields.reason', locale=self.locale)}**\n"
                     f"{self.reason}")
        container.add_item(ui.TextDisplay(body))

        self.add_item(container)

        _add_rows(self, [
            _button(_CID_ESCALATED_CANCEL,
                    t('modules.tickets.actions.deescalate', locale=self.locale),
                    BACK, discord.ButtonStyle.secondary, self.on_cancel),
        ])

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
                            reason: Optional[str], locale: str
                            ) -> TicketEscalationView:
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
    """What happens to the manually added participants when escalating.

    Three answers, because two were not enough: keeping someone in the room and
    keeping them *talking* are different decisions, and an escalation usually
    wants the first without the second.

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

        self.add_item(container)

        _add_rows(self, [
            _button(_CID_ESC_KEEP,
                    t('modules.tickets.escalate.keep', locale=self.locale),
                    GROUPS, discord.ButtonStyle.primary, self.on_keep),
            _button(_CID_ESC_MUTE,
                    t('modules.tickets.escalate.mute', locale=self.locale),
                    MIC_OFF, discord.ButtonStyle.secondary, self.on_mute),
            _button(_CID_ESC_DROP,
                    t('modules.tickets.escalate.drop', locale=self.locale),
                    DELETE, discord.ButtonStyle.danger, self.on_drop),
        ])

    async def on_keep(self, interaction: discord.Interaction):
        await self._escalate(interaction, keep=True, mute=False)

    async def on_mute(self, interaction: discord.Interaction):
        await self._escalate(interaction, keep=True, mute=True)

    async def on_drop(self, interaction: discord.Interaction):
        await self._escalate(interaction, keep=False, mute=False)

    async def _escalate(self, interaction: discord.Interaction, *,
                        keep: bool, mute: bool):
        from services.ticket_service import TicketError
        resolved = await _resolve(interaction)
        if not resolved:
            return
        service = resolved[0]
        locale = i18n.get_user_locale(interaction)
        await interaction.response.defer()
        try:
            await service.escalate(interaction.channel, interaction.user,
                                   reason=self.reason, keep_participants=keep,
                                   mute_participants=mute)
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
# 7. The claim notice (no interactive child)
# =========================================================================== #
def build_claim_notice(actor: discord.abc.User, *,
                       claimed: bool, locale: str) -> ui.LayoutView:
    """Say in the channel who took the ticket, or that it is free again.

    Posted by the service (``TicketService._announce_claim``) so the button and
    ``/ticket claim`` cannot end up announcing different things.
    """
    view = ui.LayoutView(timeout=None)
    container = ui.Container(
        accent_colour=discord.Colour(0x57F287 if claimed else 0x99AAB5))
    container.add_item(ui.TextDisplay(
        f"### {TICKET_CLAIM} "
        f"{t('modules.tickets.claim.claimed_title' if claimed else 'modules.tickets.claim.released_title', locale=locale)}"))
    container.add_item(ui.TextDisplay(t(
        'modules.tickets.claim.claimed_description' if claimed
        else 'modules.tickets.claim.released_description',
        locale=locale, user=actor.mention)))
    view.add_item(container)
    return view


# =========================================================================== #
# 8. Participants (Modal V2)
# =========================================================================== #
class TicketParticipantsModal(BaseModal):
    """Who is in this ticket, on top of its opener and the staff roles.

    A modal rather than a panel of selects: both pickers open **pre-filled with
    the current participants**, and one submit applies the whole picture at
    once. That is what makes unselecting the obvious way to remove somebody —
    the form shows who is in, not a queue of additions.

    Modals are the one surface that is deliberately not persistent (see
    docs/PERSISTENT_VIEWS.md "Deliberate exclusions"): they are answered in the
    moment, and Discord closes them on a restart anyway.
    """

    def __init__(self, locale: str, ticket: Dict[str, Any], callback_func):
        super().__init__(
            title=t('modules.tickets.participants.title', locale=locale)[:45],
            timeout=None,
        )
        self.callback_func = callback_func

        self.add_item(ui.TextDisplay(
            t('modules.tickets.participants.description', locale=locale)))

        self.user_select = ui.UserSelect(
            placeholder=t('modules.tickets.participants.members_placeholder',
                          locale=locale)[:150],
            min_values=0, max_values=25, required=False,
        )
        self.user_select.default_values = [
            discord.Object(id=uid) for uid in ticket.get('participants', [])
        ]
        self.add_item(ui.Label(
            text=t('modules.tickets.participants.members_title', locale=locale)[:45],
            description=t('modules.tickets.participants.members_description',
                          locale=locale)[:100],
            component=self.user_select,
        ))

        self.role_select = ui.RoleSelect(
            placeholder=t('modules.tickets.participants.roles_placeholder',
                          locale=locale)[:150],
            min_values=0, max_values=10, required=False,
        )
        self.role_select.default_values = [
            discord.Object(id=rid) for rid in ticket.get('participant_roles', [])
        ]
        self.add_item(ui.Label(
            text=t('modules.tickets.participants.roles_title', locale=locale)[:45],
            description=t('modules.tickets.participants.roles_description',
                          locale=locale)[:100],
            component=self.role_select,
        ))

    async def on_submit(self, interaction: discord.Interaction):
        await self.callback_func(
            interaction,
            [target.id for target in self.user_select.values],
            [role.id for role in self.role_select.values],
        )


async def open_participants_modal(interaction: discord.Interaction,
                                  ticket: Dict[str, Any],
                                  category: Dict[str, Any]) -> None:
    """Check the permission, then hand over the pre-filled participants form."""
    locale = i18n.get_user_locale(interaction)
    if PERM_PARTICIPANTS not in member_permissions(interaction.user, category, ticket):
        await send_error(interaction,
                         t('modules.tickets.errors.missing_permission', locale=locale),
                         locale)
        return

    modal = TicketParticipantsModal(locale, ticket, _apply_participants)
    modal.bot = interaction.client
    await interaction.response.send_modal(modal)


async def _apply_participants(interaction: discord.Interaction,
                              users: List[int], roles: List[int]) -> None:
    """Replace the ticket's manual participants with what the form returned."""
    resolved = await _resolve(interaction)
    if not resolved:
        return
    service, ticket, panel, category = resolved
    locale = i18n.get_user_locale(interaction)

    # Re-checked here and not only before the modal: a role can be taken away
    # while a form sits open.
    if PERM_PARTICIPANTS not in member_permissions(interaction.user, category, ticket):
        await send_error(interaction,
                         t('modules.tickets.errors.missing_permission', locale=locale),
                         locale)
        return

    # The opener is in the ticket by definition; keeping them out of the manual
    # list stops "remove the owner" from being one click.
    users = [uid for uid in users if uid != ticket['owner_id']]

    await interaction.response.defer(ephemeral=True, thinking=True)
    await interaction.client.db.set_participants(
        interaction.channel.id, users=users, roles=roles)
    ticket = await service.get_ticket(interaction.channel.id) or ticket
    await service.sync_permissions(interaction.channel, category, ticket)

    listed = [f"<@{uid}>" for uid in users] + [f"<@&{rid}>" for rid in roles]
    await send_success(
        interaction,
        t('modules.tickets.participants.saved_title', locale=locale),
        t('modules.tickets.participants.saved_description', locale=locale,
          participants=", ".join(listed)
          or t('modules.tickets.fields.none', locale=locale))[:2000])


# =========================================================================== #
# 9. Modals
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
# 10. Shared action bodies (used by the buttons AND the slash commands)
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


async def run_claim(interaction: discord.Interaction, service, *,
                    force: Optional[bool] = None) -> None:
    """Claim, release, or toggle. The notice in the channel is the service's job.

    ``force=None`` is the button: one control that takes an unheld ticket,
    releases the one you hold, and refuses a colleague's unless you may release
    it. ``force=True/False`` is ``/ticket claim`` and ``/ticket unclaim``,
    where the staffer said which one they meant.
    """
    from services.ticket_service import TicketError

    locale = i18n.get_user_locale(interaction)
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        if force is None:
            _, claimed = await service.toggle_claim(interaction.channel,
                                                    interaction.user)
        elif force:
            await service.claim_ticket(interaction.channel, interaction.user)
            claimed = True
        else:
            await service.unclaim_ticket(interaction.channel, interaction.user)
            claimed = False
    except TicketError as e:
        await handle_ticket_error(interaction, e)
        return

    key = 'claimed' if claimed else 'released'
    await send_success(interaction,
                       t(f'modules.tickets.claim.{key}_title', locale=locale),
                       t(f'modules.tickets.claim.{key}_done', locale=locale))


# =========================================================================== #
# 11. The DMs (no interactive child)
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


def build_reopen_dm(guild: discord.Guild, ticket: Dict[str, Any],
                    category: Dict[str, Any], actor: discord.abc.User,
                    channel: Optional[discord.abc.GuildChannel],
                    locale: str) -> ui.LayoutView:
    """Tell the opener their ticket is open again, with the way back to it.

    The closure was announced in a DM; its cancellation has to be too, or the
    member never learns that a channel which vanished from their list is back.
    """
    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=discord.Colour(0x57F287))

    container.add_item(ui.TextDisplay(
        f"### {TICKET_REOPEN} {t('modules.tickets.reopen.dm_title', locale=locale)}"))
    container.add_item(ui.TextDisplay(
        t('modules.tickets.reopen.dm_description', locale=locale,
          server=guild.name, number=ticket.get('number', '—'),
          category=category.get('name', '—'))))
    if channel is not None:
        container.add_item(ui.TextDisplay(
            f"-# {t('modules.tickets.reopen.dm_channel', locale=locale)} "
            f"{channel.mention}"))

    view.add_item(container)
    return view


# =========================================================================== #
# 12. Persistence marker
# =========================================================================== #
class TicketsPersistence(BaseView):
    """Marker view: registers the ticket panel's dynamic items at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(TicketOpenButton, TicketOpenSelect)
