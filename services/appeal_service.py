"""
Appeal service — opens and decides automod sanction appeals.

A sanctioned member appeals an automod sanction either to the **server** (its
moderators) or to the **Moddy team**. A reviewer's decision is **binding**:

* **accept**    → the sanction is revoked and the Discord action reversed
  (unban / timeout cleared).
* **refuse**    → the sanction stands.
* **transform** → the sanction is revoked and replaced by another action, which
  is applied on Discord.

Every step is mirrored to the case timeline (``case_events``), the reviewer
panel and the member's DM, and the server is always kept informed.

This service owns the *effects*; the UI lives in ``utils/appeal_views.py`` and
the persistence/state in ``db/repositories/appeals.py``.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional, Tuple

import discord

import config
from utils.i18n import t
from utils.moderation_cases import IssuerType, SanctionAction, EventType, AuthorType

logger = logging.getLogger("moddy.appeal_service")

# Mirror of the automod mute durations (by the sanction's gravity is unknown at
# transform time, so a transform→mute uses a sane default).
_TRANSFORM_MUTE_DURATION = timedelta(hours=1)

# Panels shown to moderators / Moddy staff are rendered in Moddy's primary
# language; the member DM uses the member's own locale.
_PANEL_LOCALE = "fr"


class AppealService:
    def __init__(self, bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    # ------------------------------------------------------------------ open
    async def open_appeal(
        self,
        *,
        case_id: str,
        sanction_id: str,
        subject_id: int,
        route: str,
        reason: str,
        dm_channel_id: Optional[int] = None,
        dm_message_id: Optional[int] = None,
        locale: str = "fr",
    ) -> Tuple[bool, str]:
        """Create + route an appeal. Returns ``(ok, status_or_error_key)``."""
        if not self.db:
            return False, "unavailable"

        case = await self.db.get_case_by_id(_as_uuid(case_id))
        if not case:
            return False, "not_found"
        case_row = case["case"]
        guild_id = int(case_row.get("scope_id") or 0)
        action = self._sanction_action(case, sanction_id)

        # One pending appeal per sanction.
        if await self.db.get_active_for_sanction(sanction_id):
            return False, "already"

        appeal = await self.db.create_appeal(
            case_id=_as_uuid(case_id),
            sanction_id=_as_uuid(sanction_id) if sanction_id else None,
            subject_id=subject_id,
            guild_id=guild_id,
            route="team" if route == "team" else "server",
            action=action,
            reason=reason,
        )
        await self.db.update_message_refs(
            appeal["id"], dm_channel_id=dm_channel_id, dm_message_id=dm_message_id,
        )

        # Timeline trace.
        await self._timeline(
            case_id, EventType.COMMENT.value, AuthorType.DISCORD_USER.value, subject_id,
            content=f"[Appel · {appeal['route']}] {reason}"[:1500],
            payload={"kind": "appeal_opened", "appeal_id": str(appeal["id"]), "route": appeal["route"]},
        )

        # Post the reviewer panel and remember its message.
        await self._post_review_panel(appeal, case, reason)
        # Keep the server informed + let the member track on the DM.
        await self._notify_server_opened(appeal, case)
        await self._refresh_dm(appeal, case, status=appeal["status"], locale=locale)
        return True, appeal["status"]

    # ---------------------------------------------------------------- decide
    async def decide(
        self,
        *,
        interaction: discord.Interaction,
        appeal_id: str,
        decision: str,
        by_id: int,
        new_action: Optional[str] = None,
        note: Optional[str] = None,
        duration_hours: Optional[int] = None,
        new_reason: Optional[str] = None,
    ) -> None:
        """Apply a binding decision on a pending appeal."""
        appeal = await self.db.get_appeal(appeal_id)
        if not appeal or appeal["status"] != "pending":
            await self._ephemeral(interaction, "handled", error=True)
            return

        status = {"accept": "accepted", "refuse": "refused", "transform": "transformed"}[decision]
        by_type = "moddy_staff" if appeal["route"] == "team" else "discord_user"

        updated = await self.db.set_decision(
            appeal_id, status=status, decided_by_type=by_type, decided_by_id=by_id,
            decision_note=note, new_action=new_action,
        )
        if not updated:
            await self._ephemeral(interaction, "handled", error=True)
            return

        guild = self.bot.get_guild(int(appeal["guild_id"]))
        subject_id = int(appeal["subject_id"])
        old_action = appeal.get("action")

        # --- Binding effects -------------------------------------------------
        try:
            if decision == "accept":
                await self._revoke_case_sanction(appeal, by_type, by_id)
                await self._reverse_discord(guild, subject_id, old_action)
            elif decision == "transform":
                await self._revoke_case_sanction(appeal, by_type, by_id)
                if new_reason:
                    await self.db.update_case_reason(_as_uuid(appeal["case_id"]), new_reason)
                await self._record_new_sanction(
                    appeal, new_action, by_type, by_id, note, duration_hours=duration_hours)
                await self._apply_discord(guild, subject_id, new_action,
                                          reason="[Automod · appeal] sanction modified",
                                          duration_hours=duration_hours)
            # refuse: nothing to change
        except Exception as e:
            logger.error("appeal %s effect failed: %s", appeal_id, e, exc_info=True)

        # Timeline trace.
        await self._timeline(
            appeal["case_id"], EventType.COMMENT.value, self._author_type(by_type), by_id,
            content=f"[Appel {status}] " + (note or ""),
            payload={"kind": "appeal_decision", "appeal_id": str(appeal_id),
                     "status": status, "new_action": new_action},
        )

        decided = {"status": status, "new_action": new_action, "by_id": by_id}
        # Update reviewer panel (the message the button lives on).
        await self._ack_review(interaction, appeal, decided)
        # Update the member DM + send them an outcome notice.
        await self._refresh_dm(appeal, None, status=status, locale=_PANEL_LOCALE, notify=True, decided=decided)
        # Inform the server.
        await self._notify_server_decided(appeal, decided)

    # ----------------------------------------------------------------- claim
    async def claim(self, *, interaction: discord.Interaction, appeal_id: str, by_id: int) -> None:
        """Assign a pending appeal to the acting reviewer + refresh the panel."""
        updated = await self.db.set_claim(appeal_id, claimed_by_id=by_id)
        if not updated:
            await self._ephemeral(interaction, "handled", error=False)
            return
        await self._rerender_pending_panel(interaction, updated)

    # ---------------------------------------------------------------- invite
    async def invite(self, *, interaction: discord.Interaction, appeal_id: str) -> None:
        """Hand the reviewer a server invite so they can join to investigate."""
        from utils.i18n import i18n as _i18n
        locale = _i18n.get_user_locale(interaction)
        appeal = await self.db.get_appeal(appeal_id)
        guild = self.bot.get_guild(int(appeal["guild_id"])) if appeal else None
        url = await self._make_invite(guild) if guild else None
        if not url:
            await self._ephemeral(interaction, "invite_failed", error=True)
            return
        from utils.components_v2 import create_info_message
        try:
            await interaction.response.send_message(
                view=create_info_message(
                    t("modules.automod.appeal.invite.title", locale=locale),
                    t("modules.automod.appeal.invite.body", locale=locale, url=url, guild=guild.name),
                ),
                ephemeral=True,
            )
        except discord.HTTPException:
            pass

    async def _make_invite(self, guild: discord.Guild) -> Optional[str]:
        me = guild.me
        candidates = [guild.rules_channel, guild.system_channel]
        candidates += [c for c in guild.text_channels]
        for ch in candidates:
            if ch is None:
                continue
            try:
                if not ch.permissions_for(me).create_instant_invite:
                    continue
                invite = await ch.create_invite(
                    max_age=86400, max_uses=1, unique=True,
                    reason="[Automod] appeal review — reviewer investigation",
                )
                return invite.url
            except (discord.Forbidden, discord.HTTPException):
                continue
        return None

    async def _rerender_pending_panel(self, interaction: discord.Interaction, appeal: dict):
        """Rebuild the (still-pending) reviewer panel in place after a claim."""
        from utils.appeal_views import build_review_view
        case = await self.db.get_case_by_id(_as_uuid(appeal["case_id"]))
        ctx = await self._review_context(appeal, case)
        view, _files = build_review_view(
            locale=_PANEL_LOCALE, route=appeal["route"], appeal_id=str(appeal["id"]),
            subject=ctx["subject"], guild=ctx["guild"], case=ctx["case"],
            appeal_reason=appeal.get("reason") or "",
            claimed_by=_int_or_none(appeal.get("claimed_by_id")),
            technical=ctx["technical"], proof=ctx["proof"],
        )
        try:
            if not interaction.response.is_done():
                await interaction.response.edit_message(view=view)
            else:
                await interaction.edit_original_response(view=view)
        except (discord.HTTPException, discord.InteractionResponded):
            await self._edit_stored(appeal.get("review_channel_id"),
                                    appeal.get("review_message_id"), view)

    async def _review_context(self, appeal: dict, case: Optional[dict]) -> dict:
        """Assemble the data blocks the reviewer panel renders."""
        subject_id = int(appeal["subject_id"])
        user = self.bot.get_user(subject_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(subject_id)
            except (discord.NotFound, discord.HTTPException):
                user = None
        subject = {
            "display": (user.display_name if user else None),
            "username": (user.name if user else None),
            "id": subject_id,
        }
        guild = self.bot.get_guild(int(appeal["guild_id"]))
        guild_block = {
            "name": guild.name if guild else None,
            "id": int(appeal["guild_id"]),
            "members": (guild.member_count if guild else None),
        }
        case_row = (case or {}).get("case", {})
        actions = sorted({s["action"] for s in (case or {}).get("sanctions", [])})
        created = case_row.get("created_at")
        case_block = {
            "ref": case_row.get("reference"),
            "actions": actions,
            "reason": case_row.get("reason"),
            "explication": self._explication(case),
            "created_ts": int(created.timestamp()) if created else None,
        }
        technical = {
            "case_uuid": str(appeal["case_id"]),
            "appeal_id": str(appeal["id"]),
        }
        return {"subject": subject, "guild": guild_block, "case": case_block,
                "technical": technical, "proof": self._proof(case)}

    # ============================================================ internals
    def _sanction_action(self, case: dict, sanction_id: str) -> Optional[str]:
        for s in case.get("sanctions", []):
            if str(s["id"]) == str(sanction_id):
                return s["action"]
        # Fall back to the most recent active sanction.
        actives = [s for s in case.get("sanctions", []) if s["status"] == "active"]
        return actives[-1]["action"] if actives else None

    async def _revoke_case_sanction(self, appeal: dict, by_type: str, by_id: int):
        action = appeal.get("action")
        if not action:
            return
        await self.bot.cases.revoke_sanction(
            "guild", subject_id=appeal["subject_id"], action=action,
            scope_id=appeal["guild_id"], by_type=by_type, by_id=by_id,
        )

    def _transform_expiry(self, action: str, duration_hours: Optional[int]):
        """Expiry for a transformed sanction: explicit hours, else a mute default."""
        from datetime import datetime, timedelta, timezone
        if duration_hours and duration_hours > 0:
            hrs = min(int(duration_hours), 24 * 28)
            return datetime.now(timezone.utc) + timedelta(hours=hrs)
        if action == "mute":
            return datetime.now(timezone.utc) + _TRANSFORM_MUTE_DURATION
        return None

    async def _record_new_sanction(self, appeal: dict, action: str, by_type: str,
                                   by_id: int, note: Optional[str],
                                   duration_hours: Optional[int] = None):
        expires = self._transform_expiry(action, duration_hours)
        await self.bot.cases.record_sanction(
            "guild", subject_id=appeal["subject_id"], action=action,
            reason="[Automod · appeal] " + (note or "sanction modified"),
            issuer_type=by_type, issuer_id=by_id, scope_id=appeal["guild_id"],
            expires_at=expires, note="Appeal — modification",
        )

    async def _reverse_discord(self, guild: Optional[discord.Guild], subject_id: int, action: Optional[str]):
        if guild is None or not action:
            return
        try:
            if action == "ban":
                await guild.unban(discord.Object(id=subject_id), reason="[Automod] appel accepté")
            elif action == "mute":
                member = guild.get_member(subject_id)
                if member is not None:
                    await member.timeout(None, reason="[Automod] appel accepté")
        except (discord.Forbidden, discord.HTTPException, discord.NotFound) as e:
            logger.warning("appeal reverse (%s) failed in guild %s: %s", action, guild.id, e)

    async def _apply_discord(self, guild: Optional[discord.Guild], subject_id: int,
                             action: Optional[str], reason: str,
                             duration_hours: Optional[int] = None):
        if guild is None or not action:
            return
        from datetime import timedelta
        me = guild.me
        member = guild.get_member(subject_id)
        mute_for = (min(timedelta(hours=int(duration_hours)), timedelta(days=28))
                    if duration_hours and duration_hours > 0 else _TRANSFORM_MUTE_DURATION)
        try:
            if action == "ban" and me.guild_permissions.ban_members:
                await guild.ban(discord.Object(id=subject_id), reason=reason, delete_message_days=0)
            elif action == "kick" and member is not None and me.guild_permissions.kick_members:
                await member.kick(reason=reason)
            elif action == "mute" and member is not None and me.guild_permissions.moderate_members:
                await member.timeout(mute_for, reason=reason)
            # warn: no Discord action
        except (discord.Forbidden, discord.HTTPException, discord.NotFound) as e:
            logger.warning("appeal apply (%s) failed in guild %s: %s", action, guild.id, e)

    async def _timeline(self, case_id, event_type, author_type, author_id, *, content, payload):
        try:
            await self.db.add_event(
                _as_uuid(case_id), event_type, author_type=author_type,
                author_id=author_id, content=content, payload=payload,
            )
        except Exception as e:
            logger.error("appeal timeline write failed: %s", e)

    @staticmethod
    def _author_type(by_type: str) -> str:
        return "moddy_staff" if by_type == "moddy_staff" else "discord_user"

    # -- panel posting / editing -----------------------------------------
    async def _post_review_panel(self, appeal: dict, case: dict, reason: str):
        from utils.appeal_views import build_review_view
        channel = await self._review_channel(appeal)
        if channel is None:
            return
        ctx = await self._review_context(appeal, case)
        view, files = build_review_view(
            locale=_PANEL_LOCALE, route=appeal["route"], appeal_id=str(appeal["id"]),
            subject=ctx["subject"], guild=ctx["guild"], case=ctx["case"],
            appeal_reason=reason, claimed_by=_int_or_none(appeal.get("claimed_by_id")),
            technical=ctx["technical"], proof=ctx["proof"],
        )
        # A server-routed appeal is reviewed in the alert channel → reply to the
        # original automod log message so the thread of events stays linked.
        reference = self._reply_reference(channel, case) if appeal["route"] != "team" else None
        try:
            msg = await channel.send(view=view, files=files, reference=reference)
            await self.db.update_message_refs(
                appeal["id"], review_channel_id=channel.id, review_message_id=msg.id,
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning("appeal review panel send failed: %s", e)

    async def _review_channel(self, appeal: dict) -> Optional[discord.abc.Messageable]:
        if appeal["route"] == "team":
            return self.bot.get_channel(config.MODDY_APPEAL_CHANNEL_ID)
        # server route → the guild's automod alert channel
        cid = await self._server_alert_channel_id(int(appeal["guild_id"]))
        return self.bot.get_channel(cid) if cid else None

    async def _server_alert_channel_id(self, guild_id: int) -> Optional[int]:
        try:
            cfg = await self.bot.module_manager.get_module_config(guild_id, "automod") or {}
            return cfg.get("notify_channel_id") or cfg.get("log_channel_id")
        except Exception:
            return None

    async def _ack_review(self, interaction: discord.Interaction, appeal: dict, decided: dict):
        from utils.appeal_views import build_review_view
        case = await self.db.get_case_by_id(_as_uuid(appeal["case_id"]))
        ctx = await self._review_context(appeal, case)
        view, _files = build_review_view(
            locale=_PANEL_LOCALE, route=appeal["route"], appeal_id=str(appeal["id"]),
            subject=ctx["subject"], guild=ctx["guild"], case=ctx["case"],
            appeal_reason=appeal.get("reason") or "",
            claimed_by=_int_or_none(appeal.get("claimed_by_id")),
            technical=ctx["technical"], proof=ctx["proof"], decided=decided,
        )
        # The decision can come from the panel button (Decline), an ephemeral
        # choice button (Accept → full) or a modal (Accept → modify). In every
        # case the *stored* panel message is the one to finalize — editing the
        # triggering interaction's message would hit the wrong (ephemeral) one.
        await self._edit_stored(
            appeal.get("review_channel_id"), appeal.get("review_message_id"), view)

        from utils.components_v2 import create_success_message
        from utils.i18n import i18n as _i18n
        loc = _i18n.get_user_locale(interaction)
        key = f"modules.automod.appeal.status.{decided['status']}"
        ack = create_success_message(
            t("modules.automod.appeal.review.outcome", locale=loc),
            t(key, locale=loc))
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(view=ack, ephemeral=True)
            else:
                await interaction.followup.send(view=ack, ephemeral=True)
        except discord.HTTPException:
            pass

    async def _refresh_dm(self, appeal: dict, case: Optional[dict], *, status: str,
                          locale: str, notify: bool = False, decided: Optional[dict] = None):
        from utils.appeal_views import build_sanction_dm_view
        ch_id = appeal.get("dm_channel_id")
        msg_id = appeal.get("dm_message_id")
        if case is None:
            case = await self.db.get_case_by_id(_as_uuid(appeal["case_id"]))
        ref = case["case"]["reference"] if case else "—"
        reason = (case["case"].get("reason") if case else "") or ""
        proof = self._proof(case)
        # The member DM is rendered in the member's (guild) language.
        dm_locale = self._dm_locale(appeal)
        decided_full = dict(decided) if decided else None
        if decided_full is not None:
            decided_full.setdefault("route", appeal["route"])
        view, _files = build_sanction_dm_view(
            locale=dm_locale, guild_name=self._guild_name(appeal),
            guild_id=int(appeal["guild_id"]), case_ref=ref,
            action=appeal.get("action") or "warn", reason=reason,
            explication=self._explication(case) if case else "",
            case_id=str(appeal["case_id"]), sanction_id=str(appeal.get("sanction_id") or ""),
            expires_at=self._sanction_expiry(case, appeal.get("sanction_id")),
            proof_text=proof.get("text"), proof_author=proof.get("author"),
            proof_message_id=proof.get("message_id"), proof_ts=proof.get("ts"),
            appeal_status=status, appeal_route=appeal["route"], decided=decided_full,
        )
        # Editing keeps the original attachment (deterministic filename), so we
        # don't need to re-upload the proof file on a status refresh.
        await self._edit_stored(ch_id, msg_id, view)
        if notify and decided:
            await self._dm_outcome(int(appeal["subject_id"]), appeal, decided, dm_locale)

    async def _dm_outcome(self, user_id: int, appeal: dict, decided: dict, locale: str):
        from utils.components_v2 import create_info_message
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            key = f"modules.automod.appeal.status.{decided['status']}"
            body = t("modules.automod.appeal.dm_outcome", locale=locale,
                     status=t(key, locale=locale), guild=self._guild_name(appeal))
            await user.send(view=create_info_message(
                t("modules.automod.appeal.dm_outcome_title", locale=locale), body))
        except (discord.Forbidden, discord.HTTPException):
            pass

    # -- server notifications --------------------------------------------
    async def _notify_server_opened(self, appeal: dict, case: dict):
        # For a team-routed appeal, tell the server an appeal was opened. For a
        # server-routed appeal the review panel already lives in that channel.
        if appeal["route"] != "team":
            return
        cid = await self._server_alert_channel_id(int(appeal["guild_id"]))
        channel = self.bot.get_channel(cid) if cid else None
        if channel is None:
            return
        from utils.components_v2 import create_info_message
        view = create_info_message(
            t("modules.automod.appeal.server_opened_title", locale=_PANEL_LOCALE),
            t("modules.automod.appeal.server_opened", locale=_PANEL_LOCALE,
              user=f"<@{appeal['subject_id']}>", case=f"`{case['case']['reference']}`"),
        )
        try:
            await channel.send(view=view, reference=self._reply_reference(channel, case))
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _notify_server_decided(self, appeal: dict, decided: dict):
        cid = await self._server_alert_channel_id(int(appeal["guild_id"]))
        channel = self.bot.get_channel(cid) if cid else None
        if channel is None:
            return
        from utils.components_v2 import create_info_message
        key = f"modules.automod.appeal.status.{decided['status']}"
        extra = ""
        if decided.get("new_action"):
            extra = " → `" + t("modules.automod.action." + decided["new_action"], locale=_PANEL_LOCALE) + "`"
        view = create_info_message(
            t("modules.automod.appeal.server_decided_title", locale=_PANEL_LOCALE),
            t("modules.automod.appeal.server_decided", locale=_PANEL_LOCALE,
              user=f"<@{appeal['subject_id']}>", route=appeal["route"],
              status=t(key, locale=_PANEL_LOCALE)) + extra,
        )
        case = await self.db.get_case_by_id(_as_uuid(appeal["case_id"]))
        try:
            await channel.send(view=view, reference=self._reply_reference(channel, case))
        except (discord.Forbidden, discord.HTTPException):
            pass

    # -- small utilities --------------------------------------------------
    async def _edit_stored(self, channel_id, message_id, view):
        if not channel_id or not message_id:
            return
        try:
            channel = self.bot.get_channel(int(channel_id)) or await self.bot.fetch_channel(int(channel_id))
            msg = await channel.fetch_message(int(message_id))
            await msg.edit(view=view)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            logger.debug("appeal: could not edit stored message: %s", e)

    async def _ephemeral(self, interaction: discord.Interaction, key: str, error: bool = False):
        from utils.components_v2 import create_error_message, create_warning_message
        from utils.i18n import i18n as _i18n
        locale = _i18n.get_user_locale(interaction)
        maker = create_error_message if error else create_warning_message
        view = maker(
            t("modules.automod.appeal.error.title", locale=locale),
            t(f"modules.automod.appeal.error.{key}", locale=locale),
        )
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(view=view, ephemeral=True)
            else:
                await interaction.followup.send(view=view, ephemeral=True)
        except discord.HTTPException:
            pass

    def _guild_name(self, appeal: dict) -> str:
        guild = self.bot.get_guild(int(appeal["guild_id"]))
        return guild.name if guild else str(appeal["guild_id"])

    def _dm_locale(self, appeal: dict) -> str:
        """Member-facing DM locale: the guild's language (Community) or English."""
        guild = self.bot.get_guild(int(appeal["guild_id"]))
        try:
            features = set(getattr(guild, "features", []) or [])
            if "COMMUNITY" not in features:
                return "en-US"
            pref = str(getattr(guild, "preferred_locale", "") or "")
        except Exception:
            return "en-US"
        return "fr" if pref.lower().startswith("fr") else "en-US"

    @staticmethod
    def _explication(case: Optional[dict]) -> str:
        if not case:
            return ""
        for ev in reversed(case.get("events", [])):
            if ev.get("type") == "evidence" and (ev.get("payload") or {}).get("explication"):
                return ev["payload"]["explication"]
        return ""

    @staticmethod
    def _proof(case: Optional[dict]) -> dict:
        """The offending-message proof for the DM, from the automod evidence."""
        if not case:
            return {}
        for ev in reversed(case.get("events", [])):
            p = ev.get("payload") or {}
            if ev.get("type") == "evidence" and p.get("source") == "automod":
                return {
                    "text": p.get("extrait") or None,
                    "author": p.get("author_name"),
                    "message_id": str(p["message_id"]) if p.get("message_id") else None,
                    "ts": p.get("ts"),
                }
        return {}

    @staticmethod
    def _sanction_expiry(case: Optional[dict], sanction_id):
        """The ``expires_at`` of the appealed sanction (or None)."""
        if not case or not sanction_id:
            return None
        for s in case.get("sanctions", []):
            if str(s.get("id")) == str(sanction_id):
                return s.get("expires_at")
        return None

    def _log_ref(self, case: Optional[dict]):
        """The (channel_id, message_id) of the original automod log, if stored."""
        if not case:
            return None, None
        for ev in reversed(case.get("events", [])):
            p = ev.get("payload") or {}
            if p.get("kind") == "automod_log" and p.get("message_id"):
                return p.get("channel_id"), p.get("message_id")
        return None, None

    def _reply_reference(self, channel, case: Optional[dict]):
        """A MessageReference to the original automod log (same channel), or None.

        Lets appeal updates posted in the alert channel reply to the original
        automod decision so the whole story is one linked thread.
        """
        ch_id, msg_id = self._log_ref(case)
        if not msg_id or not channel or str(ch_id) != str(getattr(channel, "id", "")):
            return None
        try:
            return discord.MessageReference(
                message_id=int(msg_id), channel_id=int(channel.id),
                fail_if_not_exists=False,
            )
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _evidence(case: Optional[dict]) -> str:
        if not case:
            return ""
        for ev in reversed(case.get("events", [])):
            if ev.get("type") == "evidence" and (ev.get("payload") or {}).get("source") == "automod":
                return (ev["payload"].get("extrait") or ev.get("content") or "")[:900]
        return ""


def _as_uuid(value):
    import uuid
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _int_or_none(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
