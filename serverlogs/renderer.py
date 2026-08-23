"""Uniform rendering of every server-log message.

**This is the only place that decides what a log looks like.** Listeners
describe *what happened* by pushing labelled lines onto a :class:`LogEntry`;
this module turns that into the embed everyone sees:

.. code-block:: text

    ┌──────────────────────────────────────────────┐
    │ Role(s) added to a user                      │  title  = event title (i18n)
    │ > **User:** @dyvion_ (<@120…>)               │  description = "> **Label:** value"
    │ > **Added:** <@&966…>                        │
    │ > **Reason:** Join Roles                     │
    │                                     [avatar] │  thumbnail = the subject
    │ Moddy#0001                       26/07 13:12 │  footer = executor, timestamp
    └──────────────────────────────────────────────┘

Changing the wording of a log therefore never means touching Python: the
title comes from ``modules.logs.titles.<category>.<event>`` and every label
from ``modules.logs.fields.<label>``, in all five locales.

Embeds are a **documented exception** to the Components V2 rule (CLAUDE.md
rule 1): logs are posted through channel webhooks, and Discord rejects the
``IS_COMPONENTS_V2`` message flag on webhook messages that carry a classic
embed — the same exception ``modules/starboard.py`` already relies on.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any, Iterable, List, Optional, Sequence, Union

import discord

from serverlogs import registry
from utils.i18n import t

# Discord hard limits, with a safety margin so a rendered value can never
# make the whole webhook call fail.
MAX_DESCRIPTION = 4000
MAX_FIELD_VALUE = 1000
MAX_EMBED_TOTAL = 5800

#: Accent colour per event kind (see registry.KIND_*).
KIND_COLORS = {
    registry.KIND_CREATE: 0x57F287,      # green  — something appeared
    registry.KIND_DELETE: 0xED4245,      # red    — something disappeared
    registry.KIND_UPDATE: 0x5865F2,      # blurple— something changed
    registry.KIND_MODERATION: 0xF28500,  # orange — a moderator acted
}
FALLBACK_COLOR = 0x99AAB5


# --------------------------------------------------------------------------- #
# Value formatters — every listener formats through these, never by hand, so
# a user/channel/role always reads the same way in every single log.
# --------------------------------------------------------------------------- #

def fmt_user(user: Optional[Union[discord.abc.User, discord.Member]]) -> str:
    """``@name (<@id>)`` — the mention keeps it clickable even after a leave."""
    if user is None:
        return "—"
    name = getattr(user, "name", None) or str(user)
    return f"{escape(f'@{name}')} (<@{user.id}>)"

def fmt_user_id(user_id: Optional[int]) -> str:
    return f"`{user_id}`" if user_id else "—"


def fmt_channel(channel: Any) -> str:
    """``#name (<#id>)`` for anything that has a name and an id."""
    if channel is None:
        return "—"
    name = getattr(channel, "name", None)
    channel_id = getattr(channel, "id", None)
    if channel_id is None:
        return escape(str(channel))
    if name:
        return f"{escape(f'#{name}')} (<#{channel_id}>)"
    return f"<#{channel_id}>"


def fmt_role(role: Any) -> str:
    if role is None:
        return "—"
    role_id = getattr(role, "id", None)
    return f"<@&{role_id}>" if role_id else escape(str(role))


def fmt_roles(roles: Iterable[Any]) -> str:
    rendered = [fmt_role(role) for role in roles]
    return ", ".join(rendered) if rendered else "—"


def fmt_time(moment: Optional[datetime], style: str = "R") -> str:
    """Discord timestamp — rendered in each reader's own timezone and language."""
    if moment is None:
        return "—"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return f"<t:{int(moment.timestamp())}:{style}>"


def fmt_bool(value: Optional[bool], locale: str) -> str:
    if value is None:
        return "—"
    return t(f"modules.logs.values.{'yes' if value else 'no'}", locale=locale)


def fmt_number(value: Optional[int]) -> str:
    return f"`{value}`" if value is not None else "—"


def fmt_code(value: Optional[str]) -> str:
    if value in (None, ""):
        return "—"
    return f"`{escape_code(str(value))}`"


