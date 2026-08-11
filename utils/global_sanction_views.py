"""
Global sanction UI (Components V2 + Modals V2).

Three audiences, one visual language — every panel wears the **accent colour of
the level it is about** (yellow → orange → red), so severity reads before a
single word does:

- **The subject** — one notice DM per case *group*, never one per case:
  :func:`build_sanction_notice`, :func:`build_halt_notice`,
  :func:`build_guild_join_refusal`.
- **Staff, applying** — :class:`GlobalSanctionModal`, a single Modals V2 form
  (level select + reason + duration + grace period) opened from
  ``/mod global apply``.
- **Staff, following up** — :func:`build_group_panel`, the group's status with
  live *Halt countdown* / *Lift* buttons (persistent ``DynamicItem``s that
  re-check staff permissions on every click).
"""

from __future__ import annotations

import logging
import re
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import discord
from discord import ui

from cogs.error_handler import BaseView, BaseModal
from utils import emojis
from utils import global_sanctions as gs
from utils.global_sanctions import GlobalLevel
from utils.i18n import t

logger = logging.getLogger('moddy.global_sanction_views')

# Public URLs quoted in every subject-facing panel.
URL_VIOLATIONS = "https://moddy.app/violations"
URL_SUPPORT = "https://moddy.app/support"
URL_TERMS = "https://moddy.app/terms"

# The subject has no interaction to read a locale from when the notice is sent,
# so notices are rendered in English by default (the keys stay translatable and
# the staff panels use the moderator's own locale).
NOTICE_LOCALE = "en-US"

_UUID_RE = r"[0-9a-fA-F-]{36}"


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def _container(level: GlobalLevel) -> ui.Container:
    """A container wearing the accent colour of a sanction level."""
    return ui.Container(accent_colour=discord.Colour(gs.level_accent(level)))


def _level_name(level: GlobalLevel, locale: str) -> str:
    return t(f"global_sanctions.level.{level.value}", locale=locale)


def _ts(value: Optional[datetime], style: str = "F") -> str:
    if not isinstance(value, datetime):
        return "—"
    return f"<t:{int(value.timestamp())}:{style}>"


def _case_lines(cases: List[Dict[str, Any]], bot=None) -> List[str]:
    """One line per case of the group: reference + what it is about."""
    lines = []
    for case in cases:
        reference = case.get("reference", "?")
        if case.get("subject_type") == "discord_guild":
            name = None
            if bot is not None:
                guild = bot.get_guild(int(case["subject_id"])) if str(case["subject_id"]).isdigit() else None
                name = guild.name if guild else None
            target = f"{name} (`{case['subject_id']}`)" if name else f"`{case['subject_id']}`"
            lines.append(f"{emojis.GROUPS} `{reference}` — {target}")
        else:
            lines.append(f"{emojis.USER} `{reference}` — <@{case['subject_id']}>")
    return lines


def _link_row(locale: str) -> ui.ActionRow:
    """The subject's two ways out: read the detail, or contest it."""
    row = ui.ActionRow()
    row.add_item(ui.Button(
        label=t("global_sanctions.notice.button_details", locale=locale),
        url=URL_VIOLATIONS, style=discord.ButtonStyle.link,
    ))
    row.add_item(ui.Button(
        label=t("global_sanctions.notice.button_appeal", locale=locale),
        url=URL_SUPPORT, style=discord.ButtonStyle.link,
    ))
    return row


# Footnote markers, in the order they are first referenced.
_MARKERS = ("¹", "²", "³", "⁴")


class _Footnotes:
    """Collects footnotes and numbers them in order of first use.

    The implications list is built from the subject's actual situation, so
    which footnotes exist is only known once the bullets are written. This
    hands out a marker the first time a note is referenced and reuses it
    afterwards, so the numbering is always contiguous.
    """

    def __init__(self, locale: str):
        self.locale = locale
        self._order: List[str] = []

    def mark(self, key: str) -> str:
        if key not in self._order:
            self._order.append(key)
        return _MARKERS[self._order.index(key)]

    def render(self, **params) -> Optional[str]:
        if not self._order:
            return None
        return "\n".join(
            f"-# {_MARKERS[i]} "
            + t(f"global_sanctions.notice.footnote_{key}", locale=self.locale, **params)
            for i, key in enumerate(self._order)
        )


