"""
Moderation cases browser (Components V2 + Modals V2).

A single, reusable, filterable + paginated case explorer powering two
user-facing commands:

- ``/cases``     — a member browsing their own sanctions across every server
  (``mode="user"``: subject is the member, scope is anything). Read-only.
- ``/sanctions`` — a server moderator browsing the sanctions issued in their
  server (``mode="server"``: scope is the guild, subject is anything). Lets the
  moderator comment, edit the reason and close/reopen a case.

The view has two screens that share one container:

- **List screen** — a compact, paginated overview. A single "Filters" button
  opens a Modal (Modals V2: ``Label`` + ``Select``/``UserSelect``) to choose
  status, sanction type, period and a context filter (server in user mode,
  user in server mode). A select opens any listed case.
- **Detail screen** — the full case folder (sidebar fields, reason, sanctions
  and public comments). Internal Moddy-staff notes are never shown here.

These are short-lived, viewer-scoped, ephemeral flows (timeout-based, not
persistent), mirroring the existing case-management flows.
"""

from __future__ import annotations

import logging
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import discord
from discord import ui

from cogs.error_handler import BaseView, BaseModal
from config import COLORS
from utils import emojis
from utils.i18n import t
from utils.moderation_cases import (
    Case,
    CaseType,
    SanctionStatus,
    EventType,
    SanctionAction,
    get_action_emoji,
    get_available_actions,
    get_case_type_emoji,
    TEMPORARY_ACTIONS,
)

logger = logging.getLogger("moddy.cases_browser")

# How many cases are shown per list page.
PAGE_SIZE = 5

# Detail-screen caps. A Components V2 ``LayoutView`` allows at most 40 nested
# components; we render sanctions and comments as combined TextDisplays, but we
# still cap the lines so a heavily-used case stays readable (and well under the
# ceiling whatever the rendering strategy).
_MAX_DETAIL_SANCTIONS = 12
_MAX_DETAIL_COMMENTS = 5

# Period filter values -> lookback window in hours (None = all time).
PERIODS: Dict[str, Optional[int]] = {
    "all": None,
    "1d": 24,
    "7d": 24 * 7,
    "30d": 24 * 30,
    "90d": 24 * 90,
}


# Discord guild permission required to issue / lift each sanction action.
# Administrators bypass this map. Members with only "manage_messages" may view
# and comment/edit/close, but need the specific permission to add or revoke a
# sanction of a given kind.
SANCTION_PERMISSION: Dict[SanctionAction, str] = {
    SanctionAction.WARN: "moderate_members",
    SanctionAction.MUTE: "moderate_members",
    SanctionAction.KICK: "kick_members",
    SanctionAction.BAN: "ban_members",
    SanctionAction.RESTRICT: "moderate_members",
    SanctionAction.REVOKE_ACCESS: "moderate_members",
}


def can_view_server_cases(member: discord.Member) -> bool:
    """Minimum permission to open the server cases browser: Manage Messages."""
    p = member.guild_permissions
    return p.administrator or p.manage_messages


def _can_issue_action(member: discord.Member, action: SanctionAction) -> bool:
    """Whether a member holds the specific permission for a sanction action."""
    p = member.guild_permissions
    if p.administrator:
        return True
    return getattr(p, SANCTION_PERMISSION.get(action, "moderate_members"), False)


def _ts(dt: Optional[datetime], style: str = "R") -> str:
    return f"<t:{int(dt.timestamp())}:{style}>" if dt else "—"


def _perm_error(locale: str) -> BaseView:
    """Standard 'missing permission' panel for case mutations."""
    view = BaseView()
    container = ui.Container(accent_colour=discord.Colour(COLORS["error"]))
    container.add_item(ui.TextDisplay(
        f"### {emojis.ERROR} {t('commands.cases.browser.permission_denied_title', locale=locale)}"
    ))
    container.add_item(ui.TextDisplay(
        t("commands.cases.browser.action_permission_denied", locale=locale)))
    view.add_item(container)
    return view


def _action_emoji_safe(action_value: str) -> str:
    try:
        return get_action_emoji(SanctionAction(action_value))
    except ValueError:
        return emojis.NOTE


def _case_type_emoji_safe(type_value: str) -> str:
    try:
        return get_case_type_emoji(CaseType(type_value))
    except ValueError:
        return emojis.INFO


# --------------------------------------------------------------------------- #
# Browser view
# --------------------------------------------------------------------------- #

# Namespaced custom_id constants for persistent dispatch. Two modes share one
# class — the mode is encoded in the custom_id so each shell only catches its
# own clicks.
_CID_PREFIX = "moddy:cases:browser"

# List-screen custom_ids (per mode).
_CID_LIST_PREV = f"{_CID_PREFIX}:prev:{{mode}}"
_CID_LIST_NEXT = f"{_CID_PREFIX}:next:{{mode}}"
_CID_LIST_PAGE = f"{_CID_PREFIX}:page:{{mode}}"
_CID_LIST_FILTERS = f"{_CID_PREFIX}:filters:{{mode}}"
_CID_LIST_RESET = f"{_CID_PREFIX}:reset:{{mode}}"
_CID_LIST_OPEN = f"{_CID_PREFIX}:open:{{mode}}"

# Detail-screen custom_ids (per mode).
_CID_DETAIL_BACK = f"{_CID_PREFIX}:back:{{mode}}"
_CID_DETAIL_ADD = f"{_CID_PREFIX}:add:{{mode}}"
_CID_DETAIL_REVOKE = f"{_CID_PREFIX}:revoke:{{mode}}"
_CID_DETAIL_TOGGLE = f"{_CID_PREFIX}:toggle:{{mode}}"
_CID_DETAIL_COMMENT = f"{_CID_PREFIX}:comment:{{mode}}"
_CID_DETAIL_EDIT = f"{_CID_PREFIX}:edit:{{mode}}"
_CID_DETAIL_EVIDENCE = f"{_CID_PREFIX}:evidence:{{mode}}"


