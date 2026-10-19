"""Parsing and validating what people type into /whois, /dns, /ssl, /subdomains.

Accepts a bare domain (``Example.COM``), an IDN (``bücher.de``), or a whole URL
(``https://user@www.example.com:8443/path?q=1``) and returns the host in both
its Unicode and ASCII (punycode) forms.

Public-suffix handling
----------------------
The *registrable domain* (``example.co.uk`` for ``a.b.example.co.uk``) is
needed because RDAP only knows registered names. Vendoring the full Public
Suffix List (~230 KB, ~10 k rules, updated weekly) is not worth the memory and
upkeep for this use, so :func:`registrable_domain` uses a compact heuristic:

1. an explicit set of common multi-part suffixes (``co.uk``, ``com.au``,
   ``gouv.fr``...);
2. otherwise, a two-letter country TLD preceded by a generic second-level label
   (``co``, ``com``, ``gov``...) is treated as a two-part suffix;
3. a small set of shared-hosting suffixes (``github.io``, ``vercel.app``...)
   where the registrable domain is the *customer's*, not the platform's.

It can be wrong on a rare, exotic suffix — the card then shows the registry's
answer for the wrong level, which is harmless — but it never turns a public
suffix into something the user did not type.
"""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

# Stable codes, mapped to ``tools.domain.errors.<code>`` by the cog.
EMPTY = "empty"
INVALID = "invalid"
TOO_LONG = "too_long"
IP_NOT_ALLOWED = "ip_not_allowed"
RESERVED = "reserved"
PRIVATE_ADDRESS = "private_address"

MAX_HOST_LENGTH = 253
MAX_LABEL_LENGTH = 63

#: TLDs that never resolve on the public Internet (RFC 2606, 6761, 6762...).
RESERVED_TLDS = frozenset({
    "local", "localhost", "internal", "intranet", "lan", "home", "corp",
    "test", "example", "invalid", "onion", "arpa", "localdomain", "private", "workgroup",
})

#: Common multi-part public suffixes (second level under a ccTLD).
MULTI_PART_SUFFIXES = frozenset("""
co.uk org.uk ac.uk gov.uk me.uk ltd.uk plc.uk net.uk sch.uk nhs.uk police.uk
com.au net.au org.au edu.au gov.au id.au asn.au
co.nz org.nz net.nz govt.nz ac.nz school.nz
co.jp ne.jp or.jp ac.jp go.jp ad.jp ed.jp gr.jp lg.jp
co.kr or.kr ne.kr go.kr ac.kr re.kr
com.cn net.cn org.cn gov.cn edu.cn
com.hk org.hk edu.hk gov.hk net.hk
com.tw org.tw net.tw edu.tw gov.tw idv.tw
com.sg org.sg net.sg edu.sg gov.sg
com.my org.my net.my edu.my gov.my
co.id or.id web.id ac.id go.id
co.th or.th ac.th go.th in.th
com.vn net.vn org.vn edu.vn gov.vn
com.ph org.ph net.ph edu.ph gov.ph
co.in net.in org.in gov.in ac.in edu.in res.in firm.in gen.in ind.in
com.pk org.pk net.pk edu.pk gov.pk
com.bd org.bd net.bd edu.bd gov.bd
co.il org.il ac.il gov.il net.il muni.il
com.tr org.tr net.tr edu.tr gov.tr gen.tr biz.tr
com.sa org.sa net.sa edu.sa gov.sa
com.eg org.eg net.eg edu.eg gov.eg
co.za org.za net.za gov.za ac.za web.za
co.ke or.ke ne.ke go.ke ac.ke
com.ng org.ng net.ng edu.ng gov.ng
com.gh org.gh edu.gh gov.gh
co.tz or.tz go.tz ac.tz
co.ug or.ug go.ug ac.ug
com.ma org.ma net.ma gov.ma ac.ma co.ma
com.tn org.tn gov.tn
com.dz org.dz gov.dz
com.br net.br org.br gov.br edu.br art.br eco.br blog.br
com.ar net.ar org.ar gob.ar gov.ar edu.ar
com.mx org.mx net.mx gob.mx edu.mx
com.co org.co net.co gov.co edu.co
com.pe org.pe net.pe gob.pe edu.pe
com.ve org.ve net.ve gob.ve
com.uy org.uy net.uy gub.uy edu.uy
com.ec org.ec net.ec gob.ec edu.ec
com.bo org.bo gob.bo
com.py org.py gov.py
com.do org.do gob.do
com.gt org.gt gob.gt
com.pa org.pa gob.pa
com.ua org.ua net.ua gov.ua edu.ua
com.pl org.pl net.pl edu.pl gov.pl info.pl biz.pl
com.ru org.ru net.ru
com.es org.es nom.es gob.es edu.es
com.pt org.pt gov.pt edu.pt
com.gr org.gr gov.gr edu.gr net.gr
com.cy org.cy gov.cy ac.cy
com.mt org.mt gov.mt
co.at or.at gv.at ac.at
co.hu org.hu gov.hu
co.rs org.rs ac.rs gov.rs
com.hr com.ro org.ro com.lv org.lv com.ee com.de
com.fr asso.fr nom.fr prd.fr presse.fr tm.fr gouv.fr
gouv.ci gouv.sn gouv.ml gouv.bj gouv.ht gouv.bf gouv.ga gouv.cd
gouv.qc.ca
com.lb org.lb gov.lb com.kw org.kw gov.kw com.qa org.qa gov.qa
com.om com.bh com.jo org.jo gov.jo com.np org.np gov.np
com.lk org.lk gov.lk com.kh org.kh gov.kh com.mm com.mo com.fj com.cu
co.cr or.cr go.cr
""".split())