def _implications(
    *,
    level: GlobalLevel,
    premium: bool,
    has_servers: bool,
    deadline: Optional[datetime],
    locale: str,
    appealable: bool = True,
) -> tuple:
    """Build the "what this means for you" bullets for *this* subject.

    Nothing is stated that does not apply: a warned user is not threatened
    with anything, a user with no subscription hears nothing about billing, a
    user with no server hears nothing about servers, and legal action is only
    ever mentioned to a suspended one.

    ``appealable=False`` is for a countdown restarted after a **refused**
    appeal — the deadline footnote must not keep offering an appeal that has
    already been decided.

    Returns ``(bullets_markdown, footnotes_markdown | None)``.
    """
    notes = _Footnotes(locale)
    deadline_note = "deadline" if appealable else "deadline_final"
    # A warning restricts nothing, so it carries no consequence to defer — and
    # must never threaten a subscription or a server.
    restricts = level in (GlobalLevel.LIMITED, GlobalLevel.SUSPENDED)
    deferred = deadline is not None and restricts

    def _t(key: str) -> str:
        return t(f"global_sanctions.notice.implies_{key}", locale=locale)

    bullets: List[str] = [_t(level.value)]

    # Billing — only if the sanction restricts, and only if they actually pay.
    if premium and restricts:
        if deferred:
            bullets.append(
                _t("subscription") + f" {notes.mark(deadline_note)} {notes.mark('terms')}")
        else:
            bullets.append(_t("subscription_now") + f" {notes.mark('terms')}")

    # Servers — only if any server is actually involved.
    if level is GlobalLevel.SUSPENDED and has_servers:
        if deferred:
            bullets.append(_t("servers") + f" {notes.mark(deadline_note)}")
            bullets.append(_t("data") + f" {notes.mark(deadline_note)}")
        else:
            bullets.append(_t("servers_now"))
            bullets.append(_t("data_now"))

    # Legal exposure — never for a warning or a limitation.
    if level is GlobalLevel.SUSPENDED:
        bullets.append(_t("legal") + f" {notes.mark('legal')}")

    body = (
        f"**{t('global_sanctions.notice.implies_title', locale=locale)}**\n"
        + "\n".join(f"- {line}" for line in bullets)
    )
    footnotes = notes.render(
        date=_ts(deadline), rel=_ts(deadline, "R"), terms=URL_TERMS,
    )
    return body, footnotes


# --------------------------------------------------------------------------- #
# Subject-facing notices
# --------------------------------------------------------------------------- #

def build_sanction_notice(
    *,
    bot=None,
    level: GlobalLevel,
    reason: str,
    cases: List[Dict[str, Any]],
    deadline: Optional[datetime] = None,
    premium: bool = False,
    has_servers: Optional[bool] = None,
    locale: str = NOTICE_LOCALE,
) -> BaseView:
    """The single notice a sanctioned user receives for a whole case group.

    Laid out as one explanatory container, then **one container per case**, then
    the links. What the first container says adapts to the subject: only what
    actually applies to them is stated (see :func:`_implications`).

    ``has_servers`` overrides the "is any server involved" guess — pass it when
    the subject owns servers that are not themselves in the group (a suspended
    user loses those too).
    """
    if has_servers is None:
        has_servers = any(c.get("subject_type") == "discord_guild" for c in cases)

    view = BaseView()

    # --- 1. What happened, and what it means for them -------------------
    container = _container(level)
    container.add_item(ui.TextDisplay(
        f"### {emojis.EXCLAMATION} {t('global_sanctions.notice.title', locale=locale)}\n"
        + t("global_sanctions.notice.intro", locale=locale, url=URL_VIOLATIONS)
    ))
    container.add_item(ui.TextDisplay(
        t("global_sanctions.notice.appeal", locale=locale, url=URL_SUPPORT)
    ))

    body, footnotes = _implications(
        level=level, premium=premium, has_servers=has_servers,
        deadline=deadline, locale=locale,
    )
    container.add_item(ui.TextDisplay(body))

    if footnotes:
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(footnotes))

    view.add_item(container)

    # --- 2. One container per case of the group -------------------------
    for case in cases:
        view.add_item(_case_container(case, level, reason, bot, locale))

    # --- 3. Where to go next --------------------------------------------
    view.add_item(_link_row(locale))
    return view


