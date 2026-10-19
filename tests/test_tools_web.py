"""Domain and link tools: /whois /dns /ssl /subdomains /link-check + "Check links" menu.

Pure logic (tools/domain, tools/linkcheck), the network service with a fake
transport, the cards (walked through ``view.walk_children()``), the persistent
items and the i18n coverage. Nothing here touches the network.
"""

import asyncio
import datetime as dt
import re
import types

import discord
from discord import ui
import pytest

from services import web_tools_service as svc
from tools import linkcheck as lc
from tools.domain import crtsh, doh, names, rdap, tls
from utils import tool_card as tc
from utils import tools_web_views as views
from utils.i18n import i18n

LOCALES = ("fr", "en-US", "es-ES", "pt-BR", "de")
ROOTS = ("domain", "whois", "dns", "ssl", "subdomains", "linkcheck")
NOW = dt.datetime(2026, 10, 19, tzinfo=dt.timezone.utc)


def texts(view):
    return [c.content for c in view.walk_children() if isinstance(c, discord.ui.TextDisplay)]


def joined(view):
    return "\n".join(texts(view))


def buttons(view):
    """Buttons and selects, DynamicItem wrappers unwrapped."""
    found = [getattr(c, "item", c) for c in view.walk_children()]
    return [c for c in found if isinstance(c, (discord.ui.Button, discord.ui.Select))]


def accent(view):
    container = next(c for c in view.children if isinstance(c, discord.ui.Container))
    return container.accent_colour.value


# --------------------------------------------------------------------------- #
# names
# --------------------------------------------------------------------------- #


def test_parse_target_accepts_domains_urls_and_idn():
    d = names.parse_target("HTTPS://user:pw@WWW.Example.co.uk:8443/path?q=1")
    assert d.ascii == "www.example.co.uk"
    assert d.registrable_ascii == "example.co.uk" and d.is_subdomain
    idn = names.parse_target("bücher.de")
    assert idn.ascii == "xn--bcher-kva.de" and idn.is_idn and idn.unicode == "bücher.de"
    assert names.parse_target("<example.com>").ascii == "example.com"


def test_shared_hosting_keeps_the_customer_label():
    d = names.parse_target("someone.github.io")
    assert d.shared_hosting and d.registrable_ascii == "someone.github.io"


@pytest.mark.parametrize("raw, code", [
    ("", names.EMPTY), ("not a domain", names.INVALID), ("localhost", names.RESERVED),
    ("printer.local", names.RESERVED), ("com", names.INVALID), ("1.2.3", names.INVALID),
    ("192.168.1.1", names.PRIVATE_ADDRESS), ("8.8.8.8", names.IP_NOT_ALLOWED),
    ("a" * 70 + ".com", names.INVALID), ("-bad-.com", names.INVALID),
])
def test_parse_target_refusals(raw, code):
    with pytest.raises(names.DomainError) as exc:
        names.parse_target(raw)
    assert exc.value.code == code


def test_ip_allowed_when_asked():
    d = names.parse_target("https://8.8.8.8/x", allow_ip=True)
    assert d.is_ip and d.ascii == "8.8.8.8"
    with pytest.raises(names.DomainError):
        names.parse_target("10.0.0.1", allow_ip=True)


def test_parse_domain_maps_to_tool_error():
    with pytest.raises(tc.ToolError) as exc:
        svc.parse_domain("localhost")
    assert exc.value.tool == "domain" and exc.value.code == "reserved"


# --------------------------------------------------------------------------- #
# rdap
# --------------------------------------------------------------------------- #

RDAP_SAMPLE = {
    "ldhName": "EXAMPLE.COM",
    "status": ["client transfer prohibited", "clientTransferProhibited", "ok"],
    "events": [
        {"eventAction": "registration", "eventDate": "2026-10-14T10:00:00Z"},
        {"eventAction": "expiration", "eventDate": "2027-10-14T10:00:00Z"},
        {"eventAction": "last changed", "eventDate": "2026-10-15T10:00:00Z"},
    ],
    "entities": [{"roles": ["registrar"], "vcardArray": ["vcard", [["fn", {}, "text", "ACME Registrar"]]],
                  "publicIds": [{"type": "IANA", "identifier": "292"}]}],
    "nameservers": [{"ldhName": "NS2.EXAMPLE.COM"}, {"ldhName": "ns1.example.com."}],
    "secureDNS": {"delegationSigned": True},
    "links": [{"rel": "related", "type": "application/rdap+json", "href": "https://rdap.acme.test/domain/example.com"}],
}


def test_rdap_parse_domain():
    rec = rdap.parse_domain(RDAP_SAMPLE, server="rdap.example")
    assert rec.name == "example.com" and rec.registrar == "ACME Registrar" and rec.registrar_id == "292"
    assert rec.nameservers == ["ns1.example.com", "ns2.example.com"]
    assert rec.dnssec is True and rec.related_url.startswith("https://rdap.acme.test")
    assert rdap.age_days(rec.created, NOW) == 4
    assert rdap.age_level(5) == "very_recent" and rdap.age_level(20) == "recent" and rdap.age_level(400) is None
    codes, unknown = rdap.summarize_statuses(rec.statuses)
    assert codes == ["transfer_locked"] and unknown == []
    # JSON round trip (Redis cache)
    again = rdap.DomainRecord.from_json(rec.to_json())
    assert again.created == rec.created and again.nameservers == rec.nameservers


def test_rdap_bootstrap_and_merge():
    mapping = rdap.bootstrap_map({"services": [[["com", "net"], ["http://a.test/", "https://rdap.a.test/"]]]})
    assert mapping == {"com": "https://rdap.a.test/", "net": "https://rdap.a.test/"}
    assert rdap.endpoint("https://rdap.a.test/", "x.com") == "https://rdap.a.test/domain/x.com"
    thin = rdap.DomainRecord(name="x.com", registrar="R", created=NOW)
    full = rdap.DomainRecord(name="x.com", expires=NOW, nameservers=["ns1.x.com"], statuses=["ok"])
    merged = rdap.merge(thin, full)
    assert merged.expires == NOW and merged.nameservers == ["ns1.x.com"] and merged.registrar == "R"


