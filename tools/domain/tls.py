"""X.509 certificate description and "why is it invalid" diagnosis for /ssl.

Works on the DER bytes of the leaf certificate (``getpeercert(binary_form=True)``),
so it can describe a certificate the TLS stack refused to verify. The network
part (connecting twice: verifying first, then without verification to still
read the certificate) lives in ``services/web_tools_service.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from tools.domain.names import parse_ip

#: Ports /ssl accepts: HTTPS and the common implicit-TLS services. Anything
#: else would turn the command into a port scanner.
ALLOWED_PORTS = (443, 8443, 465, 636, 853, 993, 995)
DEFAULT_PORT = 443
#: Remaining days under which a valid certificate gets a warning.
WARN_DAYS = 14

# Verdict reasons, mapped to ``tools.ssl.reasons.<reason>``.
EXPIRED = "expired"
NOT_YET_VALID = "not_yet_valid"
SELF_SIGNED = "self_signed"
UNTRUSTED = "untrusted_issuer"
HOSTNAME_MISMATCH = "hostname_mismatch"
REVOKED = "revoked"
INVALID = "invalid"

#: Most informative first: that is the headline reason of the card.
_REASON_PRIORITY = (EXPIRED, NOT_YET_VALID, HOSTNAME_MISMATCH, SELF_SIGNED, UNTRUSTED, REVOKED, INVALID)

# OpenSSL X509_V_ERR_* codes (ssl.SSLCertVerificationError.verify_code)
_VERIFY_CODES = {
    2: UNTRUSTED,       # UNABLE_TO_GET_ISSUER_CERT
    9: NOT_YET_VALID,   # CERT_NOT_YET_VALID
    10: EXPIRED,        # CERT_HAS_EXPIRED
    18: SELF_SIGNED,    # DEPTH_ZERO_SELF_SIGNED_CERT
    19: UNTRUSTED,      # SELF_SIGNED_CERT_IN_CHAIN
    20: UNTRUSTED,      # UNABLE_TO_GET_ISSUER_CERT_LOCALLY
    21: UNTRUSTED,      # UNABLE_TO_VERIFY_LEAF_SIGNATURE
    23: REVOKED,        # CERT_REVOKED
    62: HOSTNAME_MISMATCH,  # HOSTNAME_MISMATCH
    64: HOSTNAME_MISMATCH,  # IP_ADDRESS_MISMATCH
}


@dataclass
class CertInfo:
    subject_cn: Optional[str] = None
    subject_org: Optional[str] = None
    issuer_cn: Optional[str] = None
    issuer_org: Optional[str] = None
    not_before: Optional[datetime] = None
    not_after: Optional[datetime] = None
    sans: List[str] = field(default_factory=list)
    key_type: str = ""        # RSA / EC / Ed25519 ...
    key_detail: str = ""      # 2048 / secp256r1 ...
    signature: str = ""       # sha256WithRSAEncryption ...
    self_signed: bool = False
    serial: str = ""

    @property
    def issuer_name(self) -> Optional[str]:
        """The organisation that issued the certificate (``Let's Encrypt``), else its CN."""
        return self.issuer_org or self.issuer_cn

    @property
    def key_label(self) -> str:
        return f"{self.key_type} {self.key_detail}".strip()


def reason_from_verify_code(code: Optional[int]) -> str:
    return _VERIFY_CODES.get(int(code), INVALID) if code is not None else INVALID


def _attr(name, oid) -> Optional[str]:
    attrs = name.get_attributes_for_oid(oid)
    return str(attrs[0].value) if attrs else None


def describe_certificate(der: bytes) -> CertInfo:
    """Parse a DER certificate. Raises ``ValueError`` when it is not one."""
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed448, ed25519, rsa
    from cryptography.x509.oid import NameOID

    cert = x509.load_der_x509_certificate(der)
    info = CertInfo(
        subject_cn=_attr(cert.subject, NameOID.COMMON_NAME),
        subject_org=_attr(cert.subject, NameOID.ORGANIZATION_NAME),
        issuer_cn=_attr(cert.issuer, NameOID.COMMON_NAME),
        issuer_org=_attr(cert.issuer, NameOID.ORGANIZATION_NAME),
        not_before=cert.not_valid_before_utc,
        not_after=cert.not_valid_after_utc,
        self_signed=cert.subject == cert.issuer,
        serial=format(cert.serial_number, "x"),
    )
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        info.sans = list(ext.get_values_for_type(x509.DNSName))
        info.sans += [str(ip) for ip in ext.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:
        pass

    key = cert.public_key()
    if isinstance(key, rsa.RSAPublicKey):
        info.key_type, info.key_detail = "RSA", str(key.key_size)
    elif isinstance(key, ec.EllipticCurvePublicKey):
        info.key_type, info.key_detail = "EC", key.curve.name
    elif isinstance(key, ed25519.Ed25519PublicKey):
        info.key_type, info.key_detail = "Ed25519", ""
    elif isinstance(key, ed448.Ed448PublicKey):
        info.key_type, info.key_detail = "Ed448", ""
    elif isinstance(key, dsa.DSAPublicKey):
        info.key_type, info.key_detail = "DSA", str(key.key_size)
    else:
        info.key_type = type(key).__name__

    oid = cert.signature_algorithm_oid
    info.signature = getattr(oid, "_name", "") or oid.dotted_string
    if info.signature == "Unknown OID":
        info.signature = oid.dotted_string
    return info


def hostname_matches(host: str, sans: List[str], common_name: Optional[str] = None) -> bool:
    """RFC 6125 matching: SAN entries (wildcard on the leftmost label only), the
    CN only when the certificate has no SAN at all."""
    host = host.lower().rstrip(".")
    candidates = [s.lower() for s in sans] or ([common_name.lower()] if common_name else [])
    ip = parse_ip(host)
    for pattern in candidates:
        if ip is not None:
            other = parse_ip(pattern)
            if other is not None and other == ip:
                return True
            continue
        if pattern == host:
            return True
        if pattern.startswith("*."):
            head, _, rest = host.partition(".")
            if head and rest == pattern[2:]:
                return True
    return False


def diagnose(cert: CertInfo, host: str, *, now: Optional[datetime] = None,
             verify_code: Optional[int] = None) -> List[str]:
    """Every reason the certificate is not acceptable, most informative first.

    Combines what the certificate itself says (dates, names, self-signature) with
    the OpenSSL verdict (``verify_code``) which also knows about untrusted chains.
    """
    now = now or datetime.now(timezone.utc)
    reasons = set()
    if cert.not_after and cert.not_after < now:
        reasons.add(EXPIRED)
    if cert.not_before and cert.not_before > now:
        reasons.add(NOT_YET_VALID)
    if not hostname_matches(host, cert.sans, cert.subject_cn):
        reasons.add(HOSTNAME_MISMATCH)
    if cert.self_signed:
        reasons.add(SELF_SIGNED)
    if verify_code is not None:
        reasons.add(reason_from_verify_code(verify_code))
    # A self-signed leaf is also "untrusted": keep the more specific reason only.
    if SELF_SIGNED in reasons:
        reasons.discard(UNTRUSTED)
    if len(reasons) > 1:
        reasons.discard(INVALID)
    return [r for r in _REASON_PRIORITY if r in reasons]


def days_left(cert: CertInfo, now: Optional[datetime] = None) -> Optional[int]:
    if cert.not_after is None:
        return None
    return int(((cert.not_after - (now or datetime.now(timezone.utc))).total_seconds()) // 86400)


def san_preview(cert: CertInfo, limit: int = 3) -> List[str]:
    """The first SAN entries, the certificate's own name first."""
    names = sorted(set(cert.sans), key=lambda n: (n.startswith("*."), n))
    if cert.subject_cn and cert.subject_cn in names:
        names.remove(cert.subject_cn)
        names.insert(0, cert.subject_cn)
    return names[:limit]
