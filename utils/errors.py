"""Expected errors: one exception family + one card builder.

An *expected* error is something the user can fix or that simply is not
possible right now (missing permission, invalid input, not found, cooldown,
module disabled, premium required...). It is NOT a bug: it gets no error code,
no Sentry event and no database row. It is rendered as a uniform Components V2
card by the centralized handler (``cogs/error_handler.py``).

Raise a :class:`ModdyError` (or a subclass) from any command, view or modal
callback and let it propagate::

    raise NotFound("that role")
    raise InvalidInput(field="duration")
    raise ModdyError(title=t(...), description=t(...), severity="warning")

Build a card directly (without raising) with :func:`build_error_card`.
See ``docs/ERROR_HANDLING.md`` for the writing guidelines.
"""

from __future__ import annotations

import enum
import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

import discord
from discord import app_commands, ui
from discord.ext import commands

from config import COLORS
from utils.emojis import EMOJIS

# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------


class Severity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# severity -> (accent colour, default icon). Never green: green is for success.
# CRITICAL is a deeper red than ERROR and distinct from the unexpected-error card.
_SEVERITY_STYLE: Dict[Severity, tuple] = {
    Severity.INFO: (COLORS["primary"], EMOJIS["info"]),
    Severity.WARNING: (COLORS["warning"], EMOJIS["warning"]),
    Severity.ERROR: (COLORS["error"], EMOJIS["error"]),
    Severity.CRITICAL: (0x992D22, EMOJIS["exclamation"]),
}


def _severity(value: Union[Severity, str, None]) -> Severity:
    if isinstance(value, Severity):
        return value
    try:
        return Severity(str(value).lower())
    except ValueError:
        return Severity.ERROR


def severity_accent(severity: Union[Severity, str, None]) -> int:
    return _SEVERITY_STYLE[_severity(severity)][0]


# ---------------------------------------------------------------------------
# Card builder
# ---------------------------------------------------------------------------


def build_error_card(
    title: str,
    description: str,
    *,
    severity: Union[Severity, str] = Severity.ERROR,
    accent: Optional[int] = None,
    icon: Optional[str] = None,
    hint: Optional[str] = None,
    fields: Optional[Sequence[Dict[str, str]]] = None,
    buttons: Optional[Iterable[ui.Button]] = None,
) -> ui.LayoutView:
    """The single builder for every expected-error card.

    Structure (same family as the unexpected-error card): one container with an
    accent colour, ``### <icon> Title``, the description, optional fields and a
    ``-#`` hint, then an optional row of buttons under the container.

    * ``severity`` picks the default accent and icon.
    * ``accent`` overrides the colour; ``icon`` REPLACES the default icon
      (``""`` shows no icon at all). A card never shows two icons.
    * ``fields`` is a list of ``{"name": ..., "value": ...}``.
    * ``buttons`` should be link buttons: the card is not persistent.
    """
    sev = _severity(severity)
    default_accent, default_icon = _SEVERITY_STYLE[sev]
    shown_icon = default_icon if icon is None else icon
    heading = f"### {shown_icon} {title}" if shown_icon else f"### {title}"

    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=discord.Colour(default_accent if accent is None else accent))
    container.add_item(ui.TextDisplay(heading))
    if description:
        container.add_item(ui.TextDisplay(description))
    if fields:
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        for field in fields:
            container.add_item(ui.TextDisplay(f"**{field['name']}**\n{field['value']}"))
    if hint:
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(f"-# {hint}"))
    view.add_item(container)

    buttons = list(buttons or [])
    if buttons:
        row = ui.ActionRow()
        for button in buttons[:5]:
            row.add_item(button)
        view.add_item(row)
    return view


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

_NS = "errors.expected"


def _tr(key: str, locale: Optional[str], **variables: Any) -> str:
    from utils.i18n import i18n
    return i18n.get(key, locale=locale or "en-US", **variables)


def _has(key: str, locale: Optional[str]) -> bool:
    from utils.i18n import i18n
    return not _tr(key, locale).startswith("[")


