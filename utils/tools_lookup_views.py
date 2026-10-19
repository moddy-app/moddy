"""
Views of the lookup utility tools (``/status``, ``/company``, ``/username``) —
see docs/features/TOOLS_LOOKUP.md.

Cards are built with ``utils/tool_card.py``. Every interactive component is
persistent (docs/guides/PERSISTENT_VIEWS.md) and stateless where it can be:

* ``/status`` — the overview select and its Refresh button have fixed
  ``custom_id`` s (the service key is the option value); the per-service
  Refresh button carries the key in a ``DynamicItem``.
* ``/company`` — the results select has a fixed ``custom_id``; each option's
  value is the SIREN, so a click re-queries the company and nothing is stored.
* ``/username`` — the result list does not fit in a ``custom_id``: it is kept
  one hour under a short token (``save_state``) that the page / export buttons
  carry. After that, a click answers "run the command again".
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import discord
from discord import ui

from cogs.error_handler import BaseView
from tools import company as co
from tools import status_registry as sr
from tools import whatsmyname as wmn
from utils import emojis as em
from utils import tool_card as tc
from utils.automod_render import make_text_file
from utils.i18n import i18n, t

logger = logging.getLogger("moddy.tools_lookup")

STATE_KIND_USERNAME = "username"

# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def message_requester(interaction: discord.Interaction) -> Optional[int]:
    """Who ran the command that produced the clicked message (public ones only).

    A persistent callback has no memory of the original command; Discord keeps
    it in the message's interaction metadata. Ephemeral answers show no
    "requested by" part, so ``None`` is returned for them.
    """
    message = interaction.message
    if message is None or message.flags.ephemeral:
        return None
    user = getattr(getattr(message, "interaction_metadata", None), "user", None)
    return getattr(user, "id", None)


def _locale(interaction: discord.Interaction) -> str:
    return i18n.get_user_locale(interaction)


def _api(interaction: discord.Interaction):
    api = getattr(interaction.client, "public_api", None)
    if api is None:
        raise tc.ToolError("unavailable")
    return api


def _rel(epoch: Optional[float]) -> str:
    return tc.discord_timestamp(epoch, "R") if epoch else ""


# =========================================================================== #
# /status
# =========================================================================== #

_INDICATOR_ACCENT = {
    sr.IND_NONE: tc.ACCENT_SUCCESS,
    sr.IND_MINOR: tc.ACCENT_WARNING,
    sr.IND_MAJOR: tc.ACCENT_ERROR,
    sr.IND_CRITICAL: tc.ACCENT_ERROR,
    sr.IND_MAINTENANCE: tc.ACCENT_INFO,
    sr.IND_UNKNOWN: tc.ACCENT_NEUTRAL,
}


def indicator_label(indicator: str, locale: str) -> str:
    key = indicator if indicator in sr.INDICATOR_ORDER else sr.IND_UNKNOWN
    return t(f"tools.status.states.{key}", locale=locale)


def component_label(status: str, locale: str) -> str:
    text = i18n.get(f"tools.status.components.{status}", locale=locale)
    return text if not text.startswith("[") else status.replace("_", " ")


def _with_dot(dot: str, text: str) -> str:
    return tc.with_icon(dot, text)


def service_line(service: sr.Service, summary: sr.ServiceSummary, locale: str) -> str:
    """``<dot> **Name** · state`` (+ the headline incident when there is one)."""
    state = indicator_label(summary.indicator, locale)
    line = _with_dot(sr.indicator_dot(summary.indicator), f"**{service.name}** · {state}")
    if summary.incidents:
        headline, _ = tc.truncate(tc.escape_markdown(summary.incidents[0].name), 60)
        line += f" · {headline}"
    return line


def overview_summary_text(stats: sr.OverviewStats, locale: str) -> str:
    """One sentence: everything fine, or what is wrong / in maintenance / unknown."""
    parts: List[str] = []
    if stats.problems:
        parts.append(t("tools.status.summary.problems", locale=locale,
                       problems=stats.problems, total=stats.total))
    if stats.maintenance:
        parts.append(t("tools.status.summary.maintenance", locale=locale, count=stats.maintenance))
    if stats.unknown:
        parts.append(t("tools.status.summary.unknown", locale=locale, count=stats.unknown))
    if not parts:
        return t("tools.status.summary.ok", locale=locale, total=stats.total)
    return " ".join(parts)


def _overview_accent(stats: sr.OverviewStats) -> int:
    if stats.problems:
        return _INDICATOR_ACCENT.get(stats.worst, tc.ACCENT_WARNING)
    if stats.maintenance:
        return tc.ACCENT_INFO
    if stats.unknown == stats.total:
        return tc.ACCENT_NEUTRAL
    return tc.ACCENT_SUCCESS


class StatusOverviewSelect(ui.Select):
    """Open the detail of one service (the option value is the service key)."""

    def __init__(self, summaries: Optional[Dict[str, sr.ServiceSummary]] = None, locale: str = "en-US"):
        options = []
        for service in sr.SERVICES:
            summary = (summaries or {}).get(service.key)
            options.append(discord.SelectOption(
                label=service.name, value=service.key,
                description=indicator_label(summary.indicator, locale) if summary else None,
                emoji=tc.button_emoji(service.logo)))
        super().__init__(
            custom_id="moddy:tools:status:detail",
            placeholder=t("tools.status.pick_placeholder", locale=locale)[:150],
            options=options, min_values=1, max_values=1)

    @tc.guarded
    async def callback(self, interaction: discord.Interaction):
        locale = _locale(interaction)
        service = sr.get_service(self.values[0])
        if service is None:
            raise tc.ToolError("not_found", tool="status", query=self.values[0])
        await interaction.response.defer(ephemeral=True, thinking=True)
        summary = await sr.fetch_summary(_api(interaction), service)
        await interaction.followup.send(
            view=build_status_detail(service, summary, locale), ephemeral=True,
            allowed_mentions=tc.NO_MENTIONS)


class StatusRefreshAllButton(ui.Button):
    def __init__(self, locale: str = "en-US"):
        super().__init__(label=t("tools.common.buttons.refresh", locale=locale),
                         style=discord.ButtonStyle.secondary,
                         emoji=tc.button_emoji(em.REFRESH),
                         custom_id="moddy:tools:status:refresh")

    @tc.guarded
    async def callback(self, interaction: discord.Interaction):
        locale = _locale(interaction)
        await interaction.response.defer()
        summaries = await sr.fetch_all(_api(interaction), force=True)
        await interaction.edit_original_response(
            view=build_status_overview(summaries, locale, requester_id=message_requester(interaction)),
            allowed_mentions=tc.NO_MENTIONS)


class StatusOverviewView(BaseView):
    """The overview message: card + service select + Refresh."""

    __persistent__ = True

    def __init__(self, card: Optional[tc.ToolCard] = None,
                 summaries: Optional[Dict[str, sr.ServiceSummary]] = None, locale: str = "en-US"):
        super().__init__()
        if card is not None:
            self.add_item(card.container)
        self.add_item(ui.ActionRow(StatusOverviewSelect(summaries, locale)))
        self.add_item(ui.ActionRow(StatusRefreshAllButton(locale)))

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_view(cls())


def build_status_overview(summaries: Sequence[sr.ServiceSummary], locale: str, *,
                          requester_id: Optional[int] = None) -> StatusOverviewView:
    ordered = sr.overview_order(summaries)
    stats = sr.overview_stats(summaries)
    lines = [service_line(sr.BY_KEY[s.key], s, locale) for s in ordered if s.key in sr.BY_KEY]
    fetched = max((s.fetched_at for s in summaries), default=time.time())

    card = tc.ToolCard(icon=em.MONITOR_HEART, title=t("tools.status.title", locale=locale),
                       accent=_overview_accent(stats))
    card.text(f"**{overview_summary_text(stats, locale)}**")
    card.text("\n".join(lines))
    card.footer(
        tc.source_line(locale, (em.ATLASSIAN, "Atlassian Statuspage"), verb="provided_by"),
        t("tools.status.updated", locale=locale, time=_rel(fetched)) if fetched else None,
        tc.requested_by(locale, requester_id))
    return StatusOverviewView(card, {s.key: s for s in summaries}, locale)


# -- one service ------------------------------------------------------------ #

#: Lines kept in each section of a detail card (the rest is summarised).
MAX_GROUP_LINES = 10
MAX_INCIDENTS = 3
MAX_MAINTENANCES = 3
MAX_NAMES_PER_GROUP = 6
INCIDENT_UPDATE_CHARS = 280


def _components_section(summary: sr.ServiceSummary, locale: str) -> Optional[str]:
    if not summary.issues:
        return None
    groups = sr.group_issues(summary.issues)
    lines: List[str] = []
    for group in groups[:MAX_GROUP_LINES]:
        dot = sr.component_dot(group.status)
        if group.name is None:
            item = group.items[0]
            lines.append(_with_dot(dot, f"{tc.escape_markdown(item.name)} · "
                                        f"{component_label(item.status, locale)}"))
            continue
        names = [tc.escape_markdown(i.name) for i in group.items]
        shown = ", ".join(names[:MAX_NAMES_PER_GROUP])
        if len(names) > MAX_NAMES_PER_GROUP:
            shown += ", " + t("tools.status.more", locale=locale, count=len(names) - MAX_NAMES_PER_GROUP)
        lines.append(_with_dot(dot, f"**{tc.escape_markdown(group.name)}** · "
                                    f"{component_label(group.status, locale)}\n-# {shown}"))
    if len(groups) > MAX_GROUP_LINES:
        lines.append(f"-# {t('tools.status.more', locale=locale, count=len(groups) - MAX_GROUP_LINES)}")
    return f"**{t('tools.status.sections.components', locale=locale)}**\n" + "\n".join(lines)


def _incidents_section(summary: sr.ServiceSummary, locale: str) -> Optional[str]:
    if not summary.incidents:
        return None
    blocks: List[str] = []
    for incident in summary.incidents[:MAX_INCIDENTS]:
        status = i18n.get(f"tools.status.incident_status.{incident.status}", locale=locale)
        if status.startswith("["):
            status = incident.status.replace("_", " ")
        meta = status
        if incident.started_at:
            meta += " · " + t("tools.status.since", locale=locale, time=_rel(incident.started_at))
        block = f"{_with_dot(sr.impact_dot(incident.impact), '**' + tc.escape_markdown(incident.name) + '**')}\n-# {meta}"
        if incident.update:
            update, _ = tc.truncate(tc.escape_markdown(incident.update.replace("\n", " ")),
                                    INCIDENT_UPDATE_CHARS)
            block += f"\n> {update}"
        blocks.append(block)
    if len(summary.incidents) > MAX_INCIDENTS:
        blocks.append(f"-# {t('tools.status.more', locale=locale, count=len(summary.incidents) - MAX_INCIDENTS)}")
    return f"**{t('tools.status.sections.incidents', locale=locale)}**\n" + "\n".join(blocks)


def _maintenance_section(summary: sr.ServiceSummary, locale: str) -> Optional[str]:
    if not summary.maintenances:
        return None
    blocks: List[str] = []
    for item in summary.maintenances[:MAX_MAINTENANCES]:
        if item.status == "in_progress":
            when = t("tools.status.maintenance_now", locale=locale,
                     time=tc.discord_timestamp(item.end, "R") if item.end else "—")
        elif item.start:
            when = t("tools.status.maintenance_planned", locale=locale,
                     start=tc.discord_timestamp(item.start, "f"),
                     end=tc.discord_timestamp(item.end, "t") if item.end else "—")
        else:
            when = ""
        title = _with_dot(em.BLUE_STATUS, f"**{tc.escape_markdown(item.name)}**")
        blocks.append(f"{title}\n-# {when}" if when else title)
    if len(summary.maintenances) > MAX_MAINTENANCES:
        blocks.append(f"-# {t('tools.status.more', locale=locale, count=len(summary.maintenances) - MAX_MAINTENANCES)}")
    return f"**{t('tools.status.sections.maintenance', locale=locale)}**\n" + "\n".join(blocks)


class StatusRefreshButton(
    ui.DynamicItem[ui.Button],
    template=r"moddy:tools:status:refresh:(?P<key>[a-z0-9]{1,24})",
):
    """Refresh one service's detail card in place (the key is in the custom_id)."""

    def __init__(self, key: str, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=t("tools.common.buttons.refresh", locale=locale),
            style=discord.ButtonStyle.secondary, emoji=tc.button_emoji(em.REFRESH),
            custom_id=f"moddy:tools:status:refresh:{key}"))
        self.key = key

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match: re.Match):
        return cls(match["key"])

    @tc.guarded
    async def callback(self, interaction: discord.Interaction):
        locale = _locale(interaction)
        service = sr.get_service(self.key)
        if service is None:
            raise tc.ToolError("not_found", tool="status", query=self.key)
        await interaction.response.defer()
        summary = await sr.fetch_summary(_api(interaction), service, force=True)
        await interaction.edit_original_response(
            view=build_status_detail(service, summary, locale,
                                     requester_id=message_requester(interaction)),
            allowed_mentions=tc.NO_MENTIONS)


