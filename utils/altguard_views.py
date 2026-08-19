"""
AltGuard UI — verification panel, consent modal, link delivery and log cards.

Flow (docs/ALTGUARD.md §"Parcours membre"):

1. :class:`AltGuardPanelView` — the single, permanent message in the
   verification channel. One button, one job.
2. :class:`AltGuardConsentModal` — opened by that button. It states what is
   collected, links the data notice, the terms and the privacy policy, and
   requires an explicit tick before anything is sent anywhere. The tick is a
   deliberate, separate act from "I want in": the modal cannot be submitted
   without it, and closing it sends nothing.
3. On submit, the bot calls ``POST /altguard/token`` and answers **ephemerally**
   with the personal link (single use, 20 minutes).

The panel view is persistent (guild-wide, anyone may click — the interaction
itself identifies the member). The modal is not, per the documented modal
exclusion in docs/PERSISTENT_VIEWS.md.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

import discord
from discord import ui

from cogs.error_handler import BaseModal, BaseView
from utils.emojis import (
    ALTGUARD, DONE, ERROR, INFO, MANAGE_USER, SHIELD, TIME, WARNING,
    format_verification_badge, get_user_verification_badge,
)
from utils.i18n import i18n, t

logger = logging.getLogger('moddy.utils.altguard_views')

_CID_VERIFY = "moddy:altguard:panel:verify"

# Public documentation surfaced in the consent modal.
DATA_NOTICE_URL = "https://moddy.app/AltGuard-data"
TERMS_URL = "https://moddy.app/terms"
PRIVACY_URL = "https://moddy.app/privacy"

PANEL_ACCENT = discord.Colour(0x5865F2)
SUCCESS_ACCENT = discord.Colour(0x57F287)
WARNING_ACCENT = discord.Colour(0xFEE75C)
ERROR_ACCENT = discord.Colour(0xED4245)
NEUTRAL_ACCENT = discord.Colour(0x99AAB5)

# Verdict -> (emoji, accent) for the log cards.
_VERDICT_STYLE = {
    "passed": (DONE, SUCCESS_ACCENT),
    "flagged": (WARNING, WARNING_ACCENT),
    "blocked": (ERROR, ERROR_ACCENT),
}


# ---------------------------------------------------------------------- #
# Verification panel
# ---------------------------------------------------------------------- #

class AltGuardPanelView(BaseView):
    """The verification panel posted in the guild's verification channel.

    Persistent: yes. Auth: public — anyone who can see the channel may click,
    and the click verifies *that* user. The guild is read from
    ``interaction.guild_id``, so nothing needs to be encoded in the custom_id.
    """

    __persistent__ = True

    def __init__(self, locale: str = "en-US"):
        super().__init__()  # timeout=None
        self.locale = locale
        self._build_view()

    def _build_view(self) -> None:
        self.clear_items()

        container = ui.Container(accent_colour=PANEL_ACCENT)
        container.add_item(ui.TextDisplay(
            f"### {ALTGUARD} {t('modules.altguard.panel.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t('modules.altguard.panel.description', locale=self.locale)
        ))

        container.add_item(ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(
            f"-# {t('modules.altguard.panel.footer', locale=self.locale, terms=TERMS_URL, privacy=PRIVACY_URL, data=DATA_NOTICE_URL)}"
        ))

        self.add_item(container)

        # The action sits OUTSIDE the container, under the card, so the button
        # reads as the call to action rather than as part of the notice.
        button_row = ui.ActionRow()
        verify_btn = ui.Button(
            label=t('modules.altguard.panel.button', locale=self.locale),
            style=discord.ButtonStyle.primary,
            emoji=discord.PartialEmoji.from_str(SHIELD),
            custom_id=_CID_VERIFY,
        )
        verify_btn.callback = self.on_verify
        button_row.add_item(verify_btn)
        self.add_item(button_row)

    async def on_verify(self, interaction: discord.Interaction) -> None:
        """Open the consent modal. Nothing leaves the bot before it is signed."""
        locale = i18n.get_user_locale(interaction)

        if not interaction.guild_id:
            await interaction.response.send_message(
                t('modules.altguard.errors.guild_only', locale=locale), ephemeral=True,
            )
            return

        # Someone already through the gate has nothing to verify: sending them
        # to the service would collect their data for an answer the server
        # already has, and burn one of their five hourly attempts.
        if await _already_verified(interaction):
            await interaction.response.send_message(
                view=build_already_verified_card(locale), ephemeral=True,
            )
            return

        await interaction.response.send_modal(AltGuardConsentModal(locale=locale))

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: public — the clicker is the subject of the verification."""
        bot.add_view(cls())


def build_verification_panel(locale: str = "en-US") -> AltGuardPanelView:
    """The panel message posted (and re-posted) by the module."""
    return AltGuardPanelView(locale=locale)


