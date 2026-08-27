"""
Support request UI — the staff card, the reply flow, and the buttons users click.

Three surfaces, one request behind them:

* **the staff card** posted in the bug-report / configuration-help channel,
  with Claim, Reply and Resolve — the whole exchange is on it, so a second
  staffer never has to scroll to know where things stand;
* **the reply DM** the reporter receives (through the notification system,
  like everything else Moddy sends), carrying a Reply button so the exchange
  can actually be a conversation;
* **the "Configure it for me" button** Moddy puts under its own announcements
  and under ``/config``'s help — whoever clicks it *is* the requester, which
  is why it needs no owner state.

Persistence
-----------
Every button is a :class:`discord.ui.DynamicItem` whose ``custom_id`` carries
the request uuid (or nothing at all, for the entry-point button), registered
through :class:`SupportPersistence`. A request opened today must still be
answerable after next week's deploy. Modals are one-shot, per the documented
exclusion in docs/PERSISTENT_VIEWS.md.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import discord
from discord import ui

import config
from cogs.error_handler import BaseView, BaseModal
from db.repositories.support_requests import (
    AUTHOR_STAFF, KIND_BUG, KIND_CONFIG_HELP,
    STATUS_CLAIMED, STATUS_OPEN, STATUS_RESOLVED,
)
from utils.components_v2 import create_error_message, create_success_message
from utils.emojis import (
    BUG, BUILD, DONE, GROUPS, HAND, NOTE, REPLY, SUPPORT, TIME, USER,
)
from utils.i18n import i18n, t

logger = logging.getLogger("moddy.support_request_views")

#: UUID fragment shared by every custom_id template below.
_UUID = r"[0-9a-fA-F-]{36}"

#: Accent colour of a card, by status.
_STATUS_ACCENT = {
    STATUS_OPEN: 0x3661FF,
    STATUS_CLAIMED: 0xFEE75C,
    STATUS_RESOLVED: 0x57F287,
}

_KIND_EMOJI = {KIND_BUG: BUG, KIND_CONFIG_HELP: BUILD}


def _guarded(callback):
    """Route unknown errors from a dynamic item to the central error handler.

    Dynamic items dispatched through ``add_dynamic_items`` have no live
    ``BaseView``, so ``BaseView.on_error`` never fires and an unwrapped
    exception would vanish. Same pattern as ``utils/notification_views.py``.
    """
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as exc:  # noqa: BLE001 — funnel everything to the handler
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, exc, self.__class__.__name__)
    return wrapper


def shorten(text: str, limit: int = 80) -> str:
    """Trim a value to what a Discord label/description can actually hold."""
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


#: Internal alias kept for readability inside this module.
_short = shorten


# =========================================================================== #
# Staff card
# =========================================================================== #

def build_request_card(
    *, request: Dict[str, Any], messages: Optional[List[Dict[str, Any]]] = None,
    user: Optional[discord.abc.User] = None, locale: str = "en-US",
) -> BaseView:
    """The card staff act on, in the request's channel."""
    kind = request["kind"]
    status = request.get("status") or STATUS_OPEN
    messages = messages or []

    view = BaseView()
    container = ui.Container(accent_colour=discord.Colour(
        _STATUS_ACCENT.get(status, 0x3661FF)))

    container.add_item(ui.TextDisplay(
        f"### {_KIND_EMOJI.get(kind, NOTE)} {t(f'support.card.{kind}.title', locale=locale)}"))
    container.add_item(ui.TextDisplay(
        f"{t('support.card.status_label', locale=locale)} "
        f"**{t(f'support.status.{status}', locale=locale)}**"))

    container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

    name = getattr(user, "global_name", None) or getattr(user, "name", None)
    lines = [
        f"{USER} **{t('support.card.reporter', locale=locale)}** "
        + (f"**{name}** " if name else "")
        + f"<@{request['user_id']}> (`{request['user_id']}`)",
    ]
    if request.get("guild_name") or request.get("guild_id"):
        server = request.get("guild_name") or t("support.card.unknown_guild", locale=locale)
        lines.append(f"{GROUPS} **{t('support.card.server', locale=locale)}** {server}"
                     + (f" (`{request['guild_id']}`)" if request.get("guild_id") else ""))
    if request.get("created_at") is not None:
        lines.append(f"{TIME} **{t('support.card.opened_at', locale=locale)}** "
                     f"<t:{int(request['created_at'].timestamp())}:f>")
    container.add_item(ui.TextDisplay("\n".join(lines)))

    if request.get("subject"):
        container.add_item(ui.TextDisplay(
            f"**{t('support.card.subject', locale=locale)}**\n{_short(request['subject'], 200)}"))
    if request.get("body"):
        container.add_item(ui.TextDisplay(
            f"**{t('support.card.description', locale=locale)}**\n{_short(request['body'], 1200)}"))

    for key, value in (request.get("details") or {}).items():
        if not value:
            continue
        container.add_item(ui.TextDisplay(
            f"**{t(f'support.card.details.{key}', locale=locale)}**\n"
            f"{_short(str(value), 600)}"))

    if messages:
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(
            f"**{t('support.card.exchange', locale=locale)}**"))
        # The last few turns only: the card is a working surface, and the full
        # history is one query away for whoever needs it.
        for entry in messages[-4:]:
            who = (t("support.card.staff", locale=locale) if entry["author"] == AUTHOR_STAFF
                   else t("support.card.user", locale=locale))
            container.add_item(ui.TextDisplay(
                f"-# {REPLY} **{who}** <@{entry['author_id']}> · "
                f"{_short(entry['body'], 300)}"))

    if request.get("claimed_by"):
        container.add_item(ui.TextDisplay(
            f"-# {HAND} {t('support.card.claimed_by', locale=locale)} <@{request['claimed_by']}>"))
    if request.get("resolved_by"):
        container.add_item(ui.TextDisplay(
            f"-# {DONE} {t('support.card.resolved_by', locale=locale)} <@{request['resolved_by']}>"))

    container.add_item(ui.TextDisplay(
        f"-# {t('support.card.reference', locale=locale)} `{request['id']}`"))
    view.add_item(container)

    request_id = str(request["id"])
    resolved = status == STATUS_RESOLVED
    row = ui.ActionRow()
    row.add_item(SupportClaimButton(
        request_id, claimed=bool(request.get("claimed_by")) or resolved, locale=locale))
    row.add_item(SupportReplyButton(request_id, disabled=resolved, locale=locale))
    row.add_item(SupportResolveButton(request_id, disabled=resolved, locale=locale))
    view.add_item(row)

    return view


