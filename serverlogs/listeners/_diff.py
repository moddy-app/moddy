"""Declarative before/after logging.

Discord collapses a dozen distinct settings into a single ``*_update``
gateway event, while the registry exposes one event per setting (so an admin
can log renames without logging every permission tweak). Rather than writing
the same ``if before.x != after.x`` block sixty times, each listener declares
a table of :class:`DiffRule` and lets :func:`emit_diffs` do the comparison,
the formatting and the audit-log lookup.

Adding "log the new X setting" is then one line in a table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional, Sequence

import discord

from serverlogs.renderer import escape

#: Signature of a value renderer: ``(value, locale) -> str``.
Renderer = Callable[[Any, str], str]


def default_render(value: Any, locale: str) -> str:
    from utils.i18n import t
    if value is None or value == "":
        return t("modules.logs.values.none", locale=locale)
    if isinstance(value, bool):
        return t(f"modules.logs.values.{'yes' if value else 'no'}", locale=locale)
    return escape(str(value))


@dataclass(frozen=True)
class DiffRule:
    """One tracked setting: which event it emits and how it reads."""

    event: str
    attr: str
    render: Renderer = default_render
    #: Some settings only exist on certain channel types.
    applies: Optional[Callable[[Any], bool]] = None
    #: Extra lines added to the entry, ``(entry, before, after) -> None``.
    decorate: Optional[Callable[[Any, Any, Any], None]] = None
    #: Long values (topics, descriptions) go to a field instead of a line.
    as_block: bool = False


def _get(obj: Any, attr: str) -> Any:
    value = obj
    for part in attr.split("."):
        value = getattr(value, part, None)
        if value is None:
            return None
    return value


async def emit_diffs(service, guild: discord.Guild, rules: Sequence[DiffRule],
                     before: Any, after: Any, *,
                     header: Optional[Callable[[Any], None]] = None,
                     audit_action: Optional[discord.AuditLogAction] = None,
                     audit_target_id: Optional[int] = None,
                     thumbnail: Optional[str] = None) -> None:
    """Emit one log per rule whose value actually changed.

    ``header`` receives each entry and adds the identity lines shared by
    every event of the family (the channel, the role, the server…).
    """
    changed = []
    for rule in rules:
        if rule.applies is not None and not rule.applies(after):
            continue
        old, new = _get(before, rule.attr), _get(after, rule.attr)
        if old == new:
            continue
        changed.append((rule, old, new))

    if not changed:
        return

    who = reason = None
    if audit_action is not None:
        who, reason = await service.executor(
            guild, audit_action, audit_target_id, wait=1.5)

    for rule, old, new in changed:
        entry = await service.open(guild, rule.event, actor=who)
        if entry is None:
            continue
        if header is not None:
            header(entry)
        if thumbnail:
            entry.thumbnail(thumbnail)

        before_text = rule.render(old, entry.locale)
        after_text = rule.render(new, entry.locale)
        if rule.as_block:
            entry.block("before", before_text)
            entry.block("after", after_text)
        else:
            entry.line("before", before_text)
            entry.line("after", after_text)

        if rule.decorate is not None:
            rule.decorate(entry, old, new)
        entry.actor(who, reason)
        await service.submit(guild, entry)


def diff_sets(before: Iterable[Any], after: Iterable[Any]):
    """``(added, removed)`` between two iterables of hashable-by-id objects."""
    before_list, after_list = list(before or []), list(after or [])
    before_ids = {getattr(item, "id", item) for item in before_list}
    after_ids = {getattr(item, "id", item) for item in after_list}
    added = [item for item in after_list if getattr(item, "id", item) not in before_ids]
    removed = [item for item in before_list if getattr(item, "id", item) not in after_ids]
    return added, removed
