"""
The beta-launch announcement — a one-off notice to the owners who had Moddy
installed while it was still being written.

Temporary by design. It exists for the beta launch and nothing else: when the
campaign is over, this module, ``staff/commands/com/beta.py`` and the
``notifications.beta`` i18n block can be deleted together, and nothing else in
the bot depends on them. The pieces worth keeping (support requests, the
``moddy_team`` attribution) live outside it.

What it builds:

* :func:`beta_content` — the uniform :class:`NotificationContent` behind the
  message, so the dashboard and the mail pipeline render the same words the DM
  shows without anyone retyping them;
* :func:`build_beta_view` — the Discord card, plus the row of buttons *outside*
  the container: translate, ask the team to configure it for you, support,
  documentation.

The message is composed in English (what the recipients get by default) and
translated **on demand**: the Translate button re-renders the same card in the
clicker's own Discord language, using the variables stored on the notification
row. Nothing is guessed — a translation shows exactly what was sent, in another
language.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import discord
from discord import ui

import config
from cogs.error_handler import BaseView
from notifications.models import NotificationContent
from utils.emojis import MODDY_SQUARE_MIN, TRANSLATE
from utils.i18n import i18n, t
from utils.support_request_views import ConfigHelpButton, shorten

logger = logging.getLogger("moddy.beta_announcement")

#: Language the campaign is sent in. Recipients translate it themselves.
DEFAULT_LOCALE = "en-US"

#: Accent of the card — Moddy blue, as asked for the campaign.
ACCENT = 0x3661FF

#: How ``/config`` is written in the body (see ``config.command_label``).
CONFIG_LABEL = config.command_label("config")

#: Same for ``/bug-report``, named in the last paragraph.
BUG_REPORT_LABEL = config.command_label("bug-report")

#: UUID fragment used by the translate button's custom_id.
_UUID = r"[0-9a-fA-F-]{36}"


def beta_content(locale: str = DEFAULT_LOCALE) -> NotificationContent:
    """The announcement as a uniform notification payload.

    Placeholders stay unresolved (``{user}``, ``{servers}``): that is what lets
    every owner's copy share one stored body while remaining reproducible to
    the character months later.
    """
    return NotificationContent(
        title=t("notifications.beta.title", locale=locale),
        body=t("notifications.beta.body", locale=locale,
               user="{user}", servers="{servers}",
               config=CONFIG_LABEL, bug_report=BUG_REPORT_LABEL,
               support=config.SUPPORT_URL,
               dashboard=config.DASHBOARD_URL,
               docs=config.DOCS_URL),
        icon=MODDY_SQUARE_MIN,
        accent_color=ACCENT,
        links=[
            {"label": t("support.links.support", locale=locale), "url": config.SUPPORT_URL},
            {"label": t("support.links.docs", locale=locale), "url": config.DOCS_URL},
        ],
        template_id="notifications.beta",
    )


def build_beta_view(*, variables: Dict[str, Any], locale: str = DEFAULT_LOCALE,
                    notification_id: Optional[str] = None) -> BaseView:
    """The card the owner receives, buttons included.

    ``notification_id`` is what the Translate button carries: with it, a click
    re-reads the stored variables and re-renders this exact message in the
    reader's language. Without it (a staff preview) the button is left off.
    """
    content = beta_content(locale).render(variables)

    view = BaseView()
    container = ui.Container(accent_colour=discord.Colour(ACCENT))
    container.add_item(ui.TextDisplay(f"### {content.icon} {content.title}"))
    container.add_item(ui.TextDisplay(content.body))
    view.add_item(container)

    row = ui.ActionRow()
    if notification_id:
        row.add_item(BetaTranslateButton(str(notification_id), locale=locale))
    row.add_item(ConfigHelpButton(locale=locale))
    row.add_item(ui.Button(
        label=shorten(t("support.links.support", locale=locale)),
        style=discord.ButtonStyle.link, url=config.SUPPORT_URL))
    row.add_item(ui.Button(
        label=shorten(t("support.links.docs", locale=locale)),
        style=discord.ButtonStyle.link, url=config.DOCS_URL))
    row.add_item(ui.Button(
        label=shorten(t("support.links.dashboard", locale=locale)),
        style=discord.ButtonStyle.link, url=config.DASHBOARD_URL))
    view.add_item(row)
    return view


class BetaTranslateButton(
    ui.DynamicItem[ui.Button],
    template=rf"moddy:beta:translate:(?P<notification>{_UUID})",
):
    """Re-render the announcement in the reader's own Discord language.

    Public by design: the button only ever edits the DM the clicker is already
    reading, and it can show nothing the recipient was not sent — the wording
    is rebuilt from the notification's own stored template and variables.
    """

    def __init__(self, notification_id: str, *, locale: str = DEFAULT_LOCALE):
        super().__init__(ui.Button(
            label=shorten(t("notifications.beta.translate", locale=locale)),
            style=discord.ButtonStyle.primary,
            emoji=discord.PartialEmoji.from_str(TRANSLATE),
            custom_id=f"moddy:beta:translate:{notification_id}",
        ))
        self.notification_id = str(notification_id)

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["notification"])

    async def callback(self, interaction: discord.Interaction):
        try:
            locale = i18n.get_user_locale(interaction)
            service = getattr(interaction.client, "notifications", None)
            record = await service.get(self.notification_id) if service else None
            variables = self._variables(record, interaction)

            view = build_beta_view(variables=variables, locale=locale,
                                   notification_id=self.notification_id)
            # A translated card is the same notification, so it closes the same
            # way: dropping the "sent by" line would leave a message with no
            # stated origin.
            if service is not None:
                service.append_attribution(
                    view, await service.attribution_line(record, locale))
            await interaction.response.edit_message(view=view)
        except Exception as exc:  # noqa: BLE001 — dynamic items have no BaseView
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, exc, self.__class__.__name__)

    @staticmethod
    def _variables(record: Optional[Dict[str, Any]],
                   interaction: discord.Interaction) -> Dict[str, Any]:
        """The values this copy was sent with — falling back to what the
        interaction itself knows, so a translation never renders `{user}`."""
        variables = dict((record or {}).get("variables") or {})
        variables.setdefault(
            "user", interaction.user.global_name or interaction.user.name)
        variables.setdefault("servers", "")
        return variables


class BetaPersistence(BaseView):
    """Marker view: registers the announcement's translate button at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(BetaTranslateButton)


def owner_server_map(bot) -> Dict[int, List[discord.Guild]]:
    """Every server owner Moddy knows, and the servers they own.

    One DM per *owner*, not per server: someone with four servers hears about
    the beta once, and their message names all four.
    """
    owners: Dict[int, List[discord.Guild]] = {}
    for guild in bot.guilds:
        owner_id = guild.owner_id
        if not owner_id:
            continue
        owners.setdefault(owner_id, []).append(guild)
    return owners


def format_servers(guilds: List[discord.Guild], *, locale: str = DEFAULT_LOCALE) -> str:
    """``**A**``, ``**A** and **B**``, ``**A**, **B** and **C**`` — bolded,
    because the sentence around them is prose, not a list."""
    names = [f"**{g.name}**" for g in guilds]
    if not names:
        return t("notifications.beta.your_server", locale=locale)
    if len(names) == 1:
        return names[0]
    joiner = t("common.and", locale=locale)
    return f"{', '.join(names[:-1])} {joiner} {names[-1]}"