# --------------------------------------------------------------------------- #
# doh
# --------------------------------------------------------------------------- #


def test_doh_parsing():
    payload = {"Status": 0, "AD": True, "Answer": [
        {"name": "example.com.", "type": 15, "TTL": 300, "data": "20 mx2.example.com."},
        {"name": "example.com.", "type": 15, "TTL": 300, "data": "10 mx1.example.com."},
        {"name": "example.com.", "type": 5, "TTL": 60, "data": "alias.example.com."},
    ]}
    answer = doh.parse_response(payload, "MX")
    assert [(r.priority, r.value) for r in answer.records] == [(10, "mx1.example.com"), (20, "mx2.example.com")]
    assert answer.authenticated
    assert doh.parse_txt('"v=spf1 " "include:x.test -all"') == "v=spf1 include:x.test -all"
    assert doh.parse_caa("\\# 22 00 05 69 73 73 75 65 6c 65 74 73 65 6e 63 72 79 70 74 2e 6f 72 67") \
        == "0 issue letsencrypt.org"
    assert doh.ttl_parts(300) == (5, "min") and doh.ttl_parts(30) == (30, "s")
    assert doh.parse_response({"Status": 3}, "A").nxdomain


def test_email_hints():
    hints = doh.build_hints(["v=spf1 include:_spf.x.test ~all", "other"],
                            ["v=DMARC1; p=reject; rua=mailto:a@b.test"], ["google"])
    assert hints.spf_all == "~all" and hints.dmarc_policy == "reject" and hints.dkim == ["google"] and hints.any
    assert not doh.build_hints([], [], []).any
    assert doh.spf_all("v=spf1 -all") == "-all" and doh.spf_all("v=spf1 all") == "+all"


# --------------------------------------------------------------------------- #
# crt.sh
# --------------------------------------------------------------------------- #


def test_crtsh_extract():
    rows = [
        {"name_value": "www.example.com\nmail.example.com\n*.example.com", "common_name": "www.example.com"},
        {"name_value": "*.dev.example.com\nadmin@example.com\nother.test", "common_name": "example.com"},
        {"name_value": "example.com", "common_name": "example.com"},
        "garbage",
    ]
    result = crtsh.extract(rows, "example.com")
    assert result.names == ["dev.example.com", "mail.example.com", "www.example.com"]
    assert result.wildcards == 2 and result.total == 3 and result.certificates == 3
    big = crtsh.extract([{"name_value": f"h{i}.example.com"} for i in range(50)], "example.com", cap=10)
    assert big.truncated and len(big.names) == 10 and big.total == 50
    assert crtsh.page_count(0) == 1 and crtsh.page_count(81) == 3
    assert len(crtsh.page(list(range(100)), 2)) == 20


# --------------------------------------------------------------------------- #
# tls
# --------------------------------------------------------------------------- #


def make_cert(cn="example.com", sans=("example.com", "*.example.com"), days=60, self_signed=True):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    issuer = subject if self_signed else x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Test CA"), x509.NameAttribute(NameOID.COMMON_NAME, "R3")])
    start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
    builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(start).not_valid_after(start + dt.timedelta(days=days + 30)))
    if sans:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(s) for s in sans]), False)
    cert = builder.sign(key, hashes.SHA256())
    return cert.public_bytes(serialization.Encoding.DER)


def test_describe_certificate_and_diagnose():
    info = tls.describe_certificate(make_cert(self_signed=False))
    assert info.subject_cn == "example.com" and info.issuer_name == "Test CA"
    assert info.key_type == "EC" and info.sans == ["example.com", "*.example.com"]
    assert 59 <= tls.days_left(info) <= 60
    assert tls.san_preview(info, 1) == ["example.com"]
    assert tls.diagnose(info, "example.com") == []
    assert tls.diagnose(info, "api.example.com") == []                # wildcard
    assert tls.diagnose(info, "evil.test") == [tls.HOSTNAME_MISMATCH]
    selfsigned = tls.describe_certificate(make_cert())
    assert tls.SELF_SIGNED in tls.diagnose(selfsigned, "example.com", verify_code=18)
    expired = tls.describe_certificate(make_cert(days=-5, self_signed=False))
    assert tls.diagnose(expired, "example.com", verify_code=10)[0] == tls.EXPIRED


def test_hostname_matching_rules():
    assert tls.hostname_matches("a.example.com", ["*.example.com"])
    assert not tls.hostname_matches("a.b.example.com", ["*.example.com"])
    assert not tls.hostname_matches("example.com", ["*.example.com"])
    assert tls.hostname_matches("8.8.8.8", ["8.8.8.8"])
    assert tls.hostname_matches("example.com", [], "example.com")      # CN only without SAN


# --------------------------------------------------------------------------- #
# linkcheck
# --------------------------------------------------------------------------- #


def test_extract_and_prepare_urls():
    found = lc.extract_urls("see (https://a.test/x_(1)) and www.b.test, https://a.test/x_(1)!", limit=5)
    assert found == ["https://a.test/x_(1)", "https://www.b.test"]
    prepared = lc.prepare_url("Example.com/Path?q=1#frag")
    assert prepared.url == "https://example.com/Path?q=1" and not prepared.had_userinfo
    assert lc.prepare_url("https://paypal.com@evil.test/login").had_userinfo
    for raw, code in (("", "empty"), ("ftp://x.test", "unsupported_scheme"), ("a b", "invalid_url")):
        with pytest.raises(lc.BadURL) as exc:
            lc.prepare_url(raw)
        assert exc.value.code == code
    assert lc.display_url("https://example.com/a/b?x=1") == "example.com/a/b?…"


def fake_fetcher(routes):
    async def fetch(method, url, want_body):
        step = routes[url]
        if isinstance(step, lc.FetchError):
            raise step
        return step
    return fetch


async def test_follow_redirect_chain_and_meta_refresh():
    routes = {
        "https://bit.ly/a": lc.HopResponse(301, "https://mid.test/"),
        "https://mid.test/": lc.HopResponse(200, None, "text/html",
                                           b'<meta http-equiv="refresh" content="0; url=/end">'),
        "https://mid.test/end": lc.HopResponse(200, None, "image/png"),
    }
    chain = await lc.follow("https://bit.ly/a", fake_fetcher(routes), lambda u: u)
    assert [h.status for h in chain.hops] == [301, 200, 200]
    assert chain.hops[2].kind == "meta" and chain.final_url == "https://mid.test/end"
    assert chain.error is None and chain.redirects == 2


