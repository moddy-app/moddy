"""
Tool cards — the one look shared by every utility tool (/define, /hash, /whois,
/image, /music...).

Every tool answers with the same anatomy, so a user who learnt one tool can read
all of them at a glance:

    ### <icon> Tool title                    <- heading (icon may be empty)
    <the main result, first and copyable>     <- code block / big value
    **Label** · `value`                        <- detail lines
    ─────────
    -# Source: X · requested by @user          <- one dim footer line
    [ action buttons ]                         <- outside the container

Use :class:`ToolCard` to build it, :func:`loading_card` while working (anything
that may take more than a second), and :class:`ToolError` +
:func:`tool_error_card` for expected failures (the error text lives under
``tools.<tool>.errors.<code>`` with a shared fallback in ``tools.common.errors``).

State that does not fit in a ``custom_id`` (a regex + its test text, a list of
results) goes through :func:`save_state` / :func:`load_state`: Redis with a TTL,
an in-process LRU when Redis is down. The ``custom_id`` only carries the short
token. An expired token answers with :func:`expired_card` ("run the command
again") — see docs/guides/PERSISTENT_VIEWS.md.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from collections import OrderedDict
from typing import Any, Iterable, List, Optional, Sequence, Tuple, Union

import discord
from discord import ui

from config import COLORS
from utils.automod_render import make_text_file
from utils.emojis import LOADING
from utils.i18n import i18n, t

logger = logging.getLogger("moddy.tools")

NO_MENTIONS = discord.AllowedMentions.none()

# Semantic accents: information vs verdicts. Green is only for a good verdict.
ACCENT_INFO = COLORS["primary"]
ACCENT_SUCCESS = COLORS["success"]
ACCENT_WARNING = COLORS["warning"]
ACCENT_ERROR = COLORS["error"]
ACCENT_NEUTRAL = COLORS["neutral"]

# Components V2 caps the visible text of a whole message at 4000 characters.
# Cards keep a margin for the heading, details and footer.
MESSAGE_TEXT_BUDGET = 4000
DEFAULT_BODY_BUDGET = 3000

# --------------------------------------------------------------------------- #
# Small formatting helpers
# --------------------------------------------------------------------------- #


def heading(icon: Optional[str], title: str) -> str:
    """``### <icon> Title`` — an empty icon (artwork not uploaded yet) is dropped."""
    return f"### {icon} {title}" if icon else f"### {title}"


def with_icon(icon: Optional[str], text: str) -> str:
    """``<icon> text`` or just ``text`` when the icon is empty."""
    return f"{icon} {text}" if icon else text


def button_emoji(icon: Optional[str]) -> Optional[discord.PartialEmoji]:
    """``PartialEmoji`` for a button, or ``None`` when the icon is empty."""
    if not icon:
        return None
    try:
        return discord.PartialEmoji.from_str(icon)
    except Exception:  # noqa: BLE001 — a malformed token must never break a card
        return None


def code_block(text: str, lang: str = "") -> str:
    """Fenced code block; a literal ``` inside the text would close it early."""
    safe = (text or "").replace("```", "`​``")
    return f"```{lang}\n{safe}\n```"


def inline(value: Any) -> str:
    """Dynamic value in backticks (CLAUDE.md rule #6), backticks neutralised."""
    text = str(value).replace("`", "ˋ")
    return f"`{text}`" if text else "`—`"


def truncate(text: str, limit: int, *, ellipsis: str = "…") -> Tuple[str, bool]:
    """Cut ``text`` to ``limit`` characters. Returns ``(text, was_truncated)``."""
    text = text or ""
    if len(text) <= limit:
        return text, False
    return text[: max(0, limit - len(ellipsis))].rstrip() + ellipsis, True


def escape_markdown(text: str) -> str:
    """Neutralise Discord markdown in user text shown outside a code block."""
    return discord.utils.escape_markdown(discord.utils.escape_mentions(text or ""))


def format_bytes(size: Union[int, float], locale: str = "en-US") -> str:
    """Human file size (1 decimal): ``12.4 KB`` / ``3.1 MB`` (decimal comma in fr/de/es/pt)."""
    units = ("B", "KB", "MB", "GB")
    value = float(size or 0)
    unit = units[0]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            break
        value /= 1024
    text = f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
    if not locale.startswith("en"):
        text = text.replace(".", ",")
    return text


