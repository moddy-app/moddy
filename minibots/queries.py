"""
Read-only questions Moddy's staff commands ask the mini-bots.

Moddy's process has no mini-bot token and no mini-bot cache, so ``t.mutual``,
``t.server``, ``t.invite`` and ``d.servers`` ask the mini-bots process over
Redis (:func:`ask`, Moddy side) and the runtime answers with
:func:`answer` (mini-bots side). One answer for every app of the process:
``{app: result}``. Every method is read-only except ``create_invite``, which
is what ``t.invite`` already does on Moddy.

Mini-bots keep no member cache (RAM), so ``mutual_guilds`` asks the gateway
server by server (``query_members(user_ids=...)``, no privileged intent),
biggest servers first, at most :data:`MUTUAL_SCAN_LIMIT` per app — the answer
says when it was cut.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from minibots import status as mstatus

logger = logging.getLogger("moddy.minibots.queries")

MUTUAL_SCAN_LIMIT = 100
_MUTUAL_CONCURRENCY = 8

_KEY_PERMS = ("administrator", "manage_guild", "manage_channels", "manage_roles",
              "ban_members", "kick_members", "moderate_members")


# --------------------------------------------------------------------------- #
# Moddy side
# --------------------------------------------------------------------------- #

async def ask(bot, method: str, params: Optional[Dict[str, Any]] = None, *,
              timeout: float = mstatus.QUERY_TIMEOUT) -> Optional[Dict[str, Any]]:
    """``{app: result}``, or None when the mini-bots process does not answer."""
    if method not in mstatus.QUERY_METHODS:
        raise ValueError(method)
    redis = getattr(bot, "redis", None)
    if redis is None:
        return None
    request_id = mstatus.new_request_id()
    try:
        listeners = await redis.publish(mstatus.CONTROL_CHANNEL, mstatus.control_message(
            mstatus.CTRL_QUERY, request_id=request_id, method=method, params=params or {}))
        if not listeners:
            return None
        reply = await redis.blpop(mstatus.ack_key(request_id), timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Mini-bots query %s failed: %s", method, exc)
        return None
    if not reply:
        return None
    raw = reply[1] if isinstance(reply, (list, tuple)) else reply
    try:
        data = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# --------------------------------------------------------------------------- #
# Mini-bots side
# --------------------------------------------------------------------------- #

def _perms(member) -> List[str]:
    perms = member.guild_permissions
    if perms.administrator:
        return ["administrator"]
    return [name for name in _KEY_PERMS if getattr(perms, name, False)]


async def _guilds(client, params) -> List[Dict[str, Any]]:
    rows = []
    for guild in client.guilds:
        joined = guild.me.joined_at if guild.me else None
        rows.append({"id": str(guild.id), "name": guild.name,
                     "members": guild.member_count or 0,
                     "joined": int(joined.timestamp()) if joined else None})
    rows.sort(key=lambda r: r["members"], reverse=True)
    return rows


async def _mutual_guilds(client, params) -> Dict[str, Any]:
    user_id = int(params["user_id"])
    guilds = sorted(client.guilds, key=lambda g: g.member_count or 0, reverse=True)
    scanned = guilds[:MUTUAL_SCAN_LIMIT]
    semaphore = asyncio.Semaphore(_MUTUAL_CONCURRENCY)
    found: List[Dict[str, Any]] = []

    async def check(guild):
        async with semaphore:
            try:
                members = await guild.query_members(user_ids=[user_id], limit=1, cache=False)
            except Exception:  # noqa: BLE001 — one server never sinks the answer
                return
        if members:
            member = members[0]
            top = member.top_role.name if member.top_role and member.top_role.name != "@everyone" else None
            found.append({"id": str(guild.id), "name": guild.name,
                          "top_role": top, "perms": _perms(member)})

    await asyncio.gather(*(check(g) for g in scanned))
    found.sort(key=lambda r: r["name"].lower())
    return {"guilds": found, "scanned": len(scanned), "total": len(guilds),
            "truncated": len(guilds) > len(scanned)}


async def _guild_info(client, params) -> Optional[Dict[str, Any]]:
    guild = client.get_guild(int(params["guild_id"]))
    if guild is None:
        return None
    return {
        "id": str(guild.id), "name": guild.name, "owner_id": str(guild.owner_id),
        "members": guild.member_count or 0,
        "created": int(guild.created_at.timestamp()),
        "text": len(guild.text_channels), "voice": len(guild.voice_channels),
        "categories": len(guild.categories), "roles": len(guild.roles),
        "premium_tier": guild.premium_tier,
        "boosts": guild.premium_subscription_count or 0,
        "features": list(guild.features[:10]),
    }


async def _create_invite(client, params) -> Optional[Dict[str, Any]]:
    import discord
    guild = client.get_guild(int(params["guild_id"]))
    if guild is None:
        return None
    channel = guild.system_channel
    if not channel or not channel.permissions_for(guild.me).create_instant_invite:
        channel = next((c for c in guild.text_channels
                        if c.permissions_for(guild.me).create_instant_invite), None)
    if channel is None:
        return {"error": "no_perm", "name": guild.name}
    try:
        invite = await channel.create_invite(
            max_age=604800, max_uses=5, unique=True,
            reason=f"Staff invite requested by {params.get('requested_by', '?')}")
    except discord.HTTPException:
        return {"error": "no_perm", "name": guild.name}
    return {"url": invite.url, "name": guild.name}


_METHODS = {
    "guilds": _guilds,
    "mutual_guilds": _mutual_guilds,
    "guild_info": _guild_info,
    "create_invite": _create_invite,
}


async def answer(clients, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Run ``method`` on every ready client: ``{app: result}``."""
    handler = _METHODS[method]
    only = params.get("app")
    results: Dict[str, Any] = {}

    async def run(client):
        with client.emoji_context():
            try:
                results[client.app_id] = await handler(client, params)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Query %s failed on %s: %s", method, client.app_id, exc)
                results[client.app_id] = {"error": "failed"}

    targets = [c for c in clients if c.is_ready() and only in (None, c.app_id)]
    if method == "create_invite":
        # One invite is enough: the first app present in that server.
        for client in targets:
            await run(client)
            if results.get(client.app_id):
                break
        return {k: v for k, v in results.items() if v}
    await asyncio.gather(*(run(c) for c in targets))
    return results
