"""
Views of the web utility tools (``/whois``, ``/dns``, ``/ssl``, ``/subdomains``,
``/link-check`` and the "Check links" menu) — see docs/features/TOOLS_WEB.md.

Cards are built with ``utils/tool_card.py``; the logic they show lives in
``tools/domain/`` and ``tools/linkcheck.py``. Every interactive component is
persistent (docs/guides/PERSISTENT_VIEWS.md): they are ``DynamicItem`` s whose
``custom_id`` carries the domain (and a page number), so nothing is stored and
they survive a restart. Their callbacks re-derive everything from the
``custom_id`` and the interaction — the data itself comes back from the Redis
caches of ``services/web_tools_service.py`` (or a fresh lookup).

On an ephemeral card a click edits the card in place; on a public card it
answers privately instead, so one member's click never rewrites what the whole
channel is reading.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple

import discord
from discord import ui

from cogs.error_handler import BaseView
from tools import linkcheck as lc
from tools.domain import crtsh, doh, rdap, tls
from tools.domain.names import DomainName
from utils import tool_card as tc
from utils.automod_render import make_text_file
from utils.emojis import (
    ACCOUNT_TREE, ALT_ROUTE, CLOUDFLARE, CRTSH, DNS, DONE, DOWNLOAD, ERROR, HISTORY, LINK,
    LOADING, LOCK, NEXT, BACK, REFRESH, URLSCAN, VIRUSTOTAL, WARNING, WEB,
)
from utils.i18n import i18n, t

NO_MENTIONS = tc.NO_MENTIONS

#: Longest domain a button can carry in its ``custom_id`` (100 characters max).
MAX_BUTTON_DOMAIN = 60
_DOMAIN_RE = r"[a-z0-9_.-]{1,%d}" % MAX_BUTTON_DOMAIN

#: Characters of the DNS code block before the full text is attached instead.
DNS_BODY_LIMIT = 2600
DNS_LINE_LIMIT = 90
DNS_VALUE_LIMIT = 480
DNS_RECORDS_PER_TYPE = 8

RUNNING = "running"  # provider state while a check is still in progress


def _view() -> BaseView:
    return BaseView()


def _row(*items: ui.Item) -> Optional[ui.ActionRow]:
    items = [i for i in items if i is not None]
    if not items:
        return None
    row = ui.ActionRow()
    for item in items[:5]:
        row.add_item(item)
    return row


def _rows(*rows: Optional[ui.ActionRow]) -> List[ui.ActionRow]:
    return [r for r in rows if r is not None]


def button_domain_ok(domain: str) -> bool:
    return bool(re.fullmatch(_DOMAIN_RE, domain or ""))


# --------------------------------------------------------------------------- #
# Answering a click: in place when private, privately when public
# --------------------------------------------------------------------------- #


def is_private_card(interaction: discord.Interaction) -> bool:
    message = interaction.message
    return bool(message is not None and message.flags.ephemeral)


async def begin(interaction: discord.Interaction) -> bool:
    """Acknowledge a click. Returns ``True`` when the card will be edited in place."""
    in_place = is_private_card(interaction)
    if in_place:
        await interaction.response.defer()
    else:
        await interaction.response.defer(ephemeral=True, thinking=True)
    return in_place


async def finish(interaction: discord.Interaction, in_place: bool, view: ui.LayoutView,
                 files: Sequence[discord.File] = ()) -> None:
    if in_place:
        await interaction.edit_original_response(
            view=view, attachments=list(files), allowed_mentions=NO_MENTIONS)
    else:
        await interaction.followup.send(view=view, files=list(files), ephemeral=True,
                                        allowed_mentions=NO_MENTIONS)


# --------------------------------------------------------------------------- #
# /whois
# --------------------------------------------------------------------------- #


def _stamp(when: Optional[datetime]) -> Optional[str]:
    if when is None:
        return None
    epoch = when.timestamp()
    return f"{tc.discord_timestamp(epoch, 'D')} ({tc.discord_timestamp(epoch, 'R')})"


def build_whois_card(record: rdap.DomainRecord, domain: DomainName, locale: str, *,
                     requester_id: Optional[int] = None,
                     now: Optional[datetime] = None) -> ui.LayoutView:
    now = now or datetime.now(timezone.utc)
    age = rdap.age_days(record.created, now)
    level = rdap.age_level(age)
    left = rdap.days_left(record.expires, now)
    codes, unknown = rdap.summarize_statuses(record.statuses)

    accent = tc.ACCENT_INFO
    if level == "very_recent" or (left is not None and left < 0):
        accent = tc.ACCENT_ERROR
    elif level == "recent" or (left is not None and left <= 30) \
            or rdap.WARNING_STATUSES & set(codes):
        accent = tc.ACCENT_WARNING

    card = tc.ToolCard(title=t("tools.whois.title", locale=locale), icon=WEB, accent=accent)
    ascii_name = record.name or domain.registrable_ascii
    shown = rdap_unicode(ascii_name)
    card.code("\n".join([shown] + ([ascii_name] if ascii_name != shown else [])))

    if level == "very_recent":
        card.text(tc.with_icon(ERROR, "**" + t("tools.whois.alert.very_recent", locale=locale,
                                              days=tc.inline(age)) + "**"))
    elif level == "recent":
        card.text(tc.with_icon(WARNING, "**" + t("tools.whois.alert.recent", locale=locale,
                                                days=tc.inline(age)) + "**"))
    if left is not None and left < 0:
        card.text(tc.with_icon(ERROR, "**" + t("tools.whois.alert.expired", locale=locale) + "**"))
    elif left is not None and left <= 30:
        card.text(tc.with_icon(WARNING, "**" + t("tools.whois.alert.expiring", locale=locale,
                                                days=tc.inline(left)) + "**"))
    for code in sorted(rdap.WARNING_STATUSES & set(codes)):
        card.text(tc.with_icon(WARNING, t(f"tools.whois.alert_status.{code}", locale=locale)))

    rows: List[str] = []

    def add(label_key: str, value, raw: bool = False) -> None:
        if value not in (None, "", []):
            rows.append(tc.detail(t(f"tools.whois.{label_key}", locale=locale), value, raw=raw))

    add("registrar", record.registrar)
    add("created", _stamp(record.created), raw=True)
    add("expires", _stamp(record.expires), raw=True)
    add("updated", _stamp(record.updated), raw=True)
    if codes or unknown:
        labels = [tc.inline(t(f"tools.whois.statuses.{c}", locale=locale)) for c in codes]
        labels += [tc.inline(u) for u in unknown[:3]]
        add("status", ", ".join(labels), raw=True)
    if record.nameservers:
        shown_ns = [tc.inline(n) for n in record.nameservers[:6]]
        extra = len(record.nameservers) - len(shown_ns)
        if extra > 0:
            shown_ns.append(t("tools.whois.more", locale=locale, count=extra))
        add("nameservers", ", ".join(shown_ns), raw=True)
    if record.dnssec is not None:
        add("dnssec", t("tools.whois.dnssec_signed" if record.dnssec else "tools.whois.dnssec_unsigned",
                        locale=locale))
    card.details(rows)

    if domain.shared_hosting:
        card.hint(t("tools.whois.shared_hosting", locale=locale, platform=tc.inline(record.name)))
    elif domain.is_subdomain:
        card.hint(t("tools.whois.subdomain_note", locale=locale,
                    domain=tc.inline(domain.registrable_unicode)))
    card.footer(tc.source_line(locale, "RDAP", verb="data_provided_by"),
                tc.requested_by(locale, requester_id))
    link = tc.link_row(tc.link_button(t("tools.whois.registrar_record", locale=locale),
                                      record.related_url, icon=WEB)) if record.related_url else None
    return card.build(rows=[link] if link else [])


def rdap_unicode(ascii_name: str) -> str:
    from tools.domain.names import to_unicode
    return to_unicode(ascii_name)


# --------------------------------------------------------------------------- #
# /dns
# --------------------------------------------------------------------------- #

TYPE_ALL = "all"


def _ttl_text(ttl: int, locale: str) -> str:
    n, unit = doh.ttl_parts(ttl)
    return f"{n} {t(f'tools.dns.ttl_units.{unit}', locale=locale)}"


def _clip(text: str, limit: int) -> str:
    return tc.truncate(text, limit)[0]


def _dns_all_lines(lookup, locale: str) -> List[str]:
    lines: List[str] = []
    for rtype in doh.RECORD_TYPES:
        answer = lookup.answers.get(rtype)
        if not answer or not answer.records:
            continue
        for record in answer.records[:DNS_RECORDS_PER_TYPE]:
            value = f"{record.priority} {record.value}" if record.priority is not None else record.value
            lines.append(_clip(f"{rtype:<6}{value}", DNS_LINE_LIMIT))
        extra = len(answer.records) - DNS_RECORDS_PER_TYPE
        if extra > 0:
            lines.append(f"{rtype:<6}" + t("tools.dns.more_records", locale=locale, count=extra))
    return lines


def _dns_type_lines(answer: doh.DnsAnswer) -> List[str]:
    lines = []
    for record in answer.records:
        value = f"{record.priority} {record.value}" if record.priority is not None else record.value
        lines.append(_clip(value, DNS_VALUE_LIMIT))
    return lines


def _email_section(hints: doh.EmailHints, locale: str) -> str:
    rows = []
    if hints.spf:
        key = {"-all": "strict", "~all": "soft", "?all": "neutral", "+all": "open"}.get(
            hints.spf_all or "", "unknown")
        rows.append(tc.detail("SPF", t(f"tools.dns.spf.{key}", locale=locale), raw=True))
    else:
        rows.append(tc.detail("SPF", t("tools.dns.missing", locale=locale), raw=True))
    if hints.dmarc:
        key = hints.dmarc_policy or "unknown"
        rows.append(tc.detail("DMARC", t(f"tools.dns.dmarc.{key}", locale=locale), raw=True))
    else:
        rows.append(tc.detail("DMARC", t("tools.dns.missing", locale=locale), raw=True))
    if hints.dkim:
        rows.append(tc.detail("DKIM", ", ".join(tc.inline(s) for s in hints.dkim), raw=True))
    return "\n".join(rows)


def build_dns_card(lookup, rtype: str, locale: str, *, requester_id: Optional[int] = None
                   ) -> Tuple[ui.LayoutView, List[discord.File]]:
    """Records of one type (or all), the type select under the card."""
    name = lookup.name
    card = tc.ToolCard(title=t("tools.dns.title", locale=locale), icon=DNS)
    card.details([(t("tools.dns.name", locale=locale), name)])

    files: List[discord.File] = []
    if rtype == TYPE_ALL:
        lines = _dns_all_lines(lookup, locale)
        records = [r for a in lookup.answers.values() for r in a.records]
    else:
        answer = lookup.answers.get(rtype) or doh.DnsAnswer()
        lines = _dns_type_lines(answer)
        records = answer.records

    filename = f"dns-{name}.txt"
    if lines:
        block, attachment = tc.fit_or_attach("\n".join(lines), limit=DNS_BODY_LIMIT,
                                             filename=filename)
        card.text(block)
        if attachment is not None:
            full = "\n".join(_full_lines(lookup, rtype))
            files.append(make_text_file(full, filename))
            card.file(filename)
            card.hint(t("tools.dns.truncated", locale=locale))
    elif rtype == TYPE_ALL:
        card.text(tc.with_icon(WARNING, t("tools.dns.none_at_all", locale=locale)))
    else:
        card.text(tc.with_icon(WARNING, t("tools.dns.none_of_type", locale=locale,
                                          type=tc.inline(rtype))))

    summary = doh.ttl_summary(records)
    details = []
    if summary:
        low, high = summary
        value = _ttl_text(low, locale) if low == high else \
            f"{_ttl_text(low, locale)} – {_ttl_text(high, locale)}"
        details.append(tc.detail(t("tools.dns.ttl", locale=locale), value))
    if lookup.authenticated:
        details.append(tc.with_icon(DONE, t("tools.dns.dnssec_ok", locale=locale)))
    card.details(details)

    if lookup.hints and rtype in (TYPE_ALL, "TXT"):
        card.section(t("tools.dns.email_title", locale=locale),
                     _email_section(lookup.hints, locale))
    if lookup.failed:
        card.hint(t("tools.dns.failed_types", locale=locale, types=", ".join(lookup.failed)))

    card.footer(tc.source_line(locale, (CLOUDFLARE, "Cloudflare"), verb="data_provided_by"),
                tc.requested_by(locale, requester_id))
    rows = []
    if button_domain_ok(name):
        rows.append(_row(DnsTypeSelect(name, rtype, locale)))
    return card.build(view=_view(), rows=_rows(*rows)), files


def _full_lines(lookup, rtype: str) -> List[str]:
    out = []
    for kind in (doh.RECORD_TYPES if rtype == TYPE_ALL else (rtype,)):
        answer = lookup.answers.get(kind)
        for record in (answer.records if answer else []):
            value = f"{record.priority} {record.value}" if record.priority is not None else record.value
            out.append(f"{kind:<6}{record.ttl:<8}{value}")
    return out


class DnsTypeSelect(ui.DynamicItem[ui.Select],
                    template=r"moddy:tools:dns:t:(?P<domain>%s)" % _DOMAIN_RE):
    """"Record type" select under a /dns card."""

    def __init__(self, domain: str, current: str = TYPE_ALL, locale: str = "en-US"):
        options = [discord.SelectOption(
            label=t("tools.dns.type_all", locale=locale)[:100], value=TYPE_ALL,
            default=(current == TYPE_ALL))]
        options += [discord.SelectOption(label=rtype, value=rtype, default=(current == rtype))
                    for rtype in doh.RECORD_TYPES]
        super().__init__(ui.Select(
            placeholder=t("tools.dns.select_placeholder", locale=locale)[:150],
            options=options, custom_id=f"moddy:tools:dns:t:{domain}"))
        self.domain = domain

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Select,
                             match: re.Match):
        return cls(match["domain"], locale=i18n.get_user_locale(interaction))

    @tc.guarded
    async def callback(self, interaction: discord.Interaction):
        from services.web_tools_service import WebToolsService

        locale = i18n.get_user_locale(interaction)
        rtype = self.item.values[0]
        if rtype != TYPE_ALL and rtype not in doh.RECORD_TYPES:
            rtype = TYPE_ALL
        in_place = await begin(interaction)
        service = WebToolsService(interaction.client)
        try:
            lookup = await service.dns(self.domain)
        except tc.ToolError as exc:
            await finish(interaction, in_place, tc.tool_error_card(exc, locale))
            return
        view, files = build_dns_card(lookup, rtype, locale)
        await finish(interaction, in_place, view, files)


# --------------------------------------------------------------------------- #
# /ssl
# --------------------------------------------------------------------------- #


def build_ssl_card(result, domain_unicode: str, locale: str, *,
                   requester_id: Optional[int] = None) -> ui.LayoutView:
    cert = result.cert
    left = result.days_left
    if not result.verified:
        accent, icon, verdict = tc.ACCENT_ERROR, ERROR, "invalid"
    elif left is not None and left <= tls.WARN_DAYS:
        accent, icon, verdict = tc.ACCENT_WARNING, WARNING, "expiring"
    else:
        accent, icon, verdict = tc.ACCENT_SUCCESS, DONE, "valid"

    card = tc.ToolCard(title=t("tools.ssl.title", locale=locale), icon=LOCK, accent=accent)
    target = domain_unicode if result.port == tls.DEFAULT_PORT else f"{domain_unicode}:{result.port}"
    card.code(target)
    card.text(tc.with_icon(icon, f"**{t(f'tools.ssl.verdict.{verdict}', locale=locale)}**"))
    if not result.verified:
        card.text("\n".join(f"- {t(f'tools.ssl.reasons.{r}', locale=locale)}"
                            for r in result.reasons))

    rows: List[str] = []
    subject = cert.subject_cn or (cert.sans[0] if cert.sans else None)
    if subject:
        if cert.subject_org and cert.subject_org != subject:
            subject = f"{subject} ({cert.subject_org})"
        rows.append(tc.detail(t("tools.ssl.subject", locale=locale), subject))
    if cert.issuer_name:
        rows.append(tc.detail(t("tools.ssl.issuer", locale=locale), cert.issuer_name))
    if cert.not_before and cert.not_after:
        end = cert.not_after.timestamp()
        valid = (f"{tc.discord_timestamp(cert.not_before.timestamp(), 'D')} → "
                 f"{tc.discord_timestamp(end, 'D')} ({tc.discord_timestamp(end, 'R')})")
        rows.append(tc.detail(t("tools.ssl.validity", locale=locale), valid, raw=True))
    names = tls.san_preview(cert, 3)
    if names:
        text = ", ".join(tc.inline(n) for n in names)
        extra = len(set(cert.sans)) - len(names)
        if extra > 0:
            text += " " + t("tools.ssl.more", locale=locale, count=extra)
        rows.append(tc.detail(t("tools.ssl.names", locale=locale), text, raw=True))
    if result.version:
        rows.append(tc.detail(t("tools.ssl.protocol", locale=locale), result.version))
    if cert.key_label:
        rows.append(tc.detail(t("tools.ssl.key", locale=locale), cert.key_label))
    if cert.signature:
        rows.append(tc.detail(t("tools.ssl.signature", locale=locale), cert.signature))
    card.details(rows)
    card.footer(t("tools.ssl.read_directly", locale=locale), tc.requested_by(locale, requester_id))
    return card.build()


# --------------------------------------------------------------------------- #
# /subdomains
# --------------------------------------------------------------------------- #


def build_subdomains_card(result: crtsh.SubdomainResult, page: int, locale: str, *,
                          requester_id: Optional[int] = None) -> ui.LayoutView:
    pages = crtsh.page_count(len(result.names))
    page = max(0, min(page, pages - 1))
    card = tc.ToolCard(title=t("tools.subdomains.title", locale=locale), icon=ACCOUNT_TREE)
    card.details([(t("tools.subdomains.domain", locale=locale), result.domain)])

    if not result.names:
        card.text(tc.with_icon(WARNING, t("tools.subdomains.none", locale=locale)))
    else:
        summary = t("tools.subdomains.summary", locale=locale,
                    count=tc.inline(tc.format_number(result.total, locale)))
        if result.wildcards:
            summary += " · " + t("tools.subdomains.wildcards", locale=locale,
                                 count=tc.inline(result.wildcards))
        card.text(summary)
        card.code("\n".join(crtsh.page(result.names, page)))
        if pages > 1:
            card.hint(t("tools.common.page", locale=locale, page=page + 1, pages=pages))
        if result.truncated:
            card.hint(t("tools.subdomains.truncated", locale=locale,
                        shown=tc.format_number(len(result.names), locale),
                        total=tc.format_number(result.total, locale)))
    if result.source != "crt.sh":
        card.hint(t("tools.subdomains.fallback", locale=locale, source=result.source))
    card.hint(t("tools.subdomains.hint", locale=locale))
    logo = CRTSH if result.source == "crt.sh" else None
    card.footer(tc.source_line(locale, (logo, result.source), verb="data_provided_by"),
                tc.requested_by(locale, requester_id))

    rows = []
    if pages > 1 and button_domain_ok(result.domain):
        rows.append(_row(
            SubdomainsPageButton(result.domain, max(0, page - 1), "prev", disabled=page == 0,
                                 locale=locale),
            SubdomainsPageButton(result.domain, min(pages - 1, page + 1), "next",
                                 disabled=page >= pages - 1, locale=locale),
            SubdomainsExportButton(result.domain, locale)))
    return card.build(view=_view(), rows=_rows(*rows))


def subdomains_file(result: crtsh.SubdomainResult) -> discord.File:
    return make_text_file("\n".join(result.names) + "\n", f"subdomains-{result.domain}.txt")


def build_crtsh_error(exc: tc.ToolError, domain: str, locale: str) -> ui.LayoutView:
    buttons = []
    if exc.code in ("crtsh_down", "timeout", "rate_limited", "unavailable") \
            and button_domain_ok(domain):
        buttons.append(SubdomainsRetryButton(domain, locale))
    return tc.tool_error_card(exc, locale, buttons=buttons)


async def _subdomains_result(interaction: discord.Interaction, domain: str):
    from services.web_tools_service import WebToolsService
    return await WebToolsService(interaction.client).subdomains(domain)


class SubdomainsPageButton(
    ui.DynamicItem[ui.Button],
    template=r"moddy:tools:subdomains:(?P<dir>prev|next):(?P<page>\d{1,3}):(?P<domain>%s)" % _DOMAIN_RE,
):
    """Previous / next page of the subdomain list."""

    def __init__(self, domain: str, page: int, direction: str, *, disabled: bool = False,
                 locale: str = "en-US"):
        key = "previous" if direction == "prev" else "next"
        super().__init__(ui.Button(
            label=t(f"tools.common.buttons.{key}", locale=locale)[:80],
            style=discord.ButtonStyle.secondary, disabled=disabled,
            emoji=tc.button_emoji(BACK if direction == "prev" else NEXT),
            custom_id=f"moddy:tools:subdomains:{direction}:{int(page)}:{domain}"))
        self.domain = domain
        self.page = int(page)

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button,
                             match: re.Match):
        return cls(match["domain"], int(match["page"]), match["dir"])

    @tc.guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        in_place = await begin(interaction)
        try:
            result = await _subdomains_result(interaction, self.domain)
        except tc.ToolError as exc:
            await finish(interaction, in_place, build_crtsh_error(exc, self.domain, locale))
            return
        requester = None if in_place else interaction.user.id
        await finish(interaction, in_place,
                     build_subdomains_card(result, self.page, locale, requester_id=requester))


class SubdomainsExportButton(
    ui.DynamicItem[ui.Button],
    template=r"moddy:tools:subdomains:export:(?P<domain>%s)" % _DOMAIN_RE,
):
    """The whole list as a .txt file, sent privately."""

    def __init__(self, domain: str, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=t("tools.common.buttons.export", locale=locale)[:80],
            style=discord.ButtonStyle.secondary, emoji=tc.button_emoji(DOWNLOAD),
            custom_id=f"moddy:tools:subdomains:export:{domain}"))
        self.domain = domain

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button,
                             match: re.Match):
        return cls(match["domain"])

    @tc.guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await _subdomains_result(interaction, self.domain)
        except tc.ToolError as exc:
            await interaction.followup.send(view=build_crtsh_error(exc, self.domain, locale),
                                            ephemeral=True)
            return
        await interaction.followup.send(
            t("tools.subdomains.export_message", locale=locale, count=len(result.names)),
            file=subdomains_file(result), ephemeral=True, allowed_mentions=NO_MENTIONS)


class SubdomainsRetryButton(
    ui.DynamicItem[ui.Button],
    template=r"moddy:tools:subdomains:retry:(?P<domain>%s)" % _DOMAIN_RE,
):
    """"Retry" under the crt.sh error card (the service is often overloaded)."""

    def __init__(self, domain: str, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=t("tools.common.buttons.retry", locale=locale)[:80],
            style=discord.ButtonStyle.primary, emoji=tc.button_emoji(REFRESH),
            custom_id=f"moddy:tools:subdomains:retry:{domain}"))
        self.domain = domain

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button,
                             match: re.Match):
        return cls(match["domain"])

    @tc.guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        # The error card replaced the loading card: put the spinner back in
        # the same message, then the answer.
        await interaction.response.edit_message(view=tc.loading_card(locale))
        try:
            result = await _subdomains_result(interaction, self.domain)
            view = build_subdomains_card(result, 0, locale)
        except tc.ToolError as exc:
            view = build_crtsh_error(exc, self.domain, locale)
        await interaction.edit_original_response(view=view, allowed_mentions=NO_MENTIONS)


# --------------------------------------------------------------------------- #
# /link-check
# --------------------------------------------------------------------------- #

_LEVEL_STYLE = {
    lc.LEVEL_SAFE: (tc.ACCENT_SUCCESS, DONE),
    lc.LEVEL_SUSPICIOUS: (tc.ACCENT_WARNING, WARNING),
    lc.LEVEL_DANGEROUS: (tc.ACCENT_ERROR, ERROR),
}


def age_label(days: int, locale: str) -> str:
    if days >= 730:
        n, unit = days // 365, "year"
    elif days >= 60:
        n, unit = days // 30, "month"
    else:
        n, unit = days, "day"
    key = f"tools.linkcheck.age.{unit}_{'one' if n == 1 else 'other'}"
    return t(key, locale=locale, count=n)


def reason_text(reason: lc.Reason, locale: str) -> str:
    params = {k: tc.inline(v) for k, v in reason.params.items()}
    return t(f"tools.linkcheck.reasons.{reason.code}", locale=locale, **params)


def _vt_line(outcome, locale: str) -> Optional[str]:
    label = t("tools.linkcheck.vt_label", locale=locale)
    state = outcome.state
    if state == "ok" and outcome.result is not None:
        r = outcome.result
        if r.malicious or r.suspicious:
            text = t("tools.linkcheck.vt_flagged", locale=locale,
                     bad=tc.inline(r.malicious), engines=tc.inline(r.engines))
            if r.suspicious:
                text += t("tools.linkcheck.vt_suspicious_extra", locale=locale,
                          count=tc.inline(r.suspicious))
        else:
            text = t("tools.linkcheck.vt_clean", locale=locale, engines=tc.inline(r.engines))
        if outcome.stale:
            text += " " + t("tools.linkcheck.vt_stale", locale=locale)
        return f"**{label}** · {text}"
    return _provider_line(label, state, locale)


def _scan_line(outcome, locale: str) -> Optional[str]:
    label = t("tools.linkcheck.urlscan_label", locale=locale)
    if outcome.state == "ok" and outcome.result is not None:
        r = outcome.result
        if r.malicious:
            text = t("tools.linkcheck.urlscan_malicious", locale=locale)
        elif r.score > 0:
            text = t("tools.linkcheck.urlscan_suspicious", locale=locale)
        else:
            text = t("tools.linkcheck.urlscan_clean", locale=locale)
        extras = []
        if r.brands:
            extras.append(t("tools.linkcheck.urlscan_brands", locale=locale,
                            brands=", ".join(tc.inline(b) for b in r.brands[:3])))
        if outcome.country:
            extras.append(t("tools.linkcheck.urlscan_country", locale=locale,
                            country=tc.inline(outcome.country)))
        return f"**{label}** · {text}" + ("\n-# " + " · ".join(extras) if extras else "")
    return _provider_line(label, outcome.state, locale)


def _provider_line(label: str, state: str, locale: str) -> Optional[str]:
    if state in ("unavailable", "skipped", None):
        return None
    if state == RUNNING:
        return f"**{label}** · {LOADING} {t('tools.linkcheck.provider.running', locale=locale)}"
    return f"**{label}** · {t(f'tools.linkcheck.provider.{state}', locale=locale)}"


def _chain_lines(chain: lc.Chain, locale: str) -> List[str]:
    lines = []
    for hop in chain.hops[: lc.MAX_HOPS + 1]:
        status = hop.status if hop.status else "—"
        text = f"`{status}` `{lc.display_url(hop.url, 58)}`"
        if hop.kind == "meta":
            text += " " + t("tools.linkcheck.chain_meta", locale=locale)
        lines.append(text)
    return lines


def build_link_card(target, vt, scan, locale: str, *, requester_id: Optional[int] = None,
                    sources: Tuple[bool, bool] = (True, True)) -> ui.LayoutView:
    """The full card of one link, rebuilt as each provider answers.

    ``vt`` / ``scan`` are outcomes of ``services/web_tools_service`` (state
    ``running`` while pending, ``unavailable`` when the provider has no key).
    """
    signals, chain = target.signals, target.chain
    vt_result = vt.result if vt is not None and vt.state == "ok" else None
    scan_result = scan.result if scan is not None and scan.state == "ok" else None
    verdict = lc.assess(signals, vt_result, scan_result)
    level = verdict.level
    unreachable = not signals.reached and level == lc.LEVEL_SAFE
    accent, icon = _LEVEL_STYLE[level]
    verdict_key = "unreachable" if unreachable else level
    if unreachable:
        accent, icon = tc.ACCENT_NEUTRAL, WARNING

    card = tc.ToolCard(title=t("tools.linkcheck.title", locale=locale), icon=LINK, accent=accent)
    card.text(tc.with_icon(icon, f"**{t(f'tools.linkcheck.verdict.{verdict_key}', locale=locale)}**"))
    card.code(lc.display_url(chain.final_url, 120))

    reasons = [r for r in verdict.reasons if r.severity != "info"]
    infos = [r for r in verdict.reasons if r.severity == "info"]
    if reasons:
        card.text("\n".join(f"- {reason_text(r, locale)}" for r in reasons))

    if chain.redirects > 0 or chain.error:
        lines = _chain_lines(chain, locale)
        if chain.error:
            lines.append("-# " + t(f"tools.linkcheck.chain_errors.{chain.error}", locale=locale))
        card.section(t("tools.linkcheck.redirects", locale=locale), "\n".join(lines), icon=ALT_ROUTE)

    details: List[str] = []
    if signals.reached:
        details.append(tc.detail(
            t("tools.linkcheck.connection", locale=locale),
            t("tools.linkcheck.https" if signals.https else "tools.linkcheck.http", locale=locale)))
    if signals.age_days is not None:
        details.append(tc.with_icon(HISTORY, tc.detail(
            t("tools.linkcheck.domain_age", locale=locale), age_label(signals.age_days, locale))))
    for r in infos:
        details.append("-# " + reason_text(r, locale))
    card.details(details)

    rep: List[str] = []
    if vt is not None:
        rep.append(_vt_line(vt, locale))
    if scan is not None:
        rep.append(_scan_line(scan, locale))
    rep = [line for line in rep if line]
    if rep:
        card.text("\n".join(rep))
    else:
        card.hint(t("tools.linkcheck.no_reputation", locale=locale))

    if scan is not None and scan.state == "ok" and scan.screenshot_url:
        gallery = ui.MediaGallery()
        gallery.add_item(media=scan.screenshot_url, spoiler=True,
                         description=t("tools.linkcheck.screenshot_alt", locale=locale)[:1024])
        card.add(gallery)
        card.hint(t("tools.linkcheck.screenshot_note", locale=locale))

    if level == lc.LEVEL_SAFE:
        card.hint(t("tools.linkcheck.honesty", locale=locale))
    names = []
    if sources[0] and vt is not None and vt.state not in ("unavailable", "skipped"):
        names.append((VIRUSTOTAL, "VirusTotal"))
    if sources[1] and scan is not None and scan.state not in ("unavailable", "skipped"):
        names.append((URLSCAN, "urlscan.io"))
    card.footer(tc.source_line(locale, *names, verb="scanned_by") if names else None,
                tc.requested_by(locale, requester_id))

    buttons = []
    if vt is not None and vt.link and vt.state in ("ok", "pending"):
        buttons.append(tc.link_button(t("tools.linkcheck.report_vt", locale=locale), vt.link,
                                      icon=VIRUSTOTAL))
    if scan is not None and scan.report_url and scan.state in ("ok", "pending"):
        buttons.append(tc.link_button(t("tools.linkcheck.report_urlscan", locale=locale),
                                      scan.report_url, icon=URLSCAN))
    row = tc.link_row(*buttons)
    return card.build(rows=[row] if row else [])


def build_links_card(items: Sequence[Tuple[object, object]], locale: str, *,
                     skipped: int = 0) -> ui.LayoutView:
    """Several links of one message: one line each (``items`` = ``[(LinkTarget, VtOutcome)]``)."""
    verdicts = []
    for target, vt in items:
        result = vt.result if vt is not None and vt.state == "ok" else None
        verdicts.append(lc.assess(target.signals, result, None))
    worst = max((v.level for v in verdicts),
                key=lambda lv: (lc.LEVEL_SAFE, lc.LEVEL_SUSPICIOUS, lc.LEVEL_DANGEROUS).index(lv),
                default=lc.LEVEL_SAFE)
    accent, _ = _LEVEL_STYLE[worst]
    card = tc.ToolCard(title=t("tools.linkcheck.multi_title", locale=locale), icon=LINK,
                       accent=accent)
    lines = []
    for (target, vt), verdict in zip(items, verdicts):
        _, icon = _LEVEL_STYLE[verdict.level]
        if not target.signals.reached and verdict.level == lc.LEVEL_SAFE:
            icon = WARNING
        line = tc.with_icon(icon, f"`{lc.display_url(target.chain.final_url, 70)}`")
        shown = [r for r in verdict.reasons if r.severity != "info"][:2]
        if shown:
            line += "\n-# " + " · ".join(reason_text(r, locale) for r in shown)
        elif not target.signals.reached:
            line += "\n-# " + t("tools.linkcheck.verdict.unreachable", locale=locale)
        if target.chain.redirects:
            line += "\n-# " + t("tools.linkcheck.multi_redirects", locale=locale,
                                count=target.chain.redirects,
                                host=lc.display_url(target.prepared.url, 40))
        lines.append(line)
    card.text("\n".join(lines))
    if skipped:
        card.hint(t("tools.linkcheck.multi_skipped", locale=locale, count=skipped))
    card.hint(t("tools.linkcheck.multi_hint", locale=locale))
    sources = [(VIRUSTOTAL, "VirusTotal")] if any(
        vt is not None and vt.state == "ok" for _, vt in items) else []
    card.footer(tc.source_line(locale, *sources, verb="scanned_by") if sources else None)
    return card.build()


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


class ToolsWebPersistence(BaseView):
    """Marker view: registers the dynamic items of the web tools at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(DnsTypeSelect, SubdomainsPageButton, SubdomainsExportButton,
                              SubdomainsRetryButton)


PERSISTENT_VIEWS: list = [ToolsWebPersistence]
DYNAMIC_ITEM_CASES: list = [
    (DnsTypeSelect, ("example.com", "MX")),
    (SubdomainsPageButton, ("example.com", 3, "next")),
    (SubdomainsExportButton, ("example.com",)),
    (SubdomainsRetryButton, ("example.com",)),
]