def build_status_detail(service: sr.Service, summary: sr.ServiceSummary, locale: str, *,
                        requester_id: Optional[int] = None) -> ui.LayoutView:
    """The card of one service (incidents, degraded components, maintenance)."""
    accent = _INDICATOR_ACCENT.get(summary.indicator, tc.ACCENT_NEUTRAL)
    card = tc.ToolCard(icon=service.logo or em.MONITOR_HEART, title=service.name, accent=accent)
    state = indicator_label(summary.indicator, locale)
    card.text(f"{_with_dot(sr.indicator_dot(summary.indicator), f'**{state}**')}")

    if not summary.reachable:
        card.text(t("tools.status.unreachable", locale=locale, name=service.name))
    else:
        sections = [
            _components_section(summary, locale),
            _incidents_section(summary, locale),
            _maintenance_section(summary, locale),
        ]
        sections = [s for s in sections if s]
        if sections:
            for section in sections:
                card.text(section)
        elif summary.indicator == sr.IND_NONE:
            card.text(t("tools.status.all_good", locale=locale, count=summary.components_total))

    card.footer(
        tc.source_line(locale, (service.logo, f"{service.name} Status"), verb="provided_by"),
        (t("tools.status.updated", locale=locale, time=_rel(summary.fetched_at))
         if summary.fetched_at else None),
        tc.requested_by(locale, requester_id))

    row = ui.ActionRow(StatusRefreshButton(service.key, locale))
    page = summary.page_url or service.base_url
    row.add_item(tc.link_button(t("tools.status.open_page", locale=locale), page, icon=em.WEB))
    return card.build(view=BaseView(), rows=[row])


