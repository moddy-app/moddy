"""Shared helpers for the ``/mod case`` sub-group."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Tuple

import discord
from discord import ui

from staff.framework import design, parse_user_id, parse_guild_id
from utils import emojis
from utils.i18n import t
from utils.moderation_cases import (
    Case,
    SubjectType,
    SanctionStatus,
    EventType,
    REFERENCE_ALPHABET,
    REFERENCE_LENGTH,
)


# --------------------------------------------------------------- resolution

async def resolve_subject(ctx) -> Tuple[Optional[SubjectType], Optional[int], Optional[str], object]:
    """Resolve the case subject from a context.

    Returns ``(subject_type, subject_id, subject_name, error_view)``.
    ``error_view`` is a panel when the target couldn't be resolved.
    """
    bot = ctx.bot
    guild_id = None
    user_id = None

    if ctx.is_slash:
        if ctx.opt("guild_id"):
            guild_id = parse_guild_id(ctx.opt("guild_id"))
        elif ctx.opt("user"):
            user_id = ctx.opt("user").id
    else:
        parts = (ctx.raw_args or "").split()
        if parts and parts[0].lower() == "guild":
            guild_id = parse_guild_id(parts[1]) if len(parts) > 1 else None
        elif parts:
            user_id = parse_user_id(parts[0])

    if guild_id:
        guild = bot.get_guild(guild_id)
        return SubjectType.DISCORD_GUILD, guild_id, (guild.name if guild else f"Guild {guild_id}"), None
    if user_id:
        try:
            user = await bot.fetch_user(user_id)
            name = f"{user} ({user.id})"
        except Exception:
            name = f"User {user_id}"
        return SubjectType.DISCORD_USER, user_id, name, None

    return None, None, None, design.error(
        t("staff.common.invalid_usage.title", locale=ctx.locale),
        t("staff.mod.case.usage_target", locale=ctx.locale),
    )


async def load_case(bot, reference: str) -> Optional[Case]:
    """Load a full :class:`Case` (with sanctions + timeline) by public reference."""
    data = await bot.db.get_case_by_reference(reference)
    if not data:
        return None
    return Case.from_db(data["case"], data["sanctions"], data["events"])


def validate_reference(raw: str, locale: str):
    """Return ``(reference, error_view)`` after validating a public case id."""
    ref = (raw or "").strip().upper()
    if len(ref) != REFERENCE_LENGTH or any(c not in REFERENCE_ALPHABET for c in ref):
        return None, design.error(
            t("staff.mod.case.invalid_id_title", locale=locale),
            t("staff.mod.case.invalid_id", locale=locale, length=REFERENCE_LENGTH),
        )
    return ref, None


# --------------------------------------------------------------- formatting

def _subject_display(bot, case: Case) -> str:
    if case.subject_type == SubjectType.DISCORD_USER:
        return f"<@{case.subject_id}> (`{case.subject_id}`)"
    if case.subject_type == SubjectType.DISCORD_GUILD:
        guild = bot.get_guild(int(case.subject_id)) if str(case.subject_id).isdigit() else None
        name = guild.name if guild else "Guild"
        return f"{name} (`{case.subject_id}`)"
    return f"`{case.subject_type.value}` `{case.subject_id}`"


def _issuer_display(case: Case) -> str:
    if case.issuer_id and case.issuer_type.value in ("discord_user", "moddy_staff"):
        return f"<@{case.issuer_id}>"
    return f"`{case.issuer_type.value}`"


def _ts(dt: Optional[datetime], style: str = "R") -> str:
    return f"<t:{int(dt.timestamp())}:{style}>" if dt else "—"


def _sanction_line(s, locale: str) -> str:
    status_dot = emojis.GREEN_STATUS if s.status == SanctionStatus.ACTIVE else emojis.RED_STATUS
    action = t(f"staff.mod.case.action.{s.action.value}", locale=locale)
    parts = [f"{status_dot} {s.emoji()} **{action}**"]
    parts.append(f"`{s.status.value}`")
    if s.expires_at:
        parts.append(f"{emojis.TIME} {_ts(s.expires_at)}")
    elif s.is_active:
        parts.append(f"-# {t('staff.mod.case.permanent', locale=locale)}")
    line = " • ".join(parts)
    if s.issued_by_id:
        line += f"\n-# {t('staff.mod.case.by', locale=locale)} <@{s.issued_by_id}> • `{str(s.id)[:8]}`"
    else:
        line += f"\n-# `{str(s.id)[:8]}`"
    if s.note:
        line += f"\n-# {s.note[:150]}"
    return line


def _event_line(e, locale: str) -> str:
    author = f"<@{e.author_id}>" if e.author_id else t("staff.mod.case.system", locale=locale)
    when = _ts(e.created_at)
    if e.type == EventType.COMMENT:
        return f"{emojis.MESSAGE} **{author}** • {when}\n{e.content or ''}"
    if e.type == EventType.NOTE:
        return f"{emojis.NOTE} **{author}** • {when}\n-# {e.content or ''}"
    if e.type == EventType.EVIDENCE:
        url = (e.payload or {}).get("url", "")
        kind = (e.payload or {}).get("kind", "evidence")
        return f"{emojis.FLAG} **{author}** • {when}\n`{kind}` {url}"
    if e.type == EventType.SANCTION_ADDED:
        act = (e.payload or {}).get("action", "")
        label = t(f"staff.mod.case.action.{act}", locale=locale) if act else ""
        return f"{emojis.ADD} {t('staff.mod.case.evt.sanction_added', locale=locale, action=label)} • {when}"
    if e.type == EventType.SANCTION_REVOKED:
        return f"{emojis.UNDONE} {t('staff.mod.case.evt.sanction_revoked', locale=locale)} • {author} • {when}"
    if e.type == EventType.SANCTION_EXPIRED:
        act = (e.payload or {}).get("action", "")
        label = t(f"staff.mod.case.action.{act}", locale=locale) if act else ""
        return f"{emojis.TIME} {t('staff.mod.case.evt.sanction_expired', locale=locale, action=label)} • {when}"
    if e.type == EventType.STATUS_CHANGE:
        p = e.payload or {}
        return (
            f"{emojis.SYNC} {t('staff.mod.case.evt.status_change', locale=locale, frm=p.get('from', '?'), to=p.get('to', '?'), trigger=p.get('trigger', '?'))}"
            f" • {when}"
        )
    return f"{emojis.INFO} {e.type.value} • {when}"


def build_case_panel(ctx, case: Case, *, show_internal: bool = True) -> design.BaseView:
    """Build a Linear-style case detail panel: sidebar fields + timeline.

    ``show_internal=False`` hides staff-only notes (used for the user view).
    """
    bot = ctx.bot
    locale = ctx.locale
    accent = "info" if case.is_open else "neutral"
    status_dot = emojis.GREEN_STATUS if case.is_open else emojis.RED_STATUS

    view = design.BaseView()
    container = design.make_container(accent)

    # Header.
    container.add_item(ui.TextDisplay(
        design.title_line(case.type_emoji(), t("staff.mod.case.case_title", locale=locale, id=case.reference))
    ))
    if case.status_locked:
        lock_hint = f" • {emojis.LOGOUT} {t('staff.mod.case.locked', locale=locale)}"
    else:
        lock_hint = ""
    container.add_item(ui.TextDisplay(
        f"{status_dot} **{t('staff.mod.case.status', locale=locale)}:** "
        f"`{t(f'staff.mod.case.status_value.{case.status.value}', locale=locale)}`{lock_hint}"
    ))

    container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

    # Sidebar fields.
    container.add_item(ui.TextDisplay(
        f"**{t('staff.mod.case.type', locale=locale)}:** `{t(f'staff.mod.case.type_value.{case.type.value}', locale=locale)}`\n"
        f"**{t('staff.mod.case.subject', locale=locale)}:** {_subject_display(bot, case)}\n"
        f"**{t('staff.mod.case.scope', locale=locale)}:** `{case.scope_type.value}`"
        + (f" `{case.scope_id}`" if case.scope_id else "") + "\n"
        f"**{t('staff.mod.case.issuer', locale=locale)}:** {_issuer_display(case)} • {_ts(case.created_at)}"
    ))
    container.add_item(ui.TextDisplay(f"**{t('staff.mod.case.reason', locale=locale)}:**\n{case.reason[:800]}"))

    # Sanctions.
    container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    if case.sanctions:
        container.add_item(ui.TextDisplay(f"**{t('staff.mod.case.sanctions', locale=locale)}**"))
        for s in case.sanctions:
            container.add_item(ui.TextDisplay(_sanction_line(s, locale)))
    else:
        container.add_item(ui.TextDisplay(f"-# {t('staff.mod.case.no_sanctions', locale=locale)}"))

    # Timeline.
    events = case.events
    if not show_internal:
        events = [e for e in events if e.type != EventType.NOTE]
    if events:
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(f"**{t('staff.mod.case.timeline', locale=locale)}**"))
        # Most recent last, but cap to avoid overflowing the component.
        shown = events[-12:]
        if len(events) > len(shown):
            container.add_item(ui.TextDisplay(f"-# {t('staff.mod.case.timeline_more', locale=locale, count=len(events) - len(shown))}"))
        for e in shown:
            container.add_item(ui.TextDisplay(_event_line(e, locale)))

    view.add_item(container)
    return view