def _case_container(
    case: Dict[str, Any], group_level: GlobalLevel, reason: str, bot, locale: str
) -> ui.Container:
    """One case of the group, rendered as its own accented card."""
    level = group_level
    if case.get("level"):
        try:
            level = GlobalLevel(case["level"])
        except ValueError:
            pass

    container = _container(level)
    if case.get("subject_type") == "discord_guild":
        name = None
        if bot is not None and str(case["subject_id"]).isdigit():
            guild = bot.get_guild(int(case["subject_id"]))
            name = guild.name if guild else None
        target = (
            f"**{t('global_sanctions.notice.field_server', locale=locale)}:** "
            + (f"{name} (`{case['subject_id']}`)" if name else f"`{case['subject_id']}`")
        )
    else:
        target = (
            f"**{t('global_sanctions.notice.field_user', locale=locale)}:** "
            f"<@{case['subject_id']}> (`{case['subject_id']}`)"
        )

    container.add_item(ui.TextDisplay(
        f"{target}\n"
        f"**{t('global_sanctions.notice.field_level', locale=locale)}:** "
        f"{_level_name(level, locale)}\n"
        f"**{t('global_sanctions.notice.field_reason', locale=locale)}:** "
        f"{(case.get('reason') or reason or '')[:500]}\n"
        f"**{t('global_sanctions.notice.field_case', locale=locale)}:** "
        f"`{case.get('reference', '?')}`"
    ))
    return container


def build_halt_notice(
    *, level: GlobalLevel, cases: List[Dict[str, Any]],
    locale: str = NOTICE_LOCALE,
) -> BaseView:
    """Told to the subject when their appeal stops the countdown."""
    view = BaseView()
    container = ui.Container(accent_colour=discord.Colour(0x5865F2))
    container.add_item(ui.TextDisplay(
        f"### {emojis.PENDING} {t('global_sanctions.halt.title', locale=locale)}"
    ))
    container.add_item(ui.TextDisplay(
        t("global_sanctions.halt.description", locale=locale,
          level=_level_name(level, locale))
    ))
    lines = _case_lines(cases)
    if lines:
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay("\n".join(lines)))
    view.add_item(container)

    row = ui.ActionRow()
    row.add_item(ui.Button(
        label=t("global_sanctions.notice.button_details", locale=locale),
        url=URL_VIOLATIONS, style=discord.ButtonStyle.link,
    ))
    view.add_item(row)
    return view


def build_resume_notice(
    *, level: GlobalLevel, cases: List[Dict[str, Any]],
    deadline: Optional[datetime] = None, premium: bool = False,
    locale: str = NOTICE_LOCALE,
) -> BaseView:
    """Told to the subject when their appeal is refused and the clock restarts.

    This is the last thing they hear before the measures apply, so it repeats
    the new deadline and what is about to happen.
    """
    view = BaseView()
    container = _container(level)
    container.add_item(ui.TextDisplay(
        f"### {emojis.TIME} {t('global_sanctions.resume.title', locale=locale)}\n"
        + t("global_sanctions.resume.description", locale=locale,
            level=_level_name(level, locale))
    ))

    if deadline is not None:
        container.add_item(ui.TextDisplay(
            t("global_sanctions.resume.deadline", locale=locale,
              date=_ts(deadline), rel=_ts(deadline, "R"))
        ))

    body, footnotes = _implications(
        level=level, premium=premium,
        has_servers=any(c.get("subject_type") == "discord_guild" for c in cases),
        deadline=deadline, locale=locale, appealable=False,
    )
    container.add_item(ui.TextDisplay(body))
    if footnotes:
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(footnotes))

    # Compact reference list — the full per-case cards were in the first
    # notice; here the subject only needs to know which affair this is.
    lines = _case_lines(cases)
    if lines:
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay("\n".join(lines)))

    view.add_item(container)
    view.add_item(_link_row(locale))
    return view