def format_number(value: Union[int, float], locale: str = "en-US") -> str:
    """Thousands separator per locale: ``12,345`` (en) / ``12 345`` (fr) / ``12.345`` (de/es/pt)."""
    if isinstance(value, float) and not value.is_integer():
        text = f"{value:,.2f}"
    else:
        text = f"{int(value):,}"
    if locale.startswith("en"):
        return text
    if locale.startswith("fr"):
        return text.replace(",", " ").replace(".", ",")
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def detail(label: str, value: Any, *, raw: bool = False) -> str:
    """One detail line: ``**Label** · `value``` (``raw=True`` keeps value as is)."""
    return f"**{label}** · {value if raw else inline(value)}"


def discord_timestamp(epoch: Union[int, float], style: str = "f") -> str:
    return f"<t:{int(epoch)}:{style}>"


# --------------------------------------------------------------------------- #
# The card builder
# --------------------------------------------------------------------------- #


class ToolCard:
    """Fluent builder for the standard tool card.

    >>> card = ToolCard(icon=TAG, title=t("tools.hash.title", locale=loc))
    >>> card.code(digest).details([(t(...), "SHA-256")]).footer(source="...")
    >>> view = card.build(rows=[my_action_row])
    """

    def __init__(self, *, title: str, icon: Optional[str] = None,
                 accent: int = ACCENT_INFO, subtitle: Optional[str] = None):
        self._container = ui.Container(accent_colour=discord.Colour(accent))
        top = heading(icon, title)
        if subtitle:
            top += f"\n{subtitle}"
        self._container.add_item(ui.TextDisplay(top))
        self._extra: List[ui.Item] = []

    # -- content ----------------------------------------------------------- #
    def text(self, content: str) -> "ToolCard":
        if content:
            self._container.add_item(ui.TextDisplay(content))
        return self

    def code(self, content: str, lang: str = "") -> "ToolCard":
        return self.text(code_block(content, lang))

    def details(self, lines: Iterable[Union[str, Tuple[str, Any]]]) -> "ToolCard":
        """Detail lines; tuples become ``**label** · `value```, strings pass through."""
        rendered = []
        for line in lines:
            if line is None:
                continue
            if isinstance(line, tuple):
                rendered.append(detail(line[0], line[1]))
            else:
                rendered.append(str(line))
        if rendered:
            self._container.add_item(ui.TextDisplay("\n".join(rendered)))
        return self

    def section(self, title: str, body: str, *, icon: Optional[str] = None) -> "ToolCard":
        """A bold sub-heading followed by its body, in one text block."""
        head = f"**{with_icon(icon, title)}**"
        return self.text(f"{head}\n{body}" if body else head)

    def separator(self, *, visible: bool = True) -> "ToolCard":
        self._container.add_item(ui.Separator(visible=visible, spacing=discord.SeparatorSpacing.small))
        return self

    def media(self, *urls: str) -> "ToolCard":
        """Image(s) in a gallery — ``attachment://name.png`` or an https URL."""
        urls = [u for u in urls if u]
        if urls:
            gallery = ui.MediaGallery()
            for url in urls[:10]:
                gallery.add_item(media=url)
            self._container.add_item(gallery)
        return self

    def file(self, filename: str) -> "ToolCard":
        """Show an attached file (send it with ``attachments=[discord.File(..., filename)]``)."""
        self._container.add_item(ui.File(f"attachment://{filename}"))
        return self

    def hint(self, content: str) -> "ToolCard":
        """A dim ``-#`` line inside the body (tips, warnings about limits)."""
        return self.text(f"-# {content}") if content else self

    def footer(self, *parts: Optional[str], separator: bool = True) -> "ToolCard":
        """The dim last line: ``-# part · part · part`` (empty parts are skipped)."""
        parts = [p for p in parts if p]
        if parts:
            if separator:
                self.separator()
            self._container.add_item(ui.TextDisplay("-# " + " · ".join(parts)))
        return self

    def add(self, item: ui.Item) -> "ToolCard":
        """Escape hatch: any component inside the container (ActionRow, Section...)."""
        self._container.add_item(item)
        return self

    def below(self, item: ui.Item) -> "ToolCard":
        """A component under the container (usually an ``ActionRow`` of actions)."""
        self._extra.append(item)
        return self

    # -- output ------------------------------------------------------------ #
    @property
    def container(self) -> ui.Container:
        return self._container

    def build(self, *, view: Optional[ui.LayoutView] = None,
              rows: Sequence[ui.Item] = ()) -> ui.LayoutView:
        """Return a ``LayoutView`` holding the card + action rows.

        Pass ``view`` to fill an existing ``BaseView`` subclass (persistent
        views); otherwise a plain non-interactive ``LayoutView`` is created.
        """
        target = view if view is not None else ui.LayoutView(timeout=None)
        target.add_item(self._container)
        for item in list(self._extra) + list(rows):
            target.add_item(item)
        return target


