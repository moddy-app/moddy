"""The directories Moddy knows how to watch.

One :class:`BumpBot` per directory. The tuple order is the order every menu
shows them in, biggest audience first, so the directory most servers actually
use is the first thing offered.

Adding a directory is meant to be a one-entry change here plus a fixture in
``tests/test_bump_reminder.py`` — nothing else in the module knows the list.

**On the markers.** A directory answers ``/bump`` with the *same* command, from
the *same* application, whether the bump went through or the server is still on
cooldown. Matching the command alone would therefore schedule reminders off
failed bumps. So each entry carries what a **success** looks like, and the
detector additionally refuses anything carrying cooldown wording (that shared
blocklist lives in :mod:`bumpreminder.detect`).

Markers come in three flavours because the directories are not built alike:

``success_text``
    Regexes over every scrap of text in the message. Written multilingual —
    these bots answer in the reader's language, not the server's.
``success_media``
    Substrings of an image URL. Several directories name their assets after the
    outcome (``boost-success.png``), which is a stronger signal than any
    sentence and survives every translation.
``success_custom_id``
    Substrings of a button's ``custom_id``. French.gg only offers its own
    "remind me" button on a successful bump, which makes that button the tell.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Optional, Pattern, Tuple

__all__ = ["BumpBot", "BUMP_BOTS", "bot_by_app_id", "bot_by_key", "is_bump_bot"]


HOUR = 3600


@dataclass(frozen=True)
class BumpBot:
    """One server directory and how to recognise its successful bump."""

    key: str
    """Stable identifier stored in config and in the database. Never renamed."""

    app_id: int
    """The bot's application id — also its message author id."""

    name: str
    """Display name, shown as-is (a brand, so never translated)."""

    emoji: str
    """Custom emoji for menus and cards."""

    command: str
    """The clickable command mention put in the reminder card."""

    command_names: frozenset
    """Command names that can produce a bump. Checked when Discord sends it."""

    default_interval: int
    """The directory's advertised cooldown, in seconds. Servers may override it."""

    success_text: Tuple[Pattern, ...] = ()
    success_media: Tuple[str, ...] = ()
    success_custom_id: Tuple[str, ...] = ()

    failure_text: Tuple[Pattern, ...] = ()
    failure_media: Tuple[str, ...] = ()

    refusal_is_ephemeral: bool = False
    """The directory refuses a bump *privately*, so anything visible is a success.

    DISBOARD answers a cooldown with an ephemeral message, which the gateway
    never delivers to a bot. Every ``/bump`` reply Moddy can actually see from
    it therefore went through — which makes the detection language-proof
    instead of hostage to a list of translated phrases.

    The shortcut applies only to a message Discord tagged with a command name
    from :attr:`command_names` — without that, the reply could be anything the
    directory posts, so the ordinary markers have to carry it instead.

    Setting this on another directory needs the same evidence: a *visible*
    refusal would otherwise be read as a success and arm a reminder an hour
    early. The per-directory failure markers below still veto, as cheap
    insurance against the day a directory changes its mind.
    """

    next_due: str = ""
    """Where the message states the next bump time, when it does at all.

    ``"embed_timestamp"`` reads the embed's ``timestamp`` field, ``"relative"``
    picks the earliest future ``<t:…>`` out of the text. Empty means the
    directory says nothing and the configured interval is authoritative.
    """

    @property
    def command_hint(self) -> str:
        """``/bump`` — the plain name, for places a command mention cannot render.

        Select descriptions and modal labels are plain text: a ``</bump:id>``
        mention would show up there as its raw source.
        """
        name = self.command.split(":", 1)[0].lstrip("<")
        return name or "/bump"