async def _already_verified(interaction: discord.Interaction) -> bool:
    """Whether the clicker is already through the gate in this guild.

    Two authorities, either one is enough: the verified **role** (what the
    server actually enforces, and what a manual grant leaves behind) and the
    stored state. A lookup failure answers ``False`` — being asked to verify
    once too often is a far smaller problem than a gate that cannot be passed.
    """
    bot = interaction.client
    member = interaction.user
    try:
        module = await bot.module_manager.get_module_instance(
            interaction.guild_id, "altguard")
        if module is None or not module.enabled:
            return False
        if isinstance(member, discord.Member) and module.has_verified_role(member):
            return True
        return await module.is_verified(member.id)
    except Exception as e:
        logger.error(f"[AltGuard] Verified-state lookup failed for {member.id}: {e}")
        return False


# ---------------------------------------------------------------------- #
# Consent modal
# ---------------------------------------------------------------------- #

class AltGuardConsentModal(BaseModal):
    """Explicit consent, collected before any call to AltGuard.

    Not persistent — modals never are (docs/PERSISTENT_VIEWS.md, "Deliberate
    exclusions"). Nothing is lost if the bot restarts while it is open: the
    member re-opens it from the panel, which is persistent.
    """

    def __init__(self, locale: str = "en-US"):
        super().__init__(
            title=t('modules.altguard.consent.title', locale=locale)[:45],
            timeout=None,
        )
        self.locale = locale

        self.notice = ui.TextDisplay(
            t('modules.altguard.consent.body', locale=locale,
              data=DATA_NOTICE_URL, terms=TERMS_URL, privacy=PRIVACY_URL)
        )
        self.add_item(self.notice)

        # A CheckboxGroup with min_values=1 and required=True is the officially
        # supported way to make a tick mandatory (docs/MODALS_V2.md): a bare
        # Checkbox cannot be required, so the modal would submit unticked.
        self.agreement = ui.Label(
            text=t('modules.altguard.consent.agree_label', locale=locale)[:45],
            description=t('modules.altguard.consent.agree_description', locale=locale)[:100],
            component=ui.CheckboxGroup(
                options=[
                    discord.CheckboxGroupOption(
                        label=t('modules.altguard.consent.agree_terms', locale=locale)[:100],
                        value="terms",
                    ),
                    discord.CheckboxGroupOption(
                        label=t('modules.altguard.consent.agree_processing', locale=locale)[:100],
                        value="processing",
                    ),
                ],
                min_values=2,
                max_values=2,
                required=True,
            ),
        )
        self.add_item(self.agreement)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from services.altguard_client import AltGuardError, CONSENT_VERSION

        locale = i18n.get_user_locale(interaction)
        bot = interaction.client

        values = list(self.agreement.component.values or [])
        if len(values) < 2:
            # Discord enforces min_values, but never trust the client with a
            # consent record: refuse rather than call the service.
            await interaction.response.send_message(
                view=build_error_card(locale, 'consent_required'), ephemeral=True,
            )
            return

        # The consent instant is the submit, not the moment the call succeeds:
        # it is what the service stores as proof, and it must not be in the
        # future relative to the request.
        consent_at = datetime.now(timezone.utc)

        await interaction.response.defer(ephemeral=True, thinking=True)

        client = getattr(bot, "altguard", None)
        if client is None or not client.configured:
            await interaction.followup.send(
                view=build_error_card(locale, 'unavailable', code='not_configured'),
                ephemeral=True,
            )
            return

        try:
            result = await client.create_verification_token(
                discord_user_id=interaction.user.id,
                guild_id=interaction.guild_id,
                consent_at=consent_at,
                consent_version=CONSENT_VERSION,
            )
        except AltGuardError as e:
            # The code is what tells a missing secret from a rejected token
            # from a dead service — log it in full, show it discreetly.
            logger.warning(
                f"[AltGuard] Token request failed for {interaction.user.id} "
                f"in guild {interaction.guild_id}: {e.code} ({e.message})"
            )
            key = 'rate_limited' if e.code == 'rate_limited' else 'unavailable'
            await interaction.followup.send(
                view=build_error_card(locale, key, retry_after=e.retry_after, code=e.code),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            view=build_link_card(
                locale,
                url=result.get("authorization_url", ""),
                expires_at=result.get("expires_at"),
            ),
            ephemeral=True,
        )


# ---------------------------------------------------------------------- #
# Ephemeral result cards (no interactive children beyond link buttons)
# ---------------------------------------------------------------------- #

def _parse_expiry(expires_at: Optional[str]) -> Optional[int]:
    """ISO 8601 -> unix seconds, for a Discord relative timestamp."""
    if not expires_at:
        return None
    try:
        moment = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp())