def fmt_duration(seconds: Optional[int], locale: str) -> str:
    """Human duration used by slowmodes, timeouts and archive durations."""
    if seconds is None:
        return "—"
    if seconds <= 0:
        return t("modules.logs.values.disabled", locale=locale)
    units = (("day", 86400), ("hour", 3600), ("minute", 60), ("second", 1))
    parts: List[str] = []
    remaining = int(seconds)
    for unit, size in units:
        if remaining >= size:
            amount, remaining = divmod(remaining, size)
            parts.append(t(f"modules.logs.values.duration.{unit}",
                           locale=locale, count=amount))
        if len(parts) == 2:
            break
    return " ".join(parts)


def fmt_permissions(permissions: Iterable[str], locale: str) -> str:
    names = [t(f"modules.logs.permissions.{name}", locale=locale) for name in permissions]
    return ", ".join(f"`{name}`" for name in names) if names else "—"


def escape(text: Optional[str]) -> str:
    """Neutralise markdown in user-controlled text (names, topics, reasons)."""
    if not text:
        return ""
    return discord.utils.escape_markdown(str(text))


def escape_code(text: str) -> str:
    return str(text).replace("`", "ˋ")


def truncate(text: str, limit: int) -> str:
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


# --------------------------------------------------------------------------- #
# The entry a listener builds
# --------------------------------------------------------------------------- #

class LogEntry:
    """A single log message under construction.

    Listeners only ever call :meth:`line`, :meth:`block`, :meth:`subject`,
    :meth:`actor` and :meth:`attach` — the layout, colours, truncation and
    overflow-to-file rules are enforced here for every event alike.
    """

    __slots__ = ("event", "locale", "kind", "_lines", "_blocks", "_files",
                 "_thumbnail", "_image", "_footer_text", "_footer_icon",
                 "_timestamp", "_title_override", "_url")

    def __init__(self, event: str, locale: str, *, kind: Optional[str] = None):
        self.event = event
        self.locale = locale
        self.kind = kind
        self._lines: List[str] = []
        self._blocks: List[tuple] = []          # (name, value, inline)
        self._files: List[discord.File] = []
        self._thumbnail: Optional[str] = None
        self._image: Optional[str] = None
        self._footer_text: Optional[str] = None
        self._footer_icon: Optional[str] = None
        self._timestamp: datetime = datetime.now(timezone.utc)
        self._title_override: Optional[str] = None
        self._url: Optional[str] = None

    # -- content ---------------------------------------------------------- #

    def line(self, label: str, value: Any) -> "LogEntry":
        """Add a ``> **Label:** value`` line. ``label`` is an i18n field key."""
        if value in (None, "", "—"):
            return self
        name = t(f"modules.logs.fields.{label}", locale=self.locale)
        self._lines.append(f"> **{name}:** {value}")
        return self

    def raw_line(self, value: str) -> "LogEntry":
        """Add a quoted line with no label (used for standalone notes)."""
        if value:
            self._lines.append(f"> {value}")
        return self

    def block(self, label: str, value: Any, *, inline: bool = False,
              filename: Optional[str] = None) -> "LogEntry":
        """Add a field for multi-line content (message bodies, before/after).

        Content longer than a field allows is moved to a ``.txt`` attachment
        automatically — a 3000-character message is never silently cut.
        """
        if value in (None, ""):
            return self
        name = t(f"modules.logs.fields.{label}", locale=self.locale)
        text = str(value)
        if len(text) > MAX_FIELD_VALUE:
            self.attach(filename or f"{label}.txt", text)
            note = t("modules.logs.values.moved_to_file", locale=self.locale)
            self._blocks.append((name, f"{truncate(text, MAX_FIELD_VALUE - 120)}\n\n-# {note}",
                                 inline))
        else:
            self._blocks.append((name, text, inline))
        return self

    def subject(self, user: Optional[Union[discord.abc.User, discord.Member]],
                *, label: str = "user", with_id: bool = True) -> "LogEntry":
        """The user/entity the event is about: line(s) + avatar thumbnail."""
        if user is None:
            return self
        self.line(label, fmt_user(user))
        if with_id:
            self.line("id", fmt_user_id(user.id))
        avatar = getattr(user, "display_avatar", None)
        if avatar is not None:
            self._thumbnail = avatar.url
        return self

    def actor(self, user: Optional[Union[discord.abc.User, discord.Member]],
              reason: Optional[str] = None) -> "LogEntry":
        """Who did it — rendered in the footer, plus the audit-log reason."""
        if reason:
            self.line("reason", escape(truncate(reason, 400)))
        if user is not None:
            self._footer_text = str(user)
            avatar = getattr(user, "display_avatar", None)
            self._footer_icon = avatar.url if avatar is not None else None
        return self

    def thumbnail(self, url: Optional[str]) -> "LogEntry":
        if url:
            self._thumbnail = url
        return self

    def image(self, url: Optional[str]) -> "LogEntry":
        if url:
            self._image = url
        return self

    def url(self, url: Optional[str]) -> "LogEntry":
        if url:
            self._url = url
        return self

    def title(self, text: str) -> "LogEntry":
        """Override the registry title (rare — prefer an i18n title key)."""
        self._title_override = text
        return self

    def at(self, moment: Optional[datetime]) -> "LogEntry":
        if moment is not None:
            self._timestamp = moment
        return self

    def attach(self, filename: str, content: Union[str, bytes]) -> "LogEntry":
        """Attach a text file (transcripts, overflowing content, diffs)."""
        data = content.encode("utf-8") if isinstance(content, str) else content
        self._files.append(discord.File(io.BytesIO(data), filename=filename))
        return self

    def attach_file(self, file: discord.File) -> "LogEntry":
        self._files.append(file)
        return self

    # -- output ----------------------------------------------------------- #

    @property
    def files(self) -> List[discord.File]:
        return self._files

    @property
    def is_empty(self) -> bool:
        return not self._lines and not self._blocks and not self._files

    def to_embed(self) -> discord.Embed:
        spec = registry.EVENTS.get(_full_key(self.event))
        kind = self.kind or (spec.kind if spec else registry.KIND_UPDATE)
        title = self._title_override or (
            t(spec.title_key, locale=self.locale) if spec else self.event
        )

        embed = discord.Embed(
            title=truncate(title, 250),
            description=truncate("\n".join(self._lines), MAX_DESCRIPTION) or None,
            colour=KIND_COLORS.get(kind, FALLBACK_COLOR),
            timestamp=self._timestamp,
            url=self._url,
        )
        for name, value, inline in self._blocks:
            embed.add_field(name=truncate(name, 250),
                            value=truncate(value, MAX_FIELD_VALUE),
                            inline=inline)
        if self._thumbnail:
            embed.set_thumbnail(url=self._thumbnail)
        if self._image:
            embed.set_image(url=self._image)
        if self._footer_text:
            embed.set_footer(text=truncate(self._footer_text, 2000),
                             icon_url=self._footer_icon)
        return embed