def requested_by(locale: str, user_id: Optional[int]) -> Optional[str]:
    """Footer part ``requested by @user`` — omit it on ephemeral answers."""
    if not user_id:
        return None
    return t("tools.common.requested_by", locale=locale, user=f"<@{user_id}>")


SourceName = Union[str, Tuple[Optional[str], str]]


def source_line(locale: str, *names: SourceName, verb: str = "provided_by") -> str:
    """Footer part saying where the information comes from, as a short phrase
    like /translate's ``Translated by **DeepL**`` — never ``Source: X``.

    ``verb`` picks the phrase under ``tools.common.by.<verb>`` (``provided_by``,
    ``translated_by``, ``checked_by``, ``converted_by``, ``summarized_by``,
    ``found_by``, ``scanned_by``...). Each name is either
    a plain string or ``(logo, name)``: the logo sits right next to the name it
    belongs to — ``Translated by <logo> **DeepL**`` — and is dropped while its
    artwork is not uploaded. Several names read as a list: ``A, B & C``.
    """
    parts = []
    for name in names:
        if not name:
            continue
        logo, label = name if isinstance(name, tuple) else (None, name)
        parts.append(with_icon(logo, f"**{label}**"))
    joined = " & ".join(parts) if len(parts) < 3 else f"{', '.join(parts[:-1])} & {parts[-1]}"
    return t(f"tools.common.by.{verb}", locale=locale, source=joined)


def link_button(label: str, url: str, *, icon: Optional[str] = None) -> ui.Button:
    return ui.Button(label=label[:80], url=url, emoji=button_emoji(icon),
                     style=discord.ButtonStyle.link)


def link_row(*buttons: ui.Button) -> Optional[ui.ActionRow]:
    buttons = [b for b in buttons if b is not None]
    if not buttons:
        return None
    row = ui.ActionRow()
    for b in buttons[:5]:
        row.add_item(b)
    return row


# --------------------------------------------------------------------------- #
# Loading / error / expired cards
# --------------------------------------------------------------------------- #


def loading_card(locale: str, message: Optional[str] = None, *,
                 progress: Optional[Tuple[int, int]] = None) -> ui.LayoutView:
    """Spinner card shown while a tool works (edited in place once done).

    ``progress=(done, total)`` adds a 12-cell bar, for long jobs (/username).
    """
    text = message or t("tools.common.loading", locale=locale)
    body = f"{LOADING} **{text}**"
    if progress:
        done, total = progress
        body += f"\n{progress_bar(done, total)} `{done}/{total}`"
    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=discord.Colour(ACCENT_INFO))
    container.add_item(ui.TextDisplay(body))
    view.add_item(container)
    return view


def progress_bar(done: int, total: int, cells: int = 12) -> str:
    total = max(1, total)
    filled = max(0, min(cells, round(cells * done / total)))
    return "█" * filled + "░" * (cells - filled)


class ToolError(Exception):
    """An expected tool failure: ``code`` maps to ``tools.<tool>.errors.<code>``.

    ``params`` fill the i18n placeholders (wrapped in backticks automatically).
    ``severity`` picks the card accent (``error`` / ``warning`` / ``info``).
    """

    def __init__(self, code: str, *, tool: str = "common", severity: str = "error", **params: Any):
        super().__init__(f"{tool}:{code}")
        self.tool = tool
        self.code = code
        self.severity = severity
        self.params = params


def tool_error_card(error_or_code: Union[ToolError, str], locale: str, *, tool: str = "common",
                    buttons: Optional[Iterable[ui.Button]] = None, **params: Any) -> ui.LayoutView:
    """Card for an expected failure, through the shared error look (utils.errors).

    Looks up ``tools.<tool>.errors.<code>`` then ``tools.common.errors.<code>``,
    then the generic ``tools.common.errors.failed``. The title is
    ``tools.<tool>.error_title`` when it exists, else ``tools.common.error_title``.
    """
    from utils.errors import build_error_card

    severity = "error"
    if isinstance(error_or_code, ToolError):
        tool = error_or_code.tool
        code = error_or_code.code
        severity = error_or_code.severity
        params = {**error_or_code.params, **params}
    else:
        code = error_or_code
    formatted = {k: inline(v) for k, v in params.items()}

    description = None
    for key in (f"tools.{tool}.errors.{code}", f"tools.common.errors.{code}"):
        text = i18n.get(key, locale=locale, **formatted)
        if not text.startswith("["):
            description = text
            break
    if description is None:
        description = t("tools.common.errors.failed", locale=locale)
    title = i18n.get(f"tools.{tool}.error_title", locale=locale)
    if title.startswith("["):
        title = t("tools.common.error_title", locale=locale)
    return build_error_card(title, description, severity=severity, buttons=buttons)