def build_followup_notice(*, request: Dict[str, Any], user: discord.abc.User,
                          body: str, locale: str = "en-US") -> BaseView:
    """One short card under the request's own card when the reporter answers."""
    view = BaseView()
    container = ui.Container(accent_colour=discord.Colour(0x3661FF))
    container.add_item(ui.TextDisplay(
        f"### {REPLY} {t('support.followup.title', locale=locale)}"))
    container.add_item(ui.TextDisplay(
        f"{USER} <@{user.id}> (`{user.id}`)\n{_short(body, 1200)}"))
    container.add_item(ui.TextDisplay(
        f"-# {t('support.card.reference', locale=locale)} `{request['id']}`"))
    view.add_item(container)
    return view


# =========================================================================== #
# The DM the reporter receives
# =========================================================================== #

def build_reply_dm(*, request: Dict[str, Any], body: str,
                   locale: str = "en-US") -> BaseView:
    """A staff answer, as the reporter sees it.

    The attribution line the notification service appends closes the card, so
    nothing here says who sent it — that sentence is the one place it belongs.
    """
    kind = request["kind"]
    view = BaseView()
    container = ui.Container(accent_colour=discord.Colour(0x3661FF))
    container.add_item(ui.TextDisplay(
        f"### {SUPPORT} {t(f'support.reply.{kind}.title', locale=locale)}"))
    if request.get("subject"):
        container.add_item(ui.TextDisplay(
            f"-# {t('support.card.subject', locale=locale)} "
            f"**{_short(request['subject'], 200)}**"))
    container.add_item(ui.TextDisplay(body))
    container.add_item(ui.TextDisplay(
        f"-# {t('support.reply.reference', locale=locale, reference=request['id'])}"))
    view.add_item(container)

    row = ui.ActionRow()
    row.add_item(SupportUserReplyButton(str(request["id"]), locale=locale))
    row.add_item(ui.Button(
        label=_short(t("support.links.support", locale=locale)),
        style=discord.ButtonStyle.link, url=config.SUPPORT_URL))
    view.add_item(row)
    return view


# =========================================================================== #
# Staff buttons (persistent)
# =========================================================================== #

async def _load_request(interaction: discord.Interaction, request_id: str, locale: str):
    service = getattr(interaction.client, "support_requests", None)
    request = await service.get(request_id) if service else None
    if not request:
        await interaction.response.send_message(view=create_error_message(
            t("support.errors.unknown.title", locale=locale),
            t("support.errors.unknown.description", locale=locale, reference=request_id),
        ), ephemeral=True)
        return None
    return request


