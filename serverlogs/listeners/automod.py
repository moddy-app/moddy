"""Discord's own AutoMod: rules and executions.

This is **Discord AutoMod** (``Server Settings → AutoMod``), not Moddy's own
``automod_ai`` module — the two are unrelated and this file never touches the
latter.

The rule events are split in two halves for a reason:

* creations and deletions arrive as real gateway events
  (``on_automod_rule_create`` / ``on_automod_rule_delete``) and only need the
  audit log to name the moderator;
* updates do **not**: ``on_automod_rule_update`` hands over the *new* rule
  with no "before" at all, so every per-property update event
  (toggled, renamed, keywords changed…) is built from the changes carried by
  the ``automod_rule_update`` audit entry, which the cog forwards to
  :func:`on_automod_rule_audit`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import discord

from serverlogs.listeners._diff import diff_sets
from serverlogs.renderer import (
    escape, fmt_bool, fmt_channel, fmt_code, fmt_number, fmt_role, fmt_user_id,
)

#: Trigger sub-properties that describe *what* a rule matches. Everything here
#: feeds ``automod_rule_content_update`` — except ``allow_list``, which is the
#: opposite (what the rule must ignore) and gets its own event.
_CONTENT_ATTRIBUTES: Tuple[str, ...] = (
    "keyword_filter", "regex_patterns", "presets",
)
_WHITELIST_ATTRIBUTE = "allow_list"


# --------------------------------------------------------------------------- #
# Rule lifecycle
# --------------------------------------------------------------------------- #

async def on_automod_rule_create(service, rule: discord.AutoModRule) -> None:
    guild = getattr(rule, "guild", None)
    if guild is None:
        return
    who, reason = await service.executor(
        guild, discord.AuditLogAction.automod_rule_create, rule.id)

    entry = await service.open(guild, "automod_rule_create", actor=who)
    if entry is None:
        return
    _header(entry, rule=rule)
    entry.line("enabled", fmt_bool(getattr(rule, "enabled", None), entry.locale))
    entry.actor(who, reason)
    await service.submit(guild, entry)


async def on_automod_rule_delete(service, rule: discord.AutoModRule) -> None:
    guild = getattr(rule, "guild", None)
    if guild is None:
        return
    who, reason = await service.executor(
        guild, discord.AuditLogAction.automod_rule_delete, rule.id)

    entry = await service.open(guild, "automod_rule_delete", actor=who)
    if entry is None:
        return
    _header(entry, rule=rule)
    entry.line("enabled", fmt_bool(getattr(rule, "enabled", None), entry.locale))
    entry.actor(who, reason)
    await service.submit(guild, entry)


async def on_automod_rule_audit(service, guild: discord.Guild,
                                audit_entry: discord.AuditLogEntry) -> None:
    """Every ``automod_rule_update`` property, one log per property.

    discord.py's ``on_automod_rule_update`` only carries the new rule, so the
    before/after values can only come from this audit entry.
    """
    changes = _changes(audit_entry)
    if not changes:
        return

    rule = getattr(audit_entry, "target", None)
    who = getattr(audit_entry, "user", None)
    reason = getattr(audit_entry, "reason", None)

    async def start(event: str):
        """Open an entry already carrying the shared rule header."""
        entry = await service.open(guild, event, actor=who)
        if entry is None:
            return None
        _header(entry, rule=rule, audit_entry=audit_entry, changes=changes)
        return entry

    if "enabled" in changes:
        before, after = changes["enabled"]
        if before != after:
            entry = await start("automod_rule_toggle")
            if entry is not None:
                entry.line("before", fmt_bool(bool(before), entry.locale))
                entry.line("after", fmt_bool(bool(after), entry.locale))
                entry.actor(who, reason)
                await service.submit(guild, entry)

    if "name" in changes:
        before, after = changes["name"]
        if before != after:
            entry = await start("automod_rule_name_update")
            if entry is not None:
                entry.line("before", escape(before) or _value(entry, "none"))
                entry.line("after", escape(after) or _value(entry, "none"))
                entry.actor(who, reason)
                await service.submit(guild, entry)

    if "actions" in changes:
        before, after = changes["actions"]
        entry = await start("automod_rule_actions_update")
        if entry is not None:
            entry.line("before", _render_actions(before, entry.locale))
            entry.line("after", _render_actions(after, entry.locale))
            entry.actor(who, reason)
            await service.submit(guild, entry)

    for attribute, event, render in (
        ("exempt_roles", "automod_rule_roles_update", fmt_role),
        ("exempt_channels", "automod_rule_channels_update", fmt_channel),
    ):
        if attribute not in changes:
            continue
        before, after = changes[attribute]
        added, removed = diff_sets(before or [], after or [])
        if not added and not removed:
            continue
        entry = await start(event)
        if entry is None:
            continue
        if added:
            entry.line("added", ", ".join(render(item) for item in added))
        if removed:
            entry.line("removed", ", ".join(render(item) for item in removed))
        entry.actor(who, reason)
        await service.submit(guild, entry)

    await _log_trigger_changes(service, guild, audit_entry, changes, start)


async def _log_trigger_changes(service, guild: discord.Guild,
                               audit_entry: discord.AuditLogEntry,
                               changes: Dict[str, Tuple[Any, Any]], start) -> None:
    """Split the trigger metadata into "what it matches" and "what it ignores"."""
    before, after = _trigger_pair(changes)
    if before is None and after is None:
        return

    who = getattr(audit_entry, "user", None)
    reason = getattr(audit_entry, "reason", None)

    # What the rule matches on — keywords, regexes, Discord's own presets.
    content_changed = [
        (attribute, getattr(before, attribute, None), getattr(after, attribute, None))
        for attribute in _CONTENT_ATTRIBUTES
        if getattr(before, attribute, None) != getattr(after, attribute, None)
    ]
    mention_before = getattr(before, "mention_limit", None)
    mention_after = getattr(after, "mention_limit", None)

    if content_changed or mention_before != mention_after:
        entry = await start("automod_rule_content_update")
        if entry is not None:
            for attribute, old, new in content_changed:
                entry.block(attribute, _render_list_delta(old, new, entry.locale))
            if mention_before != mention_after:
                entry.line("mention_limit",
                           f"{fmt_number(mention_before)} → {fmt_number(mention_after)}")
            entry.actor(who, reason)
            await service.submit(guild, entry)

    # What the rule deliberately ignores.
    allow_before = getattr(before, _WHITELIST_ATTRIBUTE, None)
    allow_after = getattr(after, _WHITELIST_ATTRIBUTE, None)
    if allow_before != allow_after:
        entry = await start("automod_rule_whitelist_update")
        if entry is not None:
            entry.block(_WHITELIST_ATTRIBUTE,
                        _render_list_delta(allow_before, allow_after, entry.locale))
            entry.actor(who, reason)
            await service.submit(guild, entry)


# --------------------------------------------------------------------------- #
# Executions
# --------------------------------------------------------------------------- #

async def on_automod_action(service, execution: discord.AutoModAction) -> None:
    """A rule actually fired on a message."""
    guild = getattr(execution, "guild", None)
    if guild is None:
        return

    channel = getattr(execution, "channel", None)
    member = getattr(execution, "member", None)

    entry = await service.open(guild, "auto_moderation", channel=channel,
                               subject=member)
    if entry is None:
        return

    entry.line("rule", _execution_rule(execution))
    if member is None:
        entry.line("user", fmt_user_id(getattr(execution, "user_id", None)))
    if channel is not None:
        entry.line("channel", fmt_channel(channel))
    entry.line("action", _render_action_type(getattr(execution, "action", None)))

    matched = (getattr(execution, "matched_keyword", None)
               or getattr(execution, "matched_content", None))
    if matched:
        entry.line("matched", escape(str(matched)))

    # The offending message can be very long — `block` offloads anything that
    # does not fit an embed field to a .txt attachment.
    entry.block("message", getattr(execution, "content", None) or None,
                filename=f"automod-{getattr(execution, 'message_id', 0)}.txt")

    await service.submit(guild, entry)


def _execution_rule(execution: discord.AutoModAction) -> str:
    """The trigger type name, falling back to the raw rule id."""
    trigger_type = getattr(execution, "rule_trigger_type", None)
    name = getattr(trigger_type, "name", None)
    if name:
        return fmt_code(name)
    return fmt_number(getattr(execution, "rule_id", None))


# --------------------------------------------------------------------------- #
# Rendering helpers
# --------------------------------------------------------------------------- #

def _header(entry, *, rule: Any = None,
            audit_entry: Optional[discord.AuditLogEntry] = None,
            changes: Optional[Dict[str, Tuple[Any, Any]]] = None) -> None:
    """Identity lines shared by every AutoMod rule event."""
    name = getattr(rule, "name", None)
    if not name and changes:
        before, after = changes.get("name", (None, None))
        name = after or before
    if name:
        entry.line("rule", escape(str(name)))

    rule_id = getattr(rule, "id", None)
    if rule_id is None and audit_entry is not None:
        rule_id = getattr(audit_entry, "target_id", None)
    if rule_id is not None:
        entry.line("id", fmt_number(rule_id))

    trigger_name = _trigger_type_name(rule, changes)
    if trigger_name:
        entry.line("trigger", fmt_code(trigger_name))


def _trigger_type_name(rule: Any,
                       changes: Optional[Dict[str, Tuple[Any, Any]]]) -> Optional[str]:
    trigger = getattr(rule, "trigger", None)
    name = getattr(getattr(trigger, "type", None), "name", None)
    if name:
        return str(name)
    if changes:
        before, after = changes.get("trigger_type", (None, None))
        value = after if after is not None else before
        name = getattr(value, "name", None)
        if name:
            return str(name)
        trigger_before, trigger_after = _trigger_pair(changes)
        for candidate in (trigger_after, trigger_before):
            name = getattr(getattr(candidate, "type", None), "name", None)
            if name:
                return str(name)
    return None


def _trigger_pair(changes: Dict[str, Tuple[Any, Any]]) -> Tuple[Any, Any]:
    """``(before, after)`` trigger objects, whichever key Discord used."""
    for attribute in ("trigger", "trigger_metadata"):
        if attribute in changes:
            return changes[attribute]
    return None, None


def _render_list_delta(before: Any, after: Any, locale: str) -> str:
    """``✚``/``✖`` listing of the entries added to and removed from a list."""
    added, removed = diff_sets(_as_text_list(before), _as_text_list(after))
    lines: List[str] = []
    lines.extend(f"✚ {fmt_code(item)}" for item in added)
    lines.extend(f"✖ {fmt_code(item)}" for item in removed)
    if not lines:
        from utils.i18n import t
        return t("modules.logs.values.none", locale=locale)
    return "\n".join(lines)


def _as_text_list(value: Any) -> List[str]:
    """Normalise keyword/regex/preset lists (presets are enums) to strings."""
    if not value:
        return []
    if isinstance(value, (str, bytes)):
        return [str(value)]
    try:
        items = list(value)
    except TypeError:
        return [str(value)]
    return [str(getattr(item, "name", item)) for item in items]


def _render_actions(actions: Any, locale: str) -> str:
    """Comma-separated action types (``block_message``, ``timeout``…)."""
    from utils.i18n import t
    rendered = [
        fmt_code(getattr(getattr(action, "type", None), "name", None) or action)
        for action in (actions or [])
    ]
    if not rendered:
        return t("modules.logs.values.none", locale=locale)
    return ", ".join(rendered)


def _render_action_type(action: Any) -> Optional[str]:
    name = getattr(getattr(action, "type", None), "name", None)
    return fmt_code(name) if name else None


def _changes(audit_entry: discord.AuditLogEntry) -> Dict[str, Tuple[Any, Any]]:
    """``{attribute: (before, after)}`` for an audit entry, defensively."""
    result: Dict[str, Tuple[Any, Any]] = {}
    try:
        before, after = audit_entry.changes.before, audit_entry.changes.after
    except Exception:
        return result
    attributes = (set(getattr(before, "_dict", {}) or {})
                  | set(getattr(after, "_dict", {}) or {}))
    for attribute in attributes:
        result[attribute] = (getattr(before, attribute, None),
                             getattr(after, attribute, None))
    return result


def _value(entry, key: str) -> str:
    from utils.i18n import t
    return t(f"modules.logs.values.{key}", locale=entry.locale)