def expired_card(locale: str) -> ui.LayoutView:
    """Answer to a click whose stored state has expired: run the command again."""
    return tool_error_card("expired", locale, tool="common")


# --------------------------------------------------------------------------- #
# Long outputs
# --------------------------------------------------------------------------- #


def fit_or_attach(text: str, *, limit: int, filename: str,
                  lang: str = "") -> Tuple[str, Optional[discord.File]]:
    """Code block of ``text`` if it fits, else a truncated preview + the full file.

    Returns ``(markdown, file_or_None)``; when a file is returned, also call
    ``card.file(filename)`` and pass the file in ``attachments=``.
    """
    preview, cut = truncate(text, limit)
    block = code_block(preview, lang)
    if not cut:
        return block, None
    return block, make_text_file(text, filename)


# --------------------------------------------------------------------------- #
# Short-lived state for buttons (Redis TTL, LRU fallback)
# --------------------------------------------------------------------------- #

STATE_TTL = 24 * 3600
_STATE_PREFIX = "moddy:tools:state"
_LOCAL_STATE: "OrderedDict[str, Tuple[float, str]]" = OrderedDict()
_LOCAL_STATE_MAX = 512


def new_token() -> str:
    """10-char URL-safe token: short enough to sit in any ``custom_id``."""
    return secrets.token_urlsafe(8)[:10].replace("-", "x").replace("_", "y")


async def save_state(bot, kind: str, payload: Any, *, ttl: int = STATE_TTL,
                     token: Optional[str] = None) -> str:
    """Store ``payload`` (JSON-serialisable) and return its token."""
    token = token or new_token()
    key = f"{_STATE_PREFIX}:{kind}:{token}"
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    redis = getattr(bot, "redis", None)
    if redis is not None:
        try:
            await redis.set(key, data, ex=ttl)
            return token
        except Exception as exc:  # noqa: BLE001 — fall back to process memory
            logger.debug("tool state: redis set failed: %s", exc)
    _LOCAL_STATE[key] = (time.monotonic() + ttl, data)
    _LOCAL_STATE.move_to_end(key)
    while len(_LOCAL_STATE) > _LOCAL_STATE_MAX:
        _LOCAL_STATE.popitem(last=False)
    return token


async def load_state(bot, kind: str, token: str) -> Optional[Any]:
    """Payload stored by :func:`save_state`, or ``None`` once expired."""
    key = f"{_STATE_PREFIX}:{kind}:{token}"
    redis = getattr(bot, "redis", None)
    if redis is not None:
        try:
            data = await redis.get(key)
            if data is not None:
                return json.loads(data)
        except Exception as exc:  # noqa: BLE001
            logger.debug("tool state: redis get failed: %s", exc)
    entry = _LOCAL_STATE.get(key)
    if entry is None:
        return None
    expires, data = entry
    if expires < time.monotonic():
        _LOCAL_STATE.pop(key, None)
        return None
    return json.loads(data)


# --------------------------------------------------------------------------- #
# DynamicItem callbacks
# --------------------------------------------------------------------------- #


def guarded(callback):
    """Route a ``DynamicItem`` callback error to the central handler.

    A DynamicItem has no live ``BaseView.on_error``; a :class:`ToolError` is
    answered with its card instead of being reported as a bug.
    """
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except ToolError as exc:
            locale = i18n.get_user_locale(interaction)
            card = tool_error_card(exc, locale)
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(view=card, ephemeral=True)
                else:
                    await interaction.response.send_message(view=card, ephemeral=True)
            except discord.HTTPException:
                pass
        except Exception as e:  # noqa: BLE001
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, e, self.__class__.__name__)
    wrapper.__name__ = getattr(callback, "__name__", "callback")
    wrapper.__doc__ = getattr(callback, "__doc__", None)
    return wrapper


async def respond(interaction: discord.Interaction, *, view: ui.LayoutView,
                  ephemeral: bool = True, files: Sequence[discord.File] = ()) -> None:
    """Send ``view`` as the first response, or a follow-up when already answered."""
    kwargs = {"view": view, "allowed_mentions": NO_MENTIONS}
    if files:
        kwargs["files"] = list(files)
    if interaction.response.is_done():
        await interaction.followup.send(ephemeral=ephemeral, **kwargs)
    else:
        await interaction.response.send_message(ephemeral=ephemeral, **kwargs)