async def test_follow_loop_blocked_and_errors():
    loop = {"https://a.test/": lc.HopResponse(302, "https://b.test/"),
            "https://b.test/": lc.HopResponse(302, "https://a.test/")}
    assert (await lc.follow("https://a.test/", fake_fetcher(loop), lambda u: u)).error == "loop"

    def refuse(url):
        raise ValueError("private")
    blocked = await lc.follow("https://x.test/", fake_fetcher({}), refuse)
    assert blocked.error == "blocked" and blocked.final.status == 0

    down = await lc.follow("https://x.test/", fake_fetcher({"https://x.test/": lc.FetchError("timeout")}),
                           lambda u: u)
    assert down.error == "timeout"
    endless = {f"https://h{i}.test/": lc.HopResponse(302, f"https://h{i + 1}.test/") for i in range(30)}
    assert (await lc.follow("https://h0.test/", fake_fetcher(endless), lambda u: u)).error == "too_many_redirects"


def test_idn_signals():
    assert lc.idn_signal("example.com") is None
    assert lc.idn_signal("аррӏе.com") == "lookalike"          # Cyrillic letters reading as "apple"
    assert lc.idn_signal("pаypal.com") == "mixed_script"      # Latin + one Cyrillic letter
    assert lc.idn_signal("日本語.jp") is None
    assert lc.is_shortener("bit.ly") and lc.is_shortener("www.t.co") and not lc.is_shortener("example.com")


def signals(**kw):
    base = dict(host="x.test", final_host="x.test", https=True, reached=True)
    base.update(kw)
    return lc.Signals(**base)


def test_verdict_aggregation():
    assert lc.assess(signals()).level == lc.LEVEL_SAFE
    assert lc.assess(signals(shortener=True, https=False)).level == lc.LEVEL_SAFE     # weak signals only
    assert lc.assess(signals(age_level="recent", age_days=12)).level == lc.LEVEL_SUSPICIOUS
    assert lc.assess(signals(), lc.VtResult(malicious=5)).level == lc.LEVEL_DANGEROUS
    assert lc.assess(signals(), lc.VtResult(malicious=1)).level == lc.LEVEL_SUSPICIOUS
    assert lc.assess(signals(), None, lc.UrlscanResult(malicious=True)).level == lc.LEVEL_DANGEROUS
    stacked = signals(age_level="very_recent", age_days=2, idn="mixed_script", userinfo=True)
    verdict = lc.assess(stacked)
    assert verdict.level == lc.LEVEL_DANGEROUS
    assert [r.severity for r in verdict.reasons] == sorted((r.severity for r in verdict.reasons),
                                                           key={"hard": 0, "soft": 1, "info": 2}.get)


# --------------------------------------------------------------------------- #
# Service with a fake transport
# --------------------------------------------------------------------------- #


class FakeResponse:
    def __init__(self, data, status=200):
        import json
        self.status = status
        self.body = json.dumps(data).encode() if not isinstance(data, bytes) else data

    def json(self):
        import json
        return json.loads(self.body)


