"""
Moddy team labeling queue for the automod (docs/AUTOMOD_AI.md §9).

Lives on ``bot.automod_labels``. Every automod **sanction** and every
**doubtful** decision (text or image) is copied to a Moddy team channel, where
humans label it *sanctionnable* / *non sanctionnable* / *ignore*. The label
grows the bot's knowledge:

=====================  ==============================================  =========================================
item                   sanctionnable                                    non sanctionnable
=====================  ==============================================  =========================================
image_scam             hash → ``block`` + OCR text → ``arnaque_scam``   hash → ``allow``
                       embedding reference
image_nsfw             hash → ``block``                                 hash → ``allow``
texte                  embedding reference (chosen category) +          server precedent ``non_sanctionnable`` +
                       optional blocklist terms (Modal)                 eval candidate ``faux_positif``
=====================  ==============================================  =========================================

The label **never applies a sanction** — the queue is a side channel. The only
enforcement it can trigger is the reverse: "non sanctionnable" on a decision
the bot actually sanctioned **revokes** that sanction (case + Discord side),
tells the server's alert channel and DMs the member. A deleted message cannot
be restored.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import discord

import config
from automod import constants as ac
from automod.image_hash import KIND_NSFW, KIND_SCAM, VERDICT_ALLOW, VERDICT_BLOCK, from_hex
from automod.normalize import collapse_repeats
from automod.schemas import (
    ORIGINE_IMAGE_HASH, ORIGINE_IMAGE_NSFW, ORIGINE_IMAGE_OCR,
)

logger = logging.getLogger("moddy.services.automod_labels")

KIND_TEXTE = "texte"
KIND_IMAGE_SCAM = "image_scam"
KIND_IMAGE_NSFW = "image_nsfw"

#: Team panels are always English (like appeals and support requests).
PANEL_LOCALE = "en-US"

#: A learned reference this close (cosine) to an existing one adds nothing.
LEARNED_DEDUP_SIMILARITY = 0.97


def label_kind(decision) -> str:
    """Which queue lane a decision belongs to."""
    origine = getattr(decision, "origine", "texte")
    if origine == ORIGINE_IMAGE_NSFW:
        return KIND_IMAGE_NSFW
    if origine == ORIGINE_IMAGE_OCR:
        return KIND_IMAGE_SCAM
    if origine == ORIGINE_IMAGE_HASH:
        return KIND_IMAGE_NSFW if decision.categorie == "contenu_nsfw" else KIND_IMAGE_SCAM
    return KIND_TEXTE


def dedup_key(kind: str, *, phash: str = "", text: str = "") -> str:
    """Same image (hash) or same text (collapsed) → same queue item."""
    basis = phash if kind != KIND_TEXTE and phash else (collapse_repeats(text) or text).lower()
    return hashlib.sha256(f"{kind}\x00{basis}".encode("utf-8")).hexdigest()


def build_details(decision, *, motif: str, bareme=None, applied: List[str],
                  guild_name: str = "", case_id=None, message=None) -> Dict[str, Any]:
    """The JSON snapshot stored on the item (what the card renders)."""
    details: Dict[str, Any] = {
        "guild_name": guild_name,
        "origine": getattr(decision, "origine", "texte"),
        "doute": getattr(decision, "doute", None),
        "sanctionnable": bool(decision.sanctionnable),
        "categorie": decision.categorie,
        "gravite": decision.gravite,
        "confiance": decision.confiance,
        "raison": decision.raison,
        "explication": decision.explication,
        "citation": decision.citation,
        "signal_source": decision.signal_source,
        "score": round(float(decision.score_detecteur or 0.0), 4),
        "decideur": getattr(decision, "decideur", "nano"),
        "rejet_grounding": getattr(decision, "rejet_grounding", None),
        "actions": list(decision.actions or []),
        "applied": list(applied or []),
        "jump_url": getattr(message, "jump_url", "") if message is not None else "",
        "sanctions": [],
    }
    if bareme is not None:
        details["cran"] = bareme.cran
    image = getattr(decision, "image", None)
    if image is not None:
        details["image"] = dataclasses.asdict(image)
    if motif == "sanction" and case_id is not None and message is not None:
        details["sanctions"] = [{
            "guild_id": message.guild.id if message.guild else None,
            "case_id": str(case_id),
            "author_id": int(decision.auteur_id),
            "message_id": int(decision.message_id),
        }]
    return details


class AutomodLabelService:
    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------------ #
    # Enqueue (called by modules/automod_ai.py)
    # ------------------------------------------------------------------ #

    @property
    def channel_id(self) -> int:
        return int(getattr(config, "MODDY_AUTOMOD_LABEL_CHANNEL_ID", 0) or 0)

    def _channel(self) -> Optional[discord.abc.Messageable]:
        if not self.channel_id:
            return None
        return self.bot.get_channel(self.channel_id)

    async def _hourly_cap_allows(self) -> bool:
        cap = int(getattr(config, "MODDY_AUTOMOD_LABEL_HOURLY_CAP", 0) or 0)
        redis = getattr(self.bot, "redis", None)
        if cap <= 0 or redis is None:
            return True
        key = "automod:label:hour:" + datetime.now(timezone.utc).strftime("%Y%m%d%H")
        try:
            pipe = redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, 7200)
            count, _ = await pipe.execute()
            return int(count) <= cap
        except Exception:  # noqa: BLE001
            return True

    async def enqueue(self, *, message: discord.Message, decision, motif: str,
                      bareme=None, case_id=None, applied: Optional[List[str]] = None,
                      judged_text: str = "") -> Optional[str]:
        """Copy one decision to the team queue. Returns the item id (or None)."""
        db = getattr(self.bot, "db", None)
        channel = self._channel()
        if db is None or channel is None:
            return None
        kind = label_kind(decision)
        image = getattr(decision, "image", None)
        phash = image.phash if image is not None else ""
        key = dedup_key(kind, phash=phash, text=judged_text)
        details = build_details(
            decision, motif=motif, bareme=bareme, applied=applied or [],
            guild_name=message.guild.name if message.guild else "",
            case_id=case_id, message=message,
        )

        # A duplicate still waiting for a label: count it (and remember its
        # sanction, so one label can revoke them all) instead of a new card.
        existing = await db.find_recent_label_item(key)
        if existing is not None:
            sanction = details["sanctions"][0] if details["sanctions"] else None
            row = await db.bump_label_item(existing["id"], sanction)
            if row is not None:
                await self.refresh_card(row)
            return existing["id"]

        if not await self._hourly_cap_allows():
            logger.info("automod labeling queue: hourly cap reached, item dropped")
            return None

        item_id = await db.create_label_item(
            kind=kind, motif=motif,
            guild_id=message.guild.id if message.guild else 0,
            channel_id=message.channel.id, message_id=int(decision.message_id),
            author_id=int(decision.auteur_id), contenu=judged_text,
            phash=_signed_or_none(image.phash) if image is not None and image.phash else None,
            dhash=_signed_or_none(image.dhash) if image is not None and image.dhash else None,
            dedup_key=key, details=details, case_id=case_id,
        )
        if item_id is None:
            return None
        row = await db.get_label_item(item_id)
        if row is None:
            return item_id
        await self._post_card(channel, row)
        return item_id

    # ------------------------------------------------------------------ #
    # Cards
    # ------------------------------------------------------------------ #

    def _image_file(self, row: Dict[str, Any]) -> Optional[discord.File]:
        image = (row.get("details") or {}).get("image") or {}
        phash = image.get("phash")
        svc = getattr(self.bot, "automod_images", None)
        if not phash or svc is None:
            return None
        img = svc.cached_by_phash(phash)
        if img is None:
            return None
        # Always spoilered on the team card — NSFW above all, but a scam image
        # is no pleasure to scroll past either.
        return discord.File(io.BytesIO(img.data), filename=f"image_{phash}.jpg", spoiler=True)

    async def _post_card(self, channel, row: Dict[str, Any]) -> None:
        from utils.automod_label_views import render_label_card
        file = self._image_file(row)
        view = render_label_card(row, image_filename=file.filename if file else None)
        try:
            msg = await channel.send(
                view=view, files=[file] if file else [],
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.warning("automod labeling card could not be posted: %s", exc)
            return
        await self.bot.db.set_label_item_card(row["id"], msg.channel.id, msg.id)

    async def refresh_card(self, row: Dict[str, Any]) -> None:
        """Re-render the stored card in place (occurrences, label, revocation)."""
        from utils.automod_label_views import render_label_card
        ch_id, msg_id = row.get("card_channel_id"), row.get("card_message_id")
        if not ch_id or not msg_id:
            return
        channel = self.bot.get_channel(int(ch_id))
        if channel is None:
            return
        image = ((row.get("details") or {}).get("image") or {}).get("phash")
        filename = f"image_{image}.jpg" if image else None
        try:
            await channel.get_partial_message(int(msg_id)).edit(
                view=render_label_card(row, image_filename=filename))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            logger.debug("automod labeling card refresh failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Labeling
    # ------------------------------------------------------------------ #

    async def label(self, item_id: str, *, verdict: str, labeler_id: int,
                    categorie: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Record a label and apply its effects. Returns the updated row with an
        ``outcome`` dict, or None when the item is gone / already labeled."""
        db = self.bot.db
        row = await db.label_item(item_id, verdict=verdict, labeled_by=labeler_id,
                                  categorie=categorie)
        if row is None:
            return None
        outcome: Dict[str, Any] = {}
        try:
            if verdict == "sanctionnable":
                outcome = await self._learn_positive(row, labeler_id)
            elif verdict == "non_sanctionnable":
                outcome = await self._learn_negative(row, labeler_id)
        except Exception as exc:  # noqa: BLE001 — the label itself is recorded
            logger.error("automod labeling effects failed for %s: %s", item_id, exc,
                         exc_info=True)
            outcome["error"] = True
        stats = getattr(self.bot, "stats", None)
        if stats is not None:
            stats.incr("automod.label", dims={"verdict": verdict, "kind": row["kind"]})
        row = await db.get_label_item(item_id) or row
        row["outcome"] = outcome
        return row

    def _category_of(self, row: Dict[str, Any]) -> str:
        details = row.get("details") or {}
        if row["kind"] == KIND_IMAGE_SCAM:
            return row.get("categorie_humaine") or "arnaque_scam"
        if row["kind"] == KIND_IMAGE_NSFW:
            return "contenu_nsfw"
        return row.get("categorie_humaine") or details.get("categorie") or ""

    async def _learn_positive(self, row: Dict[str, Any], by: int) -> Dict[str, Any]:
        outcome: Dict[str, Any] = {}
        svc = getattr(self.bot, "automod_images", None)
        image = (row.get("details") or {}).get("image") or {}
        if row["kind"] in (KIND_IMAGE_SCAM, KIND_IMAGE_NSFW) and image.get("phash") and svc:
            kind = KIND_SCAM if row["kind"] == KIND_IMAGE_SCAM else KIND_NSFW
            await svc.add_hash(from_hex(image["phash"]), from_hex(image["dhash"]),
                               kind=kind, verdict=VERDICT_BLOCK,
                               label_item_id=row["id"], added_by=by)
            outcome["hash"] = VERDICT_BLOCK
        categorie = self._category_of(row)
        text = (row.get("contenu") or "").strip()
        if row["kind"] != KIND_IMAGE_NSFW and text and categorie:
            outcome["reference"] = await self.learn_reference(
                text, categorie, label_item_id=row["id"], added_by=by)
        await self._eval_candidate(row, "correct", by)
        return outcome

    async def _learn_negative(self, row: Dict[str, Any], by: int) -> Dict[str, Any]:
        outcome: Dict[str, Any] = {}
        svc = getattr(self.bot, "automod_images", None)
        image = (row.get("details") or {}).get("image") or {}
        if row["kind"] in (KIND_IMAGE_SCAM, KIND_IMAGE_NSFW) and image.get("phash") and svc:
            kind = KIND_SCAM if row["kind"] == KIND_IMAGE_SCAM else KIND_NSFW
            await svc.add_hash(from_hex(image["phash"]), from_hex(image["dhash"]),
                               kind=kind, verdict=VERDICT_ALLOW,
                               label_item_id=row["id"], added_by=by)
            outcome["hash"] = VERDICT_ALLOW
        text = (row.get("contenu") or "").strip()
        if row["kind"] == KIND_TEXTE and text:
            precedents = getattr(self.bot, "precedents", None)
            if precedents is not None:
                try:
                    await precedents.record(
                        int(row["guild_id"]), text, "non_sanctionnable",
                        source="equipe_moddy",
                        categorie=(row.get("details") or {}).get("categorie") or "",
                        gravite=(row.get("details") or {}).get("gravite") or "",
                    )
                    outcome["precedent"] = True
                except Exception as exc:  # noqa: BLE001
                    logger.debug("automod labeling precedent failed: %s", exc)
        await self._eval_candidate(row, "faux_positif", by)
        revoked = await self.revoke_bot_sanctions(row, by)
        if revoked:
            outcome["revoked"] = revoked
        return outcome

    async def _eval_candidate(self, row: Dict[str, Any], verdict: str, by: int) -> None:
        """Feed the offline golden-set corpus (``make eval-import``)."""
        if row["kind"] == KIND_IMAGE_NSFW or not (row.get("contenu") or "").strip():
            return
        db = self.bot.db
        details = row.get("details") or {}
        try:
            cid = await db.create_eval_candidate(
                guild_id=row["guild_id"], source="equipe_moddy",
                contenu=row.get("contenu") or "",
                verdict={k: details.get(k) for k in (
                    "sanctionnable", "categorie", "gravite", "confiance", "citation",
                    "raison", "signal_source", "origine")},
                channel_id=row.get("channel_id"), message_id=row.get("message_id"),
                author_id=row.get("author_id"),
            )
            if cid:
                await db.annotate_eval_candidate(cid, verdict, annotated_by=by)
        except Exception as exc:  # noqa: BLE001
            logger.debug("automod labeling eval candidate failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Learned references / terms
    # ------------------------------------------------------------------ #

    def _engine(self):
        from automod import get_engine
        return get_engine(self.bot)

    async def learn_reference(self, text: str, categorie: str, *,
                              label_item_id: Optional[str] = None,
                              added_by: Optional[int] = None) -> str:
        """Embed ``text`` and add it as a learned reference ("added" /
        "duplicate" / "unavailable")."""
        engine = self._engine()
        vector = await engine.embeddings.embed_query(text[:ac.PREFILTRE_MAX_CHARS])
        if vector is None:
            return "unavailable"
        if engine.embeddings.max_similarity_to_learned(vector) >= LEARNED_DEDUP_SIMILARITY:
            return "duplicate"
        await self.bot.db.add_learned_reference(
            categorie=categorie, texte=text, vector=vector,
            label_item_id=label_item_id, added_by=added_by)
        engine.embeddings.add_learned(vector, categorie)
        return "added"

    async def add_terms(self, item_id: Optional[str], terms: List[str], categorie: str,
                        mode: str, added_by: int) -> int:
        """Store blocklist terms typed by the team and reload the blocklist."""
        added = 0
        for term in terms:
            term = term.strip()
            if len(term) < 2:
                continue
            await self.bot.db.add_learned_term(
                terme=term, categorie=categorie, mode=mode,
                label_item_id=item_id, added_by=added_by)
            added += 1
        if added:
            await self.reload_terms()
        return added

    async def reload_terms(self) -> None:
        rows = await self.bot.db.list_learned_terms()
        self._engine().blocklist.reload(rows)

    async def load_learned(self) -> None:
        """Startup: learned references + terms into the shared engine."""
        db = getattr(self.bot, "db", None)
        if db is None:
            return
        try:
            refs = await db.list_learned_references()
            self._engine().embeddings.set_learned(
                [r["vector"] for r in refs], [r["categorie"] for r in refs])
            await self.reload_terms()
            svc = getattr(self.bot, "automod_images", None)
            if svc is not None:
                await svc.ensure_index(force=True)
            logger.info("automod: loaded %d learned references", len(refs))
        except Exception as exc:  # noqa: BLE001
            logger.warning("automod: learned data load failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Revocation (team says "not sanctionable" on a bot sanction)
    # ------------------------------------------------------------------ #

    async def revoke_bot_sanctions(self, row: Dict[str, Any], by: int) -> int:
        """Revoke every active sanction the bot applied for this item."""
        sanctions = (row.get("details") or {}).get("sanctions") or []
        if not sanctions or row.get("revoked"):
            return 0
        from utils.moderation_cases import AuthorType, EventType, IssuerType
        from utils.sanction_reversal import reverse_discord_sanction
        db = self.bot.db
        total = 0
        for entry in sanctions:
            case_id = entry.get("case_id")
            if not case_id:
                continue
            try:
                import uuid as _uuid
                case = await db.get_case_by_id(_uuid.UUID(str(case_id)))
            except Exception:  # noqa: BLE001
                case = None
            if not case:
                continue
            guild = self.bot.get_guild(int(entry.get("guild_id") or 0))
            lifted: List[str] = []
            for sanction in case.get("sanctions") or []:
                if str(sanction.get("status")) != "active":
                    continue
                ok = await db.revoke_sanction(
                    sanction["id"], IssuerType.MODDY_STAFF.value, by)
                if not ok:
                    continue
                total += 1
                action = str(sanction.get("action") or "")
                lifted.append(action)
                if action in ("ban", "mute"):
                    await reverse_discord_sanction(
                        guild, int(entry.get("author_id") or 0), action,
                        reason="[Automod] révoqué par l'équipe Moddy")
            if not lifted:
                continue
            try:
                await db.add_event(
                    _uuid.UUID(str(case_id)), EventType.COMMENT.value,
                    author_type=AuthorType.SYSTEM.value,
                    content="Sanction révoquée : l'équipe Moddy a jugé ce contenu non sanctionnable "
                            "(file d'étiquetage de l'automod).",
                    payload={"kind": "automod_label_revoked", "label_item_id": row["id"]},
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("automod labeling timeline event failed: %s", exc)
            await self._notify_revocation(guild, entry, case, lifted)
        if total:
            await db.mark_label_item_revoked(row["id"])
        return total

    async def _notify_revocation(self, guild: Optional[discord.Guild], entry: dict,
                                 case: dict, lifted: List[str]) -> None:
        if guild is None:
            return
        from utils.guild_language import guild_locale
        from utils.i18n import t
        locale = await guild_locale(self.bot, guild)
        reference = (case.get("case") or {}).get("reference") or "—"
        author_id = int(entry.get("author_id") or 0)

        # 1. The server's automod alert channel.
        module = None
        manager = getattr(self.bot, "module_manager", None)
        if manager is not None:
            try:
                module = await manager.get_module_instance(guild.id, "automod_ai")
            except Exception:  # noqa: BLE001
                module = None
        channel_id = getattr(module, "notify_channel_id", None) if module else None
        channel = guild.get_channel(int(channel_id)) if channel_id else None
        if channel is not None and channel.permissions_for(guild.me).send_messages:
            from utils.automod_label_views import render_revocation_notice
            try:
                await channel.send(
                    view=render_revocation_notice(locale, author_id=author_id,
                                                  case_ref=reference),
                    allowed_mentions=discord.AllowedMentions.none())
            except (discord.Forbidden, discord.HTTPException):
                pass

        # 2. The member, through the notification system.
        try:
            user = self.bot.get_user(author_id) or await self.bot.fetch_user(author_id)
        except (discord.NotFound, discord.HTTPException):
            return
        from notifications.models import NotificationContent, NotificationSource
        from utils.emojis import DONE
        try:
            await self.bot.notifications.send_dm(
                user,
                content=NotificationContent(
                    title=t("modules.automod_ai.label_revoked.dm_title", locale=locale),
                    body=t("modules.automod_ai.label_revoked.dm_body", locale=locale,
                           guild="{guild}", case_ref="{case_ref}"),
                    icon=DONE,
                    accent_color=0x57F287,
                    footer="{case_ref}",
                    template_id="automod_ai.label_revoked",
                ),
                source=NotificationSource.service_guild("automod_ai", guild.id),
                variables={"guild": guild.name, "case_ref": reference},
                locale=locale,
            )
        except (discord.Forbidden, discord.HTTPException):
            pass


def _signed_or_none(hex_value: str) -> Optional[int]:
    from automod.image_hash import to_signed
    try:
        return to_signed(from_hex(hex_value))
    except (TypeError, ValueError):
        return None
