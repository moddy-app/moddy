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
from datetime import timedelta
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
    MODULE_EMOJI = "<:filter:1520586655180783666>"

    def __init__(self, bot, guild_id: int):
        super().__init__(bot, guild_id)
        self.rules: str = ""
        self.log_channel_id: Optional[int] = None
        self.ignore_moderators: bool = True
        self._features: Dict[str, AutomodFeature] = {}
        self._warmup_task: Optional[asyncio.Task] = None

    # -- ModuleBase interface ----------------------------------------------

    async def load_config(self, config_data: Dict[str, Any]) -> bool:
        try:
            self.config = config_data or {}
            self.rules = str(self.config.get("rules", "") or "")
            self.log_channel_id = self.config.get("log_channel_id")
            self.ignore_moderators = bool(self.config.get("ignore_moderators", True))

            features_cfg = self.config.get("features", {}) or {}
            self._features = {}
            for fid, fclass in FEATURE_CLASSES.items():
                fcfg = features_cfg.get(fid, {})
                self._features[fid] = fclass(self, fcfg)

            module_on = bool(self.config.get("enabled", False))
            any_feature_on = any(f.enabled for f in self._features.values())
            self.enabled = module_on and any_feature_on
            return True
        except Exception as e:
            logger.error("Error loading automod config: %s", e, exc_info=True)
            return False

    async def validate_config(self, config_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            return False, "Serveur introuvable"

        log_channel_id = config_data.get("log_channel_id")
        if log_channel_id is not None:
            channel = guild.get_channel(int(log_channel_id))
            if not channel or not isinstance(channel, discord.TextChannel):
                return False, "Salon de logs invalide"

        rules = config_data.get("rules", "")
        if rules and len(rules) > 3000:
            return False, "Le règlement est trop long (max 3000 caractères)"

        features_cfg = config_data.get("features", {}) or {}
        for fid in features_cfg:
            if fid not in FEATURE_CLASSES:
                return False, f"Fonctionnalité inconnue : {fid}"
        return True, None

    def get_default_config(self) -> Dict[str, Any]:
        return {
            "enabled": False,
            "rules": "",
            "log_channel_id": None,
            "ignore_moderators": True,
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
        """Fetch the author's case/sanction history from the DB."""
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
            return AuthorHistory(cases_total=int(total or 0), sanctions_recentes=recent)
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
        reason = f"[Automod] {decision.raison}"[:480] or "[Automod]"

        applied: List[str] = []

        # 1. Delete the offending message.
        if "supprimer" in actions:
            # Resolve the right message: target may be a re-verified one.
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

        # 2. Ban (takes precedence over mute).
        can_act = member is not None and me is not None and member != guild.owner \
            and (me.top_role > member.top_role)

        if "ban" in actions and can_act and me.guild_permissions.ban_members:
            try:
                self._mark_moddy_initiated(member.id, "ban")
                await guild.ban(member, reason=reason, delete_message_days=0)
                applied.append("ban")
            except (discord.Forbidden, discord.HTTPException):
                pass
        elif "mute" in actions and can_act and me.guild_permissions.moderate_members:
            duration = _MUTE_DURATIONS.get(decision.gravite, timedelta(hours=1))
            try:
                self._mark_moddy_initiated(member.id, "mute")
                await member.timeout(duration, reason=reason)
                applied.append("mute")
            except (discord.Forbidden, discord.HTTPException):
                pass

        # 3. Record the case + evidence for every sanction action.
        await self._record_case(message, decision, applied)

        # 4. Log to the configured channel.
        await self._log_decision(message, decision, applied)

    async def _record_case(self, message: discord.Message, decision: Decision, applied: List[str]):
        """Open/extend a guild case for the sanctions and attach evidence."""
        if not getattr(self.bot, "cases", None):
            return

        # Determine which decided actions are real sanctions.
        sanction_actions = [
            _ACTION_TO_SANCTION[a]
            for a in decision.actions
            if _ACTION_TO_SANCTION.get(a) is not None
        ]
        if not sanction_actions:
            return  # deletion-only: no case folder

        case_ref = None
        case_id = None
        for sanction in sanction_actions:
            result = await self.bot.cases.record_sanction(
                "guild",
                subject_id=int(decision.auteur_id),
                action=sanction,
                reason=f"[Automod] {decision.raison}"[:480],
                issuer_type=IssuerType.AUTOMOD,
                issuer_id=self.bot.user.id if self.bot.user else None,
                scope_id=self.guild_id,
                note=f"Automod · {decision.categorie} · {decision.gravite}",
            )
            if result:
                case_id = result["id"]
                case_ref = result["reference"]

        # Attach the offending message as evidence on the case.
        if case_id is not None:
            evidence = (
                f"Message de `{decision.auteur_id}` dans <#{message.channel.id}> :\n"
                f"> {(message.content or '')[:1500]}\n\n"
                f"Détection : `{decision.signal_source}` · catégorie `{decision.categorie}` "
                f"· score `{decision.score_detecteur:.2f}` · confiance `{decision.confiance}`\n"
                f"Actions IA : {', '.join(decision.actions)}"
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

    async def _log_decision(self, message: discord.Message, decision: Decision, applied: List[str]):
        if not self.log_channel_id:
            return
        guild = message.guild
        channel = guild.get_channel(int(self.log_channel_id)) if guild else None
        if not channel or not isinstance(channel, discord.TextChannel):
            return
        me = guild.me
        if not channel.permissions_for(me).send_messages:
            return

        from discord import ui
        from utils.emojis import FILTER, WARNING

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
            f"-# {WARNING} **Raison :** {decision.raison}\n"
            f"-# **Actions appliquées :** {', '.join(applied) if applied else 'aucune'}"
        ))
        view.add_item(container)
        try:
            await channel.send(view=view)
        except (discord.Forbidden, discord.HTTPException):
            pass
