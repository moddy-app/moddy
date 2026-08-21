"""HTTP sortant borné pour les URLs contrôlées par des utilisateurs.

Le résolveur filtre l'adresse réellement utilisée par aiohttp, ce qui évite le
TOCTOU classique « validation DNS puis seconde résolution à la connexion ».
Les redirections sont suivies manuellement et revalidées à chaque saut.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver

MAX_REDIRECTS = 3
ALLOWED_PORTS = {80, 443}
BLOCKED_HOSTS = {
    "localhost",
    "metadata.google.internal",
}

# Stable, machine-readable codes for UnsafeURL.code — callers must switch on
# these, never on the (French, human-facing) exception message.
INVALID_URL = "invalid_url"
UNSUPPORTED_SCHEME = "unsupported_scheme"
MISSING_HOST = "missing_host"
CREDENTIALS_IN_URL = "credentials_in_url"
BLOCKED_HOST = "blocked_host"
UNSUPPORTED_PORT = "unsupported_port"
PRIVATE_ADDRESS = "private_address"
DISALLOWED_CONTENT_TYPE = "disallowed_content_type"
RESPONSE_TOO_LARGE = "response_too_large"
INVALID_CONTENT_LENGTH = "invalid_content_length"
TOO_MANY_REDIRECTS = "too_many_redirects"


class UnsafeURL(ValueError):
    """L'URL ne peut pas être contactée depuis le réseau du bot.

    ``code`` est une raison stable, indépendante de la langue du message,
    pour permettre aux appelants de distinguer les cas (image trop grosse,
    type de contenu refusé, ...) sans faire de correspondance sur le texte.
    """

    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


def _is_public_address(value: str) -> bool:
    address = ipaddress.ip_address(value.split("%", 1)[0])
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global


def validate_public_url(url: str) -> str:
    """Valide la forme d'une URL HTTP(S) publique avant résolution DNS."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise UnsafeURL("URL invalide", code=INVALID_URL) from exc
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeURL("schéma non autorisé", code=UNSUPPORTED_SCHEME)
    if not parsed.hostname:
        raise UnsafeURL("hôte manquant", code=MISSING_HOST)
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeURL("identifiants interdits dans l'URL", code=CREDENTIALS_IN_URL)
    host = parsed.hostname.rstrip(".").lower()
    if host in BLOCKED_HOSTS or host.endswith(".localhost") or host.endswith(".local"):
        raise UnsafeURL("hôte local interdit", code=BLOCKED_HOST)
    if port is not None and port not in ALLOWED_PORTS:
        raise UnsafeURL("port non autorisé", code=UNSUPPORTED_PORT)
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        pass  # Nom DNS: l'adresse réellement résolue est filtrée ci-dessous.
    else:
        if not _is_public_address(host):
            raise UnsafeURL("adresse IP non publique", code=PRIVATE_ADDRESS)
    return url


class PublicOnlyResolver(AbstractResolver):
    """Résolveur aiohttp qui ne retourne jamais une adresse non publique."""

    def __init__(self) -> None:
        self._delegate = aiohttp.DefaultResolver()

    async def resolve(self, host: str, port: int = 0, family: int = 0):
        results = await self._delegate.resolve(host, port, family)
        if not results or any(not _is_public_address(item["host"]) for item in results):
            raise OSError("résolution vers une adresse non publique refusée")
        return results

    async def close(self) -> None:
        await self._delegate.close()


@dataclass(frozen=True)
class SafeHTTPResponse:
    body: bytes
    content_type: str
    final_url: str
    status: int


async def safe_get_bytes(
    url: str,
    *,
    max_bytes: int,
    timeout_seconds: float = 15,
    allowed_content_types: set[str] | None = None,
    headers: dict[str, str] | None = None,
) -> SafeHTTPResponse:
    """Télécharge une ressource publique avec taille et redirections bornées."""
    current = validate_public_url(url)
    resolver = PublicOnlyResolver()
    connector = aiohttp.TCPConnector(resolver=resolver, ttl_dns_cache=0)
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        for _ in range(MAX_REDIRECTS + 1):
            async with session.get(current, headers=headers, allow_redirects=False) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        return SafeHTTPResponse(b"", "", current, response.status)
                    current = validate_public_url(urljoin(current, location))
                    continue

                content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].lower().strip()
                if allowed_content_types is not None and content_type not in allowed_content_types:
                    raise UnsafeURL(
                        f"type de contenu interdit: {content_type or 'inconnu'}",
                        code=DISALLOWED_CONTENT_TYPE,
                    )
                length = response.headers.get("Content-Length")
                if length:
                    try:
                        if int(length) > max_bytes:
                            raise UnsafeURL("réponse trop volumineuse", code=RESPONSE_TOO_LARGE)
                    except ValueError:
                        raise UnsafeURL("Content-Length invalide", code=INVALID_CONTENT_LENGTH)
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.content.iter_chunked(min(16_384, max_bytes + 1)):
                    total += len(chunk)
                    if total > max_bytes:
                        raise UnsafeURL("réponse trop volumineuse", code=RESPONSE_TOO_LARGE)
                    chunks.append(chunk)
                return SafeHTTPResponse(
                    body=b"".join(chunks),
                    content_type=content_type,
                    final_url=current,
                    status=response.status,
                )
    raise UnsafeURL("trop de redirections", code=TOO_MANY_REDIRECTS)