#: Second-level labels that, under a two-letter ccTLD, mean "two-part suffix".
GENERIC_SECOND_LEVEL = frozenset({
    "co", "com", "org", "net", "gov", "gob", "gouv", "edu", "ac", "or", "ne", "go",
    "mil", "asso", "nom", "sch", "ltd", "plc", "info", "biz",
})

#: Shared hosting / platform suffixes: the *customer* owns the next label up,
#: the platform's own registration age says nothing about the page.
SHARED_HOSTING_SUFFIXES = frozenset("""
github.io gitlab.io pages.dev workers.dev vercel.app now.sh netlify.app web.app
firebaseapp.com herokuapp.com onrender.com fly.dev up.railway.app railway.app
azurewebsites.net blob.core.windows.net cloudfront.net amazonaws.com s3.amazonaws.com
appspot.com run.app blogspot.com wordpress.com wixsite.com weebly.com squarespace.com
myshopify.com glitch.me repl.co replit.app codesandbox.io stackblitz.io ngrok.io
ngrok-free.app trycloudflare.com duckdns.org no-ip.org ddns.net hopto.org
000webhostapp.com infinityfreeapp.com rf.gd epizy.com tumblr.com
notion.site carrd.co webflow.io framer.app bubbleapps.io
""".split())

_LABEL_RE = re.compile(r"^[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?$")
_WRAPPERS = "<>\"'`()[]{}"