async def _guard_staff(interaction: discord.Interaction, locale: str) -> bool:
    """Staff node — re-checked on every single click, never trusted from state."""
    from utils.staff_permissions import has_staff_node

    if await has_staff_node(interaction.client, interaction.user.id, "support_request"):
        return True
    await interaction.response.send_message(view=create_error_message(
        t("support.errors.no_permission.title", locale=locale),
        t("support.errors.no_permission.description", locale=locale),
    ), ephemeral=True)
    return False


class SupportClaimButton(
    ui.DynamicItem[ui.Button],
    template=rf"moddy:support:claim:(?P<request>{_UUID})",
):
    """Take ownership so two staffers don't answer the same request."""

    def __init__(self, request_id: str, *, claimed: bool = False, locale: str = "en-US"):
        key = "support.buttons.claimed" if claimed else "support.buttons.claim"
        super().__init__(ui.Button(
            label=_short(t(key, locale=locale)),
            style=discord.ButtonStyle.secondary if claimed else discord.ButtonStyle.primary,
            emoji=discord.PartialEmoji.from_str(HAND),
            custom_id=f"moddy:support:claim:{request_id}",
            disabled=claimed,
        ))
        self.request_id = str(request_id)

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["request"])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        request = await _load_request(interaction, self.request_id, locale)
        if not request or not await _guard_staff(interaction, locale):
            return
        await interaction.response.defer()
        claimed = await interaction.client.support_requests.claim(
            self.request_id, interaction.user)
        if claimed is None:
            await interaction.followup.send(view=create_error_message(
                t("support.errors.already_claimed.title", locale=locale),
                t("support.errors.already_claimed.description", locale=locale),
            ), ephemeral=True)


class SupportReplyButton(
    ui.DynamicItem[ui.Button],
    template=rf"moddy:support:reply:(?P<request>{_UUID})",
):
    """Answer the reporter. The answer travels as a notification, not a raw DM."""

    def __init__(self, request_id: str, *, disabled: bool = False, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=_short(t("support.buttons.reply", locale=locale)),
            style=discord.ButtonStyle.success,
            emoji=discord.PartialEmoji.from_str(REPLY),
            custom_id=f"moddy:support:reply:{request_id}",
            disabled=disabled,
        ))
        self.request_id = str(request_id)

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["request"])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        request = await _load_request(interaction, self.request_id, locale)
        if not request or not await _guard_staff(interaction, locale):
            return
        await interaction.response.send_modal(
            SupportReplyModal(request_id=self.request_id, locale=locale))


class SupportResolveButton(
    ui.DynamicItem[ui.Button],
    template=rf"moddy:support:resolve:(?P<request>{_UUID})",
):
    """Close the request and tell the reporter it is closed."""

    def __init__(self, request_id: str, *, disabled: bool = False, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=_short(t("support.buttons.resolve", locale=locale)),
            style=discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(DONE),
            custom_id=f"moddy:support:resolve:{request_id}",
            disabled=disabled,
        ))
        self.request_id = str(request_id)

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["request"])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        request = await _load_request(interaction, self.request_id, locale)
        if not request or not await _guard_staff(interaction, locale):
            return
        await interaction.response.defer()
        resolved = await interaction.client.support_requests.resolve(
            self.request_id, interaction.user)
        if resolved is None:
            await interaction.followup.send(view=create_error_message(
                t("support.errors.already_resolved.title", locale=locale),
                t("support.errors.already_resolved.description", locale=locale),
            ), ephemeral=True)


class SupportReplyModal(BaseModal):
    """What the reporter will read, word for word."""

    def __init__(self, *, request_id: str, locale: str = "en-US"):
        super().__init__(title=_short(t("support.reply.modal.title", locale=locale), 45))
        self.request_id = request_id
        self.locale = locale

        self.body = ui.Label(
            text=_short(t("support.reply.modal.body.label", locale=locale), 45),
            description=_short(t("support.reply.modal.body.description", locale=locale), 100),
            component=ui.TextInput(
                style=discord.TextStyle.paragraph,
                placeholder=_short(
                    t("support.reply.modal.body.placeholder", locale=locale), 100),
                max_length=2000, required=True,
            ),
        )
        self.add_item(self.body)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        body = (self.body.component.value or "").strip()
        ok = await interaction.client.support_requests.reply(
            request_id=self.request_id, staff=interaction.user, body=body)
        if ok:
            await interaction.followup.send(view=create_success_message(
                t("support.reply.sent.title", locale=self.locale),
                t("support.reply.sent.description", locale=self.locale),
            ), ephemeral=True)
        else:
            await interaction.followup.send(view=create_error_message(
                t("support.errors.reply_failed.title", locale=self.locale),
                t("support.errors.reply_failed.description", locale=self.locale),
            ), ephemeral=True)


