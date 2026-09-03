"""Did a bump just succeed, and when is the next one due?

The whole problem is that a directory answers ``/bump`` from the same bot, with
the same command, whether the bump went through or the server is still on
cooldown. Watching for the command would therefore arm reminders off failures,
and a reminder that fires an hour early is worse than none — the channel gets
pinged, somebody runs the command, and the directory says no.

So detection is a funnel, cheapest test first:

1. the author is one of the seven known directories (a dict lookup — this is
   what runs on every message the bot sees, and what rejects all of them)
2. the command name matches, *when Discord sends one*
3. no cooldown wording anywhere in the message
4. at least one of that directory's success markers

Steps 3 and 4 are deliberately in that order: a failure marker vetoes, it never
competes with a success marker. A message saying both is a message we do not
understand, and staying quiet is the safe way to be wrong.

Nothing here touches Discord, the database or the network — which is what lets
``tests/test_bump_reminder.py`` replay the real captured payload of every
directory against the real code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Sequence, Set, Tuple

from bumpreminder.registry import BumpBot, bot_by_app_id

__all__ = [
    "BumpHit",
    "MIN_INTERVAL",
    "MAX_INTERVAL",
    "detect",
    "evaluate",
    "format_interval",
    "parse_interval",
]


# A server may shorten or lengthen the wait (some directories sell a faster
# cooldown), so the interval is configurable — but not to the point of turning
# the module into a spammer or into a reminder nobody will ever see.
MIN_INTERVAL = 5 * 60
MAX_INTERVAL = 24 * 3600

# A "next bump" time a directory states itself is only believed inside this
# window. It rejects DiscordL's footer stamp, which is the *current* time and
# would otherwise schedule a reminder three seconds out.
_MIN_LEAD = timedelta(minutes=5)
_MAX_LEAD = timedelta(hours=24)


# Cooldown wording, in the languages these directories actually answer in.
# Applied to text only — never to URLs, where a filename like ``bump-wait.png``
# would poison an otherwise good match. Every one of these was checked against
# the seven captured success payloads: none of them fires on a real bump.
_FAILURE_TEXT = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bwait\b",
        r"\battend(?:re|ez|s)\b",
        r"\bpatiente[rz]?\b",
        r"\balready\b",
        r"\bd[ée]j[àa]\b",
        r"\bcool ?down\b",
        r"\bslow down\b",
        r"\besper[ae]\b",
        r"\bwarte[nt]?\b",
        r"\baguard[ae]\b",
        r"\btry again\b",
        r"\br[ée]essay",
        r"\bnot? enough\b",
        r"\berror\b",
        r"\berreur\b",
        r"\b[ée]chec\b",
        r"\bfailed\b",
    )
)

# The harvested text is lowercased, so the style suffix must match either
# case — ``<t:123:R>`` arrives here as ``<t:123:r>``.
_TIMESTAMP_RE = re.compile(r"<t:(\d{1,15})(?::[tdfr])?>", re.IGNORECASE)


@dataclass(frozen=True)
class BumpHit:
    """A bump that went through."""

    bot: BumpBot
    bumper_id: Optional[int]
    """Who ran the command. ``None`` when Discord sent no interaction data."""

    due_at: datetime
    """When the next bump becomes possible, always timezone-aware UTC."""

    stated_by_bot: bool
    """``True`` when the directory announced ``due_at`` itself.

    A stated time beats the configured interval, because the directory is the
    authority on its own cooldown — and because a server that mis-set the
    interval then still gets a correct reminder.
    """


# --------------------------------------------------------------------------- #
# Reading a message
# --------------------------------------------------------------------------- #
def _walk_components(components: Sequence[Any], text: List[str],
                     media: List[str], custom_ids: List[str]) -> None:
    """Collect every scrap of text, media URL and custom_id, at any depth.

    Components V2 nests: a Container holds Sections and MediaGalleries, a
    Section holds an accessory. Four of the seven directories answer this way,
    and three of those put their only usable marker inside a nested node — so
    walking the whole tree is not thoroughness, it is the feature.
    """
    for component in components or ():
        content = getattr(component, "content", None)
        if isinstance(content, str) and content:
            text.append(content)

        label = getattr(component, "label", None)
        if isinstance(label, str) and label:
            text.append(label)

        custom_id = getattr(component, "custom_id", None)
        if isinstance(custom_id, str) and custom_id:
            custom_ids.append(custom_id)

        for attribute in ("url", "proxy_url"):
            value = getattr(component, attribute, None)
            if isinstance(value, str) and value:
                media.append(value)

        # A MediaGallery holds items, each wrapping its own media object.
        for item in getattr(component, "items", None) or ():
            item_media = getattr(item, "media", None) or item
            for attribute in ("url", "proxy_url"):
                value = getattr(item_media, attribute, None)
                if isinstance(value, str) and value:
                    media.append(value)

        for attribute in ("media", "accessory", "thumbnail"):
            nested = getattr(component, attribute, None)
            if nested is not None and not isinstance(nested, (str, bytes)):
                value = getattr(nested, "url", None)
                if isinstance(value, str) and value:
                    media.append(value)

        children = getattr(component, "children", None) or getattr(component, "components", None)
        if children:
            _walk_components(children, text, media, custom_ids)


def _harvest(message: Any) -> Tuple[str, str, Set[str]]:
    """Flatten a message into ``(text, media_urls, custom_ids)``, lowercased.

    Text and URLs come back as two single blobs rather than lists: every caller
    only ever asks "does this appear anywhere", and one ``in`` over one string
    beats a loop over a dozen.
    """
    text: List[str] = []
    media: List[str] = []
    custom_ids: List[str] = []

    if getattr(message, "content", None):
        text.append(message.content)

    for embed in getattr(message, "embeds", None) or ():
        for value in (getattr(embed, "title", None), getattr(embed, "description", None)):
            if value:
                text.append(str(value))
        author = getattr(embed, "author", None)
        if author is not None and getattr(author, "name", None):
            text.append(str(author.name))
        footer = getattr(embed, "footer", None)
        if footer is not None and getattr(footer, "text", None):
            text.append(str(footer.text))
        for embed_field in getattr(embed, "fields", None) or ():
            for value in (getattr(embed_field, "name", None), getattr(embed_field, "value", None)):
                if value:
                    text.append(str(value))
        for attribute in ("image", "thumbnail"):
            asset = getattr(embed, attribute, None)
            if asset is not None and getattr(asset, "url", None):
                media.append(str(asset.url))

    _walk_components(getattr(message, "components", None) or (), text, media, custom_ids)

    for attachment in getattr(message, "attachments", None) or ():
        for attribute in ("url", "proxy_url", "filename"):
            value = getattr(attachment, attribute, None)
            if value:
                media.append(str(value))

    return (
        "\n".join(text).lower(),
        "\n".join(media).lower(),
        {custom_id.lower() for custom_id in custom_ids},
    )


def _command_name(message: Any) -> Optional[str]:
    """The slash command that produced this message, if Discord said so.

    ``interaction`` is deprecated in favour of ``interaction_metadata``, but it
    is the only one of the two carrying the command *name* — and Discord still
    populates it. So we read the name from the deprecated field and take the
    user from whichever is present.
    """
    interaction = getattr(message, "interaction", None)
    name = getattr(interaction, "name", None)
    return name.lower() if isinstance(name, str) else None


def _bumper_id(message: Any, custom_ids: Set[str], spec: BumpBot) -> Optional[int]:
    """Who ran the command.

    Prefers ``interaction_metadata`` (current), falls back to ``interaction``
    (deprecated), and finally to a marker custom_id that embeds the id —
    French.gg suffixes its reminder button with the bumper's user id.
    """
    for attribute in ("interaction_metadata", "interaction"):
        source = getattr(message, attribute, None)
        user = getattr(source, "user", None)
        user_id = getattr(user, "id", None)
        if isinstance(user_id, int):
            return user_id

    for marker in spec.success_custom_id:
        for custom_id in custom_ids:
            if custom_id.startswith(marker):
                suffix = custom_id[len(marker):]
                if suffix.isdigit():
                    return int(suffix)
    return None


# --------------------------------------------------------------------------- #
# Next-bump time
# --------------------------------------------------------------------------- #
def _fresh(candidate: Optional[datetime], now: datetime) -> Optional[datetime]:
    """Keep a stated time only if it plausibly is the *next* bump.

    A directory that stamps its message with "now" (DiscordL's footer) or with
    a date far out is not telling us about the cooldown, so we fall back to the
    configured interval rather than trust it.
    """
    if candidate is None:
        return None
    if candidate.tzinfo is None:
        candidate = candidate.replace(tzinfo=timezone.utc)
    lead = candidate - now
    return candidate if _MIN_LEAD < lead <= _MAX_LEAD else None


def _stated_due(message: Any, spec: BumpBot, text: str, now: datetime) -> Optional[datetime]:
    if spec.next_due == "embed_timestamp":
        for embed in getattr(message, "embeds", None) or ():
            stamp = _fresh(getattr(embed, "timestamp", None), now)
            if stamp is not None:
                return stamp
        return None

    if spec.next_due == "relative":
        candidates = []
        for raw in _TIMESTAMP_RE.findall(text):
            try:
                stamp = datetime.fromtimestamp(int(raw), tz=timezone.utc)
            except (ValueError, OverflowError, OSError):
                continue
            stamp = _fresh(stamp, now)
            if stamp is not None:
                candidates.append(stamp)
        return min(candidates) if candidates else None

    return None


# --------------------------------------------------------------------------- #
# The funnel
# --------------------------------------------------------------------------- #
def _matches(patterns, haystack: str) -> bool:
    return any(pattern.search(haystack) for pattern in patterns)


def detect(message: Any, interval: int, *, now: Optional[datetime] = None) -> Optional[BumpHit]:
    """Return the bump this message reports, or ``None``.

    ``interval`` is the server's configured cooldown in seconds, used unless the
    directory states its own next-bump time. ``now`` is injectable so the tests
    can replay payloads captured months ago.
    """
    author = getattr(message, "author", None)
    if author is None or not getattr(author, "bot", False):
        return None

    spec = bot_by_app_id(author.id)
    if spec is None:
        return None

    return evaluate(message, spec, interval, now=now)


def evaluate(message: Any, spec: BumpBot, interval: int, *,
             now: Optional[datetime] = None) -> Optional[BumpHit]:
    """:func:`detect` with the directory already decided.

    Split out so the tests can push every directory's payload through every
    *other* directory's markers — the check that actually proves two listings
    can never be confused for one another.
    """
    # A command name, when Discord sends one, must be a command that can bump.
    # It is never sufficient on its own — a cooldown reply carries it too.
    command = _command_name(message)
    if command is not None and command not in spec.command_names:
        return None

    text, media, custom_ids = _harvest(message)

    # The shared blocklist exists to recognise a *visible* refusal. A directory
    # that refuses privately can never send one, so applying it there could only
    # ever cost a real bump — some translation of the success message tripping
    # on a word it has no business owning. Its own markers still veto.
    if not spec.refusal_is_ephemeral and _matches(_FAILURE_TEXT, text):
        return None
    if _matches(spec.failure_text, text):
        return None
    if any(marker in media for marker in spec.failure_media):
        return None

    # "Visible means it worked" is only safe for a message we know is a bump
    # reply. Without the command name it could be anything the directory posts,
    # so that shortcut is withheld and the markers have to earn it.
    succeeded = (
        (spec.refusal_is_ephemeral and command is not None)
        or _matches(spec.success_text, text)
        or any(marker in media for marker in spec.success_media)
        or any(
            custom_id.startswith(marker)
            for marker in spec.success_custom_id
            for custom_id in custom_ids
        )
    )
    if not succeeded:
        return None

    now = now or datetime.now(timezone.utc)
    stated = _stated_due(message, spec, text, now)
    return BumpHit(
        bot=spec,
        bumper_id=_bumper_id(message, custom_ids, spec),
        due_at=stated or (now + timedelta(seconds=interval)),
        stated_by_bot=stated is not None,
    )


# --------------------------------------------------------------------------- #
# Intervals, as a human types them
# --------------------------------------------------------------------------- #
_INTERVAL_RE = re.compile(
    r"^(?:(?P<hours>\d{1,3})\s*h\s*(?P<hmins>\d{1,2})?|(?P<mins>\d{1,5})\s*(?:m(?:in(?:ute)?s?)?)?)$",
    re.IGNORECASE,
)


def parse_interval(raw: str) -> Optional[int]:
    """Read ``2h``, ``2h30``, ``90m`` or a bare ``120`` (minutes) into seconds.

    Returns ``None`` for anything unreadable **or** out of range, so a caller
    has one thing to check. The bounds are part of the contract, not a detail:
    an interval of zero would turn the reminder into a loop.
    """
    if not raw:
        return None
    match = _INTERVAL_RE.match(raw.strip().replace(",", ".").replace(" ", ""))
    if not match:
        return None

    if match.group("hours") is not None:
        seconds = int(match.group("hours")) * 3600 + int(match.group("hmins") or 0) * 60
    else:
        seconds = int(match.group("mins")) * 60

    return seconds if MIN_INTERVAL <= seconds <= MAX_INTERVAL else None


def format_interval(seconds: int) -> str:
    """Render seconds the way :func:`parse_interval` reads them back."""
    hours, minutes = divmod(max(0, int(seconds)) // 60, 60)
    if hours and minutes:
        return f"{hours}h{minutes:02d}"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"
