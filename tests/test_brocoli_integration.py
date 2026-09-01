"""End-to-end test of the Brocoli channel, against a real HTTP server.

Run with: python3 -m pytest tests/test_brocoli_integration.py -q

`tests/test_brocoli.py` checks the pieces. This one wires them together and
runs the actual chain: sign an assertion → real HTTP request → real SSE
response → event handling → the messages the channel would show.

The server here is not a mock of our client; it is an independent
reimplementation of what the backend promises. It verifies the HMAC with the
backend's algorithm written out longhand and refuses anything that does not
match, so a signing bug fails the test instead of being mirrored by a stub that
was written from the same misunderstanding.

Discord is stubbed — there is no way to assert against the real thing offline —
but the stub records exactly the send/edit/delete calls the cog makes, which is
what determines whether a member sees one card that updates or a wall of
messages.
"""

import asyncio
import hashlib
import hmac
import json
import os
from typing import Optional

import pytest

os.environ.setdefault("DISCORD_TOKEN", "test-token")

from aiohttp import web  # noqa: E402

SECRET = "b" * 40
GUILD = 1421493239579676682
CHANNEL = 1544393707707437117
USER = 708006478807793776


# ---------------------------------------------------------------------------
# A stand-in backend that enforces the contract
# ---------------------------------------------------------------------------