# =========================================================================== #
# /company
# =========================================================================== #


def company_status_text(company: co.Company, locale: str) -> str:
    if company.active:
        return t("tools.company.status.active", locale=locale)
    if company.closed_on:
        return t("tools.company.status.closed_on", locale=locale, date=tc.discord_timestamp(
            datetime(company.closed_on.year, company.closed_on.month, company.closed_on.day,
                     tzinfo=timezone.utc).timestamp(), "D"))
    return t("tools.company.status.closed", locale=locale)


def age_text(created: date, today: date, locale: str) -> str:
    """``14 years and 7 months`` (singular / plural per unit, ``and`` per language)."""
    years, months = co.age_parts(created, today)
    parts = []
    for unit, count in (("years", years), ("months", months)):
        if count:
            form = "one" if count == 1 else "other"
            parts.append(t(f"tools.company.age.{unit}_{form}", locale=locale, count=count))
    if not parts:
        return t("tools.company.age.new", locale=locale)
    return f" {t('tools.company.age.and', locale=locale)} ".join(parts)


def headcount_text(company: co.Company, locale: str) -> Optional[str]:
    bracket = co.headcount_bracket(company.headcount_code)
    if bracket is None:
        return None
    kind, low, high = bracket
    text = t(f"tools.company.headcount.{kind}", locale=locale, low=tc.format_number(low, locale),
             high=tc.format_number(high, locale))
    if company.headcount_year:
        text += " " + t("tools.company.headcount.year", locale=locale, year=company.headcount_year)
    return text


