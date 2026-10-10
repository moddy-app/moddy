"""
Automod member profiles — the shared, per-bot plumbing of the ``profile`` feature.

Lives on ``bot.automod_profiles``. The feature in ``modules/automod_ai.py`` only
applies the per-guild policy; everything that must be shared across guilds and
across events lives here (docs/AUTOMOD_AI.md "Profiles"):

* **Avatars and banners** — one verdict per Discord *asset key*
  (``asset.key``, e.g. ``a_abc123``), cached in Redis (``automod:avatar:<key>``,
  30 d) with an in-process LRU in front. An avatar already seen on ANY server
  costs no download and no SafeSearch call. A miss goes download (shared image
  service) -> team hash index -> SafeSearch on the **separate profile budget**
  (monthly fail-closed gateway rule ``safe_search_profile``, smoothed pacing,
  per-guild daily share). Concurrent checks of the same asset (the same user in
  several guilds) share one in-flight job.
* **Banners** — only reachable over REST (``fetch_user``): at most one fetch per
  user per ten minutes, shared by every guild.
* **Names** — nickname / display name / username go through the text funnel
  (``engine.analyze`` with the ``profil_pseudo`` origin). A verdict is cached per
  normalized name (``automod:pseudo:<sha1>``, 30 d, global).
* **Idempotence** — a "done" marker per (guild, member, asset or name) stops a
  profile event storm from sanctioning the same thing twice.
* **Scheduling** — screenings run as bounded background tasks so a raid of joins
  never blocks another module's ``on_member_join`` nor piles up in memory.

Every failure degrades to "not analysed": a profile the pipeline could not read
is never sanctioned.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Awaitable, Dict, Optional

import discord

from automod import constants as ac
from automod.image_hash import (
    KIND_GORE, KIND_NSFW, PreparedImage, from_hex,
)
from automod.normalize import collapse_repeats
from automod.schemas import (
    Decision, ORIGINE_PROFIL_PSEUDO, AuthorHistory, TargetMessage,
)

logger = logging.getLogger("moddy.services.automod_profiles")

KIND_AVATAR = "avatar"
KIND_BANNER = "banner"

_MEMO_MAX = 512
_NEGATIVE_TTL = 600.0          # an unreadable / budget-refused asset is not retried sooner
_BANNER_MEMO_MAX = 2000
_NAME_MAX_CHARS = 100

#: Categories a SafeSearch hash kind can carry for a profile image.
_PROFILE_HASH_KINDS = (KIND_NSFW, KIND_GORE)


@dataclass
class AssetCheck:
    """The outcome of screening one avatar / banner asset (shareable, read-only)."""
    key: str
    kind: str
    #: ``False`` when nothing could be concluded (unreadable image, no budget,
    #: Vision unavailable): the profile is then left alone.
    checked: bool = False
    phash: str = ""
    dhash: str = ""
    likelihoods: Optional[Dict[str, str]] = None
    #: Team hash index hit for a nsfw / gore kind: ``block`` | ``allow``.
    hash_verdict: Optional[str] = None
    hash_kind: Optional[str] = None
    hash_match: Optional[dict] = None
    img: Optional[PreparedImage] = None
    url: str = ""


@dataclass
class NameJudgement:
    """A name verdict: the funnel's Decision (or None), and whether it is fresh."""
    decision: Optional[Decision]
    fresh: bool = True


def normalize_name(name: str) -> str:
    """Stable form of a name for caching: NFKC, case-folded, single-spaced."""
    text = unicodedata.normalize("NFKC", name or "").casefold()
    text = " ".join(text.split())
    return (collapse_repeats(text) or text)[:_NAME_MAX_CHARS]


def name_cache_key(name: str) -> str:
    return "automod:pseudo:" + hashlib.sha1(
        normalize_name(name).encode("utf-8")).hexdigest()


def synthetic_id(user_id: int, kind: str, subject: str) -> str:
    """The ``message_id`` a profile Decision carries (there is no message)."""
    digest = hashlib.sha1(f"{kind}:{subject}".encode("utf-8")).hexdigest()[:12]
    return f"profil:{user_id}:{kind}:{digest}"


