"""
"Transfer to Moddy" — the user-facing half of the mini-bots handoff.

Where a server admin meets it:

- **in a DM**, right after Moddy is added to a server where a mini-bot already
  runs one of its modules (:func:`offer_transfer`, sent to the installer
  through the notification system);
- **in /config**: a banner on the main screen, and the screen shown instead of
  a module's own when that module belongs to a mini-bot
  (:func:`build_external_module_view`).

Every path ends on the same persistent button,
``moddy:minibots:transfer:<guild_id>:<app>``: it moves every module ``app``
still runs in that server to Moddy (``bot.module_transfer``). Nothing moves
without that click — the handoff is manual by design.

Auth, re-derived on every click: *Manage Server* in the target server. From a
DM the server comes from the custom_id and the member is looked up there.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, Iterable, List, Optional

import discord
from discord import ui

from cogs.error_handler import BaseView
from minibots.ownership import APP_ID_PATTERN
from minibots.spec import app_display_name
from notifications.models import ContentAuthor, NotificationContent, NotificationSource
from utils.emojis import BACK, DONE, ERROR, INFO, MODDY, SYNC
from utils.i18n import i18n, t

logger = logging.getLogger("moddy.minibot_transfer")

#: Moddy blue, like the install welcome.
ACCENT = 0x3661FF

_CID_TRANSFER = "moddy:minibots:transfer"
_CID_BACK = "moddy:minibots:config_back"


def _guarded(callback):
    """Route unknown errors of a dynamic item to the central error handler."""
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as exc:  # noqa: BLE001 — funnel everything to the handler
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, exc, self.__class__.__name__)
    return wrapper


def module_label(bot, module_id: str, locale: str) -> str:
    """Display name of a module (its /config name)."""
    manager = getattr(bot, "module_manager", None)
    module_class = getattr(manager, "registered_modules", {}).get(module_id)
    return module_class.MODULE_NAME if module_class is not None else module_id


def _module_list(bot, module_ids: Iterable[str], locale: str) -> str:
    return ", ".join(f"`{module_label(bot, m, locale)}`" for m in sorted(module_ids))


def group_by_app(foreign: Dict[str, str]) -> Dict[str, List[str]]:
    """``{module_id: app}`` -> ``{app: [module_id, ...]}`` (sorted)."""
    grouped: Dict[str, List[str]] = {}
    for module_id, app in foreign.items():
        grouped.setdefault(app, []).append(module_id)
    return {app: sorted(mods) for app, mods in sorted(grouped.items())}


# --------------------------------------------------------------------------- #
# The button
# --------------------------------------------------------------------------- #

class TransferToModdyButton(
    ui.DynamicItem[ui.Button],
    template=rf"{_CID_TRANSFER}:(?P<guild>\d{{15,21}}):(?P<app>{APP_ID_PATTERN})",
):
    """Move everything ``app`` runs in ``guild`` to Moddy."""

    def __init__(self, guild_id: int, app: str, *, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=t("minibots.transfer.button", locale=locale)[:80],
            style=discord.ButtonStyle.primary,
            emoji=discord.PartialEmoji.from_str(SYNC),
            custom_id=f"{_CID_TRANSFER}:{guild_id}:{app}",
        ))
        self.guild_id = guild_id
        self.app = app

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["guild"]), match["app"])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        in_config = interaction.guild_id == self.guild_id

        if not await can_manage(interaction, self.guild_id):
            await interaction.response.send_message(
                view=result_card(locale, t("minibots.transfer.errors.title", locale=locale),
                                 t("minibots.transfer.errors.forbidden", locale=locale),
                                 ok=False),
                ephemeral=True)
            return

        await interaction.response.defer(ephemeral=in_config, thinking=False)
        service = getattr(bot, "module_transfer", None)
        if service is None:
            return
        results = await service.transfer_app(
            self.guild_id, self.app, actor_id=interaction.user.id,
            via="config" if in_config else "dm")

        card = summary_card(bot, locale, self.app, results)
        if in_config:
            from modules.configs._common import open_main_config
            await open_main_config(interaction)
            await interaction.followup.send(view=card, ephemeral=True)
        else:
            # The DM card becomes the outcome: no button left to click twice.
            await interaction.edit_original_response(view=card)


class ConfigBackButton(ui.DynamicItem[ui.Button], template=_CID_BACK):
    """Back to the /config main screen from the external-module screen."""

    def __init__(self, *, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=t("modules.config.buttons.back", locale=locale)[:80],
            style=discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(BACK),
            custom_id=_CID_BACK,
        ))

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls()

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        from modules.configs._common import check_guild_perms, open_main_config
        if not await check_guild_perms(interaction):
            return
        await open_main_config(interaction)


async def can_manage(interaction: discord.Interaction, guild_id: int) -> bool:
    """*Manage Server* in ``guild_id``, wherever the click comes from."""
    if interaction.guild_id == guild_id and interaction.permissions is not None:
        return interaction.permissions.manage_guild
    guild = interaction.client.get_guild(guild_id)
    if guild is None:
        return False
    if guild.owner_id == interaction.user.id:
        return True
    from utils.members import get_or_fetch_member
    member = await get_or_fetch_member(guild, interaction.user.id)
    return bool(member and member.guild_permissions.manage_guild)


class MinibotTransferPersistence(BaseView):
    """Marker view: registers the transfer and back buttons at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(TransferToModdyButton, ConfigBackButton)


# --------------------------------------------------------------------------- #
# Cards
# --------------------------------------------------------------------------- #

def result_card(locale: str, title: str, body: str, *, ok: bool) -> BaseView:
    view = BaseView()
    container = ui.Container(accent_colour=discord.Colour(0x57F287 if ok else 0xED4245))
    container.add_item(ui.TextDisplay(f"### {DONE if ok else ERROR} {title}"))
    container.add_item(ui.TextDisplay(body))
    view.add_item(container)
    return view