def _address(est: Optional[co.Establishment]) -> str:
    if est is None:
        return ""
    if est.address:
        return est.address
    return f"{est.postal_code} {est.city}".strip()


def naf_text(code: str) -> str:
    label = co.naf_division_label(code)
    return f"{code} · {label}" if label and code else (code or "")


def build_company_card(company: co.Company, locale: str, *, today: Optional[date] = None,
                       searched_siret: Optional[str] = None,
                       requester_id: Optional[int] = None) -> ui.LayoutView:
    """The record of one company: SIREN first, then what matters, then the directory link."""
    today = today or date.today()
    L = lambda key: t(f"tools.company.labels.{key}", locale=locale)  # noqa: E731
    accent = tc.ACCENT_SUCCESS if company.active else tc.ACCENT_ERROR
    card = tc.ToolCard(icon=em.BUSINESS, title=tc.truncate(tc.escape_markdown(company.name), 200)[0],
                       accent=accent)
    shown = co.format_siret(searched_siret) if (searched_siret and company.matched) else co.format_siren(company.siren)
    card.code(shown)

    # A closing date is a Discord timestamp: keep it raw so Discord renders it.
    lines: List[Any] = [tc.detail(L("status"), company_status_text(company, locale),
                                  raw=not company.active and bool(company.closed_on))]
    if company.acronym:
        lines.append((L("acronym"), company.acronym))
    if company.commercial_names:
        lines.append((L("also_known_as"), ", ".join(company.commercial_names[:3])))
    legal = co.legal_form_label(company.legal_form_code)
    if legal:
        lines.append((L("legal_form"), legal))
    if company.naf_code:
        lines.append((L("activity"), naf_text(company.naf_code)))
    if company.created_on:
        lines.append((L("created"), f"{company.created_on.strftime('%Y-%m-%d')} "
                                    f"({age_text(company.created_on, today, locale)})"))
    kinds = []
    if company.is_association:
        kinds.append(t("tools.company.kind.association", locale=locale))
    if company.is_ess:
        kinds.append(t("tools.company.kind.ess", locale=locale))
    if company.size_category:
        label = i18n.get(f"tools.company.size.{company.size_category}", locale=locale)
        kinds.append(label if not label.startswith("[") else company.size_category)
    if kinds:
        lines.append((L("category"), ", ".join(kinds)))
    card.details(lines)

    place: List[Any] = []
    if company.matched is not None and company.matched.siret != (company.head_office.siret if company.head_office else ""):
        place.append((L("searched_establishment"),
                      f"{_address(company.matched)} ({'' if company.matched.active else t('tools.company.status.closed', locale=locale) + ', '}{co.format_siret(company.matched.siret)})"))
    if company.head_office is not None:
        place.append((L("headquarters"), _address(company.head_office) or "—"))
    if company.establishments:
        place.append((L("establishments"), t("tools.company.establishments", locale=locale,
                                              total=company.establishments,
                                              open=company.open_establishments)))
    headcount = headcount_text(company, locale)
    if headcount:
        place.append((L("headcount"), headcount))
    if place:
        card.details(place)

    if company.directors:
        dirs = []
        for d in company.directors:
            who = f"**{tc.escape_markdown(d.name)}**"
            dirs.append(f"{who} · {tc.escape_markdown(d.role)}" if d.role else who)
        if company.directors_total > len(company.directors):
            dirs.append(f"-# {t('tools.company.directors_more', locale=locale, count=company.directors_total - len(company.directors))}")
        card.section(L("directors"), "\n".join(dirs))

    card.footer(
        tc.source_line(locale, (em.DATA_GOUV, "Annuaire des Entreprises"), verb="data_provided_by"),
        tc.requested_by(locale, requester_id))
    row = tc.link_row(tc.link_button(
        t("tools.company.directory_button", locale=locale),
        co.DIRECTORY_URL.format(siren=company.siren), icon=em.DATA_GOUV))
    return card.build(rows=[row] if row else [])


