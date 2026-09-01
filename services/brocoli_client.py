"""HTTP client for Brocoli, the backend's AI assistant.

Brocoli lives in ``website-backend`` (``app/ai/``, ``app/routers/ai.py``). The
bot is a second client next to the dashboard, not a second implementation: the
agent loop, the tools, the history and every write stay on the backend. This
module only carries requests and parses the event stream.

Authentication goes through a signed identity assertion
(``utils/brocoli_signature.py``): the bot says *who is typing*, the backend
derives what that person may do. See ``docs/BROCOLI_CHANNEL.md``.

Every call is signed individually — the assertion is per-request, not a token
held for the length of a conversation. A request whose assertion is replayed is
rejected by the backend, so ``build_headers`` must be called afresh each time.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Optional

import aiohttp

from utils.brocoli_signature import AssertionNotConfigured, build_headers

logger = logging.getLogger('moddy.brocoli')

# A turn can legitimately take a while: several model calls, several tools. The
# read timeout must therefore be generous, while connecting must not be — a
# backend that is down should fail fast rather than hang the channel.
CONNECT_TIMEOUT = 10
TOTAL_TIMEOUT = 180

# Longest single SSE line accepted. A malformed or hostile stream must not be
# able to grow the bot's memory without bound.
MAX_LINE_BYTES = 256 * 1024


class BrocoliError(Exception):
    """A call to Brocoli failed in a way the channel should explain.

    ``status`` is the HTTP status when there was one. ``code`` is a short
    machine-readable reason used to pick the message shown in the channel:
    ``unavailable``, ``quota``, ``busy``, ``forbidden``, ``not_found``,
    ``expired``, ``network``, ``not_configured``.
    """

    def __init__(self, code: str, status: Optional[int] = None, detail: str = ""):
        self.code = code
        self.status = status
        self.detail = detail
        super().__init__(f"{code}({status}): {detail}" if status else code)


# HTTP statuses the channel knows how to explain. Anything else is a bug on our
# side and is surfaced as a generic error rather than guessed at.
_STATUS_CODES = {
    401: "forbidden",
    403: "forbidden",
    404: "not_found",
    409: "busy",
    422: "bad_request",
    429: "quota",
    503: "unavailable",
}


class BrocoliClient:
    """Thin, per-request-signed client over the backend's ``/ai`` surface."""

    def __init__(self, base_url: str, secret: str):
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(
                    total=TOTAL_TIMEOUT, connect=CONNECT_TIMEOUT
                )
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _headers(self, user_id: int, guild_id: int) -> dict[str, str]:
        try:
            headers = build_headers(user_id, guild_id, self.secret)
        except AssertionNotConfigured as exc:
            raise BrocoliError("not_configured", detail=str(exc)) from exc
        headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def _raise_for_status(status: int, body: str) -> None:
        if status < 400:
            return
        code = _STATUS_CODES.get(status, "network")
        # 409 covers two very different situations and the channel words them
        # differently: a turn already running, versus an action that timed out.
        if status == 409 and "xpir" in body:
            code = "expired"
        raise BrocoliError(code, status=status, detail=body[:200])

    async def _post_json(
        self, path: str, user_id: int, guild_id: int, payload: dict
    ) -> dict:
        session = await self._get_session()
        try:
            async with session.post(
                f"{self.base_url}{path}",
                headers=self._headers(user_id, guild_id),
                json=payload,
            ) as response:
                body = await response.text()
                self._raise_for_status(response.status, body)
                return json.loads(body) if body else {}
        except aiohttp.ClientError as exc:
            raise BrocoliError("network", detail=str(exc)) from exc

    async def open_conversation(
        self, user_id: int, guild_id: int, mode: str = "ask"
    ) -> dict:
        """Open a ``guild_config`` conversation for this member and guild.

        The genre is not negotiable from here — the backend refuses anything
        else on an asserted call — but it is sent explicitly so the request
        reads the same as the dashboard's.
        """
        return await self._post_json(
            "/ai/conversations",
            user_id,
            guild_id,
            {"kind": "guild_config", "mode": mode, "guild_id": str(guild_id)},
        )

    async def status(self, user_id: int, guild_id: int) -> dict:
        session = await self._get_session()
        try:
            async with session.get(
                f"{self.base_url}/ai/status",
                headers=self._headers(user_id, guild_id),
            ) as response:
                body = await response.text()
                self._raise_for_status(response.status, body)
                return json.loads(body)
        except aiohttp.ClientError as exc:
            raise BrocoliError("network", detail=str(exc)) from exc

    async def send_message(
        self,
        conversation_id: str,
        user_id: int,
        guild_id: int,
        message: str,
        mode: Optional[str] = None,
    ) -> AsyncIterator[dict]:
        """Send a message and yield the backend's events as they arrive."""
        payload: dict[str, Any] = {"message": message}
        if mode:
            payload["mode"] = mode
        async for event in self._stream(
            f"/ai/conversations/{conversation_id}/messages", user_id, guild_id, payload
        ):
            yield event

    async def decide(
        self,
        conversation_id: str,
        action_id: str,
        user_id: int,
        guild_id: int,
        approve: bool,
    ) -> AsyncIterator[dict]:
        """Approve or refuse a pending action, resuming the turn."""
        async for event in self._stream(
            f"/ai/conversations/{conversation_id}/actions/{action_id}/decision",
            user_id,
            guild_id,
            {"decision": "approve" if approve else "deny"},
        ):
            yield event

    async def _stream(
        self, path: str, user_id: int, guild_id: int, payload: dict
    ) -> AsyncIterator[dict]:
        """POST and parse a ``text/event-stream`` response.

        Errors before the stream starts arrive as an HTTP status; errors after
        it started arrive as an ``error`` event, because the status can no
        longer change. Both paths must be handled — a caller that only checks
        the status would read a failed turn as a successful empty one.
        """
        session = await self._get_session()
        try:
            async with session.post(
                f"{self.base_url}{path}",
                headers=self._headers(user_id, guild_id),
                json=payload,
            ) as response:
                if response.status >= 400:
                    self._raise_for_status(response.status, await response.text())

                event_name = "message"
                data_lines: list[str] = []

                async for raw in response.content:
                    if len(raw) > MAX_LINE_BYTES:
                        raise BrocoliError("network", detail="SSE line too long")
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")

                    if not line:
                        # Blank line terminates an event.
                        if data_lines:
                            yield {
                                "event": event_name,
                                "data": _parse_json("\n".join(data_lines)),
                            }
                        event_name, data_lines = "message", []
                        continue

                    if line.startswith(":"):
                        continue  # comment / keep-alive
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())

                if data_lines:  # stream ended without a trailing blank line
                    yield {"event": event_name, "data": _parse_json("\n".join(data_lines))}
        except aiohttp.ClientError as exc:
            raise BrocoliError("network", detail=str(exc)) from exc


def _parse_json(raw: str) -> dict:
    """Decode an event payload, never raising on a malformed one.

    A single unreadable event must not tear down a turn that is otherwise fine.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("[Brocoli] unreadable SSE payload: %.120s", raw)
        return {}
    return parsed if isinstance(parsed, dict) else {}