class FakeBackend:
    """Verifies assertions the way `app/middleware/bot_auth.py` does."""

    def __init__(self, events: list[tuple[str, dict]], status: int = 200):
        self.events = events
        self.status = status
        self.seen_request_ids: set[str] = set()
        self.calls: list[dict] = []
        self.rejected: list[str] = []

    def _verify(self, request) -> Optional[str]:
        """Returns a rejection reason, or None when the assertion is good."""
        headers = request.headers
        try:
            body = {
                "user_id": headers["X-Moddy-Assert-User"],
                "guild_id": headers["X-Moddy-Assert-Guild"],
                "request_id": headers["X-Moddy-Assert-Request-Id"],
                "issued_at": headers["X-Moddy-Assert-Issued-At"],
            }
            provided = headers["X-Moddy-Assert-Signature"]
        except KeyError as exc:
            return f"missing header {exc}"

        canonical = json.dumps(
            body, separators=(",", ":"), sort_keys=True, ensure_ascii=True
        )
        expected = hmac.new(
            SECRET.encode(), canonical.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(provided, expected):
            return "bad signature"

        # Single use, exactly like the backend's SET NX marker.
        if body["request_id"] in self.seen_request_ids:
            return "replay"
        self.seen_request_ids.add(body["request_id"])
        return None

    async def conversations(self, request):
        reason = self._verify(request)
        if reason:
            self.rejected.append(reason)
            return web.json_response({"detail": reason}, status=401)
        self.calls.append({"path": "conversations", "body": await request.json()})
        return web.json_response({"id": "11111111-2222-3333-4444-555555555555"})

    async def messages(self, request):
        reason = self._verify(request)
        if reason:
            self.rejected.append(reason)
            return web.json_response({"detail": reason}, status=401)
        self.calls.append({"path": "messages", "body": await request.json()})

        if self.status >= 400:
            return web.json_response({"detail": "nope"}, status=self.status)

        response = web.StreamResponse(
            status=200, headers={"Content-Type": "text/event-stream"}
        )
        await response.prepare(request)
        for name, data in self.events:
            payload = json.dumps(data, separators=(",", ":"))
            await response.write(f"event: {name}\ndata: {payload}\n\n".encode())
        await response.write_eof()
        return response


async def _serve(backend: FakeBackend):
    app = web.Application()
    app.router.add_post("/ai/conversations", backend.conversations)
    app.router.add_post(
        "/ai/conversations/{cid}/messages", backend.messages
    )
    app.router.add_post(
        "/ai/conversations/{cid}/actions/{aid}/decision", backend.messages
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    return runner, f"http://127.0.0.1:{port}"


# ---------------------------------------------------------------------------
# Discord stubs
# ---------------------------------------------------------------------------

class FakeMessage:
    def __init__(self, channel):
        self.channel = channel
        self.views: list = []
        self.deleted = False

    async def edit(self, view=None, **_):
        self.views.append(("edit", view))

    async def delete(self):
        self.deleted = True


class FakeTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeChannel:
    def __init__(self, channel_id=CHANNEL):
        self.id = channel_id
        self.sent: list[FakeMessage] = []

    async def send(self, view=None, **_):
        message = FakeMessage(self)
        message.views.append(("send", view))
        self.sent.append(message)
        return message

    def typing(self):
        return FakeTyping()


def _first_text(view) -> str:
    """Concatenate every TextDisplay in a rendered view."""
    chunks = []
    for component in view.to_components():
        for child in component.get("components", []):
            if child.get("type") == 10:
                chunks.append(child.get("content", ""))
    return "\n".join(chunks)


# ---------------------------------------------------------------------------
# The tests
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def test_a_full_turn_signs_streams_and_renders_one_card():
    backend = FakeBackend([
        ("text_delta", {"delta": "Le starboard "}),
        ("text_delta", {"delta": "est activé."}),
        ("run_end", {"status": "completed"}),
    ])

    async def scenario():
        runner, base = await _serve(backend)
        try:
            from cogs.brocoli_chat import BrocoliChat
            from services.brocoli_client import BrocoliClient

            cog = BrocoliChat.__new__(BrocoliChat)  # no bot needed for _render
            cog.client = BrocoliClient(base, SECRET)

            channel = FakeChannel()
            from utils.brocoli_views import loading_card

            card = await channel.send(view=loading_card("fr"))

            conversation = await cog.client.open_conversation(USER, GUILD)
            stream = cog.client.send_message(
                conversation["id"], USER, GUILD, "active le starboard"
            )
            await cog._render(stream, channel, conversation["id"], "fr", card=card)
            await cog.client.close()
        finally:
            await runner.cleanup()
        return channel, card

    channel, card = _run(scenario())

    # The assertion was accepted on both calls — nothing rejected.
    assert backend.rejected == []
    assert [c["path"] for c in backend.calls] == ["conversations", "messages"]
    assert backend.calls[1]["body"]["message"] == "active le starboard"

    # Each request carried its own request_id, so neither looked like a replay.
    assert len(backend.seen_request_ids) == 2

    # One card, sent once then edited — not a wall of messages.
    assert len(channel.sent) == 1
    assert card.views[0][0] == "send"
    assert all(kind == "edit" for kind, _ in card.views[1:])
    assert "Le starboard est activé." in _first_text(card.views[-1][1])


def test_the_member_sees_the_loading_card_before_anything_else():
    backend = FakeBackend([("run_end", {"status": "completed"})])

    async def scenario():
        runner, base = await _serve(backend)
        try:
            from services.brocoli_client import BrocoliClient
            from utils.brocoli_views import loading_card

            channel = FakeChannel()
            card = await channel.send(view=loading_card("fr"))
            first = _first_text(card.views[0][1])

            client = BrocoliClient(base, SECRET)
            await client.open_conversation(USER, GUILD)
            await client.close()
            return first
        finally:
            await runner.cleanup()

    first = _run(scenario())
    assert first == "<a:spinner:1534857169667883078> **Moddy** réfléchit..."


def test_a_confirmation_replaces_the_placeholder_with_a_card_that_has_buttons():
    backend = FakeBackend([
        ("permission_request", {
            "action_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "kind": "set_module_config",
            "risk": "low",
            "preview": {
                "summary": "Active le starboard à 5 étoiles",
                "module_id": "starboard",
                "valid": True,
                "diff": [{"path": "threshold", "op": "changed",
                          "before": 3, "after": 5}],
            },
        }),
        ("run_end", {"status": "awaiting_confirmation"}),
    ])

    async def scenario():
        runner, base = await _serve(backend)
        try:
            from cogs.brocoli_chat import BrocoliChat
            from services.brocoli_client import BrocoliClient
            from utils.brocoli_views import loading_card

            cog = BrocoliChat.__new__(BrocoliChat)
            cog.client = BrocoliClient(base, SECRET)

            channel = FakeChannel()
            card = await channel.send(view=loading_card("fr"))
            conversation = await cog.client.open_conversation(USER, GUILD)
            stream = cog.client.send_message(
                conversation["id"], USER, GUILD, "starboard à 5"
            )
            await cog._render(stream, channel, conversation["id"], "fr", card=card)
            await cog.client.close()
        finally:
            await runner.cleanup()
        return channel, card

    channel, card = _run(scenario())

    # The turn produced no prose, so the placeholder is removed rather than
    # edited into an empty container (which Discord rejects).
    assert card.deleted is True

    # A second message carries the question, with two buttons.
    assert len(channel.sent) == 2
    question = channel.sent[1].views[0][1]
    text = _first_text(question)
    assert "Active le starboard à 5 étoiles" in text
    assert "threshold" in text

    buttons = [
        child
        for component in question.to_components()
        for row in component.get("components", [])
        if row.get("type") == 1
        for child in row.get("components", [])
    ]
    assert len(buttons) == 2
    assert all(
        b["custom_id"].startswith("moddy:brocoli:decision:") for b in buttons
    )


def test_a_backend_error_becomes_a_notice_and_not_a_traceback():
    backend = FakeBackend([], status=429)

    async def scenario():
        runner, base = await _serve(backend)
        try:
            from services.brocoli_client import BrocoliClient, BrocoliError

            client = BrocoliClient(base, SECRET)
            conversation = await client.open_conversation(USER, GUILD)
            try:
                async for _ in client.send_message(
                    conversation["id"], USER, GUILD, "hello"
                ):
                    pass
            except BrocoliError as exc:
                return exc.code
            finally:
                await client.close()
            return None
        finally:
            await runner.cleanup()

    assert _run(scenario()) == "quota"


def test_a_tampered_secret_is_refused_by_the_backend():
    """Proves the server is actually checking, not rubber-stamping."""
    backend = FakeBackend([("run_end", {"status": "completed"})])

    async def scenario():
        runner, base = await _serve(backend)
        try:
            from services.brocoli_client import BrocoliClient, BrocoliError

            client = BrocoliClient(base, "c" * 40)  # wrong secret
            try:
                await client.open_conversation(USER, GUILD)
            except BrocoliError as exc:
                return exc.code
            finally:
                await client.close()
            return None
        finally:
            await runner.cleanup()

    assert _run(scenario()) == "forbidden"
    assert backend.rejected == ["bad signature"]


def test_replaying_one_assertion_is_refused():
    """The client must not reuse a request_id across calls."""
    backend = FakeBackend([("run_end", {"status": "completed"})])

    async def scenario():
        runner, base = await _serve(backend)
        try:
            import aiohttp

            from utils.brocoli_signature import build_headers

            headers = build_headers(USER, GUILD, SECRET)
            headers["Content-Type"] = "application/json"
            async with aiohttp.ClientSession() as session:
                statuses = []
                for _ in range(2):
                    async with session.post(
                        f"{base}/ai/conversations", headers=headers, json={}
                    ) as response:
                        statuses.append(response.status)
                return statuses
        finally:
            await runner.cleanup()

    assert _run(scenario()) == [200, 401]
    assert backend.rejected == ["replay"]