# =========================================================================== #
# Reporter buttons (persistent)
# =========================================================================== #

class SupportUserReplyButton(
    ui.DynamicItem[ui.Button],
    template=rf"moddy:support:ureply:(?P<request>{_UUID})",
):
    """The reporter answering their own request, from the DM.

    Auth is the request itself: the service refuses a follow-up whose author is
    not the request's owner, so the button carries no owner id of its own.
    """

    def __init__(self, request_id: str, *, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=_short(t("support.buttons.user_reply", locale=locale)),
            style=discord.ButtonStyle.primary,
            emoji=discord.PartialEmoji.from_str(REPLY),
            custom_id=f"moddy:support:ureply:{request_id}",
        ))
        self.request_id = str(request_id)

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["request"])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        request = await _load_request(interaction, self.request_id, locale)
        if not request:
            return
        if request["user_id"] != interaction.user.id:
            await interaction.response.send_message(view=create_error_message(
                t("support.errors.not_yours.title", locale=locale),
                t("support.errors.not_yours.description", locale=locale),
            ), ephemeral=True)
            return
        if request.get("status") == STATUS_RESOLVED:
            await interaction.response.send_message(view=create_error_message(
                t("support.errors.closed.title", locale=locale),
                t("support.errors.closed.description", locale=locale),
            ), ephemeral=True)
            return
        await interaction.response.send_modal(
            SupportUserReplyModal(request_id=self.request_id, locale=locale))


class SupportUserReplyModal(BaseModal):
    """A follow-up from the reporter — it lands under the staff card."""

    def __init__(self, *, request_id: str, locale: str = "en-US"):
        super().__init__(title=_short(t("support.user_reply.modal.title", locale=locale), 45))
        self.request_id = request_id
        self.locale = locale

        self.body = ui.Label(
            text=_short(t("support.user_reply.modal.body.label", locale=locale), 45),
            component=ui.TextInput(
                style=discord.TextStyle.paragraph,
                placeholder=_short(
                    t("support.user_reply.modal.body.placeholder", locale=locale), 100),
                max_length=2000, required=True,
            ),
        )
        self.add_item(self.body)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok = await interaction.client.support_requests.user_followup(
            request_id=self.request_id, user=interaction.user,
            body=(self.body.component.value or "").strip())
        if ok:
            await interaction.followup.send(view=create_success_message(
                t("support.user_reply.sent.title", locale=self.locale),
                t("support.user_reply.sent.description", locale=self.locale),
            ), ephemeral=True)
        else:
            await interaction.followup.send(view=create_error_message(
                t("support.errors.reply_failed.title", locale=self.locale),
                t("support.errors.reply_failed.description", locale=self.locale),
            ), ephemeral=True)


# =========================================================================== #
# "Configure it for me" — the entry point Moddy puts under its announcements
# =========================================================================== #

class ConfigHelpButton(
    ui.DynamicItem[ui.Button],
    template=r"moddy:support:confighelp(?::(?P<guild>\d+))?",
):
    """Ask the team to set Moddy up for you.

    Public by design: whoever clicks *is* the person asking, so there is no
    owner to check. ``guild`` is optional — the button is usually clicked from
    a DM, where the modal asks which server instead.
    """

    def __init__(self, *, guild_id: Optional[int] = None, locale: str = "en-US"):
        custom_id = "moddy:support:confighelp"
        if guild_id:
            custom_id = f"{custom_id}:{guild_id}"
        super().__init__(ui.Button(
            label=_short(t("support.buttons.config_help", locale=locale)),
            style=discord.ButtonStyle.primary,
            emoji=discord.PartialEmoji.from_str(BUILD),
            custom_id=custom_id,
        ))
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        guild = match["guild"]
        return cls(guild_id=int(guild) if guild else None)

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        service = getattr(interaction.client, "support_requests", None)
        if service is not None and await service.is_rate_limited(
            interaction.user.id, KIND_CONFIG_HELP
        ):
            await interaction.response.send_message(view=create_error_message(
                t("support.errors.rate_limited.title", locale=locale),
                t("support.errors.rate_limited.description", locale=locale),
            ), ephemeral=True)
            return

        guild_id = self.guild_id or interaction.guild_id
        guild = interaction.client.get_guild(guild_id) if guild_id else None
        await interaction.response.send_modal(
            ConfigHelpModal(locale=locale, guild=guild))