class AutomodProfileService:
    def __init__(self, bot):
        self.bot = bot
        self._memo: "OrderedDict[str, dict]" = OrderedDict()
        self._negative: Dict[str, float] = {}
        self._flights: Dict[str, asyncio.Future] = {}
        self._banners: "OrderedDict[int, tuple]" = OrderedDict()
        self._done_mem: Dict[str, float] = {}
        self._name_memo: "OrderedDict[str, dict]" = OrderedDict()
        self._tasks: set = set()

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #

    def _images(self):
        return getattr(self.bot, "automod_images", None)

    def _redis(self):
        return getattr(self.bot, "redis", None)

    def stat(self, etape: str) -> None:
        stats = getattr(self.bot, "stats", None)
        if stats is not None:
            stats.incr("automod.profile", dims={"etape": etape})

    # ------------------------------------------------------------------ #
    # Scheduling (bounded background tasks)
    # ------------------------------------------------------------------ #

    def spawn(self, coro: Awaitable[Any], *, label: str = "profile") -> bool:
        """Run ``coro`` in the background. False (and dropped) when the queue is full."""
        if len(self._tasks) >= ac.PROFILE_QUEUE_MAX:
            logger.debug("automod profile queue full, %s dropped", label)
            close = getattr(coro, "close", None)
            if close is not None:
                close()
            return False
        task = asyncio.ensure_future(self._guarded(coro, label))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    @staticmethod
    async def _guarded(coro: Awaitable[Any], label: str) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("automod profile task %s failed: %s", label, exc, exc_info=True)

    # ------------------------------------------------------------------ #
    # Idempotence markers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _done_key(guild_id: int, user_id: int, subject: str) -> str:
        return f"automod:profile:done:{guild_id}:{user_id}:{subject}"

    async def already_done(self, guild_id: int, user_id: int, subject: str) -> bool:
        key = self._done_key(guild_id, user_id, subject)
        r = self._redis()
        if r is not None:
            try:
                return bool(await r.exists(key))
            except Exception:  # noqa: BLE001
                pass
        until = self._done_mem.get(key)
        if until is not None and until > time.monotonic():
            return True
        self._done_mem.pop(key, None)
        return False

    async def mark_done(self, guild_id: int, user_id: int, subject: str,
                        ttl: int) -> None:
        key = self._done_key(guild_id, user_id, subject)
        r = self._redis()
        if r is not None:
            try:
                await r.set(key, "1", ex=ttl)
                return
            except Exception:  # noqa: BLE001
                pass
        if len(self._done_mem) > 5000:
            now = time.monotonic()
            self._done_mem = {k: v for k, v in self._done_mem.items() if v > now}
        self._done_mem[key] = time.monotonic() + ttl

    async def forget_done(self, guild_id: int, user_id: int, subject: str) -> None:
        key = self._done_key(guild_id, user_id, subject)
        self._done_mem.pop(key, None)
        r = self._redis()
        if r is not None:
            try:
                await r.delete(key)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ #
    # Banners (REST only, throttled)
    # ------------------------------------------------------------------ #

    async def get_banner(self, user_id: int) -> Optional[discord.Asset]:
        """The user's banner asset (None = none / unknown), at most one REST
        fetch per user per ten minutes — shared by every guild they are in."""
        now = time.monotonic()
        hit = self._banners.get(int(user_id))
        if hit is not None and now - hit[0] < ac.PROFILE_BANNER_FETCH_INTERVAL_SECONDS:
            return hit[1]
        banner = None
        try:
            user = await self.bot.fetch_user(int(user_id))
            banner = getattr(user, "banner", None)
        except (discord.NotFound, discord.HTTPException) as exc:
            logger.debug("automod banner fetch failed for %s: %s", user_id, exc)
        except Exception as exc:  # noqa: BLE001
            logger.debug("automod banner fetch crashed for %s: %s", user_id, exc)
        self._banners[int(user_id)] = (now, banner)
        self._banners.move_to_end(int(user_id))
        while len(self._banners) > _BANNER_MEMO_MAX:
            self._banners.popitem(last=False)
        return banner

    # ------------------------------------------------------------------ #
    # Avatars / banners
    # ------------------------------------------------------------------ #

    async def _load_record(self, key: str) -> Optional[dict]:
        rec = self._memo.get(key)
        if rec is not None:
            self._memo.move_to_end(key)
            return rec
        r = self._redis()
        if r is None:
            return None
        try:
            raw = await r.get(f"automod:avatar:{key}")
            rec = json.loads(raw) if raw else None
        except Exception:  # noqa: BLE001
            return None
        if rec is not None:
            self._remember(key, rec)
        return rec

    def _remember(self, key: str, rec: dict) -> None:
        self._memo[key] = rec
        self._memo.move_to_end(key)
        while len(self._memo) > _MEMO_MAX:
            self._memo.popitem(last=False)

    async def _store_record(self, key: str, rec: dict) -> None:
        self._remember(key, rec)
        r = self._redis()
        if r is None:
            return
        try:
            await r.set(f"automod:avatar:{key}", json.dumps(rec),
                        ex=ac.PROFILE_ASSET_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            pass

    async def check_asset(self, asset: discord.Asset, *, kind: str, guild_id: int,
                          tier: str) -> AssetCheck:
        """Screen one avatar / banner asset. Never raises."""
        key = str(asset.key)
        if self._negative.get(key, 0.0) > time.monotonic():
            return AssetCheck(key=key, kind=kind, url=_url(asset))
        pending = self._flights.get(key)
        if pending is not None:
            return await pending
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._flights[key] = future
        result = AssetCheck(key=key, kind=kind, url=_url(asset))
        try:
            result = await self._check_asset(asset, key, kind, guild_id, tier)
            return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("automod profile asset check failed (%s): %s", key, exc)
            return result
        finally:
            self._flights.pop(key, None)
            if not result.checked:
                self._negative[key] = time.monotonic() + _NEGATIVE_TTL
                if len(self._negative) > 5000:
                    now = time.monotonic()
                    self._negative = {k: v for k, v in self._negative.items() if v > now}
            if not future.done():
                future.set_result(result)

    async def _check_asset(self, asset: discord.Asset, key: str, kind: str,
                           guild_id: int, tier: str) -> AssetCheck:
        images = self._images()
        if images is None:
            return AssetCheck(key=key, kind=kind, url=_url(asset))
        size = ac.PROFILE_BANNER_SIZE if kind == KIND_BANNER else ac.PROFILE_AVATAR_SIZE
        out = AssetCheck(key=key, kind=kind, url=_url(asset))

        rec = await self._load_record(key)
        img: Optional[PreparedImage] = None
        if rec is not None:
            self.stat("cache_hit")
        else:
            img = await images.prepare_asset(asset, key, size=size)
            if img is None:
                return out
            rec = {"p": img.phash_hex, "d": img.dhash_hex, "ss": None}
        out.phash, out.dhash, out.img = rec["p"], rec["d"], img

        # The team hash index is consulted on every pass (labels change it).
        index = await images.ensure_index()
        match = index.match(from_hex(rec["p"]), from_hex(rec["d"]))
        if match is not None and match.entry.kind in _PROFILE_HASH_KINDS:
            out.hash_verdict = match.entry.verdict
            out.hash_kind = match.entry.kind
            out.hash_match = match.as_dict()
            out.checked = True
            await self._store_record(key, rec)
            return out

        likelihoods = rec.get("ss")
        if likelihoods is None:
            # Same picture seen as a message image: its SafeSearch result is shared.
            likelihoods = await images.safesearch_cached_hex(rec["p"])
        if likelihoods is None:
            if img is None:
                img = await images.prepare_asset(asset, key, size=size)
                out.img = img
            if img is None:
                return out
            likelihoods = await images.safesearch(
                img, guild_id=guild_id, tier=tier, profile=True)
            if likelihoods is None:
                self.stat("skip_budget")
                logger.debug("automod profile SafeSearch refused for %s (guild %s)",
                             key, guild_id)
                return out
        rec["ss"] = dict(likelihoods)
        await self._store_record(key, rec)
        out.likelihoods = dict(likelihoods)
        out.checked = True
        self.stat("screened")
        return out

    async def prepared_for_card(self, check: AssetCheck, asset: discord.Asset
                                ) -> Optional[PreparedImage]:
        """The downscaled image behind a flagged asset (alert / team cards)."""
        if check.img is not None:
            return check.img
        images = self._images()
        if images is None:
            return None
        if check.phash:
            cached = images.cached_by_phash(check.phash)
            if cached is not None:
                return cached
        size = ac.PROFILE_BANNER_SIZE if check.kind == KIND_BANNER else ac.PROFILE_AVATAR_SIZE
        return await images.prepare_asset(asset, check.key, size=size)

    # ------------------------------------------------------------------ #
    # Names
    # ------------------------------------------------------------------ #

    async def _load_name(self, key: str) -> Optional[dict]:
        rec = self._name_memo.get(key)
        if rec is not None:
            self._name_memo.move_to_end(key)
            return rec
        r = self._redis()
        if r is None:
            return None
        try:
            raw = await r.get(key)
            rec = json.loads(raw) if raw else None
        except Exception:  # noqa: BLE001
            return None
        if rec is not None:
            self._remember_name(key, rec)
        return rec

    def _remember_name(self, key: str, rec: dict) -> None:
        self._name_memo[key] = rec
        self._name_memo.move_to_end(key)
        while len(self._name_memo) > _MEMO_MAX:
            self._name_memo.popitem(last=False)

    async def _store_name(self, key: str, rec: dict) -> None:
        self._remember_name(key, rec)
        r = self._redis()
        if r is None:
            return
        try:
            await r.set(key, json.dumps(rec), ex=ac.PROFILE_PSEUDO_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            pass

    async def judge_name(self, name: str, *, user_id: int, guild_id: int,
                         guild_name: str, rules: str, severity: int,
                         response_language: str) -> NameJudgement:
        """Judge one name through the text funnel (cached per normalized name).

        A *sanctionable* verdict is trusted everywhere; a *clean* one is only
        reused by a guild whose severity is not stricter than the one it was
        judged with (a stricter guild may route the name that a lax one did not).
        The Decision is re-labelled ``profil_pseudo``; the funnel's own category
        is kept in ``categorie_origine``.
        """
        from automod import get_engine
        text = (name or "").strip()[:_NAME_MAX_CHARS]
        if len(normalize_name(text)) < 2:
            return NameJudgement(None, fresh=False)
        key = name_cache_key(text)
        rec = await self._load_name(key)
        if rec is not None and (rec.get("s") or int(rec.get("sev", 0)) >= int(severity)):
            self.stat("cache_hit")
            return NameJudgement(self._decision_from_record(rec, user_id, text),
                                 fresh=False)

        target = TargetMessage(
            id=synthetic_id(user_id, "pseudo", normalize_name(text)),
            author_id=str(user_id), content=text, origine=ORIGINE_PROFIL_PSEUDO)

        async def no_context(_n: int):
            return []

        engine = get_engine(self.bot)
        decision = await engine.analyze(
            target, guild_id=guild_id, guild_name=guild_name, rules=rules,
            author_history=AuthorHistory(), fetch_context=no_context,
            severity=severity, response_language=response_language,
        )
        self.stat("name_judged")
        decision = self._relabel(decision, user_id, text)
        store: dict = {"s": False, "sev": int(severity)}
        if decision is not None:
            store = {"s": bool(decision.sanctionnable), "sev": int(severity),
                     "d": _decision_dict(decision)}
        await self._store_name(key, store)
        return NameJudgement(decision, fresh=True)

    @staticmethod
    def _relabel(decision: Optional[Decision], user_id: int, text: str
                 ) -> Optional[Decision]:
        if decision is None:
            return None
        decision.message_id = synthetic_id(user_id, "pseudo", normalize_name(text))
        decision.auteur_id = str(user_id)
        decision.origine = ORIGINE_PROFIL_PSEUDO
        decision.contenu_juge = text
        if decision.sanctionnable and decision.categorie != "profil_pseudo":
            decision.categorie_origine = decision.categorie
            decision.categorie = "profil_pseudo"
        return decision

    def _decision_from_record(self, rec: dict, user_id: int, text: str
                              ) -> Optional[Decision]:
        data = rec.get("d")
        if not data:
            return None
        fields = {f.name for f in dataclasses.fields(Decision)}
        try:
            decision = Decision(**{k: v for k, v in data.items() if k in fields})
        except TypeError:
            return None
        return self._relabel(decision, user_id, text)


def _decision_dict(decision: Decision) -> dict:
    data = dataclasses.asdict(decision)
    data["image"] = None
    return data


def _url(asset: discord.Asset) -> str:
    try:
        return str(asset.url)
    except Exception:  # noqa: BLE001
        return ""
