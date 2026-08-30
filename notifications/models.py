"""
Data model of the centralized notification system.

Everything Moddy sends to a human — a DM, a mail, a card on the dashboard —
is one **notification**: a uniform, platform-agnostic payload plus the
identity of whoever caused it to be sent. This module holds that model and
nothing else (no Discord calls, no database), so it can be imported from the
render layer, the service, the staff commands and the tests alike.

Three ideas carry the whole design:

* :class:`NotificationContent` is a **template**, not a finished message. Its
  strings keep their ``{placeholders}``; the resolved values travel next to it
  as ``variables``. That is what lets thousands of identical welcome DMs share
  a single stored body (hashed by :meth:`NotificationContent.template_hash`)
  while each notification stays reproducible to the character.
* :class:`NotificationSource` says **who** is behind the message: a Moddy
  service, a server, both, or Moddy itself speaking officially. The DM's
  attribution buttons and whether it can be reported are derived from it, and
  from nothing else.
* The same content renders to Discord, to an email and to the dashboard
  (:meth:`NotificationContent.to_email` / :meth:`to_dashboard`), so a
  suspension notice reads the same wherever the recipient sees it.

See docs/NOTIFICATIONS.md.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class SourceKind(str, Enum):
    """What sort of actor is behind a notification.

    ``OFFICIAL`` is the only kind that carries **no** attribution buttons at
    all: an account suspension or a leaked-token alert is Moddy speaking as an
    institution, and offering to "report" it would be nonsense. It is still
    stored and counted like every other notification.
    """

    OFFICIAL = "official"            # Moddy itself, institutional
    SERVICE = "service"              # a Moddy service acting on its own
    GUILD = "guild"                  # a server, through Moddy
    SERVICE_GUILD = "service_guild"  # a Moddy service acting for a server


class ContentAuthor(str, Enum):
    """Who actually wrote the words the recipient reads.

    This — not :class:`SourceKind` — decides whether the report button works.
    A welcome DM is written by the server (``GUILD``) and can be abused, so it
    is reportable. A sanction notice is worded by Moddy on the server's behalf
    (``MODDY``): there is nothing for the abuse team to judge, the button is
    rendered disabled and the source panel says why.
    """

    MODDY = "moddy"
    GUILD = "guild"
    STAFF = "staff"


class RecipientType(str, Enum):
    """Who a notification is addressed to.

    Broadcasts are exploded into one row per recipient (they share a
    ``batch_id``), so ``ALL_USERS`` / ``SEGMENT`` never appear on a delivered
    row — they describe the *audience* a batch was aimed at.
    """

    DISCORD_USER = "discord_user"
    DISCORD_GUILD = "discord_guild"
    ALL_USERS = "all_users"
    ALL_GUILDS = "all_guilds"
    SEGMENT = "segment"
    EMAIL = "email"


class Platform(str, Enum):
    """Where a notification must be delivered or displayed."""

    DISCORD = "discord"
    EMAIL = "email"
    DASHBOARD = "dashboard"


class DeliveryStatus(str, Enum):
    """State of one (notification, platform) pair."""

    PENDING = "pending"    # queued, not attempted yet
    SENT = "sent"          # accepted by the platform
    FAILED = "failed"      # attempted, refused (closed DMs, HTTP error…)
    SKIPPED = "skipped"    # deliberately not attempted (no email on file…)


class ReportStatus(str, Enum):
    """State of an abuse report filed against a notification."""

    PENDING = "pending"
    CLAIMED = "claimed"
    ACCEPTED = "accepted"
    REFUSED = "refused"


#: Platforms a notification defaults to when the caller says nothing.
DEFAULT_PLATFORMS = (Platform.DISCORD,)


# --------------------------------------------------------------------------- #
# Services
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ServiceInfo:
    """A Moddy feature allowed to send notifications.

    ``emoji`` is a custom Moddy emoji (see ``utils/emojis.py``) shown on the
    attribution button; ``i18n_key`` resolves the human name shown next to it.
    Registering a service here is what makes it addressable — the service id is
    stored on every notification row and is what a staff lookup shows.
    """

    id: str
    i18n_key: str
    emoji_name: str   # attribute name in utils.emojis, resolved lazily

    @property
    def emoji(self) -> str:
        from utils import emojis as _emojis
        return getattr(_emojis, self.emoji_name, _emojis.MODDY_SQUARE_MIN)


def _svc(sid: str, emoji_name: str) -> ServiceInfo:
    return ServiceInfo(id=sid, i18n_key=f"notifications.services.{sid}", emoji_name=emoji_name)


#: service id -> ServiceInfo. Adding a sender means adding one line here.
SERVICES: Dict[str, ServiceInfo] = {
    s.id: s for s in (
        _svc("moddy", "MODDY_SQUARE_MIN"),           # Moddy itself / staff broadcasts
        _svc("moddy_team", "MODDY_SQUARE_MIN"),      # the team writing to a human
        _svc("welcome_dm", "WAVING_HAND"),           # modules/welcome_dm.py
        _svc("moderation", "LEGAL"),                 # manual sanctions
        _svc("automod_ai", "SHIELD"),                # modules/automod_ai.py
        _svc("appeals", "BALANCE"),                  # services/appeal_service.py
        _svc("altguard", "ALTGUARD"),                # modules/altguard.py
        _svc("tickets", "TICKET"),                   # services/ticket_service.py
        _svc("reminder", "TIME"),                    # cogs/reminder.py
        _svc("interserver", "GROUPS"),               # modules/interserver.py
        _svc("token_detector", "SHIELD"),            # cogs/token_detector.py
        _svc("global_sanctions", "EXCLAMATION"),     # services/global_sanction_service.py
        _svc("expirations", "TIME"),                 # services/expiration_notifier.py
        _svc("support", "SUPPORT"),                  # services/support_request_service.py
        _svc("subscription", "PREMIUM"),             # bot.py::_send_subscription_dm
        # Stripe is Moddy's payment provider: it issues the invoices and it is
        # its name — not Moddy's — that belongs on a receipt, so the invoice DM
        # attributes itself to Stripe rather than to the subscription service.
        _svc("stripe", "DOLLARS"),                   # services/invoice_notifier.py
    )
}


def get_service(service_id: Optional[str]) -> Optional[ServiceInfo]:
    """Look a service up, tolerating an unknown id (returns ``None``)."""
    if not service_id:
        return None
    return SERVICES.get(service_id)


# --------------------------------------------------------------------------- #
# Source
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class NotificationSource:
    """Where a notification comes from, and whether it can be reported.

    Build one with the constructors below rather than by hand — they encode
    the four legal shapes:

    ``official()``        no attribution at all (suspension, token alert)
    ``service()``         one button: the Moddy service
    ``guild()``           two buttons: the server, and the report flag
    ``service_guild()``   three buttons: the service, the server, the flag
    """

    kind: SourceKind
    service_id: Optional[str] = None
    guild_id: Optional[int] = None
    author: ContentAuthor = ContentAuthor.MODDY
    #: staff member who triggered the send, when a human did
    actor_id: Optional[int] = None

    # -- constructors ------------------------------------------------------ #

    @classmethod
    def official(cls, service_id: str = "moddy", *, actor_id: Optional[int] = None) -> "NotificationSource":
        return cls(kind=SourceKind.OFFICIAL, service_id=service_id,
                   author=ContentAuthor.MODDY, actor_id=actor_id)

    @classmethod
    def service(cls, service_id: str, *, author: ContentAuthor = ContentAuthor.MODDY,
                actor_id: Optional[int] = None) -> "NotificationSource":
        return cls(kind=SourceKind.SERVICE, service_id=service_id,
                   author=author, actor_id=actor_id)

    @classmethod
    def guild(cls, guild_id: int, *, author: ContentAuthor = ContentAuthor.GUILD,
              actor_id: Optional[int] = None) -> "NotificationSource":
        return cls(kind=SourceKind.GUILD, guild_id=guild_id,
                   author=author, actor_id=actor_id)

    @classmethod
    def service_guild(cls, service_id: str, guild_id: int, *,
                      author: ContentAuthor = ContentAuthor.MODDY,
                      actor_id: Optional[int] = None) -> "NotificationSource":
        return cls(kind=SourceKind.SERVICE_GUILD, service_id=service_id,
                   guild_id=guild_id, author=author, actor_id=actor_id)

    # -- derived properties ------------------------------------------------ #

    @property
    def is_official(self) -> bool:
        return self.kind is SourceKind.OFFICIAL

    @property
    def has_attribution(self) -> bool:
        """Whether the DM carries the attribution row at all."""
        return not self.is_official

    @property
    def base_reportable(self) -> bool:
        """Reportability from the source alone.

        Only words a human outside Moddy chose can be abusive, so only
        ``GUILD``/``STAFF``-authored content is reportable. The final answer
        also depends on the guild being an official Moddy server, which needs a
        database read — see ``NotificationService.is_reportable``.
        """
        if self.is_official:
            return False
        return self.author is ContentAuthor.GUILD

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind.value,
            "service_id": self.service_id,
            "guild_id": self.guild_id,
            "author": self.author.value,
            "actor_id": self.actor_id,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "NotificationSource":
        """Rebuild a source from a stored ``notifications`` row."""
        return cls(
            kind=SourceKind(row["kind"]),
            service_id=row.get("source_service"),
            guild_id=row.get("source_guild_id"),
            author=ContentAuthor(row.get("author") or "moddy"),
            actor_id=row.get("actor_id"),
        )


# --------------------------------------------------------------------------- #
# Content
# --------------------------------------------------------------------------- #

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")


def substitute(text: Optional[str], variables: Dict[str, Any]) -> str:
    """Resolve ``{placeholders}`` in ``text`` from ``variables``.

    Deliberately not ``str.format``: notification bodies carry arbitrary
    server-written text, and a stray ``{`` must never raise in the middle of a
    delivery. Unknown placeholders are left untouched so a mistake is visible
    rather than silently blank.
    """
    if not text:
        return ""
    if not variables:
        return text

    def _replace(match: "re.Match") -> str:
        key = match.group(1)
        if key in variables:
            value = variables[key]
            return "" if value is None else str(value)
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_replace, text)


@dataclass
class NotificationContent:
    """One notification's payload, in template form.

    The fields are deliberately generic so every platform knows what to do with
    them: ``title`` is the heading (a mail subject, a dashboard card title),
    ``body`` the main markdown, ``sections`` optional labelled blocks,
    ``links`` the call-to-action buttons, ``footer`` a discreet closing line.

    ``icon`` is a Moddy custom emoji (Discord only — the other platforms get
    the raw name via :meth:`to_dashboard`), and ``accent_color`` an ``0xRRGGBB``
    int used as the container accent / mail header colour.
    """

    title: str
    body: str
    icon: str = ""
    accent_color: Optional[int] = None
    sections: List[Dict[str, str]] = field(default_factory=list)
    links: List[Dict[str, str]] = field(default_factory=list)
    footer: Optional[str] = None
    #: stable id of the template this content came from, e.g. "welcome_dm.entry"
    template_id: Optional[str] = None

    # -- serialization ----------------------------------------------------- #

    def to_dict(self) -> Dict[str, Any]:
        """The stored template — placeholders unresolved."""
        return {
            "title": self.title,
            "body": self.body,
            "icon": self.icon,
            "accent_color": self.accent_color,
            "sections": self.sections,
            "links": self.links,
            "footer": self.footer,
            "template_id": self.template_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NotificationContent":
        return cls(
            title=data.get("title") or "",
            body=data.get("body") or "",
            icon=data.get("icon") or "",
            accent_color=data.get("accent_color"),
            sections=list(data.get("sections") or []),
            links=list(data.get("links") or []),
            footer=data.get("footer"),
            template_id=data.get("template_id"),
        )

    def template_hash(self) -> str:
        """SHA-256 of the canonical template.

        Two welcome DMs configured with the same text hash the same however
        many members receive them, which is what keeps the content table small:
        the row is written once and every notification points at it. The hash is
        taken **before** substitution, so ``Bienvenue {user}`` is one body, not
        one per member.
        """
        payload = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # -- rendering --------------------------------------------------------- #

    def render(self, variables: Optional[Dict[str, Any]] = None) -> "NotificationContent":
        """Return a copy with every placeholder resolved."""
        variables = variables or {}
        return NotificationContent(
            title=substitute(self.title, variables),
            body=substitute(self.body, variables),
            icon=self.icon,
            accent_color=self.accent_color,
            sections=[{
                "title": substitute(s.get("title"), variables),
                "body": substitute(s.get("body"), variables),
            } for s in self.sections],
            links=[{
                "label": substitute(l.get("label"), variables),
                "url": substitute(l.get("url"), variables),
            } for l in self.links],
            footer=substitute(self.footer, variables) if self.footer else None,
            template_id=self.template_id,
        )

    def to_email(self, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """The mail shape: a subject plus a plain-text body and the CTA links.

        Moddy does not send the mail itself — the backend does — so this is the
        contract handed over, not an SMTP call. Discord custom emojis are
        stripped: they render as literal ``<:name:id>`` outside Discord.
        """
        resolved = self.render(variables)
        blocks = [resolved.body]
        for section in resolved.sections:
            heading = section.get("title") or ""
            blocks.append(f"{heading}\n{section.get('body') or ''}".strip())
        if resolved.footer:
            blocks.append(resolved.footer)
        text = "\n\n".join(b for b in blocks if b)
        return {
            "subject": strip_custom_emojis(resolved.title),
            "text": strip_custom_emojis(text),
            "links": resolved.links,
        }

    def to_dashboard(self, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """The dashboard shape: the resolved payload, markdown kept as-is."""
        resolved = self.render(variables)
        data = resolved.to_dict()
        data["icon"] = self.icon
        return data


_CUSTOM_EMOJI_RE = re.compile(r"<a?:[a-zA-Z0-9_]+:\d+>")


def strip_custom_emojis(text: str) -> str:
    """Remove Discord custom emojis and collapse the space they leave."""
    return re.sub(r"[ \t]{2,}", " ", _CUSTOM_EMOJI_RE.sub("", text or "")).strip()
