"""
Automod module — AI-assisted moderation of problematic messages.

This module is the **caller** described in ``docs/AUTOMOD.md``: it owns
configuration and *applies* the decisions produced by the detection pipeline
(``automod/``). The pipeline only decides; this module deletes / warns / mutes /
bans, records cases (with evidence) through :class:`CaseService`, logs to the
configured channel, and re-submits messages nano flagged for re-check.

Scalability
-----------
The module dispatches each message to a set of **features**. Today the only
feature is ``content`` (insults / problematic messages via the AI funnel).
Future detectors — anti-link, anti-invite, anti-spam, anti-raid — plug in by
adding an :class:`AutomodFeature` subclass to ``FEATURE_CLASSES``; they emit the
same :class:`~automod.schemas.Decision` objects and reuse the shared
application / case / logging path below. No other file needs to change.

Config (stored in ``guilds.data.modules.automod``)::

    {
      "enabled": true,
      "rules": "server rules text (AI-validated for prompt injection)",
      "log_channel_id": 123 | null,
      "ignore_moderators": true,
      "features": {
        "content": {
          "enabled": true,
          "exempt_roles": [<role_id>, ...],
          "exempt_channels": [<channel_id>, ...]
        }
      }
    }
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import discord

from modules.module_manager import ModuleBase
from automod import (
    get_engine, Decision, TargetMessage, ContextMessage, AuthorHistory,
)
from automod import constants as ac
from utils.moderation_cases import IssuerType, SanctionAction, EventType, AuthorType

logger = logging.getLogger("moddy.modules.automod")

# Mute (Discord timeout) duration per decided gravity. Capped at 28 days.
_MUTE_DURATIONS = {
    "basse": timedelta(minutes=10),
    "moyenne": timedelta(hours=1),
    "haute": timedelta(days=1),
    "critique": timedelta(days=7),
}

# Map a pipeline action to a case SanctionAction (None = not a sanction).
_ACTION_TO_SANCTION = {
    "warn": SanctionAction.WARN,
    "mute": SanctionAction.MUTE,
    "ban": SanctionAction.BAN,
    "supprimer": None,
}

# Re-verification safety caps.
_MAX_REVERIFY = 5

# Below this length, skip the (cost-bearing) embedding step; explicit short
# insults are already caught by the regex blocklist.
_MIN_EMBED_LENGTH = 4


# ===========================================================================
# Feature framework (the scalable seam)
# ===========================================================================

class AutomodFeature:
    """Base class for an automod detector feature."""

    feature_id: str = "base"

    def __init__(self, module: "AutomodModule", config: Dict[str, Any]):
        self.module = module
        self.bot = module.bot
        self.guild_id = module.guild_id
        self.config = config or {}

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", False))

    def is_exempt(self, message: discord.Message) -> bool:
        """Per-feature role / channel exemptions."""
        exempt_channels = set(self.config.get("exempt_channels", []))
        if message.channel.id in exempt_channels:
            return True
        # Also exempt the parent of a thread.
        parent_id = getattr(message.channel, "parent_id", None)
        if parent_id and parent_id in exempt_channels:
            return True
        exempt_roles = set(self.config.get("exempt_roles", []))
        if exempt_roles and isinstance(message.author, discord.Member):
            if any(r.id in exempt_roles for r in message.author.roles):
                return True
        return False

    async def process(self, message: discord.Message) -> List[Decision]:
        raise NotImplementedError


class ContentModerationFeature(AutomodFeature):
    """Insults / problematic messages, via the AI detection funnel."""

    feature_id = "content"

    async def process(self, message: discord.Message) -> List[Decision]:
        engine = get_engine(self.bot)
        decisions: List[Decision] = []

        # Skip the embedding step for very short content (blocklist already
        # covers explicit short insults) to save API calls.
        content = message.content or ""
        if len(content.strip()) < _MIN_EMBED_LENGTH and engine.blocklist.match(content) is None:
            return decisions

        target = TargetMessage(
            id=str(message.id),
            author_id=str(message.author.id),
            content=content,
        )
        history = await self.module.build_author_history(message.author.id)

        decision = await engine.analyze(
            target,
            guild_id=self.guild_id,
            guild_name=message.guild.name if message.guild else "",
            rules=self.module.rules,
            author_history=history,
            fetch_context=self.module.make_context_loader(message),
            is_bot=message.author.bot,
            is_system=message.type not in (discord.MessageType.default, discord.MessageType.reply),
            severity=self.module.severity,
            response_language=self.module.response_language(message.guild),
        )
        if decision is not None:
            decisions.append(decision)

        # One level of re-verification for messages nano flagged.
        if decision is not None and decision.a_reverifier:
            decisions.extend(await self._reverify(message, decision.a_reverifier))

        return decisions

    async def _reverify(self, origin: discord.Message, ids: List[str]) -> List[Decision]:
        engine = get_engine(self.bot)
        out: List[Decision] = []
        channel = origin.channel
        for raw_id in ids[:_MAX_REVERIFY]:
            try:
                msg_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if msg_id == origin.id:
                continue
            try:
                msg = await channel.fetch_message(msg_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                continue
            if msg.author.bot:
                continue
            target = TargetMessage(
                id=str(msg.id), author_id=str(msg.author.id), content=msg.content or "",
            )
            history = await self.module.build_author_history(msg.author.id)
            decision = await engine.analyze(
                target,
                guild_id=self.guild_id,
                guild_name=origin.guild.name if origin.guild else "",
                rules=self.module.rules,
                author_history=history,
                fetch_context=self.module.make_context_loader(msg),
                force_nano=True,   # already flagged → straight to nano
                severity=self.module.severity,
                response_language=self.module.response_language(origin.guild),
            )
            # Do NOT recurse into this decision's a_reverifier (one level only).
            if decision is not None:
                decision.a_reverifier = []
                out.append(decision)
        return out


FEATURE_CLASSES = {
    ContentModerationFeature.feature_id: ContentModerationFeature,
    # Future: "anti_link": AntiLinkFeature, "anti_spam": AntiSpamFeature, ...
}


# ===========================================================================
# Module
# ===========================================================================

class AutomodModule(ModuleBase):
    """AI-assisted automod. See module docstring."""

    MODULE_ID = "automod"
    MODULE_NAME = "Automod"
    MODULE_DESCRIPTION = "Modération automatique des messages problématiques (IA)"
    MODULE_EMOJI = "<:shield:1521471376815292498>"

    def __init__(self, bot, guild_id: int):
        super().__init__(bot, guild_id)
        self.rules: str = ""
        self.notify_channel_id: Optional[int] = None
        self.ignore_moderators: bool = True
        self.severity: int = ac.SEVERITY_DEFAULT
        self._features: Dict[str, AutomodFeature] = {}
        self._warmup_task: Optional[asyncio.Task] = None

    # -- ModuleBase interface ----------------------------------------------

    async def load_config(self, config_data: Dict[str, Any]) -> bool:
        try:
            self.config = config_data or {}
            # "indications" replaces the legacy "rules" key (read both).
            self.rules = str(self.config.get("indications", self.config.get("rules", "")) or "")
            # "notify_channel_id" replaces the legacy "log_channel_id".
            self.notify_channel_id = self.config.get("notify_channel_id", self.config.get("log_channel_id"))
            self.ignore_moderators = bool(self.config.get("ignore_moderators", True))
            self.severity = ac.clamp_severity(self.config.get("severity", ac.SEVERITY_DEFAULT))

            features_cfg = self.config.get("features", {}) or {}
            self._features = {}
            for fid, fclass in FEATURE_CLASSES.items():
                fcfg = features_cfg.get(fid, {})
                self._features[fid] = fclass(self, fcfg)

            module_on = bool(self.config.get("enabled", False))
            any_feature_on = any(f.enabled for f in self._features.values())
            # The alert channel is MANDATORY: automod does not run without it.
            has_channel = self.notify_channel_id is not None
            self.enabled = module_on and any_feature_on and has_channel
            return True
        except Exception as e:
            logger.error("Error loading automod config: %s", e, exc_info=True)
            return False

    async def validate_config(self, config_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            return False, "Serveur introuvable"

        notify_channel_id = config_data.get("notify_channel_id", config_data.get("log_channel_id"))
        if notify_channel_id is not None:
            channel = guild.get_channel(int(notify_channel_id))
            if not channel or not isinstance(channel, discord.TextChannel):
                return False, "Salon d'alertes invalide"

        indications = config_data.get("indications", config_data.get("rules", ""))
        if indications and len(indications) > 3000:
            return False, "Les indications sont trop longues (max 3000 caractères)"

        severity = config_data.get("severity")
        if severity is not None and ac.clamp_severity(severity) != int(severity):
            return False, "Niveau de sévérité invalide (1 à 5)"

        features_cfg = config_data.get("features", {}) or {}
        for fid in features_cfg:
            if fid not in FEATURE_CLASSES:
                return False, f"Fonctionnalité inconnue : {fid}"
        return True, None

    def get_default_config(self) -> Dict[str, Any]:
        return {
            "enabled": False,
            "indications": "",
            "notify_channel_id": None,
            "ignore_moderators": True,
            "severity": ac.SEVERITY_DEFAULT,
            "features": {
                "content": {
                    "enabled": False,
                    "exempt_roles": [],
                    "exempt_channels": [],
                },
            },
        }

    # -- Lifecycle ---------------------------------------------------------

    async def on_enable(self):
        # Warm up the embedding references in the background so the first real
        # message isn't slowed by reference embedding.
        engine = get_engine(self.bot)
        if not engine.embeddings.ready and (self._warmup_task is None or self._warmup_task.done()):
            self._warmup_task = asyncio.create_task(engine.ensure_ready())

    async def on_disable(self):
        if self._warmup_task and not self._warmup_task.done():
            self._warmup_task.cancel()
        self._warmup_task = None

    # -- Event entry point (called by ModuleEvents) ------------------------

    async def on_message(self, message: discord.Message):
        if not self.enabled or not message.guild:
            return
        if message.author.bot or message.webhook_id is not None:
            return
        if not isinstance(message.author, discord.Member):
            return

        # Global moderator exemption.
        if self.ignore_moderators and message.author.guild_permissions.manage_messages:
            return

        for feature in self._features.values():
            if not feature.enabled:
                continue
            if feature.is_exempt(message):
                continue
            try:
                decisions = await feature.process(message)
            except Exception as e:
                logger.error(
                    "automod feature %s failed (guild %s): %s",
                    feature.feature_id, self.guild_id, e, exc_info=True,
                )
                continue
            for decision in decisions:
                try:
                    await self.apply_decision(message, decision)
                except Exception as e:
                    logger.error(
                        "automod apply_decision failed (guild %s): %s",
                        self.guild_id, e, exc_info=True,
                    )

    # -- Providers for the pipeline ----------------------------------------

    def make_context_loader(self, message: discord.Message):
        """Return an async ``(n) -> list[ContextMessage]`` for this message."""
        channel = message.channel

        async def loader(n: int) -> List[ContextMessage]:
            out: List[ContextMessage] = []
            try:
                async for prev in channel.history(limit=n, before=message):
                    if prev.author.bot:
                        continue
                    if not (prev.content or "").strip():
                        continue
                    out.append(ContextMessage(
                        id=str(prev.id),
                        author_id=str(prev.author.id),
                        content=prev.content,
                    ))
            except (discord.Forbidden, discord.HTTPException):
                return []
            out.reverse()  # oldest → newest
            return out

        return loader

    async def build_author_history(self, user_id: int) -> AuthorHistory:
        """Fetch the author's case/sanction history from the DB.

        Includes ``messages_deja_moderes`` (messages this author already had
        actioned by automod in this guild) so nano never re-sanctions the
        current message for conduct already handled in an earlier one.
        """
        if not self.bot.db:
            return AuthorHistory()
        try:
            total = await self.bot.db.count_subject_cases("discord_user", user_id)
            active = await self.bot.db.get_active_subject_sanctions(
                "discord_user", user_id, case_type="guild",
            )
            recent = []
            for s in active[:5]:
                created = s.get("created_at")
                recent.append({
                    "type": s.get("action", ""),
                    "date": created.strftime("%Y-%m-%d") if created else "",
                    "raison": (s.get("note") or "")[:120],
                })
            already = await self.bot.db.list_automod_evidence_message_ids(
                user_id, self.guild_id, limit=25,
            )
            return AuthorHistory(
                cases_total=int(total or 0),
                sanctions_recentes=recent,
                messages_deja_moderes=already,
            )
        except Exception as e:
            logger.error("automod: failed to build author history: %s", e)
            return AuthorHistory()

    # -- Decision application ----------------------------------------------

    def _mark_moddy_initiated(self, user_id: int, action: str):
        """Tell case_sync to skip the audit-log echo of our own action."""
        store = getattr(self.bot, "_moddy_initiated_sanctions", None)
        if store is None:
            store = {}
            self.bot._moddy_initiated_sanctions = store
        store[(self.guild_id, user_id, action)] = time.time()

    async def apply_decision(self, message: discord.Message, decision: Decision):
        if not decision.sanctionnable or not decision.actions:
            return

        guild = message.guild
        member = guild.get_member(int(decision.auteur_id)) if guild else None
        me = guild.me if guild else None
        actions = decision.actions

        applied: List[str] = []

        # 1. Delete the offending message (no case needed for this).
        if "supprimer" in actions:
            target_msg = message if str(message.id) == decision.message_id else None
            if target_msg is None:
                try:
                    target_msg = await message.channel.fetch_message(int(decision.message_id))
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    target_msg = None
            if target_msg is not None:
                try:
                    await target_msg.delete()
                    applied.append("supprimer")
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

        # 2. Record the case FIRST so the Discord audit-log reason can carry the
        #    public case reference, exactly like a manual sanction.
        case_ref, case_id, primary_action, primary_sanction_id = \
            await self._record_case(message, decision)

        # 3. Apply the Discord-side sanction with the standardized reason.
        can_act = member is not None and me is not None and member != guild.owner \
            and (me.top_role > member.top_role)

        if "ban" in actions and can_act and me.guild_permissions.ban_members:
            ban_expires = self._expiry_for(decision, "ban")
            audit = self._audit_reason(case_ref, ban_expires, decision.raison)
            try:
                self._mark_moddy_initiated(member.id, "ban")
                await guild.ban(member, reason=audit, delete_message_days=0)
                applied.append("ban")
            except (discord.Forbidden, discord.HTTPException):
                pass
        elif "mute" in actions and can_act and me.guild_permissions.moderate_members:
            duration = self._duration_td(decision) or _MUTE_DURATIONS.get(
                decision.gravite, timedelta(hours=1))
            audit = self._audit_reason(
                case_ref, datetime.now(timezone.utc) + duration, decision.raison)
            try:
                self._mark_moddy_initiated(member.id, "mute")
                await member.timeout(duration, reason=audit)
                applied.append("mute")
            except (discord.Forbidden, discord.HTTPException):
                pass

        # A warn carries no Discord-side action but IS a recorded sanction —
        # surface it so the notification doesn't read "aucune".
        if "warn" in actions and "warn" not in applied:
            applied.append("warn")

        # 4. Notify the server in the mandatory alert channel.
        await self._notify_channel(message, decision, applied, case_ref)

        # 5. DM the sanctioned member (like a manual mod action) with the
        #    appeal buttons — only when a real sanction was opened.
        if case_id is not None and primary_action is not None and member is not None:
            await self._send_sanction_dm(
                member, case_id, case_ref, primary_action, primary_sanction_id, decision,
            )

    # Discord timeout / temporary sanction max (28 days).
    _MAX_DURATION = timedelta(days=28)

    def _duration_td(self, decision: Decision) -> Optional[timedelta]:
        """nano-decided sanction duration (clamped), or None for permanent."""
        hours = getattr(decision, "duree_heures", 0) or 0
        if hours <= 0:
            return None
        return min(timedelta(hours=hours), self._MAX_DURATION)

    def _expiry_for(self, decision: Decision, action: str):
        """Absolute ``expires_at`` for a sanction action (or None = permanent).

        A mute is always temporary (Discord timeouts require a duration); ban
        and warn are permanent unless nano set an explicit duration.
        """
        td = self._duration_td(decision)
        if action == "mute":
            return datetime.now(timezone.utc) + (
                td or _MUTE_DURATIONS.get(decision.gravite, timedelta(hours=1)))
        if action in ("ban", "warn") and td is not None:
            return datetime.now(timezone.utc) + td
        return None

    def _audit_reason(self, case_ref: Optional[str], expires_at, reason: str) -> str:
        """Discord audit-log reason, identical in shape to manual sanctions.

        Mirrors ``cogs.moderation_commands._build_discord_reason``:
        ``[<ref>] @<mod> (<expiry>) : <reason>``.
        """
        ref = case_ref or "Automod"
        mod_name = self.bot.user.name if self.bot.user else "Moddy"
        expiry = expires_at.strftime("%Y-%m-%d %H:%M UTC") if expires_at else "Permanent"
        return f"[{ref}] @{mod_name} ({expiry}) : {reason}"[:512]

    async def _record_case(self, message: discord.Message, decision: Decision):
        """Open ONE guild case for this incident, attach its sanctions + evidence.

        Each automod incident is its own case: the first sanction opens a fresh
        case (``link_open=False``) and any further sanctions of the same decision
        are added to *that* case. This avoids every future automod action piling
        onto one ever-open folder (which also kept reusing the same reference).

        Returns ``(case_ref, case_id, primary_action, primary_sanction_id)`` so
        the caller can DM the member an appealable notice. ``primary_action`` is
        the most punitive recorded sanction (ban > mute > warn).
        """
        if not getattr(self.bot, "cases", None):
            return None, None, None, None

        # Decided actions that are real sanctions (deletion is not a sanction),
        # ordered most-punitive first so the case opens on the primary sanction.
        order = {"ban": 3, "mute": 2, "warn": 1}
        sanction_actions = sorted(
            [a for a in decision.actions if _ACTION_TO_SANCTION.get(a) is not None],
            key=lambda a: order.get(a, 0), reverse=True,
        )
        if not sanction_actions:
            return None, None, None, None  # deletion-only: no case folder

        # Factual reason (issuer already identifies automod — no "[Automod]" noise).
        case_reason = (decision.raison or decision.categorie or "Message problématique")[:480]
        extrait = (message.content or "")[:1500]
        note = f"Automod · {decision.categorie} · {decision.gravite}"

        case_ref = case_id = None
        primary_action = None
        primary_sanction_id = None
        for idx, action in enumerate(sanction_actions):
            expires_at = self._expiry_for(decision, action)
            if idx == 0:
                # First sanction → open a brand-new case for this incident.
                result = await self.bot.cases.record_sanction(
                    "guild",
                    subject_id=int(decision.auteur_id),
                    action=_ACTION_TO_SANCTION[action],
                    reason=case_reason,
                    issuer_type=IssuerType.AUTOMOD,
                    issuer_id=self.bot.user.id if self.bot.user else None,
                    scope_id=self.guild_id,
                    expires_at=expires_at,
                    note=note,
                    link_open=False,
                )
                if not result:
                    continue
                case_id = result["id"]
                case_ref = result["reference"]
                primary_action = action
                primary_sanction_id = result.get("sanction_id")
            else:
                # Further sanctions of the SAME decision → same case folder.
                if case_id is None:
                    continue
                await self.bot.db.add_sanction(
                    case_id, _ACTION_TO_SANCTION[action].value,
                    IssuerType.AUTOMOD.value,
                    self.bot.user.id if self.bot.user else None,
                    expires_at=expires_at, note=note,
                )

        # Attach the offending message as evidence on the case.
        if case_id is not None:
            jump = getattr(message, "jump_url", "")
            evidence = (
                f"Message de <@{decision.auteur_id}> (`{decision.auteur_id}`) dans "
                f"<#{message.channel.id}> :\n"
                f"> {extrait or '*(vide)*'}\n\n"
                f"Détection : `{decision.signal_source}` · catégorie `{decision.categorie}` "
                f"· gravité `{decision.gravite}` · score `{decision.score_detecteur:.2f}` "
                f"· confiance `{decision.confiance}`"
                + (f"\n[Aller au message]({jump})" if jump else "")
            )
            try:
                await self.bot.db.add_event(
                    case_id,
                    EventType.EVIDENCE.value,
                    author_type=AuthorType.SYSTEM.value,
                    content=evidence,
                    payload={
                        "source": "automod",
                        "message_id": decision.message_id,
                        "channel_id": message.channel.id,
                        "jump_url": jump,
                        "extrait": extrait,
                        "raison": decision.raison,
                        "explication": decision.explication,
                        "signal_source": decision.signal_source,
                        "categorie": decision.categorie,
                        "gravite": decision.gravite,
                        "score_detecteur": round(decision.score_detecteur, 4),
                        "confiance": decision.confiance,
                        "actions": decision.actions,
                    },
                )
            except Exception as e:
                logger.error("automod: failed to attach evidence to case %s: %s", case_ref, e)

        return case_ref, case_id, primary_action, primary_sanction_id

    def guild_locale(self, guild: Optional[discord.Guild]) -> str:
        """The locale automod speaks in this guild.

        Uses the guild's preferred locale **only when Community is enabled**
        (that's when Discord lets the server pick a real language); otherwise we
        have no reliable language signal, so we default to English.
        """
        try:
            features = set(getattr(guild, "features", []) or [])
            if "COMMUNITY" not in features:
                return "en-US"
            pref = str(getattr(guild, "preferred_locale", "") or "")
        except Exception:
            return "en-US"
        return "fr" if pref.lower().startswith("fr") else "en-US"

    # Backwards-compatible alias used by the DM/notification helpers.
    def _dm_locale(self, guild: Optional[discord.Guild]) -> str:
        return self.guild_locale(guild)

    def response_language(self, guild: Optional[discord.Guild]) -> str:
        """Language name the AI writes its reason/explanation in."""
        from automod.nano import response_language_name
        return response_language_name(self.guild_locale(guild))

    async def _send_sanction_dm(self, member: discord.Member, case_id, case_ref: str,
                                primary_action: str, primary_sanction_id, decision: Decision):
        """DM the member their sanction with Server / Moddy-team appeal buttons."""
        if not primary_sanction_id:
            return  # cannot offer an appeal without a sanction to target
        from utils.appeal_views import build_sanction_dm_view
        locale = self._dm_locale(member.guild)
        guild_name = member.guild.name if member.guild else ""
        view = build_sanction_dm_view(
            locale=locale,
            guild_name=guild_name,
            case_ref=case_ref or "—",
            action=primary_action,
            reason=decision.raison,
            explication=decision.explication,
            case_id=str(case_id),
            sanction_id=str(primary_sanction_id),
        )
        try:
            await member.send(view=view)
        except (discord.Forbidden, discord.HTTPException):
            pass  # closed DMs — the case + channel notification still stand

    async def _notify_channel(self, message: discord.Message, decision: Decision,
                              applied: List[str], case_ref: Optional[str]):
        """Post the decision to the mandatory server alert channel."""
        if not self.notify_channel_id:
            return
        guild = message.guild
        channel = guild.get_channel(int(self.notify_channel_id)) if guild else None
        if not channel or not isinstance(channel, discord.TextChannel):
            return
        me = guild.me
        if not channel.permissions_for(me).send_messages:
            return

        from discord import ui
        from utils.emojis import FILTER, WARNING, INFO

        applied_txt = ", ".join(applied) if applied else "détection seule"
        view = ui.LayoutView(timeout=None)
        container = ui.Container()
        container.add_item(ui.TextDisplay(f"### {FILTER} Automod"))
        container.add_item(ui.TextDisplay(
            f"**Auteur :** <@{decision.auteur_id}> (`{decision.auteur_id}`)\n"
            f"**Salon :** <#{message.channel.id}>\n"
            f"**Catégorie :** `{decision.categorie}` · **Gravité :** `{decision.gravite}`\n"
            f"**Détection :** `{decision.signal_source}` (score `{decision.score_detecteur:.2f}`, "
            f"confiance `{decision.confiance}`)"
        ))
        if message.content:
            container.add_item(ui.TextDisplay(f"> {message.content[:500]}"))
        container.add_item(ui.TextDisplay(
            f"{WARNING} **Raison :** {decision.raison or '—'}\n"
            + (f"-# {decision.explication}\n" if decision.explication else "")
            + f"-# **Actions :** {applied_txt}"
            + (f" · **Case :** `{case_ref}`" if case_ref else "")
        ))
        view.add_item(container)
        try:
            await channel.send(view=view)
        except (discord.Forbidden, discord.HTTPException):
            pass