class DomainError(ValueError):
    """The input is not a usable domain. ``code`` maps to an i18n error."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class DomainName:
    """A validated host, in both forms."""

    ascii: str                # www.xn--bcher-kva.de (lowercase, punycode)
    unicode: str              # www.bücher.de
    registrable_ascii: str    # xn--bcher-kva.de
    registrable_unicode: str  # bücher.de
    suffix: str               # de / co.uk (ASCII)
    is_idn: bool
    shared_hosting: bool      # registrable domain belongs to a hosting platform
    is_ip: bool = False

    @property
    def is_subdomain(self) -> bool:
        return self.ascii != self.registrable_ascii


# --------------------------------------------------------------------------- #
# IP helpers
# --------------------------------------------------------------------------- #


def parse_ip(text: str):
    try:
        return ipaddress.ip_address(text.split("%", 1)[0].strip("[]"))
    except ValueError:
        return None


def is_public_ip(address) -> bool:
    """True for globally routable addresses (IPv4-mapped IPv6 unwrapped)."""
    if isinstance(address, str):
        address = parse_ip(address)
        if address is None:
            return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #


def extract_host(raw: str) -> str:
    """The host part of whatever was typed (domain, URL, ``user@host:port``)."""
    text = (raw or "").strip().strip(_WRAPPERS).strip()
    if not text:
        raise DomainError(EMPTY)
    if any(ch.isspace() for ch in text):
        raise DomainError(INVALID)
    if "://" not in text and not text.startswith("//"):
        text = "//" + text
    try:
        parsed = urlsplit(text)
        host = parsed.hostname  # lowercases, drops credentials and port
        _ = parsed.port         # raises ValueError on a garbage port
    except ValueError as exc:
        raise DomainError(INVALID) from exc
    if not host:
        raise DomainError(INVALID)
    return host.rstrip(".")


def to_ascii(host: str) -> str:
    """Punycode form of ``host`` (IDNA 2003 codec), lowercase."""
    host = unicodedata.normalize("NFKC", host).lower().rstrip(".")
    if not host:
        raise DomainError(INVALID)
    labels = []
    for label in host.split("."):
        if not label:
            raise DomainError(INVALID)
        try:
            ascii_label = label if label.isascii() else label.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise DomainError(INVALID) from exc
        labels.append(ascii_label.lower())
    ascii_host = ".".join(labels)
    if len(ascii_host) > MAX_HOST_LENGTH:
        raise DomainError(TOO_LONG)
    return ascii_host


def to_unicode(ascii_host: str) -> str:
    """Human form of a punycode host (falls back to the input on bad punycode)."""
    try:
        return ascii_host.encode("ascii").decode("idna")
    except (UnicodeError, ValueError):
        return ascii_host


# --------------------------------------------------------------------------- #
# Registrable domain
# --------------------------------------------------------------------------- #


def public_suffix(ascii_host: str) -> str:
    """The public suffix of ``ascii_host`` per the heuristic in the module docstring."""
    labels = ascii_host.split(".")
    if len(labels) == 1:
        return labels[0]
    for size in (3, 2):  # longest explicit match first (gouv.qc.ca)
        if len(labels) >= size and ".".join(labels[-size:]) in MULTI_PART_SUFFIXES:
            return ".".join(labels[-size:])
    tld = labels[-1]
    if len(labels) >= 3 and len(tld) == 2 and labels[-2] in GENERIC_SECOND_LEVEL:
        return ".".join(labels[-2:])
    return tld


def shared_hosting_suffix(ascii_host: str) -> Optional[str]:
    labels = ascii_host.split(".")
    for size in range(min(4, len(labels) - 1), 1, -1):
        candidate = ".".join(labels[-size:])
        if candidate in SHARED_HOSTING_SUFFIXES:
            return candidate
    return None


def registrable_domain(ascii_host: str) -> str:
    """``a.b.example.co.uk`` -> ``example.co.uk``; ``github.io`` customers keep their label."""
    labels = ascii_host.split(".")
    hosting = shared_hosting_suffix(ascii_host)
    suffix = hosting or public_suffix(ascii_host)
    size = len(suffix.split(".")) + 1
    if len(labels) <= size:
        return ascii_host
    return ".".join(labels[-size:])


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def parse_target(raw: str, *, allow_ip: bool = False) -> DomainName:
    """Validate a domain or URL typed by a person.

    * IPs are refused unless ``allow_ip`` (``/ssl``, ``/link-check``); a private
      or reserved IP is refused everywhere (``private_address``).
    * ``localhost``, ``.local``, ``.internal`` and friends are ``reserved``.
    * a single label (``com``, ``intranet``) is ``invalid``: nothing to look up.
    """
    host = extract_host(raw)
    ip = parse_ip(host)
    if ip is not None:
        if not is_public_ip(ip):
            raise DomainError(PRIVATE_ADDRESS)
        if not allow_ip:
            raise DomainError(IP_NOT_ALLOWED)
        text = str(ip)
        return DomainName(ascii=text, unicode=text, registrable_ascii=text,
                          registrable_unicode=text, suffix="", is_idn=False,
                          shared_hosting=False, is_ip=True)
    # Bare digits/dots that failed IP parsing (999.1.1.1, 1.2.3) are not domains.
    if re.fullmatch(r"[\d.]+", host):
        raise DomainError(INVALID)

    ascii_host = to_ascii(host)
    labels = ascii_host.split(".")
    if len(labels) < 2:
        raise DomainError(RESERVED if ascii_host in RESERVED_TLDS else INVALID)
    if labels[-1] in RESERVED_TLDS or ascii_host.endswith(".home.arpa"):
        raise DomainError(RESERVED)
    for label in labels:
        if len(label) > MAX_LABEL_LENGTH or not _LABEL_RE.match(label):
            raise DomainError(INVALID)
    if labels[-1].isdigit():  # a TLD is letters (or an xn-- label)
        raise DomainError(INVALID)

    registrable = registrable_domain(ascii_host)
    return DomainName(
        ascii=ascii_host,
        unicode=to_unicode(ascii_host),
        registrable_ascii=registrable,
        registrable_unicode=to_unicode(registrable),
        suffix=public_suffix(ascii_host),
        is_idn=any(label.startswith("xn--") for label in labels),
        shared_hosting=shared_hosting_suffix(ascii_host) is not None,
    )


def tld_of(ascii_host: str) -> str:
    return ascii_host.rsplit(".", 1)[-1]