def summary_card(bot, locale: str, app: str, results) -> BaseView:
    """Outcome of a transfer, one line per module."""
    name = app_display_name(app)
    if not results:
        return result_card(locale, t("minibots.transfer.done.title", locale=locale),
                           t("minibots.transfer.done.nothing", locale=locale, app=name), ok=True)
    lines = []
    for result in results:
        label = module_label(bot, result.module_id, locale)
        if result.ok:
            lines.append(f"{DONE} `{label}`")
        else:
            reason = t(f"minibots.transfer.reasons.{result.error}", locale=locale) \
                if result.error else ""
            lines.append(f"{ERROR} `{label}` — {reason}")
    all_ok = all(r.ok for r in results)
    title_key = "minibots.transfer.done.title" if all_ok else "minibots.transfer.done.partial_title"
    body = t("minibots.transfer.done.body", locale=locale, app=name) + "\n" + "\n".join(lines)
    return result_card(locale, t(title_key, locale=locale), body, ok=all_ok)


def transfer_content(locale: str) -> NotificationContent:
    """Uniform payload of the DM offer (placeholders kept, rule #11)."""
    return NotificationContent(
        title=t("minibots.transfer.offer.title", locale=locale),
        body=t("minibots.transfer.offer.body", locale=locale,
               app="{app}", server="{server}", modules="{modules}"),
        icon=MODDY,
        accent_color=ACCENT,
        template_id="minibots.transfer.offer",
    )


def build_offer_view(bot, guild: discord.Guild, app: str, module_ids: List[str],
                     locale: str) -> BaseView:
    content = transfer_content(locale).render({
        "app": app_display_name(app), "server": guild.name,
        "modules": _module_list(bot, module_ids, locale),
    })
    view = BaseView()
    container = ui.Container(accent_colour=discord.Colour(ACCENT))
    container.add_item(ui.TextDisplay(f"### {content.icon} {content.title}"))
    container.add_item(ui.TextDisplay(content.body))
    container.add_item(ui.TextDisplay(f"-# {t('minibots.transfer.offer.hint', locale=locale)}"))
    container.add_item(ui.ActionRow(TransferToModdyButton(guild.id, app, locale=locale)))
    view.add_item(container)
    return view


async def offer_transfer(bot, guild: discord.Guild) -> int:
    """DM the installer one offer per mini-bot running modules here.

    Returns the number of offers sent. Never raises.
    """
    service = getattr(bot, "module_transfer", None)
    if service is None:
        return 0
    try:
        grouped = group_by_app(await service.transferable(guild.id))
        if not grouped:
            return 0
        from utils.guild_language import guild_locale
        from utils.install_welcome import resolve_installer
        installer = await resolve_installer(bot, guild)
        if installer is None or getattr(installer, "bot", False):
            return 0
        locale = await guild_locale(bot, guild)
        sent = 0
        for app, module_ids in grouped.items():
            result = await bot.notifications.send_dm(
                installer,
                content=transfer_content(locale),
                source=NotificationSource.service("moddy", author=ContentAuthor.MODDY),
                variables={"app": app_display_name(app), "server": guild.name,
                           "modules": _module_list(bot, module_ids, locale)},
                view=build_offer_view(bot, guild, app, module_ids, locale),
                locale=locale,
            )
            if result.delivered:
                sent += 1
        return sent
    except Exception as exc:  # noqa: BLE001 — an offer is never worth a failed join
        logger.error("Could not offer a transfer for guild %s: %s", guild.id, exc, exc_info=True)
        return 0


def config_banner_items(guild_id: int, foreign: Dict[str, str], bot,
                        locale: str) -> List[ui.Item]:
    """Items for the /config main screen: one line + button per mini-bot."""
    items: List[ui.Item] = []
    for app, module_ids in list(group_by_app(foreign).items())[:5]:
        items.append(ui.TextDisplay(
            f"{INFO} **{t('minibots.transfer.banner.title', locale=locale, app=app_display_name(app))}**\n"
            f"-# {t('minibots.transfer.banner.body', locale=locale, app=app_display_name(app), modules=_module_list(bot, module_ids, locale))}"
        ))
        items.append(ui.ActionRow(TransferToModdyButton(guild_id, app, locale=locale)))
    return items


def build_external_module_view(bot, guild_id: int, locale: str, module_id: str,
                               app: str, *, viewer_app: str = "moddy") -> BaseView:
    """Shown instead of a module's /config screen when another app runs it.

    Seen from Moddy: "Managed by X" + Transfer to Moddy. Seen from a mini-bot
    (``viewer_app`` is not Moddy): "Managed by Moddy/X here", no button — a
    mini-bot never takes a module back on its own.
    """
    from modules.configs._common import breadcrumb
    name = app_display_name(app)
    label = module_label(bot, module_id, locale)
    view = BaseView()
    container = ui.Container(accent_colour=discord.Colour(ACCENT))
    container.add_item(breadcrumb(locale, label))
    container.add_item(ui.TextDisplay(
        f"### {INFO} {t('minibots.transfer.external.title', locale=locale, app=name)}"))
    if viewer_app == "moddy":
        container.add_item(ui.TextDisplay(
            t("minibots.transfer.external.body", locale=locale, app=name, module=label)))
        container.add_item(ui.ActionRow(TransferToModdyButton(guild_id, app, locale=locale)))
    else:
        container.add_item(ui.TextDisplay(
            t("minibots.transfer.external.elsewhere", locale=locale, app=name, module=label)))
    view.add_item(container)
    view.add_item(ui.ActionRow(ConfigBackButton(locale=locale)))
    return view