def build_link_card(locale: str, *, url: str, expires_at: Optional[str] = None) -> ui.LayoutView:
    """The ephemeral card carrying the member's personal verification link."""
    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=PANEL_ACCENT)

    container.add_item(ui.TextDisplay(
        f"### {ALTGUARD} {t('modules.altguard.link.title', locale=locale)}"
    ))
    container.add_item(ui.TextDisplay(
        t('modules.altguard.link.description', locale=locale)
    ))

    expiry = _parse_expiry(expires_at)
    if expiry:
        container.add_item(ui.TextDisplay(
            f"{TIME} {t('modules.altguard.link.expires', locale=locale, timestamp=f'<t:{expiry}:R>')}"
        ))

    if url:
        row = ui.ActionRow()
        row.add_item(ui.Button(
            label=t('modules.altguard.link.button', locale=locale),
            style=discord.ButtonStyle.link,
            url=url,
        ))
        container.add_item(row)

    container.add_item(ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
    container.add_item(ui.TextDisplay(
        f"-# {t('modules.altguard.link.footer', locale=locale)}"
    ))

    view.add_item(container)
    return view


def build_error_card(locale: str, key: str, *, retry_after: Optional[int] = None,
                     code: Optional[str] = None) -> ui.LayoutView:
    """An ephemeral failure card. `key` selects the i18n message.

    ``code`` is the raw :class:`~services.altguard_client.AltGuardError` code.
    It is shown as a discreet footer because "the service is not answering"
    covers half a dozen very different causes (missing secret, rejected token,
    network, 5xx) and a member reporting the problem to a moderator should be
    able to say which one. It names no internal host and leaks no signal about
    the detection itself.
    """
    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=ERROR_ACCENT)

    container.add_item(ui.TextDisplay(
        f"### {ERROR} {t(f'modules.altguard.errors.{key}.title', locale=locale)}"
    ))
    description = t(f'modules.altguard.errors.{key}.description', locale=locale)
    if key == 'rate_limited' and retry_after:
        description += "\n" + t(
            'modules.altguard.errors.rate_limited.retry', locale=locale, seconds=retry_after,
        )
    container.add_item(ui.TextDisplay(description))

    if code:
        container.add_item(ui.TextDisplay(
            f"-# {t('modules.altguard.errors.code', locale=locale, code=f'`{code}`')}"
        ))

    view.add_item(container)
    return view


def build_already_verified_card(locale: str) -> ui.LayoutView:
    """Ephemeral answer for a member who is already through the gate."""
    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=SUCCESS_ACCENT)
    container.add_item(ui.TextDisplay(
        f"### {DONE} {t('modules.altguard.already_verified.title', locale=locale)}"
    ))
    container.add_item(ui.TextDisplay(
        t('modules.altguard.already_verified.description', locale=locale)
    ))
    view.add_item(container)
    return view


# ---------------------------------------------------------------------- #
# Member DM (the outcome of the verification)
# ---------------------------------------------------------------------- #

# kind -> (emoji, accent). `flagged` and `blocked` deliberately share a wording
# that says what happens next without saying what was detected.
_DM_STYLE = {
    "passed": (DONE, SUCCESS_ACCENT),
    "flagged": (WARNING, WARNING_ACCENT),
    "blocked": (ERROR, ERROR_ACCENT),
    "manual_verify": (DONE, SUCCESS_ACCENT),
    "manual_unverify": (WARNING, WARNING_ACCENT),
}