class ConfigHelpModal(BaseModal):
    """What the team needs to know before configuring a server for someone."""

    def __init__(self, *, locale: str = "en-US", guild: Optional[discord.Guild] = None):
        super().__init__(title=_short(t("support.config_help.modal.title", locale=locale), 45))
        self.locale = locale
        self.guild = guild

        self.add_item(ui.TextDisplay(t("support.config_help.modal.intro", locale=locale)))

        self.server = ui.Label(
            text=_short(t("support.config_help.modal.server.label", locale=locale), 45),
            description=_short(
                t("support.config_help.modal.server.description", locale=locale), 100),
            component=ui.TextInput(
                style=discord.TextStyle.short, max_length=120,
                default=(guild.name if guild else None),
                # Required only when the click carried no server of its own —
                # from a DM there is nothing to prefill and nothing to guess.
                required=guild is None,
                placeholder=_short(
                    t("support.config_help.modal.server.placeholder", locale=locale), 100),
            ),
        )
        self.needs = ui.Label(
            text=_short(t("support.config_help.modal.needs.label", locale=locale), 45),
            description=_short(
                t("support.config_help.modal.needs.description", locale=locale), 100),
            component=ui.TextInput(
                style=discord.TextStyle.paragraph, max_length=1500, required=True,
                placeholder=_short(
                    t("support.config_help.modal.needs.placeholder", locale=locale), 100),
            ),
        )
        self.availability = ui.Label(
            text=_short(t("support.config_help.modal.availability.label", locale=locale), 45),
            description=_short(
                t("support.config_help.modal.availability.description", locale=locale), 100),
            component=ui.TextInput(
                style=discord.TextStyle.short, max_length=200, required=False,
            ),
        )
        self.add_item(self.server)
        self.add_item(self.needs)
        self.add_item(self.availability)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        service = getattr(interaction.client, "support_requests", None)
        if service is None:
            await interaction.followup.send(view=create_error_message(
                t("support.errors.unavailable.title", locale=self.locale),
                t("support.errors.unavailable.description", locale=self.locale),
            ), ephemeral=True)
            return

        request = await service.open_request(
            kind=KIND_CONFIG_HELP,
            user=interaction.user,
            guild=self.guild,
            guild_name=(self.server.component.value or "").strip() or None,
            locale=self.locale,
            body=(self.needs.component.value or "").strip(),
            details={"availability": (self.availability.component.value or "").strip()},
        )
        if request is None:
            await interaction.followup.send(view=create_error_message(
                t("support.errors.unavailable.title", locale=self.locale),
                t("support.errors.unavailable.description", locale=self.locale),
            ), ephemeral=True)
            return

        await interaction.followup.send(
            view=build_receipt(kind=KIND_CONFIG_HELP, request=request, locale=self.locale),
            ephemeral=True)


def build_receipt(*, kind: str, request: Dict[str, Any], locale: str) -> BaseView:
    """What the requester sees right after sending — the reference included,
    because it is the only handle they have on the exchange."""
    view = BaseView()
    container = ui.Container(accent_colour=discord.Colour(0x57F287))
    container.add_item(ui.TextDisplay(
        f"### {DONE} {t(f'support.receipt.{kind}.title', locale=locale)}"))
    container.add_item(ui.TextDisplay(
        t(f"support.receipt.{kind}.description", locale=locale)))
    container.add_item(ui.TextDisplay(
        f"-# {t('support.card.reference', locale=locale)} `{request['id']}`"))
    view.add_item(container)

    row = ui.ActionRow()
    row.add_item(ui.Button(
        label=_short(t("support.links.support", locale=locale)),
        style=discord.ButtonStyle.link, url=config.SUPPORT_URL))
    if kind == KIND_CONFIG_HELP:
        row.add_item(ui.Button(
            label=_short(t("support.links.dashboard", locale=locale)),
            style=discord.ButtonStyle.link, url=config.DASHBOARD_URL))
    else:
        row.add_item(ui.Button(
            label=_short(t("support.links.docs", locale=locale)),
            style=discord.ButtonStyle.link, url=config.DOCS_URL))
    view.add_item(row)
    return view


# =========================================================================== #
# Persistence
# =========================================================================== #

class SupportPersistence(BaseView):
    """Marker view: registers every support-request dynamic item at startup.

    The buttons themselves live on cards and DMs built per request, so what
    must survive a restart is the ``custom_id`` templates, not one instance.
    """

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(
            SupportClaimButton,
            SupportReplyButton,
            SupportResolveButton,
            SupportUserReplyButton,
            ConfigHelpButton,
        )
