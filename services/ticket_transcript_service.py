"""
Ticket transcripts — archiving a ticket's conversation when it closes.

This is a native exporter: it reads the channel through ``channel.history()``
and writes a compact JSON body. No external exporter binary, no extra runtime,
no subprocess and no bot token leaving the process — the Railway bill is driven
almost entirely by resident memory (docs/RAILWAY.md), and a .NET runtime kept
around to archive a few hundred tickets a day would be the most expensive line
in it.

Three things keep the stored size down, in order of effect:

1. The body uses short keys and omits every empty field, so a plain text
   message costs about forty bytes before compression.
2. Authors are stored **once**, in ``ticket_transcript_authors``; a message
   only carries the author id.
3. The whole body is compressed (``utils/compression.py``), which on
   conversational JSON removes another 90%+.

Attachments are referenced by their CDN url, never downloaded: an archiver that
copies files is a storage bill, not an archive.

One non-obvious requirement: Moddy posts its own cards with Components V2, so
their ``content`` and ``embeds`` are empty and all the text lives in the
component tree. :func:`extract_component_text` walks it, otherwise every card
Moddy wrote — the opening message, the closing card, every claim notice — would
be archived blank.

See docs/TICKETS.md and docs/TICKETS_INTEGRATION.md (the backend contract).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import discord

import config
from utils.compression import compress

logger = logging.getLogger('moddy.tickets.transcript')

# Body schema version. Bump it when the meaning of a key changes; the backend
# switches on it (docs/TICKETS_INTEGRATION.md).
TRANSCRIPT_VERSION = 1

# Above this, only the most recent messages are kept and ``truncated`` is set.
# Losing the beginning of a very long ticket costs less than losing how it
# ended, which is what anyone reading an archive is looking for.
MAX_TRANSCRIPT_MESSAGES = 20_000

# Per-message text cap, matching the other stored free-text fields.
MAX_MESSAGE_CONTENT = 4000

# A pathological channel must never hold a closure open.
EXPORT_TIMEOUT = 120


def transcript_url(key: str) -> str:
    """The dashboard link handed to the opener and to the ticket log."""
    return f"{config.DASHBOARD_URL.rstrip('/')}/transcripts/{key}"


def extract_component_text(components) -> str:
    """All the text of a Components V2 tree, newline-joined.

    Moddy's own messages carry no ``content``; without this they archive as
    empty. Walks children generically rather than matching on concrete classes,
    so a new container type does not silently swallow its text.
    """
    parts: List[str] = []

    def walk(node) -> None:
        content = getattr(node, 'content', None)
        if isinstance(content, str) and content:
            parts.append(content)
        for attr in ('children', 'components'):
            children = getattr(node, attr, None)
            if children:
                for child in children:
                    walk(child)
        accessory = getattr(node, 'accessory', None)
        if accessory is not None:
            walk(accessory)

    for component in components or ():
        walk(component)
    return "\n".join(parts)


def _serialise_message(message: discord.Message,
                       authors: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """One message as a compact dict. Empty fields are left out entirely."""
    author = message.author
    if author.id not in authors:
        authors[author.id] = {
            'author_id': author.id,
            'username': author.name,
            'display_name': getattr(author, 'display_name', author.name),
            'avatar_url': str(author.display_avatar.url) if author.display_avatar else None,
            'is_bot': bool(author.bot),
        }

    content = message.content or ""
    if not content:
        # A Components V2 card: the text is in the component tree.
        content = extract_component_text(message.components)

    entry: Dict[str, Any] = {
        'i': str(message.id),
        'a': author.id,
        't': int(message.created_at.timestamp()),
    }
    if content:
        entry['c'] = content[:MAX_MESSAGE_CONTENT]
    if message.edited_at:
        entry['ed'] = int(message.edited_at.timestamp())

    if message.attachments:
        entry['f'] = [{
            'n': a.filename,
            'u': a.url,
            's': a.size,
            **({'ct': a.content_type} if a.content_type else {}),
        } for a in message.attachments]

    if message.embeds:
        embeds = []
        for embed in message.embeds:
            item = {}
            if embed.title:
                item['t'] = embed.title[:256]
            if embed.description:
                item['d'] = embed.description[:MAX_MESSAGE_CONTENT]
            if embed.url:
                item['u'] = embed.url
            if embed.fields:
                item['fl'] = [{'n': f.name, 'v': f.value} for f in embed.fields]
            if item:
                embeds.append(item)
        if embeds:
            entry['e'] = embeds

    if message.reactions:
        entry['r'] = [{'e': str(r.emoji), 'c': r.count} for r in message.reactions]

    if message.reference and message.reference.message_id:
        entry['p'] = str(message.reference.message_id)

    if message.type is not discord.MessageType.default:
        entry['s'] = message.type.name

    return entry


class TicketTranscriptService:
    """Exports a ticket channel and stores the archive. ``bot.ticket_transcripts``."""

    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------------ #
    # Export
    # ------------------------------------------------------------------ #

    async def _read_channel(self, channel: discord.abc.Messageable
                            ) -> Tuple[List[Dict[str, Any]], Dict[int, Dict[str, Any]], bool]:
        """Read one channel into ``(messages, authors, truncated)``.

        Read newest-first and reversed at the end: when a channel is longer
        than the cap, that keeps the end of the conversation — the part anyone
        reading an archive actually wants — rather than its first day.
        """
        authors: Dict[int, Dict[str, Any]] = {}
        messages: List[Dict[str, Any]] = []
        count = 0
        async for message in channel.history(limit=MAX_TRANSCRIPT_MESSAGES + 1,
                                             oldest_first=False):
            count += 1
            if count > MAX_TRANSCRIPT_MESSAGES:
                return list(reversed(messages)), authors, True
            messages.append(_serialise_message(message, authors))
        return list(reversed(messages)), authors, False

    async def build_payload(self, channel: discord.abc.Messageable, *,
                            staff_thread: Optional[discord.Thread] = None
                            ) -> Tuple[Dict[str, Any], Dict[int, Dict[str, Any]], bool]:
        """The JSON body of a transcript, its authors, and whether it was cut."""
        messages, authors, truncated = await self._read_channel(channel)
        body: Dict[str, Any] = {'v': TRANSCRIPT_VERSION, 'messages': messages}

        if staff_thread is not None:
            # Kept under its own key, never merged into the conversation: this
            # is staff-only content and the dashboard must hide it from the
            # ticket's own author (docs/TICKETS_INTEGRATION.md).
            try:
                thread_messages, thread_authors, thread_truncated = \
                    await self._read_channel(staff_thread)
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.warning(f"[Tickets] Could not archive the staff thread "
                               f"of {getattr(channel, 'id', '?')}: {e}")
            else:
                body['staff_thread'] = {'messages': thread_messages}
                authors.update(thread_authors)
                truncated = truncated or thread_truncated

        return body, authors, truncated

    # ------------------------------------------------------------------ #
    # Capture (export + compress + store)
    # ------------------------------------------------------------------ #

    async def capture(self, channel: discord.abc.Messageable,
                      ticket: Dict[str, Any], category: Dict[str, Any],
                      closed_by: int) -> Optional[Dict[str, Any]]:
        """Archive one closure. Returns the stored row, or ``None``.

        Never raises: archiving is not worth failing a closure over. Every
        failure path logs and returns ``None``, and the caller then simply
        offers no transcript link rather than a dead one.
        """
        db = getattr(self.bot, 'db', None)
        if db is None:
            return None

        guild = getattr(channel, 'guild', None)
        staff_thread = None
        thread_id = ticket.get('staff_thread_id')
        if thread_id and guild is not None:
            staff_thread = guild.get_thread(thread_id)

        try:
            body, authors, truncated = await asyncio.wait_for(
                self.build_payload(channel, staff_thread=staff_thread),
                timeout=EXPORT_TIMEOUT)
        except asyncio.TimeoutError:
            logger.error(f"[Tickets] Transcript export timed out after "
                         f"{EXPORT_TIMEOUT}s on channel {getattr(channel, 'id', '?')}")
            return None
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"[Tickets] Could not read the history of "
                         f"{getattr(channel, 'id', '?')}: {e}")
            return None

        raw = json.dumps(body, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        payload, codec = compress(raw)

        try:
            row = await db.create_ticket_transcript(
                guild_id=ticket['guild_id'],
                channel_id=ticket['channel_id'],
                ticket_number=ticket['number'],
                panel_id=ticket['panel_id'],
                category_id=ticket['category_id'],
                category_name=category.get('name') or ticket['category_id'],
                owner_id=ticket['owner_id'],
                participants=ticket.get('participants') or [],
                claimed_by=ticket.get('claimed_by'),
                closed_by=closed_by,
                close_reason=ticket.get('close_reason'),
                opened_at=ticket['opened_at'],
                message_count=len(body['messages']),
                truncated=truncated,
                codec=codec,
                payload=payload,
                payload_size=len(raw),
                authors=list(authors.values()),
            )
        except Exception as e:  # noqa: BLE001 - archiving never fails a closure
            logger.error(f"[Tickets] Could not store the transcript of "
                         f"#{ticket.get('number')}: {e}", exc_info=True)
            return None

        stats = getattr(self.bot, 'stats', None)
        if stats is not None:
            stats.incr("ticket.transcript", guild_id=ticket['guild_id'],
                       dims={"codec": codec})
        logger.info(f"[Tickets] Transcript of #{ticket.get('number')}: "
                    f"{len(body['messages'])} messages, {len(raw)} -> "
                    f"{len(payload)} bytes ({codec})")
        return row