def _full_key(event: str) -> str:
    """Accept either a bare event name or a full ``category.event`` key."""
    if "." in event:
        return event
    keys = registry.keys_for(event)
    return keys[0] if keys else event


# --------------------------------------------------------------------------- #
# Transcripts — a raid deleting 200 messages must not spam 200 embeds
# --------------------------------------------------------------------------- #

def build_transcript(messages: Sequence[discord.Message], *, guild_name: str,
                     channel_name: str, header: str) -> discord.File:
    """Render deleted/purged messages as a readable ``.txt`` attachment."""
    lines = [
        header,
        f"Server : {guild_name}",
        f"Channel: #{channel_name}",
        f"Exported: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"Messages: {len(messages)}",
        "=" * 72,
        "",
    ]
    for message in messages:
        author = getattr(message, "author", None)
        author_name = f"{author} ({author.id})" if author else "Unknown author"
        created = getattr(message, "created_at", None)
        stamp = created.isoformat(timespec="seconds") if created else "unknown time"
        lines.append(f"[{stamp}] {author_name}")
        content = (getattr(message, "content", "") or "").rstrip()
        lines.append(content if content else "(no text content)")
        for attachment in getattr(message, "attachments", []) or []:
            lines.append(f"  ↳ attachment: {attachment.filename} — {attachment.url}")
        for embed in getattr(message, "embeds", []) or []:
            if embed.title or embed.description:
                lines.append(f"  ↳ embed: {embed.title or ''} {embed.description or ''}".rstrip())
        for sticker in getattr(message, "stickers", []) or []:
            lines.append(f"  ↳ sticker: {sticker.name}")
        lines.append("")

    data = "\n".join(lines).encode("utf-8")
    return discord.File(io.BytesIO(data), filename=f"transcript-{channel_name}.txt")
