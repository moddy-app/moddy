"""
Sanction expiration DM (Components V2).

When a temporary sanction reaches its ``expires_at``, ``bot.case_expiry`` flips
it to ``expired`` and :class:`services.expiration_notifier.ExpirationNotifier`
tells the subject about it: the timeout is over, the warning no longer counts,
the ban has been lifted.

The panel mirrors the sanction DM (``cogs/moderation_commands._send_sanction_dm``)
so the "good news" message is visually the sibling of the one that announced the
sanction — same layout, same case link, same ``sent by`` footer — only in green.
An expired **ban** additionally carries a *Rejoin the server* link button, since
being unbanned is worthless without a way back in.
"""

from __future__ import annotations

from typing import Optional

import discord
from discord import ui

from cogs.error_handler import BaseView
from utils import emojis
from utils.i18n import t

# Same public case URL as the sanction DM.
CASE_URL = "https://moddy.app/cases?{ref}"

# Green — an expiration is always good news for the subject.
EXPIRY_ACCENT = 0x57F287

# Sanction → title icon. The lift, not the sanction, so a check for the
# reversible ones and the sanction's own icon otherwise.
_EXPIRY_EMOJIS = {
    "ban": emojis.LEGAL,
    "mute": emojis.MIC_OFF,
    "warn": emojis.WARNING,
}

#: Actions the subject is notified about when they expire.
NOTIFIABLE_ACTIONS = ("ban", "mute", "warn")


def _key(action: str, suffix: str) -> str:
    return f"commands.moderation.expiry_dm.{action}_{suffix}"


def build_expiration_dm_view(
    *,
    locale: str,
    action: str,
    guild_name: str,
    guild_id: int,
    reference: Optional[str] = None,
    expired_at=None,
    invite_url: Optional[str] = None,
) -> BaseView:
    """The DM a subject receives when one of their sanctions expires.

    ``invite_url`` is only ever passed for an expired **ban** — the other
    actions never removed the member from the server.
    """
    icon = _EXPIRY_EMOJIS.get(action, emojis.DONE)
    guild_url = f"https://discord.com/channels/{guild_id}"

    title = t(_key(action, "title"), locale=locale)
    if title.startswith("["):  # unknown action → generic wording
        title = t("commands.moderation.expiry_dm.title", locale=locale)
    body = t(_key(action, "body"), locale=locale)
    if body.startswith("["):
        body = t("commands.moderation.expiry_dm.body", locale=locale)

    lines = [f"### {icon} {title}", body]
    if expired_at is not None:
        lines.append(
            f"> **{t('commands.moderation.expiry_dm.expired_at', locale=locale)}:** "
            f"<t:{int(expired_at.timestamp())}:R>"
        )
    if reference:
        lines.append(
            f"> **{t('commands.moderation.expiry_dm.case_id', locale=locale)}:**"
            f" [``{reference}``](<{CASE_URL.format(ref=reference)}>)"
        )
    lines.append(
        f"-# {t('commands.moderation.expiry_dm.sent_by', locale=locale, guild=guild_name, guild_id=guild_id, guild_url=guild_url)}"
    )

    view = BaseView()
    container = ui.Container(
        ui.TextDisplay("\n".join(lines)),
        accent_colour=discord.Colour(EXPIRY_ACCENT),
    )
    view.add_item(container)

    # Link buttons carry no custom_id and trigger no interaction, so the view
    # stays valid forever without being registered as persistent.
    if invite_url:
        row = ui.ActionRow()
        row.add_item(ui.Button(
            style=discord.ButtonStyle.link,
            label=t("commands.moderation.expiry_dm.rejoin", locale=locale)[:80],
            url=invite_url,
        ))
        container.add_item(row)
        container.add_item(ui.TextDisplay(
            f"-# {t('commands.moderation.expiry_dm.invite_note', locale=locale)}"
        ))

    return view
