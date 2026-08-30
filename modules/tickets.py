"""
Tickets module — configurable support tickets.

Three levels, and keeping them apart is what makes the rest of the module
simple:

- **A panel** is one message posted in a channel. It carries its own title,
  description and accent colour, and offers either **buttons** or a **select
  menu**. A guild can have several panels (``max_panels``).
- **A category** is one entry inside a panel: the button/option a member
  clicks. It decides *where* the ticket channel is created, *who* may open it,
  *what* each staff role may do inside it, and the *messages* the member sees.
  It has no language of its own: a ticket speaks the *server* language, set
  once in ``/config`` -> Server settings (``utils/guild_language.py``).
- **A ticket** is the channel a member ends up in. Its live state (owner,
  number, participants, staff thread, escalation) lives in the ``tickets``
  table — see ``db/repositories/tickets.py``. Runtime actions live in
  ``services/ticket_service.py``.

This file owns only the *configuration*: the schema, its normalisation, the
free/premium limits, the permission model, and posting/refreshing the panel
messages. It deliberately holds no ticket action, so the config surface and the
runtime surface can be reasoned about (and tested) separately.

Slash commands (``/ticket …``) are published **only in guilds where this
module is enabled** — see ``cogs/tickets.py`` and
``ModdyBot.register_module_commands``.

See docs/TICKETS.md.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any, Dict, List, Optional, Tuple

import discord

from modules.module_manager import ModuleBase, EXTERNAL_DELETED
from utils.emojis import TICKET

logger = logging.getLogger('moddy.modules.tickets')

MODULE_ID = "tickets"

# --------------------------------------------------------------------------- #
# Limits (free vs premium)
# --------------------------------------------------------------------------- #
FREE_MAX_PANELS = 3
PREMIUM_MAX_PANELS = 10
FREE_MAX_CATEGORIES = 5
PREMIUM_MAX_CATEGORIES = 15

# Hard ceilings imposed by Discord, independent of the plan: five buttons per
# ActionRow (a button panel uses at most three rows) and 25 options in a select.
MAX_BUTTONS_PER_PANEL = 15
MAX_SELECT_OPTIONS = 25

# --------------------------------------------------------------------------- #
# Field limits
# --------------------------------------------------------------------------- #
MAX_PANEL_NAME = 60
MAX_PANEL_TITLE = 100
MAX_PANEL_DESCRIPTION = 2000
MAX_CATEGORY_NAME = 60          # also the button label / select option label
MAX_CATEGORY_DESCRIPTION = 100  # select option description (Discord limit)
MAX_TICKET_MESSAGE = 2000
MAX_CHANNEL_NAME = 90           # Discord allows 100; leave room for prefixes

DEFAULT_ACCENT_COLOR = 0x5865F2

# --------------------------------------------------------------------------- #
# Panel styles
# --------------------------------------------------------------------------- #
STYLE_BUTTONS = "buttons"
STYLE_SELECT = "select"
PANEL_STYLES = (STYLE_BUTTONS, STYLE_SELECT)

# Button colours an admin may pick for a category (Discord's four styles; the
# link style makes no sense for a ticket button).
BUTTON_STYLES: Dict[str, discord.ButtonStyle] = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
}
DEFAULT_BUTTON_STYLE = "primary"

# --------------------------------------------------------------------------- #
# Ticket permissions
#
# One flat list, granted per role, per category. `admin` is the only implicit
# one: it grants everything AND survives an escalation (see the service).
# --------------------------------------------------------------------------- #
PERM_VIEW = "view"                  # see and talk in the ticket
PERM_CLOSE = "close"                # close / reopen it
PERM_CLAIM = "claim"                # take a ticket in charge (and release it)
PERM_UNCLAIM_OTHERS = "unclaim_others"  # release a ticket somebody else claimed
PERM_STAFF_THREAD = "staff_thread"  # open and join the private staff thread
PERM_RENAME = "rename"              # rename the ticket channel
PERM_MOVE = "move"                  # move it to another category
PERM_PARTICIPANTS = "participants"  # add/remove members and roles
PERM_ADMIN = "admin"                # everything above + kept on escalation

TICKET_PERMISSIONS: Tuple[str, ...] = (
    PERM_VIEW, PERM_CLOSE, PERM_CLAIM, PERM_UNCLAIM_OTHERS, PERM_STAFF_THREAD,
    PERM_RENAME, PERM_MOVE, PERM_PARTICIPANTS, PERM_ADMIN,
)

# What a brand new role entry gets: enough to actually work the ticket, not
# enough to reorganise the server.
DEFAULT_ROLE_PERMISSIONS = (PERM_VIEW, PERM_CLOSE, PERM_CLAIM, PERM_STAFF_THREAD)

# Placeholders usable in the messages an admin writes.
PLACEHOLDERS = (
    "{user}", "{username}", "{display_name}", "{server}",
    "{category}", "{number}", "{ticket}",
)

DEFAULT_NAME_FORMAT = "ticket-{number}"
# Placeholders usable in the channel name (a subset: no mentions in a name).
NAME_PLACEHOLDERS = ("{number}", "{username}", "{display_name}", "{category}")

# Open tickets one member may hold in one category at the same time.
DEFAULT_MAX_OPEN_PER_USER = 1
MAX_OPEN_PER_USER_CEILING = 10

# --------------------------------------------------------------------------- #
# The buttons offered under the opening message
#
# An admin picks which of these the ticket message carries. `close_request` is
# in the catalogue but out of the default set: offering the closure to the
# opener is a staff command (`/ticket close-request`), and a server that wants
# it as a button says so explicitly.
# --------------------------------------------------------------------------- #
BTN_CLOSE = "close"
BTN_CLAIM = "claim"
BTN_ESCALATE = "escalate"
BTN_STAFF_THREAD = "staff_thread"
BTN_PARTICIPANTS = "participants"
BTN_CLOSE_REQUEST = "close_request"

TICKET_BUTTONS: Tuple[str, ...] = (
    BTN_CLOSE, BTN_CLAIM, BTN_ESCALATE, BTN_STAFF_THREAD, BTN_PARTICIPANTS,
    BTN_CLOSE_REQUEST,
)
DEFAULT_TICKET_BUTTONS: Tuple[str, ...] = (
    BTN_CLOSE, BTN_CLAIM, BTN_ESCALATE, BTN_STAFF_THREAD, BTN_PARTICIPANTS,
)

# --------------------------------------------------------------------------- #
# Claim status, shown in the channel name
#
# These four are the ONE place Moddy uses a Unicode emoji outside country flags
# (see CLAUDE.md rule 3), and not by choice: a Discord channel name cannot
# carry a custom emoji, so a coloured dot is the only way to read a ticket's
# state from the channel list.
# --------------------------------------------------------------------------- #
STATUS_DOT_UNCLAIMED = "🔴"
STATUS_DOT_CLAIMED = "🟢"
STATUS_DOT_ESCALATED = "🟣"
STATUS_DOT_CLOSED = "⚫"
STATUS_DOTS = (STATUS_DOT_UNCLAIMED, STATUS_DOT_CLAIMED,
               STATUS_DOT_ESCALATED, STATUS_DOT_CLOSED)
# The separator between the dot and the name — "🔴〡ticket-0003".
STATUS_SEPARATOR = "〡"

# A line made of nothing but this, inside a ticket message, becomes a real
# Components V2 separator instead of three dashes of text.
SEPARATOR_MARKER = "---"

# Channel types a panel message can be posted in.
PANEL_CHANNEL_TYPES = [discord.ChannelType.text, discord.ChannelType.news]


# --------------------------------------------------------------------------- #
# Default wording
#
# Every one of these is what the module would use if the admin left the field
# empty, which is exactly why the configuration modals pre-fill them: an admin
# should be *editing* the message their members will see, never guessing at it
# in front of a blank box.
# --------------------------------------------------------------------------- #
def default_open_message(locale: str) -> str:
    """The **whole** message pinned in a ticket when the category defines none.

    Not a paragraph the module then decorates: the stored text *is* the
    message, title line and footer included, so an admin who opens the editor
    can rewrite every part of what their members read.

    ``{icon}`` is substituted here rather than passed to ``t()`` as a kwarg:
    ``t()`` runs ``str.format`` over the whole string when it gets any, and
    that would eat the ``{number}`` / ``{user}`` placeholders the admin is
    meant to keep.
    """
    from utils.emojis import TICKET
    from utils.i18n import t
    return t('modules.tickets.channel.default_open_message',
             locale=locale).replace('{icon}', TICKET)


def default_close_message(locale: str) -> str:
    """The wording suggested for the closing card."""
    from utils.i18n import t
    return t('modules.tickets.channel.default_close_message', locale=locale)


def default_panel_title(locale: str) -> str:
    from utils.i18n import t
    return t('modules.tickets.panel.default_title', locale=locale)


def default_panel_description(locale: str) -> str:
    from utils.i18n import t
    return t('modules.tickets.panel.default_description', locale=locale)


# --------------------------------------------------------------------------- #
# Ids
# --------------------------------------------------------------------------- #
def new_panel_id() -> str:
    """Short, url-safe id for a panel (custom_ids are capped at 100 chars)."""
    return f"p_{secrets.token_hex(3)}"


def new_category_id() -> str:
    return f"c_{secrets.token_hex(3)}"


# --------------------------------------------------------------------------- #
# Normalisation
#
# Everything that reads the config goes through normalize_config(): a panel
# written by the dashboard, an old config missing a key added later, and a
# panel just built by the /config UI all come out with the exact same shape.
# --------------------------------------------------------------------------- #
def _as_int(value: Any) -> Optional[int]:
    """Snowflakes arrive as int (from Discord) or str (from JSON/the API)."""
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _as_id_list(value: Any) -> List[int]:
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for item in value:
        parsed = _as_int(item)
        if parsed is not None and parsed not in out:
            out.append(parsed)
    return out


def _as_text(value: Any, limit: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:limit] if text else None


def normalize_permissions(value: Any) -> Dict[str, List[str]]:
    """``{role_id: [perm, ...]}`` with unknown roles and perms dropped.

    Keys stay **strings** because this dict is stored as JSON, where an int key
    would come back as a string anyway.
    """
    if not isinstance(value, dict):
        return {}
    out: Dict[str, List[str]] = {}
    for role_id, perms in value.items():
        parsed_role = _as_int(role_id)
        if parsed_role is None or not isinstance(perms, (list, tuple)):
            continue
        kept = [p for p in TICKET_PERMISSIONS if p in perms]
        if kept:
            out[str(parsed_role)] = kept
    return out


def normalize_category(raw: Any) -> Optional[Dict[str, Any]]:
    """One category entry, or ``None`` if it is not usable at all."""
    if not isinstance(raw, dict):
        return None

    name = _as_text(raw.get('name'), MAX_CATEGORY_NAME)
    if not name:
        return None

    button_style = raw.get('button_style')
    if button_style not in BUTTON_STYLES:
        button_style = DEFAULT_BUTTON_STYLE

    max_open = _as_int(raw.get('max_open_per_user'))
    if max_open is None or max_open < 1:
        max_open = DEFAULT_MAX_OPEN_PER_USER
    max_open = min(max_open, MAX_OPEN_PER_USER_CEILING)

    # An explicitly empty button list is a real answer ("no buttons, commands
    # only"), so only a *missing* key falls back to the default set.
    raw_buttons = raw.get('buttons')
    if isinstance(raw_buttons, (list, tuple)):
        buttons = [b for b in TICKET_BUTTONS if b in raw_buttons]
    else:
        buttons = list(DEFAULT_TICKET_BUTTONS)

    return {
        'id': _as_text(raw.get('id'), 32) or new_category_id(),
        'name': name,
        'emoji': _as_text(raw.get('emoji'), 64),
        'description': _as_text(raw.get('description'), MAX_CATEGORY_DESCRIPTION),
        'button_style': button_style,
        'discord_category_id': _as_int(raw.get('discord_category_id')),
        'allowed_role_ids': _as_id_list(raw.get('allowed_role_ids')),
        'denied_role_ids': _as_id_list(raw.get('denied_role_ids')),
        'ping_role_ids': _as_id_list(raw.get('ping_role_ids')),
        'ping_staff_roles': bool(raw.get('ping_staff_roles', True)),
        'permissions': normalize_permissions(raw.get('permissions')),
        'open_message': _as_text(raw.get('open_message'), MAX_TICKET_MESSAGE),
        'close_message': _as_text(raw.get('close_message'), MAX_TICKET_MESSAGE),
        'buttons': buttons,
        'claim_enabled': bool(raw.get('claim_enabled', True)),
        'claim_lock': bool(raw.get('claim_lock', False)),
        'name_format': _as_text(raw.get('name_format'), MAX_CHANNEL_NAME) or DEFAULT_NAME_FORMAT,
        'max_open_per_user': max_open,
        'enabled': bool(raw.get('enabled', True)),
    }


def normalize_panel(raw: Any) -> Optional[Dict[str, Any]]:
    """One panel entry, or ``None`` if it is not usable at all."""
    if not isinstance(raw, dict):
        return None

    name = _as_text(raw.get('name'), MAX_PANEL_NAME)
    if not name:
        return None

    style = raw.get('style')
    if style not in PANEL_STYLES:
        style = STYLE_BUTTONS

    categories = []
    seen_ids = set()
    for entry in raw.get('categories') or []:
        category = normalize_category(entry)
        if not category or category['id'] in seen_ids:
            continue
        seen_ids.add(category['id'])
        categories.append(category)

    accent = _as_int(raw.get('accent_color'))
    if accent is not None:
        accent = max(0, min(accent, 0xFFFFFF))

    return {
        'id': _as_text(raw.get('id'), 32) or new_panel_id(),
        'name': name,
        'channel_id': _as_int(raw.get('channel_id')),
        'message_id': _as_int(raw.get('message_id')),
        'title': _as_text(raw.get('title'), MAX_PANEL_TITLE),
        'description': _as_text(raw.get('description'), MAX_PANEL_DESCRIPTION),
        'accent_color': accent,
        'style': style,
        'placeholder': _as_text(raw.get('placeholder'), 150),
        'enabled': bool(raw.get('enabled', True)),
        'categories': categories,
    }


def normalize_config(raw: Any) -> Dict[str, Any]:
    """The whole module config, always ``{'panels': [...]}``."""
    if not isinstance(raw, dict):
        return {'panels': []}

    panels = []
    seen_ids = set()
    for entry in raw.get('panels') or []:
        panel = normalize_panel(entry)
        if not panel or panel['id'] in seen_ids:
            continue
        seen_ids.add(panel['id'])
        panels.append(panel)

    return {'panels': panels}


# --------------------------------------------------------------------------- #
# Limits
# --------------------------------------------------------------------------- #
async def get_limits(bot, guild_id: int) -> Dict[str, Any]:
    """How many panels/categories this guild may have right now.

    Premium is read at call time (never cached on a view): a panel can sit open
    for hours, and a stale render must never grant an entitlement.
    See docs/PREMIUM.md.
    """
    from utils.subscription import is_guild_premium

    try:
        premium = await is_guild_premium(bot, guild_id)
    except Exception as e:
        # Fail closed on the *limit*, not on the feature: a lookup error must
        # not silently hand a free guild the premium quota.
        logger.error(f"[Tickets] Premium check failed for guild {guild_id}: {e}")
        premium = False

    return {
        'premium': premium,
        'max_panels': PREMIUM_MAX_PANELS if premium else FREE_MAX_PANELS,
        'max_categories': PREMIUM_MAX_CATEGORIES if premium else FREE_MAX_CATEGORIES,
    }


def max_categories_for_style(style: str, limit: int) -> int:
    """The plan limit, capped by what the chosen rendering can actually show."""
    ceiling = MAX_SELECT_OPTIONS if style == STYLE_SELECT else MAX_BUTTONS_PER_PANEL
    return min(limit, ceiling)


# --------------------------------------------------------------------------- #
# Lookups
# --------------------------------------------------------------------------- #
def find_panel(config: Dict[str, Any], panel_id: str) -> Optional[Dict[str, Any]]:
    return next((p for p in config.get('panels', []) if p['id'] == panel_id), None)


def find_category(panel: Optional[Dict[str, Any]],
                  category_id: str) -> Optional[Dict[str, Any]]:
    if not panel:
        return None
    return next((c for c in panel.get('categories', []) if c['id'] == category_id), None)


def locate_category(config: Dict[str, Any], panel_id: str,
                    category_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """``(panel, category)`` — either may be ``None`` if it was deleted."""
    panel = find_panel(config, panel_id)
    return panel, find_category(panel, category_id)


# --------------------------------------------------------------------------- #
# Permission model
# --------------------------------------------------------------------------- #
def role_permissions(category: Dict[str, Any], role_id: int) -> List[str]:
    """Permissions granted to one role, with ``admin`` expanded."""
    perms = category.get('permissions', {}).get(str(role_id), [])
    if PERM_ADMIN in perms:
        return list(TICKET_PERMISSIONS)
    return list(perms)


def staff_role_ids(category: Dict[str, Any],
                   *, permission: Optional[str] = None) -> List[int]:
    """Roles with any permission in this category, or with a specific one."""
    out = []
    for role_id, perms in category.get('permissions', {}).items():
        parsed = _as_int(role_id)
        if parsed is None:
            continue
        granted = list(TICKET_PERMISSIONS) if PERM_ADMIN in perms else perms
        if permission is None or permission in granted:
            out.append(parsed)
    return out


def member_permissions(member: discord.Member, category: Dict[str, Any],
                       ticket: Optional[Dict[str, Any]] = None) -> set:
    """Everything ``member`` may do in a ticket of this category.

    Three sources, deliberately in this order:

    1. A guild administrator always has everything — locking the bot's own
       config out of the people who own the server would be absurd.
    2. Each of the member's roles contributes its granted permissions.
    3. The ticket **owner**, and anyone added to the ticket by hand, may always
       see it. That mirrors the channel overwrites exactly — someone who can
       read the ticket but whom the permission model claims cannot see it would
       be refused actions that are open to everyone in the ticket, such as
       asking for it to be closed. Being in a ticket grants no *staff*
       permission: closing your own ticket is the server's decision (grant
       ``close`` to a members role for that); asking for it needs none.
    """
    if getattr(member, 'guild_permissions', None) and member.guild_permissions.administrator:
        return set(TICKET_PERMISSIONS)

    granted = set()
    role_ids = {role.id for role in getattr(member, 'roles', [])}
    for role_id in role_ids:
        granted.update(role_permissions(category, role_id))

    if ticket:
        in_ticket = (
            ticket.get('owner_id') == member.id
            or member.id in (ticket.get('participants') or [])
            or role_ids & set(ticket.get('participant_roles') or [])
        )
        if in_ticket:
            granted.add(PERM_VIEW)

    return granted


def can_open(member: discord.Member, category: Dict[str, Any]) -> bool:
    """Is this member allowed to open a ticket in this category?

    ``denied_role_ids`` always wins over ``allowed_role_ids`` — a deny list
    that could be defeated by also holding an allowed role would be a trap.
    An empty allow list means "everyone", which is what an admin expects from
    a field they never filled in.
    """
    role_ids = {role.id for role in getattr(member, 'roles', [])}

    if role_ids & set(category.get('denied_role_ids', [])):
        return False

    allowed = category.get('allowed_role_ids', [])
    if not allowed:
        return True
    if getattr(member, 'guild_permissions', None) and member.guild_permissions.administrator:
        return True
    return bool(role_ids & set(allowed))


# --------------------------------------------------------------------------- #
# Text rendering
# --------------------------------------------------------------------------- #
def render_text(template: str, *, member: discord.abc.User, guild: discord.Guild,
                category: Dict[str, Any], number: int,
                channel: Optional[discord.abc.GuildChannel] = None) -> str:
    """Substitute the ``{placeholders}`` an admin may use in their messages.

    ``str.format`` is deliberately NOT used: an admin who types a stray ``{``
    would blow up the message for everyone.
    """
    values = {
        "{user}": member.mention,
        "{username}": member.name,
        "{display_name}": getattr(member, 'display_name', member.name),
        "{server}": guild.name,
        "{category}": category.get('name', ''),
        "{number}": str(number),
        "{ticket}": channel.mention if channel else f"#{number}",
    }
    out = template
    for key, value in values.items():
        out = out.replace(key, str(value))
    return out


def split_message_blocks(text: Optional[str]) -> List[str]:
    """Cut a ticket message on its ``---`` lines.

    Returns the text blocks between the separators, in order and stripped of
    empty ones. The caller turns the gaps into real Components V2 separators —
    writing ``---`` on its own line is the only markup an admin has for that,
    since Discord's markdown horizontal rule does not exist inside a container.
    """
    if not text:
        return []
    blocks: List[str] = []
    current: List[str] = []
    for line in text.splitlines():
        if line.strip() == SEPARATOR_MARKER:
            blocks.append("\n".join(current).strip())
            current = []
            continue
        current.append(line)
    blocks.append("\n".join(current).strip())
    return [block for block in blocks if block]


def render_channel_name(category: Dict[str, Any], *, member: discord.abc.User,
                        number: int) -> str:
    """Build the ticket channel name from the category's format."""
    values = {
        "{number}": f"{number:04d}",
        "{username}": member.name,
        "{display_name}": getattr(member, 'display_name', member.name),
        "{category}": category.get('name', ''),
    }
    name = category.get('name_format') or DEFAULT_NAME_FORMAT
    for key, value in values.items():
        name = name.replace(key, str(value))

    # Discord lowercases and mangles text channel names anyway; doing it here
    # keeps the stored name and the real one identical.
    cleaned = "".join(
        c if (c.isalnum() or c in "-_") else "-"
        for c in name.lower().replace(" ", "-")
    ).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return (cleaned or f"ticket-{number:04d}")[:MAX_CHANNEL_NAME]


# --------------------------------------------------------------------------- #
# Claim status in the channel name
#
# The dot is a *prefix*, never part of the stored format: it is stripped before
# a new one is applied, so a ticket that goes unclaimed → claimed → escalated →
# closed keeps exactly one dot however many times it changed hands.
# --------------------------------------------------------------------------- #
def ticket_status_dot(category: Dict[str, Any],
                      ticket: Dict[str, Any]) -> Optional[str]:
    """The coloured dot a ticket's channel name should carry, if any.

    ``None`` when the category does not use the claim system: a server that
    switched claiming off has no use for a red dot on every ticket, and the
    escalated/closed colours only read as a scale next to the other two.
    """
    if not category.get('claim_enabled', True):
        return None
    if ticket.get('status') == 'closed':
        return STATUS_DOT_CLOSED
    if ticket.get('escalated'):
        return STATUS_DOT_ESCALATED
    return STATUS_DOT_CLAIMED if ticket.get('claimed_by') else STATUS_DOT_UNCLAIMED


def strip_status_prefix(name: str) -> str:
    """``"🔴〡ticket-0003"`` → ``"ticket-0003"``; anything else is untouched."""
    for dot in STATUS_DOTS:
        prefix = f"{dot}{STATUS_SEPARATOR}"
        if name.startswith(prefix):
            return name[len(prefix):]
        if name.startswith(dot):
            return name[len(dot):].lstrip(STATUS_SEPARATOR)
    return name


def apply_status_prefix(name: str, dot: Optional[str]) -> str:
    """Put ``dot`` in front of a channel name, replacing any dot already there."""
    base = strip_status_prefix(name)
    if not dot:
        return base[:MAX_CHANNEL_NAME]
    return f"{dot}{STATUS_SEPARATOR}{base}"[:MAX_CHANNEL_NAME]


def parse_emoji(raw: Optional[str]) -> Optional[discord.PartialEmoji]:
    """Turn a stored emoji string into something a Button/SelectOption accepts."""
    if not raw:
        return None
    try:
        emoji = discord.PartialEmoji.from_str(raw.strip())
    except Exception:
        return None
    # from_str never fails on plain text: reject a "name" with no id that isn't
    # a real unicode emoji, otherwise Discord rejects the whole message.
    if emoji.id is None and (not emoji.name or emoji.name.isascii()):
        return None
    return emoji


# --------------------------------------------------------------------------- #
# The module
# --------------------------------------------------------------------------- #
class TicketsModule(ModuleBase):
    """Per-guild ticket configuration and panel messages."""

    MODULE_ID = MODULE_ID
    MODULE_NAME = "Tickets"
    MODULE_DESCRIPTION = "Support tickets with panels, categories and per-role permissions"
    MODULE_EMOJI = TICKET
    MODULE_ORDER = 25
    # Panels are messages written in the server language: a language change
    # has to re-post them (ModuleManager.apply_language_change).
    LANGUAGE_DEPENDENT_MESSAGES = True

    def __init__(self, bot, guild_id: int):
        super().__init__(bot, guild_id)
        self.panels: List[Dict[str, Any]] = []

    # -- ModuleBase ------------------------------------------------------- #
    async def load_config(self, config_data: Dict[str, Any]) -> bool:
        try:
            normalized = normalize_config(config_data)
            self.config = normalized
            self.panels = normalized['panels']
            # A guild "has tickets" as soon as one panel is switched on. That
            # is also the condition under which /ticket is published in the
            # guild, so it must not depend on anything transient.
            self.enabled = any(p['enabled'] for p in self.panels)
            return True
        except Exception as e:
            logger.error(f"[Tickets] Error loading config for guild {self.guild_id}: {e}",
                         exc_info=True)
            return False

    def get_default_config(self) -> Dict[str, Any]:
        return {'panels': []}

    def get_required_fields(self) -> List[str]:
        # Nothing is required to *store* the config: a guild that created a
        # panel but has not picked its channel yet must be able to save and
        # come back to it. What a panel needs to actually be posted is checked
        # in validate_config below.
        return []

    async def validate_config(self, config_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        normalized = normalize_config(config_data)
        guild = self.bot.get_guild(self.guild_id) if self.bot else None

        limits = await get_limits(self.bot, self.guild_id) if self.bot else {
            'max_panels': FREE_MAX_PANELS, 'max_categories': FREE_MAX_CATEGORIES,
        }

        if len(normalized['panels']) > limits['max_panels']:
            return False, (f"Too many ticket panels "
                           f"({len(normalized['panels'])}/{limits['max_panels']})")

        for panel in normalized['panels']:
            category_cap = max_categories_for_style(panel['style'], limits['max_categories'])
            if len(panel['categories']) > category_cap:
                return False, (f"Panel '{panel['name']}' has too many categories "
                               f"({len(panel['categories'])}/{category_cap})")

            if guild and panel['channel_id']:
                channel = guild.get_channel(panel['channel_id'])
                if channel is None:
                    return False, f"Panel '{panel['name']}': channel not found"
                if not isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
                    return False, f"Panel '{panel['name']}': not a text channel"

            for category in panel['categories']:
                if guild and category['discord_category_id']:
                    parent = guild.get_channel(category['discord_category_id'])
                    if parent is None:
                        return False, (f"Category '{category['name']}': "
                                       f"Discord category not found")
                    if not isinstance(parent, discord.CategoryChannel):
                        return False, (f"Category '{category['name']}': "
                                       f"the destination is not a category")

        return True, None

    async def on_external_config_change(self, action: str) -> Dict[str, Any]:
        """Re-post the panels after a dashboard-side change.

        Reloading the config is not enough here: a panel is a *message* in
        Discord. Its title, its buttons and its very existence are stored
        state that only a re-post can bring in line — exactly what ``/config``
        does on save. See docs/MODULE_SYSTEM.md §3bis.
        """
        if action == EXTERNAL_DELETED:
            # The stored config is already gone: only clean Discord up, never
            # write anything back (that would half-resurrect the module).
            removed = await self.delete_all_panels(persist=False)
            return {"panels_deleted": removed}

        recap = await self.refresh_all_panels()
        return {
            "panels": len(self.panels),
            "panels_posted": recap['posted'],
            "panels_failed": recap['failed'],
        }

    # -- helpers ---------------------------------------------------------- #
    @property
    def guild(self) -> Optional[discord.Guild]:
        return self.bot.get_guild(self.guild_id) if self.bot else None

    def get_panel(self, panel_id: str) -> Optional[Dict[str, Any]]:
        return find_panel({'panels': self.panels}, panel_id)

    def get_category(self, panel_id: str, category_id: str):
        return locate_category({'panels': self.panels}, panel_id, category_id)

    def open_categories(self, panel: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Categories a panel actually renders (enabled, with a destination)."""
        return [c for c in panel.get('categories', [])
                if c['enabled'] and c.get('discord_category_id')]

    async def _persist(self) -> None:
        """Write the panel list back, keeping ``message_id`` in sync.

        Goes straight to the DB rather than through ``save_module_config``:
        this only ever writes ids the bot itself just produced, and the
        module instance is already holding the authoritative state.
        """
        if not self.bot or not self.bot.db:
            return
        try:
            await self.bot.db.update_guild_data(
                self.guild_id, f"modules.{MODULE_ID}", {'panels': self.panels})
        except Exception as e:
            logger.error(f"[Tickets] Could not persist panels for guild "
                         f"{self.guild_id}: {e}")

    # -- panel messages --------------------------------------------------- #
    async def refresh_panel(self, panel: Dict[str, Any], *,
                            persist: bool = True) -> Optional[discord.Message]:
        """(Re)post one panel message, replacing the previous one.

        Always deleting and re-posting is the point: it repairs a panel someone
        deleted, and it is the only way a title, a colour or a new category
        reaches the message.
        """
        from utils.ticket_views import build_panel_view

        guild = self.guild
        if not guild or not panel.get('channel_id') or not panel.get('enabled'):
            return None

        channel = guild.get_channel(panel['channel_id'])
        if not isinstance(channel, discord.TextChannel):
            return None

        if not self.open_categories(panel):
            # A panel with nothing to click is worse than no panel: take the
            # old message down rather than leaving dead buttons up.
            await self.delete_panel(panel, persist=persist)
            return None

        await self.delete_panel(panel, persist=False)

        try:
            message = await channel.send(view=build_panel_view(panel))
        except discord.Forbidden:
            logger.warning(f"[Tickets] Cannot post panel {panel['id']} in guild "
                           f"{self.guild_id} (missing permissions)")
            return None
        except discord.HTTPException as e:
            logger.error(f"[Tickets] Failed to post panel {panel['id']} in guild "
                         f"{self.guild_id}: {e}")
            return None

        panel['message_id'] = message.id
        if persist:
            await self._persist()
        return message

    async def refresh_all_panels(self) -> Dict[str, int]:
        """Re-post every enabled panel. Returns a small recap for the dashboard."""
        posted = failed = 0
        for panel in self.panels:
            if not panel['enabled']:
                await self.delete_panel(panel, persist=False)
                continue
            message = await self.refresh_panel(panel, persist=False)
            if message:
                posted += 1
            elif panel.get('channel_id'):
                failed += 1
        await self._persist()
        return {'posted': posted, 'failed': failed}

    async def delete_panel(self, panel: Dict[str, Any], *,
                           persist: bool = True) -> bool:
        """Take a panel message down. Returns True if a message was removed."""
        message_id = panel.get('message_id')
        if not message_id:
            return False

        guild = self.guild
        channel = guild.get_channel(panel['channel_id']) if guild and panel.get('channel_id') else None
        removed = False
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.get_partial_message(message_id).delete()
                removed = True
            except (discord.NotFound, discord.Forbidden):
                pass
            except discord.HTTPException as e:
                logger.error(f"[Tickets] Could not delete panel message "
                             f"{message_id} in guild {self.guild_id}: {e}")

        panel['message_id'] = None
        if persist:
            await self._persist()
        return removed

    async def delete_all_panels(self, *, persist: bool = True) -> int:
        removed = 0
        for panel in self.panels:
            if await self.delete_panel(panel, persist=False):
                removed += 1
        if persist:
            await self._persist()
        return removed
