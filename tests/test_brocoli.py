"""Tests for the Brocoli channel: assertion signing and SSE parsing.

Run with: python3 -m pytest tests/test_brocoli.py -q

The signature tests re-derive the expected HMAC from the *backend's* algorithm
written out longhand, rather than calling our own helper twice. A test that uses
the implementation to check the implementation would pass just as happily if
both sides drifted together — and the whole point of this contract is that two
codebases must agree byte for byte.
"""

import asyncio
import hashlib
import hmac
import importlib.util
import json
import time
from pathlib import Path

import pytest

# Loaded by path: `utils/__init__.py` pulls in discord.py, and this module is
# pure stdlib — the suite must stay runnable without a Discord install.
_SPEC = importlib.util.spec_from_file_location(
    "moddy_brocoli_signature",
    Path(__file__).resolve().parent.parent / "utils" / "brocoli_signature.py",
)
signature = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(signature)

SECRET = "b" * 40
USER = "708006478807793776"
GUILD = "1421493239579676682"


def _backend_signature(fields: dict, secret: str) -> str:
    """The backend's computation, spelled out (app/redis/signing.py)."""
    body = {k: v for k, v in fields.items() if k != "signature"}
    canonical = json.dumps(body, separators=(",", ":"), sort_keys=True, ensure_ascii=True)
    return hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Signature contract
# ---------------------------------------------------------------------------

def test_headers_verify_against_the_backend_algorithm():
    headers = signature.build_headers(USER, GUILD, SECRET)

    expected = _backend_signature(
        {
            "user_id": headers[signature.HEADER_USER],
            "guild_id": headers[signature.HEADER_GUILD],
            "request_id": headers[signature.HEADER_REQUEST_ID],
            "issued_at": headers[signature.HEADER_ISSUED_AT],
        },
        SECRET,
    )
    assert hmac.compare_digest(headers[signature.HEADER_SIGNATURE], expected)


def test_canonical_key_order_is_the_contract_order():
    canonical = signature.canonical_string(
        {"user_id": USER, "guild_id": GUILD, "request_id": "r", "issued_at": "1"}
    )
    assert canonical == (
        '{"guild_id":"1421493239579676682","issued_at":"1","request_id":"r",'
        '"user_id":"708006478807793776"}'
    )
    assert " " not in canonical  # separators=(",", ":")


def test_ids_are_serialized_as_strings():
    """A snowflake sent as a number would not reproduce the backend's string."""
    canonical = signature.canonical_string(
        {"user_id": 708006478807793776, "guild_id": 1, "request_id": "r", "issued_at": 2}
    )
    assert '"user_id":"708006478807793776"' in canonical
    assert '"issued_at":"2"' in canonical


def test_every_signed_field_is_covered():
    base = {"user_id": USER, "guild_id": GUILD, "request_id": "r", "issued_at": "1"}
    reference = signature.compute_signature(base, SECRET)

    for field, value in (
        ("user_id", "1"),
        ("guild_id", "2"),
        ("request_id", "other"),
        ("issued_at", "999"),
    ):
        altered = dict(base, **{field: value})
        assert signature.compute_signature(altered, SECRET) != reference


def test_each_call_gets_a_fresh_request_id():
    """Reusing one would be rejected by the backend's anti-replay marker."""
    ids = {
        signature.build_headers(USER, GUILD, SECRET)[signature.HEADER_REQUEST_ID]
        for _ in range(20)
    }
    assert len(ids) == 20


def test_issued_at_is_current_unix_seconds():
    headers = signature.build_headers(USER, GUILD, SECRET)
    assert abs(int(headers[signature.HEADER_ISSUED_AT]) - int(time.time())) <= 2


def test_signing_fails_closed_without_a_strong_secret():
    for bad in ("", None, "too-short"):
        assert signature.is_configured(bad) is False
        with pytest.raises(signature.AssertionNotConfigured):
            signature.build_headers(USER, GUILD, bad)


def test_a_partial_field_set_is_never_signed():
    """Signing over missing fields would produce an unverifiable signature."""
    with pytest.raises(KeyError):
        signature.canonical_string({"user_id": USER})


# ---------------------------------------------------------------------------
# SSE parsing
# ---------------------------------------------------------------------------

def _client():
    """The client module, loaded without importing the whole bot package."""
    spec = importlib.util.spec_from_file_location(
        "moddy_brocoli_client",
        Path(__file__).resolve().parent.parent / "services" / "brocoli_client.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeContent:
    """Iterates raw lines the way aiohttp's response content does."""

    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        async def gen():
            for chunk in self._chunks:
                yield chunk
        return gen()


class _FakeResponse:
    def __init__(self, chunks, status=200):
        self.status = status
        self.content = _FakeContent(chunks)

    async def text(self):
        return ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, response):
        self._response = response

    def post(self, *args, **kwargs):
        return self._response


def _drain(client, response):
    client._get_session = lambda: _resolved(_FakeSession(response))

    async def run():
        return [
            event
            async for event in client._stream("/x", 1, 2, {})
        ]

    return asyncio.run(run())


def _resolved(value):
    async def coro():
        return value
    return coro()