def build_guild_join_refusal(
    *, level: GlobalLevel, guild_name: str, guild_id: int,
    guild_suspended: bool, locale: str = NOTICE_LOCALE,
) -> BaseView:
    """DM sent when Moddy refuses to stay in a server.

    Three refusals share this panel: a **suspended server**, and a **globally
    sanctioned person** — the owner or whoever added the bot. A limitation is
    enough for the two person-shaped cases: it freezes growth, and bringing
    Moddy somewhere new is growth. Either way the bot leaves immediately and
    says why, to the person it concerns.
    """
    view = BaseView()
    container = _container(level)

    if guild_suspended:
        title_key = "global_sanctions.join_refused.guild.title"
        body_key = "global_sanctions.join_refused.guild.description"
    else:
        title_key = "global_sanctions.join_refused.user.title"
        # A limited account and a suspended one are refused for different
        # reasons — say which one applies.
        suffix = "limited" if level is GlobalLevel.LIMITED else "suspended"
        body_key = f"global_sanctions.join_refused.user.description_{suffix}"

    container.add_item(ui.TextDisplay(
        f"### {emojis.LOGOUT} {t(title_key, locale=locale)}"
    ))
    container.add_item(ui.TextDisplay(t(body_key, locale=locale)))
    container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    container.add_item(ui.TextDisplay(
        f"**{t('global_sanctions.join_refused.server', locale=locale)}:** "
        f"{guild_name} (`{guild_id}`)"
    ))
    view.add_item(container)
    view.add_item(_link_row(locale))
    return view


# --------------------------------------------------------------------------- #
# Staff — apply flow (Modals V2)
# --------------------------------------------------------------------------- #

