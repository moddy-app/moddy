"""Server settings: every ``guild_update`` property plus onboarding.

Twenty-four of these come from a single ``guild_update`` gateway event and
are declared as a diff table; the five onboarding events exist only in the
audit log and are forwarded by the cog.
"""

from __future__ import annotations

from typing import Any

import discord

from serverlogs.listeners._diff import DiffRule, default_render, diff_sets, emit_diffs
from serverlogs.renderer import escape, fmt_channel, fmt_duration, fmt_user


# --------------------------------------------------------------------------- #
# Renderers
# --------------------------------------------------------------------------- #

def _render_channel(value: Any, locale: str) -> str:
    from utils.i18n import t
    if value is None:
        return t("modules.logs.values.none", locale=locale)
    return fmt_channel(value)


def _render_asset(value: Any, locale: str) -> str:
    from utils.i18n import t
    if value is None:
        return t("modules.logs.values.none", locale=locale)
    return f"[{t('modules.logs.values.open_link', locale=locale)}]({value.url})"


def _render_enum(value: Any, locale: str) -> str:
    if value is None:
        return default_render(value, locale)
    return f"`{escape(getattr(value, 'name', str(value)))}`"


def _render_seconds(value: Any, locale: str) -> str:
    return fmt_duration(int(value) if value is not None else None, locale)


def _render_owner(value: Any, locale: str) -> str:
    return fmt_user(value)


def _render_vanity(value: Any, locale: str) -> str:
    from utils.i18n import t
    if not value:
        return t("modules.logs.values.none", locale=locale)
    return f"`discord.gg/{escape(str(value))}`"


def _render_tier(value: Any, locale: str) -> str:
    from utils.i18n import t
    return t("modules.logs.values.boost_tier", locale=locale, tier=value or 0)


def _has_feature(guild: discord.Guild, feature: str) -> bool:
    return feature in (guild.features or [])


GUILD_RULES = (
    DiffRule("server_name_update", "name"),
    DiffRule("server_description_update", "description", as_block=True),
    DiffRule("server_icon_update", "icon", render=_render_asset),
    DiffRule("server_banner_update", "banner", render=_render_asset),
    DiffRule("server_splash_update", "splash", render=_render_asset),
    DiffRule("server_discovery_splash_update", "discovery_splash", render=_render_asset),
    DiffRule("afk_channel_update", "afk_channel", render=_render_channel),
    DiffRule("afk_timeout_update", "afk_timeout", render=_render_seconds),
    DiffRule("system_channel_update", "system_channel", render=_render_channel),
    DiffRule("server_rules_channel_update", "rules_channel", render=_render_channel),
    DiffRule("public_updates_channel_update", "public_updates_channel",
             render=_render_channel),
    DiffRule("message_notifications_update", "default_notifications", render=_render_enum),
    DiffRule("server_content_filter_update", "explicit_content_filter", render=_render_enum),
    DiffRule("verification_level_update", "verification_level", render=_render_enum),
    DiffRule("mfa_level_update", "mfa_level", render=_render_enum),
    DiffRule("server_owner_update", "owner", render=_render_owner),
    DiffRule("server_vanity_update", "vanity_url_code", render=_render_vanity),
    DiffRule("server_boost_level_update", "premium_tier", render=_render_tier),
    DiffRule("boost_progress_bar_toggle", "premium_progress_bar_enabled"),
    DiffRule("server_preferred_locale_update", "preferred_locale"),
    DiffRule("server_widget_update", "widget_enabled"),
)


async def on_guild_update(service, before: discord.Guild,
                          after: discord.Guild) -> None:
    await emit_diffs(
        service, after, GUILD_RULES, before, after,
        header=lambda entry: _header(entry, after),
        audit_action=discord.AuditLogAction.guild_update,
        audit_target_id=after.id,
        thumbnail=after.icon.url if after.icon else None,
    )

    if set(before.features or []) != set(after.features or []):
        await _log_features(service, before, after)


async def _log_features(service, before: discord.Guild, after: discord.Guild) -> None:
    added, removed = diff_sets(before.features or [], after.features or [])
    who, reason = await service.executor(
        after, discord.AuditLogAction.guild_update, after.id, wait=1.5)

    entry = await service.open(after, "server_features_update", actor=who)
    if entry is not None:
        _header(entry, after)
        if added:
            entry.line("added", ", ".join(f"`{escape(f)}`" for f in added))
        if removed:
            entry.line("removed", ", ".join(f"`{escape(f)}`" for f in removed))
        entry.actor(who, reason)
        await service.submit(after, entry)

    # "Partnered" and "Verified" are features, but servers care about them
    # enough that the registry gives each its own event.
    for feature, event in (("PARTNERED", "partnered_update"),
                           ("VERIFIED", "verified_update")):
        was, now = _has_feature(before, feature), _has_feature(after, feature)
        if was == now:
            continue
        flag_entry = await service.open(after, event)
        if flag_entry is None:
            continue
        _header(flag_entry, after)
        flag_entry.line("before", was)
        flag_entry.line("after", now)
        await service.submit(after, flag_entry)


# --------------------------------------------------------------------------- #
# Onboarding — audit-log only
# --------------------------------------------------------------------------- #

async def on_onboarding_update(service, guild: discord.Guild,
                               audit_entry: discord.AuditLogEntry) -> None:
    """``onboarding_update``: the toggle and the default channels live here."""
    changes = _changes(audit_entry)

    if "enabled" in changes:
        entry = await service.open(guild, "onboarding_toggle", actor=audit_entry.user)
        if entry is not None:
            _header(entry, guild)
            before, after = changes["enabled"]
            entry.line("before", bool(before))
            entry.line("after", bool(after))
            entry.actor(audit_entry.user, audit_entry.reason)
            await service.submit(guild, entry)

    if "default_channel_ids" in changes:
        entry = await service.open(guild, "onboarding_channels_update",
                                   actor=audit_entry.user)
        if entry is not None:
            _header(entry, guild)
            before, after = changes["default_channel_ids"]
            added, removed = diff_sets(before or [], after or [])
            if added:
                entry.line("added", ", ".join(f"<#{cid}>" for cid in added))
            if removed:
                entry.line("removed", ", ".join(f"<#{cid}>" for cid in removed))
            entry.actor(audit_entry.user, audit_entry.reason)
            await service.submit(guild, entry)


async def on_onboarding_prompt(service, guild: discord.Guild,
                               audit_entry: discord.AuditLogEntry,
                               event: str) -> None:
    """``onboarding_question_add`` / ``_remove`` / ``_update``."""
    entry = await service.open(guild, event, actor=audit_entry.user)
    if entry is None:
        return
    _header(entry, guild)
    changes = _changes(audit_entry)
    title = changes.get("title")
    if title:
        entry.line("question", escape(str(title[1] or title[0])))
    entry.actor(audit_entry.user, audit_entry.reason)
    await service.submit(guild, entry)


def _changes(audit_entry: discord.AuditLogEntry) -> dict:
    """``{attribute: (before, after)}`` for an audit entry, defensively."""
    result = {}
    try:
        before, after = audit_entry.changes.before, audit_entry.changes.after
    except Exception:
        return result
    for attribute in set(getattr(before, "_dict", {}) or {}) | set(getattr(after, "_dict", {}) or {}):
        result[attribute] = (getattr(before, attribute, None),
                             getattr(after, attribute, None))
    return result


def _header(entry, guild: discord.Guild) -> None:
    entry.line("server", escape(guild.name))
    entry.line("id", f"`{guild.id}`")