class CasesBrowserView(BaseView):
    """Filterable, paginated browser over moderation cases.

    Persistent: yes. Auth: in user mode, owner-only (viewer_id derived from the
    interaction); in server mode, ``Manage Messages`` minimum + per-action
    permission for mutations. State (filters, page, detail) lives in memory —
    a fresh shell after a bot restart rebuilds an empty list view and asks the
    user to re-open a case for detail mutations, per ``docs/PERSISTENT_VIEWS``
    "working-copy" guidance.
    """

    __persistent__ = True

    def __init__(
        self,
        bot=None,
        *,
        mode: str = "server",
        viewer_id: Optional[int] = None,
        locale: str = "en-US",
        subject_type: Optional[str] = None,
        subject_id: Optional[int] = None,
        scope_type: Optional[str] = None,
        scope_id: Optional[int] = None,
    ):
        # No timeout — every interactive component is registered with a stable
        # custom_id, so the view survives both inactivity and a bot restart.
        super().__init__(timeout=None)
        self.bot = bot
        self.mode = mode  # "user" | "server"
        self.viewer_id = viewer_id
        self.locale = locale

        # Fixed anchor of the query (never changes for the lifetime of the view).
        self.base_subject_type = subject_type
        self.base_subject_id = subject_id
        self.base_scope_type = scope_type
        self.base_scope_id = scope_id

        # Live filter state.
        self.f_status: Optional[str] = None       # None | "open" | "closed"
        self.f_action: Optional[str] = None       # None | SanctionAction value
        self.f_period: str = "all"
        self.f_scope_type: Optional[str] = None   # user mode: a chosen scope
        self.f_scope_id: Optional[str] = None
        self.f_subject_id: Optional[int] = None   # server mode: a chosen user

        # Paging / data.
        self.page = 0
        self.total = 0
        self.rows: List[Dict[str, Any]] = []
        self.scopes: List[Dict[str, Any]] = []    # user mode: servers w/ counts

        # Detail screen state (None => list screen).
        self.detail: Optional[Case] = None

        # On the persistent shell (no bot, no anchor) we still need to register
        # the navigation buttons so discord.py can dispatch clicks after a
        # restart. Live instances build through `refresh()` / `show_detail()`.
        if self.bot is None:
            self._build_shell()

    # --------------------------------------------------------------- shell
    def _build_shell(self):
        """Build a minimal child set so discord.py registers our custom_ids."""
        self.clear_items()
        nav = ui.ActionRow()
        for cid in (
            _CID_LIST_PREV.format(mode=self.mode),
            _CID_LIST_PAGE.format(mode=self.mode),
            _CID_LIST_NEXT.format(mode=self.mode),
            _CID_LIST_FILTERS.format(mode=self.mode),
            _CID_LIST_RESET.format(mode=self.mode),
        ):
            btn = ui.Button(style=discord.ButtonStyle.secondary, label=" ", custom_id=cid)
            btn.callback = self._on_shell_click
            nav.add_item(btn)
        self.add_item(nav)
        # Open select shell.
        open_sel = ui.Select(
            custom_id=_CID_LIST_OPEN.format(mode=self.mode),
            placeholder=" ",
            options=[discord.SelectOption(label="-", value="-")],
        )
        open_sel.callback = self._on_shell_open
        open_row = ui.ActionRow()
        open_row.add_item(open_sel)
        self.add_item(open_row)
        # Detail-screen shells.
        det = ui.ActionRow()
        for cid in (
            _CID_DETAIL_BACK.format(mode=self.mode),
            _CID_DETAIL_ADD.format(mode=self.mode),
            _CID_DETAIL_REVOKE.format(mode=self.mode),
            _CID_DETAIL_TOGGLE.format(mode=self.mode),
            _CID_DETAIL_COMMENT.format(mode=self.mode),
        ):
            b = ui.Button(style=discord.ButtonStyle.secondary, label=" ", custom_id=cid)
            b.callback = self._on_shell_click
            det.add_item(b)
        self.add_item(det)
        det2 = ui.ActionRow()
        for cid in (
            _CID_DETAIL_EDIT.format(mode=self.mode),
            _CID_DETAIL_EVIDENCE.format(mode=self.mode),
        ):
            b = ui.Button(style=discord.ButtonStyle.secondary, label=" ", custom_id=cid)
            b.callback = self._on_shell_click
            det2.add_item(b)
        self.add_item(det2)

    async def _on_shell_click(self, interaction: discord.Interaction):
        """After-restart fallback: rebuild a fresh list view from interaction."""
        await self._rehydrate_and_list(interaction)

    async def _on_shell_open(self, interaction: discord.Interaction):
        """After-restart fallback for the open-select: jump to the chosen case."""
        values = interaction.data.get("values") or []
        if not values or values[0] == "-":
            await self._rehydrate_and_list(interaction)
            return
        await self._rehydrate_and_detail(interaction, values[0])

    @staticmethod
    def _make_live_from_interaction(interaction: discord.Interaction, mode: str) -> "CasesBrowserView":
        """Build a fresh live browser anchored to this interaction's context."""
        from utils.i18n import i18n as _i18n
        locale = _i18n.get_user_locale(interaction)
        bot = interaction.client
        if mode == "user":
            return CasesBrowserView(
                bot, mode="user", viewer_id=interaction.user.id, locale=locale,
                subject_type="discord_user", subject_id=interaction.user.id,
            )
        return CasesBrowserView(
            bot, mode="server", viewer_id=interaction.user.id, locale=locale,
            scope_type="discord_guild", scope_id=interaction.guild_id,
        )

    async def _rehydrate_and_list(self, interaction: discord.Interaction):
        view = self._make_live_from_interaction(interaction, self.mode)
        await view._reload()
        await interaction.response.edit_message(view=view)

    async def _rehydrate_and_detail(self, interaction: discord.Interaction, case_id: str):
        view = self._make_live_from_interaction(interaction, self.mode)
        try:
            await view.show_detail(interaction, case_id)
        except Exception:
            await view._rehydrate_and_list(interaction)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: user-mode shell = owner-only (re-checked from interaction);
        server-mode shell = Manage Messages (re-checked from interaction)."""
        bot.add_view(cls(mode="user"))
        bot.add_view(cls(mode="server"))

    @property
    def can_manage(self) -> bool:
        """Whether this browser allows mutating cases (server mode only)."""
        return self.mode == "server"

    # ------------------------------------------------------------------ data
    def _since(self) -> Optional[datetime]:
        hours = PERIODS.get(self.f_period)
        if not hours:
            return None
        return datetime.now(timezone.utc) - timedelta(hours=hours)

    def _query_kwargs(self) -> Dict[str, Any]:
        """Resolve the effective filters against the fixed anchor."""
        subject_type = self.base_subject_type
        subject_id = self.base_subject_id
        scope_type = self.base_scope_type
        scope_id = self.base_scope_id

        if self.mode == "user" and self.f_scope_id is not None:
            scope_type = self.f_scope_type
            scope_id = self.f_scope_id
        if self.mode == "server" and self.f_subject_id is not None:
            subject_type = "discord_user"
            subject_id = self.f_subject_id

        return dict(
            subject_type=subject_type, subject_id=subject_id,
            scope_type=scope_type, scope_id=scope_id,
            status=self.f_status, action=self.f_action, since=self._since(),
        )

    async def _reload(self):
        """Re-run the query for the current filters/page and rebuild the list."""
        kwargs = self._query_kwargs()
        self.total = await self.bot.db.count_cases(**kwargs)
        max_page = max(0, (self.total - 1) // PAGE_SIZE)
        self.page = min(self.page, max_page)
        self.rows = await self.bot.db.search_cases(
            **kwargs, limit=PAGE_SIZE, offset=self.page * PAGE_SIZE,
        )
        if self.mode == "user" and not self.scopes:
            self.scopes = await self.bot.db.list_subject_scopes(
                self.base_subject_type, self.base_subject_id,
            )
        self.detail = None
        self._build_list()

    async def refresh(self, interaction: Optional[discord.Interaction] = None):
        """Reload data, rebuild, and (if given) edit the live message."""
        await self._reload()
        if interaction is not None:
            await interaction.response.edit_message(view=self)

    async def show_detail(self, interaction: discord.Interaction, case_id: str):
        """Load a case folder and switch to the detail screen."""
        data = await self.bot.db.get_case_by_id(_uuid.UUID(case_id))
        if not data:
            await interaction.response.send_message(
                t("commands.cases.error", locale=self.locale), ephemeral=True)
            return
        self.detail = Case.from_db(data["case"], data["sanctions"], data["events"])
        self._build_detail()
        await interaction.response.edit_message(view=self)

    async def open_reference(self, reference: str) -> bool:
        """Render the detail screen for a case given by its public reference.

        Returns ``False`` (treated as "not found") when the reference does not
        resolve **or** the case falls outside this browser's scope — so a
        moderator can never reach another guild's case, and a member can never
        reach someone else's case, through the optional ``case`` parameter.
        """
        data = await self.bot.db.get_case_by_reference(reference)
        if not data:
            return False
        case = Case.from_db(data["case"], data["sanctions"], data["events"])

        if self.mode == "server":
            if not (case.scope_type.value == self.base_scope_type
                    and str(case.scope_id) == str(self.base_scope_id)):
                return False
        else:
            if not (case.subject_type.value == self.base_subject_type
                    and str(case.subject_id) == str(self.base_subject_id)):
                return False

        self.detail = case
        self._build_detail()
        return True

    # ----------------------------------------------------------- list screen
    def _build_list(self):
        self.clear_items()
        self.detail = None
        container = ui.Container(accent_colour=discord.Colour(COLORS["info"]))

        # Header — same heading for both modes ("Cases"), with the folders emoji.
        title = t("commands.cases.browser.title", locale=self.locale)
        if self.mode == "server":
            subtitle = t("commands.cases.browser.server_subtitle", locale=self.locale)
        else:
            subtitle = t("commands.cases.browser.user_subtitle", locale=self.locale)
        container.add_item(ui.TextDisplay(f"### {emojis.FOLDERS} {title}"))
        container.add_item(ui.TextDisplay(f"-# {subtitle}"))

        # Active-filters summary.
        summary = self._filter_summary()
        if summary:
            container.add_item(ui.TextDisplay(
                f"-# {emojis.FILTER} **{t('commands.cases.browser.active_filters', locale=self.locale)}:** {summary}"
            ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Results.
        if not self.rows:
            key = "empty_filtered" if self._has_active_filters() else "empty"
            container.add_item(ui.TextDisplay(
                f"{emojis.INFO} {t('commands.cases.browser.' + key, locale=self.locale)}"
            ))
        else:
            for row in self.rows:
                container.add_item(ui.TextDisplay(self._list_line(row)))

        # Footer: total count only — the page number lives in the nav bar.
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(
            f"-# {t('commands.cases.browser.results', locale=self.locale, count=self.total)}"
        ))

        self.add_item(container)

        # Components below the container (per design): the "open case" select,
        # then the pagination + filter buttons.
        total_pages = max(1, (self.total + PAGE_SIZE - 1) // PAGE_SIZE)

        if self.rows:
            open_row = ui.ActionRow()
            open_opts = []
            for row in self.rows:
                reason_preview = (row.get("reason") or "").replace("\n", " ")
                if self.mode == "server":
                    subject_id = row.get("subject_id")
                    if subject_id and row.get("subject_type") == "discord_user":
                        cached = self.bot.get_user(int(subject_id)) if self.bot else None
                        subject_str = f"@{cached.name}" if cached else f"@{subject_id}"
                    else:
                        subject_str = str(subject_id) if subject_id else "?"
                    context_str = subject_str
                else:
                    scope_type = row.get("scope_type")
                    scope_id = row.get("scope_id")
                    if scope_type == "discord_guild" and scope_id and self.bot:
                        guild = self.bot.get_guild(int(scope_id)) if str(scope_id).isdigit() else None
                        context_str = guild.name if guild else str(scope_id)
                    else:
                        context_str = str(scope_id) if scope_id else "?"
                combined = f"{context_str} • {reason_preview}" if reason_preview else context_str
                desc = combined[:100] or None
                open_opts.append(discord.SelectOption(
                    label=row["reference"],
                    value=str(row["id"]),
                    emoji=discord.PartialEmoji.from_str(emojis.FOLDER),
                    description=desc,
                ))
            open_sel = ui.Select(
                custom_id=_CID_LIST_OPEN.format(mode=self.mode),
                placeholder=t("commands.cases.browser.open_placeholder", locale=self.locale),
                options=open_opts, min_values=1, max_values=1,
            )
            open_sel.callback = self._on_open
            open_row.add_item(open_sel)
            self.add_item(open_row)

        # Pagination row — |<-| 1/3 |->|. The middle button is the page
        # indicator, disabled by design.
        nav = ui.ActionRow()
        prev_btn = ui.Button(
            custom_id=_CID_LIST_PREV.format(mode=self.mode),
            style=discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(emojis.BACK),
            disabled=self.page == 0,
        )
        prev_btn.callback = self._on_prev
        nav.add_item(prev_btn)

        page_indicator = ui.Button(
            custom_id=_CID_LIST_PAGE.format(mode=self.mode),
            style=discord.ButtonStyle.secondary,
            label=f"{self.page + 1}/{total_pages}",
            disabled=True,
        )
        nav.add_item(page_indicator)

        next_btn = ui.Button(
            custom_id=_CID_LIST_NEXT.format(mode=self.mode),
            style=discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(emojis.NEXT),
            disabled=self.page >= total_pages - 1,
        )
        next_btn.callback = self._on_next
        nav.add_item(next_btn)

        filters_btn = ui.Button(
            custom_id=_CID_LIST_FILTERS.format(mode=self.mode),
            style=discord.ButtonStyle.primary,
            label=t("commands.cases.browser.filters_button", locale=self.locale),
            emoji=discord.PartialEmoji.from_str(emojis.FILTER),
        )
        filters_btn.callback = self._on_filters
        nav.add_item(filters_btn)

        if self._has_active_filters():
            reset_btn = ui.Button(
                custom_id=_CID_LIST_RESET.format(mode=self.mode),
                style=discord.ButtonStyle.danger,
                label=t("commands.cases.browser.reset", locale=self.locale),
                emoji=discord.PartialEmoji.from_str(emojis.UNDONE),
            )
            reset_btn.callback = self._on_reset
            nav.add_item(reset_btn)

        self.add_item(nav)

    def _has_active_filters(self) -> bool:
        return any([
            self.f_status, self.f_action, self.f_period != "all",
            self.f_scope_id is not None, self.f_subject_id is not None,
        ])

    def _filter_summary(self) -> str:
        parts: List[str] = []
        if self.f_status:
            parts.append(t(f"commands.cases.status_value.{self.f_status}", locale=self.locale))
        if self.f_action:
            parts.append(t(f"commands.cases.action.{self.f_action}", locale=self.locale))
        if self.f_period != "all":
            parts.append(t(f"commands.cases.browser.period.{self.f_period}", locale=self.locale))
        if self.f_scope_id is not None:
            parts.append(self._scope_label(self.f_scope_type, self.f_scope_id))
        if self.f_subject_id is not None:
            parts.append(f"<@{self.f_subject_id}>")
        return " • ".join(parts)

    def _list_line(self, row: Dict[str, Any]) -> str:
        is_open = row["status"] == "open"
        dot = emojis.GREEN_STATUS if is_open else emojis.RED_STATUS
        type_emoji = _case_type_emoji_safe(row["type"])
        ref = row["reference"]
        actions = row.get("actions") or []
        labels = " ".join(_action_emoji_safe(a) for a in actions)

        # Prefix with type emoji only when non-empty (guild type has none).
        type_prefix = f"{type_emoji} " if type_emoji else ""

        if self.mode == "server":
            who = f"<@{row['subject_id']}>" if row["subject_type"] == "discord_user" else f"`{row['subject_id']}`"
            context = f"{t('commands.cases.browser.subject_user', locale=self.locale)}: {who}"
        else:
            context = f"{t('commands.cases.browser.in_server', locale=self.locale)} {self._scope_label(row['scope_type'], row.get('scope_id'))}"

        reason = (row.get("reason") or "").replace("\n", " ")
        if len(reason) > 80:
            reason = reason[:77] + "…"

        line = (
            f"{dot} {type_prefix}**`{ref}`** {labels}\n"
            f"-# {context} • {_ts(row.get('created_at'))}"
        )
        if reason:
            line += f"\n-# **{t('commands.cases.reason', locale=self.locale)}:** {reason}"
        return line

    def _scope_label(self, scope_type: Optional[str], scope_id: Optional[str], with_id: bool = False) -> str:
        if scope_type == "discord_guild" and scope_id:
            guild = self.bot.get_guild(int(scope_id)) if str(scope_id).isdigit() else None
            if guild:
                base = f"**{guild.name}**"
                return f"{base} (`{scope_id}`)" if with_id else base
            return t("commands.cases.browser.unknown_server", locale=self.locale, id=scope_id)
        if scope_type:
            return f"`{t('commands.cases.browser.scope.' + scope_type, locale=self.locale)}`"
        return "—"

    # --------------------------------------------------------- detail screen
    def _build_detail(self):
        self.clear_items()
        case = self.detail
        # Container accent reflects the case state (green = open, red = closed)
        # — same colour language as the list dots.
        accent = COLORS["success"] if case.is_open else COLORS["error"]
        container = ui.Container(accent_colour=discord.Colour(accent))

        # Header (folder emoji, no status dot before the field — the accent bar
        # already carries the state).
        container.add_item(ui.TextDisplay(
            f"### {emojis.FOLDER} {t('commands.cases.case_title', locale=self.locale, id=case.reference)}"
        ))
        container.add_item(ui.TextDisplay(
            f"**{t('commands.cases.status', locale=self.locale)}:** "
            f"`{t('commands.cases.status_value.' + case.status.value, locale=self.locale)}`"
        ))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Sidebar fields.
        fields = (
            f"**{t('commands.cases.type', locale=self.locale)}:** "
            f"`{t('commands.cases.type_value.' + case.type.value, locale=self.locale)}`\n"
        )
        if self.mode == "user":
            fields += (
                f"**{t('commands.cases.browser.scope_field', locale=self.locale)}:** "
                f"{self._scope_label(case.scope_type.value, case.scope_id, with_id=True)}\n"
            )
        if self.mode == "server":
            subj = f"<@{case.subject_id}>" if case.subject_type.value == "discord_user" else f"`{case.subject_id}`"
            fields += f"**{t('commands.cases.browser.subject_user', locale=self.locale)}:** {subj} (`{case.subject_id}`)\n"
            issued_label = t('commands.cases.browser.issued_by', locale=self.locale)
            if case.issuer_type.value == "automod":
                fields += f"**{issued_label}:** {emojis.SHIELD} Moddy Automod\n"
            elif case.issuer_id and case.issuer_type.value in ("discord_user", "moddy_staff"):
                fields += f"**{issued_label}:** <@{case.issuer_id}>\n"
        fields += f"**{t('commands.cases.opened', locale=self.locale)}:** {_ts(case.created_at, 'F')}"
        container.add_item(ui.TextDisplay(fields))
        container.add_item(ui.TextDisplay(
            f"**{t('commands.cases.reason', locale=self.locale)}:**\n{case.reason[:800]}"
        ))

        # Sanctions — rendered as ONE combined TextDisplay (not one child per
        # sanction) so a long-lived case with many sanctions can never blow past
        # the 40-component LayoutView ceiling. Newest first, capped.
        if case.sanctions:
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(ui.TextDisplay(f"**{t('commands.cases.sanctions', locale=self.locale)}**"))
            shown = list(reversed(case.sanctions))[:_MAX_DETAIL_SANCTIONS]
            lines: List[str] = []
            for s in shown:
                dot = emojis.GREEN_STATUS if s.status == SanctionStatus.ACTIVE else emojis.RED_STATUS
                action = t("commands.cases.action." + s.action.value, locale=self.locale)
                line = (
                    f"{dot} **{action}** • "
                    f"`{t('commands.cases.sanction_status.' + s.status.value, locale=self.locale)}`"
                )
                if s.expires_at and s.status == SanctionStatus.ACTIVE:
                    line += f" • {emojis.TIME} {_ts(s.expires_at)}"
                lines.append(line)
            hidden = len(case.sanctions) - len(shown)
            if hidden > 0:
                lines.append(f"-# {t('commands.cases.browser.more_items', locale=self.locale, count=hidden)}")
            container.add_item(ui.TextDisplay("\n".join(lines)))

        # Comments are private — only server moderators can see them. One
        # combined TextDisplay, capped, for the same child-count reason.
        if self.mode == "server":
            comments = [e for e in case.events if e.type == EventType.COMMENT]
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(ui.TextDisplay(f"**{t('commands.cases.comments', locale=self.locale)}**"))
            if comments:
                shown_c = comments[-_MAX_DETAIL_COMMENTS:]
                hidden_c = len(comments) - len(shown_c)
                blocks: List[str] = []
                if hidden_c > 0:
                    blocks.append(f"-# {t('commands.cases.browser.more_items', locale=self.locale, count=hidden_c)}")
                for e in shown_c:
                    author = f"<@{e.author_id}> • " if e.author_id else ""
                    body = (e.content or "").strip()
                    if len(body) > 300:
                        body = body[:297] + "…"
                    blocks.append(f"-# {author}{_ts(e.created_at)}\n{body}")
                container.add_item(ui.TextDisplay("\n".join(blocks)))
            else:
                container.add_item(ui.TextDisplay(f"-# {t('commands.cases.browser.no_comments', locale=self.locale)}"))

        has_evidence = any(e.type == EventType.EVIDENCE for e in case.events)

        # Server-mode action rows go INSIDE the container.
        if self.can_manage:
            # Row 1 — state-changing actions (no grey buttons).
            row1 = ui.ActionRow()

            add_btn = ui.Button(
                custom_id=_CID_DETAIL_ADD.format(mode=self.mode),
                style=discord.ButtonStyle.primary,
                label=t("commands.cases.browser.action_add_sanction", locale=self.locale),
                emoji=discord.PartialEmoji.from_str(emojis.ADD),
            )
            add_btn.callback = self._on_add_sanction
            row1.add_item(add_btn)

            revoke_btn = ui.Button(
                custom_id=_CID_DETAIL_REVOKE.format(mode=self.mode),
                style=discord.ButtonStyle.danger,
                label=t("commands.cases.browser.action_revoke", locale=self.locale),
                emoji=discord.PartialEmoji.from_str(emojis.UNDONE),
            )
            revoke_btn.callback = self._on_revoke
            row1.add_item(revoke_btn)

            toggle_label = "action_close" if case.is_open else "action_reopen"
            toggle_btn = ui.Button(
                custom_id=_CID_DETAIL_TOGGLE.format(mode=self.mode),
                style=discord.ButtonStyle.success,
                label=t(f"commands.cases.browser.{toggle_label}", locale=self.locale),
                emoji=discord.PartialEmoji.from_str(emojis.DONE if case.is_open else emojis.SYNC),
            )
            toggle_btn.callback = self._on_toggle_status
            row1.add_item(toggle_btn)

            container.add_item(row1)

            # Row 2 — non-destructive case-content actions.
            row2 = ui.ActionRow()

            comment_btn = ui.Button(
                custom_id=_CID_DETAIL_COMMENT.format(mode=self.mode),
                style=discord.ButtonStyle.primary,
                label=t("commands.cases.browser.action_comment", locale=self.locale),
                emoji=discord.PartialEmoji.from_str(emojis.MESSAGE),
            )
            comment_btn.callback = self._on_comment
            row2.add_item(comment_btn)

            edit_btn = ui.Button(
                custom_id=_CID_DETAIL_EDIT.format(mode=self.mode),
                style=discord.ButtonStyle.primary,
                label=t("commands.cases.browser.action_edit", locale=self.locale),
                emoji=discord.PartialEmoji.from_str(emojis.EDIT),
            )
            edit_btn.callback = self._on_edit
            row2.add_item(edit_btn)

            evidence_btn = ui.Button(
                custom_id=_CID_DETAIL_EVIDENCE.format(mode=self.mode),
                style=discord.ButtonStyle.primary,
                label=t("commands.cases.browser.action_evidence", locale=self.locale),
                emoji=discord.PartialEmoji.from_str(emojis.IMAGE),
                disabled=not has_evidence,
            )
            evidence_btn.callback = self._on_evidence
            row2.add_item(evidence_btn)

            container.add_item(row2)

        self.add_item(container)

        # Nav row — Back to list only (outside the container).
        nav_row = ui.ActionRow()
        back_btn = ui.Button(
            custom_id=_CID_DETAIL_BACK.format(mode=self.mode),
            style=discord.ButtonStyle.secondary,
            label=t("commands.cases.browser.back", locale=self.locale),
            emoji=discord.PartialEmoji.from_str(emojis.BACK),
        )
        back_btn.callback = self._on_back
        nav_row.add_item(back_btn)

        # In user mode, evidence button is in the nav row (read-only view).
        if not self.can_manage:
            evidence_btn = ui.Button(
                custom_id=_CID_DETAIL_EVIDENCE.format(mode=self.mode),
                style=discord.ButtonStyle.secondary,
                label=t("commands.cases.browser.action_evidence", locale=self.locale),
                emoji=discord.PartialEmoji.from_str(emojis.IMAGE),
                disabled=not has_evidence,
            )
            evidence_btn.callback = self._on_evidence
            nav_row.add_item(evidence_btn)

        self.add_item(nav_row)

    # --------------------------------------------------------------- guards
    async def _guard(self, interaction: discord.Interaction) -> bool:
        # On a fresh shell (post-restart), viewer_id is unknown — accept the
        # click; the per-mode auth is then enforced by the rehydrate flow
        # (user mode anchors to interaction.user.id, server mode re-checks
        # Manage Messages from interaction.user).
        if self.viewer_id is None:
            return True
        if interaction.user.id != self.viewer_id:
            await interaction.response.send_message(
                t("commands.cases.not_yours", locale=self.locale), ephemeral=True)
            return False
        return True

    # --------------------------------------------------- list callbacks
    async def _on_prev(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        if self.bot is None:
            await self._rehydrate_and_list(interaction)
            return
        self.page = max(0, self.page - 1)
        await self.refresh(interaction)

    async def _on_next(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        if self.bot is None:
            await self._rehydrate_and_list(interaction)
            return
        self.page += 1
        await self.refresh(interaction)

    async def _on_reset(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        if self.bot is None:
            await self._rehydrate_and_list(interaction)
            return
        self.f_status = None
        self.f_action = None
        self.f_period = "all"
        self.f_scope_type = None
        self.f_scope_id = None
        self.f_subject_id = None
        self.page = 0
        await self.refresh(interaction)

    async def _on_filters(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        if self.bot is None:
            await self._rehydrate_and_list(interaction)
            return
        await interaction.response.send_modal(CaseFilterModal(self))

    async def _on_open(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        case_id = interaction.data["values"][0]
        if self.bot is None:
            await self._rehydrate_and_detail(interaction, case_id)
            return
        await self.show_detail(interaction, case_id)

    # ------------------------------------------------- detail callbacks
    async def _on_back(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        if self.bot is None:
            await self._rehydrate_and_list(interaction)
            return
        await self.refresh(interaction)

    async def _require_manage(self, interaction: discord.Interaction) -> bool:
        """Re-check the baseline (Manage Messages) before any mutation."""
        member = interaction.user
        if isinstance(member, discord.Member) and can_view_server_cases(member):
            return True
        await interaction.response.send_message(view=_perm_error(self.locale), ephemeral=True)
        return False

    async def _on_comment(self, interaction: discord.Interaction):
        if not await self._guard(interaction) or not await self._require_manage(interaction):
            return
        if self.detail is None:
            await self._rehydrate_and_list(interaction)
            return
        await interaction.response.send_modal(
            CaseCommentModalV2(self, self.detail))

    async def _on_edit(self, interaction: discord.Interaction):
        if not await self._guard(interaction) or not await self._require_manage(interaction):
            return
        if self.detail is None:
            await self._rehydrate_and_list(interaction)
            return
        await interaction.response.send_modal(
            CaseEditReasonModalV2(self, self.detail))

    async def _on_add_sanction(self, interaction: discord.Interaction):
        if not await self._guard(interaction) or not await self._require_manage(interaction):
            return
        if self.detail is None:
            await self._rehydrate_and_list(interaction)
            return
        # Only offer sanction kinds this member is actually allowed to issue.
        allowed = [
            a for a in get_available_actions(self.detail.type)
            if _can_issue_action(interaction.user, a)
        ]
        if not allowed:
            await interaction.response.send_message(
                t("commands.cases.browser.no_permitted_actions", locale=self.locale),
                ephemeral=True)
            return
        await interaction.response.send_modal(
            CaseAddSanctionModalV2(self, self.detail, allowed))

    async def _on_revoke(self, interaction: discord.Interaction):
        if not await self._guard(interaction) or not await self._require_manage(interaction):
            return
        if self.detail is None:
            await self._rehydrate_and_list(interaction)
            return
        active = [s for s in self.detail.sanctions if s.status == SanctionStatus.ACTIVE]
        if not active:
            await interaction.response.send_message(
                t("commands.cases.browser.no_active_sanctions", locale=self.locale),
                ephemeral=True)
            return
        # Only sanctions whose kind this member may lift.
        permitted = [s for s in active if _can_issue_action(interaction.user, s.action)]
        if not permitted:
            await interaction.response.send_message(
                t("commands.cases.browser.no_permitted_actions", locale=self.locale),
                ephemeral=True)
            return
        await interaction.response.send_modal(
            CaseRevokeModalV2(self, self.detail, permitted))

    async def _on_toggle_status(self, interaction: discord.Interaction):
        if not await self._guard(interaction) or not await self._require_manage(interaction):
            return
        if self.detail is None:
            await self._rehydrate_and_list(interaction)
            return
        case = self.detail
        new_status = "closed" if case.is_open else "open"
        await self.bot.db.set_status_manual(
            _uuid.UUID(case.id), new_status, "discord_user", interaction.user.id,
        )
        await self.show_detail(interaction, case.id)

    async def _on_evidence(self, interaction: discord.Interaction):
        """Send the evidence attached to the case as an ephemeral followup.

        Evidence lives in the timeline as ``evidence`` events whose ``payload``
        carries a URL (and optional kind). When the URL looks like media we add
        it to a ``MediaGallery``; everything else is listed as a clickable URL.
        """
        if not await self._guard(interaction):
            return
        if self.detail is None:
            await self._rehydrate_and_list(interaction)
            return
        case = self.detail
        evidence = [e for e in case.events if e.type == EventType.EVIDENCE]
        urls = []
        for e in evidence:
            url = (e.payload or {}).get("url")
            if url:
                urls.append((url, (e.payload or {}).get("kind") or "evidence", e.created_at))

        view = BaseView()
        container = ui.Container(accent_colour=discord.Colour(COLORS["info"]))
        container.add_item(ui.TextDisplay(
            f"### {emojis.IMAGE} {t('commands.cases.browser.evidence_title', locale=self.locale, id=case.reference)}"
        ))
        if not urls:
            container.add_item(ui.TextDisplay(
                f"-# {t('commands.cases.browser.no_evidence', locale=self.locale)}"
            ))
            view.add_item(container)
            await interaction.response.send_message(view=view, ephemeral=True)
            return

        media_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".webm", ".mov")
        media_urls = [u for u, _, _ in urls if u.lower().split("?")[0].endswith(media_exts)]
        link_lines = [
            f"-# `{kind}` • {_ts(at)}\n[{url}]({url})"
            for url, kind, at in urls if url not in media_urls
        ]
        if link_lines:
            container.add_item(ui.TextDisplay("\n".join(link_lines)))
        view.add_item(container)

        if media_urls:
            gallery = ui.MediaGallery(
                *[discord.MediaGalleryItem(media=u) for u in media_urls[:10]]
            )
            view.add_item(gallery)

        await interaction.response.send_message(view=view, ephemeral=True)


# --------------------------------------------------------------------------- #
# Filter modal (Modals V2)
# --------------------------------------------------------------------------- #

class CaseFilterModal(BaseModal):
    """Pick the active filters for a :class:`CasesBrowserView`."""

    def __init__(self, browser: CasesBrowserView):
        loc = browser.locale
        super().__init__(
            title=t("commands.cases.browser.filters_title", locale=loc)[:45],
            timeout=300,
        )
        self.browser = browser

        # Status.
        status_opts = [
            discord.SelectOption(
                label=t("commands.cases.browser.all_status", locale=loc),
                value="all", default=browser.f_status is None,
            ),
            discord.SelectOption(
                label=t("commands.cases.status_value.open", locale=loc),
                value="open", default=browser.f_status == "open",
            ),
            discord.SelectOption(
                label=t("commands.cases.status_value.closed", locale=loc),
                value="closed", default=browser.f_status == "closed",
            ),
        ]
        self.status_label = ui.Label(
            text=t("commands.cases.browser.status_label", locale=loc)[:45],
            component=ui.Select(options=status_opts, min_values=1, max_values=1),
        )
        self.add_item(self.status_label)

        # Sanction type.
        action_opts = [discord.SelectOption(
            label=t("commands.cases.browser.all_actions", locale=loc),
            value="all", default=browser.f_action is None,
        )]
        for a in SanctionAction:
            action_opts.append(discord.SelectOption(
                label=t(f"commands.cases.action.{a.value}", locale=loc),
                value=a.value,
                emoji=discord.PartialEmoji.from_str(get_action_emoji(a)),
                default=browser.f_action == a.value,
            ))
        self.action_label = ui.Label(
            text=t("commands.cases.browser.action_label", locale=loc)[:45],
            component=ui.Select(options=action_opts, min_values=1, max_values=1),
        )
        self.add_item(self.action_label)

        # Period.
        period_opts = [discord.SelectOption(
            label=t("commands.cases.browser.all_periods", locale=loc),
            value="all", default=browser.f_period == "all",
        )]
        for value in ("1d", "7d", "30d", "90d"):
            period_opts.append(discord.SelectOption(
                label=t(f"commands.cases.browser.period.{value}", locale=loc),
                value=value, default=browser.f_period == value,
            ))
        self.period_label = ui.Label(
            text=t("commands.cases.browser.period_label", locale=loc)[:45],
            component=ui.Select(options=period_opts, min_values=1, max_values=1),
        )
        self.add_item(self.period_label)

        # Context filter.
        self.scope_label = None
        self.user_label = None
        if browser.mode == "user" and browser.scopes:
            scope_opts = [discord.SelectOption(
                label=t("commands.cases.browser.all_servers", locale=loc),
                value="all", default=browser.f_scope_id is None,
            )]
            for sc in browser.scopes[:24]:
                stype = sc["scope_type"]
                sid = sc.get("scope_id")
                if stype == "discord_guild" and sid:
                    guild = browser.bot.get_guild(int(sid)) if str(sid).isdigit() else None
                    label = guild.name if guild else t(
                        "commands.cases.browser.unknown_server", locale=loc, id=sid)
                else:
                    label = t(f"commands.cases.browser.scope.{stype}", locale=loc)
                scope_opts.append(discord.SelectOption(
                    label=label[:100],
                    value=f"{stype}:{sid}"[:100],
                    default=(browser.f_scope_type == stype and str(browser.f_scope_id) == str(sid)),
                ))
            self.scope_label = ui.Label(
                text=t("commands.cases.browser.server_label", locale=loc)[:45],
                component=ui.Select(options=scope_opts, min_values=1, max_values=1),
            )
            self.add_item(self.scope_label)
        elif browser.mode == "server":
            self.user_label = ui.Label(
                text=t("commands.cases.browser.user_label", locale=loc)[:45],
                description=t("commands.cases.browser.user_label_hint", locale=loc)[:100],
                component=ui.UserSelect(min_values=0, max_values=1, required=False),
            )
            self.add_item(self.user_label)

    async def on_submit(self, interaction: discord.Interaction):
        b = self.browser

        status = self.status_label.component.values[0]
        b.f_status = None if status == "all" else status

        action = self.action_label.component.values[0]
        b.f_action = None if action == "all" else action

        b.f_period = self.period_label.component.values[0]

        if self.scope_label is not None:
            scope = self.scope_label.component.values[0]
            if scope == "all":
                b.f_scope_type = None
                b.f_scope_id = None
            else:
                stype, _, sid = scope.partition(":")
                b.f_scope_type = stype
                b.f_scope_id = sid
        if self.user_label is not None:
            users = self.user_label.component.values
            b.f_subject_id = users[0].id if users else None

        b.page = 0
        await b.refresh(interaction)


# --------------------------------------------------------------------------- #
# Server-side mutation modals (author = the guild moderator)
# --------------------------------------------------------------------------- #

class CaseCommentModalV2(BaseModal):
    """Append a public comment to a case (timeline ``comment`` event)."""

    def __init__(self, browser: CasesBrowserView, case: Case):
        loc = browser.locale
        super().__init__(
            title=t("commands.cases.browser.action_comment", locale=loc)[:45],
            timeout=300,
        )
        self.browser = browser
        self.case = case
        self.text = ui.Label(
            text=t("commands.cases.browser.comment_prompt", locale=loc)[:45],
            component=ui.TextInput(
                style=discord.TextStyle.paragraph, required=True, max_length=1500,
            ),
        )
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction):
        content = (self.text.component.value or "").strip()
        if content:
            await self.browser.bot.db.add_event(
                _uuid.UUID(self.case.id), "comment",
                author_type="discord_user", author_id=interaction.user.id,
                content=content,
            )
        await self.browser.show_detail(interaction, self.case.id)


class CaseEditReasonModalV2(BaseModal):
    """Edit the reason of a case."""

    def __init__(self, browser: CasesBrowserView, case: Case):
        loc = browser.locale
        super().__init__(
            title=t("commands.cases.browser.action_edit", locale=loc)[:45],
            timeout=300,
        )
        self.browser = browser
        self.case = case
        self.reason = ui.Label(
            text=t("commands.cases.browser.reason_prompt", locale=loc)[:45],
            component=ui.TextInput(
                style=discord.TextStyle.paragraph,
                default=(case.reason or "")[:1500],
                required=True, max_length=1500,
            ),
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        new_reason = (self.reason.component.value or "").strip()
        if new_reason and new_reason != self.case.reason:
            await self.browser.bot.db.update_case_reason(
                _uuid.UUID(self.case.id), new_reason)
        await self.browser.show_detail(interaction, self.case.id)


def _parse_duration_hours(raw: Optional[str]) -> Optional[datetime]:
    """Parse an hours value into an absolute UTC ``expires_at`` (or None).

    Raises ``ValueError`` on a non-numeric, non-empty value.
    """
    if not raw or not raw.strip():
        return None
    hours = float(raw.strip())
    if hours <= 0:
        return None
    return datetime.now(timezone.utc) + timedelta(hours=hours)


class CaseAddSanctionModalV2(BaseModal):
    """Add a sanction to a case (records on the folder, author = moderator)."""

    def __init__(self, browser: CasesBrowserView, case: Case, allowed: list):
        loc = browser.locale
        super().__init__(
            title=t("commands.cases.browser.add_sanction_title", locale=loc)[:45],
            timeout=300,
        )
        self.browser = browser
        self.case = case

        action_opts = [
            discord.SelectOption(
                label=t(f"commands.cases.action.{a.value}", locale=loc),
                value=a.value,
                emoji=discord.PartialEmoji.from_str(get_action_emoji(a)),
            )
            for a in allowed
        ]
        self.action_label = ui.Label(
            text=t("commands.cases.browser.sanction_action_label", locale=loc)[:45],
            component=ui.Select(options=action_opts, min_values=1, max_values=1),
        )
        self.add_item(self.action_label)

        self.note = ui.Label(
            text=t("commands.cases.browser.sanction_note_label", locale=loc)[:45],
            component=ui.TextInput(
                style=discord.TextStyle.paragraph, required=False, max_length=1000,
            ),
        )
        self.add_item(self.note)

        self.duration = ui.Label(
            text=t("commands.cases.browser.sanction_duration_label", locale=loc)[:45],
            description=t("commands.cases.browser.sanction_duration_hint", locale=loc)[:100],
            component=ui.TextInput(
                style=discord.TextStyle.short, required=False, max_length=10,
                placeholder="24",
            ),
        )
        self.add_item(self.duration)

    async def on_submit(self, interaction: discord.Interaction):
        action = SanctionAction(self.action_label.component.values[0])

        if not isinstance(interaction.user, discord.Member) or not _can_issue_action(interaction.user, action):
            await interaction.response.send_message(view=_perm_error(self.browser.locale), ephemeral=True)
            return

        expires_at = None
        if action in TEMPORARY_ACTIONS:
            try:
                expires_at = _parse_duration_hours(self.duration.component.value)
            except ValueError:
                await interaction.response.send_message(
                    t("commands.cases.browser.invalid_duration", locale=self.browser.locale),
                    ephemeral=True)
                return

        note = (self.note.component.value or "").strip() or None
        await self.browser.bot.db.add_sanction(
            _uuid.UUID(self.case.id), action.value, "discord_user",
            interaction.user.id, expires_at=expires_at, note=note,
        )
        await self.browser.show_detail(interaction, self.case.id)


class CaseRevokeModalV2(BaseModal):
    """Revoke one active sanction of a case (author = moderator)."""

    def __init__(self, browser: CasesBrowserView, case: Case, active: list):
        loc = browser.locale
        super().__init__(
            title=t("commands.cases.browser.revoke_title", locale=loc)[:45],
            timeout=300,
        )
        self.browser = browser
        self.case = case

        opts = []
        for s in active[:25]:
            opts.append(discord.SelectOption(
                label=t(f"commands.cases.action.{s.action.value}", locale=loc),
                value=str(s.id),
                description=str(s.id)[:8],
                emoji=discord.PartialEmoji.from_str(get_action_emoji(s.action)),
            ))
        self.sanction_label = ui.Label(
            text=t("commands.cases.browser.revoke_select_label", locale=loc)[:45],
            component=ui.Select(options=opts, min_values=1, max_values=1),
        )
        self.add_item(self.sanction_label)

    async def on_submit(self, interaction: discord.Interaction):
        sanction_id = _uuid.UUID(self.sanction_label.component.values[0])
        # Re-check the specific permission for the targeted sanction's action.
        target = next((s for s in self.case.sanctions if str(s.id) == str(sanction_id)), None)
        if target is not None and (
            not isinstance(interaction.user, discord.Member)
            or not _can_issue_action(interaction.user, target.action)
        ):
            await interaction.response.send_message(view=_perm_error(self.browser.locale), ephemeral=True)
            return
        await self.browser.bot.db.revoke_sanction(
            sanction_id, "discord_user", interaction.user.id)
        await self.browser.show_detail(interaction, self.case.id)