class CompanyPickSelect(ui.Select):
    """Open one company of the list — the option value is the SIREN (stateless)."""

    def __init__(self, companies: Optional[Sequence[co.Company]] = None, locale: str = "en-US"):
        options = []
        for company in companies or ():
            city = company.head_office.city if company.head_office else ""
            description = " · ".join(p for p in (co.format_siren(company.siren), city) if p)
            options.append(discord.SelectOption(
                label=tc.truncate(company.name, 100)[0] or company.siren,
                value=company.siren, description=description[:100] or None))
        super().__init__(
            custom_id="moddy:tools:company:pick",
            placeholder=t("tools.company.pick_placeholder", locale=locale)[:150],
            options=options, min_values=1, max_values=1)

    @tc.guarded
    async def callback(self, interaction: discord.Interaction):
        locale = _locale(interaction)
        siren = self.values[0]
        if not (siren.isdigit() and len(siren) == 9):
            raise tc.ToolError("not_found", tool="company", query=siren)
        await interaction.response.defer(ephemeral=True, thinking=True)
        company = await lookup_siren(_api(interaction), siren)
        await interaction.followup.send(
            view=build_company_card(company, locale, requester_id=None),
            ephemeral=True, allowed_mentions=tc.NO_MENTIONS)


async def lookup_siren(api, siren: str) -> co.Company:
    """Fetch one company by SIREN; expected failures become :class:`ToolError`."""
    from services.public_api import PublicAPIError
    try:
        result = await co.search_companies(api, co.Query(co.KIND_SIREN, siren))
    except PublicAPIError as exc:
        raise api_error(exc, "company") from exc
    for company in result.companies:
        if company.siren == siren:
            return company
    raise tc.ToolError("not_found", tool="company", query=siren)


