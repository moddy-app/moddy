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
                await self._record_new_sanction(appeal, new_action, by_type, by_id, note)
                await self._apply_discord(guild, subject_id, new_action,
                                          reason="[Automod · appel] transformation de sanction")
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

    async def _record_new_sanction(self, appeal: dict, action: str, by_type: str,
                                   by_id: int, note: Optional[str]):
        expires = None
        if action == "mute":
            from datetime import datetime, timezone
            expires = datetime.now(timezone.utc) + _TRANSFORM_MUTE_DURATION
        await self.bot.cases.record_sanction(
            "guild", subject_id=appeal["subject_id"], action=action,
            reason="[Automod · appel] " + (note or "sanction transformée"),
            issuer_type=by_type, issuer_id=by_id, scope_id=appeal["guild_id"],
            expires_at=expires, note="Appel — transformation",
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
                             action: Optional[str], reason: str):
        if guild is None or not action:
            return
        me = guild.me
        member = guild.get_member(subject_id)
        try:
            if action == "ban" and me.guild_permissions.ban_members:
                await guild.ban(discord.Object(id=subject_id), reason=reason, delete_message_days=0)
            elif action == "kick" and member is not None and me.guild_permissions.kick_members:
                await member.kick(reason=reason)
            elif action == "mute" and member is not None and me.guild_permissions.moderate_members:
                await member.timeout(_TRANSFORM_MUTE_DURATION, reason=reason)
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
        view = build_review_view(
            locale=_PANEL_LOCALE, route=appeal["route"], appeal_id=str(appeal["id"]),
            subject_id=int(appeal["subject_id"]),
            guild_name=self._guild_name(appeal),
            guild_id=int(appeal["guild_id"]),
            case_ref=case["case"]["reference"], action=appeal.get("action") or "warn",
            reason=case["case"].get("reason") or "", explication=self._explication(case),
            evidence=self._evidence(case), appeal_reason=reason,
        )
        try:
            msg = await channel.send(view=view)
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
        view = build_review_view(
            locale=_PANEL_LOCALE, route=appeal["route"], appeal_id=str(appeal["id"]),
            subject_id=int(appeal["subject_id"]), guild_name=self._guild_name(appeal),
            guild_id=int(appeal["guild_id"]),
            case_ref=case["case"]["reference"] if case else "—",
            action=appeal.get("action") or "warn",
            reason=(case["case"].get("reason") if case else "") or "",
            explication=self._explication(case) if case else "",
            evidence=self._evidence(case) if case else "",
            appeal_reason=appeal.get("reason") or "",
            decided=decided,
        )
        try:
            # The interaction is the button click on the panel → edit in place.
            if not interaction.response.is_done():
                await interaction.response.edit_message(view=view)
            else:
                await interaction.edit_original_response(view=view)
        except (discord.HTTPException, discord.InteractionResponded):
            # Fallback: edit the stored panel message.
            await self._edit_stored(appeal.get("review_channel_id"), appeal.get("review_message_id"), view)

    async def _refresh_dm(self, appeal: dict, case: Optional[dict], *, status: str,
                          locale: str, notify: bool = False, decided: Optional[dict] = None):
        from utils.appeal_views import build_sanction_dm_view
        ch_id = appeal.get("dm_channel_id")
        msg_id = appeal.get("dm_message_id")
        if case is None:
            case = await self.db.get_case_by_id(_as_uuid(appeal["case_id"]))
        ref = case["case"]["reference"] if case else "—"
        reason = (case["case"].get("reason") if case else "") or ""
        view = build_sanction_dm_view(
            locale=locale, guild_name=self._guild_name(appeal), case_ref=ref,
            action=appeal.get("action") or "warn", reason=reason,
            explication=self._explication(case) if case else "",
            case_id=str(appeal["case_id"]), sanction_id=str(appeal.get("sanction_id") or ""),
            appeal_status=status, appeal_route=appeal["route"],
        )
        await self._edit_stored(ch_id, msg_id, view)
        if notify and decided:
            await self._dm_outcome(int(appeal["subject_id"]), appeal, decided, locale)

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
            await channel.send(view=view)
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
        try:
            await channel.send(view=view)
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

    @staticmethod
    def _explication(case: Optional[dict]) -> str:
        if not case:
            return ""
        for ev in reversed(case.get("events", [])):
            if ev.get("type") == "evidence" and (ev.get("payload") or {}).get("explication"):
                return ev["payload"]["explication"]
        return ""

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