def humanize_permissions(names: Iterable[str]) -> str:
    """``manage_roles`` -> `` `Manage Roles` `` (comma separated, backticked)."""
    return ", ".join(f"`{str(n).replace('_', ' ').title()}`" for n in names) or "`?`"


def format_retry(seconds: float) -> str:
    """Round UP to whole seconds; ``75`` -> ``1m 15s``."""
    total = max(1, int(math.ceil(seconds)))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs and not hours:
        parts.append(f"{secs}s")
    return " ".join(parts) or "1s"


class ModdyError(Exception):
    """An expected, user-facing error. Rendered as a card, never reported.

    Args:
        description: Literal (already localized) description. Omit to use the
            i18n key ``errors.expected.<key>.description``.
        title / hint: Literal overrides, same rule.
        severity: ``info`` | ``warning`` | ``error`` | ``critical``.
        accent / icon / fields / buttons: see :func:`build_error_card`.
        key: i18n key under ``errors.expected`` (``title``, ``description`` and
            optional ``hint``). Defaults to the subclass key.
        **variables: ``str.format`` values for the i18n strings.
    """

    key: str = "generic"
    severity: Severity = Severity.ERROR
    default_icon: Optional[str] = None
    # Silent when the interaction was already answered (a check that replied itself).
    only_if_unanswered: bool = False

    def __init__(
        self,
        description: Optional[str] = None,
        *,
        title: Optional[str] = None,
        hint: Optional[str] = None,
        severity: Union[Severity, str, None] = None,
        accent: Optional[int] = None,
        icon: Optional[str] = None,
        fields: Optional[Sequence[Dict[str, str]]] = None,
        buttons: Optional[Iterable[ui.Button]] = None,
        key: Optional[str] = None,
        **variables: Any,
    ):
        super().__init__(description or key or self.key)
        self.description = description
        self.title = title
        self.hint = hint
        self.accent = accent
        self.icon = icon if icon is not None else self.default_icon
        self.fields = fields
        self.buttons = list(buttons) if buttons else None
        self.variables = variables
        if key:
            self.key = key
        if severity is not None:
            self.severity = _severity(severity)

    def render(self, locale: Optional[str] = None) -> ui.LayoutView:
        """Build the card for ``locale``."""
        base = f"{_NS}.{self.key}"
        title = self.title if self.title is not None else _tr(f"{base}.title", locale, **self.variables)
        description = (self.description if self.description is not None
                       else _tr(f"{base}.description", locale, **self.variables))
        hint = self.hint
        if hint is None and _has(f"{base}.hint", locale):
            hint = _tr(f"{base}.hint", locale, **self.variables)
        return build_error_card(
            title, description,
            severity=self.severity, accent=self.accent, icon=self.icon,
            hint=hint, fields=self.fields, buttons=self.buttons,
        )


UserFacingError = ModdyError  # alias


class MissingPermissions(ModdyError):
    """The USER lacks a permission."""
    key = "missing_permissions"
    severity = Severity.WARNING

    def __init__(self, permissions: Iterable[str] = (), **kwargs: Any):
        self.permissions = list(permissions)
        super().__init__(permissions=humanize_permissions(self.permissions), **kwargs)


class BotMissingPermissions(ModdyError):
    """MODDY lacks a permission."""
    key = "bot_missing_permissions"
    severity = Severity.WARNING

    def __init__(self, permissions: Iterable[str] = (), **kwargs: Any):
        self.permissions = list(permissions)
        super().__init__(permissions=humanize_permissions(self.permissions), **kwargs)


class InvalidInput(ModdyError):
    """A value the user typed/picked is not acceptable. ``field`` names it."""
    key = "invalid_input"
    severity = Severity.WARNING

    def __init__(self, field: Optional[str] = None, **kwargs: Any):
        if field and "key" not in kwargs:
            kwargs["key"] = "invalid_input_field"
        super().__init__(field=f"`{field}`" if field else "", **kwargs)