def api_error(exc, tool: str) -> tc.ToolError:
    """Map a ``PublicAPIError`` to the shared ``ToolError`` code of the same name."""
    code = exc.code if exc.code in ("timeout", "network", "rate_limited") else "unavailable"
    return tc.ToolError(code, tool=tool)


class CompanyResultsView(BaseView):
    """The list of matches + the select that opens one record."""

    __persistent__ = True

    def __init__(self, card: Optional[tc.ToolCard] = None,
                 companies: Optional[Sequence[co.Company]] = None, locale: str = "en-US"):
        super().__init__()
        if card is not None:
            self.add_item(card.container)
        self.add_item(ui.ActionRow(CompanyPickSelect(companies, locale)))

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_view(cls())


def build_company_results(result: co.SearchResult, query: str, locale: str, *,
                          requester_id: Optional[int] = None) -> CompanyResultsView:
    shown = result.companies[:co.PER_PAGE]
    card = tc.ToolCard(icon=em.BUSINESS, title=t("tools.company.title", locale=locale))
    card.text(t("tools.company.results_intro", locale=locale,
                total=tc.format_number(result.total, locale), shown=len(shown),
                query=tc.inline(query)))
    rows = []
    for company in shown:
        city = company.head_office.city if company.head_office else ""
        dot = em.GREEN_STATUS if company.active else em.RED_STATUS
        line = f"**{tc.escape_markdown(tc.truncate(company.name, 70)[0])}** · `{co.format_siren(company.siren)}`"
        if city:
            line += f" · {tc.escape_markdown(city)}"
        if not company.active:
            line += f" · {t('tools.company.status.closed', locale=locale)}"
        rows.append(_with_dot(dot, line))
    card.text("\n".join(rows))
    card.footer(
        tc.source_line(locale, (em.DATA_GOUV, "Annuaire des Entreprises"), verb="data_provided_by"),
        tc.requested_by(locale, requester_id))
    return CompanyResultsView(card, shown, locale)


