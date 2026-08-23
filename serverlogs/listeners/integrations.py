"""Webhooks and applications — the two families that only exist in the audit log.

Discord's gateway is unusually poor here:

* ``webhooks_update`` carries **no detail at all** — not the webhook, not the
  channel, not even whether it was a create, an edit or a delete. Everything
  in :func:`on_webhook_audit` therefore comes from the audit-log entry the
  cog forwards, which is why this file never receives a ``discord.Webhook``.
* bots and integrations have no gateway event either: an application being
  added or removed is only ever visible as ``bot_add`` /
  ``integration_create`` / ``integration_delete`` audit entries.

The one exception is :func:`on_app_command_permissions_update`, which is a
real gateway event (``on_raw_app_command_permissions_update``) and carries
its own payload.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import discord

from serverlogs.renderer import (
    escape, fmt_channel, fmt_code, fmt_number, fmt_role, fmt_user, fmt_user_id,
)


# --------------------------------------------------------------------------- #
# Webhooks
# --------------------------------------------------------------------------- #

#: ``audit change attribute -> event`` for ``webhook_update``. Discord names
#: the channel ``channel_id`` and the avatar ``avatar_hash`` depending on the
#: API version, so both spellings map to the same event.
_WEBHOOK_UPDATE_EVENTS: Tuple[Tuple[str, str], ...] = (
    ("name", "webhook_name_update"),
    ("avatar", "webhook_avatar_update"),
    ("avatar_hash", "webhook_avatar_update"),
    ("channel", "webhook_channel_update"),
    ("channel_id", "webhook_channel_update"),
)


async def on_webhook_audit(service, guild: discord.Guild,
                           audit_entry: discord.AuditLogEntry) -> None:
    """``webhook_create`` / ``webhook_update`` / ``webhook_delete`` entries.

    The cog filters on the action and forwards; the split between "created",
    "edited" and "deleted" is done here so the three webhook lifecycles share
    the same header and channel-ignore handling.
    """
    action = getattr(audit_entry, "action", None)
    changes = _changes(audit_entry)
    channel = _webhook_channel(guild, audit_entry, changes)

    if action == discord.AuditLogAction.webhook_update:
        await _log_webhook_update(service, guild, audit_entry, changes, channel)
        return

    if action == discord.AuditLogAction.webhook_create:
        event = "webhook_create"
    elif action == discord.AuditLogAction.webhook_delete:
        event = "webhook_delete"
    else:
        return

    entry = await service.open(guild, event, channel=channel,
                               actor=getattr(audit_entry, "user", None))
    if entry is None:
        return
    _webhook_header(entry, audit_entry, changes)
    if channel is not None:
        entry.line("channel", fmt_channel(channel))
    entry.line("type", _webhook_type(changes, audit_entry))
    entry.actor(getattr(audit_entry, "user", None), getattr(audit_entry, "reason", None))
    await service.submit(guild, entry)


async def _log_webhook_update(service, guild: discord.Guild,
                              audit_entry: discord.AuditLogEntry,
                              changes: Dict[str, Tuple[Any, Any]],
                              channel: Any) -> None:
    """One log per property the edit actually touched."""
    seen: List[str] = []
    for attribute, event in _WEBHOOK_UPDATE_EVENTS:
        if attribute not in changes or event in seen:
            continue
        before, after = changes[attribute]
        if before == after:
            continue
        seen.append(event)

        entry = await service.open(guild, event, channel=channel,
                                   actor=getattr(audit_entry, "user", None))
        if entry is None:
            continue
        _webhook_header(entry, audit_entry, changes)

        if event == "webhook_channel_update":
            entry.line("before", fmt_channel(_resolve_channel(guild, before)))
            entry.line("after", fmt_channel(_resolve_channel(guild, after)))
        elif event == "webhook_avatar_update":
            entry.line("before", _render_avatar(before, entry.locale))
            entry.line("after", _render_avatar(after, entry.locale))
        else:
            entry.line("before", escape(before) or _none(entry))
            entry.line("after", escape(after) or _none(entry))

        entry.actor(getattr(audit_entry, "user", None),
                    getattr(audit_entry, "reason", None))
        await service.submit(guild, entry)


def _webhook_header(entry, audit_entry: discord.AuditLogEntry,
                    changes: Dict[str, Tuple[Any, Any]]) -> None:
    """Identity lines shared by every webhook event."""
    entry.line("webhook", escape(_webhook_name(audit_entry, changes)))
    webhook_id = _target_id(audit_entry)
    if webhook_id is not None:
        entry.line("id", fmt_number(webhook_id))


def _webhook_name(audit_entry: discord.AuditLogEntry,
                  changes: Dict[str, Tuple[Any, Any]]) -> Optional[str]:
    """Best available name: the target, else whichever side of the diff has one."""
    name = getattr(getattr(audit_entry, "target", None), "name", None)
    if name:
        return str(name)
    before, after = changes.get("name", (None, None))
    for candidate in (after, before):
        if candidate:
            return str(candidate)
    return None


def _webhook_type(changes: Dict[str, Tuple[Any, Any]],
                  audit_entry: discord.AuditLogEntry) -> Optional[str]:
    """The webhook type, rendered as code — from the diff or from the target."""
    before, after = changes.get("type", (None, None))
    value = after if after is not None else before
    if value is None:
        value = getattr(getattr(audit_entry, "target", None), "type", None)
    if value is None:
        return None
    return fmt_code(getattr(value, "name", None) or value)


def _webhook_channel(guild: discord.Guild, audit_entry: discord.AuditLogEntry,
                     changes: Dict[str, Tuple[Any, Any]]) -> Any:
    """The channel a webhook lives in, from the diff or from ``entry.extra``."""
    candidates: List[Any] = []
    for attribute in ("channel", "channel_id"):
        before, after = changes.get(attribute, (None, None))
        candidates.extend((after, before))

    extra = getattr(audit_entry, "extra", None)
    candidates.append(getattr(extra, "channel", None))
    candidates.append(getattr(extra, "channel_id", None))
    candidates.append(getattr(getattr(audit_entry, "target", None), "channel", None))
    candidates.append(getattr(getattr(audit_entry, "target", None), "channel_id", None))

    for candidate in candidates:
        resolved = _resolve_channel(guild, candidate)
        if resolved is not None:
            return resolved
    return None


def _resolve_channel(guild: discord.Guild, value: Any) -> Any:
    """Turn a channel id (or an already-resolved channel) into something printable."""
    if value is None:
        return None
    if isinstance(value, (int, str)):
        try:
            channel_id = int(value)
        except (TypeError, ValueError):
            return None
        resolved = guild.get_channel_or_thread(channel_id) if guild else None
        return resolved if resolved is not None else discord.Object(id=channel_id)
    return value


def _render_avatar(value: Any, locale: str) -> str:
    """Avatars arrive either as an :class:`~discord.Asset` or as a bare hash."""
    from utils.i18n import t
    if not value:
        return t("modules.logs.values.none", locale=locale)
    url = getattr(value, "url", None)
    if url:
        return f"[{t('modules.logs.values.open_link', locale=locale)}]({url})"
    return fmt_code(value)


# --------------------------------------------------------------------------- #
# Applications
# --------------------------------------------------------------------------- #

async def on_app_audit(service, guild: discord.Guild,
                       audit_entry: discord.AuditLogEntry, event: str) -> None:
    """``app_add`` (``bot_add`` / ``integration_create``) or ``app_remove``.

    Discord files three different audit actions for what a server owner reads
    as one thing — "an app was added" — so the cog decides which of the two
    events it is and passes it in.
    """
    entry = await service.open(guild, event, actor=getattr(audit_entry, "user", None))
    if entry is None:
        return

    target = getattr(audit_entry, "target", None)
    entry.line("application", _application_label(target, audit_entry))
    application_id = _target_id(audit_entry)
    if application_id is not None:
        entry.line("id", fmt_number(application_id))

    avatar = getattr(target, "display_avatar", None)
    if avatar is not None:
        entry.thumbnail(getattr(avatar, "url", None))

    entry.actor(getattr(audit_entry, "user", None), getattr(audit_entry, "reason", None))
    await service.submit(guild, entry)


def _application_label(target: Any, audit_entry: discord.AuditLogEntry) -> Optional[str]:
    """The app as a mention when it is a real user, else its name, else its id."""
    if isinstance(target, (discord.User, discord.Member, discord.ClientUser)):
        return fmt_user(target)
    name = getattr(target, "name", None)
    if name:
        return escape(str(name))
    application_id = _target_id(audit_entry)
    return fmt_number(application_id) if application_id is not None else None


async def on_app_command_permissions_update(
        service, guild: discord.Guild,
        payload: discord.RawAppCommandPermissionsUpdateEvent) -> None:
    """A server changed who may use an application command.

    The payload's ``target_id`` is either a command id or the application id
    itself — in the latter case the overwrites apply to every command of that
    app that has no explicit rule of its own.
    """
    entry = await service.open(guild, "app_command_permission_update")
    if entry is None:
        return

    entry.line("application", fmt_number(getattr(payload, "application_id", None)))
    entry.line("command", fmt_number(getattr(payload, "target_id", None)))

    rendered = [
        f"• {_permission_target(permission)} — {_permission_word(permission, entry.locale)}"
        for permission in (getattr(payload, "permissions", None) or [])
    ]
    if rendered:
        entry.block("permissions", "\n".join(rendered))

    await service.submit(guild, entry)


def _permission_target(permission: Any) -> str:
    """Render a permission target as a mention whenever Discord resolved it."""
    target = getattr(permission, "target", None)
    if isinstance(target, discord.Role):
        return fmt_role(target)
    if isinstance(target, (discord.User, discord.Member, discord.ClientUser)):
        return fmt_user(target)
    if isinstance(target, (discord.abc.GuildChannel, discord.Thread)):
        return fmt_channel(target)

    target_id = (getattr(target, "id", None)
                 or getattr(permission, "target_id", None)
                 or getattr(permission, "id", None))
    if target_id is None:
        return "—"

    # Unresolved target: fall back to the declared type so the reader still
    # gets a clickable mention instead of a naked snowflake.
    type_name = getattr(getattr(permission, "type", None), "name", "")
    if type_name == "role":
        return fmt_role(discord.Object(id=target_id))
    if type_name == "user":
        return fmt_user_id(target_id)
    if type_name == "channel":
        return fmt_channel(discord.Object(id=target_id))
    return fmt_number(target_id)


def _permission_word(permission: Any, locale: str) -> str:
    from utils.i18n import t
    key = "allowed" if getattr(permission, "permission", False) else "denied"
    return t(f"modules.logs.values.{key}", locale=locale)


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def _changes(audit_entry: discord.AuditLogEntry) -> Dict[str, Tuple[Any, Any]]:
    """``{attribute: (before, after)}`` for an audit entry, defensively.

    Audit change payloads differ between API versions, so nothing here may
    assume an attribute exists.
    """
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


def _target_id(audit_entry: discord.AuditLogEntry) -> Optional[int]:
    return (getattr(getattr(audit_entry, "target", None), "id", None)
            or getattr(audit_entry, "target_id", None))


def _none(entry) -> str:
    from utils.i18n import t
    return t("modules.logs.values.none", locale=entry.locale)