def build_member_dm(*, locale: str, kind: str, guild_name: str = "") -> Optional[ui.LayoutView]:
    """The DM telling a member how their verification ended.

    Returns ``None`` for an unknown kind rather than sending a broken card.
    Never carries the score or the matched signals: the member is exactly the
    person those must not reach.
    """
    style = _DM_STYLE.get(kind)
    if style is None:
        return None
    emoji, accent = style

    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=accent)

    container.add_item(ui.TextDisplay(
        f"### {emoji} {t(f'modules.altguard.dm.{kind}.title', locale=locale)}"
    ))
    container.add_item(ui.TextDisplay(
        t(f'modules.altguard.dm.{kind}.description', locale=locale,
          server=f"**{guild_name}**" if guild_name else "")
    ))

    container.add_item(ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
    container.add_item(ui.TextDisplay(
        f"-# {t(f'modules.altguard.dm.{kind}.footer', locale=locale)}"
    ))

    view.add_item(container)
    return view


# ---------------------------------------------------------------------- #
# Shared name rendering
# ---------------------------------------------------------------------- #

async def format_member_name(bot, user: discord.abc.User) -> str:
    """``**display name**badge`` per the verification-badge rule in CLAUDE.md.

    Falls back to the plain bold name when the database is unreachable — a
    missing badge is a cosmetic loss, an exception here would break a
    moderation command.
    """
    attributes: dict = {}
    verification: Optional[dict] = None
    if getattr(bot, "db", None):
        try:
            row = await bot.db.get_user(user.id)
            attributes = (row or {}).get('attributes', {}) or {}
            verification = ((row or {}).get('data', {}) or {}).get('verification')
        except Exception as e:
            logger.debug(f"[AltGuard] Could not load attributes for {user.id}: {e}")

    name = getattr(user, "global_name", None) or user.name
    try:
        public_flags = user.public_flags.value
    except Exception:
        public_flags = 0

    badge, _org_names, _tier = get_user_verification_badge(
        {"public_flags": public_flags}, attributes, verification,
    )
    return f"**{name}**{format_verification_badge(badge)}"


# ---------------------------------------------------------------------- #
# Log channel cards
# ---------------------------------------------------------------------- #

def build_log_card(
    *,
    locale: str,
    kind: str,
    user_id: int,
    verdict: Optional[str] = None,
    score: Optional[int] = None,
    reasons: Optional[List[str]] = None,
    matches: Optional[List[dict]] = None,
    enforced: bool = True,
    enforced_missing: bool = False,
    verification_id: Optional[str] = None,
    actor_id: Optional[int] = None,
    moddy_staff: bool = False,
) -> ui.LayoutView:
    """A card for the optional log channel.

    Score, reasons and matches appear **here only** — the guild's moderators
    are the audience. They are never shown to the verified member, who would
    otherwise learn exactly which signal to defeat next time.
    """
    view = ui.LayoutView(timeout=None)

    if kind == "verdict":
        emoji, accent = _VERDICT_STYLE.get(verdict or "", (INFO, NEUTRAL_ACCENT))
        container = ui.Container(accent_colour=accent if enforced else NEUTRAL_ACCENT)
        container.add_item(ui.TextDisplay(
            f"### {emoji} {t(f'modules.altguard.logs.verdict.{verdict}', locale=locale)}"
        ))
        lines = [
            f"**{t('modules.altguard.logs.member', locale=locale)}** <@{user_id}> (`{user_id}`)",
        ]
        if score is not None:
            lines.append(f"**{t('modules.altguard.logs.score', locale=locale)}** `{score}`")
        if reasons:
            formatted = ", ".join(f"`{reason}`" for reason in reasons)
            lines.append(f"**{t('modules.altguard.logs.reasons', locale=locale)}** {formatted}")
        if verification_id:
            lines.append(
                f"**{t('modules.altguard.logs.reference', locale=locale)}** `{verification_id}`"
            )
        container.add_item(ui.TextDisplay("\n".join(lines)))

        if matches:
            match_lines = [f"**{t('modules.altguard.logs.matches', locale=locale)}**"]
            for match in matches:
                match_reasons = ", ".join(f"`{r}`" for r in match.get("reasons", []))
                line = f"- <@{match['user_id']}> (`{match['user_id']}`)"
                if match.get("score") is not None:
                    line += f" — {t('modules.altguard.logs.score', locale=locale)} `{match['score']}`"
                if match_reasons:
                    line += f" — {match_reasons}"
                match_lines.append(line)
            container.add_item(ui.TextDisplay("\n".join(match_lines)))

        if not enforced:
            container.add_item(ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
            # "Shadow mode" is the right wording only when the service said so
            # deliberately. A missing field is a service-side defect that looks
            # identical to a moderator, so it gets its own line.
            key = ('modules.altguard.logs.enforced_missing' if enforced_missing
                   else 'modules.altguard.logs.shadow')
            container.add_item(ui.TextDisplay(f"-# {t(key, locale=locale)}"))

        view.add_item(container)
        return view

    # Manual staff decision.
    is_verify = kind == "manual_verify"
    container = ui.Container(accent_colour=SUCCESS_ACCENT if is_verify else WARNING_ACCENT)
    container.add_item(ui.TextDisplay(
        f"### {MANAGE_USER} "
        f"{t(f'modules.altguard.logs.{kind}', locale=locale)}"
    ))
    actor_label = t(
        'modules.altguard.logs.actor_moddy' if moddy_staff else 'modules.altguard.logs.actor_server',
        locale=locale,
    )
    container.add_item(ui.TextDisplay(
        f"**{t('modules.altguard.logs.member', locale=locale)}** <@{user_id}> (`{user_id}`)\n"
        f"**{actor_label}** <@{actor_id}> (`{actor_id}`)"
    ))
    view.add_item(container)
    return view


__all__ = [
    "AltGuardPanelView", "AltGuardConsentModal", "build_verification_panel",
    "build_link_card", "build_error_card", "build_log_card", "format_member_name",
    "build_member_dm", "build_already_verified_card",
    "DATA_NOTICE_URL", "TERMS_URL", "PRIVACY_URL",
]