def _rx(*patterns: str) -> Tuple[Pattern, ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


# --------------------------------------------------------------------------- #
# The directories, largest audience first
# --------------------------------------------------------------------------- #
BUMP_BOTS: Tuple[BumpBot, ...] = (
    # ---------------------------------------------------------------- DISBOARD
    # The reference listing, and the only one here that is global rather than
    # French-speaking — so it answers in a dozen languages Moddy has no phrase
    # list for. It also refuses privately (see ``refusal_is_ephemeral``), which
    # settles the problem outright: whatever language a visible reply is in, it
    # is a success. The markers below stay as a second, cheaper path and as the
    # thing the cross-directory test exercises.
    BumpBot(
        key="disboard",
        app_id=302050872383242240,
        name="DISBOARD",
        emoji="<:disboard:1545025221612802101>",
        command="</bump:947088344167366698>",
        command_names=frozenset({"bump"}),
        default_interval=2 * HOUR,
        success_text=_rx(
            r"bump\s*(?:effectu|done|hecho|erledigt|feito|conclu|fatto|selesai)",
            r"表示順をアップ",
            r"bumpeado",
        ),
        success_media=("bot-command-image-bump",),
        failure_media=("bot-command-image-notification",),
        refusal_is_ephemeral=True,
    ),
    # ----------------------------------------------------------- DSMonitoring
    # Calls a bump a "like". Its embed timestamp is the *next* like, four hours
    # out — authoritative, so we prefer it over the configured interval.
    BumpBot(
        key="dsmonitoring",
        app_id=575776004233232386,
        name="DSMonitoring",
        emoji="<:DSMonitoring:1545027259323256842>",
        command="</bump:1343606491386609716>",
        command_names=frozenset({"bump", "like"}),
        default_interval=4 * HOUR,
        success_text=_rx(
            r"successfully\s+liked",
            r"liked\s+the\s+server",
            r"aim[ée]\s+le\s+serveur",
            r"(?:vous\s+avez\s+)?lik[ée]\s+le\s+serveur",
            r"faster\s+than",
            r"plus\s+rapide\s+que",
        ),
        next_due="embed_timestamp",
    ),
    # --------------------------------------------------------------- D-INVITES
    # The hard one: a successful bump is a bare image with a "view the server"
    # button and not one word of text, so the asset filename is the only signal
    # a success carries — see the note in docs/BUMP_REMINDER.md.
    #
    # Its refusal, by contrast, is explicit: `bump-error.png` plus "Tu pourras
    # bump à nouveau <t:…>". Both markers below are taken from a captured
    # refusal, not guessed — `/bump.png` and `bump-error.png` are distinct
    # enough that the success marker cannot match a failure.
    BumpBot(
        key="dinvites",
        app_id=678211574183362571,
        name="D-INVITES",
        emoji="<:dinvites:1545025725948624956>",
        command="</bump:1099048758228037742>",
        command_names=frozenset({"bump"}),
        default_interval=2 * HOUR,
        success_media=("/bump.png", "/bump.gif", "/bump.jpg", "/bump.webp"),
        failure_text=_rx(
            r"pourras\s+bump",
            r"able\s+to\s+bump\s+again",
        ),
        failure_media=("bump-error",),
    ),
    # ---------------------------------------------------------------- DiscordL
    # Components V2. Its banner URL carries the bump path, and its footer
    # carries a ``<t:…>`` that is *now* rather than the next bump — which is
    # exactly what the freshness window in the detector is there to reject.
    BumpBot(
        key="dl",
        app_id=528557940811104258,
        name="DiscordL",
        emoji="<:DiscordDL:1545024517066465370>",
        command="</bump:1011963835634159667>",
        command_names=frozenset({"bump"}),
        default_interval=1 * HOUR,
        # "a été bump **par**" — the trailing preposition is what keeps this
        # apart from French.gg's "a été bump**é**", which is otherwise the
        # same sentence in the same language.
        success_text=_rx(
            r"r[ée]sultat\s+du\s+bump",
            r"a\s+[ée]t[ée]\s+bump\s+par",
            r"has\s+been\s+bumped\s+by",
            r"fue\s+bumpeado\s+por",
        ),
        success_media=("/v2/bump/",),
    ),
    # ------------------------------------------------------------------- Beemp
    BumpBot(
        key="beemp",
        app_id=1293636927337136269,
        name="Beemp",
        emoji="<:beemp:1545026898520969216>",
        command="</bump:1470530261170257972>",
        command_names=frozenset({"bump", "beemp"}),
        default_interval=1 * HOUR,
        success_text=_rx(
            r"beemp\s+(?:done|effectu|r[ée]ussi|realizado)",
            r"beemp[^.\n]{0,40}success",
            r"beemp[^.\n]{0,40}succ[èe]s",
        ),
    ),
    # -------------------------------------------------------------- DiscordTop
    # Calls it a "boost" — hence the separate command name. Names its asset
    # after the outcome and states the next boost as a relative timestamp.
    BumpBot(
        key="dtop",
        app_id=1071460654839517184,
        name="DiscordTop",
        emoji="<:DTOP:1545026416331198515>",
        command="</boost:1364194690290683965>",
        command_names=frozenset({"boost", "bump"}),
        default_interval=1 * HOUR,
        success_text=_rx(
            r"boost\s+(?:envoy[ée]|sent|enviado|gesendet)",
            r"propuls[ée]",
            r"pushed\s+to\s+the\s+front",
        ),
        success_media=("boost-success",),
        failure_media=("boost-error", "boost-cooldown", "boost-failed"),
        next_due="relative",
    ),
    # -------------------------------------------------------------- French.gg
    # Offers its own "remind me in 2h" button, and only on a success — so the
    # button's custom_id is the strongest marker available here. Its suffix is
    # the bumper's user id, which the detector uses when Discord omits the
    # interaction metadata.
    BumpBot(
        key="frenchgg",
        app_id=1313443824483307531,
        name="French.gg",
        emoji="<:frenchgg:1545026009235988490>",
        command="</bump:1319697709363494946>",
        command_names=frozenset({"bump"}),
        default_interval=2 * HOUR,
        success_text=_rx(
            r"nouveau\s+bump",
            r"new\s+bump",
            r"a\s+[ée]t[ée]\s+bump[ée]",
        ),
        success_custom_id=("buttons-custom-reminder-",),
    ),
)


_BY_APP_ID: Dict[int, BumpBot] = {spec.app_id: spec for spec in BUMP_BOTS}
_BY_KEY: Dict[str, BumpBot] = {spec.key: spec for spec in BUMP_BOTS}


def bot_by_app_id(app_id: int) -> Optional[BumpBot]:
    """The directory a message author belongs to, or ``None``.

    This is the hot path: it runs for every message the bot sees, so it must
    stay a single dict lookup over seven entries.
    """
    return _BY_APP_ID.get(app_id)


def bot_by_key(key: str) -> Optional[BumpBot]:
    """The directory a stored config entry refers to, or ``None`` if retired."""
    return _BY_KEY.get(key)


def is_bump_bot(app_id: int) -> bool:
    return app_id in _BY_APP_ID