class GlobalSanctionModal(BaseModal):
    """One-shot form to sanction a whole group of targets.

    Modals V2: a ``Select`` for the level and three ``TextInput``s, plus a
    ``TextDisplay`` recapping the targets — five top-level components, the
    maximum a modal allows.
    """

    def __init__(self, *, bot, staff_id: int, user_id: Optional[int],
                 guild_ids: List[int], targets_recap: str,
                 locale: str = "en-US", on_done=None):
        super().__init__(
            title=t("global_sanctions.staff.apply_title", locale=locale)[:45],
            timeout=900,
        )
        self.bot = bot
        self.staff_id = staff_id
        self.user_id = user_id
        self.guild_ids = guild_ids
        self.locale = locale
        self.on_done = on_done

        self.add_item(ui.TextDisplay(
            f"**{t('global_sanctions.staff.targets', locale=locale)}**\n{targets_recap}"
        ))

        self.level_label = ui.Label(
            text=t("global_sanctions.staff.level_label", locale=locale)[:45],
            description=t("global_sanctions.staff.level_hint", locale=locale)[:100],
            component=ui.Select(
                options=[
                    discord.SelectOption(
                        label=_level_name(lvl, locale),
                        value=lvl.value,
                        description=t(f"global_sanctions.staff.level_desc_{lvl.value}",
                                      locale=locale)[:100],
                        emoji=discord.PartialEmoji.from_str(gs.level_emoji(lvl)),
                    )
                    for lvl in (GlobalLevel.WARN, GlobalLevel.LIMITED, GlobalLevel.SUSPENDED)
                ],
                min_values=1, max_values=1,
            ),
        )
        self.add_item(self.level_label)

        self.reason_label = ui.Label(
            text=t("global_sanctions.staff.reason_label", locale=locale)[:45],
            description=t("global_sanctions.staff.reason_hint", locale=locale)[:100],
            component=ui.TextInput(
                style=discord.TextStyle.paragraph, required=True, max_length=1500,
            ),
        )
        self.add_item(self.reason_label)

        self.duration_label = ui.Label(
            text=t("global_sanctions.staff.duration_label", locale=locale)[:45],
            description=t("global_sanctions.staff.duration_hint", locale=locale)[:100],
            component=ui.TextInput(
                style=discord.TextStyle.short, required=False,
                max_length=10, placeholder="720",
            ),
        )
        self.add_item(self.duration_label)

        self.grace_label = ui.Label(
            text=t("global_sanctions.staff.grace_label", locale=locale)[:45],
            description=t("global_sanctions.staff.grace_hint", locale=locale)[:100],
            component=ui.TextInput(
                style=discord.TextStyle.short, required=False,
                max_length=4, placeholder="48",
            ),
        )
        self.add_item(self.grace_label)

    async def on_submit(self, interaction: discord.Interaction):
        from services.global_sanction_service import ENFORCEMENT_GRACE_HOURS

        level = GlobalLevel(self.level_label.component.values[0])
        reason = (self.reason_label.component.value or "").strip()

        try:
            expires_at = _parse_hours(self.duration_label.component.value)
            grace_hours = _parse_int(
                self.grace_label.component.value, ENFORCEMENT_GRACE_HOURS)
        except ValueError:
            await interaction.response.send_message(
                view=_error_panel(
                    t("global_sanctions.staff.invalid_number_title", locale=self.locale),
                    t("global_sanctions.staff.invalid_number", locale=self.locale)),
                ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        result = await self.bot.global_sanctions.apply(
            level=level, reason=reason, issuer_id=self.staff_id,
            user_id=self.user_id, guild_ids=self.guild_ids,
            expires_at=expires_at, grace_hours=grace_hours,
        )

        if self.on_done:
            await self.on_done(interaction, level, result)
        else:
            await interaction.followup.send(
                view=build_group_panel(
                    bot=self.bot, level=level, group_id=result["group_id"],
                    cases=result["cases"], enforcement=result["enforcement"],
                    notified=result["notified"], reason=reason, locale=self.locale,
                ),
                ephemeral=True,
            )


class GlobalSanctionHaltModal(BaseModal):
    """Collect the appeal reference / note before stopping a countdown."""

    def __init__(self, *, bot, group_id: str, locale: str = "en-US", on_done=None):
        super().__init__(
            title=t("global_sanctions.staff.halt_title", locale=locale)[:45],
            timeout=600,
        )
        self.bot = bot
        self.group_id = group_id
        self.locale = locale
        self.on_done = on_done

        self.reason_label = ui.Label(
            text=t("global_sanctions.staff.halt_reason_label", locale=locale)[:45],
            description=t("global_sanctions.staff.halt_reason_hint", locale=locale)[:100],
            component=ui.TextInput(
                style=discord.TextStyle.paragraph, required=False, max_length=500,
            ),
        )
        self.add_item(self.reason_label)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        reason = (self.reason_label.component.value or "").strip() or None
        row = await self.bot.global_sanctions.halt(
            self.group_id, by_id=interaction.user.id, reason=reason)

        if row is None:
            await interaction.followup.send(view=_error_panel(
                t("global_sanctions.staff.halt_failed_title", locale=self.locale),
                t("global_sanctions.staff.halt_failed", locale=self.locale),
            ), ephemeral=True)
            return

        if self.on_done:
            await self.on_done(interaction, row)
        else:
            await interaction.followup.send(view=_success_panel(
                t("global_sanctions.staff.halt_done_title", locale=self.locale),
                t("global_sanctions.staff.halt_done", locale=self.locale),
            ), ephemeral=True)


class GlobalSanctionResumeModal(BaseModal):
    """Restart a halted countdown — the appeal was refused."""

    def __init__(self, *, bot, group_id: str, locale: str = "en-US", on_done=None):
        super().__init__(
            title=t("global_sanctions.staff.resume_title", locale=locale)[:45],
            timeout=600,
        )
        self.bot = bot
        self.group_id = group_id
        self.locale = locale
        self.on_done = on_done

        self.grace_label = ui.Label(
            text=t("global_sanctions.staff.grace_label", locale=locale)[:45],
            description=t("global_sanctions.staff.resume_grace_hint", locale=locale)[:100],
            component=ui.TextInput(
                style=discord.TextStyle.short, required=False,
                max_length=4, placeholder="48",
            ),
        )
        self.add_item(self.grace_label)

        self.reason_label = ui.Label(
            text=t("global_sanctions.staff.resume_reason_label", locale=locale)[:45],
            description=t("global_sanctions.staff.resume_reason_hint", locale=locale)[:100],
            component=ui.TextInput(
                style=discord.TextStyle.paragraph, required=False, max_length=500,
            ),
        )
        self.add_item(self.reason_label)

    async def on_submit(self, interaction: discord.Interaction):
        from services.global_sanction_service import ENFORCEMENT_GRACE_HOURS

        try:
            grace_hours = _parse_int(
                self.grace_label.component.value, ENFORCEMENT_GRACE_HOURS)
        except ValueError:
            await interaction.response.send_message(view=_error_panel(
                t("global_sanctions.staff.invalid_number_title", locale=self.locale),
                t("global_sanctions.staff.invalid_number", locale=self.locale),
            ), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        reason = (self.reason_label.component.value or "").strip() or None
        row = await self.bot.global_sanctions.resume(
            self.group_id, by_id=interaction.user.id,
            grace_hours=grace_hours, reason=reason,
        )

        if row is None:
            await interaction.followup.send(view=_error_panel(
                t("global_sanctions.staff.resume_failed_title", locale=self.locale),
                t("global_sanctions.staff.resume_failed", locale=self.locale),
            ), ephemeral=True)
            return

        if self.on_done:
            await self.on_done(interaction, row)
        else:
            await interaction.followup.send(view=_success_panel(
                t("global_sanctions.staff.resume_done_title", locale=self.locale),
                t("global_sanctions.staff.resume_done", locale=self.locale),
            ), ephemeral=True)


# --------------------------------------------------------------------------- #
# Staff — group status panel + live actions
# --------------------------------------------------------------------------- #

def build_group_panel(
    *,
    bot=None,
    level: GlobalLevel,
    group_id: str,
    cases: List[Dict[str, Any]],
    enforcement: Optional[Dict[str, Any]] = None,
    notified: Optional[bool] = None,
    reason: Optional[str] = None,
    locale: str = "en-US",
    actions: bool = True,
) -> BaseView:
    """Status of one sanction group, with the actions staff need next."""
    view = BaseView()
    container = _container(level)

    container.add_item(ui.TextDisplay(
        f"### {gs.level_emoji(level)} "
        f"{t('global_sanctions.staff.group_title', locale=locale)}"
    ))
    container.add_item(ui.TextDisplay(
        f"**{t('global_sanctions.notice.level', locale=locale)}:** "
        f"`{_level_name(level, locale)}`\n"
        f"**{t('global_sanctions.staff.group_id', locale=locale)}:** `{group_id}`"
    ))

    if reason:
        container.add_item(ui.TextDisplay(
            f"**{t('global_sanctions.notice.reason', locale=locale)}:**\n{reason[:600]}"
        ))

    lines = _case_lines(cases, bot)
    if lines:
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(
            f"**{t('global_sanctions.notice.cases', locale=locale)}** "
            f"(`{len(cases)}`)\n" + "\n".join(lines)
        ))

    container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    container.add_item(ui.TextDisplay(
        _enforcement_summary(enforcement, notified, locale)))

    view.add_item(container)

    # The one action that makes sense right now, and only that one.
    if actions and enforcement:
        status = enforcement.get("status")
        row = ui.ActionRow()
        if status == "pending":
            row.add_item(GlobalSanctionHaltButton(group_id, locale=locale))
        elif status == "halted":
            row.add_item(GlobalSanctionResumeButton(group_id, locale=locale))
        if row.children:
            view.add_item(row)

    return view


def _enforcement_summary(
    enforcement: Optional[Dict[str, Any]], notified: Optional[bool], locale: str
) -> str:
    """The countdown block: status, deadline, whether the subject was told."""
    if not enforcement:
        text = f"{emojis.INFO} {t('global_sanctions.staff.no_countdown', locale=locale)}"
    else:
        status = enforcement.get("status", "pending")
        deadline = enforcement.get("deadline")
        if isinstance(deadline, str):
            try:
                deadline = datetime.fromisoformat(deadline)
            except ValueError:
                deadline = None
        icon = {
            "pending": emojis.TIME,
            "halted": emojis.PENDING,
            "executed": emojis.DONE,
            "cancelled": emojis.UNDONE,
        }.get(status, emojis.INFO)
        text = (
            f"{icon} **{t('global_sanctions.staff.countdown', locale=locale)}:** "
            f"`{t(f'global_sanctions.staff.countdown_{status}', locale=locale)}`"
        )
        if status == "pending" and deadline is not None:
            text += f" • {_ts(deadline)} ({_ts(deadline, 'R')})"
        if enforcement.get("premium"):
            text += f"\n-# {emojis.PREMIUM} {t('global_sanctions.staff.premium_note', locale=locale)}"

    if notified is not None:
        key = "notified_yes" if notified else "notified_no"
        text += f"\n-# {t(f'global_sanctions.staff.{key}', locale=locale)}"
    return text


def _guarded(callback):
    """Route any uncaught component error to the central handler."""
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as e:  # noqa: BLE001 — funnel everything to the handler
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, e, self.__class__.__name__)
    return wrapper


