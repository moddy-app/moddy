"""`/team account` — the full Moddy account behind a Discord user.

Where ``/team user`` answers "who is this Discord account", this one answers
"what does Moddy know about this person": the email and Stripe customer stored
on the ``users`` row, the subscription, the staff roles, the attributes, the
global sanction level, the cases opened against them and the notifications
Moddy sent them.

Those fields are personal data, so the command is gated behind the
``user_lookup`` node (see ``utils/staff_role_permissions.py``) on top of the
staff role check, and every invocation goes through the usual staff audit log.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import discord

from staff.framework import (
    StaffCommand, SlashOption, staff_command, design, CommandType, parse_user_id,
)
from staff.framework import badges
from utils import emojis, global_sanctions
from utils.i18n import t

logger = logging.getLogger("moddy.staff.team.account")

#: How many recent notifications the panel summarizes.
_NOTIFICATION_SAMPLE = 25


def _stamp(moment: Optional[datetime], style: str = "R") -> str:
    """``<t:…>`` for a datetime, ``—`` when there is none."""
    if not moment:
        return "`—`"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return f"<t:{int(moment.timestamp())}:{style}>"


def _code(value: Any) -> str:
    """Backtick a dynamic value (DESIGN.md rule), ``—`` when empty."""
    text = str(value) if value not in (None, "") else "—"
    return f"`{text}`"


async def _safe(coro, default):
    """Await a DB call, degrading to ``default`` instead of failing the panel.

    One unavailable table must not cost the staffer the eight other sections.
    """
    try:
        return await coro
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("account lookup: %s", exc, exc_info=True)
        return default


@staff_command
class AccountCommand(StaffCommand):
    command_type = CommandType.TEAM
    name = "account"
    aliases = ("acc", "whois")
    permission = "user_lookup"
    description = "Everything Moddy stores about a user (email, billing, cases…)."
    options = [
        SlashOption("user", "user", "Target user.", required=True),
    ]

    def parse_message(self, raw: str) -> dict:
        return {"user_id": (raw or "").strip()}

    async def execute(self, ctx):
        bot = ctx.bot
        locale = ctx.locale

        target = ctx.opt("user")
        user_id = target.id if target else parse_user_id(ctx.opt("user_id") or "")
        if not user_id:
            await ctx.send(view=design.invalid_usage(locale, "t.account <@user|user_id>"))
            return

        await ctx.defer()

        try:
            user = await bot.fetch_user(user_id)
        except discord.NotFound:
            await ctx.send(view=design.error(
                t("staff.team.user_notfound_title", locale=locale),
                t("staff.team.user_notfound", locale=locale, id=f"`{user_id}`"),
            ))
            return

        if not bot.db:
            await ctx.send(view=design.error(
                t("staff.common.error.title", locale=locale),
                t("staff.team.account.no_db", locale=locale),
            ))
            return

        from utils.subscription import get_subscription

        (record, staff_perms, subscription, sub_servers, cases_total, cases_open,
         scopes, global_actions, notifications) = await asyncio.gather(
            _safe(bot.db.get_user(user_id), {}),
            _safe(bot.db.get_staff_permissions(user_id), {}),
            _safe(get_subscription(bot, user_id), None),
            _safe(bot.db.get_subscription_servers(user_id), []),
            _safe(bot.db.count_subject_cases("discord_user", user_id), 0),
            _safe(bot.db.count_subject_cases("discord_user", user_id, "open"), 0),
            _safe(bot.db.list_subject_scopes("discord_user", user_id), []),
            _safe(bot.db.list_active_global_actions("discord_user", user_id), []),
            _safe(bot.db.list_notifications(recipient_id=user_id, limit=_NOTIFICATION_SAMPLE), []),
        )

        attributes = (record or {}).get("attributes") or {}
        verification = ((record or {}).get("data") or {}).get("verification") or {}
        rendered, orgs, tier = badges.render_name(user, attributes, verification)

        description = rendered
        if tier == "org_member" and orgs:
            description += f"\n-# {t('staff.common.affiliation', locale=locale, orgs=', '.join(orgs))}"

        fields = [
            _identity_field(bot, user, locale),
            _account_field(record, locale),
            _subscription_field(subscription, sub_servers, record, locale),
            _attributes_field(attributes, locale),
            _moderation_field(global_actions, cases_total, cases_open, scopes, locale),
            _notifications_field(notifications, locale),
        ]
        staff_field = _staff_field(staff_perms, locale)
        if staff_field:
            fields.insert(3, staff_field)

        await ctx.send(view=design.panel(
            "info",
            t("staff.team.account.title", locale=locale),
            description,
            fields=fields,
            emoji=emojis.MANAGE_USER,
            accent="primary",
            footer=t("staff.team.account.privacy", locale=locale),
        ))


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #

def _identity_field(bot, user: discord.abc.User, locale: str) -> Dict[str, str]:
    shared = sum(1 for guild in bot.guilds if guild.get_member(user.id))
    return {
        "name": f"{emojis.USER} {t('staff.team.account.identity', locale=locale)}",
        "value": (
            f"**ID:** `{user.id}`\n"
            f"**{t('staff.team.user.username', locale=locale)}:** `@{user.name}`\n"
            f"**{t('staff.team.user.bot', locale=locale)}:** `{'yes' if user.bot else 'no'}`\n"
            f"**{t('staff.team.user.created', locale=locale)}:** {_stamp(user.created_at)}\n"
            f"**{t('staff.team.user.shared', locale=locale)}:** `{shared}`"
        ),
    }


def _account_field(record: Dict[str, Any], locale: str) -> Dict[str, str]:
    data = (record or {}).get("data") or {}
    email = (record or {}).get("email")
    lines = [
        f"**{t('staff.team.account.email', locale=locale)}:** {_code(email)}",
        f"**Stripe:** {_code((record or {}).get('stripe_customer_id'))}",
        f"**{t('staff.team.user.first_seen', locale=locale)}:** {_stamp((record or {}).get('created_at'))}",
        f"**{t('staff.team.account.updated', locale=locale)}:** {_stamp((record or {}).get('updated_at'))}",
    ]
    if data.get("reminder_timezone"):
        lines.append(f"**{t('staff.team.account.timezone', locale=locale)}:** "
                     f"{_code(data['reminder_timezone'])}")
    if not email:
        lines.append(f"-# {t('staff.team.account.no_email', locale=locale)}")
    return {
        "name": f"{emojis.AT} {t('staff.team.account.moddy_account', locale=locale)}",
        "value": "\n".join(lines),
    }


def _subscription_field(subscription: Optional[Dict[str, Any]],
                        servers: List[Dict[str, Any]],
                        record: Dict[str, Any], locale: str) -> Dict[str, str]:
    active = bool(subscription and subscription.get("is_active"))
    dot = emojis.GREEN_STATUS if active else emojis.RED_STATUS
    status = t("staff.team.subscription.active" if active else "staff.team.subscription.inactive",
               locale=locale)
    lines = [f"{dot} {status}"]
    if active:
        lines[0] += f" — `{subscription.get('tier') or 'Moddy Max'}`"
    if subscription and subscription.get("expires_at"):
        lines.append(f"**{t('staff.team.subscription.expires', locale=locale)}:** "
                     f"{_stamp(subscription['expires_at'], 'f')} ({_stamp(subscription['expires_at'])})")
    lines.append(f"**{t('staff.team.subscription.servers', locale=locale)}:** `{len(servers)}/5`"
                 + ("\n" + "\n".join(f"-# `{s['server_id']}` — {_stamp(s.get('added_at'), 'D')}"
                                     for s in servers[:5]) if servers else ""))
    return {
        "name": f"{emojis.PREMIUM} {t('staff.team.subscription.title', locale=locale)}",
        "value": "\n".join(lines),
    }


def _staff_field(staff_perms: Dict[str, Any], locale: str) -> Optional[Dict[str, str]]:
    roles = (staff_perms or {}).get("roles") or []
    if not roles:
        return None
    role_line = " ".join(f"{badges.role_badge(role)} `{role}`" for role in roles)
    lines = [role_line]
    nodes = sorted({node
                    for granted in ((staff_perms or {}).get("role_permissions") or {}).values()
                    for node in granted})
    lines.append(f"**{t('staff.team.account.nodes', locale=locale)}:** "
                 + (" • ".join(f"`{node}`" for node in nodes)
                    if nodes else f"`{t('staff.team.none', locale=locale)}`"))
    denied = (staff_perms or {}).get("denied_commands") or []
    if denied:
        lines.append(f"**{t('staff.team.account.denied', locale=locale)}:** "
                     + " • ".join(f"`{cmd}`" for cmd in denied))
    lines.append(f"-# {t('staff.team.account.staff_since', locale=locale)} "
                 f"{_stamp((staff_perms or {}).get('created_at'))}")
    return {
        "name": f"{emojis.STAFF} {t('staff.team.account.staff', locale=locale)}",
        "value": "\n".join(lines),
    }


def _attributes_field(attributes: Dict[str, Any], locale: str) -> Dict[str, str]:
    if attributes:
        value = " • ".join(f"`{key}`" + (f": `{val}`" if val is not True else "")
                           for key, val in attributes.items())
    else:
        value = f"-# {t('staff.team.none', locale=locale)}"
    return {"name": f"{emojis.SETTINGS} {t('staff.team.attributes', locale=locale)}",
            "value": value}


def _moderation_field(global_actions: List[Dict[str, Any]], total: int, open_count: int,
                      scopes: List[Dict[str, Any]], locale: str) -> Dict[str, str]:
    level = global_sanctions.level_from_actions(
        action["action"] for action in (global_actions or []))
    lines = [
        f"{global_sanctions.level_emoji(level)} "
        f"**{t('staff.team.account.global_level', locale=locale)}:** `{level.value}`"
    ]
    expiries = [action.get("expires_at") for action in (global_actions or [])
                if action.get("expires_at")]
    if expiries:
        lines.append(f"-# {t('staff.team.account.global_expires', locale=locale)} "
                     f"{_stamp(min(expiries), 'f')}")
    lines.append(
        f"**{t('staff.team.account.cases', locale=locale)}:** `{total}` "
        f"({t('staff.team.account.cases_open', locale=locale)} `{open_count}`) — "
        f"`{len(scopes or [])}` {t('staff.team.account.cases_scopes', locale=locale)}"
    )
    return {"name": f"{emojis.SHIELD} {t('staff.team.account.moderation', locale=locale)}",
            "value": "\n".join(lines)}


def _notifications_field(notifications: List[Dict[str, Any]], locale: str) -> Dict[str, str]:
    if not notifications:
        return {"name": f"{emojis.MESSAGE} {t('staff.team.account.notifications', locale=locale)}",
                "value": f"-# {t('staff.team.none', locale=locale)}"}
    shown = len(notifications)
    suffix = "+" if shown >= _NOTIFICATION_SAMPLE else ""
    latest = notifications[0]
    lines = [
        f"**{t('staff.team.account.notifications_recent', locale=locale)}:** `{shown}{suffix}`",
        f"-# {t('staff.team.account.notifications_last', locale=locale)} "
        f"`{latest.get('kind') or '—'}` — {_stamp(latest.get('created_at'))}",
        f"-# `{latest.get('id')}`",
    ]
    return {"name": f"{emojis.MESSAGE} {t('staff.team.account.notifications', locale=locale)}",
            "value": "\n".join(lines)}