# =========================================================================== #
# /username
# =========================================================================== #

#: Characters of profile lines per result page (the message cap is 4000).
PAGE_BUDGET = 2600


def category_label(cat: str, locale: str) -> str:
    text = i18n.get(f"tools.username.categories.{cat}", locale=locale)
    return text if not text.startswith("[") else cat.capitalize()


def result_payload(outcome: wmn.SearchOutcome) -> Dict[str, Any]:
    return {"u": outcome.username, "p": wmn.profiles_to_json(outcome.found),
            "n": outcome.total, "c": outcome.checked, "t": outcome.timed_out}


def username_pages(profiles: Sequence[wmn.Profile], locale: str) -> List[List[Tuple[str, List[str]]]]:
    blocks = []
    for cat, items in wmn.group_by_category(profiles):
        title = f"**{category_label(cat, locale)}** · `{len(items)}`"
        blocks.append((title, [wmn.profile_line(p) for p in items]))
    cont = t("tools.username.continued", locale=locale)
    return wmn.paginate(blocks, budget=PAGE_BUDGET, continued=lambda title: f"{title} {cont}")


def username_loading_card(locale: str, name: str, *, progress: Optional[Tuple[int, int]] = None):
    return tc.loading_card(locale, t("tools.username.searching", locale=locale, name=tc.inline(name)),
                           progress=progress)


def build_username_result(payload: Dict[str, Any], token: str, page: int, locale: str, *,
                          requester_id: Optional[int] = None) -> ui.LayoutView:
    name = payload["u"]
    profiles = wmn.profiles_from_json(payload.get("p") or [])
    total, checked = int(payload.get("n") or 0), int(payload.get("c") or 0)
    found = len(profiles)

    card = tc.ToolCard(icon=em.PERSON_SEARCH, title=t("tools.username.title", locale=locale),
                       accent=tc.ACCENT_INFO)
    if found:
        card.text(t("tools.username.found", locale=locale, count=found, checked=checked,
                    name=tc.inline(name)))
    else:
        card.text(t("tools.username.none", locale=locale, checked=checked, name=tc.inline(name)))

    pages = username_pages(profiles, locale) if found else [[]]
    page = max(0, min(page, len(pages) - 1))
    if found:
        body = "\n\n".join(f"{head}\n" + "\n".join(lines) for head, lines in pages[page])
        card.text(body)

    hints = []
    unchecked = max(0, total - checked)
    if payload.get("t"):
        hints.append(t("tools.username.cut", locale=locale, seconds=int(wmn.GLOBAL_DEADLINE),
                       unchecked=unchecked))
    elif unchecked:
        hints.append(t("tools.username.unchecked", locale=locale, count=unchecked))
    hints.append(t("tools.username.note", locale=locale))
    card.hint("\n-# ".join(hints))

    parts = [tc.source_line(locale, (em.WHATSMYNAME, "WhatsMyName"), verb="data_provided_by")]
    if len(pages) > 1:
        parts.append(t("tools.common.page", locale=locale, page=page + 1, pages=len(pages)))
    parts.append(tc.requested_by(locale, requester_id))
    card.footer(*parts)

    row = ui.ActionRow()
    if len(pages) > 1:
        row.add_item(UsernamePageButton(token, max(0, page - 1), "prev", locale, disabled=page == 0))
        row.add_item(UsernamePageButton(token, min(len(pages) - 1, page + 1), "next", locale,
                                        disabled=page >= len(pages) - 1))
    if found:
        row.add_item(UsernameExportButton(token, locale))
    row.add_item(tc.link_button("WhatsMyName", wmn.PROJECT_URL, icon=em.WHATSMYNAME))
    view = BaseView()
    return card.build(view=view, rows=[row])