async def _may_manage(interaction: discord.Interaction) -> bool:
    """Re-derive the staff permission from the interaction, never from state.

    A persistent button outlives the panel that carried it, so authorization is
    resolved from scratch on every click.
    """
    bot = interaction.client
    try:
        from utils.staff_permissions import (
            staff_permissions, has_staff_node, CommandType,
        )
        if staff_permissions is None:
            return False
        allowed, _ = await staff_permissions.check_command_permission(
            interaction.user.id, CommandType.MODERATOR, "global")
        if not allowed:
            return False
        return await has_staff_node(bot, interaction.user.id, "global_sanction")
    except Exception:
        logger.exception("Global sanction permission check failed")
        return False


class GlobalSanctionHaltButton(
    ui.DynamicItem[ui.Button],
    template=rf"moddy:gsanc:halt:(?P<group>{_UUID_RE})",
):
    """Stop a group's enforcement countdown, straight from the status panel."""

    def __init__(self, group_id: str, locale: str = "en-US"):
        super().__init__(
            ui.Button(
                label=t("global_sanctions.staff.halt_button", locale=locale)[:80],
                style=discord.ButtonStyle.secondary,
                emoji=discord.PartialEmoji.from_str(emojis.PENDING),
                custom_id=f"moddy:gsanc:halt:{group_id}",
            )
        )
        self.group_id = group_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["group"])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        from utils.i18n import i18n
        locale = i18n.get_user_locale(interaction)
        if not await _may_manage(interaction):
            await interaction.response.send_message(view=_error_panel(
                t("global_sanctions.staff.denied_title", locale=locale),
                t("global_sanctions.staff.denied", locale=locale),
            ), ephemeral=True)
            return
        await interaction.response.send_modal(GlobalSanctionHaltModal(
            bot=interaction.client, group_id=self.group_id, locale=locale,
        ))


