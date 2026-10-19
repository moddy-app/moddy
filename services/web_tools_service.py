"""
Network side of the domain and link tools: ``/whois``, ``/dns``, ``/ssl``,
``/subdomains``, ``/link-check`` and the "Check links" menu.

The pure logic (parsing, verdicts) lives in ``tools/domain/`` and
``tools/linkcheck.py``; the cards live in ``utils/tools_web_views.py``. This
module only moves bytes:

* RDAP, DNS-over-HTTPS and crt.sh are keyless public APIs on fixed hosts and go
  through ``bot.public_api`` (public-only resolver, per-host concurrency,
  Redis cache);
* ``/ssl`` opens a TLS connection itself, to an address it has just checked is
  public (the address, not the name, is what it connects to);
* a URL typed by a person (``/link-check``) is fetched hop by hop with the same
  public-only resolver and never with the user's credentials;
* VirusTotal and urlscan.io are paid-tier providers behind the gateway; both
  are optional and every failure degrades into a state the card can explain.

See docs/features/TOOLS_WEB.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import socket
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import aiohttp

from services.public_api import PublicAPIError
from tools import linkcheck as lc
from tools.domain import crtsh, doh, rdap, tls
from tools.domain.names import (
    DomainError, DomainName, is_public_ip, parse_ip, parse_target, shared_hosting_suffix,
)
from utils import safe_http
from utils.tool_card import ToolError

logger = logging.getLogger("moddy.web_tools")

RDAP_TIMEOUT = 12.0
RDAP_CACHE_TTL = 3600
BOOTSTRAP_TTL = 24 * 3600
DOH_TIMEOUT = 6.0
DNS_CACHE_TTL = 300
TLS_TIMEOUT = 8.0
SUBDOMAINS_TIMEOUT = 30.0
SUBDOMAINS_CACHE_TTL = 6 * 3600
SUBDOMAINS_MAX_BYTES = 24 * 1024 * 1024
FETCH_TIMEOUT = 8.0
FETCH_BODY_BYTES = 32 * 1024
REPUTATION_CACHE_TTL = 15 * 60
VT_REPORT_MAX_AGE = 3 * 86400
VT_POLLS = (15.0, 10.0)  # seconds to wait before each analysis poll
CALL_TYPE = "link_check"

# Provider states, mapped to ``tools.linkcheck.provider.<state>`` by the card.
OK = "ok"
PENDING = "pending"
UNAVAILABLE = "unavailable"
QUOTA = "quota"
BUSY = "busy"
REFUSED = "refused"
FAILED = "failed"
SKIPPED = "skipped"


# --------------------------------------------------------------------------- #
# Input -> errors
# --------------------------------------------------------------------------- #


def parse_domain(raw: str, *, allow_ip: bool = False) -> DomainName:
    """Validate what was typed. Raises a ``ToolError`` of the ``domain`` tool."""
    try:
        return parse_target(raw, allow_ip=allow_ip)
    except DomainError as exc:
        raise ToolError(exc.code, tool="domain") from None


def api_error(exc: PublicAPIError, tool: str) -> ToolError:
    """A :class:`PublicAPIError` -> the ``ToolError`` of the same code."""
    code = exc.code if exc.code in ("timeout", "network", "rate_limited") else "unavailable"
    return ToolError(code, tool=tool)


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #


@dataclass
class DnsLookup:
    name: str
    answers: Dict[str, doh.DnsAnswer] = field(default_factory=dict)
    failed: List[str] = field(default_factory=list)   # record types that could not be asked
    hints: Optional[doh.EmailHints] = None
    fetched_at: float = 0.0

    @property
    def authenticated(self) -> bool:
        return any(a.authenticated for a in self.answers.values())

    def to_json(self) -> Dict[str, Any]:
        return {
            "name": self.name, "failed": self.failed, "fetched_at": self.fetched_at,
            "answers": {k: {"rcode": a.rcode, "ad": a.authenticated,
                            "records": [[r.type, r.name, r.ttl, r.value, r.priority]
                                        for r in a.records]}
                        for k, a in self.answers.items()},
            "hints": None if self.hints is None else {
                "spf": self.hints.spf, "spf_all": self.hints.spf_all,
                "dmarc": self.hints.dmarc, "dmarc_policy": self.hints.dmarc_policy,
                "dkim": self.hints.dkim},
        }

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "DnsLookup":
        out = cls(name=data["name"], failed=list(data.get("failed") or []),
                  fetched_at=float(data.get("fetched_at") or 0))
        for rtype, raw in (data.get("answers") or {}).items():
            out.answers[rtype] = doh.DnsAnswer(
                rcode=int(raw.get("rcode", 0)), authenticated=bool(raw.get("ad")),
                records=[doh.DnsRecord(type=r[0], name=r[1], ttl=r[2], value=r[3], priority=r[4])
                         for r in raw.get("records") or []])
        h = data.get("hints")
        if h:
            out.hints = doh.EmailHints(spf=h.get("spf"), spf_all=h.get("spf_all"),
                                       dmarc=h.get("dmarc"), dmarc_policy=h.get("dmarc_policy"),
                                       dkim=list(h.get("dkim") or []))
        return out


@dataclass
class TlsResult:
    host: str
    port: int
    ip: str
    cert: tls.CertInfo
    verified: bool
    reasons: List[str]
    version: str = ""
    cipher: str = ""

    @property
    def days_left(self) -> Optional[int]:
        return tls.days_left(self.cert)


@dataclass
class VtOutcome:
    state: str
    result: Optional[lc.VtResult] = None
    link: Optional[str] = None
    stale: bool = False          # an older report was used (a fresh analysis was not ready)


@dataclass
class ScanOutcome:
    state: str
    result: Optional[lc.UrlscanResult] = None
    report_url: Optional[str] = None
    screenshot_url: Optional[str] = None
    page_ip: Optional[str] = None
    country: Optional[str] = None
    title: Optional[str] = None


@dataclass
class LinkTarget:
    """The local part of a check: chain, domain facts, signals."""

    prepared: lc.PreparedUrl
    chain: lc.Chain
    signals: lc.Signals
    final_unicode: str = ""


# --------------------------------------------------------------------------- #
# The service
# --------------------------------------------------------------------------- #


class WebToolsService:
    def __init__(self, bot):
        self.bot = bot
        self._bootstrap: Optional[Tuple[float, Dict[str, str]]] = None
        self._tls_sem = asyncio.Semaphore(4)

    @property
    def api(self):
        api = getattr(self.bot, "public_api", None)
        if api is None:
            raise ToolError("unavailable", tool="domain")
        return api

    # ------------------------------------------------------------------ #
    # RDAP (/whois, domain age of /link-check)
    # ------------------------------------------------------------------ #

    async def _rdap_servers(self) -> Dict[str, str]:
        now = time.monotonic()
        if self._bootstrap and now - self._bootstrap[0] < 3600:
            return self._bootstrap[1]
        api = self.api
        key = api.cache_key("rdap_bootstrap", "v1")
        mapping = await api.cache_get(key)
        if not isinstance(mapping, dict) or not mapping:
            try:
                data = await api.get_json(rdap.BOOTSTRAP_URL, timeout=RDAP_TIMEOUT)
            except PublicAPIError:
                return self._bootstrap[1] if self._bootstrap else {}
            mapping = rdap.bootstrap_map(data)
            if mapping:
                await api.cache_set(key, mapping, BOOTSTRAP_TTL)
        self._bootstrap = (now, mapping)
        return mapping

    async def whois(self, domain: DomainName) -> rdap.DomainRecord:
        """RDAP record of the registrable domain. Raises ``ToolError`` (tool ``whois``)."""
        # On a hosting platform (foo.github.io) the customer owns no registration:
        # the record shown is the platform's own.
        name = (shared_hosting_suffix(domain.ascii) if domain.shared_hosting else None) \
            or domain.registrable_ascii
        api = self.api
        key = api.cache_key("rdap", name)
        cached = await api.cache_get(key)
        if isinstance(cached, dict) and cached.get("name"):
            return rdap.DomainRecord.from_json(cached)

        servers = await self._rdap_servers()
        base = servers.get(domain.suffix.rsplit(".", 1)[-1])
        url = rdap.endpoint(base, name) if base else rdap.FALLBACK_URL.format(name=name)
        try:
            resp = await api.request(
                "GET", url, headers={"Accept": "application/rdap+json, application/json"},
                timeout=RDAP_TIMEOUT, accept_statuses=(404,))
        except PublicAPIError as exc:
            if exc.code == "not_found":
                resp = None
            else:
                raise api_error(exc, "whois") from None
        if resp is None or resp.status == 404:
            raise ToolError("not_registered" if base else "no_rdap", tool="whois",
                            domain=domain.registrable_unicode, tld=domain.suffix)
        try:
            record = rdap.parse_domain(resp.json(), server=urlsplit(url).hostname)
        except PublicAPIError:
            raise ToolError("unavailable", tool="whois") from None
        if not record.name:
            record.name = name

        # Thin registries (.com) only know the registrar: its own record has
        # the expiry date, the statuses and the name servers.
        if record.related_url and (record.expires is None or not record.nameservers):
            try:
                extra_resp = await api.request(
                    "GET", record.related_url, headers={"Accept": "application/rdap+json"},
                    timeout=8.0)
                extra = rdap.parse_domain(extra_resp.json(),
                                          server=urlsplit(record.related_url).hostname)
                record = rdap.merge(record, extra)
            except (PublicAPIError, ValueError):
                pass
        await api.cache_set(key, record.to_json(), RDAP_CACHE_TTL)
        return record

    # ------------------------------------------------------------------ #
    # DNS-over-HTTPS (/dns)
    # ------------------------------------------------------------------ #

    async def _doh(self, name: str, rtype: str) -> dict:
        last: Optional[PublicAPIError] = None
        for url in (doh.PRIMARY_URL, doh.FALLBACK_URL):
            try:
                return await self.api.get_json(
                    url, params={"name": name, "type": rtype},
                    headers={"Accept": doh.ACCEPT}, timeout=DOH_TIMEOUT)
            except PublicAPIError as exc:
                last = exc
        raise last or PublicAPIError("network")

    async def dns(self, name: str) -> DnsLookup:
        """All record types of ``name`` plus e-mail authentication hints."""
        api = self.api
        key = api.cache_key("dns", name)
        cached = await api.cache_get(key)
        if isinstance(cached, dict) and cached.get("name") == name:
            return DnsLookup.from_json(cached)

        async def one(rtype: str):
            return rtype, await self._doh(name, rtype)

        outcomes = await asyncio.gather(*(one(t) for t in doh.RECORD_TYPES), return_exceptions=True)
        lookup = DnsLookup(name=name, fetched_at=time.time())
        last_error: Optional[PublicAPIError] = None
        for rtype, outcome in zip(doh.RECORD_TYPES, outcomes):
            if isinstance(outcome, BaseException):
                if isinstance(outcome, asyncio.CancelledError):
                    raise outcome
                if isinstance(outcome, PublicAPIError):
                    last_error = outcome
                else:
                    logger.warning("dns lookup failed: %s", outcome)
                lookup.failed.append(rtype)
                continue
            lookup.answers[rtype] = doh.parse_response(outcome[1], rtype)
        if not lookup.answers:
            raise api_error(last_error, "dns") if last_error else ToolError("unavailable", tool="dns")
        if any(a.nxdomain for a in lookup.answers.values()):
            raise ToolError("nxdomain", tool="dns", domain=name)

        lookup.hints = await self._email_hints(name, lookup)
        await api.cache_set(key, lookup.to_json(), DNS_CACHE_TTL)
        return lookup

    async def _email_hints(self, name: str, lookup: DnsLookup) -> Optional[doh.EmailHints]:
        txt = [r.value for r in (lookup.answers.get("TXT") or doh.DnsAnswer()).records]

        async def probe(qname: str):
            try:
                return await self._doh(qname, "TXT")
            except PublicAPIError:
                return None

        names = [f"_dmarc.{name}"] + [f"{sel}._domainkey.{name}" for sel in doh.DKIM_SELECTORS]
        payloads = await asyncio.gather(*(probe(n) for n in names))
        dmarc_txt: List[str] = []
        if payloads[0]:
            dmarc_txt = [r.value for r in doh.parse_response(payloads[0], "TXT").records]
        dkim = [sel for sel, payload in zip(doh.DKIM_SELECTORS, payloads[1:])
                if payload and doh.has_answer(payload)]
        hints = doh.build_hints(txt, dmarc_txt, dkim)
        return hints if hints.any else None

    # ------------------------------------------------------------------ #
    # TLS (/ssl)
    # ------------------------------------------------------------------ #

    async def _public_addresses(self, host: str, port: int) -> List[str]:
        ip = parse_ip(host)
        if ip is not None:
            if not is_public_ip(ip):
                raise ToolError("private_address", tool="domain")
            return [str(ip)]
        loop = asyncio.get_running_loop()
        try:
            infos = await asyncio.wait_for(
                loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), TLS_TIMEOUT)
        except asyncio.TimeoutError:
            raise ToolError("timeout", tool="ssl") from None
        except OSError:
            raise ToolError("dns_failed", tool="ssl", host=host) from None
        addresses = []
        for family, _, _, _, sockaddr in sorted(infos, key=lambda i: i[0] != socket.AF_INET):
            address = sockaddr[0]
            if address not in addresses:
                addresses.append(address)
        public = [a for a in addresses if is_public_ip(a)]
        if not public:
            raise ToolError("private_address", tool="domain")
        return public

    @staticmethod
    async def _handshake(ip: str, port: int, host: str, verify: bool):
        """One TLS handshake. Returns ``(der, version, cipher)``."""
        ctx = ssl.create_default_context()
        if not verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port, ssl=ctx, server_hostname=host,
                                    ssl_handshake_timeout=TLS_TIMEOUT), TLS_TIMEOUT)
        try:
            sslobj = writer.get_extra_info("ssl_object")
            der = sslobj.getpeercert(binary_form=True)
            cipher = sslobj.cipher()
            return der, sslobj.version() or "", (cipher[0] if cipher else "")
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 2.0)
            except Exception:  # noqa: BLE001 — the answer is already in hand
                pass

    async def tls(self, host: str, port: int = tls.DEFAULT_PORT) -> TlsResult:
        """Inspect the certificate a server presents. Raises ``ToolError`` (tool ``ssl``)."""
        if port not in tls.ALLOWED_PORTS:
            raise ToolError("bad_port", tool="ssl")
        addresses = await self._public_addresses(host, port)
        last: Optional[ToolError] = None
        async with self._tls_sem:
            for ip in addresses[:2]:
                try:
                    return await self._inspect(host, port, ip)
                except ToolError as exc:
                    last = exc
                    if exc.code not in ("timeout", "unreachable"):
                        raise
        raise last or ToolError("unreachable", tool="ssl")

    async def _inspect(self, host: str, port: int, ip: str) -> TlsResult:
        verified = True
        verify_code: Optional[int] = None
        try:
            try:
                der, version, cipher = await self._handshake(ip, port, host, True)
            except ssl.SSLCertVerificationError as exc:
                verified = False
                verify_code = exc.verify_code
                der, version, cipher = await self._handshake(ip, port, host, False)
        except asyncio.TimeoutError:
            raise ToolError("timeout", tool="ssl") from None
        except ssl.SSLError:
            raise ToolError("tls_failed", tool="ssl", port=port) from None
        except (ConnectionError, OSError):
            raise ToolError("unreachable", tool="ssl", host=host, port=port) from None
        if not der:
            raise ToolError("tls_failed", tool="ssl", port=port)
        try:
            cert = tls.describe_certificate(der)
        except ValueError:
            raise ToolError("tls_failed", tool="ssl", port=port) from None
        reasons = [] if verified else (tls.diagnose(cert, host, verify_code=verify_code)
                                       or [tls.INVALID])
        return TlsResult(host=host, port=port, ip=ip, cert=cert, verified=verified,
                         reasons=reasons, version=version, cipher=cipher)

    # ------------------------------------------------------------------ #
    # crt.sh (/subdomains)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_crt(body: bytes, domain: str) -> crtsh.SubdomainResult:
        data = json.loads(body.decode("utf-8"))
        if not isinstance(data, list):
            raise ValueError("unexpected crt.sh payload")
        return crtsh.extract(data, domain)

    async def subdomains(self, domain: str) -> crtsh.SubdomainResult:
        """Names found in Certificate Transparency logs. Raises ``ToolError`` (tool ``subdomains``)."""
        api = self.api
        key = api.cache_key("crtsh", domain)
        cached = await api.cache_get(key)
        if isinstance(cached, dict) and cached.get("domain") == domain:
            return crtsh.SubdomainResult(**cached)

        api.set_host_limit("crt.sh", 2)
        try:
            resp = await api.request(
                "GET", crtsh.SEARCH_URL, params=crtsh.query_params(domain),
                timeout=SUBDOMAINS_TIMEOUT, max_bytes=SUBDOMAINS_MAX_BYTES)
        except PublicAPIError as exc:
            if exc.code == "too_large":
                raise ToolError("too_large", tool="subdomains", domain=domain) from None
            if exc.code == "rate_limited":
                raise ToolError("rate_limited", tool="subdomains") from None
            if exc.code == "timeout":
                raise ToolError("crtsh_down", tool="subdomains", reason="timeout") from None
            raise ToolError("crtsh_down", tool="subdomains", reason=exc.code) from None
        try:
            result = await asyncio.to_thread(self._parse_crt, resp.body, domain)
        except (ValueError, UnicodeDecodeError):
            raise ToolError("crtsh_down", tool="subdomains", reason="bad_payload") from None
        await api.cache_set(key, {
            "domain": result.domain, "names": result.names, "wildcards": result.wildcards,
            "total": result.total, "certificates": result.certificates}, SUBDOMAINS_CACHE_TTL)
        return result

    # ------------------------------------------------------------------ #
    # /link-check — local part
    # ------------------------------------------------------------------ #

    async def _fetch(self, method: str, url: str, want_body: bool) -> lc.HopResponse:
        session = await self.api.session()
        try:
            async with session.request(
                method, url, allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT),
                headers={"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"},
            ) as resp:
                body = b""
                ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if want_body and resp.status < 400 and ("html" in ctype or not ctype):
                    chunks, size = [], 0
                    async for chunk in resp.content.iter_chunked(8192):
                        chunks.append(chunk)
                        size += len(chunk)
                        if size >= FETCH_BODY_BYTES:
                            break
                    body = b"".join(chunks)
                return lc.HopResponse(status=resp.status, location=resp.headers.get("Location"),
                                      content_type=ctype, body=body)
        except asyncio.TimeoutError:
            raise lc.FetchError("timeout") from None
        except aiohttp.ClientSSLError:
            raise lc.FetchError("tls") from None
        except (aiohttp.ClientError, OSError) as exc:
            if "non publique" in str(exc):
                raise lc.FetchError("blocked") from None
            raise lc.FetchError("unreachable") from None

    @staticmethod
    def _validate(url: str) -> str:
        return safe_http.validate_public_url(url)

    async def domain_age(self, host: str) -> Tuple[Optional[int], Optional[str], bool]:
        """``(age in days, age level, shared hosting)`` of a host, ``None`` when RDAP does not know."""
        try:
            domain = parse_target(host, allow_ip=True)
        except DomainError:
            return None, None, False
        if domain.is_ip:
            return None, None, False
        if domain.shared_hosting:
            return None, None, True
        try:
            record = await asyncio.wait_for(self.whois(domain), RDAP_TIMEOUT + 4)
        except (ToolError, asyncio.TimeoutError):
            return None, None, False
        days = rdap.age_days(record.created)
        return days, rdap.age_level(days), False

    async def inspect_link(self, prepared: lc.PreparedUrl) -> LinkTarget:
        """Follow the redirects and gather the local signals."""
        chain = await lc.follow(prepared.url, self._fetch, self._validate)
        final_host = (urlsplit(chain.final_url).hostname or "").lower()
        final_unicode = final_host
        age_days = age_level = None
        shared = False
        try:
            domain = parse_target(final_host, allow_ip=True)
            final_unicode = domain.unicode
        except DomainError:
            domain = None
        if domain is not None and chain.final.status > 0:
            age_days, age_level, shared = await self.domain_age(final_host)
        original_host = (urlsplit(prepared.url).hostname or "").lower()
        signals = lc.build_signals(
            original_host, chain, final_unicode_host=final_unicode, userinfo=prepared.had_userinfo,
            age_days=age_days, age_level=age_level, shared_hosting=shared)
        return LinkTarget(prepared=prepared, chain=chain, signals=signals,
                          final_unicode=final_unicode)

    # ------------------------------------------------------------------ #
    # /link-check — reputation providers (gateway)
    # ------------------------------------------------------------------ #

    def providers(self) -> Tuple[bool, bool]:
        """``(virustotal, urlscan)`` availability."""
        gateway = getattr(self.bot, "gateway", None)
        if gateway is None or not getattr(gateway, "available", False):
            return False, False
        return gateway.virustotal_available(), gateway.urlscan_available()

    async def quota_plan(self, user_id: int, guild_id: Optional[int]):
        """The quota targets of one check, or ``None`` when one is used up."""
        from gateway.spec import QuotaTarget

        plan = [QuotaTarget.user(user_id, CALL_TYPE)]
        if guild_id:
            plan.append(QuotaTarget.guild(guild_id, CALL_TYPE))
        plan.append(QuotaTarget.global_(CALL_TYPE))
        gateway = self.bot.gateway
        for target in plan:
            if not await gateway.quota_available(target):
                return None
        return plan

    @staticmethod
    def _state_for(exc: BaseException) -> str:
        from gateway.errors import (
            APIUnavailableError, ConfigurationError, GatewayTimeoutError, ModelRateLimitError,
            ProviderError, QuotaExceededError, RateLimitError,
        )
        if isinstance(exc, QuotaExceededError):
            return QUOTA
        if isinstance(exc, (ModelRateLimitError, RateLimitError)):
            return BUSY
        if isinstance(exc, ProviderError):
            if exc.status in (402, 403, 429):
                return BUSY
            if exc.status in (400, 404, 410, 422):
                return REFUSED
        if isinstance(exc, (APIUnavailableError, ConfigurationError)):
            return UNAVAILABLE
        if isinstance(exc, (GatewayTimeoutError, asyncio.TimeoutError)):
            return FAILED
        logger.warning("reputation provider failed: %s: %s", type(exc).__name__, exc)
        return FAILED

    @staticmethod
    def _cache_key(api, provider: str, url: str) -> str:
        return api.cache_key(f"linkcheck_{provider}", hashlib.sha256(url.encode()).hexdigest())

    async def check_virustotal(self, url: str, *, plan=None, report_only: bool = False,
                               metadata: Optional[dict] = None) -> VtOutcome:
        """Existing report, else a fresh analysis (``report_only`` never submits)."""
        from gateway.spec import QuotaTarget  # noqa: F401 — keeps the import cost lazy
        from gateway.adapters.virustotal import gui_url

        link = gui_url(url)
        api = self.api
        key = self._cache_key(api, "vt", url)
        cached = await api.cache_get(key)
        if isinstance(cached, dict) and cached.get("stats"):
            return VtOutcome(OK, lc.VtResult(**cached["stats"]), link, bool(cached.get("stale")))

        client = self.bot.gateway.virustotal
        try:
            report = await client.url_report(url, quota=plan, metadata=metadata)
            stats = dict(report.get("stats") or {}) if report.get("found") else None
            fresh = bool(stats) and (time.time() - float(report.get("analysis_date") or 0)
                                     < VT_REPORT_MAX_AGE)
            if fresh and sum(stats.values()) > 0:
                return await self._vt_done(api, key, stats, link, stale=False)
            if report_only:
                if stats and sum(stats.values()) > 0:
                    return await self._vt_done(api, key, stats, link, stale=True)
                return VtOutcome(PENDING, None, link)

            submitted = await client.url_submit(url, metadata=metadata)
            analysis_id = submitted.get("analysis_id")
            for wait in VT_POLLS:
                await asyncio.sleep(wait)
                analysis = await client.analysis(analysis_id, metadata=metadata)
                if analysis.get("status") == "completed":
                    return await self._vt_done(api, key, analysis.get("stats") or {}, link,
                                               stale=False)
            if stats and sum(stats.values()) > 0:
                return await self._vt_done(api, key, stats, link, stale=True, cache=False)
            return VtOutcome(PENDING, None, link)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — every failure becomes a card state
            return VtOutcome(self._state_for(exc), None, link)

    @staticmethod
    async def _vt_done(api, key: str, stats: dict, link: str, *, stale: bool,
                       cache: bool = True) -> VtOutcome:
        values = {k: int(stats.get(k, 0) or 0)
                  for k in ("malicious", "suspicious", "harmless", "undetected")}
        if cache:
            await api.cache_set(key, {"stats": values, "stale": stale}, REPUTATION_CACHE_TTL)
        return VtOutcome(OK, lc.VtResult(**values), link, stale)

    async def check_urlscan(self, url: str, *, plan=None,
                            metadata: Optional[dict] = None) -> ScanOutcome:
        """Sandboxed scan (unlisted) with screenshot."""
        api = self.api
        key = self._cache_key(api, "urlscan", url)
        cached = await api.cache_get(key)
        if isinstance(cached, dict) and cached.get("uuid"):
            return self._scan_outcome(cached)
        client = self.bot.gateway.urlscan
        try:
            submitted = await client.scan(url, quota=plan, metadata=metadata)
            data = await client.result(submitted["uuid"], metadata=metadata)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            return ScanOutcome(self._state_for(exc))
        if not data.get("ready"):
            return ScanOutcome(PENDING, report_url=data.get("report_url"))
        await api.cache_set(key, data, REPUTATION_CACHE_TTL)
        return self._scan_outcome(data)

    @staticmethod
    def _scan_outcome(data: dict) -> ScanOutcome:
        return ScanOutcome(
            OK,
            lc.UrlscanResult(malicious=bool(data.get("malicious")), score=int(data.get("score") or 0),
                             categories=list(data.get("categories") or []),
                             brands=list(data.get("brands") or [])),
            report_url=data.get("report_url"), screenshot_url=data.get("screenshot_url"),
            page_ip=data.get("page_ip"), country=data.get("country"), title=data.get("title"))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
