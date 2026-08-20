from __future__ import annotations

import pytest

from utils.safe_http import PublicOnlyResolver, UnsafeURL, validate_public_url


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "http://localhost/admin",
        "http://service.local/",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",
        "https://user:pass@example.com/",
        "https://example.com:6379/",
    ],
)
def test_validate_public_url_rejects_ssrf_targets(url):
    with pytest.raises(UnsafeURL):
        validate_public_url(url)


def test_validate_public_url_accepts_regular_https_url():
    assert validate_public_url("https://example.com/path?q=1") == "https://example.com/path?q=1"


@pytest.mark.asyncio
async def test_resolver_rejects_private_dns_answer(monkeypatch):
    resolver = PublicOnlyResolver()

    async def fake_resolve(host, port, family):
        return [{"hostname": host, "host": "10.0.0.5", "port": port, "family": family, "proto": 0, "flags": 0}]

    monkeypatch.setattr(resolver._delegate, "resolve", fake_resolve)
    with pytest.raises(OSError):
        await resolver.resolve("attacker.example", 443)
    await resolver.close()