class GlobalSanctionResumeButton(
    ui.DynamicItem[ui.Button],
    template=rf"moddy:gsanc:resume:(?P<group>{_UUID_RE})",
):
    """Restart a halted countdown, straight from the status panel."""

    def __init__(self, group_id: str, locale: str = "en-US"):
        super().__init__(
            ui.Button(
                label=t("global_sanctions.staff.resume_button", locale=locale)[:80],
                style=discord.ButtonStyle.danger,
                emoji=discord.PartialEmoji.from_str(emojis.TIME),
                custom_id=f"moddy:gsanc:resume:{group_id}",
            )
        )
        self.group_id = group_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["group"])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        from utils.i18n import i18n
        locale = i18n.get_user_locale(interaction)
        if not await _may_manage(interaction):
            await interaction.response.send_message(view=_error_panel(
                t("global_sanctions.staff.denied_title", locale=locale),
                t("global_sanctions.staff.denied", locale=locale),
            ), ephemeral=True)
            return
        await interaction.response.send_modal(GlobalSanctionResumeModal(
            bot=interaction.client, group_id=self.group_id, locale=locale,
        ))


class GlobalSanctionPersistence(BaseView):
    """Marker view registering the global sanction dynamic items at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(
            GlobalSanctionHaltButton,
            GlobalSanctionResumeButton,
        )


# --------------------------------------------------------------------------- #
# Small local helpers
# --------------------------------------------------------------------------- #

def _parse_hours(raw: Optional[str]) -> Optional[datetime]:
    """Parse an hours value into an absolute UTC ``expires_at`` (or None)."""
    if not raw or not raw.strip():
        return None
    hours = float(raw.strip())
    if hours <= 0:
        return None
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def _parse_int(raw: Optional[str], default: int) -> int:
    if not raw or not raw.strip():
        return default
    value = int(raw.strip())
    if value < 0:
        raise ValueError("negative")
    return value


def _error_panel(title: str, description: str) -> BaseView:
    view = BaseView()
    container = ui.Container(accent_colour=discord.Colour(0xED4245))
    container.add_item(ui.TextDisplay(f"### {emojis.ERROR} {title}"))
    container.add_item(ui.TextDisplay(description))
    view.add_item(container)
    return view


def _success_panel(title: str, description: str) -> BaseView:
    view = BaseView()
    container = ui.Container(accent_colour=discord.Colour(0x57F287))
    container.add_item(ui.TextDisplay(f"### {emojis.DONE} {title}"))
    container.add_item(ui.TextDisplay(description))
    view.add_item(container)
    return view
