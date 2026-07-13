"""A tiny in-memory async Redis stand-in for the automod cost-control tests.

Only the handful of commands the engine uses are implemented, with
``decode_responses=True`` semantics (str in, str out) to match the real client.
"""

from __future__ import annotations

from typing import Dict, List, Set


class FakeRedis:
    def __init__(self):
        self.kv: Dict[str, str] = {}
        self.lists: Dict[str, List[str]] = {}
        self.sets: Dict[str, Set[str]] = {}

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, value):
        self.kv[key] = str(value)
        return True

    async def incr(self, key):
        self.kv[key] = str(int(self.kv.get(key, "0")) + 1)
        return int(self.kv[key])

    async def expire(self, key, ttl):
        return True

    async def lpush(self, key, *values):
        lst = self.lists.setdefault(key, [])
        for v in values:
            lst.insert(0, str(v))  # newest at the front, like Redis
        return len(lst)

    async def ltrim(self, key, start, end):
        lst = self.lists.get(key, [])
        self.lists[key] = lst[start:] if end == -1 else lst[start:end + 1]
        return True

    async def lrange(self, key, start, end):
        lst = self.lists.get(key, [])
        return list(lst[start:]) if end == -1 else list(lst[start:end + 1])

    async def sadd(self, key, *values):
        s = self.sets.setdefault(key, set())
        for v in values:
            s.add(str(v))
        return len(s)

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def delete(self, *keys):
        for k in keys:
            self.kv.pop(k, None)
            self.lists.pop(k, None)
            self.sets.pop(k, None)
        return True