def build_export_view(name: str, filename: str, locale: str) -> ui.LayoutView:
    card = tc.ToolCard(icon=em.PERSON_SEARCH, title=t("tools.username.export_title", locale=locale))
    card.text(t("tools.username.export_body", locale=locale, name=tc.inline(name)))
    card.file(filename)
    return card.build()


def export_file(payload: Dict[str, Any], locale: str) -> Tuple[str, discord.File]:
    name = payload["u"]
    profiles = wmn.profiles_from_json(payload.get("p") or [])
    text = wmn.export_text(
        name, profiles, generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        category_label=lambda cat: category_label(cat, locale))
    filename = f"username-{name}.txt"
    return filename, make_text_file(text, filename)


async def _load_payload(interaction: discord.Interaction, token: str) -> Dict[str, Any]:
    payload = await tc.load_state(interaction.client, STATE_KIND_USERNAME, token)
    if not payload:
        raise tc.ToolError("expired")
    return payload


class UsernamePageButton(
    ui.DynamicItem[ui.Button],
    template=r"moddy:tools:username:(?P<dir>prev|next):(?P<token>[A-Za-z0-9]{10}):(?P<page>\d+)",
):
    """Previous / next page; the target page and the result token are in the custom_id."""

    def __init__(self, token: str, page: int, direction: str = "next", locale: str = "en-US", *,
                 disabled: bool = False):
        previous = direction == "prev"
        super().__init__(ui.Button(
            label=t(f"tools.common.buttons.{'previous' if previous else 'next'}", locale=locale),
            style=discord.ButtonStyle.secondary, emoji=tc.button_emoji(em.BACK if previous else em.NEXT),
            custom_id=f"moddy:tools:username:{direction}:{token}:{page}", disabled=disabled))
        self.token, self.page, self.direction = token, page, direction

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match: re.Match):
        return cls(match["token"], int(match["page"]), match["dir"])

    @tc.guarded
    async def callback(self, interaction: discord.Interaction):
        locale = _locale(interaction)
        payload = await _load_payload(interaction, self.token)
        await interaction.response.edit_message(
            view=build_username_result(payload, self.token, self.page, locale,
                                       requester_id=message_requester(interaction)),
            allowed_mentions=tc.NO_MENTIONS)


class UsernameExportButton(
    ui.DynamicItem[ui.Button],
    template=r"moddy:tools:username:export:(?P<token>[A-Za-z0-9]{10})",
):
    def __init__(self, token: str, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=t("tools.username.export_button", locale=locale),
            style=discord.ButtonStyle.secondary, emoji=tc.button_emoji(em.DOWNLOAD),
            custom_id=f"moddy:tools:username:export:{token}"))
        self.token = token

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match: re.Match):
        return cls(match["token"])

    @tc.guarded
    async def callback(self, interaction: discord.Interaction):
        locale = _locale(interaction)
        payload = await _load_payload(interaction, self.token)
        filename, file = export_file(payload, locale)
        await interaction.response.send_message(
            view=build_export_view(payload["u"], filename, locale), files=[file],
            ephemeral=True, allowed_mentions=tc.NO_MENTIONS)


# =========================================================================== #
# Registration
# =========================================================================== #


class LookupPersistence(BaseView):
    """Marker view: registers the dynamic items of the lookup tools at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(StatusRefreshButton, UsernamePageButton, UsernameExportButton)


_TOKEN = "AbCdEfGh12"

PERSISTENT_VIEWS: list = [StatusOverviewView, CompanyResultsView, LookupPersistence]
DYNAMIC_ITEM_CASES: list = [
    (StatusRefreshButton, ("cloudflare",)),
    (UsernamePageButton, (_TOKEN, 2, "next")),
    (UsernameExportButton, (_TOKEN,)),
]