def test_stream_parses_named_events():
    module = _client()
    client = module.BrocoliClient("https://api.example", SECRET)

    events = _drain(
        client,
        _FakeResponse([
            b'event: text_delta\n',
            b'data: {"delta":"Hel"}\n',
            b'\n',
            b'event: text_delta\n',
            b'data: {"delta":"lo"}\n',
            b'\n',
            b'event: run_end\n',
            b'data: {"status":"completed"}\n',
            b'\n',
        ]),
    )

    assert [e["event"] for e in events] == ["text_delta", "text_delta", "run_end"]
    assert "".join(e["data"].get("delta", "") for e in events) == "Hello"


def test_stream_yields_a_trailing_event_without_blank_line():
    """A stream cut short must not swallow its last event."""
    module = _client()
    client = module.BrocoliClient("https://api.example", SECRET)

    events = _drain(
        client,
        _FakeResponse([b'event: run_end\n', b'data: {"status":"error"}\n']),
    )
    assert events == [{"event": "run_end", "data": {"status": "error"}}]


def test_stream_ignores_comments_and_keepalives():
    module = _client()
    client = module.BrocoliClient("https://api.example", SECRET)

    events = _drain(
        client,
        _FakeResponse([
            b': keep-alive\n',
            b'event: run_end\n',
            b'data: {"status":"completed"}\n',
            b'\n',
        ]),
    )
    assert len(events) == 1


def test_a_malformed_payload_does_not_kill_the_turn():
    module = _client()
    client = module.BrocoliClient("https://api.example", SECRET)

    events = _drain(
        client,
        _FakeResponse([
            b'event: text_delta\n', b'data: {oops\n', b'\n',
            b'event: run_end\n', b'data: {"status":"completed"}\n', b'\n',
        ]),
    )
    assert events[0]["data"] == {}
    assert events[1]["data"]["status"] == "completed"


def test_statuses_map_to_wordings_the_channel_knows():
    module = _client()
    for status, code in (
        (429, "quota"), (503, "unavailable"), (409, "busy"),
        (403, "forbidden"), (404, "not_found"),
    ):
        with pytest.raises(module.BrocoliError) as exc:
            module.BrocoliClient._raise_for_status(status, "")
        assert exc.value.code == code


def test_an_expired_action_is_told_apart_from_a_busy_turn():
    """Both are 409 and the channel words them differently."""
    module = _client()
    with pytest.raises(module.BrocoliError) as exc:
        module.BrocoliClient._raise_for_status(409, "Action expiree - redemande")
    assert exc.value.code == "expired"


def test_a_successful_status_raises_nothing():
    module = _client()
    assert module.BrocoliClient._raise_for_status(200, "") is None


# ---------------------------------------------------------------------------
# Channel matching
# ---------------------------------------------------------------------------

def _cog_module():
    """`cogs.brocoli_chat` needs a token to import (config validates at import)."""
    import os

    os.environ.setdefault("DISCORD_TOKEN", "test-token")
    import importlib

    return importlib.import_module("cogs.brocoli_chat")


def test_a_configured_channel_needs_no_database_row():
    """BROCOLI_CHANNEL_IDS points the feature at a channel that already exists."""
    module = _cog_module()
    match = module.BrocoliChat._is_brocoli_channel

    module.BROCOLI_CHANNEL_IDS.append(1544393707707437117)
    try:
        assert match(1544393707707437117, {}) is True
        assert match(999, {}) is False
    finally:
        module.BROCOLI_CHANNEL_IDS.remove(1544393707707437117)


def test_a_stored_channel_is_still_recognised():
    module = _cog_module()
    match = module.BrocoliChat._is_brocoli_channel

    assert match(42, {"channel_id": "42"}) is True
    assert match(42, {"channel_id": 42}) is True  # ints and strings both occur
    assert match(43, {"channel_id": "42"}) is False


def test_no_channel_configured_matches_nothing():
    module = _cog_module()
    assert module.BrocoliChat._is_brocoli_channel(1, {}) is False
    assert module.BrocoliChat._is_brocoli_channel(1, {"channel_id": ""}) is False
    assert module.BrocoliChat._is_brocoli_channel(1, {"channel_id": None}) is False


# ---------------------------------------------------------------------------
# Loading card
# ---------------------------------------------------------------------------

def test_loading_card_matches_the_agreed_payload():
    """One container, no accent colour, one line with the animated spinner."""
    _cog_module()  # ensures config/i18n are loaded
    from utils.brocoli_views import loading_card

    payload = loading_card("fr").to_components()

    assert len(payload) == 1
    container = payload[0]
    assert container["type"] == 17
    assert container["accent_color"] is None
    assert len(container["components"]) == 1

    line = container["components"][0]
    assert line["type"] == 10
    assert line["content"] == (
        "<a:spinner:1534857169667883078> **Moddy** réfléchit..."
    )


def test_a_turn_with_no_prose_renders_no_empty_container():
    """Discord rejects a container with no content, so it must not be built."""
    _cog_module()
    from utils.brocoli_views import answer_card

    payload = answer_card("", locale="fr", thinking=True).to_components()
    # While thinking the loading line is always there, so the container is valid.
    assert payload[0]["components"]