class NotFound(ModdyError):
    """Something the user referred to does not exist. ``what`` names it."""
    key = "not_found"
    severity = Severity.WARNING

    def __init__(self, what: Optional[str] = None, **kwargs: Any):
        if what and "key" not in kwargs:
            kwargs["key"] = "not_found_named"
        super().__init__(what=what or "", **kwargs)


class Cooldown(ModdyError):
    key = "cooldown"
    severity = Severity.INFO

    def __init__(self, retry_after: float = 1.0, **kwargs: Any):
        self.retry_after = retry_after
        super().__init__(retry=format_retry(retry_after), **kwargs)


class ModuleDisabled(ModdyError):
    """The module behind this feature is not active on the server."""
    key = "module_disabled"
    severity = Severity.INFO

    def __init__(self, module: Optional[str] = None, **kwargs: Any):
        if module and "key" not in kwargs:
            kwargs["key"] = "module_disabled_named"
        super().__init__(module=f"**{module}**" if module else "", **kwargs)


class PremiumRequired(ModdyError):
    key = "premium_required"
    severity = Severity.INFO
    default_icon = EMOJIS.get("premium", EMOJIS["info"])

    def __init__(self, feature: Optional[str] = None, **kwargs: Any):
        if feature and "key" not in kwargs:
            kwargs["key"] = "premium_required_named"
        super().__init__(feature=f"**{feature}**" if feature else "", **kwargs)


class NotAllowed(ModdyError):
    key = "not_allowed"
    severity = Severity.WARNING


class NotYours(ModdyError):
    """A button/menu that belongs to someone else."""
    key = "not_yours"
    severity = Severity.INFO


class GuildOnly(ModdyError):
    key = "guild_only"
    severity = Severity.INFO


class Unavailable(ModdyError):
    """Not possible right now (temporary)."""
    key = "unavailable"
    severity = Severity.INFO


class MissingArgument(ModdyError):
    key = "missing_argument"
    severity = Severity.WARNING

    def __init__(self, argument: str = "", **kwargs: Any):
        super().__init__(argument=f"`{argument}`", **kwargs)


# ---------------------------------------------------------------------------
# discord.py built-ins -> ModdyError
# ---------------------------------------------------------------------------


def to_expected_error(error: BaseException) -> Optional[ModdyError]:
    """Return the :class:`ModdyError` an exception stands for, or ``None``.

    Unwraps ``CommandInvokeError`` / ``.original`` and maps discord.py's
    expected errors. A plain ``CheckFailure`` becomes a :class:`NotAllowed` that
    stays silent when the check already answered the interaction.
    """
    seen = 0
    while seen < 5:
        if isinstance(error, ModdyError):
            return error
        inner = getattr(error, "original", None)
        if isinstance(inner, BaseException) and inner is not error:
            error, seen = inner, seen + 1
            continue
        break

    if isinstance(error, (app_commands.BotMissingPermissions, commands.BotMissingPermissions)):
        return BotMissingPermissions(error.missing_permissions)
    if isinstance(error, (app_commands.MissingPermissions, commands.MissingPermissions)):
        return MissingPermissions(error.missing_permissions)
    if isinstance(error, (app_commands.CommandOnCooldown, commands.CommandOnCooldown)):
        return Cooldown(error.retry_after)
    if isinstance(error, (app_commands.NoPrivateMessage, commands.NoPrivateMessage)):
        return GuildOnly()
    if isinstance(error, app_commands.TransformerError):
        value = str(getattr(error, "value", ""))[:80].replace("`", "'")
        return ModdyError(key="unresolved_argument", severity=Severity.WARNING, value=value)
    if isinstance(error, commands.MissingRequiredArgument):
        return MissingArgument(error.param.name)
    if isinstance(error, (commands.BadArgument, commands.UserInputError)):
        return InvalidInput()
    if isinstance(error, (app_commands.MissingRole, app_commands.MissingAnyRole,
                          commands.MissingRole, commands.MissingAnyRole)):
        return NotAllowed()
    if isinstance(error, (app_commands.CheckFailure, commands.CheckFailure)):
        expected = NotAllowed()
        expected.only_if_unanswered = True
        return expected
    return None
