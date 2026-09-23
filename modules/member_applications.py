"""
Member Applications module — review Discord's "Apply to Join" applications
from a channel.

Discord lets a server require an application form before anyone can join
(https://support.discord.com/hc/en-us/articles/29729107418519). The form, the
questions and the gate itself stay Discord's: they are set up in the server
settings, and nothing here can replace them. What this module adds is the
review side, where the staff already works:

- every submitted application is posted as a card in a review channel —
  applicant, account age, earlier applications, every answer;
- *Approve* / *Reject* buttons act on the application through the API (a
  rejection asks for the reason Discord shows the applicant, with the server's
  preset reasons one click away);
- a decision taken in Discord's own review screen updates the card too, so
  the channel never shows an application as pending when it is not.

The pieces:

- ``services/member_application_service.py`` — API calls, card lifecycle
- ``cogs/member_applications.py``            — gateway events + reconciliation poll
- ``utils/member_application_views.py``      — the card, its buttons, the reject modal
- ``modules/configs/member_applications_config.py`` — the /config panel

See docs/MEMBER_APPLICATIONS.md.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import discord

from modules.module_manager import ModuleBase
from utils.emojis import SHAPES
from utils.i18n import t

logger = logging.getLogger("moddy.modules.member_applications")

MODULE_ID = "member_applications"

# Discord's guild feature for a server whose join requests need a manual
# decision — i.e. the application form has more than a rules checkbox.
DISCORD_FEATURE = "MEMBER_VERIFICATION_MANUAL_APPROVAL"

MAX_PING_ROLES = 5
MAX_REVIEWER_ROLES = 10
# A preset reason is offered as a select option in the reject modal, whose
# label Discord caps at 100 characters — tighter than the 160 characters the
# API accepts for the reason itself.
MAX_PRESET_REASONS = 10
MAX_PRESET_REASON_LENGTH = 100
# What Discord accepts for a rejection reason (Action Guild Join Request).
MAX_REJECTION_REASON_LENGTH = 160

CHANNEL_TYPES = [discord.ChannelType.text, discord.ChannelType.news]


def normalize_reasons(raw: Any) -> List[str]:
    """Clean a preset reason list: trimmed, deduplicated, capped."""
    if isinstance(raw, str):
        raw = raw.splitlines()
    if not isinstance(raw, list):
        return []
    reasons: List[str] = []
    for item in raw:
        text = " ".join(str(item).split())[:MAX_PRESET_REASON_LENGTH]
        if text and text not in reasons:
            reasons.append(text)
    return reasons[:MAX_PRESET_REASONS]


def _ids(raw: Any, cap: int) -> List[int]:
    if not isinstance(raw, list):
        return []
    return [int(v) for v in raw if str(v).isdigit()][:cap]


class MemberApplicationsModule(ModuleBase):
    """Posts Discord membership applications in a review channel."""

    MODULE_ID = MODULE_ID
    MODULE_NAME = "Member Applications"
    MODULE_DESCRIPTION = "Review Discord membership applications from a channel"
    MODULE_EMOJI = SHAPES
    # Next to AltGuard: both decide who gets into the server.
    MODULE_ORDER = 22
    # Discord requires Kick Members to list and action join requests. Nothing
    # else: the review card is a plain message in a channel the server picks,
    # checked for View/Send at save time.
    REQUIRED_BOT_PERMISSIONS = ["kick_members"]

    def __init__(self, bot, guild_id: int):
        super().__init__(bot, guild_id)
        self.channel_id: Optional[int] = None
        self.ping_role_ids: List[int] = []
        self.reviewer_role_ids: List[int] = []
        self.rejection_reasons: List[str] = []

    # ----------------------------------------------------------------- #
    # Configuration
    # ----------------------------------------------------------------- #

    def get_default_config(self) -> Dict[str, Any]:
        # No "enabled" flag: a configured server is a server using the module,
        # and deleting the configuration is how it is turned off.
        return {
            "channel_id": None,
            "ping_role_ids": [],
            "reviewer_role_ids": [],
            "rejection_reasons": [],
        }

    async def load_config(self, config_data: Dict[str, Any]) -> bool:
        try:
            self.config = config_data
            channel_id = config_data.get("channel_id")
            self.channel_id = int(channel_id) if str(channel_id or "").isdigit() else None
            self.ping_role_ids = _ids(config_data.get("ping_role_ids"), MAX_PING_ROLES)
            self.reviewer_role_ids = _ids(config_data.get("reviewer_role_ids"), MAX_REVIEWER_ROLES)
            self.rejection_reasons = normalize_reasons(config_data.get("rejection_reasons"))
            # Configured = active. There is no on/off switch; a legacy
            # "enabled" key, if any, is ignored.
            self.enabled = self.channel_id is not None
            return True
        except Exception as e:
            logger.error(f"Error loading member_applications config: {e}", exc_info=True)
            return False

    async def validate_config(self, config_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        locale = await self._locale()

        if not config_data.get("channel_id"):
            return False, t("modules.member_applications.errors.channel_required", locale=locale)

        if len(config_data.get("ping_role_ids") or []) > MAX_PING_ROLES:
            return False, t("modules.member_applications.errors.too_many_roles",
                            locale=locale, max=MAX_PING_ROLES)
        if len(config_data.get("reviewer_role_ids") or []) > MAX_REVIEWER_ROLES:
            return False, t("modules.member_applications.errors.too_many_roles",
                            locale=locale, max=MAX_REVIEWER_ROLES)

        reasons = config_data.get("rejection_reasons") or []
        if not isinstance(reasons, list) or len(reasons) > MAX_PRESET_REASONS:
            return False, t("modules.member_applications.errors.too_many_reasons",
                            locale=locale, max=MAX_PRESET_REASONS)

        guild = self.bot.get_guild(self.guild_id) if self.bot else None
        if guild is None:
            return True, None

        channel = guild.get_channel(int(config_data["channel_id"]))
        if channel is None:
            return False, t("modules.member_applications.errors.channel_not_found", locale=locale)
        if not isinstance(channel, discord.TextChannel):
            return False, t("modules.member_applications.errors.channel_type", locale=locale)
        perms = channel.permissions_for(guild.me)
        if not perms.view_channel or not perms.send_messages:
            return False, t("modules.member_applications.errors.no_send_permission",
                            locale=locale, channel=channel.mention)

        return True, None

    # ----------------------------------------------------------------- #
    # Runtime helpers
    # ----------------------------------------------------------------- #

    def can_review(self, member: Any) -> bool:
        """Whether this member may approve or reject from the card.

        Discord itself requires Kick Members to act on a join request; the
        module lets a server widen that to chosen roles, so a recruitment team
        can review without being handed the power to kick.
        """
        perms = getattr(member, "guild_permissions", None)
        if perms is not None and (perms.administrator or perms.kick_members):
            return True
        role_ids = {role.id for role in getattr(member, "roles", []) or []}
        return bool(role_ids & set(self.reviewer_role_ids))

    async def _locale(self) -> str:
        """Server language — the review card is read by the whole staff."""
        from utils.guild_language import guild_locale
        return await guild_locale(self.bot, self.guild_id)
