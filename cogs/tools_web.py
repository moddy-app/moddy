"""
Domain and link utility tools: ``/whois``, ``/dns``, ``/ssl``, ``/subdomains``,
``/link-check``.

See docs/features/TOOLS_WEB.md and the shared contract in docs/features/TOOLS.md.
The network work lives in ``services/web_tools_service.py``, the cards in
``utils/tools_web_views.py`` and the pure logic in ``tools/domain/`` and
``tools/linkcheck.py``.

* ``/whois``, ``/dns``, ``/ssl`` and ``/subdomains`` answer with a loading card
  that is edited into the result.
* ``/link-check`` first shows what Moddy finds on its own (redirect chain,
  domain age, look-alike characters), then edits the same card as VirusTotal
  and urlscan.io answer. Both are optional: without a key the card says so
  instead of failing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

import discord
from discord import app_commands
from discord.ext import commands

from services import web_tools_service as svc
from tools import linkcheck as lc
from tools.domain import tls
from tools.domain.names import DomainError, DomainName, parse_target
from utils import tool_card as tc
from utils import tools_web_views as views
from utils.i18n import i18n, t
from utils.incognito import add_incognito_option, get_incognito_setting

logger = logging.getLogger("moddy.tools_web")

#: Lookups a member can start per minute (every command fans out to public APIs).
LOOKUPS_PER_MINUTE = 10


def _tool_error(exc: tc.ToolError, locale: str) -> discord.ui.LayoutView:
    return tc.tool_error_card(exc, locale)


class ToolsWeb(commands.Cog):
    """/whois /dns /ssl /subdomains /link-check"""

    def __init__(self, bot):
        self.bot = bot
        self.service = svc.WebToolsService(bot)
        self._usage: Dict[int, List[float]] = {}

    # ------------------------------------------------------------------ #
    # Shared helpers
    # ------------------------------------------------------------------ #

    def check_rate(self, user_id: int) -> None:
        """At most :data:`LOOKUPS_PER_MINUTE` lookups a minute per member."""
        now = time.monotonic()
        stamps = [s for s in self._usage.get(user_id, []) if now - s < 60]
        if len(stamps) >= LOOKUPS_PER_MINUTE:
            wait = int(60 - (now - stamps[0])) + 1
            self._usage[user_id] = stamps
            raise tc.ToolError("cooldown", tool="domain", retry=f"{wait} s")
        stamps.append(now)
        self._usage[user_id] = stamps
        if len(self._usage) > 2048:
            self._usage = {u: s for u, s in self._usage.items() if s and now - s[-1] < 60}

    def _count(self, name: str, **dims) -> None:
        stats = getattr(self.bot, "stats", None)
        if stats is None:
            return
        try:
            stats.incr(name, dims=dims)
        except Exception:  # noqa: BLE001 — statistics never break a command
            pass

    async def _start(self, interaction: discord.Interaction, raw: str, *, allow_ip: bool = False,
                     loading_text: Optional[str] = None):
        """Validate the input and open the loading card.

        Returns ``(domain, locale, ephemeral)``, or ``None`` after answering with
        an error card.
        """
        locale = i18n.get_user_locale(interaction)
        ephemeral = get_incognito_setting(interaction)
        try:
            self.check_rate(interaction.user.id)
            domain = svc.parse_domain(raw, allow_ip=allow_ip)
        except tc.ToolError as exc:
            await tc.respond(interaction, view=_tool_error(exc, locale), ephemeral=True)
            return None
        await interaction.response.send_message(
            view=tc.loading_card(locale, loading_text), ephemeral=ephemeral)
        return domain, locale, ephemeral

    async def _show(self, interaction: discord.Interaction, view, files=()) -> None:
        kwargs = {"view": view, "allowed_mentions": tc.NO_MENTIONS}
        if files:
            kwargs["attachments"] = list(files)
        await interaction.edit_original_response(**kwargs)

    # ------------------------------------------------------------------ #
    # /whois
    # ------------------------------------------------------------------ #

    @app_commands.command(name="whois",
                          description="Who registered a domain, and when (registrar, dates, name servers)")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(domain="Domain name or link, for example example.com",
                           incognito="Make response visible only to you")
    @add_incognito_option()
    async def whois_command(self, interaction: discord.Interaction,
                            domain: app_commands.Range[str, 1, 300],
                            incognito: Optional[bool] = None):
        started = await self._start(interaction, domain)
        if started is None:
            return
        parsed, locale, ephemeral = started
        try:
            record = await self.service.whois(parsed)
        except tc.ToolError as exc:
            await self._show(interaction, _tool_error(exc, locale))
            return
        self._count("domain_tools.used", tool="whois")
        await self._show(interaction, views.build_whois_card(
            record, parsed, locale, requester_id=None if ephemeral else interaction.user.id))

    # ------------------------------------------------------------------ #
    # /dns
    # ------------------------------------------------------------------ #

    @app_commands.command(name="dns",
                          description="DNS records of a domain (A, MX, TXT, NS...) and e-mail protection")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(domain="Domain name or link, for example example.com",
                           incognito="Make response visible only to you")
    @add_incognito_option()
    async def dns_command(self, interaction: discord.Interaction,
                          domain: app_commands.Range[str, 1, 300],
                          incognito: Optional[bool] = None):
        started = await self._start(interaction, domain)
        if started is None:
            return
        parsed, locale, ephemeral = started
        try:
            lookup = await self.service.dns(parsed.ascii)
        except tc.ToolError as exc:
            await self._show(interaction, _tool_error(exc, locale))
            return
        self._count("domain_tools.used", tool="dns")
        view, files = views.build_dns_card(
            lookup, views.TYPE_ALL, locale,
            requester_id=None if ephemeral else interaction.user.id)
        await self._show(interaction, view, files)

    # ------------------------------------------------------------------ #
    # /ssl
    # ------------------------------------------------------------------ #

    @app_commands.command(name="ssl",
                          description="Check the HTTPS certificate of a website (validity, issuer, names)")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(domain="Domain name or link, for example example.com",
                           port="Port (default 443)",
                           incognito="Make response visible only to you")
    @app_commands.choices(port=[app_commands.Choice(name=str(p), value=p)
                                for p in tls.ALLOWED_PORTS])
    @add_incognito_option()
    async def ssl_command(self, interaction: discord.Interaction,
                          domain: app_commands.Range[str, 1, 300],
                          port: Optional[app_commands.Choice[int]] = None,
                          incognito: Optional[bool] = None):
        started = await self._start(interaction, domain, allow_ip=True)
        if started is None:
            return
        parsed, locale, ephemeral = started
        chosen = port.value if port else _port_in(domain)
        try:
            result = await self.service.tls(parsed.ascii, chosen)
        except tc.ToolError as exc:
            await self._show(interaction, _tool_error(exc, locale))
            return
        self._count("domain_tools.used", tool="ssl")
        await self._show(interaction, views.build_ssl_card(
            result, parsed.unicode, locale,
            requester_id=None if ephemeral else interaction.user.id))

    # ------------------------------------------------------------------ #
    # /subdomains
    # ------------------------------------------------------------------ #

    @app_commands.command(name="subdomains",
                          description="List the subdomains of a domain seen in public certificates")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(domain="Domain name or link, for example example.com",
                           incognito="Make response visible only to you")
    @add_incognito_option()
    async def subdomains_command(self, interaction: discord.Interaction,
                                 domain: app_commands.Range[str, 1, 300],
                                 incognito: Optional[bool] = None):
        started = await self._start(interaction, domain)
        if started is None:
            return
        parsed, locale, ephemeral = started
        # "www.example.com" means "example.com"; any other subdomain is searched as typed.
        name = parsed.ascii
        if parsed.is_subdomain and name.startswith("www."):
            name = name[4:]
        try:
            result = await self.service.subdomains(name)
        except tc.ToolError as exc:
            await self._show(interaction, views.build_crtsh_error(exc, name, locale))
            return
        self._count("domain_tools.used", tool="subdomains")
        await self._show(interaction, views.build_subdomains_card(
            result, 0, locale, requester_id=None if ephemeral else interaction.user.id))

    # ------------------------------------------------------------------ #
    # /link-check and the menu
    # ------------------------------------------------------------------ #

    @app_commands.command(name="link-check",
                          description="Check if a link is dangerous: redirects, domain age, reputation")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(url="The link to check",
                           incognito="Make response visible only to you")
    @add_incognito_option()
    async def link_check_command(self, interaction: discord.Interaction,
                                 url: app_commands.Range[str, 1, lc.MAX_URL_LENGTH],
                                 incognito: Optional[bool] = None):
        locale = i18n.get_user_locale(interaction)
        ephemeral = get_incognito_setting(interaction)
        try:
            self.check_rate(interaction.user.id)
            prepared = self.prepare(url)
        except tc.ToolError as exc:
            await tc.respond(interaction, view=_tool_error(exc, locale), ephemeral=True)
            return
        await interaction.response.send_message(
            view=tc.loading_card(locale, t("tools.linkcheck.following", locale=locale)),
            ephemeral=ephemeral)
        await self.run_link_check(interaction, prepared, locale, ephemeral=ephemeral,
                                  source="slash")

    @staticmethod
    def prepare(raw: str) -> lc.PreparedUrl:
        """Normalise a typed link. Raises a ``ToolError`` (tool ``linkcheck`` / ``domain``)."""
        try:
            prepared = lc.prepare_url(raw)
        except lc.BadURL as exc:
            raise tc.ToolError(exc.code, tool="linkcheck") from None
        host = urlsplit(prepared.url).hostname or ""
        try:
            domain = parse_target(host, allow_ip=True)
        except DomainError as exc:
            raise tc.ToolError(exc.code, tool="domain") from None
        return lc.PreparedUrl(url=_with_ascii_host(prepared.url, domain),
                              had_userinfo=prepared.had_userinfo)

    async def run_link_check(self, interaction: discord.Interaction, prepared: lc.PreparedUrl,
                             locale: str, *, ephemeral: bool, source: str) -> None:
        """Full check of one link on the already-sent loading card."""
        requester = None if ephemeral else interaction.user.id
        service = self.service
        target = await service.inspect_link(prepared)
        final_url = target.chain.final_url

        use_vt, use_scan = service.providers()
        reached = target.signals.reached
        vt = svc.VtOutcome(views.RUNNING if use_vt else svc.UNAVAILABLE)
        scan = svc.ScanOutcome(views.RUNNING if (use_scan and reached) else
                               (svc.SKIPPED if use_scan else svc.UNAVAILABLE))
        plan = None
        if use_vt or use_scan:
            plan = await service.quota_plan(interaction.user.id, interaction.guild_id)
            if plan is None:
                vt = svc.VtOutcome(svc.QUOTA if use_vt else svc.UNAVAILABLE)
                scan = svc.ScanOutcome(svc.QUOTA if (use_scan and reached) else scan.state)
                use_vt = use_scan = False
        if use_vt:
            from gateway.adapters.virustotal import gui_url
            vt.link = gui_url(final_url)

        async def render():
            await self._show(interaction, views.build_link_card(
                target, vt, scan, locale, requester_id=requester))

        try:
            await render()
        except discord.NotFound:
            return

        running = use_vt or (use_scan and reached)
        if running:
            meta = {"user_id": interaction.user.id, "guild_id": interaction.guild_id}
            charged = "vt" if use_vt else "scan"
            tasks: Dict[asyncio.Task, str] = {}
            if use_vt:
                tasks[asyncio.create_task(service.check_virustotal(
                    final_url, plan=plan if charged == "vt" else None, metadata=meta))] = "vt"
            if use_scan and reached:
                tasks[asyncio.create_task(service.check_urlscan(
                    final_url, plan=plan if charged == "scan" else None, metadata=meta))] = "scan"
            try:
                pending = set(tasks)
                while pending:
                    done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        outcome = task.result()
                        if tasks[task] == "vt":
                            vt = outcome
                        else:
                            scan = outcome
                    try:
                        await render()
                    except discord.NotFound:
                        return
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()

        result = lc.assess(target.signals,
                           vt.result if vt.state == "ok" else None,
                           scan.result if scan.state == "ok" else None)
        verdict = "unreachable" if (not reached and result.level == lc.LEVEL_SAFE) else result.level
        self._count("link_check.used", source=source, verdict=verdict)


def _port_in(raw: str) -> int:
    """The port typed inside the input (``example.com:8443``) when /ssl allows it."""
    text = (raw or "").strip()
    try:
        port = urlsplit(text if "://" in text else "//" + text).port
    except ValueError:
        port = None
    return port if port in tls.ALLOWED_PORTS else tls.DEFAULT_PORT


def _with_ascii_host(url: str, domain: DomainName) -> str:
    """The URL with its host in punycode, so every client sends the same bytes."""
    parts = urlsplit(url)
    host = domain.ascii
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))


async def setup(bot):
    await bot.add_cog(ToolsWeb(bot))