class FakeAPI:
    """Stands in for bot.public_api: ``routes`` maps a URL substring to a payload."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []
        self.cache = {}

    def cache_key(self, ns, *parts):
        return f"{ns}:{parts}"

    async def cache_get(self, key):
        return self.cache.get(key)

    async def cache_set(self, key, value, ttl):
        self.cache[key] = value

    def set_host_limit(self, host, limit):
        pass

    def _match(self, url, params):
        self.calls.append((url, params))
        for needle, payload in self.routes.items():
            if needle in url or (params and needle in str(params)):
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise AssertionError(f"unexpected request {url} {params}")

    async def get_json(self, url, *, params=None, **kw):
        return self._match(url, params)

    async def request(self, method, url, *, params=None, accept_statuses=(), **kw):
        payload = self._match(url, params)
        if isinstance(payload, FakeResponse):
            return payload
        return FakeResponse(payload)


def service(routes):
    from services.public_api import PublicAPIError  # noqa: F401
    bot = types.SimpleNamespace(public_api=FakeAPI(routes), redis=None)
    return svc.WebToolsService(bot), bot.public_api


async def test_whois_uses_bootstrap_and_registrar_record():
    thin = dict(RDAP_SAMPLE, events=[RDAP_SAMPLE["events"][0]], nameservers=[])
    s, api = service({
        "data.iana.org": {"services": [[["com"], ["https://rdap.registry.test/"]]]},
        "rdap.registry.test": thin,
        "rdap.acme.test": RDAP_SAMPLE,
    })
    record = await s.whois(names.parse_target("www.example.com"))
    assert record.registrar == "ACME Registrar"
    assert record.expires is not None and record.nameservers      # merged from the registrar's record
    assert any("rdap.registry.test/domain/example.com" in c[0] for c in api.calls)
    cached = await s.whois(names.parse_target("example.com"))     # served from the cache
    assert cached.name == "example.com"


async def test_whois_not_registered_and_no_rdap():
    from services.public_api import PublicAPIError
    s, _ = service({
        "data.iana.org": {"services": [[["com"], ["https://rdap.registry.test/"]]]},
        "rdap.registry.test": PublicAPIError("not_found", status=404),
    })
    with pytest.raises(tc.ToolError) as exc:
        await s.whois(names.parse_target("nothing-here.com"))
    assert exc.value.code == "not_registered" and exc.value.tool == "whois"
    s2, _ = service({
        "data.iana.org": {"services": []},
        "rdap.org": PublicAPIError("not_found", status=404),
    })
    with pytest.raises(tc.ToolError) as exc2:
        await s2.whois(names.parse_target("example.zz"))
    assert exc2.value.code == "no_rdap" and exc2.value.params["tld"] == ".zz"


async def test_dns_lookup_collects_types_and_hints():
    def answer(qtype, data, **kw):
        return {"Status": 0, "AD": True, "Answer": [{"name": "example.com.", "type": qtype, "TTL": 300,
                                                       "data": data}], **kw}

    def route(tag, payload):
        return f"'name': '{tag}'", payload

    s, api = service({})
    async def doh_call(name, rtype):
        if name == "_dmarc.example.com":
            return answer(16, '"v=DMARC1; p=quarantine"')
        if name.endswith("._domainkey.example.com"):
            return {"Status": 3} if not name.startswith("google") else answer(16, '"v=DKIM1; k=rsa"')
        return {"A": answer(1, "93.184.216.34"), "MX": answer(15, "10 mail.example.com."),
                "TXT": answer(16, '"v=spf1 -all"')}.get(rtype, {"Status": 0})
    s._doh = doh_call
    lookup = await s.dns("example.com")
    assert [r.value for r in lookup.answers["A"].records] == ["93.184.216.34"]
    assert lookup.hints.spf_all == "-all" and lookup.hints.dmarc_policy == "quarantine"
    assert lookup.hints.dkim == ["google"] and lookup.authenticated
    again = svc.DnsLookup.from_json(lookup.to_json())
    assert again.answers["MX"].records[0].priority == 10 and again.hints.dkim == ["google"]


async def test_dns_nxdomain_and_total_failure():
    from services.public_api import PublicAPIError
    s, _ = service({})

    async def nx(name, rtype):
        return {"Status": 3}
    s._doh = nx
    with pytest.raises(tc.ToolError) as exc:
        await s.dns("nope.example")
    assert exc.value.code == "nxdomain"

    async def down(name, rtype):
        raise PublicAPIError("timeout")
    s._doh = down
    with pytest.raises(tc.ToolError) as exc2:
        await s.dns("example.com")
    assert exc2.value.code == "timeout" and exc2.value.tool == "dns"


async def test_subdomains_fetch_cache_and_errors():
    from services.public_api import PublicAPIError
    rows = [{"name_value": "a.example.com\nb.example.com", "common_name": "a.example.com"}]
    s, api = service({"crt.sh": rows})
    result = await s.subdomains("example.com")
    assert result.names == ["a.example.com", "b.example.com"]
    await s.subdomains("example.com")
    assert len(api.calls) == 1                                    # second call from the cache

    for code, expected in (("timeout", "crtsh_down"), ("http_error", "crtsh_down"),
                           ("too_large", "too_large"), ("rate_limited", "rate_limited")):
        s2, _ = service({"crt.sh": PublicAPIError(code), "certspotter": PublicAPIError("timeout")})
        with pytest.raises(tc.ToolError) as exc:
            await s2.subdomains("example.com")
        assert exc.value.code == expected
    s3, _ = service({"crt.sh": FakeResponse(b"<html>502</html>"),
                     "certspotter": PublicAPIError("http_error")})
    with pytest.raises(tc.ToolError) as exc3:
        await s3.subdomains("example.com")
    assert exc3.value.code == "crtsh_down"


async def test_subdomains_fall_back_to_certspotter_when_crtsh_is_down():
    from services.public_api import PublicAPIError
    issuances = [{"id": "1", "dns_names": ["a.example.com", "*.example.com", "example.com"]},
                 {"id": "2", "dns_names": ["b.example.com"]}]
    s, api = service({"crt.sh": PublicAPIError("http_error", status=502),
                      "certspotter": FakeResponse(issuances)})
    # The walk stops on an empty page: the fake answers the same page, so cut it short.
    pages = iter([issuances, []])
    original = api._match

    def match(url, params):
        if "certspotter" in url:
            api.calls.append((url, params))
            return FakeResponse(next(pages))
        return original(url, params)
    api._match = match
    result = await s.subdomains("example.com")
    assert result.names == ["a.example.com", "b.example.com"]
    assert result.source == "Cert Spotter" and result.wildcards == 1
    assert len([c for c in api.calls if "certspotter" in c[0]]) == 2   # an empty page ends the walk
    await s.subdomains("example.com")
    assert len(api.calls) == 3                                          # then served from the cache

    card = views.build_subdomains_card(result, 0, "en-US")
    text = "\n".join(c.content for c in card.walk_children() if isinstance(c, ui.TextDisplay))
    assert "Cert Spotter" in text and "crt.sh did not answer" in text


async def test_certspotter_fallback_pages_are_capped():
    from services.public_api import PublicAPIError
    page = [{"id": str(i), "dns_names": [f"h{i}.example.com"]} for i in range(5)]
    s, api = service({"crt.sh": PublicAPIError("timeout"), "certspotter": FakeResponse(page)})
    await s.subdomains("example.com")
    assert len([c for c in api.calls if "certspotter" in c[0]]) == crtsh.CERTSPOTTER_MAX_PAGES


async def test_tls_inspection_against_a_local_server(tmp_path, monkeypatch):
    """Real handshake with a self-signed certificate on 127.0.0.1: the public-address
    guard is bypassed here only; it has its own test below."""
    import ssl
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "local.example")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=90))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("local.example")]), False)
            .sign(key, hashes.SHA256()))
    (tmp_path / "c.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "k.pem").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(tmp_path / "c.pem", tmp_path / "k.pem")

    async def handle(reader, writer):
        writer.close()
    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=ctx)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr(tls, "ALLOWED_PORTS", (port,))
    s, _ = service({})

    async def local(host, p):
        return ["127.0.0.1"]
    s._public_addresses = local
    try:
        result = await s.tls("local.example", port)
    finally:
        server.close()
        await server.wait_closed()
    assert result.verified is False and tls.SELF_SIGNED in result.reasons
    assert result.cert.subject_cn == "local.example" and result.version.startswith("TLS")


async def test_tls_refuses_private_targets_and_odd_ports():
    s, _ = service({})
    with pytest.raises(tc.ToolError) as exc:
        await s.tls("127.0.0.1")
    assert exc.value.code == "private_address"
    with pytest.raises(tc.ToolError) as exc2:
        await s.tls("example.com", 22)
    assert exc2.value.code == "bad_port"


# --------------------------------------------------------------------------- #
# Cards
# --------------------------------------------------------------------------- #


def whois_record(created_days_ago=400, expires_in=300):
    rec = rdap.parse_domain(RDAP_SAMPLE)
    rec.created = NOW - dt.timedelta(days=created_days_ago)
    rec.expires = NOW + dt.timedelta(days=expires_in)
    return rec


def test_whois_card_alerts_and_accents():
    domain = names.parse_target("example.com")
    calm = views.build_whois_card(whois_record(), domain, "en-US", now=NOW)
    assert accent(calm) == tc.ACCENT_INFO
    body = joined(calm)
    assert "ACME Registrar" in body and "`ns1.example.com`" in body and "Transfer locked" in body
    assert "Provided by" not in body or "RDAP" in body
    fresh = views.build_whois_card(whois_record(created_days_ago=3), domain, "en-US", now=NOW)
    assert accent(fresh) == tc.ACCENT_ERROR and "3" in joined(fresh)
    recent = views.build_whois_card(whois_record(created_days_ago=20), domain, "en-US", now=NOW)
    assert accent(recent) == tc.ACCENT_WARNING
    expiring = views.build_whois_card(whois_record(expires_in=10), domain, "fr", now=NOW)
    assert accent(expiring) == tc.ACCENT_WARNING
    expired = views.build_whois_card(whois_record(expires_in=-2), domain, "de", now=NOW)
    assert accent(expired) == tc.ACCENT_ERROR
    link_buttons = [b for b in buttons(calm) if getattr(b, "url", None)]
    assert link_buttons and link_buttons[0].url.startswith("https://rdap.acme.test")


def test_whois_card_idn_and_hosting_notes():
    idn = names.parse_target("bücher.de")
    rec = rdap.DomainRecord(name="xn--bcher-kva.de", created=NOW - dt.timedelta(days=900))
    body = joined(views.build_whois_card(rec, idn, "en-US", now=NOW))
    assert "bücher.de" in body and "xn--bcher-kva.de" in body
    shared = names.parse_target("me.github.io")
    body2 = joined(views.build_whois_card(rdap.DomainRecord(name="github.io"), shared, "en-US", now=NOW))
    assert "hosting platform" in body2


def dns_lookup(**extra):
    look = svc.DnsLookup(name="example.com")
    look.answers["A"] = doh.DnsAnswer(records=[doh.DnsRecord("A", "example.com", 300, "93.184.216.34")])
    look.answers["MX"] = doh.DnsAnswer(records=[doh.DnsRecord("MX", "example.com", 3600, "mail.example.com", 10)])
    look.answers["TXT"] = doh.DnsAnswer(records=[doh.DnsRecord("TXT", "example.com", 300, "v=spf1 -all")])
    look.hints = doh.build_hints(["v=spf1 -all"], [], [])
    for key, value in extra.items():
        setattr(look, key, value)
    return look


def test_dns_card_all_and_filtered():
    view, files = views.build_dns_card(dns_lookup(), views.TYPE_ALL, "en-US")
    body = joined(view)
    assert "93.184.216.34" in body and "10 mail.example.com" in body and "Strict" in body
    assert not files
    selects = [b for b in buttons(view) if isinstance(b, discord.ui.Select)]
    assert len(selects) == 1 and selects[0].custom_id == "moddy:tools:dns:t:example.com"
    assert [o.value for o in selects[0].options] == ["all"] + list(doh.RECORD_TYPES)
    assert [o.value for o in selects[0].options if o.default] == ["all"]

    only_mx, _ = views.build_dns_card(dns_lookup(), "MX", "fr")
    assert "mail.example.com" in joined(only_mx) and "93.184" not in joined(only_mx)
    assert [o.value for o in [b for b in buttons(only_mx) if isinstance(b, discord.ui.Select)][0].options
            if o.default] == ["MX"]
    none, _ = views.build_dns_card(dns_lookup(), "CAA", "en-US")
    assert "No `CAA` record" in joined(none)


def test_dns_card_attaches_long_output_and_skips_select_for_long_names():
    look = dns_lookup()
    look.answers["TXT"] = doh.DnsAnswer(records=[
        doh.DnsRecord("TXT", "example.com", 300, f"token-{i}-" + "x" * 400) for i in range(40)])
    view, files = views.build_dns_card(look, "TXT", "en-US")
    assert files and files[0].filename == "dns-example.com.txt"
    long_name = ("a" * 61) + ".example.com"
    look2 = dns_lookup()
    look2.name = long_name
    view2, _ = views.build_dns_card(look2, views.TYPE_ALL, "en-US")
    assert not [b for b in buttons(view2) if isinstance(b, discord.ui.Select)]


def tls_result(**kw):
    info = tls.describe_certificate(make_cert(self_signed=False))
    base = dict(host="example.com", port=443, ip="93.184.216.34", cert=info, verified=True,
                reasons=[], version="TLSv1.3", cipher="TLS_AES_256_GCM_SHA384")
    base.update(kw)
    return svc.TlsResult(**base)


def test_ssl_card_verdicts():
    ok = views.build_ssl_card(tls_result(), "example.com", "en-US")
    assert accent(ok) == tc.ACCENT_SUCCESS and "Valid certificate" in joined(ok)
    assert "TLSv1.3" in joined(ok) and "Test CA" in joined(ok)
    soon_cert = tls.describe_certificate(make_cert(days=5, self_signed=False))
    soon = views.build_ssl_card(tls_result(cert=soon_cert), "example.com", "en-US")
    assert accent(soon) == tc.ACCENT_WARNING
    bad = views.build_ssl_card(tls_result(verified=False, reasons=[tls.EXPIRED, tls.SELF_SIGNED], port=8443),
                               "example.com", "fr")
    body = joined(bad)
    assert accent(bad) == tc.ACCENT_ERROR and "example.com:8443" in body
    assert i18n.get("tools.ssl.reasons.expired", locale="fr") in body


def sub_result(count=100):
    return crtsh.SubdomainResult(domain="example.com", names=[f"h{i:03}.example.com" for i in range(count)],
                                 wildcards=2, total=count, certificates=500)


def test_subdomains_card_pagination():
    view = views.build_subdomains_card(sub_result(), 1, "en-US")
    body = joined(view)
    assert "h040.example.com" in body and "h039.example.com" not in body and "Page 2/3" in body
    ids = [b.custom_id for b in buttons(view)]
    assert ids == ["moddy:tools:subdomains:prev:0:example.com",
                   "moddy:tools:subdomains:next:2:example.com",
                   "moddy:tools:subdomains:export:example.com"]
    first = views.build_subdomains_card(sub_result(), 0, "en-US")
    assert [b.disabled for b in buttons(first)][:2] == [True, False]
    last = views.build_subdomains_card(sub_result(), 99, "en-US")      # clamped to the last page
    assert [b.disabled for b in buttons(last)][:2] == [False, True]
    short = views.build_subdomains_card(sub_result(5), 0, "en-US")
    assert not buttons(short)
    empty = views.build_subdomains_card(crtsh.SubdomainResult(domain="example.com"), 0, "en-US")
    assert "No subdomain appeared" in joined(empty)


def test_subdomains_error_card_has_retry():
    error = views.build_crtsh_error(tc.ToolError("crtsh_down", tool="subdomains"), "example.com", "en-US")
    assert [b.custom_id for b in buttons(error)] == ["moddy:tools:subdomains:retry:example.com"]
    assert "crt.sh" in joined(error)
    no_retry = views.build_crtsh_error(tc.ToolError("too_large", tool="subdomains", domain="example.com"),
                                       "example.com", "en-US")
    assert not buttons(no_retry)


def test_subdomains_export_file():
    file = views.subdomains_file(sub_result(3))
    assert file.filename == "subdomains-example.com.txt"
    assert file.fp.read().decode().splitlines() == ["h000.example.com", "h001.example.com", "h002.example.com"]


def link_target(url="https://bit.ly/a", final="https://evil.test/login", hops=2, **sig):
    chain = lc.Chain(hops=[lc.Hop(url, 301), lc.Hop(final, 200)][:hops])
    return svc.LinkTarget(prepared=lc.PreparedUrl(url, False), chain=chain,
                          signals=signals(final_host="evil.test", redirects=hops - 1, **sig))


def test_link_card_states_and_verdicts():
    target = link_target(age_level="very_recent", age_days=2, shortener=True)
    running = views.build_link_card(target, svc.VtOutcome(views.RUNNING), svc.ScanOutcome(views.RUNNING),
                                    "en-US")
    body = joined(running)
    assert "Analysing" in body and "evil.test/login" in body and "bit.ly/a" in body
    assert "registered only" in body and accent(running) == tc.ACCENT_WARNING

    vt = svc.VtOutcome("ok", lc.VtResult(malicious=7, harmless=60, undetected=20), "https://vt.test/report")
    scan = svc.ScanOutcome("ok", lc.UrlscanResult(malicious=True, brands=["PayPal"]),
                           report_url="https://urlscan.io/result/x/",
                           screenshot_url="https://urlscan.io/screenshots/x.png", country="RU")
    done = views.build_link_card(target, vt, scan, "en-US", requester_id=42)
    text = joined(done)
    assert accent(done) == tc.ACCENT_ERROR and "Dangerous link" in text
    assert "7" in text and "87" in text and "PayPal" in text and "<@42>" in text
    gallery = next(c for c in done.walk_children() if isinstance(c, discord.ui.MediaGallery))
    assert gallery.items[0].spoiler
    urls = [b.url for b in buttons(done) if getattr(b, "url", None)]
    assert urls == ["https://vt.test/report", "https://urlscan.io/result/x/"]
    assert not any("https://evil.test" in c for c in texts(done) if c.startswith("[")), "links stay inert"


def test_link_card_without_providers_and_with_errors():
    clean = link_target(url="https://example.com/", final="https://example.com/", hops=1)
    no_keys = views.build_link_card(clean, svc.VtOutcome("unavailable"), svc.ScanOutcome("unavailable"), "en-US")
    body = joined(no_keys)
    assert accent(no_keys) == tc.ACCENT_SUCCESS and "No threat detected" in body
    assert "not enabled" in body and "does not mean a link is safe" in body
    quota = views.build_link_card(clean, svc.VtOutcome("quota"), svc.ScanOutcome("quota"), "fr")
    assert i18n.get("tools.linkcheck.provider.quota", locale="fr") in joined(quota)

    unreachable = link_target(hops=1, reached=False)
    unreachable.chain.hops[0].status = 0
    unreachable.chain.error = "timeout"
    card = views.build_link_card(unreachable, svc.VtOutcome("unavailable"), svc.ScanOutcome("skipped"), "en-US")
    assert "Could not be reached" in joined(card) and accent(card) == tc.ACCENT_NEUTRAL


def test_links_summary_card():
    a = link_target()
    b = link_target(url="https://example.com/", final="https://example.com/", hops=1)
    card = views.build_links_card([(a, svc.VtOutcome("ok", lc.VtResult(malicious=9), "x")), (b, None)],
                                  "en-US", skipped=2)
    body = joined(card)
    assert "evil.test/login" in body and "example.com" in body and "2 link(s) not examined" in body
    assert accent(card) == tc.ACCENT_ERROR


@pytest.mark.parametrize("days, expected", [(1, "1 day"), (20, "20 days"), (90, "3 months"), (800, "2 years")])
def test_age_label(days, expected):
    assert views.age_label(days, "en-US") == expected


# --------------------------------------------------------------------------- #
# Persistent items
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("cls, args", views.DYNAMIC_ITEM_CASES)
async def test_dynamic_items_round_trip(cls, args):
    item = cls(*args)
    custom_id = item.item.custom_id
    assert len(custom_id) <= 100
    match = cls.__discord_ui_compiled_template__.fullmatch(custom_id)
    assert match is not None
    rebuilt = await cls.from_custom_id(types.SimpleNamespace(locale=discord.Locale.american_english),
                                       item.item, match)
    assert rebuilt.item.custom_id == custom_id


def test_button_domain_limit():
    assert views.button_domain_ok("example.com") and not views.button_domain_ok("a" * 61 + ".com")
    assert not views.button_domain_ok("UPPER.com") and not views.button_domain_ok("")


# --------------------------------------------------------------------------- #
# i18n
# --------------------------------------------------------------------------- #


def flatten(tree, prefix=""):
    out = {}
    for key, value in tree.items():
        if isinstance(value, dict):
            out.update(flatten(value, f"{prefix}{key}."))
        else:
            out[prefix + key] = value
    return out


def placeholders(text):
    return set(re.findall(r"\{(\w+)\}", text))


@pytest.mark.parametrize("root", ROOTS)
def test_i18n_keys_match_in_every_locale(root):
    reference = flatten(i18n._translations["en-US"]["tools"][root])
    assert reference
    for locale in LOCALES:
        other = flatten(i18n._translations[locale]["tools"][root])
        assert set(other) == set(reference), f"{locale} tools.{root}"
        for key, text in other.items():
            assert text.strip(), f"{locale} tools.{root}.{key} is empty"
            assert placeholders(text) == placeholders(reference[key]), f"{locale} tools.{root}.{key}"


def test_every_dynamic_key_used_by_the_code_exists():
    en = flatten(i18n._translations["en-US"]["tools"])
    need = set()
    need |= {f"whois.statuses.{c}" for c in set(rdap._STATUS_CODES.values())}
    need |= {f"whois.alert_status.{c}" for c in rdap.WARNING_STATUSES}
    need |= {f"ssl.reasons.{r}" for r in tls._REASON_PRIORITY}
    need |= {f"dns.ttl_units.{u}" for u in ("s", "min", "h", "d")}
    need |= {f"dns.spf.{k}" for k in ("strict", "soft", "neutral", "open", "unknown")}
    need |= {f"dns.dmarc.{k}" for k in ("none", "quarantine", "reject", "unknown")}
    need |= {f"linkcheck.chain_errors.{c}" for c in (
        "too_many_redirects", "loop", "blocked", "timeout", "unreachable", "bad_redirect", "tls")}
    need |= {f"linkcheck.provider.{s}" for s in ("pending", "quota", "busy", "refused", "failed", "running")}
    need |= {f"linkcheck.verdict.{v}" for v in ("safe", "suspicious", "dangerous", "unreachable")}
    need |= {f"linkcheck.age.{u}_{n}" for u in ("day", "month", "year") for n in ("one", "other")}
    every_signal = signals(age_level="very_recent", age_days=1, idn="mixed_script", userinfo=True, ip_host=True,
                           chain_error="loop", https=False, shortener=True, redirects=5)
    reasons = lc.collect_reasons(every_signal, lc.VtResult(malicious=4), lc.UrlscanResult(malicious=True))
    reasons += lc.collect_reasons(signals(idn="lookalike", age_level="recent", age_days=9,
                                          chain_error="too_many_redirects"), lc.VtResult(suspicious=3),
                                  lc.UrlscanResult(score=3))
    need |= {f"linkcheck.reasons.{r.code}" for r in reasons}
    # errors raised by the service and the cog
    need |= {f"domain.errors.{c}" for c in (names.EMPTY, names.INVALID, names.TOO_LONG, names.IP_NOT_ALLOWED,
                                            names.RESERVED, names.PRIVATE_ADDRESS, "cooldown")}
    need |= {"whois.errors.not_registered", "whois.errors.no_rdap", "dns.errors.nxdomain",
             "ssl.errors.bad_port", "ssl.errors.dns_failed", "ssl.errors.tls_failed", "ssl.errors.unreachable",
             "subdomains.errors.crtsh_down", "subdomains.errors.too_large",
             "linkcheck.errors.empty", "linkcheck.errors.invalid_url", "linkcheck.errors.unsupported_scheme",
             "linkcheck.errors.no_links"}
    missing = sorted(k for k in need if k not in en)
    assert not missing, missing


def test_error_cards_use_the_tool_texts():
    card = tc.tool_error_card(tc.ToolError("private_address", tool="domain"), "fr")
    assert i18n.get("tools.domain.errors.private_address", locale="fr") in joined(card)
    card = tc.tool_error_card(tc.ToolError("not_registered", tool="whois", domain="x.com"), "en-US")
    assert "`x.com`" in joined(card)


# --------------------------------------------------------------------------- #
# Cog helpers
# --------------------------------------------------------------------------- #


def test_prepare_link_input():
    from cogs.tools_web import ToolsWeb, _port_in, _with_ascii_host

    prepared = ToolsWeb.prepare("bücher.de/x?y=1")
    assert prepared.url == "https://xn--bcher-kva.de/x?y=1"
    with pytest.raises(tc.ToolError) as exc:
        ToolsWeb.prepare("http://localhost/admin")
    assert exc.value.tool == "domain" and exc.value.code == "reserved"
    with pytest.raises(tc.ToolError) as exc2:
        ToolsWeb.prepare("http://192.168.0.1/")
    assert exc2.value.code == "private_address"
    with pytest.raises(tc.ToolError) as exc3:
        ToolsWeb.prepare("ftp://x.test")
    assert exc3.value.tool == "linkcheck" and exc3.value.code == "unsupported_scheme"
    assert _port_in("example.com:8443") == 8443 and _port_in("example.com:22") == 443
    assert _port_in("https://example.com") == 443


def test_message_links_reads_content_and_embeds():
    from cogs.tools_web import ToolsWeb

    message = types.SimpleNamespace(content="look https://a.test/x and www.b.test",
                                    embeds=[types.SimpleNamespace(url="https://c.test/")])
    assert ToolsWeb.message_links(message) == ["https://a.test/x", "https://www.b.test", "https://c.test/"]
    assert ToolsWeb.message_links(types.SimpleNamespace(content="nothing", embeds=[])) == []


def test_rate_limit_per_member():
    from cogs.tools_web import LOOKUPS_PER_MINUTE, ToolsWeb

    class Tree:
        def add_command(self, c):
            pass

        def remove_command(self, *a, **k):
            pass
    cog = ToolsWeb(types.SimpleNamespace(tree=Tree(), public_api=None))
    for _ in range(LOOKUPS_PER_MINUTE):
        cog.check_rate(1)
    with pytest.raises(tc.ToolError) as exc:
        cog.check_rate(1)
    assert exc.value.code == "cooldown"
    cog.check_rate(2)                                              # another member is unaffected


# --------------------------------------------------------------------------- #
# Gateway wiring
# --------------------------------------------------------------------------- #


def test_gateway_wiring():
    from gateway.config import GatewayConfig
    from gateway.executor import GatewayExecutor

    cfg = GatewayConfig()
    rules = cfg.model_rate_limits
    vt = {r.name: r.limit for r in rules[("virustotal", "public_api")]}
    assert vt["rpm"] == 4
    assert ("urlscan", "scan") in rules and ("urlscan", "result") in rules
    executor = GatewayExecutor({}, None, None, None, cfg)
    assert executor._timeout_for("scan_result") == cfg.timeout_scan > 40
    assert executor._timeout_for("url_report") == cfg.timeout_search
    assert GatewayConfig.from_env().virustotal_api_key is None or isinstance(
        GatewayConfig.from_env().virustotal_api_key, str)


async def test_gateway_clients_keep_the_url_out_of_the_log_payload():
    from gateway.clients.web_security import UrlscanClient, VirusTotalClient

    seen = []

    class Executor:
        async def execute(self, spec):
            seen.append(spec)
            return {"found": False}
    secret = "https://example.com/reset?token=SECRET123"
    await VirusTotalClient(Executor()).url_report(secret)
    await UrlscanClient(Executor()).scan(secret)
    for spec in seen:
        assert spec.binary == secret.encode()
        assert "SECRET123" not in repr(spec.payload) and "SECRET123" not in repr(spec.metadata)
        assert spec.metadata["host"] == "example.com"
    assert seen[0].model == "public_api" and seen[1].model == "scan"


# --------------------------------------------------------------------------- #
# Command flows (fake interaction, fake service)
# --------------------------------------------------------------------------- #


class FakeInteraction:
    def __init__(self, extras=None):
        self.user = types.SimpleNamespace(id=7)
        self.guild_id = 99
        self.locale = discord.Locale.american_english
        self.extras = extras or {}
        self.sent = []
        self.edits = []
        self.response = types.SimpleNamespace(is_done=lambda: bool(self.sent), send_message=self._send)
        self.message = None

    async def _send(self, **kw):
        self.sent.append(kw)

    async def edit_original_response(self, **kw):
        self.edits.append(kw)

    @property
    def followup(self):
        return types.SimpleNamespace(send=self._send)


def make_cog():
    from cogs.tools_web import ToolsWeb

    class Tree:
        def add_command(self, c):
            pass

        def remove_command(self, *a, **k):
            pass
    return ToolsWeb(types.SimpleNamespace(tree=Tree(), public_api=None, stats=None, db=None))


async def test_whois_command_flow_and_error_flow():
    cog = make_cog()

    async def fake_whois(domain):
        return whois_record()
    cog.service.whois = fake_whois
    it = FakeInteraction()
    await cog.whois_command.callback(cog, it, "https://www.example.com/path", incognito=False)
    assert it.sent[0]["ephemeral"] is False                      # loading card first
    assert "ACME Registrar" in joined(it.edits[-1]["view"]) and "<@7>" in joined(it.edits[-1]["view"])

    async def failing(domain):
        raise tc.ToolError("not_registered", tool="whois", domain="x.com")
    cog.service.whois = failing
    it2 = FakeInteraction()
    await cog.whois_command.callback(cog, it2, "x.com")
    assert "`x.com`" in joined(it2.edits[-1]["view"])

    it3 = FakeInteraction()
    await cog.whois_command.callback(cog, it3, "localhost")      # refused before any loading card
    assert it3.sent[0]["ephemeral"] is True and not it3.edits
    assert i18n.get("tools.domain.errors.reserved", locale="en-US") in joined(it3.sent[0]["view"])


async def test_link_check_updates_the_card_as_providers_answer():
    cog = make_cog()
    target = link_target()

    async def inspect(prepared):
        return target

    async def quota(user, guild):
        return ["plan"]
    seen_plans = {}

    async def vt(url, *, plan=None, report_only=False, metadata=None):
        seen_plans["vt"] = plan
        await asyncio.sleep(0.01)
        return svc.VtOutcome("ok", lc.VtResult(malicious=6, harmless=50), "https://vt.test/r")

    async def scan(url, *, plan=None, metadata=None):
        seen_plans["scan"] = plan
        await asyncio.sleep(0.03)
        return svc.ScanOutcome("ok", lc.UrlscanResult(malicious=True), report_url="https://urlscan.io/r",
                               screenshot_url="https://urlscan.io/s.png")
    cog.service.inspect_link = inspect
    cog.service.providers = lambda: (True, True)
    cog.service.quota_plan = quota
    cog.service.check_virustotal = vt
    cog.service.check_urlscan = scan

    it = FakeInteraction()
    await cog.run_link_check(it, lc.PreparedUrl("https://bit.ly/a", False), "en-US", ephemeral=True,
                             source="slash")
    assert len(it.edits) == 3                                     # local, after VirusTotal, after urlscan
    assert "Analysing" in joined(it.edits[0]["view"])
    assert "Analysing" in joined(it.edits[1]["view"])             # urlscan still running
    final = joined(it.edits[2]["view"])
    assert "Dangerous link" in final and "Analysing" not in final
    assert seen_plans == {"vt": ["plan"], "scan": None}          # the quota is charged once, by VirusTotal


async def test_link_check_degrades_without_keys_and_over_quota():
    cog = make_cog()
    clean = link_target(url="https://example.com/", final="https://example.com/", hops=1)

    async def inspect(prepared):
        return clean
    cog.service.inspect_link = inspect
    cog.service.providers = lambda: (False, False)
    it = FakeInteraction()
    await cog.run_link_check(it, lc.PreparedUrl("https://example.com/", False), "en-US", ephemeral=True,
                             source="slash")
    assert len(it.edits) == 1 and "not enabled" in joined(it.edits[0]["view"])

    async def no_quota(user, guild):
        return None
    cog.service.providers = lambda: (True, True)
    cog.service.quota_plan = no_quota
    it2 = FakeInteraction()
    await cog.run_link_check(it2, lc.PreparedUrl("https://example.com/", False), "en-US", ephemeral=True,
                             source="slash")
    assert len(it2.edits) == 1
    assert i18n.get("tools.linkcheck.provider.quota", locale="en-US") in joined(it2.edits[0]["view"])
